"""Iterative cell layout of a fleet: robot bases, handover stations and park poses.

Each iteration: (1) bases at the enclosing-circle centre of their member's points (task points and handovers),
(2) every free handover moved along the line between its two bases to balance their reach shares (golden-section
search), (3) bases pushed apart to MIN_BASE_SPACING_M and away from task points and stations. It stops when nothing
moves more than CONVERGED_M, or after max_iter iterations.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..physics.requirements import enclosing_circle
from ..schemas import BasePose, FleetAssignment, FleetLimits, TechnicalSpec, Vec3
from .assign import member_sequence, reach_budget, steps

MIN_BASE_SPACING_M = 0.6      # between two robot bases (xy)
POINT_CLEARANCE_M = 0.3       # base to task points and its own handover stations (the scene's free circle)
STATION_SIZE_M = 0.3          # footprint of a generated handover station
FOREIGN_STATION_CLEARANCE_M = POINT_CLEARANCE_M + STATION_SIZE_M / 2 * math.sqrt(2)   # another member's station
CONVERGED_M = 1e-3
PARK_SHARE = 0.5              # park poses sit this share of the reach budget from the base, away from the partner
GOLDEN = (math.sqrt(5) - 1) / 2


@dataclass
class Layout:
    bases: dict[str, BasePose]
    handovers: dict[str, tuple[float, float, float]]
    parks: dict[str, tuple[Vec3, Vec3]]           # member -> (start, end) park marker positions
    log: list[str] = field(default_factory=list)
    iterations: int = 0
    converged: bool = False


def _golden(f, lo=0.0, hi=1.0, tol=1e-5):
    a, b = lo, hi
    c, d = b - GOLDEN * (b - a), a + GOLDEN * (b - a)
    fc, fd = f(c), f(d)
    while b - a > tol:
        if fc <= fd:
            b, d, fd = d, c, fc
            c = b - GOLDEN * (b - a)
            fc = f(c)
        else:
            a, c, fc = c, d, fd
            d = a + GOLDEN * (b - a)
            fd = f(d)
    return (a + b) / 2


def _centre(points):
    pts = {(round(float(p[0]), 9), round(float(p[1]), 9)) for p in points}
    if len(pts) == 1:
        return np.array(next(iter(pts)))
    return np.asarray(enclosing_circle([np.array(p) for p in pts])[0], float)


def _push_from(b, p, dist):
    """b moved straight away from p until |b - p| >= dist (xy)."""
    d = b - p
    n = float(np.linalg.norm(d))
    if n >= dist:
        return b
    u = d / n if n > 1e-9 else np.array([0.0, -1.0])
    return p + u * dist


def plan_layout(spec: TechnicalSpec, assignment: FleetAssignment, limits: FleetLimits,
                reach: dict[str, float] | None = None, max_iter: int = 20) -> Layout:
    """Bases, handover positions and park poses for a validated assignment. User-fixed bases (member.base) and
    handovers (fixed=True) are kept. Base z is the task's robot_base marker z when the task has one, else 0; the yaw
    faces the member's first pick, as physics.place_base does for the member's sub-spec."""
    a = assignment
    ids = [m.id for m in a.members]
    mem = {m.id: m for m in a.members}
    R = {i: reach_budget(i, limits, reach) for i in ids}
    marks = {m.id: np.array(m.position, float) for m in spec.markers}
    bz = float(marks['robot_base'][2]) if 'robot_base' in marks else 0.0
    seqs = {i: member_sequence(spec, a, i) for i in ids}
    task_pts = [s.point for s in steps(spec) if s.point is not None]

    def own(i):
        return [marks[it.target] for it in seqs[i] if not it.handover and it.target in marks and it.target != 'robot_base'
                and it.verb in ('pick', 'place', 'move', 'press', 'inspect', 'push')]

    touch = {i: [h.id for h in a.handovers if i in (h.from_member, h.to_member)] for i in ids}
    hand = {h.id: h for h in a.handovers}

    # (1) handovers start between the giver's last point and the receiver's first point
    H: dict[str, np.ndarray] = {}
    for h in a.handovers:
        if h.position is not None:
            H[h.id] = np.array(h.position, float)
            continue
        g, r = seqs[h.from_member], seqs[h.to_member]
        gi = next(j for j, it in enumerate(g) if it.handover == h.id)
        ri = next(j for j, it in enumerate(r) if it.handover == h.id)
        before = [marks[it.target] for it in g[:gi] if not it.handover and it.target in marks]
        after = [marks[it.target] for it in r[ri + 1:] if not it.handover and it.target in marks]
        p = before[-1] if before else (own(h.from_member) or after or task_pts)[-1]
        q = after[0] if after else (own(h.to_member) or [p])[0]
        mid = (p + q) / 2
        H[h.id] = np.array([mid[0], mid[1], max(p[2], q[2])])

    def points(i):
        return [p[:2] for p in own(i)] + [H[h][:2] for h in touch[i]]

    fixed_base = {i: np.array(mem[i].base, float) for i in ids if mem[i].base is not None}
    B = {i: fixed_base[i][:2].copy() if i in fixed_base else _centre(points(i)) for i in ids}
    log: list[str] = []
    converged, it = False, 0
    for it in range(1, max_iter + 1):
        before_b = {i: B[i].copy() for i in ids}
        before_h = {k: v.copy() for k, v in H.items()}
        # (2) bases at the centre of their member's points
        for i in ids:
            if i not in fixed_base:
                B[i] = _centre(points(i))
        # (3) free handovers between their two bases, balancing the reach shares
        for h in a.handovers:
            if h.fixed:
                continue
            bf, bt = B[h.from_member], B[h.to_member]
            rf, rt = R[h.from_member], R[h.to_member]
            if np.linalg.norm(bt - bf) < 1e-9:
                continue
            f = lambda u: max(np.linalg.norm(bf + u * (bt - bf) - bf) / rf,   # noqa: E731
                              np.linalg.norm(bf + u * (bt - bf) - bt) / rt)
            u = _golden(f)
            xy = bf + u * (bt - bf)
            H[h.id] = np.array([xy[0], xy[1], H[h.id][2]])
        # (4) spacing between bases, clearance from task points and stations
        _separate(B, fixed_base, ids, task_pts, H, touch)
        moved = max([float(np.linalg.norm(B[i] - before_b[i])) for i in ids] +
                    [float(np.linalg.norm(H[k][:2] - before_h[k][:2])) for k in H])
        worst = max(ids, key=lambda i: max((np.linalg.norm(p - B[i]) for p in points(i)), default=0.0) / R[i])
        wr = max((float(np.linalg.norm(p - B[worst])) for p in points(worst)), default=0.0)
        log.append(f'Layout iteration {it}: largest move {moved * 1000:.1f} mm; widest robot {worst} works '
                   f'{wr:.2f} m from its base (budget {R[worst]:.2f} m)')
        if moved < CONVERGED_M:
            converged = True
            break
    spacing = min((float(np.linalg.norm(B[i] - B[j])) for x, i in enumerate(ids) for j in ids[x + 1:]), default=math.inf)
    if spacing < MIN_BASE_SPACING_M - 1e-6:
        log.append(f'Bases are only {spacing:.2f} m apart (fixed positions); {MIN_BASE_SPACING_M} m wanted')
    if not converged:
        log.append(f'Layout did not settle within {max_iter} iterations; the last positions are used')

    bases = {}
    for i in ids:
        pz = float(fixed_base[i][2]) if i in fixed_base else bz
        seq = seqs[i]
        first = next((it for it in seq if it.verb == 'pick'), None)
        tgt = None
        if first is not None:
            tgt = H[first.handover] if first.handover else marks.get(first.target)
        if tgt is None:
            pts = own(i) + [H[h] for h in touch[i]]
            tgt = pts[0] if pts else None
        d = (tgt[:2] - B[i]) if tgt is not None else np.zeros(2)
        yaw = math.atan2(d[1], d[0]) if np.linalg.norm(d) > 1e-9 else 0.0
        bases[i] = BasePose(position=(float(B[i][0]), float(B[i][1]), pz), yaw=yaw)
    parks = {i: _parks(i, ids, a, B, R, own(i) + [H[h] for h in touch[i]], mem[i]) for i in ids}
    return Layout(bases=bases, handovers={k: (float(v[0]), float(v[1]), float(v[2])) for k, v in H.items()},
                  parks=parks, log=log, iterations=it, converged=converged)


def _separate(B, fixed, ids, task_pts, H, touch, rounds=12):
    for _ in range(rounds):
        changed = False
        for x, i in enumerate(ids):
            for j in ids[x + 1:]:
                d = B[j] - B[i]
                n = float(np.linalg.norm(d))
                if n >= MIN_BASE_SPACING_M - 1e-9:
                    continue
                u = d / n if n > 1e-9 else np.array([1.0, 0.0])
                gap = MIN_BASE_SPACING_M - n
                fi, fj = i in fixed, j in fixed
                if fi and fj:
                    continue
                si, sj = (0.0, gap) if fi else (gap, 0.0) if fj else (gap / 2, gap / 2)
                B[i] = B[i] - u * si
                B[j] = B[j] + u * sj
                changed = True
        for i in ids:
            if i in fixed:
                continue
            obstacles = [(p[:2], POINT_CLEARANCE_M) for p in task_pts]
            obstacles += [(v[:2], POINT_CLEARANCE_M if k in touch[i] else FOREIGN_STATION_CLEARANCE_M) for k, v in H.items()]
            for p, clr in obstacles:
                nb = _push_from(B[i], p, clr)
                if np.linalg.norm(nb - B[i]) > 1e-9:
                    B[i] = nb
                    changed = True
        if not changed:
            return


def _parks(i, ids, a, B, R, pts, m):
    """(start, end) park marker positions: PARK_SHARE x reach budget from the base, away from the partner robots, at
    the member's highest working surface; the move waypoint above a marker (sim/task.py) puts the tool at carry height
    (marker z + object height + approach height). User-given start/end positions are kept."""
    partners = sorted({h.to_member if h.from_member == i else h.from_member
                       for h in a.handovers if i in (h.from_member, h.to_member)}) or [j for j in ids if j != i]
    pb = [B[j] for j in partners]
    d = B[i] - np.mean(pb, axis=0)
    if np.linalg.norm(d) < 1e-6 and len(pb) > 1:
        e = pb[-1] - pb[0]
        d = np.array([-e[1], e[0]])
    if np.linalg.norm(d) < 1e-6:
        d = np.array([0.0, -1.0])
    u = d / np.linalg.norm(d)
    z = max((float(p[2]) for p in pts), default=0.0)
    xy = B[i] + PARK_SHARE * R[i] * u
    auto = (float(xy[0]), float(xy[1]), z)
    start = tuple(float(c) for c in m.start) if m.start is not None else auto
    end = tuple(float(c) for c in m.end) if m.end is not None else auto
    return start, end
