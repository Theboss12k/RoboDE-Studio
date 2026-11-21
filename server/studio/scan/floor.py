"""The floor of a scan and the aligned frame A (z up, floor at z = 0, scan units, no scale).

`A_from_R` is a rigid 4x4: rotation rows (x, y, z) with z the floor normal, and a translation that puts the origin on
the floor. Everything here is numpy; the numbers come from code (RANSAC and SVD), never from a model.
"""
import numpy as np

from . import ScanError  # noqa: F401  (re-exported for callers that catch scan errors from this module)


def camera_up(poses):
    """Mean camera "up" (the negated y axis of each registered R_from_C camera, unit) in R, and the mean camera
    "right" axis. `poses` are 4x4 arrays or None."""
    ok = [np.asarray(p, dtype=float) for p in poses if p is not None]
    if not ok:
        raise ValueError('No registered camera')
    up = -np.mean([p[:3, 1] for p in ok], axis=0)
    right = np.mean([p[:3, 0] for p in ok], axis=0)
    return up / np.linalg.norm(up), right


def fit_plane(points):
    """Least-squares plane (n, d) through Nx3 points, n unit, n . p + d = 0, by SVD of the centred points."""
    P = np.asarray(points, dtype=float)
    centre = P.mean(axis=0)
    n = np.linalg.svd(P - centre, full_matrices=False)[2][-1]
    return n / np.linalg.norm(n), -float(n @ centre)


def _refit(P, n, d, threshold, up):
    mask = np.abs(P @ n + d) <= threshold
    for _ in range(3):
        if mask.sum() < 3:
            break
        n, d = fit_plane(P[mask])
        if n @ up < 0:
            n, d = -n, -d
        mask = np.abs(P @ n + d) <= threshold
    return n, d, mask


def horizontal_planes(points, up, threshold, max_planes=6, iters=800, max_tilt_deg=25.0, seed=0, max_points=40000):
    """Near-horizontal planes found by sequential RANSAC: a list of (n, d, inlier count), n oriented up (n . up > 0),
    largest first. Hypotheses are triples whose normal is within `max_tilt_deg` of `up`; each plane is refitted on its
    inliers by SVD, then its inliers are removed before the next search."""
    P = np.asarray(points, dtype=float)
    P = P[np.all(np.isfinite(P), axis=1)]
    rng = np.random.default_rng(seed)
    if len(P) > max_points:
        P = P[rng.choice(len(P), max_points, replace=False)]
    up = np.asarray(up, dtype=float) / np.linalg.norm(up)
    cos_min = np.cos(np.radians(max_tilt_deg))
    remaining = P
    found = []
    for _ in range(max_planes):
        if len(remaining) < 30:
            break
        tri = rng.integers(0, len(remaining), size=(iters, 3))
        a, b, c = remaining[tri[:, 0]], remaining[tri[:, 1]], remaining[tri[:, 2]]
        n = np.cross(b - a, c - a)
        norm = np.linalg.norm(n, axis=1)
        n = n / np.where(norm > 1e-12, norm, 1.0)[:, None]
        n = np.where((n @ up < 0)[:, None], -n, n)
        ok = (norm > 1e-12) & (n @ up >= cos_min)
        if not ok.any():
            break
        n, a = n[ok], a[ok]
        d = -np.einsum('ij,ij->i', n, a)
        counts = (np.abs(remaining @ n.T + d) <= threshold).sum(axis=0)
        best = int(np.argmax(counts))
        nb, db, mask = _refit(remaining, n[best], d[best], threshold, up)
        if mask.sum() < 30 or nb @ up < cos_min:
            break
        found.append((nb, float(db), int(mask.sum())))
        remaining = remaining[~mask]
    return found


def find_floor(points, up, centres=None, threshold=None, seed=0):
    """The floor plane (n, d) in R: n unit, oriented up, n . p + d = 0, from Nx3 scene points.

    Among the near-horizontal planes (within 25 degrees of the mean camera up) that hold at least 35% as many points as
    the biggest one, and with at least 90% of the camera centres `centres` above them (a ceiling is excluded), it takes
    the lowest: a table top is above the floor. `threshold` defaults to 0.8% of the point cloud's extent.
    Raises ScanError('scan_failed') when there is no such plane."""
    P = np.asarray(points, dtype=float)
    P = P[np.all(np.isfinite(P), axis=1)]
    if len(P) < 100:
        raise ScanError('scan_failed', 'Too few points to find the floor')
    if threshold is None:
        lo, hi = np.percentile(P, [2, 98], axis=0)
        threshold = 0.008 * float(np.max(hi - lo))
    planes = horizontal_planes(P, up, threshold, seed=seed)
    if centres is not None and len(centres):
        C = np.asarray(centres, dtype=float)
        planes = [p for p in planes if np.mean(C @ p[0] + p[1] > 0) >= 0.9]
    if not planes:
        raise ScanError('scan_failed', 'No floor was found in the scan. Film some bare floor, with the camera held '
                                       'upright, and move slower.')
    biggest = max(p[2] for p in planes)
    cand = [p for p in planes if p[2] >= 0.35 * biggest]
    ref = np.mean(np.asarray(centres, dtype=float), axis=0) if centres is not None and len(centres) else P.mean(axis=0)
    n, d, _ = max(cand, key=lambda p: ref @ p[0] + p[1])      # farthest below the cameras
    return n, d


def floor_from_points(points, up):
    """The floor (n, d) from clicked points (>= 3, in R): a least-squares plane, oriented to `up`.
    Raises ValueError when the points are (nearly) on one line."""
    P = np.asarray(points, dtype=float)
    if P.ndim != 2 or P.shape[1] != 3 or len(P) < 3 or not np.all(np.isfinite(P)):
        raise ValueError('The floor needs at least 3 points [x, y, z]')
    s = np.linalg.svd(P - P.mean(axis=0), compute_uv=False)
    if s[1] < 1e-6 * max(s[0], 1e-12):
        raise ValueError('The floor points are on one line; pick points spread over the floor')
    n, d = fit_plane(P)
    up = np.asarray(up, dtype=float)
    if n @ up < 0:
        n, d = -n, -d
    return n, d


def alignment(n, d, x_hint=None, origin=None):
    """A_from_R (4x4, rigid): maps the plane n . p + d = 0 to z = 0 with n to +z.

    The x axis is `x_hint` projected onto the floor (default: the R axis least aligned with n); the origin is `origin`
    (default: R's origin) dropped onto the floor along n."""
    n = np.asarray(n, dtype=float)
    n = n / np.linalg.norm(n)
    if x_hint is None:
        x_hint = np.eye(3)[int(np.argmin(np.abs(n)))]
    x = np.asarray(x_hint, dtype=float)
    x = x - (x @ n) * n
    if np.linalg.norm(x) < 1e-9:
        x = np.eye(3)[int(np.argmin(np.abs(n)))]
        x = x - (x @ n) * n
    x = x / np.linalg.norm(x)
    y = np.cross(n, x)
    Rm = np.vstack([x, y, n])
    o = np.zeros(3) if origin is None else np.asarray(origin, dtype=float)
    o = o - (n @ o + d) * n
    A = np.eye(4)
    A[:3, :3] = Rm
    A[:3, 3] = -Rm @ o
    return A


def apply(A, points):
    """Transform Nx3 points by the 4x4 matrix A."""
    P = np.atleast_2d(np.asarray(points, dtype=float))
    return P @ np.asarray(A)[:3, :3].T + np.asarray(A)[:3, 3]
