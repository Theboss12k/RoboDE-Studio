"""Photo-AR geometry (server/studio/ar/geometry.py): synthetic cameras, RANSAC and image headers. Pure numpy, no model."""
import math
import struct
import zlib

import numpy as np
import pytest

from studio.ar import geometry as g

A4 = [0.297, 0.210]
WIDTH, HEIGHT, FOCAL = 4000, 3000, 1400.0
K = g.intrinsic_matrix(FOCAL, FOCAL, WIDTH / 2, HEIGHT / 2)
INTRINSICS = {'fx': FOCAL, 'fy': FOCAL, 'cx': WIDTH / 2, 'cy': HEIGHT / 2, 'width': WIDTH, 'height': HEIGHT}


def look_at(target, yaw_deg, pitch_deg, height):
    """T_FC of a camera `height` m above the floor, pitched `pitch_deg` down, heading rotated `yaw_deg` from +y, whose
    optical axis meets the floor at `target`."""
    yaw, pitch = math.radians(yaw_deg), math.radians(pitch_deg)
    heading = np.array([-math.sin(yaw), math.cos(yaw), 0.0])
    forward = np.array([heading[0] * math.cos(pitch), heading[1] * math.cos(pitch), -math.sin(pitch)])
    right = np.cross(forward, [0.0, 0.0, 1.0])
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    T = np.eye(4)
    T[:3, :3] = np.column_stack([right, down, forward])
    T[:3, 3] = np.array([target[0], target[1], 0.0]) - forward * (height / math.sin(pitch))
    return T


def rect_centre(size):
    return (size[0] / 2, size[1] / 2)


def synthetic(yaw=20.0, pitch=35.0, height=1.4):
    T = look_at(rect_centre(A4), yaw, pitch, height)
    world3 = np.column_stack([g.reference_points(A4), np.zeros(4)])
    return T, world3, g.project(world3, K, T)


def rotation_error_deg(R_a, R_b):
    cos = (np.trace(R_a.T @ R_b) - 1) / 2
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def test_projection_looks_at_the_target():
    T, _, _ = synthetic()
    centre = g.project([[A4[0] / 2, A4[1] / 2, 0.0]], K, T)[0]
    assert centre == pytest.approx([WIDTH / 2, HEIGHT / 2], abs=1e-6)
    assert T[2, 3] == pytest.approx(1.4)


def test_homography_maps_the_points():
    _, _, pixels = synthetic()
    H = g.homography_dlt(g.reference_points(A4), pixels)
    mapped = np.column_stack([g.reference_points(A4), np.ones(4)]) @ H.T
    assert mapped[:, :2] / mapped[:, 2:] == pytest.approx(pixels, abs=1e-6)


def test_homography_with_more_points():
    T, _, _ = synthetic()
    grid = np.array([[x, y] for x in (0, 0.1, 0.2, 0.297) for y in (0, 0.1, 0.21)])
    pixels = g.project(np.column_stack([grid, np.zeros(len(grid))]), K, T)
    H = g.homography_dlt(grid, pixels)
    mapped = np.column_stack([grid, np.ones(len(grid))]) @ H.T
    assert mapped[:, :2] / mapped[:, 2:] == pytest.approx(pixels, abs=1e-5)


def test_recovers_focal_and_pose():
    T, world3, pixels = synthetic()
    H = g.homography_dlt(g.reference_points(A4), pixels)
    f = g.focal_from_homography(H, WIDTH / 2, HEIGHT / 2)
    assert f == pytest.approx(FOCAL, rel=0.01)
    R, t = g.pose_from_homography(H, g.intrinsic_matrix(f, f, WIDTH / 2, HEIGHT / 2), world3)
    estimate = g.world_from_camera(R, t)
    assert np.linalg.norm(estimate[:3, 3] - T[:3, 3]) < 1e-3
    assert rotation_error_deg(estimate[:3, :3], T[:3, :3]) < 0.1
    assert np.linalg.det(R) == pytest.approx(1.0)
    assert g.rms_residual(g.project(world3, K, estimate), pixels) < 0.05


@pytest.mark.parametrize('yaw', [0.0, 45.0, 90.0])
def test_focal_when_the_sheet_is_square_or_diagonal_to_the_camera(yaw):
    """The common photo, a sheet square to the image (or at 45 degrees), leaves one usable constraint: still solved."""
    _, _, pixels = synthetic(yaw=yaw)
    H = g.homography_dlt(g.reference_points(A4), pixels)
    assert g.focal_from_homography(H, WIDTH / 2, HEIGHT / 2) == pytest.approx(FOCAL, rel=0.01)


def test_calibrate_from_reference_end_to_end():
    T, _, pixels = synthetic()
    result = g.calibrate(WIDTH, HEIGHT, pixels.tolist(), A4)
    assert result['focal_source'] == 'reference' and result['scale_source'] == 'reference'
    assert result['intrinsics']['fx'] == pytest.approx(FOCAL, rel=0.01)
    assert result['intrinsics']['cx'] == WIDTH / 2
    estimate = np.array(result['world_from_camera'])
    assert np.linalg.norm(estimate[:3, 3] - T[:3, 3]) < 2e-3          # the focal error (<1%) moves the camera a little
    assert rotation_error_deg(estimate[:3, :3], T[:3, :3]) < 0.1
    assert result['camera_height_m'] == pytest.approx(1.4, abs=2e-3)
    assert result['residual_px'] < 0.05
    assert result['warnings'] == []


def test_pose_with_the_true_focal_is_exact():
    T, world3, pixels = synthetic()
    H = g.homography_dlt(g.reference_points(A4), pixels)
    R, t = g.pose_from_homography(H, K, world3)
    estimate = g.world_from_camera(R, t)
    assert np.linalg.norm(estimate[:3, 3] - T[:3, 3]) < 1e-3
    assert rotation_error_deg(estimate[:3, :3], T[:3, :3]) < 0.1


def frontal():
    """The A4 seen straight from above, 1.4 m up."""
    T = np.eye(4)
    T[:3, :3] = np.column_stack([[1, 0, 0], [0, -1, 0], [0, 0, -1]])
    T[:3, 3] = [A4[0] / 2, A4[1] / 2, 1.4]
    pixels = g.project(np.column_stack([g.reference_points(A4), np.zeros(4)]), K, T)
    return T, pixels


def test_fronto_parallel_focal_is_none():
    _, pixels = frontal()
    H = g.homography_dlt(g.reference_points(A4), pixels)
    assert g.focal_from_homography(H, WIDTH / 2, HEIGHT / 2) is None


def test_fronto_parallel_falls_back_to_default_fov():
    _, pixels = frontal()
    result = g.calibrate(WIDTH, HEIGHT, pixels.tolist(), A4)
    assert result['focal_source'] == 'default'
    assert result['intrinsics']['fx'] == pytest.approx(WIDTH / (2 * math.tan(math.radians(30))))
    assert result['intrinsics']['cx'] == WIDTH / 2 and result['intrinsics']['cy'] == HEIGHT / 2
    assert result['warnings'] and result['camera_height_m'] > 0


def test_fronto_parallel_uses_given_intrinsics():
    _, pixels = frontal()
    result = g.calibrate(WIDTH, HEIGHT, pixels.tolist(), A4, INTRINSICS)
    assert result['focal_source'] == 'estimate'
    assert result['intrinsics']['fx'] == pytest.approx(FOCAL)
    assert result['warnings']
    assert result['camera_height_m'] == pytest.approx(1.4, abs=1e-3)       # the given focal is the true one


def test_given_intrinsics_are_scaled_to_the_photo():
    _, pixels = frontal()
    half = g.calibrate(WIDTH // 2, HEIGHT // 2, (pixels / 2).tolist(), A4, INTRINSICS)
    assert half['intrinsics']['fx'] == pytest.approx(FOCAL / 2)
    assert half['intrinsics']['width'] == WIDTH // 2


@pytest.mark.parametrize('corners', [
    [[100, 100], [200, 200], [300, 300], [400, 400]],               # collinear
    [[100, 100], [100, 100], [100, 100], [100, 100]],               # one point
    [[0, 0], [100, 100], [100, 0], [0, 100]],                       # self-intersecting (bow tie)
    [[0, 0], [100, 0], [50, 10], [100, 100]],                       # concave
])
def test_bad_reference_is_coded(corners):
    with pytest.raises(g.ArError) as raised:
        g.calibrate(WIDTH, HEIGHT, corners, A4)
    assert raised.value.code == 'ar_bad_reference'


def test_mirrored_corner_order_puts_the_camera_below_the_floor():
    _, _, pixels = synthetic()
    mirrored = pixels[[0, 3, 2, 1]]
    with pytest.raises(g.ArError) as raised:
        g.calibrate(WIDTH, HEIGHT, mirrored.tolist(), A4)
    assert raised.value.code == 'ar_bad_reference'


def test_ransac_recovers_a_noisy_plane_with_outliers():
    rng = np.random.default_rng(7)
    n_true = np.array([0.1, -0.9, 0.3])
    n_true /= np.linalg.norm(n_true)
    u = np.cross(n_true, [1.0, 0.0, 0.0])
    u /= np.linalg.norm(u)
    v = np.cross(n_true, u)
    origin = np.array([0.0, 1.4, 2.0])
    count = 1000
    a, b = rng.uniform(-1, 1, (2, count))
    points = origin + np.outer(a, u) + np.outer(b, v) + np.outer(rng.normal(0, 0.005, count), n_true)
    outliers = rng.random(count) < 0.30
    points[outliers] = rng.uniform(-3, 3, (int(outliers.sum()), 3)) + [0, 0, 2]
    n, d, mask = g.fit_plane_ransac(points, threshold=0.02, seed=1)
    angle = math.degrees(math.acos(min(1.0, abs(float(n @ n_true)))))
    assert angle < 1.0
    assert n[1] < 0                                                  # up is -y
    assert float(n @ origin + d) == pytest.approx(0.0, abs=0.01)
    assert mask[~outliers].mean() > 0.95
    n2, d2, _ = g.fit_plane_ransac(points, threshold=0.02, seed=1)   # seeded: repeatable
    assert np.array_equal(n, n2) and d == d2


def test_pose_from_floor_plane():
    T = look_at((0.0, 0.0), 20.0, 35.0, 1.4)
    R_CF = T[:3, :3].T
    n = R_CF @ np.array([0.0, 0.0, 1.0])                             # the floor normal in the camera frame
    d = float(T[2, 3])                                               # camera height: n.p + d = 0 on the floor
    estimate = g.pose_from_floor_plane(n, d)
    assert estimate[2, 3] == pytest.approx(1.4)
    assert estimate[2, :3] == pytest.approx(n)
    assert np.linalg.det(estimate[:3, :3]) == pytest.approx(1.0)
    s = -d / n[2]
    assert (estimate @ np.array([0.0, 0.0, s, 1.0]))[:3] == pytest.approx([0, 0, 0], abs=1e-9)
    assert estimate[0, 1] == pytest.approx(-n[0] * n[1] / math.sqrt(1 - n[0] ** 2))   # x axis = e1 projected on the floor


def test_pose_from_floor_plane_rejects_a_floor_above_the_camera():
    with pytest.raises(g.ArError) as raised:
        g.pose_from_floor_plane([0.0, -0.8, 0.6], -1.4)
    assert raised.value.code == 'ar_estimate_failed'


# -- image headers ----------------------------------------------------------------------------------------------------

def png_bytes(width, height):
    ihdr = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    chunk = lambda kind, data: struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IEND', b'')


def jpeg_bytes(width, height):
    """Headers only (SOI, JFIF, a comment, SOF0, EOI): enough for the size and the signature, not decodable."""
    app0 = b'\xff\xe0' + struct.pack('>H', 16) + b'JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
    com = b'\xff\xfe' + struct.pack('>H', 2 + 5) + b'hello'
    sof = b'\xff\xc0' + struct.pack('>HBHHB', 17, 8, height, width, 3) + bytes([1, 0x11, 0, 2, 0x11, 1, 3, 0x11, 1])
    return b'\xff\xd8' + app0 + com + sof + b'\xff\xd9'


def webp_bytes(kind, width, height):
    if kind == 'VP8 ':
        body = b'\x10\x02\x00' + b'\x9d\x01\x2a' + struct.pack('<HH', width, height)
    elif kind == 'VP8L':
        body = b'\x2f' + (((height - 1) << 14) | (width - 1)).to_bytes(4, 'little')
    else:
        body = b'\x00\x00\x00\x00' + (width - 1).to_bytes(3, 'little') + (height - 1).to_bytes(3, 'little')
    chunk = kind.encode() + struct.pack('<I', len(body)) + body
    return b'RIFF' + struct.pack('<I', 4 + len(chunk)) + b'WEBP' + chunk


def test_image_size_of_png_jpeg_and_webp(tmp_path):
    cases = [('a.png', png_bytes(4000, 3000)), ('a.jpg', jpeg_bytes(4096, 2731)), ('a.jpg2', jpeg_bytes(5, 3)),
             ('a.webp', webp_bytes('VP8 ', 640, 480)), ('b.webp', webp_bytes('VP8L', 1234, 567)),
             ('c.webp', webp_bytes('VP8X', 3000, 2000))]
    expected = [(4000, 3000), (4096, 2731), (5, 3), (640, 480), (1234, 567), (3000, 2000)]
    for (name, payload), size in zip(cases, expected):
        path = tmp_path / name
        path.write_bytes(payload)
        assert g.image_size(path) == size


def test_image_size_rejects_garbage(tmp_path):
    path = tmp_path / 'x.bin'
    path.write_bytes(b'not an image at all')
    with pytest.raises(ValueError):
        g.image_size(path)
    path.write_bytes(png_bytes(10, 10)[:14])
    with pytest.raises(ValueError):
        g.image_size(path)
