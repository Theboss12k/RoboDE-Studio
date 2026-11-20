"""A revise patch for a multi-robot design (`FleetPatch`) and its application to the planner's inputs.

The revise agent proposes the patch; code applies it to the assignment and the layout inputs as user-fixed values
(a moved base or station is fixed, a chosen family becomes the member's family), then re-plans, re-sizes and
re-checks everything. Nothing in the patch is a result: pass/fail numbers come from the planner and the simulator.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from .. import families
from ..schemas import FleetAssignment, FleetHandover, FleetLimits, FleetMemberAssignment, TechnicalSpec, Vec3
from .assign import FIXED_BASE_FAMILIES, auto_assign, validate_assignment


class MemberPatch(BaseModel):
    model_config = {'extra': 'ignore'}
    id: str = Field(pattern=r'^r[1-9]$')
    family: str | None = None                        # another robot type for this member
    params: dict | None = None                       # numbers the user asked for (kept when code re-sizes)
    actions: list[int] | None = None                 # task action indices this member does (0-based)
    base: Vec3 | None = None                         # where its base stands (world, m); fixed from now on


class HandoverPatch(BaseModel):
    model_config = {'extra': 'ignore'}
    id: str = Field(pattern=r'^h[1-9]$')
    position: Vec3                                   # station position (world, m); fixed from now on


class FleetPatch(BaseModel):
    model_config = {'extra': 'ignore'}
    members: list[MemberPatch] = []
    handovers: list[HandoverPatch] = []
    max_robots: int | None = Field(default=None, ge=2, le=3)


def flow_handovers(task: TechnicalSpec, members: list[FleetMemberAssignment]) -> list[FleetHandover]:
    """Handovers implied by who does which action: an object changes hands when a member places or carries an
    object that another member picked (relays are not derived)."""
    owner = {i: m.id for m in members for i in m.actions}
    holder: dict[str, str] = {}
    out: list[FleetHandover] = []
    for i, a in enumerate(task.actions):
        who = owner.get(i)
        if who is None or not a.object:
            continue
        if a.verb == 'pick':
            holder[a.object] = who
        elif a.verb in ('place', 'move') and holder.get(a.object) not in (None, who):
            out.append(FleetHandover(id=f'h{len(out) + 1}', object=a.object, from_member=holder[a.object], to_member=who))
            holder[a.object] = who
        if a.verb == 'place':
            holder.pop(a.object, None)
    return out


def _check_family(family: str, params: dict | None, limits: FleetLimits, mid: str):
    if family not in families.REGISTRY or family not in FIXED_BASE_FAMILIES:
        raise ValueError(f'{mid}: "{family}" is not a fixed-base robot type; use one of {list(FIXED_BASE_FAMILIES)}')
    if limits.families and family not in limits.families:
        raise ValueError(f'{mid}: "{family}" is not among the robot types the user allows ({", ".join(limits.families)})')
    if params is not None:
        try:
            families.get(family).parse(params)
        except ValueError as error:
            raise ValueError(f'{mid}: params do not fit the {family} parameters: {str(error)[:500]}') from error


def apply(task: TechnicalSpec, limits: FleetLimits, assignment: FleetAssignment, robots: dict, patch: FleetPatch,
          task_changed: bool = False):
    """(limits, assignment, changed) after the patch: `robots` is {member id: RobotConcept} of the current version,
    `changed` is {member id: (family, params)} for members whose robot the patch set. Raises ValueError (message for
    the model) when the patch does not fit the task, the limits or the flow."""
    if patch.max_robots is not None:
        limits = limits.model_copy(update={'max_robots': patch.max_robots})
    members = {m.id: m for m in assignment.members}
    hands = {h.id: h for h in assignment.handovers}
    changed: dict[str, tuple[str, dict | None]] = {}
    split_changed = False
    for mp in patch.members:
        m = members.get(mp.id)
        if m is None:
            if mp.actions is None:
                raise ValueError(f'{mp.id} is a new robot: give its "actions" (task action indices)')
            m = FleetMemberAssignment(id=mp.id, name=f'Robot {mp.id[1:]}', actions=[])
        upd = {}
        if mp.actions is not None:
            upd['actions'] = sorted(set(mp.actions))
            split_changed = True
        if mp.base is not None:
            upd['base'] = tuple(float(c) for c in mp.base)
        if mp.family is not None or mp.params is not None:
            old = robots.get(mp.id)
            family = mp.family or (old.family if old else (m.family or 'serial_arm'))
            _check_family(family, mp.params, limits, mp.id)
            upd['family'] = family
            changed[mp.id] = (family, mp.params)
        members[mp.id] = m.model_copy(update=upd)
    for hp in patch.handovers:
        if hp.id not in hands:
            raise ValueError(f'Handover {hp.id} does not exist; handovers are {sorted(hands) or "none"}')
        hands[hp.id] = hands[hp.id].model_copy(update={'position': tuple(float(c) for c in hp.position), 'fixed': True})
    a = assignment.model_copy(update={'members': [m for m in members.values() if m.actions or _relay(m.id, hands)],
                                      'handovers': list(hands.values())})
    if patch.members or patch.handovers or patch.max_robots is not None:
        a = a.model_copy(update={'source': 'user'}) if split_changed else a
    try:
        return limits, validate_assignment(task, a, limits), changed
    except ValueError as first:
        if split_changed:                      # the split changed: derive the handovers from the new flow
            derived = flow_handovers(task, a.members)
            try:
                return limits, validate_assignment(task, a.model_copy(update={'handovers': derived}), limits), changed
            except ValueError:
                pass
        if (task_changed or patch.max_robots is not None) and assignment.source == 'auto' and not assignment.confirmed:
            fresh = auto_assign(task, limits)
            fam = {m.id: m.family for m in a.members}
            fresh = fresh.model_copy(update={'members': [m.model_copy(update={'family': fam.get(m.id, '')})
                                                          for m in fresh.members]})
            return limits, validate_assignment(task, fresh, limits), changed
        raise ValueError(f'The change does not fit the robots\' split of the task: {first}') from first


def _relay(mid, hands):
    return any(h.to_member == mid for h in hands.values()) and any(h.from_member == mid for h in hands.values())
