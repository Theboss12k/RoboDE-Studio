"""The pure design pipeline (pipeline/design.py) with scripted LLMs: no store, no HTTP. Java's FullFlowTest covers
the same flow through the real Python tasks."""
from fixtures_specs import bottle_spec
from studio.llm.registry import LLMRegistry
from studio.llm.scripted import ScriptedLLM
from studio.pipeline import design as pipeline
from studio_scripts import llms, scene_dict, spec_dict

BRIEF = 'Move a 0.3 kg bottle from A to B on a table'


def setup(tmp_path, **kw):
    return LLMRegistry(tmp_path, llms(**kw))


async def make_v1(registry):
    spec = await pipeline.specify(registry, BRIEF, None, {}, 1, lambda t: None)
    return (await pipeline.design(registry, spec['spec'], lambda t: None)).model_copy(update={'version': 1})


async def test_bottle_end_to_end(tmp_path):
    registry = setup(tmp_path)
    lines = []
    result = await pipeline.specify(registry, BRIEF, None, {}, 1, lines.append)
    assert result['status'] == 'spec' and result['round'] == 1
    v = await pipeline.design(registry, result['spec'], lines.append)
    assert v.spec.title == 'Bottle A to B' and {r.id for r in v.requirements} >= {'reach_max', 'payload', 'grip_force'}
    assert v.robot.family == 'serial_arm' and v.robot.params['payload_kg'] >= 0.3   # sized by code, not by the LLM
    assert v.sim.frames and v.sim.checks and '<robot' in v.urdf
    assert 'Working out the requirements' in lines and 'Building the scene and the robot concept' in lines


async def test_spec_questions_then_answers(tmp_path):
    question = {'questions': [{'id': 'cycle', 'text': 'Cycle time?', 'kind': 'number', 'unit': 's'}]}
    answers = [question, {'spec': spec_dict()}]
    kw = llms()
    kw['spec'] = ScriptedLLM([('requirements engineer', lambda i, s, im: answers.pop(0))])
    registry = LLMRegistry(tmp_path, kw)
    first = await pipeline.specify(registry, BRIEF, None, {}, 1, lambda t: None)
    assert first['status'] == 'questions' and first['round'] == 1
    second = await pipeline.specify(registry, BRIEF, None, {'cycle': 6}, 2, lambda t: None)
    assert second['status'] == 'spec' and second['round'] == 2


async def test_scene_and_robot_run_concurrently(tmp_path):
    stamps = {}
    registry = setup(tmp_path, delay=0.3, stamps=stamps)
    spec = await pipeline.specify(registry, BRIEF, None, {}, 1, lambda t: None)
    await pipeline.design(registry, spec['spec'], lambda t: None)
    (s0, s1), (r0, r1) = stamps['scene'], stamps['robot']
    assert s0 < r1 and r0 < s1, 'the two calls must overlap'
    assert max(s1, r1) - min(s0, r0) < 0.55


async def test_revise_reach_further_returns_a_child_and_keeps_the_base(tmp_path):
    registry = setup(tmp_path)
    v1 = await make_v1(registry)
    before = v1.model_dump_json()
    moved = bottle_spec()
    moved.markers[1].position = (0.5, -0.5, 0.75)     # B 20 cm further out
    patch = {'targets': ['spec', 'scene'], 'spec': moved.model_dump(mode='json'), 'scene': scene_dict(moved),
             'summary': 'Moved B 20 cm further'}
    registry.overrides['revise'] = ScriptedLLM([('change an existing', patch)])
    v2 = await pipeline.revise(registry, v1, 'make it reach 20 cm further', None, lambda t: None)
    assert v2.parent_version == 1 and v1.model_dump_json() == before
    reach = lambda v: next(r.target for r in v.requirements if r.id == 'reach_max')
    assert reach(v2) > reach(v1)
    assert v2.change_request == 'make it reach 20 cm further' and v2.change_summary.startswith('Moved B 20 cm further Checked in code after the change: ')
    passed = sum(c.passed is True for c in v2.sim.checks)
    assert f'{passed} of {len(v2.sim.checks)} pass' in v2.change_summary
    assert v2.scene.robot_base == v2.robot.base and v2.spec.marker('b').position[1] == -0.5


async def test_revise_robot_keeps_explicit_numbers(tmp_path):
    registry = setup(tmp_path)
    old = await make_v1(registry)
    robot = {**old.robot.model_dump(mode='json'), 'params': {**old.robot.params, 'tool_length_m': 0.2}}
    registry.overrides['revise'] = ScriptedLLM([('change an existing', {'targets': ['robot'], 'robot': robot, 'summary': 'Longer tool'})])
    v2 = await pipeline.revise(registry, old, 'use a 20 cm tool', None, lambda t: None)
    assert v2.robot.params['tool_length_m'] == 0.2 and v2.spec == old.spec
