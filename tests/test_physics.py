"""Physics tests against hand-calculated values (arithmetic in comments)."""
import math

import numpy as np
import pytest
from fixtures_specs import bottle_spec, mobile_delivery_spec, pallet_spec

from studio.physics import derive, grip, locomotion, motion, place_base, statics
from studio.physics.requirements import _enclosing_circle
from studio.schemas import BasePose

G = 9.80665


def by_id(reqs):
    return {r.id: r for r in reqs}


def test_grip_force_parallel():
    # F = 0.3 (9.80665 + 2) / (2 * 0.4) * 1.5 = 0.3 * 14.7583125 * 1.5 = 6.641240625 N
    c = grip.force('parallel', 0.3, 2.0, 2, 0.4, 1.5)
    assert c.result == pytest.approx(0.3 * (G + 2) / (2 * 0.4) * 1.5)
    assert c.result == pytest.approx(6.641240625)
    assert c.unit == 'N' and {'m', 'a', 'n', 'mu', 'SF'} <= set(c.inputs)


def test_grip_force_vacuum_and_none():
    # vacuum: 20 (9.80665 + 1) * 1.5 = 20 * 10.80665 * 1.5 = 324.1995 N
    assert grip.force('vacuum', 20, 1.0, 1, 0.4, 1.5).result == pytest.approx(324.1995)
    # fork: no clamping, supported load 2 (9.80665 + 0) = 19.6133 N
    assert grip.force('fork', 2, 0.0).result == pytest.approx(19.6133)


def test_trapezoid_known_move():
    # v^2/a = 0.25 <= 1 m so trapezoid: t_a = 0.5/1 = 0.5 s, t = 1/0.5 + 0.5 = 2.5 s
    p = motion.trapezoid(1.0, 0.5, 1.0)
    assert p['t_total'] == pytest.approx(2.5) and p['t_accel'] == pytest.approx(0.5)
    assert p['v_peak'] == 0.5 and not p['triangular']


def test_trapezoid_triangular():
    # v^2/a = 1 > 0.2 m so triangular: v_peak = sqrt(0.2 * 1) = 0.44721, t = 2 * 0.44721 = 0.89443 s
    p = motion.trapezoid(0.2, 1.0, 1.0)
    assert p['triangular'] and p['v_peak'] == pytest.approx(math.sqrt(0.2))
    assert p['t_total'] == pytest.approx(2 * math.sqrt(0.2))


def test_required_for():
    # d = 1.2, T = 3, t_a = 1 s: v = 1.2 / (3 - 1) = 0.6 m/s, a = 0.6 / 1 = 0.6 m/s^2
    r = motion.required_for(1.2, 3.0)
    assert r['v_peak'] == pytest.approx(0.6) and r['a'] == pytest.approx(0.6)
    # and the trapezoid with those limits takes T = 1.2/0.6 + 0.6/0.6 = 3 s
    assert motion.trapezoid(1.2, r['v_peak'], r['a'])['t_total'] == pytest.approx(3.0)


@pytest.mark.parametrize('args', [(1.0, 0.5, 1.0, 0.04), (0.2, 1.0, 1.0, 0.04), (0.5, 2.0, 3.0, 0.1)])
def test_sample_monotonic_and_exact_end(args):
    s = motion.sample(*args)
    assert s[0] == 0.0 and s[-1] == args[0]
    assert np.all(np.diff(s) >= 0)
    # sample count = ceil(T / dt) + 1 at most
    T = motion.trapezoid(*args[:3])['t_total']
    assert len(s) in (math.ceil(T / args[3]) + 1, math.ceil(T / args[3]) + 2)


def test_wheel_torque_slope():
    # m = 35, a = 0.5, 5 deg: g sin5 = 9.80665 * 0.0871557 = 0.854714; g Crr cos5 = 9.80665 * 0.015 * 0.996195 = 0.146543
    # F = 35 (0.5 + 0.854714 + 0.146543) = 35 * 1.501257 = 52.5440 N
    # tau = 52.5440 * 0.08 / 2 * 1.5 = 3.15264 N*m
    c = locomotion.wheel_torque(35, 0.5, 5.0, 0.015, 0.08, 2, 1.5)
    assert c.result == pytest.approx(3.1526, rel=1e-4)
    assert c.unit == 'N*m'


def test_battery():
    # m=35, d=12, v=1, flat, Crr=0.015: rolling = 35*9.80665*0.015 = 5.14849 N -> 61.782 J; KE = 17.5 J
    # E = (61.782 + 17.5) / 0.7 * 1.15 = 130.25 J = 0.036180 Wh per cycle; 10 cycles -> 0.36180 Wh
    c = locomotion.battery(35, 12, 1.0, 0.0, 0.015, 10)
    assert c.result == pytest.approx(0.3618, rel=1e-3)


def test_vehicle_mass_estimate_documented():
    # 25 + 1.0 * 5 = 30 kg; total 35 kg
    assert locomotion.estimate_vehicle_mass(5) == 30.0
    r = by_id(derive(mobile_delivery_spec(), place_base(mobile_delivery_spec())))
    assert 'estimated' in r['wheel_torque'].calc.note and r['wheel_torque'].calc.inputs['m_total'].value == 35
    assert r['wheel_torque'].calc.inputs['r'].value == 0.08


def test_two_link_static_torque():
    l1, l2 = 0.4, 0.3

    def elbow(q):
        return np.array([l1 * math.cos(q[0]), 0.0, l1 * math.sin(q[0])])

    def tip(q):
        return np.array([l1 * math.cos(q[0]) + l2 * math.cos(q[0] + q[1]), 0.0,
                         l1 * math.sin(q[0]) + l2 * math.sin(q[0] + q[1])])

    q = np.zeros(2)
    tau = statics.gravity_torques([statics.numeric_jacobian(elbow, q), statics.numeric_jacobian(tip, q)], [2.0, 1.0])
    # tau1 = (2 * 0.4 + 1 * 0.7) g = 1.5 * 9.80665 = 14.709975 ; tau2 = 1 * 0.3 * g = 2.941995
    assert tau[0] == pytest.approx(1.5 * G, rel=1e-6)
    assert tau[1] == pytest.approx(0.3 * G, rel=1e-6)


def test_enclosing_circle_two_and_three_points():
    # two points (0,0),(4,0): centre (2,0), r 2
    c, r = _enclosing_circle([(0, 0), (4, 0)])
    assert np.allclose(c, [2, 0]) and r == pytest.approx(2)
    # right triangle (0,0),(4,0),(0,3): hypotenuse is the diameter -> centre (2,1.5), r 2.5
    c, r = _enclosing_circle([(0, 0), (4, 0), (0, 3)])
    assert np.allclose(c, [2, 1.5]) and r == pytest.approx(2.5)
    # acute triangle (0,0),(2,0),(1,1.5) is circumscribed: centre (1, y), y^2 + 1 = (1.5 - y)^2 -> 1 = 2.25 - 3y -> y = 0.416667, r = 1.5 - y = 1.083333
    c, r = _enclosing_circle([(0, 0), (2, 0), (1, 1.5)])
    assert np.allclose(c, [1, 5 / 12]) and r == pytest.approx(13 / 12)
    # an interior point does not change the circle
    c, r = _enclosing_circle([(0, 0), (4, 0), (2, 0.5)])
    assert np.allclose(c, [2, 0]) and r == pytest.approx(2)


def test_place_base_fixed():
    b = place_base(bottle_spec())
    # circle of (0.5, 0.3),(0.5,-0.3): centre (0.5, 0); first pick at A is at +y -> yaw = atan2(0.3, 0) = pi/2
    assert b.position == pytest.approx((0.5, 0.0, 0.0))
    assert b.yaw == pytest.approx(math.pi / 2)


def test_place_base_mobile():
    b = place_base(mobile_delivery_spec())
    assert b.position == (0.0, 0.0, 0.0) and b.yaw == pytest.approx(0.0)


def test_derive_bottle():
    spec = bottle_spec()
    reqs = derive(spec, place_base(spec))
    r = by_id(reqs)
    assert [x.id for x in reqs] == ['reach_max', 'reach_min', 'z_max', 'z_min', 'payload', 'tcp_speed', 'tcp_accel',
                                    'grip_force', 'cycle_time', 'accuracy']
    # base (0.5, 0, 0); marker (0.5, 0.3, 0.75): raised 0.85 -> sqrt(0.3^2 + 0.85^2) = sqrt(0.8125) = 0.901388
    # reach_max = 0.901388 + 0.05 = 0.951388
    assert r['reach_max'].calc.result == pytest.approx(math.sqrt(0.8125) + 0.05)
    assert r['reach_max'].target == pytest.approx(0.951, abs=1e-9)
    assert r['reach_max'].comparator == '>='
    # reach_min = sqrt(0.3^2 + 0.75^2) = 0.807775
    assert r['reach_min'].calc.result == pytest.approx(math.sqrt(0.6525))
    # z_max = 0.75 + 0.10 = 0.85, z_min = 0.75
    assert r['z_max'].target == pytest.approx(0.85) and r['z_min'].target == pytest.approx(0.75)
    # tool = 0.3 + 0.15 * 0.3 = 0.345 ; payload = (0.3 + 0.345) * 1.5 = 0.9675
    assert r['payload'].calc.result == pytest.approx(0.9675)
    # moves: A->B 0.6, B->A 0.6, four approach moves of 0.1 -> sum 1.6, T_move = 6 * 0.6 / 1.6 = 2.25 s
    # a given = 2 (>= 4 * 0.6 / 2.25^2 = 0.474), v = (a T - sqrt(a^2 T^2 - 4 a d)) / 2
    #   = (4.5 - sqrt(20.25 - 4.8)) / 2 = (4.5 - 3.930649) / 2 = 0.284676 m/s
    assert r['tcp_accel'].calc.result == pytest.approx(2.0)
    assert r['tcp_speed'].calc.result == pytest.approx((4.5 - math.sqrt(15.45)) / 2)
    # grip: 0.3 (9.80665 + 2) / (2 * 0.4) * 1.5 = 6.641240625 N
    assert r['grip_force'].calc.result == pytest.approx(0.3 * (G + 2) / 0.8 * 1.5)
    assert r['grip_force'].target == pytest.approx(6.64, abs=1e-9)
    assert r['cycle_time'].comparator == '<=' and r['cycle_time'].target == 6.0
    # accuracy 0.005 / 2 = 0.0025
    assert r['accuracy'].target == pytest.approx(0.0025) and r['accuracy'].comparator == '<='
    for x in reqs:
        assert x.calc.formula and x.calc.inputs, x.id


def test_tcp_from_cycle_time_without_peak_accel():
    spec = bottle_spec()
    spec.physics_inputs.peak_acceleration_m_s2 = None
    r = by_id(derive(spec, place_base(spec)))
    # T_move = 2.25 s, t_a = 0.75 s: v = 0.6 / (2.25 - 0.75) = 0.4 m/s, a = 0.4 / 0.75 = 0.53333 m/s^2
    assert r['tcp_speed'].calc.result == pytest.approx(0.4)
    assert r['tcp_accel'].calc.result == pytest.approx(0.4 / 0.75)
    # grip then uses a = 0.53333: 0.3 (9.80665 + 0.53333) / 0.8 * 1.5
    assert r['grip_force'].calc.result == pytest.approx(0.3 * (G + 0.4 / 0.75) / 0.8 * 1.5)


def test_derive_pallet_vacuum():
    spec = pallet_spec()
    base = place_base(spec)
    assert base.position == pytest.approx((0.0, 0.0, 0.0))
    r = by_id(derive(spec, base))
    # tool = 0.3 + 0.15 * 20 = 3.3 (cap only for <= 10 kg); payload = 23.3 * 1.5 = 34.95
    assert r['payload'].calc.result == pytest.approx(34.95)
    # reach: raised point (0.6, 0, 0.65) -> sqrt(0.36 + 0.4225) = 0.884590, + 0.05
    assert r['reach_max'].calc.result == pytest.approx(math.sqrt(0.7825) + 0.05)
    # vacuum: m (g + a) SF with a from tcp_accel
    a = r['tcp_accel'].calc.result
    assert r['grip_force'].calc.result == pytest.approx(20 * (G + a) * 1.5)
    assert 'travel_distance' not in r


def test_derive_mobile_delivery():
    spec = mobile_delivery_spec()
    r = by_id(derive(spec, place_base(spec)))
    for id_ in ('travel_distance', 'drive_speed', 'drive_accel', 'wheel_torque', 'slope', 'battery'):
        assert id_ in r, id_
    # path 6 + 6 = 12 m; drive time = 0.6 * 60 = 36 s (no dwell); v = 12 / (36 * 2/3) = 0.5 m/s; a = 0.5 / 12 = 0.041667
    assert r['travel_distance'].calc.result == pytest.approx(12.0)
    assert r['drive_speed'].calc.result == pytest.approx(0.5)
    assert r['drive_accel'].calc.result == pytest.approx(0.5 / 12)
    assert r['slope'].target == 3.0 and r['slope'].unit == 'deg'
    # reach from the nearest stop: marker A (0.5, 0, 0.8) raised 0.9 from stop (0,0,0): sqrt(0.25 + 0.81) = 1.029563
    assert r['reach_max'].calc.result == pytest.approx(math.sqrt(1.06) + 0.05)
    # wheel torque: 35 kg total, a = 0.041667, 3 deg
    th = math.radians(3)
    tau = 35 * (0.5 / 12 + G * math.sin(th) + G * 0.015 * math.cos(th)) * 0.08 / 2 * 1.5
    assert r['wheel_torque'].calc.result == pytest.approx(tau)
    assert r['battery'].unit == 'Wh' and r['battery'].calc.result > 0


def test_base_pose_type():
    assert isinstance(place_base(bottle_spec()), BasePose)
