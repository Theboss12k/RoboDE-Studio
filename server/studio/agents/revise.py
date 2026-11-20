"""Revise agent: turns a change request into a patch of the spec, the scene and/or the robot."""
from typing import Literal

from pydantic import BaseModel

from ..llm.base import ask_json
from ..physics import place_base
from ..schemas import DesignVersion, RobotConcept, SceneSpec, TechnicalSpec
from . import robot as robot_agent
from . import scene as scene_agent
from . import spec as spec_agent
from .common import UNTRUSTED, dumps, parse

SYSTEM = f"""You change an existing robot design because the user asked for something different. You get the current specification, scene, robot concept and the checks that failed, then the user's request.

{UNTRUSTED} The user's request itself is the instruction to follow.

Rules:
- Change as little as the request needs. "targets" lists what you changed, from "spec", "scene", "robot". For each target return the COMPLETE new object (all fields), not a diff.
- spec: for changed tasks, positions, payloads, speeds, accuracy, environment. Keep ids stable. Keep physics_inputs consistent with the main object. Markers are in metres in the spec's frame ("make it reach 20 cm further" means move the far marker 20 cm further away).
- scene: for changes of furniture or obstacles only. If markers move in the spec, the scene must follow, so include "scene" too.
- robot: when the user wants a different robot kind or states robot numbers (link lengths, payload, speed). Put exactly the numbers the user asked for into params; code keeps explicit numbers when it re-sizes.
- Never set requirement values or check results; code recomputes them.
- "summary" is one plain sentence about what changed.
Answer with one JSON object {{"targets": [...], "spec": ..., "scene": ..., "robot": ..., "summary": "..."}}; leave out what you did not change."""


class Patch(BaseModel):
    targets: list[Literal['spec', 'scene', 'robot']]
    spec: TechnicalSpec | None = None
    scene: SceneSpec | None = None
    robot: RobotConcept | None = None
    summary: str = ''


SCHEMA = Patch.model_json_schema()


def check(data, version: DesignVersion):
    patch = parse(Patch, data, 'The patch')
    targets = list(dict.fromkeys(patch.targets))
    if not targets:
        raise ValueError('"targets" is empty: say what you changed')
    for t in targets:
        if getattr(patch, t) is None:
            raise ValueError(f'"{t}" is in targets but its full new object is missing')
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


def view(version: DesignVersion):
    failing = [{'id': c.requirement_id, 'name': c.name, 'required': c.required, 'actual': c.actual, 'unit': c.unit,
                'detail': c.detail} for c in version.sim.checks if c.passed is False]
    robot = {'family': version.robot.family, 'name': version.robot.name, 'rationale': version.robot.rationale,
             'params': version.robot.params}
    return {'spec': version.spec, 'scene': version.scene, 'robot': robot, 'failing_checks': failing}


async def run(adapter, version, request):
    prompt = 'Current design (JSON):\n' + dumps(view(version)) + '\n\nUser request:\n' + request.strip()[:2000]
    return await ask_json(adapter, prompt, SYSTEM, schema=SCHEMA, validate=lambda d: check(d, version))
