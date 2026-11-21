"""Pure geometry of the photo-AR feature (docs/AR_PHOTO.md). numpy only: no OpenCV, no scipy, no Pillow.

Frames: F is the floor frame (right-handed, z up, z = 0 on the floor), C the OpenCV camera frame (x right, y down,
z forward). `T_FC` is the 4x4 matrix mapping C points into F. The projection is u = fx*Xc/Zc + cx, v = fy*Yc/Zc + cy
with the pixel origin at the top-left of the stored photo; no lens distortion is modelled.
"""
import math
import struct
from pathlib import Path

import numpy as np

DEFAULT_HFOV_DEG = 60.0
MAX_FOCAL_DISAGREEMENT = 0.15      # the two focal estimates may differ by at most 15% of their mean
MIN_TILT_DEG = 5.0                 # a plane closer to fronto-parallel than this gives no usable focal length


class ArError(ValueError):
    """A coded failure of the AR tasks (the task protocol passes `code` through): ar_bad_reference,
    ar_estimator_unavailable, ar_estimate_failed."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# -- homography -------------------------------------------------------------------------------------------------------

def _normalising_transform(points):
    """(3x3 T, normalised points): T moves the centroid to the origin and scales the mean distance to sqrt(2)."""
    centre = points.mean(axis=0)
    spread = np.linalg.norm(points - centre, axis=1).mean()
    scale = math.sqrt(2.0) / spread if spread > 0 else 1.0
    T = np.array([[scale, 0.0, -scale * centre[0]], [0.0, scale, -scale * centre[1]], [0.0, 0.0, 1.0]])
    return T, (points - centre) * scale


def homography_dlt(src, dst):
    """Homography H (3x3, H[2,2] = 1 when possible) with dst ~ H @ [src, 1], by the normalised direct linear transform.

    `src` and `dst` are Nx2 arrays of corresponding points, N >= 4 (floor-plane metres -> pixels). With more than four
    pairs it is the algebraic least-squares fit."""
    src, dst = np.asarray(src, dtype=float), np.asarray(dst, dtype=float)
    if src.ndim != 2 or src.shape[1] != 2 or src.shape != dst.shape or len(src) < 4:
        raise ValueError('homography_dlt needs two Nx2 arrays with N >= 4')
    t1, s = _normalising_transform(src)
    t2, d = _normalising_transform(dst)
    rows = []
    for (x, y), (u, v) in zip(s, d):
        rows.append([-x, -y, -1.0, 0.0, 0.0, 0.0, u * x, u * y, u])
        rows.append([0.0, 0.0, 0.0, -x, -y, -1.0, v * x, v * y, v])
    _, _, vt = np.linalg.svd(np.array(rows))
    H = np.linalg.inv(t2) @ vt[-1].reshape(3, 3) @ t1
    return H / H[2, 2] if abs(H[2, 2]) > 1e-12 else H / np.linalg.norm(H)


def focal_from_homography(H, cx, cy):
    """Focal length in pixels from a plane homography, assuming square pixels and the principal point (cx, cy).

    The pixels are shifted by (cx, cy) first, so K = diag(f, f, 1). With h1, h2 the first two columns of the shifted H,
    r_i ~ K^-1 h_i must be orthogonal and of equal norm:
        h1x*h2x/f^2 + h1y*h2y/f^2 + h1z*h2z = 0        ->  f^2 = -(h1x*h2x + h1y*h2y) / (h1z*h2z)
        (|h1xy|^2 - |h2xy|^2)/f^2 = h2z^2 - h1z^2      ->  f^2 = (|h1xy|^2 - |h2xy|^2) / (h2z^2 - h1z^2)
    Returns None when ill-conditioned: a (nearly) fronto-parallel plane, a negative or non-finite f^2, a denominator
    that is small next to its terms, or two estimates that differ by more than 15%."""
    H = np.asarray(H, dtype=float)
    if H.shape != (3, 3) or not np.all(np.isfinite(H)):
        return None
    shift = np.array([[1.0, 0.0, -cx], [0.0, 1.0, -cy], [0.0, 0.0, 1.0]])
    S = shift @ H
    scale = np.linalg.norm(S[:2, :2])
    if scale <= 0:
        return None
    S = S / scale
    h1, h2 = S[:, 0], S[:, 1]
    z1, z2 = h1[2], h2[2]
    if math.hypot(z1, z2) < 1e-6:            # fronto-parallel: no depth variation to measure the focal length from
        return None
    d1 = z1 * z2
    d2 = z2 * z2 - z1 * z1
    scale_z = z1 * z1 + z2 * z2
    # Each constraint is used only when its denominator is well-conditioned. A rectangle square to the image
    # (z1 ~ 0) leaves only the equal-norm constraint; one at 45 degrees (z1 ~ z2) leaves only orthogonality.
    estimates = []
    if abs(d1) >= 0.05 * scale_z:
        estimates.append(-(h1[0] * h2[0] + h1[1] * h2[1]) / d1)
    if abs(d2) >= 0.05 * scale_z:
        estimates.append((h1[0] ** 2 + h1[1] ** 2 - h2[0] ** 2 - h2[1] ** 2) / d2)
    if not estimates or any(not math.isfinite(e) or e <= 0 for e in estimates):
        return None
    fs = [math.sqrt(e) for e in estimates]
    f = sum(fs) / len(fs)
    if len(fs) == 2 and abs(fs[0] - fs[1]) > MAX_FOCAL_DISAGREEMENT * f:
        return None
    # tilt of the plane from fronto-parallel: sin = sqrt(r1z^2 + r2z^2) for unit r1, r2
    r1z = z1 / math.hypot(h1[0] / f, h1[1] / f, z1)
    r2z = z2 / math.hypot(h2[0] / f, h2[1] / f, z2)
    if math.hypot(r1z, r2z) < math.sin(math.radians(MIN_TILT_DEG)):
        return None
    return float(f)


# -- pose -------------------------------------------------------------------------------------------------------------

def intrinsic_matrix(fx, fy, cx, cy):
    """The 3x3 camera matrix K."""
    return np.array([[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]])


def pose_from_homography(H, K, points_F=None):
    """(R_CF, t_CF): the rotation and translation taking floor-frame points into the camera frame, from H (floor
    plane, z = 0, metres -> pixels) and K.

    [r1 r2 t] = K^-1 H scaled by the mean norm of its first two columns; the overall sign is the one that puts the
    plane points in front of the camera (`points_F`, Nx2 or Nx3; else the frame origin); r3 = r1 x r2; R is then
    orthonormalised by SVD (det +1)."""
    M = np.linalg.inv(np.asarray(K, dtype=float)) @ np.asarray(H, dtype=float)
    lam = 0.5 * (np.linalg.norm(M[:, 0]) + np.linalg.norm(M[:, 1]))
    if not np.isfinite(lam) or lam <= 0:
        raise ArError('ar_bad_reference', 'The reference corners do not define a usable plane')
    r1, r2, t = M[:, 0] / lam, M[:, 1] / lam, M[:, 2] / lam
    if points_F is None:
        depth = t[2]
    else:
        p = np.asarray(points_F, dtype=float)
        depth = float(np.mean(r1[2] * p[:, 0] + r2[2] * p[:, 1] + t[2]))
    if depth < 0:
        r1, r2, t = -r1, -r2, -t
    R = np.column_stack([r1, r2, np.cross(r1, r2)])
    U, _, Vt = np.linalg.svd(R)
    if np.linalg.det(U @ Vt) < 0:
        U[:, 2] *= -1.0
    return U @ Vt, t


def world_from_camera(R_CF, t_CF):
    """T_FC (4x4) from the camera-from-floor pose: the inverse of [R_CF | t_CF]."""
    R_CF = np.asarray(R_CF, dtype=float)
    T = np.eye(4)
    T[:3, :3] = R_CF.T
    T[:3, 3] = -R_CF.T @ np.asarray(t_CF, dtype=float)
    return T


def project(points_F, K, T_FC):
    """Pixels (Nx2) of floor-frame points (Nx3) seen by the camera K at pose T_FC. Points at or behind the camera
    plane give inf/nan; callers check depth first when that can happen."""
    P = np.atleast_2d(np.asarray(points_F, dtype=float))
    T = np.asarray(T_FC, dtype=float)
    Pc = (P - T[:3, 3]) @ T[:3, :3]                  # R^T (P - C), row-wise
    K = np.asarray(K, dtype=float)
    with np.errstate(divide='ignore', invalid='ignore'):
        return np.column_stack([K[0, 0] * Pc[:, 0] / Pc[:, 2] + K[0, 2], K[1, 1] * Pc[:, 1] / Pc[:, 2] + K[1, 2]])


def rms_residual(predicted, observed):
    """Root-mean-square pixel distance between two Nx2 arrays."""
    diff = np.asarray(predicted, dtype=float) - np.asarray(observed, dtype=float)
    return float(math.sqrt(np.mean(np.sum(diff * diff, axis=1))))


# -- the reference rectangle ------------------------------------------------------------------------------------------

def reference_points(size_m):
    """Floor-frame (x, y) of the 4 reference corners: corner 0 is the origin, corner 0->1 is +x, corner 0->3 is +y."""
    length, width = float(size_m[0]), float(size_m[1])
    return np.array([[0.0, 0.0], [length, 0.0], [length, width], [0.0, width]])


def check_quad(corners_px):
    """Raise ArError('ar_bad_reference') unless the 4 pixel corners form a convex, non-degenerate quadrilateral
    (collinear, repeated and self-intersecting corner sets all fail)."""
    c = np.asarray(corners_px, dtype=float)
    if c.shape != (4, 2) or not np.all(np.isfinite(c)):
        raise ArError('ar_bad_reference', 'The reference needs 4 finite corner points')
    edges = np.roll(c, -1, axis=0) - c
    turns = [edges[i, 0] * edges[(i + 1) % 4, 1] - edges[i, 1] * edges[(i + 1) % 4, 0] for i in range(4)]
    eps = max(1e-9, 1e-4 * float(np.max(np.sum(edges * edges, axis=1))))
    if not (all(t > eps for t in turns) or all(t < -eps for t in turns)):
        raise ArError('ar_bad_reference', 'The reference corners must form a convex four-sided shape, clicked in order '
                                          'around the rectangle')


def calibrate(width, height, corners_px, size_m, intrinsics=None):
    """Camera and floor from a photographed rectangle of known size. Returns an ArCalibration-shaped dict.

    `intrinsics` (optional dict with fx, fy, cx, cy, width, height) is the fallback used when the focal length cannot be
    taken from the homography; without it the default 60 degree horizontal field of view is used. Raises
    ArError('ar_bad_reference') for a bad quadrilateral or a camera that would sit below the floor."""
    check_quad(corners_px)
    corners = np.asarray(corners_px, dtype=float)
    world = reference_points(size_m)
    world3 = np.column_stack([world, np.zeros(4)])
    H = homography_dlt(world, corners)
    cx0, cy0 = width / 2.0, height / 2.0
    warnings = []
    f = focal_from_homography(H, cx0, cy0)
    if f is not None:
        fx = fy = f
        cx, cy = cx0, cy0
        focal_source = 'reference'
    elif intrinsics:
        sx, sy = width / float(intrinsics['width']), height / float(intrinsics['height'])
        fx, fy, cx, cy = intrinsics['fx'] * sx, intrinsics['fy'] * sy, intrinsics['cx'] * sx, intrinsics['cy'] * sy
        focal_source = 'estimate'
        warnings.append('The focal length could not be recovered from the reference (the view is too frontal or the '
                        'corners are ill-conditioned); the supplied camera intrinsics were used.')
    else:
        fx = fy = width / (2.0 * math.tan(math.radians(DEFAULT_HFOV_DEG / 2.0)))
        cx, cy = cx0, cy0
        focal_source = 'default'
        warnings.append(f'The focal length could not be recovered from the reference (the view is too frontal or the '
                        f'corners are ill-conditioned); a default {DEFAULT_HFOV_DEG:.0f} degree horizontal field of '
                        f'view was assumed, so distances are approximate.')
    K = intrinsic_matrix(fx, fy, cx, cy)
    R, t = pose_from_homography(H, K, world)
    T = world_from_camera(R, t)
    if not np.all(np.isfinite(T)) or T[2, 3] <= 0:
        raise ArError('ar_bad_reference', 'The camera would be below the floor: click the corners in order around the '
                                          'rectangle, counter-clockwise as seen from above (corner 0 to 1 is +x, '
                                          '0 to 3 is +y)')
    residual = rms_residual(project(world3, K, T), corners)
    if residual > 3.0:
        warnings.append(f'The reference corners fit the camera model poorly ({residual:.1f} px); check their positions '
                        f'and the rectangle size.')
    return {'intrinsics': {'fx': float(fx), 'fy': float(fy), 'cx': float(cx), 'cy': float(cy),
                           'width': int(width), 'height': int(height)},
            'world_from_camera': T.tolist(), 'scale_source': 'reference', 'focal_source': focal_source,
            'residual_px': residual, 'camera_height_m': float(T[2, 3]), 'warnings': warnings}


# -- floor plane (depth-model estimate) -------------------------------------------------------------------------------

def fit_plane_ransac(points, iters=300, threshold=None, seed=0):
    """RANSAC plane fit on Nx3 camera-frame points -> (n, d, inlier mask), with n a unit normal and n.p + d = 0.

    The RNG is seeded, so the result is repeatable. Candidate normals are flipped to point "up" (against camera +y,
    since up is -y in OpenCV camera coordinates), and the score is the inlier count weighted by how upright the
    plane is, so a floor beats a wall of similar size. The winner is refitted on its inliers by SVD. `threshold` is the
    inlier distance in the unit of the points (default 2% of the median point distance)."""
    P = np.asarray(points, dtype=float)
    if P.ndim != 2 or P.shape[1] != 3 or len(P) < 3:
        raise ArError('ar_estimate_failed', 'Too few points to fit a floor plane')
    if threshold is None:
        threshold = 0.02 * float(np.median(np.linalg.norm(P, axis=1)))
    rng = np.random.default_rng(seed)
    best, best_score = None, -1.0
    for _ in range(iters):
        a, b, c = P[rng.choice(len(P), 3, replace=False)]
        n = np.cross(b - a, c - a)
        norm = np.linalg.norm(n)
        if norm < 1e-12:
            continue
        n = n / norm
        if n[1] > 0:
            n = -n
        d = -float(n @ a)
        count = int(np.count_nonzero(np.abs(P @ n + d) <= threshold))
        score = count * (0.5 + 0.5 * min(1.0, max(0.0, -n[1])))
        if score > best_score:
            best, best_score = (n, d), score
    if best is None:
        raise ArError('ar_estimate_failed', 'The points are degenerate; no floor plane found')
    n, d = best
    mask = np.abs(P @ n + d) <= threshold
    for _ in range(2):                                # refit on the inliers, then re-collect them
        if np.count_nonzero(mask) < 3:
            break
        Q = P[mask]
        centre = Q.mean(axis=0)
        n = np.linalg.svd(Q - centre, full_matrices=False)[2][-1]
        if n[1] > 0:
            n = -n
        d = -float(n @ centre)
        mask = np.abs(P @ n + d) <= threshold
    return n, d, mask


def pose_from_floor_plane(n, d):
    """T_FC (4x4) from a camera-frame floor plane n.p + d = 0 (unit n pointing up, camera above it, so d > 0 is the
    camera height).

    The floor origin is where the optical axis meets the floor and +x is the camera x axis projected onto the floor;
    z is the plane normal and y = z x x."""
    n = np.asarray(n, dtype=float)
    n = n / np.linalg.norm(n)
    if abs(n[2]) < 1e-3:
        raise ArError('ar_estimate_failed', 'The optical axis does not meet the estimated floor')
    s = -d / n[2]                                      # depth of the optical axis at the floor
    if not (s > 0 and d > 0):
        raise ArError('ar_estimate_failed', 'The estimated floor is not below the camera')
    ex = np.array([1.0, 0.0, 0.0]) - n[0] * n
    if np.linalg.norm(ex) < 1e-6:
        raise ArError('ar_estimate_failed', 'The estimated floor is degenerate')
    ex = ex / np.linalg.norm(ex)
    ey = np.cross(n, ex)
    R_FC = np.vstack([ex, ey, n])                      # rows: F axes expressed in C
    T = np.eye(4)
    T[:3, :3] = R_FC
    T[:3, 3] = -R_FC @ np.array([0.0, 0.0, s])
    return T


# -- image size from the header ---------------------------------------------------------------------------------------

_JPEG_NO_LENGTH = {0x01, *range(0xD0, 0xDA)}          # TEM, RSTn, SOI, EOI carry no length
_JPEG_SOF = {m for m in range(0xC0, 0xD0) if m not in (0xC4, 0xC8, 0xCC)}


def image_size_from_bytes(data):
    """(width, height) of a PNG, JPEG or WebP image, read from its header; ValueError when it cannot be read."""
    try:
        if data.startswith(b'\x89PNG\r\n\x1a\n') and data[12:16] == b'IHDR':
            return struct.unpack('>II', data[16:24])
        if data.startswith(b'\xff\xd8'):
            pos = 2
            while pos + 4 <= len(data):
                if data[pos] != 0xFF:
                    pos += 1
                    continue
                marker = data[pos + 1]
                if marker == 0xFF:
                    pos += 1
                    continue
                if marker in _JPEG_NO_LENGTH:
                    pos += 2
                    continue
                (length,) = struct.unpack('>H', data[pos + 2:pos + 4])
                if marker in _JPEG_SOF:
                    height, width = struct.unpack('>HH', data[pos + 5:pos + 9])
                    return width, height
                pos += 2 + length
        if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
            kind = data[12:16]
            if kind == b'VP8 ' and data[23:26] == b'\x9d\x01\x2a':
                width, height = struct.unpack('<HH', data[26:30])
                return width & 0x3FFF, height & 0x3FFF
            if kind == b'VP8L' and data[20:21] == b'\x2f':
                bits = int.from_bytes(data[21:25], 'little')
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            if kind == b'VP8X':
                return (int.from_bytes(data[24:27], 'little') + 1, int.from_bytes(data[27:30], 'little') + 1)
    except (struct.error, IndexError):
        pass
    raise ValueError('Cannot read the image size: the file is not a complete PNG, JPEG or WebP image')


def image_size(path):
    """(width, height) of the PNG, JPEG or WebP file at `path`, from its header (no image library)."""
    size = image_size_from_bytes(Path(path).read_bytes())
    if size[0] < 1 or size[1] < 1:
        raise ValueError('The image has no pixels')
    return size
