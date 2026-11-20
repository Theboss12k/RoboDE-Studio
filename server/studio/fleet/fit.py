"""Sized reach of fleet members: the reach the family's own sizing rule gives a member for its sub-task.

The automatic split and the reach-limit checks must use the same metric. A serial arm is sized with its own margins
(the wrist point within 0.85 of the link reach, base height included), so a member that works 0.8 m from its base can
be sized to a rated reach near 1.6 m. Here each candidate member is laid out, its sub-spec built, its requirements
derived and every allowed fixed-base family sized from them exactly as `size_and_simulate` does first (no simulation).
"""
from __future__ import annotations

from .. import families
from ..physics import derive, place_base
from ..schemas import FleetAssignment, FleetLimits, TechnicalSpec
from .assign import FIXED_BASE_FAMILIES
from .layout import Layout, plan_layout
from .subspec import member_spec


def allowed_families(limits: FleetLimits, preferred: str = '') -> list[str]:
    """Fixed-base families a member may use: its own preference, else the user's allowed list, else all."""
    if preferred:
        return [preferred] if preferred in FIXED_BASE_FAMILIES else []
    allowed = [f for f in (limits.families or FIXED_BASE_FAMILIES) if f in FIXED_BASE_FAMILIES]
    return allowed or list(FIXED_BASE_FAMILIES)


def sized_reach(spec: TechnicalSpec, family: str, hint: dict | None = None) -> float:
    """Rated reach (capabilities.reach_max_m) of `family` sized from `spec`'s derived requirements."""
    fam = families.get(family)
    reqs = derive(spec, place_base(spec))
    params = fam.size_from_physics(reqs, spec, dict(hint or {}))
    return float(fam.capabilities(params).reach_max_m)


def member_reaches(task: TechnicalSpec, assignment: FleetAssignment, layout: Layout, limits: FleetLimits
                   ) -> dict[str, dict[str, float]]:
    """{member: {family: sized reach}} for the allowed families of every member."""
    out = {}
    for m in assignment.members:
        s = member_spec(task, assignment, layout, m.id)
        out[m.id] = {f: sized_reach(s, f) for f in allowed_families(limits, m.family)}
    return out


def reach_ratio(reaches: dict[str, dict[str, float]], limit: float) -> float:
    """Worst member's best family: max over members of min over families of sized reach / limit (<= 1 fits)."""
    return max((min(r.values(), default=float('inf')) / limit for r in reaches.values()), default=0.0)


def sized_fit(task: TechnicalSpec, assignment: FleetAssignment, limits: FleetLimits):
    """(ratio, layout, reaches) for an assignment under the user's reach limit; ratio <= 1 means every member has an
    allowed family whose sized reach is within the limit. Raises ValueError when the assignment cannot be laid out."""
    layout = plan_layout(task, assignment, limits)
    reaches = member_reaches(task, assignment, layout, limits)
    return reach_ratio(reaches, float(limits.max_reach_m)), layout, reaches
