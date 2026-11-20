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
