import json

import pytest

from fixtures_specs import bottle_spec
from studio.agents.revise import Patch  # noqa: F401
from studio.llm.registry import LLMRegistry
from studio.llm.scripted import ScriptedLLM
from studio.pipeline import design as pipeline
from studio.projects import ProjectStore
from studio_scripts import llms, scene_dict, spec_dict
from studio.schemas import TechnicalSpec


def setup(tmp_path, **kw):
    registry = LLMRegistry(tmp_path, llms(**kw))
    project = ProjectStore(tmp_path).create('Move a 0.3 kg bottle from A to B on a table')
    return registry, project


async def test_bottle_end_to_end(tmp_path):
    registry, project = setup(tmp_path)
    lines = []
    result = await pipeline.specify(registry, project, {}, lines.append)
    assert result == {'status': 'designed', 'version': 1}
    v = project.version(1)
    assert v.spec.title == 'Bottle A to B' and {r.id for r in v.requirements} >= {'reach_max', 'payload', 'grip_force'}
    assert v.robot.family == 'serial_arm' and v.robot.params['payload_kg'] >= 0.3   # sized by code, not by the LLM
    assert v.sim.frames and v.sim.checks and '<robot' in v.urdf
    assert 'Working out the requirements' in lines and 'Building the scene and the robot concept' in lines
    assert project.meta['stage'] == 'designed' and project.meta['spec']


async def test_spec_questions_then_answers(tmp_path):
    question = {'questions': [{'id': 'cycle', 'text': 'Cycle time?', 'kind': 'number', 'unit': 's'}]}
    answers = [question, {'spec': spec_dict()}]
    kw = llms()
    kw['spec'] = ScriptedLLM([('requirements engineer', lambda i, s, im: answers.pop(0))])
    registry = LLMRegistry(tmp_path, kw)
    project = ProjectStore(tmp_path).create('bottle')
    first = await pipeline.specify(registry, project, {}, lambda t: None)
    assert first['status'] == 'questions' and project.meta['round'] == 1 and project.spec is None
    second = await pipeline.specify(registry, project, {'cycle': 6}, lambda t: None)
    assert second['status'] == 'designed' and project.meta['answers'] == {'cycle': 6}
    assert project.versions() == [1]


async def test_scene_and_robot_run_concurrently(tmp_path):
    stamps = {}
    registry, project = setup(tmp_path, delay=0.3, stamps=stamps)
    await pipeline.specify(registry, project, {}, lambda t: None)
    (s0, s1), (r0, r1) = stamps['scene'], stamps['robot']
    assert s0 < r1 and r0 < s1, 'the two calls must overlap'
    assert max(s1, r1) - min(s0, r0) < 0.55


async def test_revise_reach_further_creates_v2_and_keeps_v1(tmp_path):
    registry, project = setup(tmp_path)
    await pipeline.specify(registry, project, {}, lambda t: None)
    before = (project.dir / 'versions' / 'v1.json').read_text()
    moved = bottle_spec()
    moved.markers[1].position = (0.5, -0.5, 0.75)     # B 20 cm further out
    patch = {'targets': ['spec', 'scene'], 'spec': moved.model_dump(mode='json'), 'scene': scene_dict(moved),
             'summary': 'Moved B 20 cm further'}
    registry.overrides['revise'] = ScriptedLLM([('change an existing', patch)])
    lines = []
    v2 = await pipeline.revise(registry, project, 'make it reach 20 cm further', lines.append)
    assert v2.version == 2 and project.versions() == [1, 2]
    assert (project.dir / 'versions' / 'v1.json').read_text() == before
    v1 = project.version(1)
    reach = lambda v: next(r.target for r in v.requirements if r.id == 'reach_max')
    assert reach(v2) > reach(v1)
    assert v2.change_request == 'make it reach 20 cm further' and v2.change_summary == 'Moved B 20 cm further'
    assert v2.scene.robot_base == v2.robot.base and v2.spec.marker('b').position[1] == -0.5


async def test_revise_robot_keeps_explicit_numbers(tmp_path):
    registry, project = setup(tmp_path)
    await pipeline.specify(registry, project, {}, lambda t: None)
    old = project.version(1)
    robot = {**old.robot.model_dump(mode='json'), 'params': {**old.robot.params, 'tool_length_m': 0.2}}
    registry.overrides['revise'] = ScriptedLLM([('change an existing', {'targets': ['robot'], 'robot': robot, 'summary': 'Longer tool'})])
    v2 = await pipeline.revise(registry, project, 'use a 20 cm tool', lambda t: None)
    assert v2.robot.params['tool_length_m'] == 0.2 and v2.spec == old.spec


async def test_only_approved_flag_changes(tmp_path):
    registry, project = setup(tmp_path)
    await pipeline.specify(registry, project, {}, lambda t: None)
    before = json.loads((project.dir / 'versions' / 'v1.json').read_text())
    project.approve(1)
    after = json.loads((project.dir / 'versions' / 'v1.json').read_text())
    assert after.pop('approved') is True and before.pop('approved') is False and after == before
