"""Revise agent: turns a change request into a patch of the spec, the scene and/or the robot."""
from dataclasses import asdict
from typing import Literal

from pydantic import BaseModel

from .. import families
from ..llm.base import ask_json
from ..physics import place_base
from ..schemas import DesignVersion, Marker, RobotConcept, SceneSpec, TechnicalSpec
from . import robot as robot_agent
from . import scene as scene_agent
from . import spec as spec_agent
from .common import UNTRUSTED, dumps, parse

SYSTEM = f"""You change an existing robot design because the user asked for something different. You get the version the user is looking at (its specification, scene, robot family and parameters, the robot's computed capabilities, the requirement targets and every check with its result) and, when the user paused the playback, the moment on screen (the nearest simulation frame: time, phase, joint positions, tool position, what is held, and the events around it). Then you get the user's request.

The request refers to this version and, when "moment" is present, to that moment: words like "longer", "here", "this", "that joint" or "now" mean what the version and the moment show. Do not assume anything about other versions.

{UNTRUSTED} The user's request itself is the instruction to follow.

Rules:
- Change as little as the request needs. "targets" lists what you changed, from "spec", "scene", "robot". For each target return the COMPLETE new object (all fields), not a diff.
- spec: for changed tasks, positions, payloads, speeds, accuracy, environment. Keep ids stable. Keep physics_inputs consistent with the main object. Markers are in metres in the spec's frame ("make it reach 20 cm further" means move the far marker 20 cm further away).
- scene: for changes of furniture or obstacles only. If markers move in the spec, the scene must follow, so include "scene" too.
- Robot position: the spec's marker with id "robot_base" is the single source of truth for where a fixed robot stands (x, y) and the height of its mounting surface (z). To move the robot or mount it on a table, add or move that marker in the spec (z = the top of the surface it is mounted on, 0 = floor); without that marker the tool places the base itself on the floor. If the new mount needs a surface (z above 0), the scene must hold a table or plinth whose top is at that z and whose footprint contains the base x, y: include a "scene" change for it (e.g. join the two tables, or add a plate).
- Speed or cycle time changes go into the spec (physics_inputs.cycle_time_s); code then re-sizes the robot's speeds.
- robot: when the user wants a different robot kind or states robot numbers (link lengths, payload, speed). Put exactly the numbers the user asked for into params; code keeps explicit numbers when it re-sizes.
- Never set requirement values or check results; code recomputes them.
- "summary" is one plain sentence about what changed.
- Requests that start with "Automatic repair" come from code after a collision check: keep the task as it is and change only the levers the request lists.
Answer with one JSON object {{"targets": [...], "spec": ..., "scene": ..., "robot": ..., "summary": "..."}}; leave out what you did not change."""


class Patch(BaseModel):
    targets: list[Literal['spec', 'scene', 'robot']]
    spec: TechnicalSpec | None = None
    scene: SceneSpec | None = None
    robot: RobotConcept | None = None
    summary: str = ''


SCHEMA = Patch.model_json_schema()


BASE_MOVE_M = 0.01     # a patched robot.base further than this from the current one is a request to move the robot


def base_marker_from_robot(patch: Patch, version: DesignVersion):
    """The model sometimes writes the new base position into robot.base and forgets the spec's `robot_base` marker,
    which is the single source of truth. Turn a moved robot.base into that marker so the intent is not dropped."""
    if patch.robot is None or 'base' not in patch.robot.model_fields_set:
        return
    spec = patch.spec or version.spec
    if spec.locomotion.required or any(m.id == 'robot_base' for m in spec.markers):
        return
    new, cur = patch.robot.base.position, version.robot.base.position
    if max(abs(a - b) for a, b in zip(new, cur)) <= BASE_MOVE_M:
        return
    marker = Marker(id='robot_base', name='Robot base', position=tuple(new), note='Mounting point of the robot base')
    patch.spec = spec.model_copy(update={'markers': [*spec.markers, marker]})
    if 'spec' not in patch.targets:
        patch.targets.append('spec')
    if patch.scene is not None and all(m.id != 'robot_base' for m in patch.scene.markers):
        patch.scene = patch.scene.model_copy(update={'markers': [*patch.scene.markers, marker]})


def check(data, version: DesignVersion):
    patch = parse(Patch, data, 'The patch')
    targets = list(dict.fromkeys(patch.targets))
    if not targets:
        raise ValueError('"targets" is empty: say what you changed')
    for t in targets:
        if getattr(patch, t) is None:
            raise ValueError(f'"{t}" is in targets but its full new object is missing')
    patch.targets = targets
    base_marker_from_robot(patch, version)
    targets = patch.targets
    spec = patch.spec if 'spec' in targets else version.spec
    if 'spec' in targets:
        spec_agent.validate_spec(spec)
    if 'scene' in targets:
        scene_agent.validate_scene(patch.scene, spec, place_base(spec))
    if 'robot' in targets:
        robot_agent.validate_concept(patch.robot, spec)
    for t in ('spec', 'scene', 'robot'):
        if t not in targets:
            setattr(patch, t, None)
    patch.targets = targets
    patch.summary = patch.summary.strip()[:400] or 'Design changed as requested'
    return patch


MOMENT_EVENT_WINDOW_S = 0.5


def _r(x, n=4):
    return round(x, n) if isinstance(x, float) else x


def moment(version: DesignVersion, t: float):
    """The simulation as the user sees it at time `t`: the nearest frame and the events within +-0.5 s."""
    frames = version.sim.frames
    if not frames:
        return None
    f = min(frames, key=lambda f: abs(f.t - t))
    return {'requested_t': _r(t), 'frame': {'t': _r(f.t), 'phase': f.phase, 'holding': f.holding or None,
                                            'joints': {k: _r(v) for k, v in f.joints.items()},
                                            'tcp': [_r(c) for c in f.tcp] if f.tcp else None,
                                            'base': [_r(c) for c in f.base] if f.base else None},
            'events': [{'t': _r(e.t), 'kind': e.kind, 'text': e.text} for e in version.sim.events
                       if abs(e.t - t) <= MOMENT_EVENT_WINDOW_S]}


def capabilities(robot: RobotConcept):
    """Rated values of the robot as the family computes them (reach, payload, speeds, repeatability, ...)."""
    try:
        family = families.get(robot.family)
        caps = asdict(family.capabilities(family.parse(robot.params)))
    except (KeyError, ValueError, TypeError):
        return None
    return {k: (_r(v) if not isinstance(v, dict) else v) for k, v in caps.items()}


def view(version: DesignVersion, t: float | None = None):
    """What the model sees of the viewed version (built from the stored version, never from client numbers)."""
    checks = [{'id': c.requirement_id, 'name': c.name, 'passed': c.passed, 'required': c.required, 'actual': c.actual,
               'unit': c.unit, 'detail': c.detail} for c in version.sim.checks]
    targets = [{'id': r.id, 'name': r.name, 'comparator': r.comparator, 'target': r.target, 'unit': r.unit}
               for r in version.requirements]
    robot = {'family': version.robot.family, 'name': version.robot.name, 'rationale': version.robot.rationale,
             'params': version.robot.params, 'base': version.robot.base.model_dump(mode='json'),
             'capabilities': capabilities(version.robot)}
    out = {'version': version.version, 'spec': version.spec.model_dump(mode='json'),
           'scene': version.scene.model_dump(mode='json'), 'robot': robot, 'requirement_targets': targets,
           'checks': checks, 'duration_s': version.sim.duration_s}
    if t is not None:
        out['moment'] = moment(version, t)
    return out


async def run(adapter, version, request, t=None):
    prompt = 'Version on screen (JSON):\n' + dumps(view(version, t)) + '\n\nUser request:\n' + request.strip()[:2000]
    return await ask_json(adapter, prompt, SYSTEM, schema=SCHEMA, validate=lambda d: check(d, version))
