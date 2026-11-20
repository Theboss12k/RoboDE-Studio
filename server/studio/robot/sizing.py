"""Motor and drive sizing for the robot studio. Pure code: no store, no LLM. Every number that decides pass or fail
is computed here and carries a `Calc` or a `Check`.

Method (kinematic + static + acceleration; not dynamics simulation, not FEA, not a hardware test)
------------------------------------------------------------------------------------------------
Per actuated joint i, over poses = up to MAX_SAMPLES evenly spaced task frames of the design's simulation plus, per
joint, the frames of its largest accelerations (payload = the carried object's mass when the frame holds one), plus a
stretched pose with the RATED payload:

* static_i(q)   = |gravity_torques(q)_i|  (tau = -sum_k J_k^T m_k g, the family's own statics).
  Linear axes of a gantry: Z holds (m_z + m_tool + payload) g; X and Y carry FRICTION_COEFF * m * g
  (guide and belt drag allowance).
* J_i(q)        = reflected load inertia about the joint: sum_k m_k |dp_k/dq_i|^2 + sum_k m_k L_k^2 / 12 (links that
  move with the joint, as rods). Gantry axes: the carried mass. Conservative: not reduced for orientation.
* alpha_i(f)    = |second difference of q_i(t)| at frame f (the simulated trapezoid motion, at rest at both ends),
  smoothed (median of 3, then mean of 3 frames) so a one-frame discretisation spike is not read as an acceleration. It
  follows the sim's own speed and acceleration limits, so it is the acceleration the robot really performs.
* accel_i(f)    = J_i(q_f) * alpha_i(f)  (at the SAME frame as the static term).
* demand_i(f)   = static_i(q_f) + accel_i(f).
* continuous_i  = SF_CONTINUOUS * sqrt(mean_f demand_i(f)^2)  (RMS over the evenly sampled task frames).
* peak_i        = max(SF_PEAK * max_f demand_i(f), SF_CONTINUOUS * static_i(q_stretched)). The stretched pose (rated
  payload) is a sustained static hold, so it carries the continuous factor, not the peak factor, and no acceleration is
  added there (the task never accelerates the arm in that pose).
* Motor links: the extra masses (motor + gearbox) of a joint sit on its PARENT link, at the link's centre of mass
  (links: turret, upper_arm, forearm, wrist, tool; gantry: x_carriage, y_carriage, z_carriage; base: base_link).
* Wheels (mobile base): m = vehicle + payload, n = drive wheels, r = wheel radius, slope = rated max slope:
  static = m g (sin th + Crr cos th) r / n; accel = m a r / n; J = m r^2 / n; alpha = a / r;
  continuous = SF_C * sqrt(static^2 + 2 f accel^2) with f = WHEEL_ACCEL_FRACTION (a trapezoid of accelerate / cruise /
  decelerate phases of f, 1-2f, f of the time); peak = SF_P * (static + accel).

Gearing (`choose_drive`): the smallest standard ratio N whose motor torque tau / (N eta) fits the motor (continuous and
peak) AND whose joint speed omega N stays <= 0.8 of the motor's speed (rated, no-load derated, or the assumed stepper
speed); a motor that no ratio fits is told which constraint binds (torque needs N >= a, speed allows N <= b).
Gearing and the motor side (`motor_side`): tau_m = tau / (N eta); omega_m = omega N. N = motor turns per joint turn;
for a linear axis N is motor rad per metre (2 pi / lead). Efficiencies (planning figures): planetary single stage
(5, 10) 0.90, two stage (20, 50) 0.85; harmonic 0.80; timing-belt reduction 0.95; GT2 belt axis 0.90; rack and
pinion 0.90; wheel gearbox 0.90 (<= 10), 0.85 (<= 30), 0.80 (50); trapezoidal leadscrew (7 mm pitch diameter, mu 0.15,
14.5 deg thread half angle): eta = tan(lam) / tan(lam + phi), self-locking when lam <= phi.

Motor capability (`evaluate`): only `verified` values decide. Torque: rated_torque_nm, or for steppers
holding_torque_nm * STEPPER_TORQUE_USE (a running-torque allowance, not a pull-out curve); peak: peak_torque_nm else
the same (no overload credit). Speed: rated_speed_rpm, or no_load_speed_rpm * NO_LOAD_SPEED_DERATE.
A stepper whose page states no rated (or no-load) speed is judged at STEPPER_USABLE_RPM for its frame instead of
being rejected; that speed is added to the values as `assumed` and the speed check says so. Torque and current stay
verified; servos, BLDC and DC motors still need a verified speed.
Margins: continuous and peak = provided / required joint-side torque; speed = SPEED_USE_MAX * rated speed (joint side)
/ required speed, so the joint speed stays <= 0.8 of the rated speed; inertia_ratio = J_load / (N^2 J_rotor) <=
INERTIA_RATIO_MAX[kind] (checked only when the rotor inertia is verified; gearbox inertia ignored).
Current_a and voltage_v must also be verified (the electronics checks use them). A missing or unverified deciding value
fails the motor and `reason` names it.
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable

import numpy as np

from .. import families
from ..physics import quantities, statics
from ..schemas import (Calc, Check, DesignVersion, Drive, ElectronicsItem, JointSizing, MotorAlternative,
                       MotorChoice, Quantity, SpecValue)

SF_CONTINUOUS = 1.25
SF_PEAK = 1.5
ACCEL_RAMP_S = 0.2                # only for wheels (a / r) and as a documented fallback; joints use the trajectory
EXTRA_ALPHA_FRAMES = 5            # per joint, the frames of the largest accelerations are always evaluated
SPEED_USE_MAX = 0.8
INERTIA_RATIO_MAX = {'stepper': 5, 'servo': 10, 'bldc': 10, 'dc_gear': 10, 'other': 5}
STEPPER_TORQUE_USE = 0.7          # share of the holding torque usable while running
NO_LOAD_SPEED_DERATE = 0.6        # rated speed taken as this share of a quoted no-load speed
# Conservative usable speed (rpm) of a hybrid stepper on a chopper driver at 24 V, where about 70 % of the holding torque
# is still available, by NEMA frame. A planning assumption, NOT a manufacturer value: shop pages for steppers usually
# state holding torque but no rated speed. The PDF and the UI show it as `assumed`. Unknown frame: STEPPER_USABLE_RPM_UNKNOWN.
STEPPER_USABLE_RPM = {'8': 300.0, '11': 300.0, '14': 300.0, '17': 300.0, '23': 300.0, '34': 200.0}
STEPPER_USABLE_RPM_UNKNOWN = 200.0
STEPPER_SPEED_NOTE = 'conservative stepper usable speed at 24 V; the page states no rated speed'
FRICTION_COEFF = 0.05             # horizontal gantry axis drag: F = mu m g
WHEEL_ACCEL_FRACTION = 1 / 3
YAW_RADIUS_M = 0.05               # tool-yaw load: (tool + payload) at this radius
MAX_SAMPLES = 60
DRIVER_MARGIN = 1.2               # driver current >= 1.2 x motor current
PSU_MARGIN = 1.3                  # PSU watts >= 1.3 x sum(V I)
BATTERY_C_RATE = 1.0              # continuous discharge power = capacity (Wh) x this per hour
V1_FAMILIES = ('serial_arm', 'gantry', 'mobile_base')
DEFAULT_VOLTAGE_V = {'stepper': 24.0, 'bldc': 48.0, 'servo': 48.0, 'dc_gear': 24.0, 'other': 24.0}
STEPPER_BUS_V = DEFAULT_VOLTAGE_V['stepper']   # chopper drives run steppers from a bus far above the coil's rated voltage
DRIVER_TYPE = {'stepper': 'stepper', 'dc_gear': 'dc'}   # motor type -> driver type; anything else needs a BLDC / servo drive
DRIVER_NAME = {'stepper': 'Stepper driver', 'dc': 'DC motor driver', 'bldc': 'BLDC / servo drive'}


def driver_type(motor_type: str) -> str:
    """The driver type a motor type needs: stepper, dc (brushed) or bldc (BLDC / servo)."""
    return DRIVER_TYPE.get(motor_type, 'bldc')


def driver_type_of(item) -> str | None:
    """The driver type of a defined driver item, from the name `electronics_for` gave it."""
    return next((t for t, name in DRIVER_NAME.items() if item.name.startswith(name)), None)
DRIVER_RATINGS_A = (1.5, 2.5, 4.2, 6.0, 8.0, 10.0, 15.0, 20.0, 30.0, 50.0, 80.0, 120.0)
PSU_RATINGS_W = (60, 120, 240, 350, 480, 600, 750, 1000, 1500, 2000, 3000, 5000)
CONTROLLER_AXES = (3, 4, 5, 6, 8)
LS_PITCH_DIAMETER_M, LS_MU, LS_HALF_ANGLE = 0.007, 0.15, math.radians(14.5)

ARM_MASS_FIELDS = {'turret': 'base_mass_kg', 'upper_arm': 'upper_arm_mass_kg', 'forearm': 'forearm_mass_kg',
                   'wrist': 'wrist_mass_kg', 'tool': 'tool_mass_kg'}
GANTRY_MASS_FIELDS = {'x_carriage': 'x_moving_mass_kg', 'y_carriage': 'y_moving_mass_kg',
                      'z_carriage': 'z_moving_mass_kg', 'tool': 'tool_mass_kg'}
MASS_FIELDS = {'serial_arm': ARM_MASS_FIELDS, 'gantry': GANTRY_MASS_FIELDS, 'mobile_base': {'base_link': 'mass_kg'}}
ARM_PARENT = {'j1': 'base_link', 'j2': 'turret', 'j3': 'upper_arm', 'j4': 'forearm', 'j5': 'wrist', 'j6': 'wrist'}
GANTRY_PARENT = {'x_axis': 'base_link', 'y_axis': 'x_carriage', 'z_axis': 'y_carriage', 'tool_yaw': 'z_carriage'}


def _q(value, unit=''):
    return Quantity(value=float(value), unit=unit)


def _calc(formula, inputs, result, unit, note=''):
    return Calc(formula=formula, inputs={k: _q(*v) if isinstance(v, tuple) else _q(v) for k, v in inputs.items()},
                result=float(result), unit=unit, note=note)


def parent_link(family: str, joint: str) -> str:
    """The link a joint's motor sits on (its parent link)."""
    if family == 'mobile_base':
        return 'base_link'
    return (ARM_PARENT if family == 'serial_arm' else GANTRY_PARENT).get(joint, 'base_link')


def _with_extra(family, p, extra):
    """Params with the extra masses added to the named links; unknown links (e.g. base_link) have no effect."""
    fields = MASS_FIELDS[family]
    upd = {}
    for link, kg in (extra or {}).items():
        if link in fields and kg:
            upd[fields[link]] = getattr(p, fields[link]) + float(kg)
    return p.model_copy(update=upd) if upd else p


# --- per-family loads ---------------------------------------------------------------------------------------------------

def _arm_inertia(fam, p, q, payload):
    masses = fam._mass_points(p, q, payload)[0]
    jac = [statics.numeric_jacobian(lambda x, k=k: fam._mass_points(p, x, payload)[1][k], q)
           for k in range(len(masses))]
    lengths = [p.upper_arm_m, p.forearm_m, p.wrist_m, p.tool_length_m]
    out = np.zeros(len(q))
    for i in range(len(q)):
        first = 0 if i == 0 else min(i - 1, 3)
        out[i] = (sum(m * float(np.dot(J[:, i], J[:, i])) for m, J in zip(masses, jac, strict=True))
                  + sum(masses[k] * lengths[k] ** 2 / 12 for k in range(first, 4)))
    return out


def _gantry_loads(p, payload):
    t = p.tool_mass_kg + payload
    mx = p.x_moving_mass_kg + p.y_moving_mass_kg + p.z_moving_mass_kg + t
    my = p.y_moving_mass_kg + p.z_moving_mass_kg + t
    mz = p.z_moving_mass_kg + t
    static = [FRICTION_COEFF * mx * statics.G, FRICTION_COEFF * my * statics.G, mz * statics.G]
    inertia = [mx, my, mz]
    if p.tool_yaw:
        static.append(0.0)
        inertia.append(t * YAW_RADIUS_M ** 2)
    return np.array(static), np.array(inertia)


def _loads(family, fam, p, q, payload):
    if family == 'serial_arm':
        return np.abs(fam.gravity_torques(p, q, payload)), _arm_inertia(fam, p, q, payload)
    return _gantry_loads(p, payload)


def _peak_speeds(frames, names):
    peak = np.zeros(len(names))
    for a, b in zip(frames, frames[1:]):
        dt = b.t - a.t
        if dt <= 1e-9:
            continue
        for i, n in enumerate(names):
            if n in a.joints and n in b.joints:
                peak[i] = max(peak[i], abs(b.joints[n] - a.joints[n]) / dt)
    return peak


def _smooth(a):
    """Median of 3 then mean of 3 (edge values repeated): removes one-frame spikes, keeps a sustained plateau."""
    if len(a) < 3:
        return a
    pad = np.concatenate([[a[0]], a, [a[-1]]])
    med = np.median(np.stack([pad[:-2], pad[1:-1], pad[2:]]), axis=0)
    pad = np.concatenate([[med[0]], med, [med[-1]]])
    return (pad[:-2] + pad[1:-1] + pad[2:]) / 3.0


def frame_alphas(frames, names) -> np.ndarray:
    """|alpha| per frame and joint (rows = frames): the second difference of q(t) with the robot at rest before the first
    and after the last frame, smoothed. Frames closer than 1 ns in time are treated as one instant."""
    n = len(frames)
    out = np.zeros((n, len(names)))
    if n < 2:
        return out
    t = np.array([f.t for f in frames], float)
    dt = np.diff(t)
    for i, name in enumerate(names):
        q = np.array([f.joints.get(name, np.nan) for f in frames], float)
        if np.isnan(q).any():
            continue
        v = np.where(dt > 1e-9, np.diff(q) / np.where(dt > 1e-9, dt, 1.0), 0.0)
        vv = np.concatenate([[0.0], v, [0.0]])                        # velocity before, between and after the frames
        span = np.concatenate([[dt[0]], (dt[1:] + dt[:-1]) / 2, [dt[-1]]])
        a = np.abs(np.diff(vv)) / np.where(span > 1e-9, span, 1.0)
        out[:, i] = _smooth(a)
    return out


def _payload_of(version, holding):
    objs = {o.id: o for o in version.spec.objects}
    if holding in objs:
        return float(objs[holding].mass_kg)
    return float(version.spec.physics_inputs.object_mass_kg) if holding else 0.0


def _kind(joint):
    return 'prismatic' if joint.type == 'prismatic' else 'revolute'


def size_joints(version: DesignVersion, extra_masses: dict[str, float] | None = None) -> list[JointSizing]:
    """Per-joint requirements (see the module docstring). `extra_masses`: link name -> kg of motor and gearbox added
    to that link (serial arm: turret, upper_arm, forearm, wrist, tool; gantry: x_carriage, y_carriage, z_carriage,
    tool; mobile base: base_link). Joint order = the family's actuated joints (wheels for a mobile base)."""
    family = version.robot.family
    if family not in V1_FAMILIES:
        raise ValueError(f'{family} is not in v1 of the robot studio')
    fam = families.get(family)
    p = _with_extra(family, fam.parse(version.robot.params), extra_masses)
    if family == 'mobile_base':
        return _wheel_sizing(version, fam, p)
    joints = [j for j in fam.joints(p) if j.actuated]
    names = [j.name for j in joints]
    frames = version.sim.frames
    omega = _peak_speeds(frames, names)
    alphas = frame_alphas(frames, names)
    uniform = set(np.unique(np.linspace(0, len(frames) - 1, min(MAX_SAMPLES, len(frames))).astype(int)).tolist()) if frames else set()
    extra = set()
    for i in range(len(names)):
        if len(frames) and alphas[:, i].max() > 0:
            extra |= set(np.argsort(alphas[:, i])[-EXTRA_ALPHA_FRAMES:].tolist())
    idx = sorted(uniform | extra)
    stretched = fam._stretched(p) if hasattr(fam, '_stretched') else np.zeros(len(names))
    s_static, s_inertia = _loads(family, fam, p, stretched, float(p.payload_kg))
    rows, even = [], []                                             # (static, inertia) and alpha per sampled frame
    for i in idx:
        f = frames[int(i)]
        q = np.array([f.joints.get(n, 0.0) for n in names], float)
        rows.append(_loads(family, fam, p, q, _payload_of(version, f.holding)))
        even.append(int(i) in uniform)
    if not rows:
        rows, even, idx = [(s_static, s_inertia)], [True], [0]
        alphas = np.zeros((1, len(names)))
    even = np.array(even)
    out = []
    for i, j in enumerate(joints):
        kind = _kind(j)
        stat = np.array([r[0][i] for r in rows])
        iner = np.array([r[1][i] for r in rows])
        al = np.array([alphas[k, i] for k in idx])
        accel_f = iner * al
        demand = stat + accel_f
        k = int(np.argmax(demand))
        static = float(max(stat.max(), s_static[i]))
        inertia = float(max(iner.max(), s_inertia[i]))
        accel = float(accel_f[k])
        rms = float(np.sqrt(np.mean(demand[even] ** 2)))
        spin = _spin_up(version, j.name, static) if kind == 'revolute' else None   # rotate actions only
        if spin is not None:
            accel = max(accel, spin[0])
            demand_peak = max(float(demand[k]), spin[0] + static)
        else:
            demand_peak = float(demand[k])
        task_peak, hold_peak = SF_PEAK * demand_peak, SF_CONTINUOUS * float(s_static[i])
        peak = max(task_peak, hold_peak)
        tu, ju = ('N', 'kg') if kind == 'prismatic' else ('N*m', 'kg*m^2')
        su = 'm/s' if kind == 'prismatic' else 'rad/s'
        where = 'stretched pose' if s_static[i] >= stat.max() else 'a task frame'
        at = (f'governed by the stretched-pose hold: SF_C x static {hold_peak:.4g} > SF_P x task-frame demand {task_peak:.4g}'
              if hold_peak > task_peak else
              f'governed by the task frame (static + J alpha at the same frame): SF_P x {demand[k]:.4g} >= SF_C x stretched static {hold_peak:.4g}')
        calcs = [
            _calc('tau_static = max over task frames and the stretched pose of |sum_k J_k^T m_k g| (payload carried)',
                  {'frames_used': len(rows), 'payload_rated': (p.payload_kg, 'kg')}, static, tu, f'worst at the {where}'),
            _calc('J_load = sum_k m_k |dp_k/dq|^2 + sum_k m_k L_k^2 / 12 (worst pose)', {'poses': len(rows) + 1},
                  inertia, ju),
            _calc('alpha_peak = max over frames of the smoothed |d2q/dt2| of the simulated trajectory',
                  {'frames': len(frames), 'omega_peak': (omega[i], su)}, float(al.max()), su + '/s',
                  'second difference of q(t), median-3 then mean-3 smoothed; follows the sim trapezoid limits'),
            _calc('tau_accel = J_load(f*) * alpha(f*) at the frame f* of the largest demand',
                  {'J_load': (float(iner[k]), ju), 'alpha': (float(al[k]), su + '/s')}, accel, tu),
            _calc('tau_cont = SF_C * sqrt(mean_f (tau_static_f + J_f alpha_f)^2)',
                  {'SF_C': SF_CONTINUOUS, 'rms': (rms, tu)}, SF_CONTINUOUS * rms, tu),
            _calc('tau_peak = max(SF_P * max_f (tau_static_f + J_f alpha_f), SF_C * tau_static(stretched pose))',
                  {'SF_P': SF_PEAK, 'SF_C': SF_CONTINUOUS, 'task_demand': (float(demand[k]), tu),
                   'stretched_static': (float(s_static[i]), tu)}, peak, tu, at),
            _calc('omega = max |dq/dt| over the simulated frames', {'frames': len(frames)}, omega[i], su)]
        if spin is not None:
            calcs.insert(4, spin[1])
        out.append(JointSizing(joint=j.name, kind=kind, static_torque=static, accel_torque=accel,
                               continuous=SF_CONTINUOUS * rms, peak=peak, speed=float(omega[i]),
                               torque_unit=tu, speed_unit=su, calcs=calcs))
    return out


def _spin_up(version, joint, static):
    """(torque, Calc) of the inertial torque that spins up a rotated load on `joint`: the largest of
    tau = I_obj_z * alpha_rotate over the spec's rotate actions on this joint that turn a held or attached object
    (physics.quantities: the object's inertia about the vertical axis from its mass, size and shape, and alpha from
    rpm and ramp). Code-computed, never from a plan. None when no rotate action turns a load on this joint."""
    best = None
    for r in quantities.rotate_actions(version.spec, version.robot):
        if r['joint'] != joint or r['object'] is None:
            continue
        inertia, how = quantities.inertia_z(r['object'])
        tau = inertia * r['alpha']
        if best is None or tau > best[0]:
            best = (tau, r, inertia, how)
    if best is None:
        return None
    tau, r, inertia, how = best
    calc = _calc('tau_rotate = I_obj_z * alpha_rotate (spin-up of the rotated load about the vertical axis, '
                 'added to the joint peak demand with the static load)',
                 {'I_obj_z': (inertia, 'kg*m^2'), 'alpha_rotate': (r['alpha'], 'rad/s^2'),
                  'omega_rotate': (r['omega'], 'rad/s'), 'static': (static, 'N*m')}, tau, 'N*m',
                 f'{r["object"].id}: {how}; rotate action {r["n"]} ({r["action"].turns:g} turns at {r["action"].rpm:g} rpm)')
    return tau, calc


def _wheel_sizing(version, fam, p):
    pi = version.spec.physics_inputs
    m, n, r, a = p.mass_kg + p.payload_kg, p.drive_wheels, p.wheel_radius_m, p.max_accel_m_s2
    th = math.radians(p.max_slope_deg)
    static = m * statics.G * (math.sin(th) + pi.rolling_resistance * math.cos(th)) * r / n
    accel = m * a * r / n
    f = WHEEL_ACCEL_FRACTION
    rms = math.sqrt(static ** 2 + 2 * f * accel ** 2)
    omega = p.max_speed_m_s / r
    calcs = [
        _calc('tau_static = m g (sin(th) + Crr cos(th)) r / n',
              {'m': (m, 'kg'), 'theta': (p.max_slope_deg, 'deg'), 'Crr': pi.rolling_resistance, 'r': (r, 'm'),
               'n': n}, static, 'N*m'),
        _calc('J_load = m r^2 / n', {'m': (m, 'kg'), 'r': (r, 'm'), 'n': n}, m * r * r / n, 'kg*m^2'),
        _calc('alpha = a / r', {'a': (a, 'm/s^2'), 'r': (r, 'm')}, a / r, 'rad/s^2'),
        _calc('tau_accel = m a r / n', {'m': (m, 'kg'), 'a': (a, 'm/s^2'), 'r': (r, 'm'), 'n': n}, accel, 'N*m'),
        _calc('tau_cont = SF_C * sqrt(static^2 + 2 f accel^2)', {'SF_C': SF_CONTINUOUS, 'f': f}, SF_CONTINUOUS * rms,
              'N*m', 'accelerate / cruise / decelerate profile'),
        _calc('tau_peak = SF_P * (static + accel)', {'SF_P': SF_PEAK}, SF_PEAK * (static + accel), 'N*m'),
        _calc('omega = v / r', {'v': (p.max_speed_m_s, 'm/s'), 'r': (r, 'm')}, omega, 'rad/s')]
    return [JointSizing(joint=j.name, kind='wheel', static_torque=static, accel_torque=accel,
                        continuous=SF_CONTINUOUS * rms, peak=SF_PEAK * (static + accel), speed=omega, calcs=calcs)
            for j in fam.joints(p)]


def _find(sizing: JointSizing, prefix: str) -> float | None:
    return next((c.result for c in sizing.calcs if c.formula.startswith(prefix)), None)


# --- drives -------------------------------------------------------------------------------------------------------------

def leadscrew_angles(lead_m: float) -> tuple[float, float]:
    """(lead angle lam, friction angle phi) of the trapezoidal screw."""
    lam = math.atan(lead_m / (math.pi * LS_PITCH_DIAMETER_M))
    return lam, math.atan(LS_MU / math.cos(LS_HALF_ANGLE))


def leadscrew_efficiency(lead_m: float) -> float:
    lam, phi = leadscrew_angles(lead_m)
    return math.tan(lam) / math.tan(lam + phi)


def self_locking(drive: Drive) -> bool:
    """A leadscrew holds its load with the power off when lam <= phi (other drive types never do)."""
    if drive.type != 'leadscrew' or not drive.lead_m:
        return False
    lam, phi = leadscrew_angles(drive.lead_m)
    return lam <= phi


PLANETARY_RATIOS = (3, 4, 5, 7, 10, 15, 20, 30, 50, 100)


def _planetary(n):
    """Standard planetary gearbox: one stage (<= 10) 0.90, two stages (<= 50) 0.85, three stages (100) 0.80."""
    return Drive(type='planetary', ratio=n, efficiency=0.9 if n <= 10 else 0.85 if n <= 50 else 0.8,
                 name=f'Planetary gearbox {n:g}:1')


def drive_options(family: str, joint_index: int, sizing: JointSizing) -> list[Drive]:
    """Standard gearings for a joint: revolute arm joints planetary 3/4/5/7/10/15/20/30/50/100, harmonic 50/100 (joints 1-4), belt
    reduction 3:1, direct drive for wrist and yaw joints; gantry X/Y GT2 belts (20 and 30 tooth pulleys) and a rack;
    gantry Z leadscrew lead 2/5/8/10 mm; wheels direct or gearbox 5-50:1."""
    if sizing.kind == 'wheel':
        return [Drive(type='direct', ratio=1.0, efficiency=1.0, name='Direct (hub motor)')] + [
            Drive(type='wheel_gearbox', ratio=n, efficiency=0.9 if n <= 10 else 0.85 if n <= 30 else 0.8,
                  name=f'Wheel gearbox {n}:1') for n in (5, 10, 20, 30, 50)]
    if sizing.kind == 'prismatic':
        if sizing.joint.startswith('z'):
            return [Drive(type='leadscrew', ratio=2 * math.pi / (mm / 1000), efficiency=round(leadscrew_efficiency(mm / 1000), 3),
                          lead_m=mm / 1000, name=f'Trapezoidal leadscrew, {mm} mm lead') for mm in (2, 5, 8, 10)]
        out = [Drive(type='belt', ratio=2 * math.pi / (teeth * 0.002), efficiency=0.9, lead_m=teeth * 0.002,
                     name=f'GT2 belt, {teeth}-tooth pulley') for teeth in (20, 30)]
        pinion = math.pi * 0.03
        return out + [Drive(type='rack', ratio=2 * math.pi / pinion, efficiency=0.9, lead_m=pinion,
                            name='Rack and 30 mm pinion')]
    out = [_planetary(n) for n in PLANETARY_RATIOS]
    if joint_index < 4 and family == 'serial_arm':
        out += [Drive(type='harmonic', ratio=n, efficiency=0.8, name=f'Harmonic reducer {n}:1') for n in (50, 100)]
    out.append(Drive(type='belt', ratio=3.0, efficiency=0.95, name='Timing-belt reduction 3:1'))
    if joint_index >= 4 or family == 'gantry':
        out.append(Drive(type='direct', ratio=1.0, efficiency=1.0, name='Direct drive'))
    return out


def motor_side(sizing: JointSizing, drive: Drive) -> dict:
    """{continuous_nm, peak_nm, speed_rpm}: tau_m = tau / (N eta), omega_m = omega N (rpm = omega_m 60 / 2 pi)."""
    k = drive.ratio * drive.efficiency
    return {'continuous_nm': sizing.continuous / k, 'peak_nm': sizing.peak / k,
            'speed_rpm': sizing.speed * drive.ratio * 60 / (2 * math.pi)}


STEPPER_HOLD_TIERS_NM = (3.0, 12.0)   # holding torque of the largest NEMA 23 and NEMA 34 stacks (NEMA_TABLE)


def motor_side_calcs(sizing: JointSizing, drive: Drive) -> list[Calc]:
    """The motor-side figures after the chosen ratio, as calculations (a sanity check of the gearing): tau_m = tau /
    (N eta) for the continuous and peak torque, and the motor speed omega N in rpm."""
    ms = motor_side(sizing, drive)
    inputs = {'N': drive.ratio, 'eta': drive.efficiency}
    tu = sizing.torque_unit
    return [
        _calc('tau_m_continuous = tau_continuous / (N eta)', {**inputs, 'tau_continuous': (sizing.continuous, tu)},
              ms['continuous_nm'], 'N*m', drive.name),
        _calc('tau_m_peak = tau_peak / (N eta)', {**inputs, 'tau_peak': (sizing.peak, tu)}, ms['peak_nm'], 'N*m',
              drive.name),
        _calc('omega_m = omega N 60 / (2 pi)', {'N': drive.ratio, 'omega': (sizing.speed, sizing.speed_unit)},
              ms['speed_rpm'], 'rpm', drive.name)]


def default_drive(sizing: JointSizing, family: str, joint_index: int) -> Drive:
    """Gearing for a defined motor (and for the search needs). Revolute joints: the smallest standard ratio whose motor
    speed stays <= 1000 rpm (stepper) and whose motor torque fits a NEMA 23 stack, else a NEMA 34 stack; else (servo) the
    smallest ratio within 3000 rpm and 30 N*m, else the largest ratio within the speed limit. Linear axes and wheels: the
    largest ratio whose motor speed stays <= 1000 rpm (stepper), else <= 3000 rpm, else the slowest option."""
    opts = sorted(drive_options(family, joint_index, sizing), key=lambda d: (d.ratio, -d.efficiency))
    if sizing.kind == 'revolute':
        slow = [d for d in opts if motor_side(sizing, d)['speed_rpm'] <= 1000.0]
        for hold in STEPPER_HOLD_TIERS_NM:
            ok = [d for d in slow if max(motor_side(sizing, d)['continuous_nm'],
                                         motor_side(sizing, d)['peak_nm']) / STEPPER_TORQUE_USE <= hold]
            if ok:
                return ok[0]
        servo = [d for d in opts if motor_side(sizing, d)['speed_rpm'] <= 3000.0
                 and max(motor_side(sizing, d)['continuous_nm'], motor_side(sizing, d)['peak_nm']) <= SERVO_FRAMES[-1][1]]
        if servo:
            return servo[0]
    for limit in (1000.0, 3000.0):
        ok = [d for d in opts if motor_side(sizing, d)['speed_rpm'] <= limit]
        if ok:
            return max(ok, key=lambda d: d.ratio)
    return min(opts, key=lambda d: motor_side(sizing, d)['speed_rpm'])


def choose_drive(alt: MotorAlternative, sizing: JointSizing, options: list[Drive], motor_kind: str
                 ) -> tuple[Drive, bool, dict, list[Check], str]:
    """(drive, passes, margins, checks, reason) for a motor: the smallest standard ratio that satisfies torque
    (tau / (N eta) <= the motor's continuous and peak torque) and speed (omega N <= 0.8 of the motor speed, assumed or
    verified) and the other checks. When none does, the drive with the best smallest margin is returned and `reason` names
    the binding constraint: torque needs N >= a, speed allows N <= b (or neither range exists)."""
    opts = sorted(options, key=lambda d: (d.ratio, -d.efficiency))
    results = [(d, *evaluate(alt, sizing, d, motor_kind)) for d in opts]
    for r in results:
        if r[1]:
            return r
    best = max(results, key=lambda r: min(r[2].values(), default=-1.0))
    if not best[2]:                                              # missing deciding values: nothing to compare
        return best
    torque_ok = [r[0].ratio for r in results if r[2].get('continuous', 0) >= 1 and r[2].get('peak', 0) >= 1]
    speed_ok = [r[0].ratio for r in results if r[2].get('speed', 0) >= 1]
    unit = 'm^-1' if sizing.kind == 'prismatic' else ':1'
    if not torque_ok and not speed_ok:
        why = 'torque and speed both fail at every standard ratio'
    elif not torque_ok:
        why = (f'torque binds: the motor is too weak even at the largest ratio (best '
               f'{max(r[2]["peak"] for r in results):.2f}x of the peak torque needed)')
    elif not speed_ok:
        why = (f'speed binds: the motor is too slow even at the smallest ratio (best '
               f'{max(r[2]["speed"] for r in results):.2f}x of the speed needed)')
    elif min(torque_ok) > max(speed_ok):
        why = (f'torque needs ratio >= {min(torque_ok):g}{unit} but speed allows <= {max(speed_ok):g}{unit}: '
               f'torque and speed cannot both be met')
    else:
        why = 'torque and speed windows overlap but another check fails'
    detail = best[4]
    if detail.startswith('Fails'):
        detail = detail.split(': ', 1)[-1]
    return best[0], False, best[2], best[3], f'Fails ({why}); best at {best[0].ratio:g}{unit}: {detail}'[:400]


def drive_mass(drive: Drive, sizing: JointSizing) -> float:
    """Rule-of-thumb gearbox/transmission mass (kg): planetary 0.1 + 0.04 tau_peak, harmonic 0.2 + 0.03 tau,
    wheel gearbox 0.2 + 0.03 tau (tau = joint-side peak, N*m); others 0.1."""
    t = sizing.peak if sizing.kind != 'prismatic' else 0.0
    return {'planetary': 0.1 + 0.04 * t, 'harmonic': 0.2 + 0.03 * t, 'wheel_gearbox': 0.2 + 0.03 * t}.get(drive.type, 0.1 if drive.type != 'direct' else 0.0)


# --- evaluation ---------------------------------------------------------------------------------------------------------

def verified(values: list[SpecValue], key: str) -> float | None:
    return next((v.value for v in values if v.key == key and v.status == 'verified' and v.value is not None), None)


def stepper_frame(values: list[SpecValue]) -> str | None:
    """NEMA frame number of a stepper: the frame value (verified text, else assumed), else the NEMA_TABLE frame whose
    flange matches a flange_mm value (within 1 mm), else None."""
    from .sourcing import NEMA_TABLE
    frame = _frame_number(values)
    if frame:
        return frame
    flange = next((v.value for v in values if v.key == 'flange_mm' and v.value is not None), None)
    return None if flange is None else next((n for n, t in NEMA_TABLE.items() if abs(t['flange_mm'] - flange) <= 1.0), None)


def stepper_assumed_speed(values: list[SpecValue]) -> SpecValue:
    """The `assumed` rated_speed_rpm of a stepper without a verified speed (STEPPER_USABLE_RPM for its frame)."""
    rpm = STEPPER_USABLE_RPM.get(stepper_frame(values) or '', STEPPER_USABLE_RPM_UNKNOWN)
    return SpecValue(key='rated_speed_rpm', value=rpm, unit='rpm', status='assumed', note=STEPPER_SPEED_NOTE)


def capability(values: list[SpecValue], motor_kind: str) -> tuple[dict, list[str]]:
    """Motor-side capability from verified values: ({continuous_nm, peak_nm, speed_rpm, current_a, voltage_v, and
    speed_assumed = True when a stepper's speed is the STEPPER_USABLE_RPM assumption}, missing)."""
    cap, missing = {}, []
    t = verified(values, 'rated_torque_nm')
    if t is None and motor_kind == 'stepper':
        h = verified(values, 'holding_torque_nm')
        t = None if h is None else h * STEPPER_TORQUE_USE
    if t is None:
        missing.append('verified rated torque' + (' (or holding torque)' if motor_kind == 'stepper' else ''))
    else:
        cap['continuous_nm'] = t
        cap['peak_nm'] = max(verified(values, 'peak_torque_nm') or 0.0, t)
    s = verified(values, 'rated_speed_rpm')
    if s is None:
        n = verified(values, 'no_load_speed_rpm')
        s = None if n is None else n * NO_LOAD_SPEED_DERATE
    if s is None and motor_kind == 'stepper':
        s = stepper_assumed_speed(values).value
        cap['speed_assumed'] = True
    if s is None:
        missing.append('verified rated speed')
    else:
        cap['speed_rpm'] = s
    for key, label in (('current_a', 'verified current'), ('voltage_v', 'verified voltage')):
        v = verified(values, key)
        if v is None:
            missing.append(label)
        else:
            cap[key] = v
    return cap, missing


def provided_side(cap: dict, drive: Drive) -> dict:
    """Joint-side capability through the drive: continuous and peak = tau_m N eta; speed = 0.8 rated / N."""
    k = drive.ratio * drive.efficiency
    return {'continuous': cap['continuous_nm'] * k, 'peak': cap['peak_nm'] * k,
            'speed': SPEED_USE_MAX * cap['speed_rpm'] * 2 * math.pi / 60 / drive.ratio}


def _ratio(provided, required):
    return 999.0 if required <= 1e-12 else min(provided / required, 999.0)


def _check(rid, name, required, actual, unit, passed, detail, formula, inputs):
    return Check(requirement_id=rid, name=name, required=required, actual=actual, unit=unit, passed=passed,
                 detail=detail, calc=_calc(formula, inputs, actual if actual is not None else 0.0, unit))


def evaluate(alt: MotorAlternative, sizing: JointSizing, drive: Drive, motor_kind: str
             ) -> tuple[bool, dict, list[Check], str]:
    """(passes, margins, checks, reason) for a motor with a drive. Uses `verified` values only; a missing deciding value
    (torque, speed, current, voltage) fails with a reason naming it. Margins: continuous, peak, speed, inertia_ratio."""
    j, tu = sizing.joint, sizing.torque_unit
    cap, missing = capability(alt.values, motor_kind)
    if missing:
        reason = 'Missing: ' + ', '.join(missing)
        checks = [Check(requirement_id=f'motor_{j}_values', name='Deciding values verified', required=None,
                        actual=None, unit='', passed=False, detail=reason)]
        return False, {}, checks, reason
    if cap.get('speed_assumed') and not any(v.key == 'rated_speed_rpm' for v in alt.values):
        alt.values.append(stepper_assumed_speed(alt.values))
    prov = provided_side(cap, drive)
    req = {'continuous': sizing.continuous, 'peak': sizing.peak, 'speed': sizing.speed}
    margins = {k: _ratio(prov[k], req[k]) for k in req}
    su = sizing.speed_unit
    inputs = {'N': drive.ratio, 'eta': drive.efficiency}
    checks = [
        _check(f'motor_{j}_continuous', 'Continuous torque', req['continuous'], prov['continuous'], tu,
               prov['continuous'] >= req['continuous'], f'{margins["continuous"]:.2f}x margin',
               'provided = tau_rated N eta', {**inputs, 'tau_rated': (cap['continuous_nm'], 'N*m')}),
        _check(f'motor_{j}_peak', 'Peak torque', req['peak'], prov['peak'], tu, prov['peak'] >= req['peak'],
               f'{margins["peak"]:.2f}x margin', 'provided = tau_peak N eta',
               {**inputs, 'tau_peak': (cap['peak_nm'], 'N*m')}),
        _check(f'motor_{j}_speed', 'Speed (<= 0.8 rated)', req['speed'], prov['speed'], su,
               prov['speed'] >= req['speed'], f'{margins["speed"]:.2f}x margin' + (
                   f'; speed assumed ({cap["speed_rpm"]:g} rpm conservative stepper usable speed, not on the page)'
                   if cap.get('speed_assumed') else ''),
               'provided = 0.8 rpm_rated 2 pi / 60 / N', {'rpm_rated': (cap['speed_rpm'], 'rpm'), 'N': drive.ratio})]
    j_load = _find(sizing, 'J_load')
    j_rotor = verified(alt.values, 'rotor_inertia_kgm2')
    limit = INERTIA_RATIO_MAX.get(motor_kind, 5)
    if j_load is not None and j_rotor:
        ratio = j_load / drive.ratio ** 2 / j_rotor
        margins['inertia_ratio'] = ratio
        checks.append(_check(f'motor_{j}_inertia', 'Reflected inertia ratio', float(limit), ratio, '', ratio <= limit,
                             f'limit {limit} for a {motor_kind}', 'ratio = J_load / (N^2 J_rotor)',
                             {'J_load': j_load, 'N': drive.ratio, 'J_rotor': (j_rotor, 'kg*m^2')}))
    else:
        checks.append(Check(requirement_id=f'motor_{j}_inertia', name='Reflected inertia ratio', required=float(limit),
                            actual=None, unit='', passed=None, detail='rotor inertia not verified: not checked'))
    if sizing.kind == 'prismatic' and sizing.joint.startswith('z') and drive.type == 'leadscrew':
        locks = self_locking(drive)
        checks.append(Check(requirement_id=f'motor_{j}_holding', name='Z axis holds with power off', required=None,
                            actual=None, unit='', passed=True if locks else None,
                            detail='self-locking leadscrew' if locks else
                            'not self-locking: the axis drops when power is lost; fit a brake or a 2 mm lead'))
    bad = [c for c in checks if c.passed is False]
    reason = ('Fails: ' + '; '.join(f'{c.name} {c.actual:.3g} vs {c.required:.3g} {c.unit}'.strip() for c in bad)
              if bad else f'Passes: continuous {margins["continuous"]:.2f}x, peak {margins["peak"]:.2f}x, '
                          f'speed {margins["speed"]:.2f}x')
    if cap.get('speed_assumed'):
        reason = reason.replace(': ', ' (speed assumed (conservative)): ', 1) if bad else reason + ' (speed assumed (conservative))'
    return not bad, margins, checks, reason[:400]


# --- defined motor ------------------------------------------------------------------------------------------------------

def _frame_number(values):
    for v in values:
        if v.key == 'frame':
            m = re.search(r'NEMA\s*(\d+)', v.text, re.I)
            if m:
                return m.group(1)
    return None


# (flange mm, rated torque cap N*m, shaft mm) of standard BLDC servo frames
SERVO_FRAMES = ((40, 0.64, 8), (60, 1.27, 14), (80, 2.4, 19), (100, 7.7, 22), (130, 15.0, 24), (180, 30.0, 35))


def _sv(key, value, unit, status, note='', text=''):
    return SpecValue(key=key, value=value, text=text, unit=unit, status=status, note=note)


def defined_motor(sizing: JointSizing, drive: Drive, family: str) -> MotorChoice:
    """A motor specified by rules when no product passes ("procure to this spec"). Stepper when the holding torque
    needed (max(continuous, peak)/0.7) fits a NEMA 34 stack and the motor speed is <= 1000 rpm; otherwise a BLDC servo
    (DC gearmotor for small wheels). All spec values are `defined`; NEMA frame dimensions are `assumed` from the table."""
    from .sourcing import NEMA_TABLE, nema_values
    ms = motor_side(sizing, drive)
    mc, mp, rpm = ms['continuous_nm'], ms['peak_nm'], ms['speed_rpm']
    need_hold = max(mc, mp) / STEPPER_TORQUE_USE
    rated_rpm = rpm / SPEED_USE_MAX
    j_load = _find(sizing, 'J_load')
    wheel = sizing.kind == 'wheel'
    frame = next((n for n, f in NEMA_TABLE.items() if f['stacks'][-1]['holding_torque_nm'] >= need_hold), None)
    stepper = not wheel and frame is not None and rpm <= 1000.0
    vals = [_sv('required_torque_nm', mc, 'N*m', 'defined', 'motor-side continuous = joint torque / (N eta)'),
            _sv('required_peak_torque_nm', mp, 'N*m', 'defined'),
            _sv('required_speed_rpm', rated_rpm, 'rpm', 'defined', 'motor speed / 0.8')]
    if j_load is not None:
        limit = INERTIA_RATIO_MAX['stepper' if stepper else 'servo']
        vals.append(_sv('min_rotor_inertia_kgm2', j_load / drive.ratio ** 2 / limit, 'kg*m^2', 'defined',
                        f'so the reflected inertia ratio stays <= {limit}'))
    if stepper:
        stack = next(s for s in NEMA_TABLE[frame]['stacks'] if s['holding_torque_nm'] >= need_hold)
        volt = 48.0 if frame == '34' else 24.0
        vals += [_sv('frame', None, '', 'defined', text=f'NEMA {frame}'),
                 _sv('holding_torque_nm', stack['holding_torque_nm'], 'N*m', 'defined', f'{stack["name"]} stack'),
                 _sv('voltage_v', volt, 'V', 'defined'), _sv('current_a', stack['current_a'], 'A', 'defined')]
        vals += nema_values(frame, stack)
        cap = {'continuous_nm': stack['holding_torque_nm'] * STEPPER_TORQUE_USE,
               'peak_nm': stack['holding_torque_nm'] * STEPPER_TORQUE_USE, 'speed_rpm': rated_rpm}
        mtype, label = 'stepper', f'NEMA {frame} stepper ({stack["name"]} stack, {stack["length_mm"]:g} mm), {volt:g} V'
    else:
        volt = 24.0 if wheel and mc <= 1.5 else 48.0
        mtype = 'dc_gear' if wheel and mc <= 1.5 else 'bldc'
        cap = {'continuous_nm': mc, 'peak_nm': mp, 'speed_rpm': rated_rpm}
        amps = max(round(mc * rated_rpm * 2 * math.pi / 60 / (volt * 0.8), 1), 1.0)
        vals += [_sv('voltage_v', volt, 'V', 'defined'), _sv('current_a', amps, 'A', 'defined',
                                                              'P_mech / (V * 0.8)')]
        if mtype == 'bldc':
            fl, _, shaft = next((f for f in SERVO_FRAMES if f[1] >= mc), SERVO_FRAMES[-1])
            vals += [_sv('frame', None, '', 'defined', text=f'{fl} mm flange servo'),
                     _sv('flange_mm', fl, 'mm', 'assumed', 'servo frame rule'),
                     _sv('shaft_d_mm', shaft, 'mm', 'assumed', 'servo frame rule')]
            label = f'BLDC servo, {fl} mm flange, {volt:g} V'
        else:
            vals.append(_sv('frame', None, '', 'defined', text='DC gearmotor, 37 mm'))
            label = f'DC gearmotor, {volt:g} V'
    vals.append(_sv('gear_ratio', drive.ratio, '', 'defined', drive.name))
    prov = provided_side(cap, drive)
    req = {'continuous': sizing.continuous, 'peak': sizing.peak, 'speed': sizing.speed}
    margins = {k: _ratio(prov[k], req[k]) for k in req}
    check = Check(requirement_id=f'motor_{sizing.joint}_defined', name='Defined to meet the requirement',
                  required=req['continuous'], actual=prov['continuous'], unit=sizing.torque_unit, passed=True,
                  detail='specified by rule, not a checked product: procure to this spec',
                  calc=_calc('provided = tau_spec N eta', {'N': drive.ratio, 'eta': drive.efficiency,
                                                           'tau_spec': (cap['continuous_nm'], 'N*m')},
                             prov['continuous'], sizing.torque_unit))
    return MotorChoice(joint=sizing.joint, source='defined', alternative_id=None, name=f'Defined spec: {label}',
                       motor_type=mtype, values=vals, drive=drive, required=req, provided=prov, margins=margins,
                       checks=[check], calcs=motor_side_calcs(sizing, drive))


def motor_mass(choice: MotorChoice) -> float:
    """Motor mass (kg): a verified or assumed mass_kg value, else 0.5 kg."""
    return next((v.value for v in choice.values if v.key == 'mass_kg' and v.value), 0.5)


# --- electronics --------------------------------------------------------------------------------------------------------

def _val(values, key):
    return next((v.value for v in values if v.key == key and v.value is not None), None)


def _rating(ratings, need):
    return next((r for r in ratings if r >= need), None)


def electronics_for(motors: list[MotorChoice], family: str, battery_wh: float | None = None) -> list[ElectronicsItem]:
    """Rule-based `defined` electronics: a driver per motor of the matching type (rated current >= 1.2 x motor current,
    standard ratings; a stepper with a coil rated below STEPPER_BUS_V runs from that bus, as chopper drives do),
    controllers (axes >= motors, 8 axes each at most), power supplies per voltage (W >= 1.3 x sum(V I)); the mobile base
    gets a battery (capacity `battery_wh` or 0.5 h of the sum(V I), discharge power = capacity x 1 C) instead of a
    PSU. A motor without current or voltage uses the default voltage of its type and its driver check is
    `None` (could not be checked)."""
    items, groups = [], {}
    for m in motors:
        cur, coil = _val(m.values, 'current_a'), _val(m.values, 'voltage_v')
        volt = coil or DEFAULT_VOLTAGE_V.get(m.motor_type, 24.0)
        note = 'default for the motor type' if coil is None else ''
        if m.motor_type == 'stepper' and coil is not None and coil < STEPPER_BUS_V:
            volt, note = STEPPER_BUS_V, f'chopper drive bus; the coil is rated {coil:g} V'
        kind = DRIVER_NAME[driver_type(m.motor_type)]
        need = None if cur is None else DRIVER_MARGIN * cur
        rated = None if need is None else (_rating(DRIVER_RATINGS_A, need) or math.ceil(need))
        chk = (Check(requirement_id=f'driver_{m.joint}_current', name='Driver current >= 1.2 x motor current',
                     required=None, actual=None, unit='A', passed=None, detail='motor current unknown')
               if need is None else
               _check(f'driver_{m.joint}_current', 'Driver current >= 1.2 x motor current', need, float(rated), 'A',
                      rated >= need, f'{rated:g} A standard rating for {need:.2f} A needed',
                      'I_driver >= 1.2 I_motor', {'I_motor': (cur, 'A'), 'margin': DRIVER_MARGIN}))
        vals = [SpecValue(key='voltage_v', value=volt, unit='V', status='defined', note=note)]
        if rated:
            vals.append(SpecValue(key='current_a', value=float(rated), unit='A', status='defined'))
        items.append(ElectronicsItem(id=f'driver_{m.joint}'[:63], role='driver', name=f'{kind}, {rated or "?"} A, '
                                     f'{volt:g} V', source='defined', for_joints=[m.joint], values=vals, checks=[chk]))
        groups.setdefault(volt, []).append((m, volt * (cur or 0.0)))
    n = len(motors)
    if not n:
        return items
    qty = math.ceil(n / CONTROLLER_AXES[-1])
    axes = _rating(CONTROLLER_AXES, math.ceil(n / qty))
    iface = 'step/dir' if all(m.motor_type == 'stepper' for m in motors) else 'CAN / RS-485 / PWM'
    items.append(ElectronicsItem(
        id='controller', role='controller', name=f'Motion controller, {axes} axes ({iface})', source='defined',
        qty=qty, for_joints=[m.joint for m in motors],
        values=[SpecValue(key='axes', value=float(axes), status='defined'),
                SpecValue(key='interface', text=iface, status='defined')],
        checks=[_check('controller_axes', 'Controller axes >= motors', float(n), float(axes * qty), '',
                       axes * qty >= n, f'{n} motors, {qty} controller(s)', 'axes = n_motors', {'n_motors': n})]))
    if family == 'mobile_base':
        volt = max(groups, key=lambda v: len(groups[v]))
        power = sum(w for _, w in sum(groups.values(), []))
        wh = float(battery_wh) if battery_wh else math.ceil(0.5 * power)
        cap_w = wh * BATTERY_C_RATE
        items.append(ElectronicsItem(
            id='battery', role='battery', name=f'Battery pack, {volt:g} V, {wh:g} Wh', source='defined',
            for_joints=[m.joint for m in motors],
            values=[SpecValue(key='voltage_v', value=volt, unit='V', status='defined'),
                    SpecValue(key='capacity_wh', value=wh, unit='Wh', status='defined',
                              note='design battery_wh' if battery_wh else '0.5 h of the motor power')],
            checks=[_check('battery_power', 'Battery discharge >= 1.3 x motor power', PSU_MARGIN * power, cap_w, 'W',
                           cap_w >= PSU_MARGIN * power, f'{wh:g} Wh at {BATTERY_C_RATE:g} C',
                           'P_batt = Wh * C', {'Wh': wh, 'C': BATTERY_C_RATE})]))
        return items
    for volt, rows in sorted(groups.items()):
        power = sum(w for _, w in rows)
        need = PSU_MARGIN * power
        watts = _rating(PSU_RATINGS_W, need) or math.ceil(need)
        items.append(ElectronicsItem(
            id=f'psu_{volt:g}v'.replace('.', '_'), role='power_supply', name=f'Power supply {volt:g} V, {watts} W',
            source='defined', for_joints=[m.joint for m, _ in rows],
            values=[SpecValue(key='voltage_v', value=volt, unit='V', status='defined'),
                    SpecValue(key='power_w', value=float(watts), unit='W', status='defined')],
            checks=[_check(f'psu_{volt:g}v_power'.replace('.', '_'), 'PSU watts >= 1.3 x sum(V I)', need,
                           float(watts), 'W', watts >= need, f'sum(V I) = {power:.0f} W', 'W_psu >= 1.3 sum(V I)',
                           {'sum_VI': (power, 'W'), 'margin': PSU_MARGIN})]))
    return items


# --- iteration ----------------------------------------------------------------------------------------------------------

def _signature(choices):
    return tuple((c.joint, c.alternative_id, c.drive.type, round(c.drive.ratio, 4)) for c in choices)


def _extra_masses(version, sizing, choices):
    family = version.robot.family
    extra: dict[str, float] = {}
    by_joint = {s.joint: s for s in sizing}
    for c in choices:
        kg = motor_mass(c) + drive_mass(c.drive, by_joint[c.joint])
        link = parent_link(family, c.joint)
        extra[link] = extra.get(link, 0.0) + kg
    return extra


def iterate_sizing(version: DesignVersion, choose: Callable[[list[JointSizing]], list[MotorChoice]], rounds: int = 3
                   ) -> tuple[list[JointSizing], list[MotorChoice]]:
    """Size, choose, feed the motor + gearbox masses back into the link masses, and repeat until the choices stop
    changing (at most `rounds` sizings). Returns the last sizing and the choices made for it."""
    extra, prev = None, None
    sizing, choices = [], []
    for _ in range(max(1, rounds)):
        sizing = size_joints(version, extra)
        choices = choose(sizing)
        sig = _signature(choices)
        if sig == prev:
            break
        prev, extra = sig, _extra_masses(version, sizing, choices)
    return sizing, choices
