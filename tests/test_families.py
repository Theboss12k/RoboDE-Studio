"""Robot families: IK round trips, limits, URDF, statics, sizing and export."""
import math

import numpy as np
import pytest
from fixtures_specs import bottle_spec, mobile_delivery_spec, pallet_spec

from studio import families, physics
from studio.families import export
from studio.families.ik import tool_down_error
from studio.families.params import GantryParams, MobileManipulatorParams, SerialArmParams
from studio.ingest.urdf import parse_urdf
from studio.physics.statics import G

RNG = np.random.default_rng(42)


def tool_down_q(p, fam, rng):
    """Random arm configuration with the tool pointing down (q4 = pi - q2 - q3) inside the limits."""
    while True:
        q = np.zeros(p.dof)
        q[:3] = [rng.uniform(-2.5, 2.5), rng.uniform(-0.2, 1.4), rng.uniform(-1.4, 1.8)]
        q[3] = math.pi - q[1] - q[2]
        lo = np.array([j.lower for j in fam.joints(p)])
        hi = np.array([j.upper for j in fam.joints(p)])
        if np.all(q >= lo) and np.all(q <= hi):
            return q


ARMS = [SerialArmParams(dof=d) for d in (4, 5, 6)]


@pytest.mark.parametrize('p', ARMS, ids=lambda p: f'dof{p.dof}')
def test_serial_arm_ik_round_trip_tool_down(p):
    fam = families.get('serial_arm')
    lo = np.array([j.lower for j in fam.joints(p)])
    hi = np.array([j.upper for j in fam.joints(p)])
    for _ in range(25):
        x = fam.fk(p, tool_down_q(p, fam, RNG))[:3, 3]
        r = fam.ik(p, x)
        T = fam.fk(p, r.q)
        assert r.ok and np.linalg.norm(T[:3, 3] - x) < 1e-3
        assert tool_down_error(T) < 0.02
        assert np.all(r.q >= lo - 1e-9) and np.all(r.q <= hi + 1e-9)


def test_serial_arm_unreachable_is_reported():
    fam = families.get('serial_arm')
    p = SerialArmParams()
    r = fam.ik(p, np.array([3.0, 0.0, 0.5]))
    assert not r.ok and r.error_m > 1.0


def test_gantry_ik_round_trip_and_limits():
    fam = families.get('gantry')
    for p in (GantryParams(), GantryParams(tool_yaw=True)):
        js = fam.joints(p)
        for _ in range(25):
            q = np.array([RNG.uniform(j.lower, j.upper) for j in js])
            x = fam.fk(p, q)[:3, 3]
            r = fam.ik(p, x)
            assert r.ok and np.linalg.norm(fam.fk(p, r.q)[:3, 3] - x) < 1e-3
            assert all(j.lower - 1e-9 <= v <= j.upper + 1e-9 for j, v in zip(js, r.q))
            assert tool_down_error(fam.fk(p, r.q)) < 1e-6
        assert not fam.ik(p, np.array([p.x_stroke_m, 0, 0.5])).ok     # outside the strokes


def test_mobile_base_and_manipulator_ik():
    mb = families.get('mobile_base')
    p = mb.parse({})
    x = mb.fk(p, np.zeros(0))[:3, 3]
    assert mb.ik(p, x).ok and not mb.ik(p, x + [5, 0, 0]).ok
    mm = families.get('mobile_manipulator')
    p = MobileManipulatorParams()
    for _ in range(15):
        q = tool_down_q(p.arm, families.get('serial_arm'), RNG)
        x = mm.fk(p, q)[:3, 3]
        r = mm.ik(p, x)
        assert r.ok and np.linalg.norm(mm.fk(p, r.q)[:3, 3] - x) < 1e-3
        assert tool_down_error(mm.fk(p, r.q)) < 0.02
    assert mm.mobile and mb.mobile


@pytest.mark.parametrize('name', list(families.REGISTRY))
def test_urdf_reparses_and_matches_joints(name):
    fam = families.get(name)
    p = fam.parse({})
    xml = fam.build_urdf(p, 'test_robot')
    parsed = parse_urdf(xml.encode())
    moving = {j['name'] for j in parsed['joints'] if j['type'] != 'fixed'}
    assert moving == {j.name for j in fam.joints(p)}
    assert parsed['base_link'] == 'base_link' and 'tcp' in parsed['links']
    assert not parsed['unsupported']


@pytest.mark.parametrize('name', list(families.REGISTRY))
def test_export_glb_and_stl(name):
    xml = families.get(name).build_urdf(families.get(name).parse({}), 'x')
    glb = export.meshes(xml)
    assert len(glb) > 500 and glb[:4] == b'glTF'
    assert len(export.stl(xml)) > 500


def test_registry_and_describe():
    assert set(families.REGISTRY) == {'serial_arm', 'gantry', 'mobile_base', 'mobile_manipulator'}
    d = {x['name']: x for x in families.describe()}
    assert d['mobile_manipulator']['mobile'] and not d['gantry']['mobile']
    assert 'properties' in d['serial_arm']['params_schema']
    with pytest.raises(KeyError):
        families.get('nope')


def test_gravity_torques_stretched_arm_hand_calculation():
    # Arm stretched horizontally: q = [0, 90 deg, 0, 0]. h = 0.2, L1 = 0.4, L2 = 0.3, wrist = 0.1, tool = 0.1,
    # masses 3 / 2 / 1 / 0.5 kg and a 2 kg payload at the tool centre point.
    # Lever arms (m) from J2:  upper arm centre 0.2, forearm centre 0.4 + 0.15 = 0.55, wrist centre 0.7 + 0.05 = 0.75,
    # tool centre 0.8 + 0.05 = 0.85, payload 0.9.
    #   tau2 = -g (3*0.2 + 2*0.55 + 1*0.75 + 0.5*0.85 + 2*0.9) = -g * 4.675
    # From J3 (0.4): forearm 0.15, wrist 0.35, tool 0.45, payload 0.5:
    #   tau3 = -g (2*0.15 + 1*0.35 + 0.5*0.45 + 2*0.5) = -g * 1.875
    # From J4 (0.7): wrist 0.05, tool 0.15, payload 0.2:
    #   tau4 = -g (1*0.05 + 0.5*0.15 + 2*0.2) = -g * 0.525;   tau1 = 0 (vertical axis).
    # The sign is negative because gravity pulls the positive (forward/down) direction.
    p = SerialArmParams(dof=4, base_height_m=0.2, upper_arm_m=0.4, forearm_m=0.3, wrist_m=0.1, tool_length_m=0.1,
                        upper_arm_mass_kg=3, forearm_mass_kg=2, wrist_mass_kg=1, tool_mass_kg=0.5)
    tau = families.get('serial_arm').gravity_torques(p, np.array([0, math.pi / 2, 0, 0]), 2.0)
    np.testing.assert_allclose(tau, [0, -G * 4.675, -G * 1.875, -G * 0.525], atol=1e-4)


def test_gantry_gravity_holds_the_z_axis():
    p = GantryParams(z_moving_mass_kg=3, tool_mass_kg=0.5)
    tau = families.get('gantry').gravity_torques(p, np.zeros(3), 2.0)
    np.testing.assert_allclose(tau, [0, 0, -(3 + 0.5 + 2.0) * G], atol=1e-9)


COMPARE = dict(payload='payload_kg', grip_force='gripper_force_n', tcp_speed='max_tcp_speed_m_s',
               tcp_accel='max_tcp_accel_m_s2', accuracy='repeatability_m')
EXTRA = dict(drive_speed='max_speed_m_s', drive_accel='max_accel_m_s2', wheel_torque='wheel_torque_nm',
             slope='max_slope_deg', battery='battery_wh')


@pytest.mark.parametrize('name,spec_fn', [('serial_arm', bottle_spec), ('gantry', pallet_spec),
                                          ('mobile_base', mobile_delivery_spec),
                                          ('mobile_manipulator', mobile_delivery_spec)])
def test_size_from_physics_meets_requirements(name, spec_fn):
    spec = spec_fn()
    reqs = physics.derive(spec, physics.place_base(spec))
    fam = families.get(name)
    caps = fam.capabilities(fam.size_from_physics(reqs, spec, {}))
    for r in reqs:
        if r.comparator == 'info':
            continue
        actual = None
        if r.id in COMPARE and name not in ('mobile_base',):
            actual = getattr(caps, COMPARE[r.id])
        elif r.id == 'payload':
            actual = caps.payload_kg
        elif r.id in EXTRA and name.startswith('mobile'):
            actual = caps.extra[EXTRA[r.id]]
        elif r.id == 'reach_max' and name != 'mobile_base':
            actual = caps.reach_max_m * 0.85 if name == 'serial_arm' else caps.reach_max_m
        if actual is None:
            continue
        ok = actual >= r.target - 1e-9 if r.comparator == '>=' else actual <= r.target + 1e-9
        assert ok, f'{name}: {r.id} needs {r.comparator} {r.target}, got {actual}'


def test_size_from_physics_arm_torques_from_statics():
    spec = bottle_spec()
    reqs = physics.derive(spec, physics.place_base(spec))
    fam = families.get('serial_arm')
    p = fam.size_from_physics(reqs, spec, {})
    tau = np.abs(fam.gravity_torques(p, np.array([0, math.pi / 2] + [0] * (p.dof - 2)), p.payload_kg))
    assert p.max_torque_nm[1] >= tau[1] * spec.physics_inputs.safety_factor - 1e-9
    assert p.max_torque_nm[1] == math.ceil(tau[1] * spec.physics_inputs.safety_factor)
