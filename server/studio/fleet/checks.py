"""Fleet checks, all computed in code (report.custom, stable ids): the user's limits per member, and the fleet as a
whole (robot count, base spacing, handover reach by IK from both bases, robot-robot clearance, cycle period)."""
from __future__ import annotations

import math

import numpy as np

from .. import families
from ..schemas import BasePose, Check, FleetAssignment, FleetLimits, FleetMember, Requirement, RobotConcept, TechnicalSpec
from ..sim import report
from .assign import FIXED_BASE_FAMILIES
from .layout import MIN_BASE_SPACING_M, Layout
from .schedule import fleet_period


def member_limit_checks(robot: RobotConcept, requirements: list[Requirement], limits: FleetLimits) -> list[Check]:
    """limit_family (always), limit_reach (when max_reach_m is set), limit_payload (when max_payload_kg is set)."""
    allowed = list(limits.families) or list(FIXED_BASE_FAMILIES)
    ok = robot.family in FIXED_BASE_FAMILIES and robot.family in allowed
    out = [report.custom('limit_family', 'Robot type allowed', None, None, '', passed=ok,
                         detail=f'{robot.family} {"is" if ok else "is not"} among the allowed fixed-base types: '
                                f'{", ".join(a for a in allowed if a in FIXED_BASE_FAMILIES) or "none"}')]
    fam = families.get(robot.family)
    caps = fam.capabilities(fam.parse(robot.params))
    if limits.max_reach_m is not None:
        out.append(report.custom('limit_reach', 'Reach within your limit', limits.max_reach_m, caps.reach_max_m, 'm',
                                 comparator='<=', detail='rated reach of the sized robot vs your maximum reach'))
    if limits.max_payload_kg is not None:
        pay = next((r.target for r in requirements if r.id == 'payload'), None)
        out.append(report.custom('limit_payload', 'Payload within your limit', limits.max_payload_kg, pay, 'kg',
                                 comparator='<=', detail='required payload (with tool and safety factor) vs your '
                                                         'maximum payload' if pay is not None else
                                 'no payload requirement for this member'))
    return out


def _to_base(base: BasePose, xyz):
    c, s = math.cos(-base.yaw), math.sin(-base.yaw)
    d = np.asarray(xyz, float) - np.asarray(base.position, float)
    return np.array([c * d[0] - s * d[1], s * d[0] + c * d[1], d[2]])


def handover_reachable(robot: RobotConcept, base: BasePose, point, approach_m: float = 0.10) -> tuple[bool, str]:
    """IK from `base` to the grasp point `point` (world) and to the point raised by the approach height."""
    fam = families.get(robot.family)
    p = fam.parse(robot.params)
    pt = np.asarray(point, float)
    bad = []
    for label, q in (('grasp', pt), ('approach', pt + np.array([0.0, 0.0, approach_m]))):
        res = fam.ik(p, _to_base(base, q))
        if not res.ok:
            bad.append(f'{label} point missed by {res.error_m * 1000:.0f} mm')
    d = float(np.linalg.norm(pt[:2] - np.asarray(base.position[:2], float)))
    if bad:
        return False, f'{"; ".join(bad)} ({d:.2f} m from the base)'
    return True, f'grasp and approach points reached ({d:.2f} m from the base)'


def grasp_point(task: TechnicalSpec, obj: str, station) -> np.ndarray:
    """Grasp point above a station marker, as sim/task.py computes it (top grip: object height, else half)."""
    objs = {o.id: o for o in task.objects}
    o = objs.get(obj)
    size_z = o.size_m[2] if o else task.physics_inputs.object_size_m[2]
    top = (o.grip if o else task.physics_inputs.grip) in ('vacuum', 'magnetic')
    x, y, z = station
    return np.array([x, y, z + (size_z if top else size_z / 2)], float)


def fleet_checks(task: TechnicalSpec, assignment: FleetAssignment, layout: Layout, members: list[FleetMember],
                 clearance: tuple[int, list, float], limits: FleetLimits) -> list[Check]:
    """fleet_robot_count, fleet_base_spacing, fleet_handover_reach, fleet_robot_clearance, fleet_cycle_time."""
    by = {m.id: m for m in members}
    n = len(members)
    out = [report.custom('fleet_robot_count', 'Number of robots', limits.max_robots, n, '', comparator='<=',
                         passed=2 <= n <= limits.max_robots, detail=f'{n} robots; your limit is {limits.max_robots}')]
    bases = {m.id: np.asarray(m.robot.base.position[:2], float) for m in members}
    pairs = [(a, b) for x, a in enumerate(bases) for b in list(bases)[x + 1:]]
    if pairs:
        a, b = min(pairs, key=lambda ab: float(np.linalg.norm(bases[ab[0]] - bases[ab[1]])))
        d = float(np.linalg.norm(bases[a] - bases[b]))
        out.append(report.custom('fleet_base_spacing', 'Space between robot bases', MIN_BASE_SPACING_M, d, 'm',
                                 detail=f'closest pair {a} and {b}'))
    bad, notes = [], []
    appr = task.physics_inputs.approach_height_m
    for h in assignment.handovers:
        pos = layout.handovers.get(h.id) or h.position
        if pos is None:
            bad.append(f'{h.id}: no position')
            continue
        gp = grasp_point(task, h.object, pos)
        for who in (h.from_member, h.to_member):
            m = by.get(who)
            if m is None:
                bad.append(f'{h.id}: robot {who} missing')
                continue
            ok, why = handover_reachable(m.robot, m.robot.base, gp, appr)
            (notes if ok else bad).append(f'{h.id} from {who}: {why}')
    if assignment.handovers:
        out.append(report.custom('fleet_handover_reach', 'Handover stations reachable from both robots', None, None, '',
                                 passed=not bad, detail='; '.join(bad or notes)[:600]))
    episodes, _, gap = clearance
    out.append(report.custom('fleet_robot_clearance', 'No robot-robot collisions', None,
                             None if not math.isfinite(gap) else round(gap, 4), 'm', passed=episodes == 0,
                             detail=(f'{episodes} contact episode(s)' if episodes else 'no contact found') +
                                    (f'; smallest gap {gap:.3f} m' if math.isfinite(gap) else '') +
                                    '; one item on the shared clock'))
    period = fleet_period(members)
    out.append(report.custom('fleet_cycle_time', 'Fleet cycle time', task.physics_inputs.cycle_time_s, period, 's',
                             comparator='<=', detail='steady state with a one-item station buffer; overlap of '
                                                     'consecutive items not checked'))
    return out
