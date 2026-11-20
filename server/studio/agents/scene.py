"""Scene agent: tables, shelves and obstacles under the spec's markers, objects at their start markers."""
import math

from ..llm.base import ask_json
from ..schemas import BasePose, SceneSpec, TechnicalSpec
from .common import dumps, finite, parse

SYSTEM = """You build the 3D scene for a robot task: the furniture and obstacles around the robot. World frame, metres, z up, floor at z = 0. Sizes are full extents; "center" is the middle of the box.

Rules:
- Copy every marker of the spec into "markers" with the same id and position.
- Put a table, shelf, conveyor or pallet under every pick or place target and every marker where an object starts, when it is above the floor: its top face (center z + size z / 2) is at the marker's z, and the marker's x and y lie inside its footprint. Make surfaces a little larger than the object. Markers at z = 0 need no support. Hover, home, waypoint and robot_base markers need no surface: never invent a shelf or support for them.
- Every object of the spec appears in "objects" at its start marker: x and y equal the marker, the bottom of the object (center z - size z / 2) sits at the marker's z. Use the object's own size and shape.
- Keep a circle of radius 0.3 m around the robot base (x, y of "robot_base") free of boxes that collide, except the table or plinth the base is mounted on when its z is above the floor (that surface's top is at the base z). Tables holding a marker may come closer, but never closer than 0.1 m.
- When the robot base z is above 0 (it is mounted on a table or plinth), the scene must contain a mounting surface: a box (table, plinth, plate) whose top is at the base z and whose footprint contains the base x, y. If the tables under the markers leave a gap there, join them into one table or add a bridging plate; do not leave the base floating.
- Floor: floor_size_m must be big enough to hold the markers, the path and the base with at least 0.5 m margin.
- Add only what the brief implies (walls, a second table, obstacles it mentions). Do not invent obstacles on the robot's path. Floor markings use collides false.
- Ids are lowercase letters, digits and underscores. Colours are #rrggbb.
Answer with one JSON object that is the scene."""

SCHEMA = SceneSpec.model_json_schema()
TOP_KINDS = ('table', 'shelf', 'conveyor', 'pallet', 'box', 'obstacle')


def rect_distance(box, x, y):
    """Distance from a point to the footprint of a box (0 inside), honouring yaw."""
    c, s = math.cos(-box.yaw), math.sin(-box.yaw)
    dx, dy = x - box.center[0], y - box.center[1]
    lx, ly = c * dx - s * dy, s * dx + c * dy
    return math.hypot(max(abs(lx) - box.size[0] / 2, 0), max(abs(ly) - box.size[1] / 2, 0))


def validate_scene(scene: SceneSpec, spec: TechnicalSpec, base: BasePose) -> SceneSpec:
    """Raises ValueError (message for the LLM) when the scene does not fit the spec."""
    placed = {m.id: m for m in scene.markers}
    for m in spec.markers:
        s = placed.get(m.id)
        if s is None:
            raise ValueError(f'Marker "{m.id}" is missing from markers')
        if max(abs(a - b) for a, b in zip(s.position, m.position)) > 0.01:
            raise ValueError(f'Marker "{m.id}" moved: it must stay at {list(m.position)}')
    for b in scene.boxes:
        if not finite(b.center) or not finite(b.size) or min(b.size) <= 0:
            raise ValueError(f'Box "{b.id}" needs positive sizes and finite positions')
    if len({b.id for b in scene.boxes}) != len(scene.boxes):
        raise ValueError('Box ids must be unique')
    by_id = {o.id: o for o in scene.objects}
    for o in spec.objects:
        so = by_id.get(o.id)
        if so is None:
            raise ValueError(f'Object "{o.id}" is missing from objects')
        if min(so.size) <= 0:
            raise ValueError(f'Object "{o.id}" needs positive sizes')
        if o.start_marker:
            m = spec.marker(o.start_marker)
            if abs(so.center[0] - m.position[0]) > 0.01 or abs(so.center[1] - m.position[1]) > 0.01:
                raise ValueError(f'Object "{o.id}" must be at the x, y of its start marker "{m.id}" ({m.position[0]}, {m.position[1]})')
            bottom = so.center[2] - so.size[2] / 2
            if abs(bottom - m.position[2]) > 0.01:
                raise ValueError(f'Object "{o.id}": its bottom is at z = {bottom:.3f} but marker "{m.id}" is at z = {m.position[2]}; '
                                 'center z = marker z + size z / 2')
    resting = {a.target for a in spec.actions if a.verb in ('pick', 'place') and a.target}
    resting |= {o.start_marker for o in spec.objects if o.start_marker}
    for m in spec.markers:
        if m.id in resting and m.position[2] > 0.02 and not any(b.kind in TOP_KINDS and abs(b.center[2] + b.size[2] / 2 - m.position[2]) <= 0.02
                                            and rect_distance(b, m.position[0], m.position[1]) == 0 for b in scene.boxes):
            raise ValueError(f'Marker "{m.id}" is at z = {m.position[2]} with nothing under it: add a table or shelf whose top is at that height '
                             'and whose footprint contains the marker')
    if base.position[2] > 0.02 and not any(abs(b.center[2] + b.size[2] / 2 - base.position[2]) <= 0.02
                                           and rect_distance(b, base.position[0], base.position[1]) == 0 for b in scene.boxes):
        raise ValueError(f'The robot base is mounted at ({base.position[0]:.2f}, {base.position[1]:.2f}, {base.position[2]:.2f}) '
                         'with nothing under it: add a table, plinth or plate whose top is at that height and whose footprint '
                         'contains the base x, y (join the tables if there is a gap)')
    for b in scene.boxes:
        # A surface holding a marker may come closer: the task itself puts the marker near the base.
        holds = any(m.position[2] > 0.02 and abs(b.center[2] + b.size[2] / 2 - m.position[2]) <= 0.02
                    and rect_distance(b, m.position[0], m.position[1]) == 0 for m in spec.markers)
        free = 0.1 if holds else 0.3
        mount = base.position[2] > 0.02 and abs(b.center[2] + b.size[2] / 2 - base.position[2]) <= 0.02 \
            and rect_distance(b, base.position[0], base.position[1]) == 0      # the robot is mounted on this surface
        if b.collides and not mount and rect_distance(b, base.position[0], base.position[1]) < free and b.center[2] - b.size[2] / 2 < 2.0:
            raise ValueError(f'Box "{b.id}" is within {free} m of the robot base at ({base.position[0]:.2f}, {base.position[1]:.2f}); keep it free')
    xs = [m.position[0] for m in spec.markers] + [base.position[0]] + [p[0] for p in spec.locomotion.path]
    ys = [m.position[1] for m in spec.markers] + [base.position[1]] + [p[1] for p in spec.locomotion.path]
    need = (max(xs) - min(xs) + 0.5, max(ys) - min(ys) + 0.5)
    if min(scene.floor_size_m) <= 0 or scene.floor_size_m[0] < need[0] or scene.floor_size_m[1] < need[1]:
        raise ValueError(f'floor_size_m {list(scene.floor_size_m)} is too small; the layout spans at least {need[0]:.1f} x {need[1]:.1f} m')
    return scene


async def run(adapter, spec, base):
    prompt = ('Technical specification (JSON):\n' + dumps({'title': spec.title, 'summary': spec.summary, 'markers': spec.markers,
                                                          'objects': spec.objects, 'environment': spec.environment,
                                                          'locomotion': spec.locomotion}) +
              '\n\nRobot base: ' + dumps(base))

    def check(data):
        scene = validate_scene(parse(SceneSpec, data, 'The scene'), spec, base)
        return scene.model_copy(update={'robot_base': base})
    return await ask_json(adapter, prompt, SYSTEM, schema=SCHEMA, validate=check)
