"""The simulated tool centre point keeps the robot's rated tool speed and the task's acceleration limit."""
import numpy as np

from fixtures_specs import bottle_spec
from studio import families
from studio.physics import derive, place_base
from studio.pipeline.design import size_and_simulate
from studio.schemas import RobotConcept, SceneSpec


def _tool_speed(concept, sim):
    fam = families.get(concept.family)
    p = fam.parse(concept.params)
    pts = np.array([fam.fk(p, np.array(list(f.joints.values())))[:3, 3] for f in sim.frames])
    ts = np.array([f.t for f in sim.frames])
    keep = np.concatenate([[True], np.diff(ts) > 1e-9])
    return float((np.linalg.norm(np.diff(pts[keep], axis=0), axis=1) / np.diff(ts[keep])).max()), p


def test_serial_arm_tool_keeps_its_rated_speed_and_the_acceleration_limit():
    spec = bottle_spec()
    spec = spec.model_copy(update={'physics_inputs': spec.physics_inputs.model_copy(update={'cycle_time_s': 6.0,
                                                                                          'max_accel_m_s2': 0.5})})
    base = place_base(spec)
    reqs = derive(spec, base)
    concept, _, _ = size_and_simulate(spec, reqs, SceneSpec(robot_base=base), RobotConcept(family='serial_arm', name='arm'), base)
    slow = concept.model_copy(update={'params': {**concept.params, 'max_tcp_speed_m_s': 0.1}})
    from studio.sim import simulate
    sim = simulate(spec, reqs, SceneSpec(robot_base=base), slow)
    speed, p = _tool_speed(slow, sim)
    assert speed <= p.max_tcp_speed_m_s * 1.05
    acc = next(c for c in sim.checks if c.requirement_id == 'max_accel')
    assert acc.passed is True and acc.actual <= 0.5 * 1.05
