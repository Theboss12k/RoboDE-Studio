"""Collision auto-repair (pipeline/design.py repair_collisions) with scripted LLMs: no model is called, so these are
fixture tests of the pipeline logic, not model evaluations. The colliding design is the bottle task with a wall box
over the robot base; the simulator and the checks are real code."""
import asyncio

import pytest
from fixtures_specs import bottle_spec
from studio_scripts import llms, scene_dict

from studio.llm.registry import LLMRegistry
from studio.llm.scripted import ScriptedLLM
from studio.pipeline import design as pl
from studio.schemas import SceneSpec
from studio.sim.report import collision_detail, episodes_from
from studio.sim.task import simulate
from studio.tasks.handlers import version_json

WALL = {'id': 'wall', 'kind': 'wall', 'center': [0.5, 0.35, 1.1], 'size': [0.1, 0.1, 0.5]}
REPAIR = 'User request:\nAutomatic repair'
USER = 'User request:\nadd a wall'


def scene_with(*extra):
    d = scene_dict()
    d['boxes'] = [*d['boxes'], *extra]
    return d


def far_box(x):
    """A harmless box far from the robot; a different x makes a different design."""
    return {'id': 'crate', 'kind': 'box', 'center': [x, 2.0, 0.2], 'size': [0.2, 0.2, 0.4]}


def patch(scene, summary='Changed the scene'):
    return {'targets': ['scene'], 'scene': scene, 'summary': summary}


def registry(tmp_path, *rules):
    r = LLMRegistry(tmp_path, llms())
    r.overrides['revise'] = ScriptedLLM(list(rules))
    return r


async def make_v1(tmp_path):
    r = LLMRegistry(tmp_path, llms())
    spec = await pl.specify(r, 'Move a 0.3 kg bottle from A to B on a table', None, {}, 1, lambda t: None)
    return (await pl.design(r, spec['spec'], lambda t: None)).model_copy(update={'version': 1})


def with_scene(v, scene):
    """The version re-simulated in code with another scene."""
    sc = SceneSpec.model_validate(scene).model_copy(update={'robot_base': v.robot.base})
    return v.model_copy(update={'scene': sc, 'sim': simulate(v.spec, v.requirements, sc, v.robot)})


async def colliding(tmp_path):
    v1 = await make_v1(tmp_path)
    v = with_scene(v1, scene_with(WALL))
    assert pl.collision_episodes(v) > 0 and v.sim.checks
    return v.model_copy(update={'version': 3, 'parent_version': 2, 'change_request': 'user words', 'created': 'T0'})


def test_detail_round_trip():
    assert collision_detail(0) == 'no contact found' and collision_detail(2) == '2 collision episode(s)'
    from studio.schemas import Check
    mk = lambda d, p: Check(requirement_id='collision_free', name='n', required=None, actual=None, unit='', passed=p, detail=d)
    assert episodes_from(mk(collision_detail(3), False)) == 3
    assert episodes_from(mk(collision_detail(0), True)) == 0 and episodes_from(None) == 0


async def test_helpers(tmp_path):
    v = await colliding(tmp_path)
    text, t = pl.repair_request(v, [])
    assert text.startswith('Automatic repair (attempt 1 of 3):') and 'collides with box wall' in text and 't=' in text
    assert 'robot_base' in text and 'approach_height_m' in text and len(text) <= 1900
    assert t == pytest.approx(pl.collision_events(v)[0].t) and 0 <= t <= v.sim.duration_s
    moved = bottle_spec()
    assert pl.task_unchanged(moved, bottle_spec()) is None
    moved.markers[1].position = (0.5, -0.5, 0.75)
    assert 'b' in pl.task_unchanged(bottle_spec(), moved)
    d = bottle_spec().model_dump(mode='json')
    d['markers'].append({'id': 'robot_base', 'name': 'Robot base', 'position': [0.2, 0, 0]})
    d['physics_inputs']['approach_height_m'] = 0.2
    assert pl.task_unchanged(bottle_spec(), type(bottle_spec()).model_validate(d)) is None
    d['physics_inputs']['cycle_time_s'] = 9
    assert 'cycle_time_s' in pl.task_unchanged(bottle_spec(), type(bottle_spec()).model_validate(d))
    assert pl.design_fingerprint(v) == pl.design_fingerprint(v.model_copy()) != pl.design_fingerprint(with_scene(v, scene_with()))


async def test_fix_clears_collisions(tmp_path):
    v = await colliding(tmp_path)
    lines = []
    r = registry(tmp_path, ('Automatic repair', patch(scene_with(), 'Removed the wall')))
    out = await pl.repair_collisions(r, v, lines.append)
    assert pl.collision_episodes(out) == 0 and out.repair.kept == 1 and out.repair.stop_reason == 'collision free'
    assert [a.outcome for a in out.repair.attempts] == ['kept'] and out.repair.collisions_before > 0
    assert out.repair.attempts[0].summary == 'Removed the wall' and out.repair.attempts[0].t is not None
    assert (out.version, out.parent_version, out.change_request, out.created) == (3, 2, 'user words', 'T0')
    assert out.change_summary == 'Automatic collision repair: kept attempt 1 of 1 (0 collision(s) left).'
    assert lines[0].startswith('Collisions found (') and lines[0].endswith('automatic fix, attempt 1 of 3')
    assert lines[1].startswith('Attempt 1: 0 collision(s) left, ') and len(lines) == 2
    assert v.repair is None and pl.collision_episodes(v) > 0       # the input is not changed


async def test_robot_base_move_is_allowed(tmp_path):
    v = await colliding(tmp_path)
    spec = v.spec.model_dump(mode='json')
    spec['markers'].append({'id': 'robot_base', 'name': 'Robot base', 'position': [0.0, 0.0, 0.0]})
    spec['physics_inputs']['approach_height_m'] = 0.15
    scene = scene_with(WALL)
    scene['markers'] = [*scene['markers'], spec['markers'][-1]]
    moved = {'targets': ['spec', 'scene'], 'spec': spec, 'scene': scene, 'summary': 'Moved the robot'}
    out = await pl.repair_collisions(registry(tmp_path, ('Automatic repair', moved)), v, lambda t: None)
    att = out.repair.attempts[0]
    assert att.outcome in ('kept', 'worse') and att.collisions is not None
    assert att.outcome == 'worse' or out.spec.marker('robot_base').position[0] == 0.0


async def test_identical_patch_stops(tmp_path):
    v = await colliding(tmp_path)
    r = registry(tmp_path, ('Automatic repair', patch(scene_with(WALL), 'Looked again')))
    lines = []
    out = await pl.repair_collisions(r, v, lines.append)
    assert [a.outcome for a in out.repair.attempts] == ['no_change'] and out.repair.stop_reason == 'model changed nothing'
    assert out.repair.kept == 0 and out.scene == v.scene and len(r.overrides['revise'].calls) == 1
    assert out.change_summary == 'Automatic collision repair: no attempt removed the collisions; the original design is kept.'


async def test_moved_task_marker_is_discarded(tmp_path):
    v = await colliding(tmp_path)
    moved = bottle_spec()
    moved.markers[1].position = (0.5, -0.5, 0.75)
    bad = {'targets': ['spec', 'scene'], 'spec': moved.model_dump(mode='json'), 'scene': scene_dict(moved), 'summary': 'Moved B'}
    ok = patch(scene_with(far_box(1.0)), 'Added a crate')
    answers = [bad, ok, ok]
    r = registry(tmp_path, ('Automatic repair', lambda i, s, im: answers.pop(0)))
    out = await pl.repair_collisions(r, v, lambda t: None)
    first = out.repair.attempts[0]
    assert first.outcome == 'changed_task' and 'b' in first.note and first.collisions is None
    assert out.spec == v.spec


async def test_three_attempts_without_improvement_keep_the_original(tmp_path):
    v = await colliding(tmp_path)
    xs = iter([1.0, 1.2, 1.4])
    r = registry(tmp_path, ('Automatic repair', lambda i, s, im: patch(scene_with(WALL, far_box(next(xs))), 'Moved a crate')))
    lines = []
    out = await pl.repair_collisions(r, v, lines.append)
    rep = out.repair
    assert rep.kept == 0 and rep.stop_reason == 'max attempts' and len(rep.attempts) == 3
    assert {a.outcome for a in rep.attempts} == {'worse'} and out.scene == v.scene
    assert sum(l.startswith('Collisions found') for l in lines) == 3
    assert 'attempt 3 of 3' in ' '.join(lines)
    assert 'Earlier attempts' in r.overrides['revise'].calls[2]['input']


async def test_invalid_attempt_is_logged(tmp_path):
    v = await colliding(tmp_path)
    answers = [{'targets': []}, patch(scene_with(), 'Removed the wall')]
    r = registry(tmp_path, ('Automatic repair', lambda i, s, im: answers.pop(0)))
    out = await pl.repair_collisions(r, v, lambda t: None)
    # the agent retries an unusable answer itself; either way the end state is a kept, collision-free design
    assert pl.collision_episodes(out) == 0 and out.repair.kept == 1


async def test_clean_design_is_untouched(tmp_path):
    v1 = await make_v1(tmp_path)
    assert pl.collision_episodes(v1) == 0

    async def run(**kw):
        r = LLMRegistry(tmp_path, llms())
        lines = []
        spec = bottle_spec()
        v = await pl.design(r, spec, lines.append, **kw)
        return v, lines
    a, la = await run()
    b, lb = await run(auto_repair=False)
    assert la == lb and a.repair is None and 'repair' not in version_json(a)
    assert 'Collisions found' not in ' '.join(la)


WEAK = ('joint_speed', 'accuracy', 'cycle_time')


def fake(v, scene, restored=0):
    """A candidate that differs by scene and by how many of the WEAK checks pass again (a stand-in for the revise step)."""
    checks = [c.model_copy(update={'passed': True}) if c.requirement_id in WEAK[:restored] else c for c in v.sim.checks]
    return v.model_copy(update={'scene': SceneSpec.model_validate(scene).model_copy(update={'robot_base': v.robot.base}),
                                'sim': v.sim.model_copy(update={'checks': checks})})


async def test_tie_break_on_checks_passed(tmp_path):
    v = await colliding(tmp_path)
    v = fake(v, scene_with(WALL))      # same design, but three checks (not collisions) fail
    v = v.model_copy(update={'sim': v.sim.model_copy(update={'checks': [
        c.model_copy(update={'passed': False}) if c.requirement_id in WEAK else c for c in v.sim.checks]})})
    plan = iter([(1.0, 0), (1.2, 2), (1.4, 2)])     # (crate x, checks restored): tie with the original, better, tie again

    async def fn(llms_, best, request, t, progress):
        x, n = next(plan)
        return fake(v, scene_with(WALL, far_box(x)), n)
    out = await pl.repair_collisions(None, v, lambda t: None, revise_fn=fn)
    assert [a.outcome for a in out.repair.attempts] == ['worse', 'kept', 'worse'] and out.repair.kept == 2
    assert out.repair.attempts[1].checks_passed == out.repair.attempts[0].checks_passed + 2
    assert out.repair.stop_reason == 'max attempts'


async def test_user_revise_with_collisions_is_repaired(tmp_path):
    v1 = await make_v1(tmp_path)
    rules = [(REPAIR, patch(scene_with(), 'Removed the wall')), (USER, patch(scene_with(WALL), 'Added a wall'))]
    lines = []
    out = await pl.revise(registry(tmp_path, *rules), v1, 'add a wall over the base', None, lines.append)
    assert out.parent_version == 1 and out.change_request == 'add a wall over the base'
    assert out.repair.kept == 1 and pl.collision_episodes(out) == 0
    assert out.change_summary.startswith('Added a wall Checked in code after the change: ')
    assert out.change_summary.endswith('Automatic collision repair: kept attempt 1 of 1 (0 collision(s) left).')
    assert any(l.startswith('Collisions found (') for l in lines)
    assert 'Added a wall Checked' in out.change_summary and 'failing: No collisions' not in out.change_summary
    assert all(b.id != 'wall' for b in out.scene.boxes)


async def test_auto_repair_off_skips_it(tmp_path):
    v1 = await make_v1(tmp_path)
    r = registry(tmp_path, (REPAIR, patch(scene_with(), 'Removed the wall')), (USER, patch(scene_with(WALL), 'Added a wall')))
    out = await pl.revise(r, v1, 'add a wall over the base', None, lambda t: None, auto_repair=False)
    assert out.repair is None and pl.collision_episodes(out) > 0 and len(r.overrides['revise'].calls) == 1
