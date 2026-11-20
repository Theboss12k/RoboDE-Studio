"""Regressions from the real-model revise run (tests/fixtures/real_bottle_revise.json): the saved v1 design and what the
real revise agent wrote. The revise LLM is scripted with that answer; the pipeline, families and simulator are real code.
No model is called."""
import json
import math

import pytest
from conftest import FIXTURES

from studio import families
from studio.llm.registry import LLMRegistry
from studio.llm.scripted import ScriptedLLM
from studio.pipeline import design as pipeline
from studio.schemas import DesignVersion, TechnicalSpec

DATA = json.loads((FIXTURES / 'real_bottle_revise.json').read_text())
BASE_XYZ = (0.5, 0.0, 0.75)


def with_base_marker(spec):
    d = spec.model_dump(mode='json')
    d['markers'] = [*d['markers'], {'id': 'robot_base', 'name': 'Robot base', 'position': list(BASE_XYZ)}]
    return TechnicalSpec.model_validate(d)


def bridged_scene(spec, extra=None):
    """One table spanning A and B (the gap closed), the object at A."""
    boxes = [{'id': 'table', 'kind': 'table', 'center': [0.5, 0.0, 0.375], 'size': [0.6, 1.0, 0.75]}] + (extra or [])
    o = spec.objects[0]
    a = spec.marker('point_a').position
    return {'floor_size_m': [3.0, 3.0], 'boxes': boxes, 'markers': [m.model_dump(mode='json') for m in spec.markers],
            'objects': [{'id': o.id, 'shape': o.shape, 'center': [a[0], a[1], a[2] + o.size_m[2] / 2], 'size': list(o.size_m)}]}


def model_patch(old, with_marker=False):
    """What the real model wrote: cycle 4 s, assumption text, robot.base at z 0.75, a shorter base column."""
    spec = TechnicalSpec.model_validate(DATA['v2_spec'])
    if with_marker:
        spec = with_base_marker(spec)
    robot = {'family': 'serial_arm', 'name': old.robot.name, 'rationale': DATA['v2_robot_rationale'],
             'params': {**old.robot.params, 'base_height_m': 0.16}, 'base': {'position': list(BASE_XYZ), 'yaw': 0.0}}
    patch = {'targets': ['spec', 'robot', 'scene'], 'spec': spec.model_dump(mode='json'), 'robot': robot,
             'scene': bridged_scene(spec), 'summary': DATA['v2_change_summary']}
    return patch


async def run_revise(tmp_path, patch_for_old, scene_script=None):
    v1 = DesignVersion.model_validate(DATA['v1'])
    registry = LLMRegistry(tmp_path, {})
    rules = [('change an existing', patch_for_old(v1))]
    if scene_script:
        rules.append(('3D scene', scene_script))
    registry.overrides['revise'] = ScriptedLLM(rules)
    registry.overrides['scene'] = ScriptedLLM([('3D scene', scene_script or bridged_scene(with_base_marker(TechnicalSpec.model_validate(DATA['v2_spec']))))])
    return v1, await pipeline.revise(registry, v1, DATA['change_request'], None, lambda t: None)


def check_v2(v1, v2):
    assert v2.robot.base.position == pytest.approx(BASE_XYZ)
    marker = v2.spec.marker('robot_base')
    assert tuple(marker.position) == pytest.approx(BASE_XYZ)
    assert v2.scene.robot_base == v2.robot.base
    top = [b for b in v2.scene.boxes if abs(b.center[2] + b.size[2] / 2 - 0.75) < 0.01
           and abs(b.center[0] - 0.5) <= b.size[0] / 2 and abs(b.center[1]) <= b.size[1] / 2]
    assert top, 'no mounting surface under the base'
    assert not [e for e in v2.sim.events if e.kind == 'collision']
    assert next(c for c in v2.sim.checks if c.requirement_id == 'collision_free').passed
    assert v2.robot.params['base_height_m'] < 0.5      # sized relative to the mount, not to the floor


async def test_patch_with_only_robot_base_gets_a_marker_and_a_mounted_scene(tmp_path):
    v1, v2 = await run_revise(tmp_path, lambda old: model_patch(old))
    check_v2(v1, v2)
    assert v1.spec.markers == DesignVersion.model_validate(DATA['v1']).spec.markers     # v1 untouched


async def test_patch_with_marker_directly(tmp_path):
    v1, v2 = await run_revise(tmp_path, lambda old: model_patch(old, with_marker=True))
    check_v2(v1, v2)


async def test_scene_is_rebuilt_when_patch_has_no_scene(tmp_path):
    def no_scene(old):
        p = model_patch(old)
        p['targets'].remove('scene')
        del p['scene']
        return p
    v1, v2 = await run_revise(tmp_path, no_scene)      # the scene agent (scripted with a bridged table) runs
    check_v2(v1, v2)


async def test_four_second_cycle_passes_after_speed_sizing(tmp_path):
    v1, v2 = await run_revise(tmp_path, lambda old: model_patch(old))
    cyc = next(c for c in v2.sim.checks if c.requirement_id == 'cycle_time')
    assert cyc.passed, cyc.detail
    speeds = v2.robot.params['max_speed_deg_s']
    assert max(speeds) > 180 and max(speeds) <= 360
    assert 'Speed sizing' in v2.robot.rationale


def test_scene_validator_requires_a_mounting_surface():
    from studio.agents.scene import validate_scene
    from studio.physics import place_base
    spec = TechnicalSpec.model_validate(DATA['v2_spec'])
    spec = with_base_marker(spec)
    base = place_base(spec)
    from studio.schemas import SceneSpec
    two = bridged_scene(spec)
    gap = {**two, 'boxes': [{'id': 'ta', 'kind': 'table', 'center': [0.5, -0.4, 0.375], 'size': [0.6, 0.4, 0.75]},
                            {'id': 'tb', 'kind': 'table', 'center': [0.5, 0.4, 0.375], 'size': [0.6, 0.4, 0.75]}]}
    with pytest.raises(ValueError, match='nothing under it'):
        validate_scene(SceneSpec.model_validate(gap), spec, base)
    validate_scene(SceneSpec.model_validate(two), spec, base)


def test_sizing_loop_reports_the_limiting_joint_when_caps_prevent_the_cycle():
    from studio.schemas import RobotConcept, SceneSpec
    from studio import physics
    v1 = DesignVersion.model_validate(DATA['v1'])
    spec = TechnicalSpec.model_validate(DATA['v2_spec'])
    spec.physics_inputs.cycle_time_s = 1.0
    spec = with_base_marker(spec)
    base = physics.place_base(spec)
    reqs = physics.derive(spec, base)
    scene = SceneSpec.model_validate(bridged_scene(spec)).model_copy(update={'robot_base': base})
    concept = RobotConcept(family='serial_arm', name='x', params=v1.robot.params, base=base)
    sized, urdf, sim = pipeline.size_and_simulate(spec, reqs, scene, concept, base)
    cyc = next(c for c in sim.checks if c.requirement_id == 'cycle_time')
    assert cyc.passed is False and 'speed cap' in cyc.detail
    assert max(sized.params['max_speed_deg_s']) <= 360 + 1e-6


def test_explicit_speeds_win_over_sizing():
    from studio.schemas import RobotConcept, SceneSpec
    from studio import physics
    v1 = DesignVersion.model_validate(DATA['v1'])
    spec = TechnicalSpec.model_validate(DATA['v2_spec'])
    spec = with_base_marker(spec)
    base = physics.place_base(spec)
    scene = SceneSpec.model_validate(bridged_scene(spec)).model_copy(update={'robot_base': base})
    concept = RobotConcept(family='serial_arm', name='x', params=v1.robot.params, base=base)
    explicit = {'max_speed_deg_s': [120.0, 120.0, 120.0, 120.0]}
    sized, _, sim = pipeline.size_and_simulate(spec, physics.derive(spec, base), scene, concept, base, explicit)
    assert sized.params['max_speed_deg_s'] == [120.0] * 4


# --- caps apply to every source; a revision that changes the task re-sizes the robot ---------------------------------
def _sizing_inputs(hint_params, cycle=None):
    from studio import physics
    from studio.schemas import RobotConcept, SceneSpec
    spec = with_base_marker(TechnicalSpec.model_validate(DATA['v2_spec']))
    if cycle:
        spec.physics_inputs.cycle_time_s = cycle
    base = physics.place_base(spec)
    scene = SceneSpec.model_validate(bridged_scene(spec)).model_copy(update={'robot_base': base})
    concept = RobotConcept(family='serial_arm', name='x', params=hint_params, base=base)
    return spec, physics.derive(spec, base), scene, concept, base


def test_hint_speeds_above_the_cap_are_clamped():
    v1 = DesignVersion.model_validate(DATA['v1'])
    hint = {**v1.robot.params, 'max_speed_deg_s': [1401.0, 1401.0, 1401.0, 1821.0], 'max_tcp_speed_m_s': 15.4}
    sized, _, _ = pipeline.size_and_simulate(*_sizing_inputs(hint))
    assert max(sized.params['max_speed_deg_s']) <= 360 + 1e-6
    assert sized.params['max_tcp_speed_m_s'] <= 4.0 + 1e-6


def test_explicit_speed_above_the_cap_is_kept_and_the_check_reports_it():
    v1 = DesignVersion.model_validate(DATA['v1'])
    explicit = {'max_speed_deg_s': [1400.0] * 4}
    sized, _, sim = pipeline.size_and_simulate(*_sizing_inputs(v1.robot.params), explicit)
    assert sized.params['max_speed_deg_s'] == [1400.0] * 4
    c = next(c for c in sim.checks if c.requirement_id == 'joint_speed')
    assert c.actual == pytest.approx(math.radians(1400.0), rel=1e-3) and c.passed   # the rating as the user gave it


def test_caps_cover_the_other_families():
    from studio.families.gantry import MAX_AXIS_ACCEL_M_S2, MAX_AXIS_SPEED_M_S
    from studio.families.mobile_base import MAX_ACCEL_M_S2, MAX_SPEED_M_S
    g = families.get('gantry').clamp_to_caps({'max_speed_m_s': [9, 9, 9], 'max_accel_m_s2': [99, 99, 99]})
    assert max(g['max_speed_m_s']) == MAX_AXIS_SPEED_M_S and max(g['max_accel_m_s2']) == MAX_AXIS_ACCEL_M_S2
    b = families.get('mobile_base').clamp_to_caps({'max_speed_m_s': 9, 'max_accel_m_s2': 9})
    assert (b['max_speed_m_s'], b['max_accel_m_s2']) == (MAX_SPEED_M_S, MAX_ACCEL_M_S2)
    m = families.get('mobile_manipulator').clamp_to_caps({'arm': {'max_speed_deg_s': [999] * 6}, 'base': {'max_speed_m_s': 9}})
    assert max(m['arm']['max_speed_deg_s']) == 360 and m['base']['max_speed_m_s'] == MAX_SPEED_M_S


def test_sizing_hint_drops_sized_numbers_and_keeps_choices_and_explicit():
    old = {'dof': 4, 'gripper': 'vacuum', 'upper_arm_m': 0.59, 'forearm_m': 0.54, 'max_speed_deg_s': [1, 2, 3, 4],
           'upper_arm_mass_kg': 4.6, 'tool_length_m': 0.2}
    h = pipeline.sizing_hint('serial_arm', old, {'upper_arm_m': 0.3})
    assert h == {'dof': 4, 'gripper': 'vacuum', 'tool_length_m': 0.2, 'upper_arm_m': 0.3}
    mm = pipeline.sizing_hint('mobile_manipulator', {'arm': old, 'base': {'length_m': 2.0, 'deck_height_m': 0.4},
                                                     'arm_mount_m': [0.1, 0, 0]})
    assert mm['base'] == {'deck_height_m': 0.4} and mm['arm']['gripper'] == 'vacuum' and 'forearm_m' not in mm['arm']


async def test_revise_that_moves_the_base_resizes_the_arm_for_the_new_task(tmp_path):
    v1, v2 = await run_revise(tmp_path, lambda old: model_patch(old))      # v1 links: 0.59 + 0.54 m, sized for the floor
    old, p = v1.robot.params, v2.robot.params
    need = next(r.target for r in v2.requirements if r.id == 'reach_max') / 0.85
    links = p['upper_arm_m'] + p['forearm_m']           # shoulder-to-wrist link reach (the 0.85 rule applies to it)
    assert old['upper_arm_m'] + old['forearm_m'] > 1.1
    assert need <= links <= 1.4 * need, (links, need)
    assert max(p['max_speed_deg_s']) <= 360 + 1e-6
    assert next(c for c in v2.sim.checks if c.requirement_id == 'reach_max').passed
    assert next(c for c in v2.sim.checks if c.requirement_id == 'collision_free').passed
