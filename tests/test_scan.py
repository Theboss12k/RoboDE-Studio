"""Site scan (docs/SITE_SCAN.md): the Python side. Unit tests on synthetic numbers, and one end-to-end run of
`scan_reconstruct` on a synthetic walk-around video (needs a CUDA GPU and the cached MoGe weights; marked `gpu`).

Fixture tests here use rendered video and ground truth, not real footage: they say nothing about phone videos."""
import asyncio
import json
import math
import sys
import time
import types
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent))

from studio.scan import ScanError, dense, floor, scale, sfm, video  # noqa: E402
from studio.schemas import ScanScale, SiteScan  # noqa: E402
from studio.tasks import handlers  # noqa: E402

CACHE = Path(__file__).parent / '.cache'


def run(coro):
    return asyncio.run(coro)


def ctx(out_dir=None, messages=None):
    return types.SimpleNamespace(out_dir=Path(out_dir) if out_dir else None,
                                 progress=(messages.append if messages is not None else (lambda text: None)))


def rotation(rng, tilt_deg=None):
    """A random rotation matrix (QR of a Gaussian matrix)."""
    q, r = np.linalg.qr(rng.normal(size=(3, 3)))
    q = q * np.sign(np.diag(r))
    return q if np.linalg.det(q) > 0 else q * np.array([1, 1, -1])


# -- depth alignment ----------------------------------------------------------------------------------------------------

def test_align_depth_recovers_scale_and_shift_among_outliers():
    rng = np.random.default_rng(1)
    z_model = rng.uniform(0.8, 6.0, 400)
    z_sparse = 2.5 * z_model + 0.4 + rng.normal(0, 0.02, 400)
    bad = rng.choice(400, 100, replace=False)                       # a quarter of the sparse points are wrong
    z_sparse[bad] += rng.uniform(-3.0, 3.0, 100)
    a, b, inliers, rms = dense.align_depth(z_model, z_sparse)
    assert a == pytest.approx(2.5, rel=0.01)
    assert b == pytest.approx(0.4, abs=0.05)
    assert 0.6 < inliers < 0.95
    assert rms < 0.05


def test_align_depth_is_scale_only_when_the_depth_range_is_narrow():
    rng = np.random.default_rng(2)
    z_model = rng.uniform(3.0, 3.2, 200)                            # scale and shift cannot be told apart here
    z_sparse = 1.7 * z_model + rng.normal(0, 0.01, 200)
    a, b, inliers, _ = dense.align_depth(z_model, z_sparse)
    assert b == 0.0
    assert a == pytest.approx(1.7, rel=0.01)
    assert inliers > 0.9
    a, b, inliers, _ = dense.align_depth(z_model[:2], z_sparse[:2])  # too few points
    assert math.isnan(a) and inliers == 0.0


def test_local_correction_follows_the_sparse_points_on_their_own_surface():
    h, w = 120, 160
    depth = np.full((h, w), 4.0, np.float32)
    depth[:, 80:] = 2.0                                              # a near object on the right, a far wall on the left
    rng = np.random.default_rng(3)
    uv = np.column_stack([rng.uniform(5, w - 5, 300), rng.uniform(5, h - 5, 300)])
    true = np.where(uv[:, 0] >= 80, 2.0 * 1.2, 4.0)                  # the near object is really 20% farther
    field = dense.local_correction(depth, uv, true)
    assert field[60, 120] == pytest.approx(1.2, rel=0.03)
    assert field[60, 30] == pytest.approx(1.0, abs=0.02)             # the far wall keeps its depth
    empty = dense.local_correction(depth, np.zeros((0, 2)), np.zeros(0))
    assert np.allclose(empty, 1.0)


# -- floor and alignment ------------------------------------------------------------------------------------------------

def test_alignment_is_rigid_and_puts_the_floor_at_z0():
    rng = np.random.default_rng(4)
    n = rng.normal(size=3)
    n /= np.linalg.norm(n)
    d = 1.7
    on_plane = rng.normal(size=(50, 3))
    on_plane -= np.outer(on_plane @ n + d, n)                        # n . p + d = 0
    origin = on_plane[0] + 0.3 * n
    A = floor.alignment(n, d, x_hint=rng.normal(size=3), origin=origin)
    R = A[:3, :3]
    assert np.allclose(R @ R.T, np.eye(3), atol=1e-12) and np.linalg.det(R) == pytest.approx(1.0)
    assert np.allclose(R @ n, [0, 0, 1], atol=1e-12)                 # the normal becomes +z
    assert np.allclose(floor.apply(A, on_plane)[:, 2], 0.0, atol=1e-9)
    assert np.allclose(floor.apply(A, origin[None])[0], [0, 0, 0.3 * 1.0 * 0 + float(n @ origin + d)], atol=1e-9)
    assert np.allclose(A[3], [0, 0, 0, 1])
    x_hint = rng.normal(size=3)
    A2 = floor.alignment(n, d, x_hint=x_hint)                        # the x axis follows the hint, projected on the floor
    expect = x_hint - (x_hint @ n) * n
    assert np.allclose(A2[0, :3], expect / np.linalg.norm(expect))


def room_cloud(rng, tilt_deg):
    """Points of a room (floor, table top, walls, ceiling) in a rotated frame, plus camera centres and the mean 'up'."""
    def plane(count, lo, hi, axis, value):
        p = rng.uniform(lo, hi, (count, 3))
        p[:, axis] = value
        return p + rng.normal(0, 0.004, p.shape)
    floor_pts = plane(20000, (0, 0, 0), (6, 5, 0), 2, 0.0)
    table = plane(3000, (2, 1.5, 0), (3.2, 2.3, 0), 2, 0.75)
    walls = np.vstack([plane(3000, (0, 0, 0), (0, 5, 2.6), 0, 0.0), plane(3000, (0, 0, 0), (6, 0, 2.6), 1, 0.0)])
    ceiling = plane(5000, (0, 0, 0), (6, 5, 0), 2, 2.6)
    t = np.linspace(0, 2 * np.pi, 40, endpoint=False)
    centres = np.column_stack([3 + 1.5 * np.cos(t), 2.5 + 1.5 * np.sin(t), np.full(40, 1.5)])
    Q = rotation(rng)
    tilt = math.radians(tilt_deg)
    up_world = np.array([math.sin(tilt), 0, math.cos(tilt)])          # the mean camera up is a little off the true normal
    return (np.vstack([floor_pts, table, walls, ceiling]) @ Q.T, centres @ Q.T, Q @ up_world, Q @ np.array([0, 0, 1.0]))


def test_find_floor_takes_the_lowest_big_plane_not_the_table_or_the_ceiling():
    rng = np.random.default_rng(5)
    points, centres, up, true_up = room_cloud(rng, tilt_deg=6)
    n, d = floor.find_floor(points, up, centres)
    assert math.degrees(math.acos(min(1.0, float(n @ true_up)))) < 0.5
    assert abs(centres.mean(axis=0) @ n + d - 1.5) < 0.02            # the cameras are 1.5 above the floor plane found
    # without the camera centres, a ceiling could win; with them it cannot: drop the floor and the table is the choice
    only_ceiling = points[points @ true_up > 2.0]
    with pytest.raises(ScanError) as error:
        floor.find_floor(only_ceiling, up, centres)
    assert error.value.code == 'scan_failed'


def test_floor_from_clicked_points_is_a_least_squares_plane():
    rng = np.random.default_rng(6)
    Q = rotation(rng)
    pts = np.column_stack([rng.uniform(0, 4, 8), rng.uniform(0, 3, 8), rng.normal(0, 0.003, 8) + 0.5]) @ Q.T
    n, d = floor.floor_from_points(pts, up=Q @ [0, 0, 1])
    assert math.degrees(math.acos(float(n @ (Q @ [0, 0, 1])))) < 1.0
    assert np.abs(pts @ n + d).max() < 0.01
    n2, _ = floor.floor_from_points(pts, up=-(Q @ [0, 0, 1]))          # oriented to the given up
    assert n2 @ (-(Q @ [0, 0, 1])) > 0.99
    with pytest.raises(ValueError):
        floor.floor_from_points(pts[:2], up=[0, 0, 1])
    line = np.outer(np.arange(4.0), [1, 0.5, 0.2])
    with pytest.raises(ValueError):
        floor.floor_from_points(line, up=[0, 0, 1])


def test_scan_floor_task_returns_floor_and_alignment():
    pts = [[0, 0, 1.0], [2, 0, 1.0], [0, 3, 1.0], [2, 3, 1.001]]
    out = run(handlers.scan_floor({'points': pts, 'camera_up': [0, 0, 1]}, ctx()))
    assert out['floor']['normal'] == pytest.approx([0, 0, 1], abs=1e-3)
    assert out['floor']['d'] == pytest.approx(-1.0, abs=1e-3)
    A = np.array(out['alignment'])
    assert A.shape == (4, 4)
    assert np.allclose(floor.apply(A, np.array(pts))[:, 2], 0.0, atol=2e-3)


# -- scale --------------------------------------------------------------------------------------------------------------

CAMERA = {'model': 'SIMPLE_RADIAL', 'fx': 800.0, 'fy': 800.0, 'cx': 480.0, 'cy': 270.0, 'k1': 0.03, 'width': 960,
          'height': 540}


def look_down_pose(position, target):
    """R_from_C of a camera at `position` looking at `target` (OpenCV axes; world up is +z)."""
    forward = np.asarray(target, float) - position
    forward /= np.linalg.norm(forward)
    right = np.cross(forward, [0, 0, 1.0])
    right /= np.linalg.norm(right)
    T = np.eye(4)
    T[:3, 0], T[:3, 1], T[:3, 2], T[:3, 3] = right, np.cross(forward, right), forward, position
    return T


def project_k(points, T, camera=CAMERA):
    pc = (np.asarray(points) - T[:3, 3]) @ T[:3, :3]
    xn = pc[:, :2] / pc[:, 2:3]
    xd = xn * (1 + camera['k1'] * np.sum(xn ** 2, axis=1, keepdims=True))
    return camera['fx'] * xd + [camera['cx'], camera['cy']]


def sheet_in_scan_units(true_factor, size_m=(0.297, 0.210), yaw_deg=15, centre=(1.0, 1.5)):
    """The 4 sheet corners on the floor z = 0 of a frame R whose unit is true_factor metres."""
    L, W = size_m[0] / true_factor, size_m[1] / true_factor
    c, s = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    local = [(-L / 2, -W / 2), (L / 2, -W / 2), (L / 2, W / 2), (-L / 2, W / 2)]
    return np.array([[centre[0] + c * x - s * y, centre[1] + s * x + c * y, 0.0] for x, y in local])


def test_sheet_scale_recovers_the_factor_through_a_distorting_camera():
    T = look_down_pose(np.array([0.3, -0.2, 0.8]), np.array([1.0, 1.5, 0.0]))     # 0.8 units above the floor
    corners = project_k(sheet_in_scan_units(2.0), T)
    out = scale.scale_from_sheet(CAMERA, T, {'normal': [0, 0, 1], 'd': 0.0}, corners.tolist(), [0.297, 0.210], frame=4)
    assert out['method'] == 'sheet' and out['frame'] == 4
    assert out['factor'] == pytest.approx(2.0, rel=1e-6)
    assert out['residual'] < 1e-6
    ScanScale.model_validate(out)


def test_sheet_scale_residual_and_errors():
    T = look_down_pose(np.array([0.3, -0.2, 0.8]), np.array([1.0, 1.5, 0.0]))
    plane = {'normal': [0, 0, 1], 'd': 0.0}
    corners = project_k(sheet_in_scan_units(2.0), T).tolist()
    ok = scale.scale_from_sheet(CAMERA, T, plane, corners, [0.297, 0.210 * 1.05])      # 5% off in one side: allowed
    assert 0.02 < ok['residual'] < 0.15
    with pytest.raises(ScanError) as error:                                              # sides disagree by 30%
        scale.scale_from_sheet(CAMERA, T, plane, corners, [0.297, 0.210 * 1.3])
    assert error.value.code == 'scan_bad_scale' and 'disagree' in str(error.value)
    with pytest.raises(ScanError) as error:                                              # rays that point above the horizon
        scale.scale_from_sheet(CAMERA, T, plane, [[100, 5], [200, 5], [200, 60], [100, 60]], [0.297, 0.210])
    assert error.value.code == 'scan_bad_scale'
    with pytest.raises(ScanError) as error:                                              # collinear corners
        scale.scale_from_sheet(CAMERA, T, plane, [[100, 300], [200, 300], [300, 300], [400, 300]], [0.297, 0.210])
    assert error.value.code == 'scan_bad_scale'
    with pytest.raises(ScanError):                                                       # a bow tie
        scale.scale_from_sheet(CAMERA, T, plane, [corners[0], corners[2], corners[1], corners[3]], [0.297, 0.210])
    with pytest.raises(ScanError):
        scale.scale_from_sheet(CAMERA, T, plane, corners, [0.297, -1])


def test_distance_scale_and_errors():
    out = scale.scale_from_distance([0, 0, 0], [3, 4, 0], 2.5)
    assert out['method'] == 'distance' and out['factor'] == pytest.approx(0.5) and out['residual'] == 0.0
    assert out['p1'] == [0.0, 0.0, 0.0] and out['distance_m'] == 2.5
    for args in (([0, 0, 0], [1, 1, 1], 0), ([0, 0, 0], [1, 1, 1], -2), ([1, 2, 3], [1, 2, 3], 1.0)):
        with pytest.raises(ScanError) as error:
            scale.scale_from_distance(*args)
        assert error.value.code == 'scan_bad_scale'


def test_scan_scale_task_for_both_methods():
    T = look_down_pose(np.array([0.3, -0.2, 0.8]), np.array([1.0, 1.5, 0.0]))
    scan = {'camera': CAMERA, 'floor': {'normal': [0, 0, 1], 'd': 0.0},
            'frames': [{'index': 0, 'world_from_camera': None}, {'index': 1, 'world_from_camera': T.tolist()}]}
    corners = project_k(sheet_in_scan_units(0.5), T).tolist()
    out = run(handlers.scan_scale({'scan': scan, 'method': 'sheet', 'frame': 1, 'corners_px': corners,
                                   'size_m': [0.297, 0.210]}, ctx()))
    assert out['factor'] == pytest.approx(0.5, rel=1e-6) and out['frame'] == 1 and out['corners_px'] == corners
    with pytest.raises(ScanError) as error:                                              # frame 0 never registered
        run(handlers.scan_scale({'scan': scan, 'method': 'sheet', 'frame': 0, 'corners_px': corners,
                                 'size_m': [0.297, 0.210]}, ctx()))
    assert error.value.code == 'scan_bad_scale'
    out = run(handlers.scan_scale({'scan': scan, 'method': 'distance', 'p1': [0, 0, 0], 'p2': [0, 0, 2.0],
                                   'distance_m': 1.0}, ctx()))
    assert out['factor'] == pytest.approx(0.5)
    with pytest.raises(ValueError):
        run(handlers.scan_scale({'scan': scan, 'method': 'tape'}, ctx()))


def test_scale_ignores_k1_when_the_frames_are_undistorted():
    T = look_down_pose(np.array([0.3, -0.2, 0.8]), np.array([1.0, 1.5, 0.0]))
    flat = {**CAMERA, 'k1': 0.0}
    corners = project_k(sheet_in_scan_units(2.0), T, flat).tolist()
    stored = {**CAMERA, 'undistorted': True}                                 # k1 = 0.03 is informational
    out = scale.scale_from_sheet(stored, T, {'normal': [0, 0, 1], 'd': 0.0}, corners, [0.297, 0.210])
    assert out['factor'] == pytest.approx(2.0, rel=1e-6)


# -- undistorted frames, colour and mesh helpers ------------------------------------------------------------------------

def line_residual(image, rows):
    """RMS distance (px) of the dark pixels in image rows `rows` (one centroid per column) from their least-squares line."""
    sub = image[rows[0]:rows[1], 100:540].mean(axis=2) < 128
    xs, ys = [], []
    for x in range(sub.shape[1]):
        r = np.nonzero(sub[:, x])[0]
        if len(r):
            xs.append(x)
            ys.append(r.mean())
    xs, ys = np.array(xs), np.array(ys)
    assert len(xs) > 300
    fit = np.polyval(np.polyfit(xs, ys, 1), xs)
    return float(np.sqrt(np.mean((ys - fit) ** 2)))


def test_undistort_frames_straightens_a_line_and_keeps_k(tmp_path):
    import cv2
    w, h, f, k1 = 640, 480, 500.0, -0.2
    cam = {'fx': f, 'fy': f, 'cx': w / 2, 'cy': h / 2, 'k1': k1, 'width': w, 'height': h}
    ideal = np.full((h, w, 3), 255, np.uint8)
    cv2.line(ideal, (0, 50), (w, 50), (0, 0, 0), 3)                          # a straight edge in the pinhole picture
    gx, gy = np.meshgrid(np.arange(w, dtype=np.float64), np.arange(h, dtype=np.float64))
    xu = sfm.undistort_norm(np.column_stack([gx.ravel(), gy.ravel()]), f, k1, w / 2, h / 2) * f + [w / 2, h / 2]
    distorted = cv2.remap(ideal, xu[:, 0].reshape(h, w).astype(np.float32), xu[:, 1].reshape(h, w).astype(np.float32),
                          cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))
    path = tmp_path / '0000.jpg'
    cv2.imwrite(str(path), distorted, [cv2.IMWRITE_JPEG_QUALITY, 98])
    before = line_residual(cv2.imread(str(path)), (0, 150))
    skipped = tmp_path / '0001.jpg'
    cv2.imwrite(str(skipped), distorted)
    skipped_bytes = skipped.read_bytes()
    n = dense.undistort_frames([path, skipped], [np.eye(4), None], cam)
    after = line_residual(cv2.imread(str(path)), (0, 150))
    assert n == 1
    assert before > 2.0 and after < 0.6 and after < before / 4
    assert cv2.imread(str(path)).shape[:2] == (h, w)
    assert skipped.read_bytes() == skipped_bytes                              # the unregistered frame is left alone


def test_colour_weights_skip_the_blurriest_fifth_and_favour_sharp_frames():
    s = np.arange(1.0, 11.0)
    w = dense.colour_weights(s)
    assert (w[:2] == 0).all() and (w[2:] > 0).all()
    assert w[-1] > w[2]
    assert (dense.colour_weights([5.0, 5.0]) > 0).all()                      # too few frames to drop any


def test_bilateral_vertex_colours_remove_speckle_and_keep_edges():
    n = 20
    faces = np.array([[i, i + 1, i + 2] for i in range(n - 2)])
    c = np.zeros((n, 3))
    c[10:] = 1.0                                                              # a hard colour edge
    c[4] = [0.08, 0.05, 0.05]                                                 # a mild speckle on the dark side
    out = dense.smooth_vertex_colours(c, faces)
    assert out[4, 0] < c[4, 0] * 0.6
    assert out[8].max() < 0.1 and out[12].min() > 0.9                         # the edge is not blurred across


def test_camera_undistorted_defaults_false_for_older_scans():
    from studio.schemas import ScanCamera
    old = ScanCamera.model_validate(CAMERA)
    assert old.undistorted is False
    assert ScanCamera.model_validate({**CAMERA, 'undistorted': True}).undistorted is True


def test_glb_carries_normals_and_colours(tmp_path):
    V = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0.1]], float)
    F = np.array([[0, 1, 2], [1, 3, 2]])
    N = np.tile([0, 0, 1.0], (4, 1))
    dense.write_glb(tmp_path / 'm.glb', V, F, np.full((4, 3), 128, np.uint8), N)
    assert glb_attributes(tmp_path / 'm.glb') >= {'POSITION', 'NORMAL', 'COLOR_0'}
    dense.write_glb(tmp_path / 'n.glb', V, F, np.full((4, 3), 128, np.uint8))     # normals computed when not given
    assert 'NORMAL' in glb_attributes(tmp_path / 'n.glb')


# -- camera model and tracks --------------------------------------------------------------------------------------------

def test_undistort_inverts_the_radial_projection():
    rng = np.random.default_rng(7)
    X = rng.uniform(-1, 1, (200, 3)) + [0, 0, 4]
    uv, depth = sfm.project(X, np.zeros((200, 3)), np.zeros((200, 3)), 700.0, -0.08, 480.0, 270.0)
    xu = sfm.undistort_norm(uv, 700.0, -0.08, 480.0, 270.0)
    assert np.allclose(xu, X[:, :2] / X[:, 2:3], atol=1e-9)
    assert np.all(depth > 0)


def test_triangulate_batch_and_rotation():
    rng = np.random.default_rng(8)
    X = rng.uniform(-1, 1, (50, 3)) + [0, 0, 5]
    R = np.array([[math.cos(0.2), 0, math.sin(0.2)], [0, 1, 0], [-math.sin(0.2), 0, math.cos(0.2)]])
    t = np.array([-1.0, 0.1, 0.0])
    P1 = np.broadcast_to(np.hstack([np.eye(3), np.zeros((3, 1))]), (50, 3, 4))
    P2 = np.broadcast_to(np.hstack([R, t[:, None]]), (50, 3, 4))
    x1 = X[:, :2] / X[:, 2:3]
    Xc = X @ R.T + t
    x2 = Xc[:, :2] / Xc[:, 2:3]
    assert np.allclose(sfm.triangulate_batch(P1, P2, x1, x2), X, atol=1e-8)
    import cv2
    rv = np.tile([0.1, -0.2, 0.3], (50, 1))
    assert np.allclose(sfm.rotate(X, rv), X @ cv2.Rodrigues(rv[0])[0].T, atol=1e-12)


def test_build_tracks_joins_chains_and_drops_inconsistent_ones():
    pairs = {(0, 1): (np.array([0, 1, 2]), np.array([0, 1, 2])), (1, 2): (np.array([0, 1]), np.array([5, 6])),
             (0, 2): (np.array([2]), np.array([6]))}
    track, frame, kp = sfm.build_tracks(3, [3, 3, 7], pairs)
    # keypoint 0: frames 0,1,2 (kp 0, 0, 5); keypoint 1: 0,1 (kp 1, 1) then frame 2 kp 6 via pair (1,2); keypoint 2 of frame 0
    # joins frame 2 kp 6 as well, so tracks {f0k1, f1k1, f2k6, f0k2, f1k2...} would hit frame 0 twice and is dropped
    sets = {}
    for t, f, k in zip(track, frame, kp):
        sets.setdefault(int(t), set()).add((int(f), int(k)))
    assert {(0, 0), (1, 0), (2, 5)} in sets.values()
    assert all(len({f for f, _ in s}) == len(s) for s in sets.values())          # never two observations in one frame
    assert not any((2, 6) in s for s in sets.values())


# -- video: probe, keyframes --------------------------------------------------------------------------------------------

def tiny_video(path, frames=20, fps=10, size=(160, 120), rotation_cw=None, container=None, blur=()):
    """A small textured test video: frames[i] blurred when i is in `blur`."""
    import av
    import cv2
    rng = np.random.default_rng(9)
    base = cv2.resize(rng.integers(0, 255, (size[1] // 4, size[0] // 4, 3), dtype=np.uint8), size, interpolation=cv2.INTER_CUBIC)
    with av.open(str(path), 'w', format=container) as out:
        stream = out.add_stream('libx264' if container != 'avi' else 'mpeg4', rate=fps)
        stream.width, stream.height, stream.pix_fmt = size[0], size[1], 'yuv420p'
        if rotation_cw:
            stream.set_display_rotation(-rotation_cw)                 # FFmpeg's angle is counter-clockwise
        for i in range(frames):
            img = np.roll(base, i * 3, axis=1)
            if i in blur:
                img = cv2.GaussianBlur(img, (0, 0), 6)
            for packet in stream.encode(av.VideoFrame.from_ndarray(img, format='rgb24')):
                out.mux(packet)
        for packet in stream.encode():
            out.mux(packet)


def test_probe_reads_the_container_header(tmp_path):
    path = tmp_path / 'a.mp4'
    tiny_video(path, frames=20, fps=10)
    info = video.probe(path)
    assert info['duration_s'] == pytest.approx(2.0, abs=0.15)
    assert (info['width'], info['height'], info['rotation_deg']) == (160, 120, 0)
    assert info['fps'] == pytest.approx(10.0)
    out = run(handlers.scan_probe({'video': {'path': str(path)}}, ctx()))
    assert out['bytes'] == path.stat().st_size and out['width'] == 160


@pytest.mark.parametrize('degrees', [90, 180, 270])
def test_probe_reads_the_display_rotation(tmp_path, degrees):
    path = tmp_path / 'r.mp4'
    tiny_video(path, frames=6, rotation_cw=degrees)
    assert video.probe(path)['rotation_deg'] == degrees


def test_display_matrix_rotation_is_clockwise_degrees():
    def matrix(cos, sin):
        fixed = [round(cos * 65536), round(sin * 65536), 0, round(-sin * 65536), round(cos * 65536), 0, 0, 0, 1 << 30]
        import struct
        return struct.pack('<9i', *fixed)
    assert video.display_rotation_cw(matrix(1, 0)) == 0
    assert video.display_rotation_cw(matrix(0, 1)) == 90             # the matrix FFmpeg writes for a 90 degree clockwise display turn
    assert video.display_rotation_cw(matrix(0, -1)) == 270
    assert video.display_rotation_cw(matrix(-1, 0)) == 180
    assert video.display_rotation_cw(b'short') is None


def test_probe_errors(tmp_path, monkeypatch):
    text = tmp_path / 'notes.mp4'
    text.write_text('this is not a video', encoding='utf-8')
    with pytest.raises(ScanError) as error:
        video.probe(text)
    assert error.value.code == 'scan_bad_video'
    avi = tmp_path / 'clip.avi'
    tiny_video(avi, frames=5, container='avi')
    with pytest.raises(ScanError) as error:                             # a real video, but not an MP4, MOV or WebM
        video.probe(avi)
    assert error.value.code == 'scan_bad_video' and 'MP4' in str(error.value)
    with pytest.raises(ScanError) as error:
        run(handlers.scan_probe({'video': {'path': str(text)}}, ctx()))
    assert error.value.code == 'scan_bad_video'
    with pytest.raises(FileNotFoundError):
        run(handlers.scan_probe({'video': {'path': str(tmp_path / 'missing.mp4')}}, ctx()))
    real = {'format': 'mov,mp4,m4a,3gp,3g2,mj2', 'width': 1920, 'height': 1080, 'fps': 30.0, 'rotation_deg': 0}
    monkeypatch.setattr(video, '_read_header', lambda path: {**real, 'duration_s': 181.0})
    with pytest.raises(ScanError) as error:
        video.probe(text)
    assert error.value.code == 'scan_too_long' and '181' in str(error.value)
    monkeypatch.setattr(video, '_read_header', lambda path: {**real, 'duration_s': 180.0})
    assert video.probe(text)['duration_s'] == 180.0                     # exactly the limit is fine


def test_keep_sharp_drops_under_40_percent_of_the_median():
    assert video.keep_sharp([100, 90, 110, 30, 35.9, 41, 100]) == [0, 1, 2, 5, 6]
    assert video.keep_sharp([]) == []


def test_keyframes_are_rotated_sharp_and_written(tmp_path):
    import cv2
    path = tmp_path / 'k.mp4'
    tiny_video(path, frames=60, fps=10, rotation_cw=90, blur=range(20, 24))      # 6 s, one window all blurry
    out = video.extract_keyframes(path, 90, tmp_path / 'frames')
    frames = out['frames']
    assert 15 <= len(frames) <= 19 and out['dropped'] >= 1                      # about 3 fps, the blurry window is gone
    assert [f['index'] for f in frames] == list(range(len(frames)))
    assert frames[0]['image'] == 'frames/0000.jpg'
    assert all(not 2.0 <= f['t_s'] < 2.3 for f in frames)
    img = cv2.imread(str(tmp_path / 'frames' / '0000.jpg'))
    assert img.shape[:2] == (160, 120) and (out['width'], out['height']) == (120, 160)       # turned upright
    assert not list((tmp_path / 'frames').glob('tmp_*'))
    t = [f['t_s'] for f in frames]
    assert t == sorted(t)


def test_keyframes_shrink_to_the_long_side_and_cap_the_count(tmp_path):
    import cv2
    path = tmp_path / 'k.mp4'
    tiny_video(path, frames=60, fps=10)
    out = video.extract_keyframes(path, 0, tmp_path / 'f', long_side=100, max_frames=5)
    assert len(out['frames']) <= 5
    assert max(out['width'], out['height']) == 100
    assert max(cv2.imread(str(tmp_path / 'f' / '0000.jpg')).shape[:2]) == 100


# -- the models and the handlers ----------------------------------------------------------------------------------------

def test_models_validate_and_keep_lists():
    scan = SiteScan.model_validate({
        'id': 'sc_0123456789', 'created': '2026-01-01T00:00:00Z', 'status': 'uploaded',
        'video': {'file': 'video.mp4', 'bytes': 10, 'duration_s': 1.0, 'width': 640, 'height': 480, 'fps': 30.0,
                  'rotation_deg': 0}})
    assert scan.placement.yaw_deg == 0 and scan.frames == [] and scan.mesh is None
    with pytest.raises(ValueError):
        ScanScale(method='distance', factor=0.0, residual=0.0)
    schema = json.dumps(SiteScan.model_json_schema())
    assert 'prefixItems' not in schema


def test_missing_backend_is_a_coded_error(tmp_path, monkeypatch):
    path = tmp_path / 'x.mp4'
    path.write_bytes(b'x')
    monkeypatch.setitem(sys.modules, 'open3d', None)                    # makes `import open3d` raise ImportError
    with pytest.raises(ScanError) as error:
        run(handlers.scan_reconstruct({'video': {'path': str(path)}, 'rotation_deg': 0,
                                       'out': {'frames_dir': 'frames', 'mesh_path': 'scan.glb'}}, ctx(tmp_path)))
    assert error.value.code == 'scan_backend_unavailable'


def test_reconstruct_output_paths_stay_inside_the_job_folder(tmp_path):
    path = tmp_path / 'x.mp4'
    path.write_bytes(b'x')
    with pytest.raises(ValueError, match='inside'):
        run(handlers.scan_reconstruct({'video': {'path': str(path)}, 'rotation_deg': 0,
                                       'out': {'frames_dir': '../elsewhere', 'mesh_path': 'scan.glb'}}, ctx(tmp_path)))


def test_too_few_frames_is_scan_failed_with_a_hint(tmp_path):
    path = tmp_path / 'short.mp4'
    tiny_video(path, frames=20)                                         # 2 s: about 6 keyframes
    pytest.importorskip('open3d')
    with pytest.raises(ScanError) as error:
        run(handlers.scan_reconstruct({'video': {'path': str(path)}, 'rotation_deg': 0,
                                       'out': {'frames_dir': 'frames', 'mesh_path': 'scan.glb'}}, ctx(tmp_path)))
    assert error.value.code == 'scan_failed' and 'light' in str(error.value)


# -- end to end ---------------------------------------------------------------------------------------------------------

def cuda_available():
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001 - a missing or blocked torch means no GPU run
        return False


@pytest.fixture(scope='session')
def synth():
    """The synthetic walk-around video and its ground truth, rendered once and cached in tests/.cache/."""
    import scan_synth
    path, gt = scan_synth.make_video(CACHE)
    return path, gt, scan_synth


def glb_attributes(path):
    """The vertex attribute names (POSITION, NORMAL, COLOR_0...) of the first primitive of a GLB, from its JSON chunk."""
    import struct
    data = Path(path).read_bytes()
    length, kind = struct.unpack('<II', data[12:20])
    assert kind == 0x4E4F534A                                           # the JSON chunk
    doc = json.loads(data[20:20 + length].decode('utf-8'))
    return set(doc['meshes'][0]['primitives'][0]['attributes'])


def best_rotation(gt_poses, est_poses):
    """The rotation W <- (est frame) that best matches the camera orientations (well conditioned, unlike the almost
    coplanar camera centres): the SVD of the summed R_gt R_est^T."""
    M = sum(g[:3, :3] @ e[:3, :3].T for g, e in zip(gt_poses, est_poses))
    U, _, Vt = np.linalg.svd(M)
    return U @ np.diag([1, 1, np.linalg.det(U @ Vt)]) @ Vt


@pytest.mark.gpu
@pytest.mark.skipif(not cuda_available(), reason='needs a CUDA GPU')
def test_scan_reconstruct_end_to_end_on_the_synthetic_room(synth, tmp_path, capsys):
    import trimesh
    video_path, gt, scan_synth = synth
    messages = []
    started = time.monotonic()
    result = run(handlers.scan_reconstruct({'video': {'path': str(video_path)}, 'rotation_deg': 0,
                                            'out': {'frames_dir': 'frames', 'mesh_path': 'scan.glb'}},
                                           ctx(tmp_path, messages)))
    total_s = time.monotonic() - started
    poses = [np.array(p) for p in gt['poses']]
    frames = result['frames']
    registered = [f for f in frames if f['world_from_camera'] is not None]
    cam = result['camera']
    SiteScan.model_validate({'id': 'sc_0123456789', 'created': 'x', 'status': 'reconstructed',
                             'video': {'file': 'v.mp4', 'bytes': 1, 'duration_s': 30.0, 'width': 960, 'height': 540,
                                       'fps': 10.0, 'rotation_deg': 0},
                             **{k: result[k] for k in ('camera', 'frames', 'floor', 'alignment', 'mesh', 'stats')}})

    def truth(frame):
        return poses[min(len(poses) - 1, int(round(frame['t_s'] * gt['fps'])))]

    # registered frames and the focal length (the frames keep the video's 960 x 540, so no resize factor)
    resize = cam['width'] / gt['width']
    focal_error = abs(cam['fx'] - gt['K'][0][0] * resize) / (gt['K'][0][0] * resize)
    fraction = len(registered) / len(frames)

    # the floor: the A-frame +z, in R, against the true up mapped into R by the camera orientations
    A = np.array(result['alignment'])
    R_wr = best_rotation([truth(f) for f in registered], [np.array(f['world_from_camera']) for f in registered])
    up_in_r = R_wr.T @ np.array([0, 0, 1.0])
    floor_angle = math.degrees(math.acos(min(1.0, float(A[2, :3] @ up_in_r))))

    # the scale from the sheet, with the ground-truth corner pixels in the registered frame that sees it best
    candidates = []
    for f in registered:
        view = scan_synth.sheet_view(truth(f))
        if view['visible']:
            candidates.append((view['area_px'], f, view))
    assert candidates, 'no registered keyframe sees the whole sheet'
    _, sheet_frame, view = max(candidates, key=lambda c: c[0])
    scan = {'camera': cam, 'frames': frames, 'floor': result['floor']}
    scale_result = run(handlers.scan_scale({'scan': scan, 'method': 'sheet', 'frame': sheet_frame['index'],
                                            'corners_px': view['pixels'], 'size_m': gt['sheet']['size_m']}, ctx()))
    factor = scale_result['factor']
    S = np.diag([factor, factor, factor, 1.0]) @ A                          # S_from_R

    # the mesh in metres, then in the world frame of the ground truth (rotation from the orientations, translation from
    # the camera centres: the sheet fixes the scale, the ground truth only fixes where the room is)
    mesh = trimesh.load(str(tmp_path / 'scan.glb'), force='mesh')
    V = np.asarray(mesh.vertices) @ S[:3, :3].T + S[:3, 3]
    R_ws = best_rotation([truth(f) for f in registered], [S @ np.array(f['world_from_camera']) for f in registered])
    centres_s = np.array([(S @ np.array(f['world_from_camera']))[:3, 3] for f in registered])
    centres_w = np.array([truth(f)[:3, 3] for f in registered])
    shift = (centres_w - centres_s @ R_ws.T).mean(axis=0)
    VW = V @ R_ws.T + shift
    cam_rms = float(np.sqrt(((centres_s @ R_ws.T + shift - centres_w) ** 2).sum(axis=1).mean()))

    room = gt['room']

    def wall(axis, value):
        other = 1 - axis
        keep = ((np.abs(VW[:, axis] - value) < 0.4) & (VW[:, 2] > 0.3) & (VW[:, 2] < 2.3) & (VW[:, other] > 0.3) &
                (VW[:, other] < room[other] - 0.3))
        assert keep.sum() > 500, f'wall {axis}={value} is missing from the mesh'
        return float(np.median(VW[keep, axis]))
    wall_errors = {}
    for axis, length in ((0, room[0]), (1, room[1])):
        measured = wall(axis, length) - wall(axis, 0.0)
        wall_errors['xy'[axis]] = (measured, abs(measured - length) / length)

    lo, size = np.array(gt['table']['min']), np.array(gt['table']['size'])
    world_mesh = trimesh.Trimesh(VW, mesh.faces, process=False)
    samples, face = world_mesh.sample(300000, return_index=True)
    top = ((world_mesh.face_normals[face][:, 2] > 0.9) & (samples[:, 2] > 0.4) &
           np.all((samples[:, :2] > lo[:2] + 0.1) & (samples[:, :2] < lo[:2] + size[:2] - 0.1), axis=1))
    assert top.sum() > 200, 'the table top is missing from the mesh'
    table_height = float(np.median(samples[top, 2]))
    table_error = abs(table_height - gt['table']['top_height']) / gt['table']['top_height']

    numbers = {
        'frames_registered': f"{len(registered)}/{len(frames)} ({100 * fraction:.0f}%)",
        'focal_px': f"{cam['fx']:.1f} (truth {gt['K'][0][0] * resize:.1f}, error {100 * focal_error:.2f}%)",
        'k1': f"{cam['k1']:.4f}",
        'reproj_px': f"{result['stats']['reproj_px']:.2f}",
        'points3d': result['stats']['points3d'],
        'floor_normal_error_deg': f'{floor_angle:.2f}',
        'sheet_factor': f"{factor:.5f} m/unit (side residual {scale_result['residual']:.4f}, frame {sheet_frame['index']})",
        'camera_centre_rms_m': f'{cam_rms:.4f}',
        'wall_x_m': f"{wall_errors['x'][0]:.3f} (truth {room[0]}, error {100 * wall_errors['x'][1]:.2f}%)",
        'wall_y_m': f"{wall_errors['y'][0]:.3f} (truth {room[1]}, error {100 * wall_errors['y'][1]:.2f}%)",
        'table_top_m': f"{table_height:.3f} (truth {gt['table']['top_height']}, error {100 * table_error:.2f}%)",
        'mesh': f"{result['mesh']['vertices']} vertices, {result['mesh']['triangles']} triangles",
        'seconds_per_stage': result['timings_s'],
        'seconds_total': round(total_s, 1),
        'progress': messages,
    }
    with capsys.disabled():
        print('\nsite scan end-to-end (synthetic room, rendered video; not real footage):')
        for key, value in numbers.items():
            print(f'  {key}: {value}')

    assert fraction >= 0.9
    assert focal_error <= 0.03
    assert floor_angle <= 2.0
    assert all(e <= 0.03 for _, e in wall_errors.values())
    assert table_error <= 0.03
    assert (tmp_path / 'scan.glb').is_file() and result['mesh']['triangles'] >= 10_000
    assert result['mesh']['file'] == 'scan.glb' and result['mesh']['triangles'] <= dense.MAX_TRIANGLES == 600_000
    assert (tmp_path / 'frames' / '0000.jpg').is_file()
    assert cam['undistorted'] is True
    import cv2
    first = cv2.imread(str(tmp_path / 'frames' / '0000.jpg'))
    assert first.shape[:2] == (cam['height'], cam['width'])
    assert glb_attributes(tmp_path / 'scan.glb') >= {'POSITION', 'NORMAL', 'COLOR_0'}
    assert np.abs(np.linalg.norm(np.asarray(mesh.vertex_normals), axis=1) - 1).max() < 1e-3
    assert total_s < 360
    assert any(m.startswith('Frames: ') and m.endswith('kept') or ' kept' in m for m in messages)
    assert any(m.startswith('Cameras: ') and 'registered' in m for m in messages)
    assert any(m.startswith('Depth: ') for m in messages) and any(m.startswith('Fusion: ') for m in messages)
    assert any(m.startswith('Mesh: ') for m in messages)


