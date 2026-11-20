"""Task simulation: frames, checks, events."""
import numpy as np
import pytest
from fixtures_specs import bottle_spec, mobile_delivery_spec, pallet_spec

from studio import families, physics, sim
from studio.ingest.urdf import parse_urdf
from studio.schemas import RobotConcept, SceneBox, SceneSpec


def run(spec, family, params=None, boxes=(), hint=None):
    base = physics.place_base(spec)
    reqs = physics.derive(spec, base)
    fam = families.get(family)
    p = fam.parse(params) if params is not None else fam.size_from_physics(reqs, spec, hint or {})
    concept = RobotConcept(family=family, name='test', params=p.model_dump(), base=base)
    scene = SceneSpec(boxes=list(boxes), robot_base=base)
    return sim.simulate(spec, reqs, scene, concept), concept, fam.build_urdf(p, 'test')


def assert_playable(res, urdf):
    t = [f.t for f in res.frames]
    assert t[0] == 0 and all(b > a for a, b in zip(t, t[1:])) and len(t) <= 1500
    names = {j['name'] for j in parse_urdf(urdf.encode())['joints'] if j['type'] != 'fixed'}
    assert all(set(f.joints) == names for f in res.frames)
    assert all(f.tcp is not None for f in res.frames)


def table_boxes():
    return [SceneBox(id=f'table_{n}', kind='table', center=(0.5, y, 0.7), size=(0.3, 0.3, 0.1))
            for n, y in (('a', 0.3), ('b', -0.3))]


def test_bottle_with_serial_arm_passes():
    res, _, urdf = run(bottle_spec(), 'serial_arm', boxes=table_boxes())
    assert res.ok, [(c.requirement_id, c.detail) for c in res.checks if c.passed is False]
    assert all(c.passed is not False for c in res.checks)
    assert not [e for e in res.events if e.kind in ('collision', 'unreachable')]
    assert_playable(res, urdf)
    held = [i for i, f in enumerate(res.frames) if f.holding == 'bottle']
    assert held and held == list(range(held[0], held[-1] + 1))          # one contiguous carrying stretch
    assert res.frames[0].holding == '' and res.frames[-1].holding == ''
    assert res.frames[held[0] - 1].phase and res.duration_s == pytest.approx(res.frames[-1].t)
    assert any(c.requirement_id.startswith('joint_torque_') for c in res.checks)
    assert res.frames[0].base is None


def test_short_arm_fails_reach_and_reports_unreachable():
    res, _, _ = run(bottle_spec(), 'serial_arm', params=dict(upper_arm_m=0.1, forearm_m=0.1))
    reach = next(c for c in res.checks if c.requirement_id == 'reach_max')
    assert reach.passed is False and not res.ok
    assert any(e.kind == 'unreachable' for e in res.events)


def test_scene_box_in_the_way_gives_collision_event():
    clear, concept, _ = run(bottle_spec(), 'serial_arm')
    mid = clear.frames[len(clear.frames) // 2 + 4].tcp              # the tool passes here during the carry
    wall = SceneBox(id='wall', kind='obstacle', center=mid, size=(0.2, 0.2, 0.2))
    res, _, _ = run(bottle_spec(), 'serial_arm', params=concept.params, boxes=[wall])
    assert any(e.kind == 'collision' and 'wall' in e.text for e in res.events)
    assert next(c for c in res.checks if c.requirement_id == 'collision_free').passed is False
    assert not res.ok


def test_pallet_with_gantry_passes():
    res, _, urdf = run(pallet_spec(), 'gantry')
    assert res.ok, [(c.requirement_id, c.detail) for c in res.checks if c.passed is False]
    assert_playable(res, urdf)
    xs = [f.joints['x_axis'] for f in res.frames]
    assert min(xs) < -0.5 < 0.5 < max(xs) or max(abs(x) for x in xs) > 0.5


def test_mobile_delivery_with_mobile_base_follows_the_path():
    res, _, urdf = run(mobile_delivery_spec(), 'mobile_base')
    assert res.ok, [(c.requirement_id, c.detail) for c in res.checks if c.passed is False]
    assert_playable(res, urdf)
    bases = np.array([f.base for f in res.frames])
    assert bases[0] == pytest.approx([0, 0, 0], abs=1e-9)
    assert bases[-1][:2] == pytest.approx([12, 0], abs=1e-6)
    assert np.all(np.diff(bases[:, 0]) >= -1e-9) and np.abs(bases[:, 1]).max() < 1e-6
    assert res.frames[-1].joints['wheel_left'] > 100                      # wheels turned about 12 m / r
    ids = {c.requirement_id: c for c in res.checks}
    for k in ('drive_speed', 'drive_accel', 'wheel_torque', 'slope', 'battery', 'path_follow'):
        assert ids[k].passed is True, k
    held = [f.holding for f in res.frames]
    assert 'box' in held and held[-1] == ''
    assert res.frames[-1].tcp[0] == pytest.approx(12, abs=1e-6)


def test_mobile_manipulator_drives_and_works_with_the_arm():
    res, _, urdf = run(mobile_delivery_spec(), 'mobile_manipulator')
    assert res.ok, [(c.requirement_id, c.detail) for c in res.checks if c.passed is False]
    assert_playable(res, urdf)
    assert res.frames[-1].base[0] == pytest.approx(12, abs=1e-6)
    assert any(abs(f.joints['j2'] - res.frames[0].joints['j2']) > 0.01 for f in res.frames)


def test_waypoints_follow_the_contract():
    spec = bottle_spec()
    wps = sim.waypoints(spec, SceneSpec(), physics.place_base(spec))
    kinds = [w.kind for w in wps]
    assert kinds == ['approach', 'grasp', 'lift', 'carry', 'approach', 'place', 'release', 'retreat']
    assert wps[1].xyz == pytest.approx((0.5, 0.3, 0.75 + 0.11))          # parallel grip: object centre
    assert wps[0].xyz[2] == pytest.approx(0.75 + 0.11 + 0.10)
    vac = sim.waypoints(pallet_spec(), SceneSpec(), physics.place_base(pallet_spec()))
    assert vac[1].xyz[2] == pytest.approx(0.5 + 0.3)                      # vacuum: top of the box
