"""A fleet member's own sub-task as a TechnicalSpec, so place_base, derive and size_and_simulate run on it unchanged,
and the handover stations for the scene."""
from __future__ import annotations

import re

from ..agents.scene import rect_distance
from ..schemas import Locomotion, Marker, SceneBox, TaskAction, TechnicalSpec
from .assign import member_sequence
from .layout import STATION_SIZE_M, Layout

STATION_COLOR = '#c9a66b'
_GIVE = re.compile(r'^place (\S+) at handover (h[1-9])$')
_TAKE = re.compile(r'^pick (\S+) at handover (h[1-9])$')


def handover_place_label(obj: str, hid: str) -> str:
    """Description (and so the simulation phase) of the giver's place on the station."""
    return f'place {obj} at handover {hid}'


def handover_pick_label(obj: str, hid: str) -> str:
    """Description (and so the simulation phase) of the receiver's pick from the station."""
    return f'pick {obj} at handover {hid}'


def handover_roles(spec: TechnicalSpec) -> dict[str, tuple[str, str]]:
    """{handover id: ('give' | 'take', object id)} read from a member spec built by member_spec."""
    out = {}
    for a in spec.actions:
        for kind, rx in (('give', _GIVE), ('take', _TAKE)):
            m = rx.match(a.description)
            if m:
                out[m.group(2)] = (kind, m.group(1))
    return out


def handover_marker_id(task: TechnicalSpec, hid: str) -> str:
    """Marker id of a handover station in member specs: the handover id, unless the task already uses that id."""
    return hid if all(m.id != hid for m in task.markers) else f'handover_{hid}'


def park_marker_ids(member_id: str) -> tuple[str, str]:
    return f'park_{member_id}_start', f'park_{member_id}_end'


def member_spec(task: TechnicalSpec, assignment, layout: Layout, member_id: str) -> TechnicalSpec:
    """The member's sub-task: its own actions with handover places/picks at the station markers, a move to its start
    park pose first and to its end park pose last; markers for its base (`robot_base`, the layout base), the stations
    it uses and its parks; only the objects it handles (a received object starts on the station); the task's
    physics inputs and cycle time."""
    a = assignment
    mem = next(m for m in a.members if m.id == member_id)
    name = mem.name or f'Robot {member_id[1:]}'
    seq = member_sequence(task, a, member_id)
    marks = {m.id: m for m in task.markers}
    objs = {o.id: o for o in task.objects}
    hand = {h.id: h for h in a.handovers}
    hm = {h.id: handover_marker_id(task, h.id) for h in a.handovers}
    p_start, p_end = park_marker_ids(member_id)
    start, end = layout.parks[member_id]

    actions = [TaskAction(verb='move', target=p_start, description=f'{member_id} moves to its start park position')]
    used_marks: list[str] = []
    first_marker: dict[str, str] = {}
    role = []
    for it in seq:
        if it.handover:
            h = hand[it.handover]
            obj_name = objs[h.object].name if h.object in objs else h.object
            if it.verb == 'place':
                actions.append(TaskAction(verb='place', object=it.object, target=hm[h.id],
                                          description=handover_place_label(it.object, h.id)))
                role.append(f'places the {obj_name} on station {h.id} for {h.to_member}')
            else:
                actions.append(TaskAction(verb='pick', object=it.object, target=hm[h.id],
                                          description=handover_pick_label(it.object, h.id)))
                role.append(f'picks the {obj_name} from station {h.id} (from {h.from_member})')
                first_marker.setdefault(it.object, hm[h.id])
            continue
        act = task.actions[it.action]
        actions.append(act.model_copy())
        if it.target in marks and it.target != 'robot_base':
            used_marks.append(it.target)
        if it.verb == 'pick' and it.object:
            first_marker.setdefault(it.object, it.target)
        if it.verb in ('pick', 'place') and it.target in marks:
            role.append(f'{it.verb}s the {objs[it.object].name if it.object in objs else it.object or "object"} '
                        f'at {marks[it.target].name}')
    actions.append(TaskAction(verb='move', target=p_end, description=f'{member_id} moves to its end park position'))

    base = layout.bases[member_id]
    markers = [Marker(id='robot_base', name=f'{name} base', position=base.position,
                      note='Robot base placed by the fleet layout')]
    seen = {'robot_base'}
    for mid in used_marks:
        if mid not in seen:
            markers.append(marks[mid].model_copy())
            seen.add(mid)
    for it in seq:
        if it.handover and hm[it.handover] not in seen:
            h = hand[it.handover]
            markers.append(Marker(id=hm[h.id], name=f'Handover station {h.id}', position=layout.handovers[h.id],
                                  note=f'{h.object} from {h.from_member} to {h.to_member}'))
            seen.add(hm[h.id])
    for o, mid in first_marker.items():        # an object picked without a target starts at its start marker
        if mid in marks and mid not in seen:
            markers.append(marks[mid].model_copy())
            seen.add(mid)
    markers.append(Marker(id=p_start, name=f'{member_id} start park', position=start))
    markers.append(Marker(id=p_end, name=f'{member_id} end park', position=end))

    handled = []
    for it in seq:
        if it.object and it.object in objs and it.object not in handled:
            handled.append(it.object)
    objects = [objs[o].model_copy(update={'start_marker': first_marker.get(o, objs[o].start_marker
                                                                            if objs[o].start_marker in seen else '')})
               for o in handled]
    pi = task.physics_inputs.model_copy(update={'travel_distance_m': 0.0})
    carried = [it.object for it in seq if it.object in objs and it.verb in ('pick', 'place', 'move')]
    if carried:                                # validate_spec: physics inputs follow the member's main object
        o = objs[carried[0]]
        pi = pi.model_copy(update={'object_mass_kg': o.mass_kg, 'object_size_m': o.size_m, 'grip': o.grip})
    k = len(a.members)
    summary = (f'{name}, one of {k} robots sharing "{task.title}": it ' + ('; then '.join(role) if role else
               'carries out its assigned steps') + '. It starts and ends at its park positions; objects pass between '
               'robots through handover stations (the giver places the object on a stand, the receiver picks it up).')
    return TechnicalSpec(
        title=f'{task.title} - {name}'[:160], summary=summary[:2000], frame=task.frame, actions=actions,
        objects=objects, markers=markers, locomotion=Locomotion(), environment=task.environment,
        duty_cycle=task.duty_cycle, accuracy_m=task.accuracy_m, speed_target=task.speed_target,
        payload_description=task.payload_description, physics_inputs=pi,
        assumptions=list(task.assumptions) + [f'Fleet member {member_id} of {k}; its base, stations and park positions '
                                              'come from the fleet layout (code).'],
        reference='')


def station_boxes(layout: Layout, size: float = STATION_SIZE_M, boxes=()) -> list[SceneBox]:
    """One support stand per handover station: top face at the station marker z (the surface the object rests on, as
    for every task marker), footprint `size` x `size` centred on it, standing on the highest scene box (`boxes`) under
    that footprint, else on the floor. No stand when that surface is already at the station height."""
    out = []
    for hid, (x, y, z) in sorted(layout.handovers.items()):
        under = [b.center[2] + b.size[2] / 2 for b in boxes
                 if b.collides and not b.id.startswith('station_') and rect_distance(b, x, y) < size / 2
                 and b.center[2] + b.size[2] / 2 <= z + 0.005]
        bottom = max(under, default=0.0)
        if z - bottom <= 0.02:
            continue
        out.append(SceneBox(id=f'station_{hid}', kind='table', center=(x, y, (bottom + z) / 2), size=(size, size, z - bottom),
                            color=STATION_COLOR, collides=True))
    return out
