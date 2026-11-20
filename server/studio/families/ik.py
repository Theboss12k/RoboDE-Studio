"""Generic bounded damped-least-squares inverse kinematics (numpy) and small transform helpers.

`solve` works on any forward-kinematics function `fk(q) -> 4x4`: joint-limit clamping, several seeds (given, caller
guesses, zero, random inside the limits from a fixed-seed RNG), a position target and an optional tool-down
constraint (tool z axis pointing along world -z).
"""
from __future__ import annotations

import numpy as np

DOWN = np.array([0.0, 0.0, -1.0])
TOL_M = 1e-3            # accepted position error
TOL_ORI = 0.02          # accepted tool-down error, rad
LAMBDA = 0.05


def rx(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[1, 0, 0, 0], [0, c, -s, 0], [0, s, c, 0], [0, 0, 0, 1.0]])


def ry(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, 0, s, 0], [0, 1, 0, 0], [-s, 0, c, 0], [0, 0, 0, 1.0]])


def rz(a):
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, -s, 0, 0], [s, c, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1.0]])


def tr(x, y, z):
    T = np.eye(4)
    T[:3, 3] = (x, y, z)
    return T


def tool_down_error(T):
    """Angle in rad between the tool z axis and straight down."""
    return float(np.arccos(np.clip(T[:3, 2] @ DOWN, -1.0, 1.0)))


def _residual(fk, q, target, tool_down, w):
    T = fk(q)
    r = T[:3, 3] - target
    if tool_down:
        r = np.concatenate([r, w * np.cross(T[:3, 2], DOWN)])
    return r


def _dls(fk, q, lo, hi, target, active, tool_down, max_iter, w=0.3):
    q = np.clip(q, lo, hi)
    for it in range(max_iter):
        r = _residual(fk, q, target, tool_down, w)
        if np.linalg.norm(r[:3]) < 1e-5 and (not tool_down or np.linalg.norm(r[3:]) < 1e-5):
            return q, it
        J = np.zeros((r.size, len(active)))
        for k, i in enumerate(active):
            d = np.zeros_like(q)
            d[i] = 1e-6
            J[:, k] = (_residual(fk, q + d, target, tool_down, w) - _residual(fk, q - d, target, tool_down, w)) / 2e-6
        step = -J.T @ np.linalg.solve(J @ J.T + LAMBDA ** 2 * np.eye(r.size), r)
        norm = np.linalg.norm(step)
        if norm > 0.4:
            step *= 0.4 / norm
        q = q.copy()
        q[active] += step
        q = np.clip(q, lo, hi)
    return q, max_iter


def solve(fk, lo, hi, target, seed=None, guesses=(), active=None, tool_down=False, n_random=6, max_iter=120,
          rng_seed=12345):
    """Returns (q, pos_error_m, ori_error_rad, iterations, tool_down_used). Tool-down is tried first and dropped
    when it cannot be met; the caller reads the orientation error."""
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    n = lo.size
    active = list(range(n)) if active is None else list(active)
    target = np.asarray(target, float)
    rng = np.random.default_rng(rng_seed)
    guesses = [np.asarray(g, float) for g in guesses]
    # the caller's first guess (the preferred configuration) goes first, then the given seed, then the rest
    seeds = guesses[:1] + ([np.asarray(seed, float)] if seed is not None else []) + guesses[1:]
    seeds += [np.clip(np.zeros(n), lo, hi)] + [rng.uniform(lo, hi) for _ in range(n_random)]
    best = None
    for use_tool in ([True, False] if tool_down else [False]):
        for s in seeds:
            q, it = _dls(fk, s, lo, hi, target, active, use_tool, max_iter)
            T = fk(q)
            pos, ori = float(np.linalg.norm(T[:3, 3] - target)), tool_down_error(T)
            good = pos < TOL_M and (not use_tool or ori < TOL_ORI)
            cand = (q, pos, ori, it, use_tool)
            if best is None or (good, -pos) > (best[0], -best[1][1]):
                best = (good, cand)
            if good:
                return cand
        if best[0]:
            break
    return best[1]


def req_target(requirements, rid, default=None):
    """Target value of requirement `rid`, or `default`."""
    for r in requirements:
        if r.id == rid:
            return r.target
    return default


def task_extent(spec):
    """World-frame extent of the task waypoints: (z_min, z_max, [xyz...], base xyz or None). Used for sizing."""
    import numpy as np

    from ..physics import place_base
    from ..schemas import SceneSpec
    from ..sim.task import waypoints
    base = place_base(spec)
    pts = np.array([w.xyz for w in waypoints(spec, SceneSpec(), base) if w.xyz is not None and w.kind != 'drive'])
    if not len(pts):
        pts = np.array([m.position for m in spec.markers], float)
    return float(pts[:, 2].min()), float(pts[:, 2].max()), pts, np.array(base.position, float)
