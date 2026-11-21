"""Metric scale of a scan: a sheet of known size on the floor, or a measured distance (docs/SITE_SCAN.md).

`factor` converts scan units to metres: S = factor * A. Failures raise ScanError('scan_bad_scale').
"""
import numpy as np

from . import ScanError
from .sfm import undistort_norm

MAX_RESIDUAL = 0.15


def pixel_rays(camera, pose, corners_px):
    """Rays through pixels: (origin (3,), directions (n, 3) unit) in R, for a camera dict (fx, cx, cy, k1) and the frame
    pose R_from_C (4x4)."""
    T = np.asarray(pose, dtype=float)
    xu = undistort_norm(np.asarray(corners_px, dtype=float), camera['fx'], 0.0 if camera.get('undistorted') else camera['k1'], camera['cx'], camera['cy'])
    d = np.column_stack([xu, np.ones(len(xu))]) @ T[:3, :3].T
    return T[:3, 3], d / np.linalg.norm(d, axis=1, keepdims=True)


def floor_points(camera, pose, pixels, plane):
    """The R-frame points where the pixel rays meet the floor plane (n, d). ScanError scan_bad_scale when a ray points
    away from the floor or runs parallel to it."""
    o, d = pixel_rays(camera, pose, pixels)
    n = np.asarray(plane['normal'], dtype=float)
    denom = d @ n
    t = -(n @ o + plane['d']) / np.where(np.abs(denom) > 1e-9, denom, np.nan)
    if not np.all(np.isfinite(t)) or np.any(t <= 0):
        raise ScanError('scan_bad_scale', 'The sheet corners do not land on the floor in that frame; pick a frame that '
                                          'sees the sheet lying on the floor, and click its four corners')
    return o + t[:, None] * d


def check_quad(corners):
    """ScanError scan_bad_scale unless the 4 pixel corners form a convex, non-degenerate quadrilateral."""
    c = np.asarray(corners, dtype=float)
    if c.shape != (4, 2) or not np.all(np.isfinite(c)):
        raise ScanError('scan_bad_scale', 'The sheet needs 4 corner points [u, v]')
    edges = np.roll(c, -1, axis=0) - c
    turns = [edges[i, 0] * edges[(i + 1) % 4, 1] - edges[i, 1] * edges[(i + 1) % 4, 0] for i in range(4)]
    eps = max(1e-9, 1e-4 * float(np.max(np.sum(edges * edges, axis=1))))
    if not (all(t > eps for t in turns) or all(t < -eps for t in turns)):
        raise ScanError('scan_bad_scale', 'The sheet corners must form a convex four-sided shape, clicked in order around '
                                          'the sheet')


def scale_from_sheet(camera, pose, plane, corners_px, size_m, frame=None):
    """ScanScale-shaped dict from a sheet of `size_m` = [side 0->1, side 1->2] metres whose 4 corners were clicked in
    order at `corners_px` in a frame with pose R_from_C.

    The corner rays are cast through the camera and the pose and intersected with the floor plane in R. For each pair of
    opposite sides the factor is the mean of true / measured over the two sides; `factor` is the mean of the two pair
    factors and `residual` their relative difference |f1 - f2| / factor. ScanError scan_bad_scale for rays that miss the
    floor, a degenerate quad, or a residual above 0.15."""
    check_quad(corners_px)
    size = [float(s) for s in (size_m or [])]
    if len(size) != 2 or min(size) <= 0:
        raise ScanError('scan_bad_scale', 'The sheet size needs two positive side lengths in metres')
    P = floor_points(camera, pose, corners_px, plane)
    side = np.linalg.norm(np.roll(P, -1, axis=0) - P, axis=1)            # sides 0->1, 1->2, 2->3, 3->0
    if np.min(side) < 1e-9:
        raise ScanError('scan_bad_scale', 'The sheet corners coincide on the floor')
    f1 = float(np.mean([size[0] / side[0], size[0] / side[2]]))
    f2 = float(np.mean([size[1] / side[1], size[1] / side[3]]))
    factor = 0.5 * (f1 + f2)
    residual = abs(f1 - f2) / factor
    if residual > MAX_RESIDUAL:
        raise ScanError('scan_bad_scale', f'The sheet sides disagree by {residual * 100:.0f}% (limit '
                                          f'{MAX_RESIDUAL * 100:.0f}%); check the corner order and the sheet size')
    return {'method': 'sheet', 'factor': factor, 'residual': float(residual), 'frame': frame,
            'corners_px': [[float(u), float(v)] for u, v in corners_px], 'size_m': size, 'p1': None, 'p2': None,
            'distance_m': None}


def scale_from_distance(p1, p2, distance_m):
    """ScanScale-shaped dict from two R-frame points a known distance apart. ScanError scan_bad_scale when the distance
    is not positive or the points coincide."""
    try:
        a, b = np.asarray(p1, dtype=float), np.asarray(p2, dtype=float)
        dist = float(distance_m)
    except (TypeError, ValueError) as error:
        raise ScanError('scan_bad_scale', 'The distance method needs p1, p2 and distance_m') from error
    if a.shape != (3,) or b.shape != (3,) or not (np.all(np.isfinite(a)) and np.all(np.isfinite(b))):
        raise ScanError('scan_bad_scale', 'p1 and p2 must be [x, y, z] points')
    if not dist > 0:
        raise ScanError('scan_bad_scale', 'The distance must be greater than zero')
    measured = float(np.linalg.norm(a - b))
    if measured < 1e-9:
        raise ScanError('scan_bad_scale', 'The two points are the same; click two different points')
    return {'method': 'distance', 'factor': dist / measured, 'residual': 0.0, 'frame': None, 'corners_px': None,
            'size_m': None, 'p1': [float(v) for v in a], 'p2': [float(v) for v in b], 'distance_m': dist}
