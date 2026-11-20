"""Capsule vs oriented box distance (numpy). Capsules are sampled along their axis; boxes are z-yawed cuboids."""
from __future__ import annotations

import numpy as np


def point_box_distance(points, center, size, yaw=0.0):
    """Distance from each point (N, 3) to the solid box (0 inside)."""
    pts = np.atleast_2d(np.asarray(points, float)) - np.asarray(center, float)
    c, s = np.cos(-yaw), np.sin(-yaw)
    local = np.stack([c * pts[:, 0] - s * pts[:, 1], s * pts[:, 0] + c * pts[:, 1], pts[:, 2]], axis=1)
    out = np.maximum(np.abs(local) - np.asarray(size, float) / 2, 0.0)
    return np.linalg.norm(out, axis=1)


def capsule_box_distance(a, b, radius, center, size, yaw=0.0, step=0.02):
    """Gap between the capsule surface (segment a-b, `radius`) and the box; <= 0 means touching or overlapping."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    n = max(2, int(np.ceil(np.linalg.norm(b - a) / step)) + 1)
    pts = a + np.linspace(0.0, 1.0, n)[:, None] * (b - a)
    return float(point_box_distance(pts, center, size, yaw).min() - radius)


def segment_distance(a0, a1, b0, b1) -> float:
    """Closest distance between the segments a0-a1 and b0-b1 (closed form, parameters clamped to [0, 1]; robust to
    parallel and zero-length segments)."""
    p1, q1, p2, q2 = (np.asarray(v, float) for v in (a0, a1, b0, b1))
    d1, d2, r = q1 - p1, q2 - p2, p1 - p2
    a, e, f = float(d1 @ d1), float(d2 @ d2), float(d2 @ r)
    eps = 1e-12
    if a <= eps and e <= eps:                      # both are points
        return float(np.linalg.norm(r))
    if a <= eps:                                   # first is a point
        s, t = 0.0, float(np.clip(f / e, 0.0, 1.0))
    else:
        c = float(d1 @ r)
        if e <= eps:                               # second is a point
            s, t = float(np.clip(-c / a, 0.0, 1.0)), 0.0
        else:
            b = float(d1 @ d2)
            denom = a * e - b * b
            s = float(np.clip((b * f - c * e) / denom, 0.0, 1.0)) if denom > eps * a * e else 0.0   # parallel: any s
            t = (b * s + f) / e
            if t < 0.0:
                t, s = 0.0, float(np.clip(-c / a, 0.0, 1.0))
            elif t > 1.0:
                t, s = 1.0, float(np.clip((b - c) / a, 0.0, 1.0))
    return float(np.linalg.norm((p1 + d1 * s) - (p2 + d2 * t)))


def capsule_capsule_distance(a0, a1, ra, b0, b1, rb) -> float:
    """Gap between two capsules (segment a0-a1 with radius ra, b0-b1 with rb); <= 0 means touching or overlapping."""
    return segment_distance(a0, a1, b0, b1) - ra - rb
