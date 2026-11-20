"""Generic motions: rotate, attached objects, at-most limits and timed moves (fixture specs; no model calls)."""
import math

import numpy as np
import pytest

from studio import families, physics, sim
from studio.agents.spec import validate_spec
from studio.schemas import (Marker, PhysicsInputs, RobotConcept, SceneSpec, TaskAction, TaskObject,
                            TechnicalSpec)

GANTRY = {'x_stroke_m': 0.3, 'y_stroke_m': 0.3, 'z_stroke_m': 5.64, 'frame_height_m': 6.04, 'z_offset_m': 0.2,
          'tool_length_m': 0.3, 'tool_yaw': True, 'tool_yaw_continuous': True, 'beam_size_m': 0.6,
          'max_speed_m_s': [0.05, 0.05, 0.1], 'max_accel_m_s2': [1.3, 1.3, 1.0],
          'max_force_n': [46870.0, 46840.0, 389140.0], 'gripper': 'none', 'payload_kg': 14400.0}


def spin_mill(lift_s=30.0, max_accel=1.0, rpm=4.0):
    acts = [TaskAction(verb='wait', object='cabin', target='cabin_home', duration_s=60.0),
            TaskAction(verb='move', object='cabin', target='cabin_raised', duration_s=lift_s, description='lift'),
            TaskAction(verb='rotate', turns=5, rpm=rpm, ramp_s=10, description='spin'),
            TaskAction(verb='move', object='cabin', target='cabin_home', duration_s=30.0, description='lower'),
            TaskAction(verb='wait', object='cabin', target='cabin_home', duration_s=60.0),
            TaskAction(verb='home')]
    return TechnicalSpec(
        title='Spin mill', summary='Lift, spin five turns, lower.', actions=acts,
        objects=[TaskObject(id='cabin', name='Cabin', mass_kg=5000, size_m=(30, 30, 3), shape='cylinder', grip='none',
                            attached=True, start_marker='cabin_home')],
        markers=[Marker(id='robot_base', name='Pole', position=(0, 0, 0)),
                 Marker(id='cabin_home', name='Home', position=(0, 0, 0)),
                 Marker(id='cabin_raised', name='Raised', position=(0, 0, 1.22))],
        physics_inputs=PhysicsInputs(object_mass_kg=5000, object_size_m=(30, 30, 3), grip='none', cycle_time_s=600,
                                     approach_height_m=1.22, max_accel_m_s2=max_accel, max_jerk_m_s3=2.0,
                                     peak_acceleration_m_s2=1.0))


def run(spec, params=GANTRY):
    base = physics.place_base(spec)
    reqs = physics.derive(spec, base)
    concept = RobotConcept(family='gantry', name='t', params=params, base=base)
    return sim.simulate(spec, reqs, SceneSpec(boxes=[], robot_base=base), concept), reqs


def check(res, rid):
    return next(c for c in res.checks if c.requirement_id == rid)


def test_spin_mill_rotates_lifts_and_loads_the_cabin():
    res, reqs = run(spin_mill())
    yaw = [f.joints['tool_yaw'] for f in res.frames]
    assert max(yaw) - min(yaw) == pytest.approx(10 * math.pi, rel=1e-6)
    assert res.frames[0].holding == 'cabin'
    # the lift is the first stretch of z motion after the 60 s wait: it takes 30 s
    z = np.array([f.tcp[2] for f in res.frames])
    t = np.array([f.t for f in res.frames])
    moving = t[np.abs(np.gradient(z, t)) > 1e-6]
    assert moving[0] == pytest.approx(60.0, abs=0.3)
    lift_end = t[(t > 60.0) & (np.abs(z - z.max()) < 1e-6)][0]
    assert lift_end - 60.0 == pytest.approx(30.0, abs=0.3)  # frames are thinned to 1500
    assert check(res, 'joint_torque_z_axis').actual >= 5000 * 9.81
    r = check(res, 'rotate_2')
    assert r.actual == pytest.approx(85.0, abs=0.1) and r.passed is None
    assert '5 turns at 4 rpm, ramp 10 s on tool_yaw: 85.0 s' in r.detail
    assert check(res, 'rotate_range_tool_yaw').passed is True
    assert res.duration_s == pytest.approx(60 + 30 + 85 + 30 + 60, abs=0.5)
    assert check(res, 'joint_limits').passed is True


def test_max_accel_fails_with_centripetal_rim_acceleration_and_jerk_is_not_judged():
    res, reqs = run(spin_mill())
    assert {r.id for r in reqs} >= {'max_accel', 'max_jerk'}
    c = check(res, 'max_accel')
    assert c.passed is False and c.actual == pytest.approx(2.63, rel=0.05)
    assert 'centripetal' in c.detail and c.calc.inputs['a_centripetal'].value == pytest.approx(2.63, rel=0.01)
    j = check(res, 'max_jerk')
    assert j.passed is None and 'not bounded' in j.detail
    slow, _ = run(spin_mill(rpm=1.0))
    assert check(slow, 'max_accel').passed is True


def test_rotate_range_needs_a_continuous_joint():
    res, _ = run(spin_mill(), {**GANTRY, 'tool_yaw_continuous': False})
    c = check(res, 'rotate_range_tool_yaw')
    assert c.passed is False and '5 turns need a continuous joint' in c.detail and '180' in c.detail
    assert check(res, 'joint_limits').passed is True        # rotated joints are not limit-checked


def test_timed_move_too_short_fails():
    res, _ = run(spin_mill(lift_s=5.0))
    c = check(res, 'move_time_1')
    assert c.passed is False and 'allowed 5.0 s' in c.detail and not res.ok


def test_rotate_without_a_joint_fails():
    res, _ = run(spin_mill(), {**GANTRY, 'tool_yaw': False, 'tool_yaw_continuous': False})
    assert check(res, 'rotate').passed is False
    assert any(e.kind == 'warning' for e in res.events)


def test_defaults_are_dropped_from_json_and_the_urdf_is_valid():
    plain = TaskAction(verb='wait', duration_s=1.0).model_dump()
    assert not {'turns', 'rpm', 'ramp_s', 'axis'} & plain.keys()
    assert 'attached' not in TaskObject(id='a', name='a', mass_kg=1, size_m=(1, 1, 1)).model_dump()
    assert not {'max_accel_m_s2', 'max_jerk_m_s3'} & PhysicsInputs(
        object_mass_kg=1, object_size_m=(1, 1, 1), cycle_time_s=1).model_dump().keys()
    assert 'tool_yaw_continuous' not in families.get('gantry').parse({}).model_dump()
    fam = families.get('gantry')
    xml = fam.build_urdf(fam.parse(GANTRY), 't')
    seg = xml[xml.index('name="tool_yaw"'):].split('</joint>')[0]
    assert 'type="continuous"' in xml and '<limit effort=' in seg and 'lower=' not in seg


def test_spec_validator_rotate_rules():
    ok = spin_mill()
    validate_spec(ok)
    bad = ok.model_copy(update={'actions': [TaskAction(verb='rotate', turns=2, rpm=0)]})
    with pytest.raises(ValueError, match='rpm > 0'):
        validate_spec(bad)
    long = ok.model_copy(update={'physics_inputs': ok.physics_inputs.model_copy(update={'cycle_time_s': 200.0})})
    with pytest.raises(ValueError, match='add up'):
        validate_spec(long)
