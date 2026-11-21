"""Dense geometry of a scan: MoGe-2 depth per registered frame, aligned to the sparse points, fused into a TSDF.

Needs torch, MoGe and Open3D (and trimesh for the GLB); they are imported inside the functions. The frames given to
`fuse` are already undistorted (`undistort_frames`), so they are pure pinhole images with the camera's K. The depth model only
shapes the mesh: its numbers are aligned to the structure-from-motion points by a robust scale and shift per frame,
and a frame whose alignment is poor is left out.
"""
import math
import time
from pathlib import Path

import numpy as np

from . import ScanError
from .sfm import undistort_norm

MODEL_NAME = 'Ruicheng/moge-2-vitl-normal'
RESOLUTION_LEVEL = 8                  # MoGe token budget, 0 (fastest) to 9
MAX_TRIANGLES = 600_000
VOXELS = 384                          # the voxel is the scene extent / 384 ...
FALLBACK_VOXELS = 320                 # ... or / 320 when the TSDF runs out of memory
TAUBIN_ITERATIONS = 10
COLOR_BLUR_SKIP = 0.2                 # the blurriest 20% of the frames do not colour the mesh
COLOR_SMOOTH_ITERATIONS = 2
COLOR_SIGMA = 24.0 / 255.0            # bilateral range sigma of the vertex-colour filter
MIN_VIEW_COS = 0.2                    # a frame colours a vertex only when it sees the surface within ~78 degrees
TRUNC_VOXELS = 4                      # sdf_trunc = 4 voxels
MIN_WEIGHT = 2.0                      # a voxel needs at least two observations to reach the mesh
MIN_ALIGN_POINTS = 25
MIN_ALIGN_INLIERS = 0.5
EDGE_GRADIENT = 0.06                  # drop pixels whose depth changes by more than 6% over 2 px (flying pixels)


# -- depth alignment --------------------------------------------------------------------------------------------------

def align_depth(z_model, z_sparse, shift=True, iters=12, huber=1.345):
    """Robust scale and shift mapping model depths onto sparse depths: z_sparse ~ a * z_model + b.

    Iteratively reweighted least squares with Huber weights (scale from the median absolute deviation), then a refit on
    the points within 3 deviations (the trim). The shift is dropped (b = 0) when `shift` is false or the model depths span
    under 15% of their mean, where a and b cannot be told apart. Returns (a, b, inlier fraction, rms residual of the
    inliers); the inputs are 1-D arrays of the same length."""
    zm, zs = np.asarray(z_model, dtype=float), np.asarray(z_sparse, dtype=float)
    ok = np.isfinite(zm) & np.isfinite(zs) & (zm > 0) & (zs > 0)
    zm, zs = zm[ok], zs[ok]
    if len(zm) < 3:
        return float('nan'), 0.0, 0.0, float('inf')
    use_shift = shift and np.std(zm) > 0.15 * np.mean(zm)
    a, b = float(np.median(zs / zm)), 0.0

    def solve(w):
        if use_shift:
            A = np.column_stack([zm, np.ones_like(zm)]) * np.sqrt(w)[:, None]
            return tuple(np.linalg.lstsq(A, zs * np.sqrt(w), rcond=None)[0])
        return float(np.sum(w * zm * zs) / np.sum(w * zm * zm)), 0.0

    for _ in range(iters):
        r = zs - (a * zm + b)
        s = max(1.4826 * float(np.median(np.abs(r - np.median(r)))), 1e-9 * float(np.mean(zs)))
        w = np.minimum(1.0, huber * s / np.maximum(np.abs(r), 1e-12))
        a, b = solve(w)
    r = zs - (a * zm + b)
    s = max(1.4826 * float(np.median(np.abs(r - np.median(r)))), 1e-9 * float(np.mean(zs)))
    inl = np.abs(r) <= 3.0 * s
    if inl.sum() >= 3:
        zm, zs = zm[inl], zs[inl]
        a, b = solve(np.ones(len(zm)))
        r = zs - (a * zm + b)
    return float(a), float(b), float(inl.sum()) / len(inl), float(np.sqrt(np.mean(r * r)))


def local_correction(depth, uv, z_sparse, cell=16, sigma_px=40.0, sigma_depth=0.06, prior=1.0, max_log=0.5):
    """A smooth multiplicative correction field (h, w) that pulls an aligned depth map towards the sparse depths.

    The depth model can be off by several percent on one object even when a global scale and shift fit. The ratio
    z_sparse / depth is taken at every sparse point (pixels `uv`) and averaged on a grid of `cell` px with weights that
    fall off with the pixel distance (`sigma_px`) and with the relative depth difference between point and cell
    (`sigma_depth`), so a table top is corrected by its own points and not by the floor behind it. The average is shrunk
    to 1 by `prior` pseudo-points, so a region without sparse points is left alone, and the grid is bilinearly resized.
    Ratios beyond exp(+-max_log) are ignored as bad matches."""
    import cv2
    h, w = depth.shape
    at_points = sample(depth, uv)
    ok = np.isfinite(at_points) & (at_points > 0) & (z_sparse > 0)
    ratio = np.where(ok, z_sparse / np.where(ok, at_points, 1.0), 1.0)
    ok &= np.abs(np.log(np.clip(ratio, 1e-6, None))) < max_log
    gw, gh = -(-w // cell), -(-h // cell)
    gx, gy = np.meshgrid((np.arange(gw) + 0.5) * cell, (np.arange(gh) + 0.5) * cell)
    centres = np.column_stack([gx.ravel(), gy.ravel()])
    grid_depth = sample(depth, centres)
    field = np.ones(len(centres))
    valid = np.isfinite(grid_depth) & (grid_depth > 0)
    if ok.sum() >= 3 and valid.any():
        P, r, dp = uv[ok], ratio[ok], np.log(at_points[ok])
        d2 = ((centres[valid][:, None, :] - P[None, :, :]) ** 2).sum(axis=2)
        dd = (np.log(grid_depth[valid])[:, None] - dp[None, :]) / sigma_depth
        wgt = np.exp(-0.5 * d2 / sigma_px ** 2 - 0.5 * dd ** 2)
        field[valid] = (wgt @ r + prior) / (wgt.sum(axis=1) + prior)
    return cv2.resize(field.reshape(gh, gw).astype(np.float32), (gw * cell, gh * cell),
                      interpolation=cv2.INTER_LINEAR)[:h, :w]

# -- depth model ------------------------------------------------------------------------------------------------------

class DepthModel:
    """MoGe-2 loaded once; `depth(rgb, fov_x_deg)` -> (depth HxW float32, valid HxW bool)."""

    def __init__(self, device=None):
        import torch
        from moge.model.v2 import MoGeModel
        self.torch = torch
        self.device = torch.device(device or ('cuda' if torch.cuda.is_available() else 'cpu'))
        self.model = MoGeModel.from_pretrained(MODEL_NAME).to(self.device).eval()

    def depth(self, rgb, fov_x_deg):
        torch = self.torch
        tensor = torch.tensor(rgb / 255.0, dtype=torch.float32, device=self.device).permute(2, 0, 1)
        with torch.no_grad():
            out = self.model.infer(tensor, fov_x=fov_x_deg, resolution_level=RESOLUTION_LEVEL)
        depth = out['depth'].float().cpu().numpy()
        valid = out['mask'].cpu().numpy().astype(bool) & np.isfinite(depth) & (depth > 0)
        return depth, valid


def sample(depth, uv):
    """Bilinear samples of a depth map at pixel coordinates (n, 2) (pixel centres at +0.5 are NOT assumed: uv is in the
    same convention as the keypoints, whose integer coordinates are pixel centres)."""
    h, w = depth.shape
    x = np.clip(uv[:, 0], 0, w - 1.001)
    y = np.clip(uv[:, 1], 0, h - 1.001)
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = x - x0, y - y0
    return ((1 - fy) * ((1 - fx) * depth[y0, x0] + fx * depth[y0, x0 + 1]) +
            fy * ((1 - fx) * depth[y0 + 1, x0] + fx * depth[y0 + 1, x0 + 1]))


def scene_extent(points, centres):
    """Largest side (scan units) of the robust (1st to 99th percentile) box around the sparse points and cameras."""
    P = np.vstack([np.asarray(points), np.asarray(centres)])
    lo, hi = np.percentile(P, [1, 99], axis=0)
    return float(np.max(hi - lo))


# -- undistorted frames -----------------------------------------------------------------------------------------------

def undistort_frames(files, poses, camera, progress=None, quality=95):
    """Rewrite the JPEG of every registered frame (poses[i] is not None) undistorted with the recovered SIMPLE_RADIAL k1.

    The same K is the new camera matrix, so each saved image is a pure pinhole picture with the camera's own fx, fy, cx
    and cy (the corners that fall outside the old picture stay black). Returns the number of frames written."""
    import cv2
    K = np.array([[camera['fx'], 0, camera['cx']], [0, camera['fy'], camera['cy']], [0, 0, 1.0]])
    dist = np.array([camera['k1'], 0, 0, 0], dtype=np.float64)
    size = (int(camera['width']), int(camera['height']))
    map_x, map_y = cv2.initUndistortRectifyMap(K, dist, None, K, size, cv2.CV_32FC1)
    done = 0
    for i, path in enumerate(files):
        if poses[i] is None:
            continue
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        out = cv2.remap(image, map_x, map_y, cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT)
        if not cv2.imwrite(str(path), out, [cv2.IMWRITE_JPEG_QUALITY, quality]):
            raise ScanError('scan_failed', 'An undistorted frame could not be written')
        done += 1
    if progress:
        progress(f'Cameras: {done} frames undistorted')
    return done


# -- fusion and mesh --------------------------------------------------------------------------------------------------

def frame_sharpness(rgb):
    """Variance of the Laplacian of a frame (higher is sharper)."""
    import cv2
    return float(cv2.Laplacian(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), cv2.CV_64F).var())


def colour_weights(sharpness, skip=COLOR_BLUR_SKIP):
    """Per-frame colour weights from sharpness: 0 for the blurriest `skip` fraction, otherwise sharpness over the median
    (clipped to 0.25..2), so a sharp frame counts more than a soft one."""
    s = np.asarray(sharpness, dtype=float)
    if len(s) == 0:
        return s
    cut = np.quantile(s, skip) if skip > 0 and len(s) >= 5 else -np.inf
    w = np.clip(s / max(float(np.median(s)), 1e-9), 0.25, 2.0)
    return np.where(s > cut, w, 0.0)


def view_colours(vertices, normals, views, camera, tolerance, device=None):
    """Colour every vertex from the frames that see it: (n, 3) float in 0..1 and (n,) total weight (0 = never seen).

    `views` is a list of (R_from_C 4x4, depth HxW float32 (0 = invalid), rgb HxW3 uint8, weight). A frame sees a vertex when
    the vertex projects inside the picture, its depth agrees with the frame's depth map within `tolerance` (occlusion
    test) and the viewing angle is within MIN_VIEW_COS. The frame's weight is its sharpness weight times the cosine
    between the surface normal and the direction to the camera."""
    import torch
    import torch.nn.functional as F
    dev = torch.device(device or ('cuda' if torch.cuda.is_available() else 'cpu'))
    f, fy, cx, cy = camera['fx'], camera['fy'], camera['cx'], camera['cy']
    V = torch.tensor(np.asarray(vertices), dtype=torch.float32, device=dev)
    N = torch.tensor(np.asarray(normals), dtype=torch.float32, device=dev)
    acc = torch.zeros((len(V), 3), dtype=torch.float32, device=dev)
    total = torch.zeros(len(V), dtype=torch.float32, device=dev)
    for T, depth, rgb, weight in views:
        if weight <= 0:
            continue
        h, w = depth.shape
        T = torch.tensor(np.linalg.inv(np.asarray(T)), dtype=torch.float32, device=dev)
        pc = V @ T[:3, :3].T + T[:3, 3]
        z = pc[:, 2]
        zs = torch.where(z > 1e-6, z, torch.ones_like(z))
        u, v = f * pc[:, 0] / zs + cx, fy * pc[:, 1] / zs + cy
        ok = (z > 1e-6) & (u >= 2) & (u <= w - 3) & (v >= 2) & (v <= h - 3)
        idx = ok.nonzero(as_tuple=True)[0]
        if len(idx) == 0:
            continue
        D = torch.tensor(depth, device=dev)
        ui, vi = u[idx].round().long(), v[idx].round().long()
        seen = D[vi, ui]
        centre = torch.linalg.inv(T)[:3, 3]
        to_cam = centre - V[idx]
        cos = (N[idx] * to_cam).sum(1) / to_cam.norm(dim=1).clamp_min(1e-12)
        keep = (seen > 0) & ((z[idx] - seen).abs() < tolerance) & (cos > MIN_VIEW_COS)
        idx, cos = idx[keep], cos[keep]
        if len(idx) == 0:
            continue
        grid = torch.stack([2 * u[idx] / (w - 1) - 1, 2 * v[idx] / (h - 1) - 1], dim=1)[None, None]
        image = torch.tensor(rgb, device=dev).permute(2, 0, 1)[None].float() / 255.0
        sampled = F.grid_sample(image, grid, mode='bilinear', align_corners=True)[0, :, 0].T
        wt = float(weight) * cos
        acc.index_add_(0, idx, sampled * wt[:, None])
        total.index_add_(0, idx, wt)
    colours = acc / total.clamp_min(1e-12)[:, None]
    return colours.cpu().numpy(), total.cpu().numpy()


def smooth_vertex_colours(colours, faces, iterations=COLOR_SMOOTH_ITERATIONS, sigma=COLOR_SIGMA):
    """Light bilateral filter of vertex colours over the mesh edges: each vertex takes the average of itself and its
    neighbours, a neighbour counting less the further its colour is (range sigma `sigma`, colours in 0..1), so speckle is
    flattened and colour edges stay."""
    c = np.asarray(colours, dtype=np.float64)
    F = np.asarray(faces)
    if len(F) == 0 or iterations <= 0:
        return c
    a = np.concatenate([F[:, 0], F[:, 1], F[:, 2], F[:, 1], F[:, 2], F[:, 0]])
    b = np.concatenate([F[:, 1], F[:, 2], F[:, 0], F[:, 0], F[:, 1], F[:, 2]])
    n = len(c)
    for _ in range(iterations):
        w = np.exp(-np.sum((c[a] - c[b]) ** 2, axis=1) / (2.0 * sigma * sigma))
        num = c.copy()
        den = np.ones(n)
        for k in range(3):
            num[:, k] += np.bincount(a, weights=w * c[b, k], minlength=n)
        den += np.bincount(a, weights=w, minlength=n)
        c = num / den[:, None]
    return c


def _integrate(frames, K, voxel, depth_max):
    """TSDF-integrate [(T, depth, rgb)] into a VoxelBlockGrid with the given voxel size and return the legacy mesh with
    vertex colours."""
    import open3d as o3d
    import open3d.core as o3c
    device = o3c.Device('CPU:0')
    volume = o3d.t.geometry.VoxelBlockGrid(('tsdf', 'weight', 'color'), (o3c.float32, o3c.float32, o3c.float32),
                                           ((1), (1), (3)), voxel, 16, 20000, device)     # the block table grows on demand
    intrinsic = o3c.Tensor(K, o3c.float64)
    for T, d, rgb in frames:
        depth_img = o3d.t.geometry.Image(o3c.Tensor(np.ascontiguousarray(d[..., None]), device=device))
        color_img = o3d.t.geometry.Image(o3c.Tensor(np.ascontiguousarray(rgb, dtype=np.float32) / 255.0, device=device))
        extrinsic = o3c.Tensor(np.linalg.inv(T), o3c.float64)
        blocks = volume.compute_unique_block_coordinates(depth_img, intrinsic, extrinsic, 1.0, depth_max)
        volume.integrate(blocks, depth_img, color_img, intrinsic, extrinsic, 1.0, depth_max, float(TRUNC_VOXELS))
    return volume.extract_triangle_mesh(weight_threshold=MIN_WEIGHT).to_legacy()


def _drop_specks(mesh, fraction=0.02):
    labels, counts, _ = mesh.cluster_connected_triangles()
    labels, counts = np.asarray(labels), np.asarray(counts)
    mesh.remove_triangles_by_mask(counts[labels] < fraction * counts.max())
    mesh.remove_unreferenced_vertices()


def fuse(rec, read_color, progress=None, depth_model=None):
    """TSDF fusion of the registered frames of a Reconstruction. `read_color(i)` returns the UNDISTORTED BGR frame i.
    Returns (vertices (n, 3) float, faces (m, 3) int, colours (n, 3) uint8 RGB, info dict) with the mesh in R; info holds
    the vertex `normals`. Raises ScanError('scan_failed') when fewer than 3 frames could be used.

    Geometry: marching cubes on the TSDF (voxel = extent / 384, or / 320 when memory runs out), Taubin smoothing, quadric
    decimation to MAX_TRIANGLES, removal of degenerate and duplicated triangles and of small floating pieces, vertex
    normals. Colour: every vertex is coloured from all frames that see it (depth-tested), weighted by frame sharpness and
    viewing angle, the blurriest 20% of the frames left out, then a light bilateral filter over the mesh edges."""
    import cv2
    import open3d as o3d
    say = progress or (lambda text: None)
    cam = rec.camera
    f, k1, cx, cy, w, h = cam['fx'], cam['k1'], cam['cx'], cam['cy'], cam['width'], cam['height']
    K = np.array([[f, 0, cx], [0, cam['fy'], cy], [0, 0, 1.0]])
    fov_x = math.degrees(2.0 * math.atan(w / (2.0 * f)))
    reg = [i for i, p in enumerate(rec.poses) if p is not None]
    centres = np.array([rec.poses[i][:3, 3] for i in reg])
    extent = scene_extent(rec.points, centres)
    depth_max = float(extent * 1.2)
    model = depth_model or DepthModel()
    used, skipped, t_depth, t_fuse = 0, 0, 0.0, 0.0
    scales = []
    frames = []                                                          # (T, aligned depth, rgb, sharpness)
    for n, i in enumerate(reg):
        t0 = time.monotonic()
        rgb = cv2.cvtColor(read_color(i), cv2.COLOR_BGR2RGB)             # already undistorted: pure pinhole with K
        depth, valid = model.depth(rgb, fov_x)
        T = np.asarray(rec.poses[i])
        pids, xy = rec.visible[i]
        t_depth += time.monotonic() - t0
        if len(pids) >= MIN_ALIGN_POINTS:
            uv = undistort_norm(xy, f, k1, cx, cy) * f + [cx, cy]
            z_sparse = (rec.points[pids] - T[:3, 3]) @ T[:3, 2]
            z_model = sample(depth, uv)
            a, b, frac, rms = align_depth(z_model, z_sparse)
        else:
            a, b, frac, rms = float('nan'), 0.0, 0.0, float('inf')
        if not (a > 0 and frac >= MIN_ALIGN_INLIERS):
            skipped += 1
            continue
        t0 = time.monotonic()
        d = (a * depth + b).astype(np.float32)
        d *= local_correction(d, uv, z_sparse)
        gx = np.zeros_like(d)
        gy = np.zeros_like(d)
        gx[:, 1:-1] = np.abs(d[:, 2:] - d[:, :-2])
        gy[1:-1] = np.abs(d[2:] - d[:-2])
        valid &= (d > 0) & (np.maximum(gx, gy) < EDGE_GRADIENT * d)
        d[~valid] = 0
        frames.append((T, d, rgb, frame_sharpness(rgb)))
        t_fuse += time.monotonic() - t0
        used += 1
        scales.append(a)
        if (n + 1) % 10 == 0 or n + 1 == len(reg):
            say(f'Depth: {n + 1}/{len(reg)}')
    if used < 3:
        raise ScanError('scan_failed', 'The depth maps could not be matched to the camera track. Move slower, use more '
                                       'light and film more texture.')
    # The legacy ScalableTSDFVolume integrates nothing in the Open3D 0.20 Windows wheel, so the tensor VoxelBlockGrid
    # (the same hashed-block TSDF fusion) is used instead.
    t0 = time.monotonic()
    mesh, divisions = None, VOXELS
    for divisions in (VOXELS, FALLBACK_VOXELS):
        voxel = extent / divisions
        try:
            mesh = _integrate([(T, d, rgb) for T, d, rgb, _ in frames], K, voxel, depth_max)
            break
        except (MemoryError, RuntimeError) as error:
            if divisions == FALLBACK_VOXELS:
                raise ScanError('scan_failed', f'The fusion ran out of memory: {error}') from error
            say(f'Fusion: out of memory at voxel extent/{divisions}, retrying at extent/{FALLBACK_VOXELS}')
    say(f'Fusion: {used} frames fused ({skipped} skipped), voxel {voxel:.4g} units (extent/{divisions})')
    mesh.remove_degenerate_triangles()
    mesh.remove_unreferenced_vertices()
    tri_before = len(mesh.triangles)
    if tri_before == 0:
        raise ScanError('scan_failed', 'The fused volume holds no surface')
    _drop_specks(mesh)
    mesh = mesh.filter_smooth_taubin(number_of_iterations=TAUBIN_ITERATIONS)
    if len(mesh.triangles) > MAX_TRIANGLES:
        mesh = mesh.simplify_quadric_decimation(target_number_of_triangles=MAX_TRIANGLES)
    mesh.remove_degenerate_triangles()
    mesh.remove_duplicated_triangles()
    mesh.remove_duplicated_vertices()
    mesh.remove_unreferenced_vertices()
    _drop_specks(mesh)
    mesh.compute_vertex_normals()
    vertices = np.asarray(mesh.vertices)
    faces = np.asarray(mesh.triangles)
    normals = np.asarray(mesh.vertex_normals)
    colours = np.clip(np.asarray(mesh.vertex_colors), 0.0, 1.0)            # the TSDF colour: the fallback
    sharp = np.array([fr[3] for fr in frames])
    weights = colour_weights(sharp)
    seen, total = view_colours(vertices, normals, [(T, d, rgb, wt) for (T, d, rgb, _), wt in zip(frames, weights)],
                               cam, tolerance=3.0 * voxel)
    has = total > 0
    colours[has] = seen[has]
    colours = smooth_vertex_colours(colours, faces)
    colours = np.clip(colours * 255.0 + 0.5, 0, 255).astype(np.uint8)
    t_fuse += time.monotonic() - t0
    say(f'Mesh: {len(faces):,} triangles')
    return vertices, faces, colours, {'frames_fused': used, 'frames_skipped': skipped, 'voxel': voxel, 'extent': extent,
                                      'depth_s': t_depth, 'fusion_s': t_fuse, 'triangles_before': tri_before,
                                      'depth_scale_median': float(np.median(scales)), 'normals': normals,
                                      'voxel_divisions': divisions, 'colour_frames': int((weights > 0).sum()),
                                      'vertices_coloured': float(has.mean())}


def write_glb(path, vertices, faces, colours, normals=None):
    """Write a GLB with vertex colours and vertex normals (trimesh); the vertices stay in R."""
    import trimesh
    rgba = np.column_stack([colours, np.full(len(colours), 255, dtype=np.uint8)])
    mesh = trimesh.Trimesh(vertices=np.asarray(vertices, dtype=np.float64), faces=np.asarray(faces), vertex_colors=rgba,
                           process=False)
    if normals is not None:
        mesh.vertex_normals = np.asarray(normals, dtype=np.float64)
    else:
        mesh.vertex_normals                                              # computes and caches them
    Path(path).write_bytes(mesh.export(file_type='glb', include_normals=True))
