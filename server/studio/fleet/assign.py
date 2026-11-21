"""Which robot does which action: the task's step chain, the automatic contiguous split, and validation of an
assignment (auto or edited by the user) against the object flow.

A member is a fixed-base robot (serial arm or gantry). When a pick and its place fall to different members the
object goes through a handover station: the giver places it there, the receiver picks it from there.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

import numpy as np

from ..physics.requirements import TASK_VERBS, enclosing_circle
from ..schemas import FleetAssignment, FleetHandover, FleetLimits, FleetMemberAssignment, TechnicalSpec
from .feasibility import PRACTICAL_REACH_M

FIT_SHARE = 0.85             # a member's points must fit within this share of its reach budget around one centre
FIXED_BASE_FAMILIES = ('serial_arm', 'gantry')
CARRY_VERBS = ('place', 'move')   # verbs that need the named object in the gripper


@dataclass
class Step:
    index: int                       # task action index
    verb: str
    marker: str | None               # marker id (a pick without target uses its object's start marker)
    object: str | None
    point: np.ndarray | None         # world xyz of the marker; None = no position (wait, home, inspect w/o target)


def steps(spec: TechnicalSpec) -> list[Step]:
    """The task's actions in order, with their marker positions (task verbs only, as physics/requirements does)."""
    objs = {o.id: o for o in spec.objects}
    marks = {m.id: m for m in spec.markers}
    out = []
    for i, a in enumerate(spec.actions):
        mid = a.target or (objs[a.object].start_marker if a.verb == 'pick' and a.object in objs else '')
        point = None
        if a.verb in TASK_VERBS and mid in marks and mid != 'robot_base':
            point = np.array(marks[mid].position, float)
        out.append(Step(i, a.verb, mid or None, a.object or None, point))
    return out


def reach_budget(member_id: str, limits: FleetLimits, reach: dict[str, float] | None = None) -> float:
    """Reach budget R of a member: the caller's value, else the user's max reach, else the practical arm reach."""
    if reach and reach.get(member_id):
        return float(reach[member_id])
    return float(limits.max_reach_m or PRACTICAL_REACH_M['serial_arm'])


# --- automatic split -------------------------------------------------------------------------------------------------

def _held_after(spec: TechnicalSpec, upto: int) -> str:
    """Object in the gripper after the task's actions [0, upto)."""
    held = ''
    for a in spec.actions[:upto]:
        if a.verb == 'pick':
            held = a.object
        elif a.verb == 'place':
            held = ''
    return held


def _radius(points) -> float:
    pts = {(round(float(p[0]), 6), round(float(p[1]), 6)) for p in points}
    if len(pts) <= 1:
        return 0.0
    return float(enclosing_circle([np.array(p) for p in pts])[1])


def _evaluate(spec, st, pos, cuts):
    """Members' step indices, handovers [(object, giver k, receiver k, midpoint)] and xy radius of every member for
    the contiguous split of the positioned steps `pos` at `cuts` (positions in `pos` where a member starts)."""
    bounds = [0, *cuts, len(pos)]
    groups = []
    for k in range(len(bounds) - 1):
        first = 0 if k == 0 else pos[bounds[k]]
        last = len(st) if k == len(bounds) - 2 else pos[bounds[k + 1]]
        groups.append(list(range(first, last)))    # positionless steps stay with the member before them
    hands = []
    for k in range(1, len(groups)):
        held = _held_after(spec, groups[k][0])
        if held:
            g_last = next(st[i].point for i in reversed(groups[k - 1]) if st[i].point is not None)
            r_first = next(st[i].point for i in groups[k] if st[i].point is not None)
            hands.append((held, k - 1, k, (g_last + r_first) / 2))
    radii = []
    for k, g in enumerate(groups):
        pts = [st[i].point[:2] for i in g if st[i].point is not None]
        pts += [h[3][:2] for h in hands if k in (h[1], h[2])]
        radii.append(_radius(pts))
    return groups, hands, radii


def _member_name(spec, k, group, st):
    first = next((st[i] for i in group if st[i].point is not None), None)
    if first is None:
        return f'Robot {k + 1}'
    m = next((m for m in spec.markers if m.id == first.marker), None)
    return f'Robot {k + 1} ({first.verb} at {m.name if m else first.marker})'[:80]


def auto_assign(spec: TechnicalSpec, limits: FleetLimits, reach: dict[str, float] | None = None) -> FleetAssignment:
    """Smallest k in 2..limits.max_robots with a contiguous split of the step chain such that every member's points
    (its task points plus its handover midpoints) fit within FIT_SHARE x its reach budget of one centre (xy).
    Among the splits that fit: fewest handovers, then the smallest worst radius share. When no k fits, the best split
    with k = max_robots is returned (the fleet checks report what does not fit)."""
    st = steps(spec)
    pos = [i for i, s in enumerate(st) if s.point is not None]
    if len(pos) < 2:
        raise ValueError('A fleet needs at least two task points to share between robots')
    best_any = None
    for k in range(2, limits.max_robots + 1):
        if k > len(pos):
            break
        budgets = [reach_budget(f'r{j + 1}', limits, reach) for j in range(k)]
        best = None
        for cuts in itertools.combinations(range(1, len(pos)), k - 1):
            groups, hands, radii = _evaluate(spec, st, pos, cuts)
            share = max(r / (FIT_SHARE * b) for r, b in zip(radii, budgets))
            key = (len(hands), share) if share <= 1 + 1e-9 else (math.inf, share)
            if best is None or key < best[0]:
                best = (key, groups, hands)
            if k == limits.max_robots and (best_any is None or share < best_any[0]):
                best_any = (share, groups, hands)
        if best is not None and best[0][0] != math.inf:
            return _assignment(spec, st, best[1], best[2])
    if best_any is None:                       # fewer task points than robots allowed: use as many as there are
        groups, hands, _ = _evaluate(spec, st, pos, tuple(range(1, len(pos))))
        return _assignment(spec, st, groups, hands)
    return _assignment(spec, st, best_any[1], best_any[2])


def _assignment(spec, st, groups, hands):
    members = [FleetMemberAssignment(id=f'r{k + 1}', name=_member_name(spec, k, g, st), actions=list(g))
               for k, g in enumerate(groups)]
    handovers = [FleetHandover(id=f'h{j + 1}', object=o, from_member=f'r{g + 1}', to_member=f'r{r + 1}')
                 for j, (o, g, r, _) in enumerate(hands)]
    return FleetAssignment(members=members, handovers=handovers, source='auto', confirmed=False)


# --- a member's sequence (shared by validation, layout and the member spec) --------------------------------------------

@dataclass
class Item:
    action: int | None               # task action index; None for a handover pick/place code adds
    verb: str
    object: str
    target: str                      # task marker id, or the handover id when `handover` is set
    handover: str = ''


def member_sequence(spec: TechnicalSpec, a: FleetAssignment, member_id: str) -> list[Item]:
    """The member's actions in task order with the handover places and picks inserted where the flow needs them:
    a giver places the held object on its outgoing station before its next pick (or at its end); a receiver picks
    from its incoming station before it places or carries an object it does not hold. Raises ValueError (message
    for people) when the handovers do not fit the flow."""
    mem = next((m for m in a.members if m.id == member_id), None)
    if mem is None:
        raise ValueError(f'Unknown robot {member_id}')
    objs = {o.id: o for o in spec.objects}
    incoming = [h for h in a.handovers if h.to_member == member_id]
    outgoing = [h for h in a.handovers if h.from_member == member_id]
    used: set[str] = set()
    out: list[Item] = []
    held = ''

    def give():
        nonlocal held
        h = next((h for h in outgoing if h.id not in used and h.object == held), None)
        if h is None:
            return False
        out.append(Item(None, 'place', held, h.id, h.id))
        used.add(h.id)
        held = ''
        return True

    def receive(obj):
        nonlocal held
        h = next((h for h in incoming if h.id not in used and h.object == obj), None)
        if h is None:
            return False
        out.append(Item(None, 'pick', obj, h.id, h.id))
        used.add(h.id)
        held = obj
        return True

    for i in sorted(mem.actions):
        act = spec.actions[i]
        label = act.description or f'{act.verb} {act.object} {act.target}'.strip()
        if act.verb == 'pick':
            if held and not give():
                raise ValueError(f'{member_id} would pick "{act.object}" (action {i + 1}, "{label}") while still '
                                 f'holding "{held}": add a handover for "{held}" from {member_id}')
            target = act.target or (objs[act.object].start_marker if act.object in objs else '')
            out.append(Item(i, 'pick', act.object, target))
            held = act.object
        elif act.verb in CARRY_VERBS and act.object:
            if held != act.object:
                if held and not give():
                    raise ValueError(f'{member_id} holds "{held}" when action {i + 1} ("{label}") needs '
                                     f'"{act.object}": add a handover for "{held}" from {member_id}')
                if not receive(act.object):
                    raise ValueError(f'{member_id} would {act.verb} "{act.object}" (action {i + 1}, "{label}") without '
                                     f'holding it: assign its pick to {member_id} or add a handover of '
                                     f'"{act.object}" to {member_id}')
            out.append(Item(i, act.verb, act.object, act.target))
            if act.verb == 'place':
                held = ''
        else:
            out.append(Item(i, act.verb, act.object, act.target))
    if held:
        give()
    for h in incoming:                         # a pass-through member: takes the object and hands it on at its end
        if h.id not in used and not held:
            receive(h.object)
            give()
    left = [h for h in a.handovers if h.id not in used and member_id in (h.from_member, h.to_member)]
    if left:
        h = left[0]
        role = 'give' if h.from_member == member_id else 'take'
        raise ValueError(f'Handover {h.id} ("{h.object}" from {h.from_member} to {h.to_member}) does not fit the flow: '
                         f'{member_id} never holds "{h.object}" at a point where it could {role} it')
    return out


def validate_assignment(spec: TechnicalSpec, a: FleetAssignment, limits: FleetLimits) -> FleetAssignment:
    """Raise ValueError (message for people) unless every action is assigned exactly once, there are 2..max_robots
    members with unique ids, every handover names existing members and objects and fits the flow (the giver holds the
    object before it, the receiver after it). Returns a normalised copy (sorted actions, members by id)."""
    n = len(spec.actions)
    k = len(a.members)
    if k < 2:
        raise ValueError('A fleet needs at least two robots')
    if k > limits.max_robots:
        raise ValueError(f'{k} robots are assigned but the limit is {limits.max_robots}')
    ids = [m.id for m in a.members]
    if len(set(ids)) != k:
        raise ValueError('Robot ids must be unique')
    owner: dict[int, str] = {}
    label = lambda i: spec.actions[i].description or spec.actions[i].verb   # noqa: E731
    for m in a.members:
        if not m.actions:
            raise ValueError(f'{m.id} has no actions: give it at least one or remove it')
        if m.family and m.family not in FIXED_BASE_FAMILIES:
            raise ValueError(f'{m.id}: family "{m.family}" is not a fixed-base robot ({", ".join(FIXED_BASE_FAMILIES)})')
        if m.family and limits.families and m.family not in limits.families:
            raise ValueError(f'{m.id}: family "{m.family}" is not among the allowed families {limits.families}')
        for i in m.actions:
            if not 0 <= i < n:
                raise ValueError(f'{m.id} names action {i + 1}, but the task has {n} actions')
            if i in owner:
                raise ValueError(f'Action {i + 1} ("{label(i)}") is assigned to both {owner[i]} and {m.id}')
            owner[i] = m.id
    missing = [i for i in range(n) if i not in owner]
    if missing:
        i = missing[0]
        raise ValueError(f'Action {i + 1} ("{label(i)}") is not assigned to any robot')
    objects = {o.id for o in spec.objects}
    hids = [h.id for h in a.handovers]
    if len(set(hids)) != len(hids):
        raise ValueError('Handover ids must be unique')
    for h in a.handovers:
        if h.object not in objects:
            raise ValueError(f'Handover {h.id} moves object "{h.object}", which the task does not have')
        for who in (h.from_member, h.to_member):
            if who not in ids:
                raise ValueError(f'Handover {h.id} names robot {who}, which is not in the fleet')
        if h.from_member == h.to_member:
            raise ValueError(f'Handover {h.id} goes from {h.from_member} to itself')
        if h.fixed and h.position is None:
            raise ValueError(f'Handover {h.id} is fixed but has no position')
        if h.position is not None and not all(math.isfinite(c) and abs(c) <= 1000 for c in h.position):
            raise ValueError(f'Handover {h.id} has a position that is not a sensible number of metres')
    seqs = {m.id: member_sequence(spec, a, m.id) for m in a.members}
    for h in a.handovers:                      # the giver must have the object before the receiver needs it
        give = seqs[h.from_member]
        take = seqs[h.to_member]
        gi = next(j for j, it in enumerate(give) if it.handover == h.id)
        ti = next(j for j, it in enumerate(take) if it.handover == h.id)
        before = max((it.action for it in give[:gi] if it.action is not None), default=-1)
        after = min((it.action for it in take[ti + 1:] if it.action is not None), default=n)
        if before >= after:
            raise ValueError(f'Handover {h.id}: {h.to_member} needs "{h.object}" (action {after + 1}) before '
                             f'{h.from_member} has it (action {before + 1})')
    members = sorted((m.model_copy(update={'actions': sorted(m.actions)}) for m in a.members), key=lambda m: m.id)
    return a.model_copy(update={'members': members, 'handovers': sorted(a.handovers, key=lambda h: h.id)})
