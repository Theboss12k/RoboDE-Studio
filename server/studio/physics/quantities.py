"""The variable catalogue of a design: named quantities with units that the checks agent may use in formulas.

Pure: no store access, no LLM. Every number comes from the stored spec, the robot's parameters, the requirements or the
simulation result, or from a physical constant. The names are stable (snake_case, plus the capital `I` of an inertia):

  spec      m_<object> (kg)  w_<object> d_<object> h_<object> (m: size x, size y or the cylinder diameter, size z)
            I_<object>_z (kg*m**2, about the vertical axis: cylinder m r^2/2, box m (w^2 + d^2)/12, sphere 2 m r^2/5)
            cycle_time, turns_rotate_<n>, rpm_rotate_<n>, ramp_rotate_<n>, omega_rotate_<n>, alpha_rotate_<n>
            (<n> is the action's index in the task), max_accel_limit, max_jerk_limit, safety_factor, ...
  robot     rating_<joint> (N*m or N), vmax_<joint>, stroke_<joint> (linear axes), payload, reach_max,
            m_robot_<link> (the moving masses the family exposes), base_height
  sim       peak_speed_<joint>, peak_accel_<joint>, peak_tcp_speed, peak_tcp_accel, duration,
            static_load_<joint> (the simulation's joint_torque_* checks), time_rotate_<n>
  constants g (9.80665 m/s**2), pi

Units are pint-parsable strings. Entries are {name, value, unit, source}.
"""
from __future__ import annotations

import math
import re

import numpy as np

from .. import families

G = 9.80665
ACCEL_TIME_S = 0.25            # the simulator's default joint acceleration: full speed in this time


def _name(text):
    return re.sub(r'[^A-Za-z0-9_]', '_', str(text))


def _entry(name, value, unit, source):
    return {'name': name, 'value': float(value), 'unit': unit, 'source': source}


def inertia_z(obj):
    """Moment of inertia of a task object about the vertical axis (kg*m^2) from its mass, size and shape; (value,
    formula text)."""
    m, (x, y, z) = float(obj.mass_kg), obj.size_m
    if obj.shape == 'cylinder':
        r = max(x, y) / 2
        return 0.5 * m * r * r, 'I = m r^2 / 2'
    if obj.shape == 'sphere':
        r = max(x, y, z) / 2
        return 0.4 * m * r * r, 'I = 2 m r^2 / 5'
    return m * (x * x + y * y) / 12, 'I = m (w^2 + d^2) / 12'


def rotate_joint(act, axis):
    """Index into the actuated joints `act` of the joint a rotate with this `axis` turns, or None (as the simulator picks)."""
    names = [j.name for j in act]
    if axis == 'tool_yaw':
        if 'tool_yaw' in names:
            return names.index('tool_yaw')
        ok = [i for i, j in enumerate(act) if j.type in ('revolute', 'continuous')]
        return ok[-1] if ok else None
    return names.index(axis) if axis in names and act[names.index(axis)].type != 'prismatic' else None


def held_at(spec, index):
    """The task object the robot holds when action `index` starts ('' = none): the attached object, then pick/place."""
    held = next((o.id for o in spec.objects if o.attached), '')
    for a in spec.actions[:index]:
        if a.verb == 'pick':
            held = a.object
        elif a.verb == 'place':
            held = ''
    return held


def rotate_actions(spec, concept):
    """[{n, action, joint, omega, alpha, object}] for each valid rotate action of the spec on the concept's robot:
    omega = rpm 2 pi / 60 (rad/s); alpha = omega / ramp (rad/s^2), or the joint's own acceleration when ramp = 0;
    `joint` is the actuated joint's name and `object` the TaskObject held (or None)."""
    fam = families.get(concept.family)
    p = fam.parse(concept.params)
    act = [j for j in fam.joints(p) if j.actuated]
    vel = np.array([j.max_velocity for j in act])
    acc = (np.array(fam.joint_accels(p)) if hasattr(fam, 'joint_accels') else vel / ACCEL_TIME_S) if act else vel
    objs = {o.id: o for o in spec.objects}
    out = []
    for n, a in enumerate(spec.actions):
        if a.verb != 'rotate' or a.rpm <= 0:
            continue
        j = rotate_joint(act, a.axis)
        omega = a.rpm * 2 * math.pi / 60
        if a.ramp_s > 0:
            alpha = omega / a.ramp_s
        elif j is not None:
            alpha = float(acc[j])
        else:
            continue
        held = held_at(spec, n)
        out.append({'n': n, 'action': a, 'joint': act[j].name if j is not None else '', 'omega': omega, 'alpha': alpha,
                    'object': objs.get(held)})
    return out


def _tcp_peaks(frames):
    """(peak speed, peak acceleration) of the tool centre point over the frames: velocity between frames, acceleration as
    its second difference with the robot at rest before the first and after the last frame, smoothed like robot/sizing."""
    from ..robot.sizing import _smooth
    pts = [(f.t, f.tcp) for f in frames if f.tcp is not None]
    if len(pts) < 2:
        return 0.0, 0.0
    t = np.array([p[0] for p in pts], float)
    x = np.array([p[1] for p in pts], float)
    dt = np.diff(t)
    ok = dt > 1e-9
    v = np.where(ok[:, None], np.diff(x, axis=0) / np.where(ok, dt, 1.0)[:, None], 0.0)
    vv = np.vstack([np.zeros((1, 3)), v, np.zeros((1, 3))])
    span = np.concatenate([[dt[0]], (dt[1:] + dt[:-1]) / 2, [dt[-1]]])
    a = np.linalg.norm(np.diff(vv, axis=0), axis=1) / np.where(span > 1e-9, span, 1.0)
    return float(np.linalg.norm(v, axis=1).max()), float(_smooth(a).max())


def catalogue(spec, concept, requirements=None, sim=None, scene=None, fleet=None):
    """The catalogue (list of {name, value, unit, source}) of a single-robot design. `concept` is the RobotConcept
    (family and params), `sim` the SimResult (its planned checks are ignored). `fleet` (a FleetPlan, optional) adds the
    shared clock. Non-finite values are left out."""
    out, seen = [], set()

    def add(name, value, unit, source):
        if name in seen or value is None or not math.isfinite(float(value)):
            return
        seen.add(name)
        out.append(_entry(name, value, unit, source))

    # constants
    add('g', G, 'm/s**2', 'constant: standard gravity')
    add('pi', math.pi, '1', 'constant')
    # spec
    for o in spec.objects:
        n = _name(o.id)
        x, y, z = o.size_m
        add(f'm_{n}', o.mass_kg, 'kg', f'spec: mass of {o.name or o.id}')
        add(f'w_{n}', x, 'm', f'spec: size x of {o.id}')
        add(f'd_{n}', max(x, y) if o.shape == 'cylinder' else y, 'm',
            f'spec: {"diameter" if o.shape == "cylinder" else "size y"} of {o.id}')
        add(f'h_{n}', z, 'm', f'spec: size z of {o.id}')
        value, formula = inertia_z(o)
        add(f'I_{n}_z', value, 'kg*m**2', f'computed from the spec ({o.shape}, about the vertical axis): {formula}')
    pi = spec.physics_inputs
    add('cycle_time', pi.cycle_time_s, 's', 'spec: cycle time')
    add('safety_factor', pi.safety_factor, '1', 'spec: safety factor')
    add('friction_coefficient', pi.friction_coefficient, '1', 'spec: friction coefficient')
    add('approach_height', pi.approach_height_m, 'm', 'spec: approach height')
    if pi.max_accel_m_s2 is not None:
        add('max_accel_limit', pi.max_accel_m_s2, 'm/s**2', 'spec: acceleration limit (at most)')
    if pi.max_jerk_m_s3 is not None:
        add('max_jerk_limit', pi.max_jerk_m_s3, 'm/s**3', 'spec: jerk limit (at most)')
    for n, a in enumerate(spec.actions):
        if a.verb == 'rotate':
            add(f'turns_rotate_{n}', a.turns, '1', f'spec: turns of action {n}')
            add(f'rpm_rotate_{n}', a.rpm, 'rpm', f'spec: speed of action {n}')
            add(f'ramp_rotate_{n}', a.ramp_s, 's', f'spec: ramp time of action {n}')
        elif a.verb == 'move' and a.duration_s > 0:
            add(f'time_move_{n}', a.duration_s, 's', f'spec: allowed time of move action {n}')
    # robot
    fam = families.get(concept.family)
    p = fam.parse(concept.params)
    joints = [j for j in fam.joints(p) if j.actuated]
    for j in joints:
        jn = _name(j.name)
        lin = j.type == 'prismatic'
        add(f'rating_{jn}', j.max_effort, 'N' if lin else 'N*m', f'robot: rated {"force" if lin else "torque"} of {j.name}')
        add(f'vmax_{jn}', j.max_velocity, 'm/s' if lin else 'rad/s', f'robot: rated speed of {j.name}')
        if lin:
            add(f'stroke_{jn}', j.upper - j.lower, 'm', f'robot: stroke of {j.name}')
    caps = fam.capabilities(p)
    add('payload', caps.payload_kg, 'kg', 'robot: rated payload')
    add('reach_max', caps.reach_max_m, 'm', 'robot: reach')
    if caps.gripper_force_n:
        add('grip_force', caps.gripper_force_n, 'N', 'robot: gripper force')
    try:
        from ..robot.sizing import MASS_FIELDS
        for link, field in MASS_FIELDS.get(concept.family, {}).items():
            add(f'm_robot_{_name(link)}', getattr(p, field), 'kg', f'robot: moving mass of {link}')
    except (AttributeError, ImportError):
        pass
    add('base_height', concept.base.position[2], 'm', 'robot: base mounting height')
    # rotate values need the robot (the joint acceleration when no ramp is given)
    for r in rotate_actions(spec, concept):
        add(f'omega_rotate_{r["n"]}', r['omega'], 'rad/s', f'computed: peak speed of action {r["n"]} = rpm 2 pi / 60')
        add(f'alpha_rotate_{r["n"]}', r['alpha'], 'rad/s**2',
            f'computed: omega / ramp of action {r["n"]}' if r['action'].ramp_s > 0 else
            f'robot: acceleration of {r["joint"]} (no ramp given)')
    # simulation
    if sim is not None:
        from ..robot.sizing import _peak_speeds, frame_alphas
        names = [j.name for j in joints]
        if len(sim.frames) >= 2:
            speeds, alphas = _peak_speeds(sim.frames, names), frame_alphas(sim.frames, names)
            for i, j in enumerate(joints):
                lin = j.type == 'prismatic'
                add(f'peak_speed_{_name(j.name)}', speeds[i], 'm/s' if lin else 'rad/s', f'simulation: peak speed of {j.name}')
                add(f'peak_accel_{_name(j.name)}', alphas[:, i].max(), 'm/s**2' if lin else 'rad/s**2',
                    f'simulation: peak acceleration of {j.name} (smoothed second difference)')
            v, a = _tcp_peaks(sim.frames)
            add('peak_tcp_speed', v, 'm/s', 'simulation: peak tool centre point speed')
            add('peak_tcp_accel', a, 'm/s**2', 'simulation: peak tool centre point acceleration')
        add('duration', sim.duration_s, 's', 'simulation: length of the run')
        by_id = {c.requirement_id: c for c in sim.checks if c.source != 'planned'}
        for j in joints:
            c = by_id.get(f'joint_torque_{j.name}')
            if c is not None and c.actual is not None:
                add(f'static_load_{_name(j.name)}', c.actual, 'N' if j.type == 'prismatic' else 'N*m',
                    f'simulation: worst-case static load of {j.name}')
        for n, c in by_id.items():
            m = re.fullmatch(r'rotate_(\d+)', n)
            if m and c.actual is not None:
                add(f'time_rotate_{m.group(1)}', c.actual, 's', f'simulation: time of rotate action {m.group(1)}')
    if fleet is not None:
        add('fleet_robots', len(fleet.members), '1', 'fleet: number of robots')
        add('fleet_duration', fleet.duration_s, 's', 'fleet: length of the shared run')
    return out
