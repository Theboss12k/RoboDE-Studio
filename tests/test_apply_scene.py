"""apply_scene (pipeline/apply_scene.py and the task handler): the user's scene edits as a new version, computed in code.

The base design comes from the scripted (fixture) LLMs, as in test_repair.py; the edit itself uses no model, so these are
tests of the pipeline logic with real kinematics, simulation and checks, not model evaluations."""
import io

import pytest
from studio_scripts import llms, scene_dict

from studio.llm.registry import LLMRegistry
from studio.pipeline import apply_scene as ap
from studio.pipeline import design as pl
from studio.schemas import SceneSpec
from studio.tasks import protocol, runner
from studio.tasks.handlers import version_json

NEW_TABLE = {'id': 'table_new', 'kind': 'table', 'center': [0.9, -0.4, 0.375], 'size': [0.4, 0.4, 0.75]}
PREFIX = 'Edited in the site scan: moved B onto the new table.'


class NoLLM:
    """A registry that fails on any use: the deterministic path must never reach a model."""
    def for_role(self, role):
        raise AssertionError(f'an LLM was used (role {role})')


async def make_v1(tmp_path):
    r = LLMRegistry(tmp_path, llms())
    spec = await pl.specify(r, 'Move a 0.3 kg bottle from A to B on a table', None, {}, 1, lambda t: None)
    return (await pl.design(r, spec['spec'], lambda t: None)).model_copy(update={'version': 1})


def edited_scene(v, boxes):
    d = scene_dict()
    d['boxes'] = boxes
    d['floor_size_m'] = [10.0, 10.0]
    return SceneSpec.model_validate(d)


def table(v, mid):
    return next(b.model_dump(mode='json') for b in v.scene.boxes if b.id == f'table_{mid}')


def marker(v, mid):
    return v.spec.marker(mid).position


async def move_b(v1, **kw):
    scene = edited_scene(v1, [table(v1, 'a'), NEW_TABLE])
    return await ap.apply_scene(v1, scene, [{'id': 'b', 'position': [0.9, -0.4, 0.75]}], False, 'moved B onto the new table',
                                llms=NoLLM(), **kw)


async def test_move_drop_marker_onto_new_table(tmp_path):
    v1 = await make_v1(tmp_path)
    v2 = await move_b(v1)
    assert v2.version == 0 and v2.parent_version == 1
    assert v2.change_request == PREFIX[:-1]
    assert v2.change_summary.startswith(PREFIX + ' ' + pl.CHECKED_PREFIX)
    assert marker(v2, 'b') == (0.9, -0.4, 0.75) and marker(v1, 'b') == (0.5, -0.3, 0.75)
    assert next(m for m in v2.scene.markers if m.id == 'b').position == (0.9, -0.4, 0.75)
    assert any(b.id == 'table_new' for b in v2.scene.boxes)
    # the object still starts at A and rests on it
    bottle = v2.scene.objects[0]
    assert bottle.center == pytest.approx((0.5, 0.3, 0.75 + 0.22 / 2))
    # the checks were re-run on the new layout: a different path, the base follows the task points
    assert v2.sim.checks and v2.sim.model_dump() != v1.sim.model_dump()
    assert [f.joints for f in v2.sim.frames] != [f.joints for f in v1.sim.frames]
    assert v2.robot.base == v2.scene.robot_base and v2.robot.base.position != v1.robot.base.position
    assert v2.urdf


async def test_start_marker_moves_its_object(tmp_path):
    v1 = await make_v1(tmp_path)
    box = {'id': 'table_a', 'kind': 'table', 'center': [0.6, 0.4, 0.375], 'size': [0.3, 0.3, 0.75]}
    scene = edited_scene(v1, [box, table(v1, 'b')])
    v2 = await ap.apply_scene(v1, scene, [{'id': 'a', 'position': [0.6, 0.4, 0.75]}], False, 'moved A', llms=NoLLM())
    assert v2.scene.objects[0].center == pytest.approx((0.6, 0.4, 0.86))
    assert v2.spec.objects[0].start_marker == 'a'


async def test_move_robot_base(tmp_path):
    v1 = await make_v1(tmp_path)
    scene = edited_scene(v1, [table(v1, 'a'), table(v1, 'b')])
    v2 = await ap.apply_scene(v1, scene, [{'id': 'robot_base', 'position': [0.1, 0.0, 0.0]}], False, 'moved the robot',
                              llms=NoLLM())
    assert marker(v2, 'robot_base') == (0.1, 0.0, 0.0)
    assert next(m for m in v2.scene.markers if m.id == 'robot_base').position == (0.1, 0.0, 0.0)
    assert v2.scene.robot_base.position == (0.1, 0.0, 0.0) and v2.robot.base.position == (0.1, 0.0, 0.0)
    assert max(abs(a - b) for a, b in zip(v1.robot.base.position, v2.robot.base.position)) > 0.01
    assert v2.robot.params != v1.robot.params            # moved over 1 cm: re-sized, not kept
    assert v2.parent_version == 1 and v2.change_summary.startswith('Edited in the site scan: moved the robot.')


async def test_nothing_moved_keeps_the_sizes(tmp_path):
    v1 = await make_v1(tmp_path)
    scene = edited_scene(v1, [table(v1, 'a'), table(v1, 'b')])
    v2 = await ap.apply_scene(v1, scene, [], False, 'nothing', llms=NoLLM())
    assert v2.robot.base == v1.robot.base and marker(v2, 'a') == marker(v1, 'a')
    assert [c.passed for c in v2.sim.checks] == [c.passed for c in v1.sim.checks]


async def test_unknown_marker_is_scene_invalid(tmp_path):
    v1 = await make_v1(tmp_path)
    scene = edited_scene(v1, [table(v1, 'a'), table(v1, 'b')])
    with pytest.raises(ap.SceneInvalid) as error:
        await ap.apply_scene(v1, scene, [{'id': 'nope', 'position': [0, 0, 0]}], False, 'x', llms=NoLLM())
    assert error.value.code == 'scene_invalid' and 'nope' in str(error.value)
    assert protocol.classify(error.value) == ('value', 'scene_invalid')


async def test_invalid_scene_is_scene_invalid(tmp_path):
    v1 = await make_v1(tmp_path)
    scene = edited_scene(v1, [table(v1, 'a')])           # B at z 0.75 with nothing under it
    with pytest.raises(ap.SceneInvalid, match='nothing under it'):
        await ap.apply_scene(v1, scene, [], False, 'x', llms=NoLLM())


async def test_out_of_reach_still_gives_a_version_with_code_checks(tmp_path):
    v1 = await make_v1(tmp_path)
    scene = edited_scene(v1, [table(v1, 'a'), table(v1, 'b')])
    v2 = await ap.apply_scene(v1, scene, [{'id': 'robot_base', 'position': [-3.0, 0.0, 0.0]}], False, 'far away',
                              llms=NoLLM())
    assert v2.parent_version == 1 and v2.sim.checks
    assert v2.change_summary.endswith(pl.checked_outcome(v2.sim))
    assert v2.robot.base.position == (-3.0, 0.0, 0.0)


async def test_no_llm_without_auto_repair_or_without_registry(tmp_path):
    v1 = await make_v1(tmp_path)
    v2 = await move_b(v1)                                # NoLLM raises on any use
    assert v2.repair is None
    scene = edited_scene(v1, [table(v1, 'a'), NEW_TABLE])
    v3 = await ap.apply_scene(v1, scene, [{'id': 'b', 'position': [0.9, -0.4, 0.75]}], True, 'x')   # auto_repair, no llms
    assert v3.repair is None


async def test_auto_repair_only_runs_on_collisions(tmp_path):
    v1 = await make_v1(tmp_path)
    scene = edited_scene(v1, [table(v1, 'a'), NEW_TABLE])
    v2 = await ap.apply_scene(v1, scene, [{'id': 'b', 'position': [0.9, -0.4, 0.75]}], True, 'x', llms=NoLLM())
    assert pl.collision_episodes(v2) == 0 and v2.repair is None       # no collision, so no model was asked


async def test_handler_round_trip(tmp_path):
    v1 = await make_v1(tmp_path)
    scene = edited_scene(v1, [table(v1, 'a'), NEW_TABLE])
    inp = {'base': version_json(v1), 'scene': scene.model_dump(mode='json'), 'auto_repair': False,
           'markers': [{'id': 'b', 'position': [0.9, -0.4, 0.75]}], 'note': 'moved B onto the new table'}
    request = {'protocol': 1, 'task': 'apply_scene', 'input': inp, 'llm': {'roles': {}, 'profile': None},
               'out_dir': str(tmp_path / 'out')}
    data, _ = await runner.run('apply_scene', request, protocol.Emitter(io.StringIO()))
    assert set(data) == {'version', 'parsed'} and data['parsed']
    v = data['version']
    assert v['parent_version'] == 1 and v['change_summary'].startswith(PREFIX)
    assert next(m for m in v['spec']['markers'] if m['id'] == 'b')['position'] == [0.9, -0.4, 0.75]
    inp['markers'] = [{'id': 'nope', 'position': [0, 0, 0]}]
    with pytest.raises(ap.SceneInvalid):
        await runner.run('apply_scene', request, protocol.Emitter(io.StringIO()))
