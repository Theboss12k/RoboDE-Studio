"""Robot studio sizing (robot/sizing.py): hand-calculated values, no LLM, no network."""
import math

import pytest
from test_pipeline import make_v1, setup

from studio.physics.statics import G
from studio.robot import sizing as sz
from studio.schemas import Drive, Frame, JointSizing, MotorAlternative, MotorChoice, RobotConcept, SpecValue


@pytest.fixture
async def v1(tmp_path):
    return await make_v1(setup(tmp_path))


def planar(v1, frames=(), **params):
    """The bottle design reduced to a 2-link planar arm: no wrist, tool or their masses (dof 4)."""
    p = {**v1.robot.params, 'dof': 4, 'wrist_m': 0.0, 'tool_length_m': 0.0, 'wrist_mass_kg': 0.0, 'tool_mass_kg': 0.0,
         'upper_arm_m': 0.4, 'forearm_m': 0.3, 'upper_arm_mass_kg': 2.0, 'forearm_mass_kg': 1.0, 'payload_kg': 0.5,
         'joint_limits_deg': [], 'max_speed_deg_s': [], 'max_torque_nm': [], **params}
    return v1.model_copy(update={'robot': v1.robot.model_copy(update={'params': p}),
                                 'sim': v1.sim.model_copy(update={'frames': list(frames)})})


def by_joint(sizing):
    return {s.joint: s for s in sizing}


async def test_planar_static_is_sum_m_g_r(v1):
    s = by_joint(sz.size_joints(planar(v1)))
    j2 = G * (2.0 * 0.2 + 1.0 * (0.4 + 0.15) + 0.5 * 0.7)           # m1 r1 + m2 r2 + payload r
    j3 = G * (1.0 * 0.15 + 0.5 * 0.3)
    assert s['j2'].static_torque == pytest.approx(j2, rel=1e-4) and s['j3'].static_torque == pytest.approx(j3, rel=1e-4)
    assert s['j1'].static_torque == pytest.approx(0, abs=1e-6)
    # no frames, no motion: no acceleration term; continuous = SF 1.25 x RMS (one pose), peak = SF 1.5 x worst
    assert s['j2'].accel_torque == 0 and s['j2'].speed == 0
    assert s['j2'].continuous == pytest.approx(1.25 * j2, rel=1e-4) and s['j2'].peak == pytest.approx(1.5 * j2, rel=1e-4)
    assert all(len(j.calcs) == 7 and all(c.formula and c.unit for c in j.calcs) for j in s.values())


async def test_planar_accel_rms_and_peak(v1):
    q = {'j1': 0, 'j2': math.pi / 2, 'j3': 0, 'j4': 0}
    frames = [Frame(t=0, joints=q), Frame(t=1.0, joints={**q, 'j2': math.pi / 2 + 0.5})]
    s = by_joint(sz.size_joints(planar(v1, frames)))['j2']
    inertia = 2.0 * 0.2 ** 2 + 1.0 * 0.55 ** 2 + 0.5 * 0.7 ** 2 + (2.0 * 0.4 ** 2 + 1.0 * 0.3 ** 2) / 12
    alpha = 0.5 / sz.ACCEL_RAMP_S
    assert s.speed == pytest.approx(0.5) and s.accel_torque == pytest.approx(inertia * alpha, rel=1e-4)
    static = G * (2.0 * 0.2 + 1.0 * 0.55 + 0.5 * 0.7)
    assert s.peak == pytest.approx(1.5 * (static + inertia * alpha), rel=1e-4)
    assert s.continuous < s.peak and s.continuous > 1.25 * 0.5 * static


async def test_planar_rms_over_frames_with_carried_payload(v1):
    q = {'j1': 0, 'j2': math.pi / 2, 'j3': 0, 'j4': 0}
    frames = [Frame(t=0, joints=q, holding='bottle'), Frame(t=0.5, joints=q, holding='bottle')]
    s = by_joint(sz.size_joints(planar(v1, frames)))['j2']
    carried = G * (2.0 * 0.2 + 1.0 * 0.55 + 0.3 * 0.7)               # the bottle weighs 0.3 kg
    rated = G * (2.0 * 0.2 + 1.0 * 0.55 + 0.5 * 0.7)                 # rated payload at the stretched pose
    assert s.continuous == pytest.approx(1.25 * carried, rel=1e-4)
    assert s.peak == pytest.approx(1.5 * rated, rel=1e-4)


async def test_extra_masses_load_the_parent_joints(v1):
    base = by_joint(sz.size_joints(planar(v1)))
    more = by_joint(sz.size_joints(planar(v1), {'upper_arm': 1.0, 'turret': 5.0, 'nowhere': 9.0}))
    assert more['j2'].static_torque - base['j2'].static_torque == pytest.approx(G * 1.0 * 0.2, rel=1e-4)
    assert more['j3'].static_torque == pytest.approx(base['j3'].static_torque, rel=1e-6)
    assert sz.parent_link('serial_arm', 'j3') == 'upper_arm'


async def test_bottle_arm_stretched_pose(v1):
    p = v1.robot.params
    L1, L2, Lw, Lt = p['upper_arm_m'], p['forearm_m'], p['wrist_m'], p['tool_length_m']
    arm = [(p['upper_arm_mass_kg'], L1 / 2), (p['forearm_mass_kg'], L1 + L2 / 2), (p['wrist_mass_kg'], L1 + L2 + Lw / 2),
           (p['tool_mass_kg'], L1 + L2 + Lw + Lt / 2), (p['payload_kg'], L1 + L2 + Lw + Lt)]
    j2 = G * sum(m * r for m, r in arm)
    j4 = G * (p['wrist_mass_kg'] * Lw / 2 + p['tool_mass_kg'] * (Lw + Lt / 2) + p['payload_kg'] * (Lw + Lt))
    s = by_joint(sz.size_joints(v1))
    assert list(s) == ['j1', 'j2', 'j3', 'j4', 'j5', 'j6'] and all(x.kind == 'revolute' for x in s.values())
    assert s['j2'].static_torque == pytest.approx(j2, rel=1e-4)       # the stretched pose is the worst case
    assert s['j4'].static_torque == pytest.approx(j4, rel=1e-4)
    assert s['j1'].static_torque == pytest.approx(0, abs=1e-6)
    for x in s.values():
        assert x.peak >= 1.5 * x.static_torque - 1e-9 and x.continuous <= x.peak + 1e-9
    assert s['j1'].speed > 0 and s['j1'].accel_torque > 0          # the base yaw moves in the task


def motor_values(**over):
    base = {'holding_torque_nm': 3.0, 'rated_speed_rpm': 600.0, 'current_a': 2.8, 'voltage_v': 24.0}
    base.update(over)
    return [SpecValue(key=k, value=v, status='verified', quote=f'{k} {v}') for k, v in base.items() if v is not None]


def alt(values, kind='stepper'):
    return MotorAlternative(id='robu_x', name='X', motor_type=kind, values=values)


def joint(**over):
    base = dict(joint='j2', kind='revolute', static_torque=8.0, accel_torque=2.0, continuous=10.0, peak=15.0, speed=2.0,
                calcs=[sz._calc('J_load = test', {'x': 1}, 0.4, 'kg*m^2')])
    base.update(over)
    return JointSizing(**base)


def test_motor_side_planetary_10_to_1():
    d = Drive(type='planetary', ratio=10.0, efficiency=0.9)
    m = sz.motor_side(joint(peak=20.0), d)
    assert m['continuous_nm'] == pytest.approx(10 / (10 * 0.9)) and m['peak_nm'] == pytest.approx(20 / 9)
    assert m['speed_rpm'] == pytest.approx(2.0 * 10 * 60 / (2 * math.pi))
    lead = Drive(type='belt', ratio=2 * math.pi / 0.04, efficiency=0.9, lead_m=0.04)
    lin = JointSizing(joint='x_axis', kind='prismatic', static_torque=1, accel_torque=1, continuous=100.0, peak=150.0,
                      speed=1.0, torque_unit='N', speed_unit='m/s')
    assert sz.motor_side(lin, lead)['continuous_nm'] == pytest.approx(100 * 0.04 / (2 * math.pi * 0.9))   # F lead / (2 pi eta)


def test_evaluate_passes_with_margins():
    d = Drive(type='planetary', ratio=10.0, efficiency=0.9)
    ok, margins, checks, reason = sz.evaluate(alt(motor_values(rotor_inertia_kgm2=1e-3)), joint(), d, 'stepper')
    assert ok and reason.startswith('Passes')
    assert margins['continuous'] == pytest.approx(3.0 * 0.7 * 9 / 10)           # holding x 0.7 x N eta / required
    assert margins['speed'] == pytest.approx(0.8 * 600 * 2 * math.pi / 60 / 10 / 2.0)   # 0.8 rated, joint side
    assert margins['inertia_ratio'] == pytest.approx(0.4 / 100 / 1e-3)
    assert all(c.passed is not False and c.calc for c in checks)


def test_evaluate_fails_on_torque_speed_and_inertia():
    d = Drive(type='planetary', ratio=10.0, efficiency=0.9)
    weak = sz.evaluate(alt(motor_values(holding_torque_nm=1.0)), joint(), d, 'stepper')
    assert not weak[0] and weak[1]['continuous'] < 1 and 'Continuous torque' in weak[3]
    slow = sz.evaluate(alt(motor_values(rated_speed_rpm=100.0)), joint(), d, 'stepper')
    assert not slow[0] and slow[1]['speed'] < 1
    spin = sz.evaluate(alt(motor_values(rotor_inertia_kgm2=1e-6)), joint(), d, 'stepper')
    assert not spin[0] and spin[1]['inertia_ratio'] > sz.INERTIA_RATIO_MAX['stepper']
    # a servo may carry a higher inertia ratio than a stepper
    assert sz.INERTIA_RATIO_MAX['servo'] > sz.INERTIA_RATIO_MAX['stepper']
    # speed exactly 0.8 of rated passes, just above fails
    edge = joint(speed=0.8 * 600 * 2 * math.pi / 60 / 10)
    assert sz.evaluate(alt(motor_values()), edge, d, 'stepper')[0]
    assert not sz.evaluate(alt(motor_values()), joint(speed=edge.speed * 1.01), d, 'stepper')[0]


def test_evaluate_needs_verified_deciding_values():
    d = Drive(type='planetary', ratio=10.0, efficiency=0.9)
    # a stepper with no verified speed is no longer rejected (assumed usable speed, see the tests below), so the
    # missing speed is checked on a BLDC; torque, current and voltage still decide for a stepper
    for key, word, kind in (('holding_torque_nm', 'torque', 'stepper'), ('rated_speed_rpm', 'speed', 'bldc'),
                            ('current_a', 'current', 'stepper'), ('voltage_v', 'voltage', 'stepper')):
        ok, margins, checks, reason = sz.evaluate(alt(motor_values(**{key: None}, **(
            {'rated_torque_nm': 3.0} if kind == 'bldc' else {})), kind), joint(), d, kind)
        assert not ok and word in reason and checks[0].passed is False
    assumed = [v.model_copy(update={'status': 'assumed'}) if v.key == 'holding_torque_nm' else v for v in motor_values()]
    assert not sz.evaluate(alt(assumed), joint(), d, 'stepper')[0]       # assumed values never decide
    noload = [v for v in motor_values(rated_speed_rpm=None)] + [SpecValue(key='no_load_speed_rpm', value=1000.0,
                                                                         status='verified', quote='q')]
    assert sz.evaluate(alt(noload), joint(), d, 'stepper')[1]['speed'] == pytest.approx(
        0.8 * 600 * 2 * math.pi / 60 / 10 / 2.0)                            # 1000 rpm no-load x 0.6 derating


def test_defined_motor_values():
    s = joint(continuous=10.0, peak=20.0, speed=2.0)
    d = Drive(type='planetary', ratio=20.0, efficiency=0.85)
    m = sz.defined_motor(s, d, 'serial_arm')
    k = 20 * 0.85
    vals = {v.key: v for v in m.values}
    assert m.source == 'defined' and m.alternative_id is None and m.motor_type == 'stepper'
    assert vals['required_torque_nm'].value == pytest.approx(10 / k) and vals['required_peak_torque_nm'].value == pytest.approx(20 / k)
    assert vals['required_speed_rpm'].value == pytest.approx(2.0 * 20 * 60 / (2 * math.pi) / 0.8)
    assert vals['frame'].text == 'NEMA 23' and vals['gear_ratio'].value == 20.0   # 1.18 N*m peak / 0.7 = 1.68 needs NEMA 23
    assert vals['required_torque_nm'].status == 'defined' and vals['flange_mm'].status == 'assumed'
    assert all(mv > 0 for mv in m.provided.values()) and min(m.margins.values()) >= 1.0
    assert m.checks[0].passed and 'procure' in m.checks[0].detail


def test_defined_motor_servo_for_heavy_joint_and_wheel_motor():
    heavy = joint(continuous=200.0, peak=300.0, speed=1.0)
    m = sz.defined_motor(heavy, Drive(type='planetary', ratio=10.0, efficiency=0.9), 'serial_arm')
    assert m.motor_type == 'bldc' and {v.key for v in m.values} >= {'voltage_v', 'current_a', 'shaft_d_mm'}
    wheel = joint(joint='wheel_left', kind='wheel', continuous=1.0, peak=2.0, speed=15.0)
    w = sz.defined_motor(wheel, Drive(type='wheel_gearbox', ratio=20.0, efficiency=0.85), 'mobile_base')
    assert w.motor_type == 'dc_gear'


def choice(joint_name, amps, volts, kind='stepper'):
    vals = [SpecValue(key='current_a', value=amps, status='verified', quote='q'),
            SpecValue(key='voltage_v', value=volts, status='verified', quote='q')]
    return MotorChoice(joint=joint_name, source='shop', alternative_id='robu_x', name='x', motor_type=kind, values=vals,
                       drive=Drive(type='direct', ratio=1.0, efficiency=1.0))


def test_electronics_driver_controller_psu():
    items = sz.electronics_for([choice('j1', 2.8, 24.0), choice('j2', 2.8, 24.0)], 'serial_arm')
    by = {i.id: i for i in items}
    d = by['driver_j1']
    assert d.role == 'driver' and d.checks[0].required == pytest.approx(1.2 * 2.8) and d.checks[0].actual == 4.2
    assert d.checks[0].passed
    assert by['controller'].values[0].value == 3.0 and by['controller'].checks[0].passed
    psu = by['psu_24v']
    assert psu.checks[0].required == pytest.approx(1.3 * (24 * 2.8 * 2)) and psu.values[1].value == 240.0
    assert psu.checks[0].passed and all(i.source == 'defined' for i in items)
    unknown = sz.electronics_for([choice('j1', 2.8, 24.0).model_copy(update={'values': []})], 'serial_arm')
    assert unknown[0].checks[0].passed is None


def test_electronics_battery_for_mobile_base():
    items = sz.electronics_for([choice('wheel_left', 5.0, 24.0, 'dc_gear'), choice('wheel_right', 5.0, 24.0, 'dc_gear')],
                               'mobile_base', battery_wh=500.0)
    assert [i.role for i in items] == ['driver', 'driver', 'controller', 'battery']
    bat = items[-1]
    assert bat.checks[0].required == pytest.approx(1.3 * 240) and bat.checks[0].passed
    short = sz.electronics_for([choice('wheel_left', 5.0, 24.0, 'dc_gear')], 'mobile_base', battery_wh=50.0)[-1]
    assert short.checks[0].passed is False


def gantry_version(v1, frames=()):
    params = {'payload_kg': 10.0, 'x_moving_mass_kg': 12.0, 'y_moving_mass_kg': 6.0, 'z_moving_mass_kg': 3.0,
              'tool_mass_kg': 0.5}
    return v1.model_copy(update={'robot': RobotConcept(family='gantry', name='g', params=params),
                                 'sim': v1.sim.model_copy(update={'frames': list(frames)})})


async def test_gantry_belt_and_leadscrew(v1):
    q = {'x_axis': 0.0, 'y_axis': 0.0, 'z_axis': 0.1}
    frames = [Frame(t=0, joints=q), Frame(t=1.0, joints={**q, 'x_axis': 0.5})]
    s = by_joint(sz.size_joints(gantry_version(v1, frames)))
    assert list(s) == ['x_axis', 'y_axis', 'z_axis'] and all(x.kind == 'prismatic' and x.torque_unit == 'N' for x in s.values())
    mx, my, mz = 12 + 6 + 3 + 0.5 + 10, 6 + 3 + 0.5 + 10, 3 + 0.5 + 10
    assert s['x_axis'].static_torque == pytest.approx(0.05 * mx * G, rel=1e-6)        # guide drag only
    assert s['z_axis'].static_torque == pytest.approx(mz * G, rel=1e-6)             # Z holds its weight
    assert s['x_axis'].accel_torque == pytest.approx(mx * 0.5 / 0.2, rel=1e-6)       # m * (v / ramp)
    belts = sz.drive_options('gantry', 0, s['x_axis'])
    assert {d.type for d in belts} == {'belt', 'rack'} and belts[0].ratio == pytest.approx(2 * math.pi / 0.04)
    screws = sz.drive_options('gantry', 2, s['z_axis'])
    assert [d.lead_m for d in screws] == [0.002, 0.005, 0.008, 0.010]
    assert sz.self_locking(screws[0]) and not sz.self_locking(screws[3])
    assert screws[0].efficiency < screws[3].efficiency < 1
    # a motor holding the Z load: self-locking lead passes the holding check, a steep lead cannot be checked
    a = alt(motor_values(holding_torque_nm=3.0, rated_speed_rpm=3000.0))
    z = s['z_axis']
    hold = lambda drive: [c for c in sz.evaluate(a, z, drive, 'stepper')[2] if c.requirement_id.endswith('holding')][0]
    assert hold(screws[0]).passed is True and hold(screws[3]).passed is None


async def test_mobile_base_wheels(v1):
    params = {'mass_kg': 40.0, 'payload_kg': 50.0, 'wheel_radius_m': 0.08, 'drive_wheels': 2, 'max_accel_m_s2': 0.8,
              'max_slope_deg': 5.0, 'max_speed_m_s': 1.2}
    v = v1.model_copy(update={'robot': RobotConcept(family='mobile_base', name='b', params=params)})
    s = sz.size_joints(v)
    assert [x.joint for x in s] == ['wheel_left', 'wheel_right'] and s[0].kind == 'wheel'
    crr, th = v1.spec.physics_inputs.rolling_resistance, math.radians(5.0)
    static = 90.0 * G * (math.sin(th) + crr * math.cos(th)) * 0.08 / 2
    accel = 90.0 * 0.8 * 0.08 / 2
    assert s[0].static_torque == pytest.approx(static, rel=1e-6) and s[0].accel_torque == pytest.approx(accel, rel=1e-6)
    assert s[0].speed == pytest.approx(1.2 / 0.08)
    assert s[0].continuous == pytest.approx(1.25 * math.sqrt(static ** 2 + 2 / 3 * accel ** 2), rel=1e-6)
    assert s[0].peak == pytest.approx(1.5 * (static + accel), rel=1e-6)
    assert any(d.type == 'wheel_gearbox' for d in sz.drive_options('mobile_base', 0, s[0]))


async def test_mobile_manipulator_is_not_in_v1(v1):
    v = v1.model_copy(update={'robot': RobotConcept(family='mobile_manipulator', name='m', params={})})
    with pytest.raises(ValueError, match='not in v1'):
        sz.size_joints(v)


async def test_iterate_sizing_converges_and_feeds_masses_back(v1):
    from studio.robot import sourcing
    calls = []

    def choose(sizing):
        calls.append(sizing)
        return sourcing.select(sizing, {}, 'serial_arm', {})

    base = sz.size_joints(v1)
    sizing, choices = sz.iterate_sizing(v1, choose, rounds=3)
    assert 2 <= len(calls) <= 3 and [c.joint for c in choices] == [s.joint for s in sizing]
    assert sz._signature(choices) == sz._signature(sourcing.select(sizing, {}, 'serial_arm', {}))   # stable
    assert by_joint(sizing)['j2'].static_torque > by_joint(base)['j2'].static_torque    # motor masses added
    assert sz.motor_mass(choices[0]) > 0 and sz.drive_mass(choices[1].drive, sizing[1]) > 0


def stepper_page(frame='23', **over):
    """Holding torque + current + voltage, no speed (like a Pololu product page); frame as a verified value."""
    vals = motor_values(rated_speed_rpm=None, **over)
    return vals + [SpecValue(key='frame', text=f'NEMA {frame}', status='verified', quote=f'NEMA {frame}')]


def test_stepper_without_speed_uses_the_assumed_usable_speed():
    d = Drive(type='planetary', ratio=10.0, efficiency=0.9)
    rpm = sz.STEPPER_USABLE_RPM['23']
    assert rpm == 300.0 and sz.STEPPER_USABLE_RPM['34'] == 200.0 and sz.STEPPER_USABLE_RPM_UNKNOWN == 200.0
    ok_speed = 0.8 * rpm * 2 * math.pi / 60 / 10                      # joint speed at the 0.8 limit
    a = alt(stepper_page())
    ok, margins, checks, reason = sz.evaluate(a, joint(speed=ok_speed), d, 'stepper')
    assert ok and 'speed assumed (conservative)' in reason
    assert margins['speed'] == pytest.approx(1.0)
    speed_check = next(c for c in checks if c.requirement_id.endswith('_speed'))
    assert 'assumed' in speed_check.detail
    val = next(v for v in a.values if v.key == 'rated_speed_rpm')
    assert val.status == 'assumed' and val.value == 300.0
    assert val.note == 'conservative stepper usable speed at 24 V; the page states no rated speed'
    # above 300 rpm motor-side the speed check fails with a speed reason
    ok, margins, checks, reason = sz.evaluate(alt(stepper_page()), joint(speed=ok_speed * 1.05), d, 'stepper')
    assert not ok and 'Speed' in reason and 'speed assumed' in reason


def test_stepper_assumed_speed_by_frame_and_without_current():
    d = Drive(type='planetary', ratio=10.0, efficiency=0.9)
    assert sz.stepper_assumed_speed(stepper_page('34')).value == 200.0
    assert sz.stepper_assumed_speed(stepper_page('14')).value == 300.0
    assert sz.stepper_assumed_speed(motor_values(rated_speed_rpm=None)).value == 200.0        # unknown frame
    by_flange = motor_values(rated_speed_rpm=None) + [SpecValue(key='flange_mm', value=86.3, status='assumed')]
    assert sz.stepper_assumed_speed(by_flange).value == 200.0                                  # NEMA 34 flange
    ok, _, checks, reason = sz.evaluate(alt(stepper_page(current_a=None)), joint(), d, 'stepper')
    assert not ok and 'current' in reason and checks[0].passed is False             # current must still be verified


def test_non_stepper_without_speed_still_fails():
    d = Drive(type='planetary', ratio=10.0, efficiency=0.9)
    vals = motor_values(rated_speed_rpm=None, rated_torque_nm=3.0)
    ok, _, checks, reason = sz.evaluate(alt(vals, 'bldc'), joint(), d, 'bldc')
    assert not ok and 'speed' in reason and checks[0].passed is False
