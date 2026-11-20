"""Digital twin (robot studio): limits, stepping, IK moves, task replay. Kinematic + static(+acceleration) code only."""
import math
from types import SimpleNamespace

import numpy as np
import pytest
from fixtures_specs import bottle_spec, mobile_delivery_spec, pallet_spec

from studio import families, physics, sim
from studio.robot import twin
from studio.schemas import (DesignVersion, MotorChoice, RobotConcept, SceneBox, SceneSpec, StudioBuild, TwinMoveRequest,
                            TwinTarget)


def make(spec, family, boxes=()):
    base = physics.place_base(spec)
    reqs = physics.derive(spec, base)
    fam = families.get(family)
    p = fam.size_from_physics(reqs, spec, {})
    concept = RobotConcept(family=family, name='t', params=p.model_dump(), base=base)
    scene = SceneSpec(boxes=list(boxes), robot_base=base)
    res = sim.simulate(spec, reqs, scene, concept)
    ver = DesignVersion.model_construct(version=1, spec=spec, requirements=reqs, scene=scene, robot=concept, sim=res)
    return fam, p, ver


@pytest.fixture(scope='module')
def arm():
    fam, p, ver = make(bottle_spec(), 'serial_arm')
    return fam, p, ver, StudioBuild.model_construct(twin_limits=twin.twin_limits(ver, [], {'stroke_m': 0.08}))


@pytest.fixture(scope='module')
def gantry():
    fam, p, ver = make(pallet_spec(), 'gantry')
    return fam, p, ver, StudioBuild.model_construct(twin_limits=twin.twin_limits(ver, [], None))


def q0_of(fam, p, **extra):
    d = {j.name: 0.0 for j in fam.joints(p)}
    d.update(extra)
    return d


def world_of(ver, fam, p, q):
    """World TCP of a joint vector (through the design's base pose)."""
    b = ver.robot.base
    t = fam.fk(p, np.array(q))[:3, 3]
    c, s = math.cos(b.yaw), math.sin(b.yaw)
    return (b.position[0] + c * t[0] - s * t[1], b.position[1] + s * t[0] + c * t[1], b.position[2] + t[2])


def with_limits(build, **per_joint):
    """Copy of the build whose limits are changed: per_joint[name] = dict of TwinJointLimit fields."""
    lim = build.twin_limits
    joints = [j.model_copy(update=per_joint.get(j.joint, {})) for j in lim.joints]
    return StudioBuild.model_construct(twin_limits=lim.model_copy(update={'joints': joints}))


def test_chain_matches_family_kinematics_and_gravity(arm, gantry):
    rng = np.random.default_rng(3)
    for fam, p, ver, _ in (arm, gantry):
        chain = twin.chain_for(ver.robot.family, p)
        js = fam.joints(p)
        lo, hi = [j.lower for j in js], [j.upper for j in js]
        for _ in range(5):
            q = rng.uniform(lo, hi)
            assert np.allclose(twin.chain_fk(chain, q), fam.fk(p, q), atol=1e-9)
            g, _ = twin.gravity_inertia(chain, q, 0.3)
            if ver.robot.family == 'serial_arm':
                assert np.allclose(g, fam.gravity_torques(p, q, 0.3), atol=1e-6)
            else:
                assert g[2] == pytest.approx(fam.gravity_torques(p, q, 0.3)[2], abs=1e-6)


def test_twin_limits_use_motors_and_gripper(arm):
    fam, p, ver, build = arm
    motor = MotorChoice.model_construct(joint='j2', provided={'peak': 3.0, 'speed': 0.5, 'continuous': 1.0})
    lim = twin.twin_limits(ver, [motor], {'stroke_m': 0.1, 'speed_m_s': 0.05})
    j2 = next(j for j in lim.joints if j.joint == 'j2')
    assert (j2.max_speed, j2.max_torque) == (0.5, 3.0) and j2.max_accel <= 0.5 / twin.ACCEL_RAMP_S + 1e-9
    assert lim.gripper.upper == 0.1 and lim.gripper.max_speed == 0.05
    j1 = next(j for j in lim.joints if j.joint == 'j1')
    assert j1.max_speed == pytest.approx(fam.joints(p)[0].max_velocity)


def test_clamp_step_ramps_clamps_and_stops_on_target():
    L = [SimpleNamespace(lower=-1.0, upper=1.0, max_speed=1.0, max_accel=2.0)]
    q, qd = np.zeros(1), np.zeros(1)
    seen = set()
    for _ in range(400):
        q, qd, hits = twin.clamp_step(q, [0.5], 0.01, L, qd)
        seen |= {k for _, k in hits}
        assert abs(qd[0]) <= 1.0 + 1e-9
    assert q[0] == pytest.approx(0.5, abs=1e-12) and qd[0] == 0 and not seen
    q2, _, hits = twin.clamp_step(np.zeros(1), [3.0], 0.01, L, np.zeros(1))
    assert (0, 'position') in hits


def test_joint_move_reaches_target_deterministically(arm):
    fam, p, ver, build = arm
    req = TwinMoveRequest(q0=q0_of(fam, p, gripper=0.08), target=TwinTarget(joints={'j1': 0.8, 'j2': -0.4}, gripper=0.03))
    a, b = twin.move(build, ver, req), twin.move(build, ver, req)
    assert a.model_dump() == b.model_dump()
    assert a.reached and not a.limits and 2 <= len(a.frames) <= 1500
    last = a.frames[-1]
    assert last.joints['j1'] == pytest.approx(0.8) and last.joints['j2'] == pytest.approx(-0.4)
    assert last.joints['gripper'] == pytest.approx(0.03) and last.tcp is not None
    ts = [f.t for f in a.frames]
    assert ts[0] == 0 and all(y > x for x, y in zip(ts, ts[1:])) and a.duration_s == pytest.approx(ts[-1])
    assert all(set(f.joints) == set(a.frames[0].joints) for f in a.frames)
    # the joints finish together (time-scaled), not one after the other
    assert a.torque_use['j2'] > 0


def test_position_limit_reported_and_not_reached(arm):
    fam, p, ver, build = arm
    res = twin.move(build, ver, TwinMoveRequest(q0=q0_of(fam, p), target=TwinTarget(joints={'j2': 5.0})))
    assert not res.reached and any(h.limit == 'position' and h.joint == 'j2' for h in res.limits)
    assert max(f.joints['j2'] for f in res.frames) <= build.twin_limits.joints[1].upper + 1e-9


def test_cartesian_target_in_world_frame_tool_down(arm):
    fam, p, ver, build = arm
    q = [0.5, 0.7, 0.9, math.pi - 1.6, 0.0, 0.0]
    target = world_of(ver, fam, p, q)
    res = twin.move(build, ver, TwinMoveRequest(q0=q0_of(fam, p), target=TwinTarget(cartesian=target)))
    assert res.reached and res.ik_error_m < 1e-3
    assert np.allclose(res.frames[-1].tcp, target, atol=2e-3)
    # frame tcp is the world position (base pose applied), and the tool points down
    qf = np.array([res.frames[-1].joints[j.name] for j in fam.joints(p)])
    assert fam.fk(p, qf)[2, 2] == pytest.approx(-1.0, abs=0.03)


def test_unreachable_cartesian_target(arm):
    fam, p, ver, build = arm
    b = ver.robot.base.position
    res = twin.move(build, ver, TwinMoveRequest(q0=q0_of(fam, p), target=TwinTarget(cartesian=(b[0] + 5, b[1], 0.5))))
    assert not res.reached and res.ik_error_m > 1.0
    assert [h.limit for h in res.limits] == ['ik'] and len(res.frames) == 1


def test_gantry_cartesian_move(gantry):
    fam, p, ver, build = gantry
    q = [0.3, -0.1, 0.2]
    target = world_of(ver, fam, p, q)
    res = twin.move(build, ver, TwinMoveRequest(q0=q0_of(fam, p), target=TwinTarget(cartesian=target)))
    assert res.reached and np.allclose(res.frames[-1].tcp, target, atol=2e-3)
    assert res.frames[-1].joints['x_axis'] == pytest.approx(0.3, abs=2e-3)


def test_speed_scale_above_one_binds_speed_limit(arm):
    fam, p, ver, build = arm
    req = lambda s: TwinMoveRequest(q0=q0_of(fam, p), target=TwinTarget(joints={'j1': 2.5, 'j3': 1.0}), speed_scale=s)  # noqa: E731
    slow, fast, over = (twin.move(build, ver, req(s)) for s in (0.5, 1.0, 1.5))
    assert not fast.limits
    kinds = {h.limit for h in over.limits}
    assert 'speed' in kinds and 'acceleration' in kinds
    vmax = build.twin_limits.joints[0].max_speed
    for res in (fast, over):
        v = [abs(b.joints['j1'] - a.joints['j1']) / (b.t - a.t) for a, b in zip(res.frames, res.frames[1:])]
        assert max(v) <= vmax * (1 + 1e-6)
    assert over.duration_s == pytest.approx(fast.duration_s, rel=0.05)     # asking for more does not go faster
    assert slow.duration_s > 1.5 * fast.duration_s


def test_torque_limit_binds_and_slows(arm):
    fam, p, ver, build = arm
    req = TwinMoveRequest(q0=q0_of(fam, p, j2=0.5), target=TwinTarget(joints={'j2': 1.4}))
    ok = twin.move(build, ver, req)
    tiny = twin.move(with_limits(build, j2={'max_torque': 0.5}), ver, req)
    assert not any(h.limit == 'torque' for h in ok.limits)
    assert any(h.limit == 'torque' and h.joint == 'j2' for h in tiny.limits)
    assert tiny.torque_use['j2'] > 1.0 and tiny.duration_s > ok.duration_s


def test_run_task_phases_and_duration(arm):
    fam, p, ver, build = arm
    res = twin.run_task(build, ver, 1.0)
    assert res.reached and not [h for h in res.limits if h.limit == 'ik'] and len(res.frames) <= 1500
    phases = {w.phase for w in twin.waypoints(ver.spec, ver.scene, ver.robot.base) if w.phase}
    assert {f.phase for f in res.frames} == phases
    assert {'bottle'} <= {f.holding for f in res.frames}
    assert res.frames[0].holding == '' and res.frames[-1].holding == ''
    assert all(f.tcp is not None and 'gripper' in f.joints for f in res.frames)
    again = twin.run_task(build, ver, 1.0)
    assert again.model_dump() == res.model_dump()
    # a slower build (a tenth of the speeds) takes at least as long as the design's own simulation
    slow = with_limits(build, **{j.joint: {'max_speed': j.max_speed * 0.1, 'max_accel': j.max_accel * 0.01}
                                 for j in build.twin_limits.joints})
    assert twin.run_task(slow, ver, 1.0).duration_s >= ver.sim.duration_s
    assert twin.run_task(slow, ver, 1.0).duration_s > res.duration_s


def test_run_task_gantry_and_mobile_base():
    fam, p, ver = make(pallet_spec(), 'gantry')
    build = StudioBuild.model_construct(twin_limits=twin.twin_limits(ver, [], None))
    assert twin.run_task(build, ver, 1.0).reached
    fam, p, ver = make(mobile_delivery_spec(), 'mobile_base')
    build = StudioBuild.model_construct(twin_limits=twin.twin_limits(ver, [], None))
    res = twin.run_task(build, ver, 1.0)
    assert res.reached and res.duration_s == pytest.approx(ver.sim.duration_s)


def test_family_not_in_v1_is_refused():
    ver = SimpleNamespace(robot=SimpleNamespace(family='mobile_manipulator', params={}))
    with pytest.raises(ValueError, match='not in v1'):
        twin.twin_limits(ver, [], None)


def test_scene_box_does_not_change_the_twin(arm):
    # collisions are not part of the twin: the same limits and frames regardless of scene boxes
    fam, p, ver, build = arm
    box = SceneBox(id='t', kind='table', center=(0.5, 0.0, 0.7), size=(0.3, 0.3, 0.1))
    ver2 = ver.model_copy(update={'scene': ver.scene.model_copy(update={'boxes': [box]})})
    assert twin.run_task(build, ver2, 1.0).duration_s == twin.run_task(build, ver, 1.0).duration_s
