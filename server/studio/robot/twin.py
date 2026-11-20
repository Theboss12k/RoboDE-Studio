"""Digital twin of a studio build: deterministic, stateless, no LLM.

A move is joint targets or a world-frame tool-centre-point target (damped-least-squares IK, tool down, joint clamp).
It is stepped at `limits.dt_s` by `clamp_step` under the build's limits: joint range, speed (never above `max_speed`,
`speed_scale` > 1 asks for more so the limit binds and is reported), an acceleration ramp, and a torque check
(static gravity load plus a point-mass inertia term against `max_torque`; the acceleration is cut when it binds).

These are kinematic and static(+acceleration) calculations in code: not a dynamics simulation, not a hardware test.
Link masses are the design's family masses (point masses, no rotational inertia). The chain model (`chain_for`) is
shared with `packages/core/src/twin.ts`; `scripts/twin_vectors.py` writes shared test vectors for both.
"""
from __future__ import annotations

import math

import numpy as np

from .. import families
from ..physics import motion
from ..physics.statics import G
from ..schemas import (Frame, MotorChoice, StudioBuild, TwinJointLimit, TwinLimitHit, TwinLimits, TwinMoveRequest,
                       TwinMoveResult)
from ..sim.task import GRIP_S, waypoints

MAX_FRAMES = 1500
MAX_TIME_S = 120.0
ACCEL_RAMP_S = 0.2
DESIGN_ACCEL_TIME_S = 0.25
WHEEL_RANGE = 1e6
TOL_M, TOL_ORI, LAMBDA, W_ORI, STEP_MAX = 1e-3, 0.02, 0.05, 0.3, 0.4
TORQUE_ACCEL_FLOOR = 0.05          # torque never cuts the acceleration below this share of max_accel
EPS = 1e-9
DOWN = np.array([0.0, 0.0, -1.0])


# --- chain model (shared with twin.ts) -----------------------------------------------------------------------------

def _el(name, typ, xyz=(0, 0, 0), rpy=(0, 0, 0), axis=(0, 0, 1), lower=0.0, upper=0.0):
    return dict(name=name, type=typ, xyz=[float(v) for v in xyz], rpy=[float(v) for v in rpy],
                axis=[float(v) for v in axis], lower=float(lower), upper=float(upper))


def chain_for(family: str, p) -> dict:
    """Kinematic chain of a family's params in the base frame: elements (fixed | revolute | prismatic, each with
    origin xyz/rpy and axis), the tool offset, the joints IK may use (`active`, indices into the movable joints) and
    mass points (`after` element index, -1 = base; `local` offset in that frame). Movable order = `fam.joints`."""
    fam = families.get(family)
    js = fam.joints(p)
    if family == 'serial_arm':
        els = [_el('j1', 'revolute', axis=(0, 0, 1)), _el('j2', 'revolute', (0, 0, p.base_height_m), axis=(0, 1, 0)),
               _el('j3', 'revolute', (0, 0, p.upper_arm_m), axis=(0, 1, 0)),
               _el('j4', 'revolute', (0, 0, p.forearm_m), axis=(0, 1, 0))]
        if p.dof == 4:
            els.append(_el('wrist', 'fixed', (0, 0, p.wrist_m)))
        elif p.dof == 5:
            els.append(_el('j5', 'revolute', (0, 0, p.wrist_m), axis=(0, 0, 1)))
        else:
            els += [_el('j5', 'revolute', (0, 0, p.wrist_m), axis=(1, 0, 0)), _el('j6', 'revolute', axis=(0, 0, 1))]
        masses = [dict(after=1, local=[0, 0, p.upper_arm_m / 2], mass=p.upper_arm_mass_kg),
                  dict(after=2, local=[0, 0, p.forearm_m / 2], mass=p.forearm_mass_kg),
                  dict(after=3, local=[0, 0, p.wrist_m / 2], mass=p.wrist_mass_kg),
                  dict(after=len(els) - 1, local=[0, 0, p.tool_length_m / 2], mass=p.tool_mass_kg)]
        chain = dict(elements=els, tool=[0, 0, p.tool_length_m], active=[0, 1, 2, 3], masses=masses)
    elif family == 'gantry':
        els = [_el('x_axis', 'prismatic', axis=(1, 0, 0)), _el('y_axis', 'prismatic', axis=(0, 1, 0)),
               _el('z_axis', 'prismatic', (0, 0, p.frame_height_m - p.z_offset_m - p.tool_length_m), axis=(0, 0, -1)),
               _el('tool_down', 'fixed', rpy=(math.pi, 0, 0))]
        if p.tool_yaw:
            els.append(_el('tool_yaw', 'revolute', axis=(0, 0, 1)))
        # additive point masses (inclusive "carries" figures counted once each: a safe, larger inertia)
        masses = [dict(after=0, local=[0, 0, 0], mass=p.x_moving_mass_kg),
                  dict(after=1, local=[0, 0, 0], mass=p.y_moving_mass_kg),
                  dict(after=2, local=[0, 0, 0], mass=p.z_moving_mass_kg + p.tool_mass_kg)]
        chain = dict(elements=els, tool=[0, 0, 0], active=[0, 1, 2], masses=masses)
    elif family == 'mobile_base':
        chain = dict(elements=[], tool=[0, 0, p.deck_height_m], active=[], masses=[])
    else:
        raise ValueError(f'robot family {family!r} is not in v1 of the robot studio')
    mov = [e for e in chain['elements'] if e['type'] != 'fixed']
    if family != 'mobile_base':
        for e, j in zip(mov, js, strict=True):
            e['lower'], e['upper'] = float(j.lower), float(j.upper)
        assert len(mov) == len([j for j in js if j.actuated])
    return chain


def _rpy(r, p, y):
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    return np.array([[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
                     [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
                     [-sp, cp * sr, cp * cr]])


def _axis_rot(axis, a):
    x, y, z = axis
    c, s = math.cos(a), math.sin(a)
    C = 1 - c
    return np.array([[c + x * x * C, x * y * C - z * s, x * z * C + y * s],
                     [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
                     [z * x * C - y * s, z * y * C + x * s, c + z * z * C]])


def chain_frames(chain: dict, q) -> tuple[list[np.ndarray], np.ndarray]:
    """([base frame, frame after element 0, ...], TCP pose), all 4x4 in the base frame."""
    T = np.eye(4)
    out, k = [T], 0
    for el in chain['elements']:
        A = np.eye(4)
        A[:3, :3] = _rpy(*el['rpy'])
        A[:3, 3] = el['xyz']
        T = T @ A
        if el['type'] != 'fixed':
            M = np.eye(4)
            if el['type'] == 'prismatic':
                M[:3, 3] = np.asarray(el['axis'], float) * q[k]
            else:
                M[:3, :3] = _axis_rot(el['axis'], q[k])
            T = T @ M
            k += 1
        out.append(T)
    tcp = np.eye(4)
    tcp[:3, 3] = chain['tool']
    return out, out[-1] @ tcp


def chain_fk(chain: dict, q) -> np.ndarray:
    return chain_frames(chain, q)[1]


def _mass_points(chain, q, payload_kg):
    frames, tcp = chain_frames(chain, q)
    pts = [(m['mass'], (frames[m['after'] + 1] @ [*m['local'], 1.0])[:3]) for m in chain['masses']]
    if payload_kg > 0:
        pts.append((payload_kg, tcp[:3, 3]))
    return pts


def gravity_inertia(chain: dict, q, payload_kg: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """(static gravity torque/force per joint (sign kept), point-mass inertia per joint) at q. Inertia of joint i =
    sum m |d point / d q_i|^2 (kg m^2 or kg)."""
    q = np.asarray(q, float)
    n = q.size
    masses = [m for m, _ in _mass_points(chain, q, payload_kg)]
    J = np.zeros((len(masses), 3, n))
    for i in range(n):
        d = np.zeros(n)
        d[i] = 1e-6
        hi = _mass_points(chain, q + d, payload_kg)
        lo = _mass_points(chain, q - d, payload_kg)
        for k in range(len(masses)):
            J[k, :, i] = (hi[k][1] - lo[k][1]) / 2e-6
    m = np.array(masses)
    return G * (m[:, None] * J[:, 2, :]).sum(0), (m[:, None] * (J ** 2).sum(1)).sum(0)


# --- inverse kinematics (port of families/ik.py with deterministic seeds) -------------------------------------------

def _tool_down_error(T):
    return float(math.acos(float(np.clip(T[:3, 2] @ DOWN, -1.0, 1.0))))


def _residual(chain, q, target, tool_down):
    T = chain_fk(chain, q)
    r = T[:3, 3] - target
    return np.concatenate([r, W_ORI * np.cross(T[:3, 2], DOWN)]) if tool_down else r


def _dls(chain, q, lo, hi, target, active, tool_down, max_iter):
    q = np.clip(q, lo, hi)
    for it in range(max_iter):
        r = _residual(chain, q, target, tool_down)
        if np.linalg.norm(r[:3]) < 1e-5 and (not tool_down or np.linalg.norm(r[3:]) < 1e-5):
            return q, it
        J = np.zeros((r.size, len(active)))
        for k, i in enumerate(active):
            d = np.zeros_like(q)
            d[i] = 1e-6
            J[:, k] = (_residual(chain, q + d, target, tool_down) - _residual(chain, q - d, target, tool_down)) / 2e-6
        step = -J.T @ np.linalg.solve(J @ J.T + LAMBDA ** 2 * np.eye(r.size), r)
        norm = float(np.linalg.norm(step))
        if norm > STEP_MAX:
            step *= STEP_MAX / norm
        q = q.copy()
        q[list(active)] += step
        q = np.clip(q, lo, hi)
    return q, max_iter


def _analytic_seeds(chain, target, n) -> list[np.ndarray]:
    """Closed-form planar tool-down solutions (elbow up first) for an anthropomorphic chain (yaw, three pitch joints,
    wrist offset, tool); empty for other chains. DLS polishes them."""
    els = chain['elements']
    if n < 4 or len(els) < 5 or [e['axis'] for e in els[:4]] != [[0, 0, 1], [0, 1, 0], [0, 1, 0], [0, 1, 0]]:
        return []
    h, L1, L2, wrist = els[1]['xyz'][2], els[2]['xyz'][2], els[3]['xyz'][2], els[4]['xyz'][2]
    x, y, z = target
    dz = z + chain['tool'][2] + wrist - h
    yaw = math.atan2(y, x)
    out = []
    for q1, r in ((yaw, math.hypot(x, y)), (yaw - math.copysign(math.pi, yaw), -math.hypot(x, y))):
        D = max(math.hypot(r, dz), 1e-9)
        phi = math.atan2(r, dz)
        alpha = math.acos(float(np.clip((L1 ** 2 + D ** 2 - L2 ** 2) / (2 * L1 * D), -1, 1)))
        for sgn in (1, -1):
            a2 = phi - sgn * alpha
            a3 = math.atan2(r - L1 * math.sin(a2), dz - L1 * math.cos(a2))
            q = np.zeros(n)
            q[:4] = [q1, a2, a3 - a2, math.pi - a3]
            out.append(q)
    return out


def ik_seeds(chain: dict, q0, lo, hi, target) -> list[np.ndarray]:
    """Deterministic seed list: the current pose, yaw-aligned variants, zero, mid-range, an elbow lattice."""
    q0 = np.clip(np.asarray(q0, float), lo, hi)
    n = q0.size
    seeds = [q0]
    movable = [e for e in chain['elements'] if e['type'] != 'fixed']
    revolute_z = n > 0 and movable[0]['type'] == 'revolute' and movable[0]['axis'] == [0, 0, 1]
    yaw = math.atan2(target[1], target[0])
    if revolute_z:
        for a in (yaw, yaw - math.copysign(math.pi, yaw)):
            s = q0.copy()
            s[0] = a
            seeds.append(np.clip(s, lo, hi))
    seeds += [np.clip(g, lo, hi) for g in _analytic_seeds(chain, target, n)]
    seeds.append(np.clip(np.zeros(n), lo, hi))
    seeds.append((lo + hi) / 2)
    if revolute_z and n >= 3 and movable[1]['type'] == 'revolute' and movable[2]['type'] == 'revolute':
        for a, b in ((0.7, 1.2), (0.7, -1.2), (-0.7, 1.2), (-0.7, -1.2)):
            s = np.zeros(n)
            s[:3] = (yaw, a, b)
            seeds.append(np.clip(s, lo, hi))
    return seeds


def ik_solve(chain: dict, target, q0, tool_down: bool = True, max_iter: int = 120) -> dict:
    """Bounded DLS to `target` (base frame). Tool-down is tried first and dropped when it cannot be met.
    Returns dict(q, pos_error, ori_error, ok, iterations, tool_down)."""
    movable = [e for e in chain['elements'] if e['type'] != 'fixed']
    lo = np.array([e['lower'] for e in movable], float)
    hi = np.array([e['upper'] for e in movable], float)
    target = np.asarray(target, float)
    active = chain['active']
    seeds = ik_seeds(chain, q0, lo, hi, target)
    best = None
    for use_tool in ([True, False] if tool_down else [False]):
        for s in seeds:
            q, it = _dls(chain, s, lo, hi, target, active, use_tool, max_iter)
            T = chain_fk(chain, q)
            pos, ori = float(np.linalg.norm(T[:3, 3] - target)), _tool_down_error(T)
            good = pos < TOL_M and (not use_tool or ori < TOL_ORI)
            cand = dict(q=q, pos_error=pos, ori_error=ori, ok=bool(pos < TOL_M), iterations=it, tool_down=use_tool)
            if best is None or (good, -pos) > (best[0], -best[1]['pos_error']):
                best = (good, cand)
            if good:
                return cand
        if best[0]:
            break
    return best[1]


# --- stepping ------------------------------------------------------------------------------------------------------

def clamp_step(q, target, dt, limits, qd, speed_scale=1.0, accel_cap=None):
    """One step of every joint toward `target` under `limits` (list of TwinJointLimit-like, same order as q).
    Returns (q_next, qd_next, hits) with hits = [(joint index, 'position'|'speed'|'acceleration'|'torque')].

    Per joint: target clamped to [lower, upper] ('position'); requested cruise speed max_speed*s, never above max_speed
    ('speed' when s > 1); requested acceleration max_accel*s^2, never above max_accel ('acceleration' when s > 1) nor
    above `accel_cap[i]` (the torque-limited value, 'torque'); velocity follows the stop-in-time curve
    sqrt(2 a |error|), changes by at most a*dt per step, and the position stops exactly on the target.
    `speed_scale` is a number or one per joint."""
    n = len(q)
    scales = [float(speed_scale)] * n if np.isscalar(speed_scale) else [float(s) for s in speed_scale]
    qn, vn, hits = [], [], []
    for i in range(n):
        L = limits[i]
        lo, hi, vmax, amax = float(L.lower), float(L.upper), float(L.max_speed), float(L.max_accel)
        s = scales[i]
        tgt = min(max(float(target[i]), lo), hi)
        if abs(tgt - float(target[i])) > EPS:
            hits.append((i, 'position'))
        a_req = amax * s * s
        cap = math.inf if accel_cap is None else float(accel_cap[i])
        a_use = min(a_req, amax, cap)
        e = tgt - float(q[i])
        v_des = math.copysign(min(vmax * s, math.sqrt(2 * a_use * abs(e)), abs(e) / dt), e) if abs(e) > 0 else 0.0
        if abs(v_des) > vmax * (1 + EPS):
            v_des = math.copysign(vmax, v_des)
            hits.append((i, 'speed'))
        dv = v_des - float(qd[i])
        if abs(dv) > a_use * dt + EPS:
            dv = math.copysign(a_use * dt, dv)
            if cap < min(a_req, amax) - EPS:
                hits.append((i, 'torque'))
            elif a_req > amax + EPS:
                hits.append((i, 'acceleration'))
        v = min(max(float(qd[i]) + dv, -vmax), vmax)
        x = float(q[i]) + v * dt
        if (x - tgt) * (float(q[i]) - tgt) <= 0 or abs(tgt - x) < 1e-9:
            x, v = tgt, 0.0
        elif x < lo or x > hi:
            x, v = min(max(x, lo), hi), 0.0
            hits.append((i, 'position'))
        qn.append(x)
        vn.append(v)
    return np.array(qn), np.array(vn), hits


def torque_use(gravity, inertia, qdd, max_torque):
    """(|gravity| + |inertia * qdd|) / max_torque per joint (1.0 = at the limit); inf when max_torque <= 0."""
    gravity, inertia, qdd, mt = (np.asarray(v, float) for v in (gravity, inertia, qdd, max_torque))
    need = np.abs(gravity) + np.abs(inertia * qdd)
    return np.where(mt > 0, need / np.where(mt > 0, mt, 1.0), np.inf)


def accel_cap_from_torque(gravity, inertia, max_torque, max_accel):
    """Largest joint acceleration the torque allows: (max_torque - |gravity|) / inertia, never below 5 % of max_accel."""
    inertia = np.maximum(np.asarray(inertia, float), 1e-9)
    room = (np.asarray(max_torque, float) - np.abs(gravity)) / inertia
    return np.maximum(room, TORQUE_ACCEL_FLOOR * np.asarray(max_accel, float))


# --- limits --------------------------------------------------------------------------------------------------------

def _gripper_limit(gripper: dict) -> TwinJointLimit:
    g = gripper or {}
    stroke = float(g.get('stroke_m', g.get('stroke', g.get('opening_m', 0.08))))
    speed = float(g.get('speed_m_s', g.get('max_speed', 0.1)))
    return TwinJointLimit(joint='gripper', kind='prismatic', lower=0.0, upper=stroke, max_speed=speed,
                          max_accel=float(g.get('accel_m_s2', g.get('max_accel', speed / ACCEL_RAMP_S))),
                          max_torque=float(g.get('force_n', g.get('max_torque', 40.0))))


def twin_limits(version, motors: list[MotorChoice], gripper: dict | None) -> TwinLimits:
    """Per-joint limits of the build: URDF range, speed = min(design rating, motor speed through the drive), peak
    torque of the chosen motor through the drive (design rating when no motor is chosen for the joint), acceleration
    from the design's (scaled with speed, at most speed / accel_ramp_s)."""
    fam = families.get(version.robot.family)
    if version.robot.family not in ('serial_arm', 'gantry', 'mobile_base'):
        raise ValueError(f'robot family {version.robot.family!r} is not in v1 of the robot studio')
    p = fam.parse(version.robot.params)
    js = fam.joints(p)
    design_acc = (list(fam.joint_accels(p)) if hasattr(fam, 'joint_accels')
                  else [j.max_velocity / DESIGN_ACCEL_TIME_S for j in js])
    by = {m.joint: m for m in motors}
    out = []
    for j, a0 in zip(js, design_acc, strict=True):
        m = by.get(j.name)
        v = j.max_velocity
        torque = j.max_effort
        if m is not None:
            if m.provided.get('speed', 0) > 0:
                v = min(v, float(m.provided['speed']))
            if m.provided.get('peak', 0) > 0:
                torque = float(m.provided['peak'])
        acc = min(a0 * v / max(j.max_velocity, 1e-9), v / ACCEL_RAMP_S)
        wheel = not j.actuated
        out.append(TwinJointLimit(joint=j.name, kind='wheel' if wheel else ('prismatic' if j.type == 'prismatic' else 'revolute'),
                                  lower=-WHEEL_RANGE if wheel else j.lower, upper=WHEEL_RANGE if wheel else j.upper,
                                  max_speed=float(v), max_accel=float(acc), max_torque=float(torque)))
    return TwinLimits(joints=out, gripper=_gripper_limit(gripper) if gripper else None)


# --- the twin ------------------------------------------------------------------------------------------------------

class _Twin:
    """State of one replay: joint vector (movable joints, wheels, then the gripper), time, frames, hits, peak use."""

    def __init__(self, build: StudioBuild, version, speed_scale: float):
        fam_name = version.robot.family
        self.fam = families.get(fam_name)
        if fam_name not in ('serial_arm', 'gantry', 'mobile_base'):
            raise ValueError(f'robot family {fam_name!r} is not in v1 of the robot studio')
        self.p = self.fam.parse(version.robot.params)
        self.chain = chain_for(fam_name, self.p)
        self.limits_all = build.twin_limits
        by = {l.joint: l for l in self.limits_all.joints}
        derived = {l.joint: l for l in twin_limits(version, [], None).joints}
        joints = self.fam.joints(self.p)
        self.names = [j.name for j in joints]
        self.limits = [by.get(n, derived[n]) for n in self.names]
        self.nm = len([j for j in joints if j.actuated])         # chain joints come first
        self.gi = None
        if self.limits_all.gripper is not None:
            self.gi = len(self.names)
            self.names.append('gripper')
            self.limits.append(self.limits_all.gripper)
        self.n = len(self.names)
        self.dt = float(self.limits_all.dt_s)
        self.s = float(speed_scale)
        b = version.robot.base
        c, s_ = math.cos(b.yaw), math.sin(b.yaw)
        self.base_T = np.array([[c, -s_, 0, b.position[0]], [s_, c, 0, b.position[1]], [0, 0, 1, b.position[2]],
                                [0, 0, 0, 1.0]])
        self.q = np.zeros(self.n)
        self.qd = np.zeros(self.n)
        self.t = 0.0
        self.rows: list[tuple] = []
        self.hits: list[TwinLimitHit] = []
        self._seen: set = set()
        self.peak = np.zeros(self.nm)
        self.max_torque = np.array([l.max_torque for l in self.limits[:self.nm]])
        self.max_accel = np.array([l.max_accel for l in self.limits[:self.nm]])

    # frames
    def tcp_world(self, q):
        if not self.nm:
            return self.base_T[:3, 3] + [0, 0, getattr(self.p, 'deck_height_m', 0.0)]
        return (self.base_T @ np.append(chain_fk(self.chain, q[:self.nm])[:3, 3], 1.0))[:3]

    def record(self, phase, holding=''):
        self.rows.append((self.t, self.q.copy(), self.tcp_world(self.q), phase, holding))

    def hit(self, joint, limit, detail=''):
        if (joint, limit) not in self._seen:
            self._seen.add((joint, limit))
            self.hits.append(TwinLimitHit(t=round(self.t, 6), joint=joint, limit=limit, detail=detail[:300]))

    def set_q(self, q):
        self.q = np.asarray(q, float).copy()
        self.qd = np.zeros(self.n)

    # motion
    def scales(self, target):
        """Per-joint speed scales that make every joint finish together (trapezoid time scaling)."""
        s_eff = min(self.s, 1.0)
        T = []
        for i, L in enumerate(self.limits):
            d = abs(min(max(float(target[i]), L.lower), L.upper) - self.q[i])
            T.append(motion.trapezoid(d, L.max_speed * s_eff, min(L.max_accel * s_eff ** 2, L.max_accel))['t_total']
                     if d > 1e-9 else 0.0)
        lead = max(T) if T else 0.0
        if lead <= 0:
            return [self.s] * self.n
        return [self.s if t >= lead * (1 - 1e-9) else s_eff * t / lead for t in T]

    def segment(self, target, phase='', holding='', payload=0.0, record_first=False) -> bool:
        """Move to `target` (joint vector) until settled; returns whether every joint reached its target."""
        target = np.asarray(target, float)
        for i, L in enumerate(self.limits):
            if abs(min(max(target[i], L.lower), L.upper) - target[i]) > EPS:
                self.hit(self.names[i], 'position', f'target {target[i]:.4f} is outside {L.lower:.4f}..{L.upper:.4f}')
        clipped = np.array([min(max(target[i], L.lower), L.upper) for i, L in enumerate(self.limits)])
        if record_first:
            self.record(phase, holding)
        scales = self.scales(target)
        t_end = self.t + MAX_TIME_S
        while self.t < t_end:
            if np.all(np.abs(clipped - self.q) < 1e-9) and np.all(np.abs(self.qd) < 1e-9):
                break
            cap = None
            if self.nm:
                g, inertia = gravity_inertia(self.chain, self.q[:self.nm], payload)
                cap = np.concatenate([accel_cap_from_torque(g, inertia, self.max_torque, self.max_accel),
                                      np.full(self.n - self.nm, math.inf)])
            qn, vn, hits = clamp_step(self.q, target, self.dt, self.limits, self.qd, scales, cap)
            self.t += self.dt
            for i, kind in hits:
                self.hit(self.names[i], kind, self._detail(i, kind))
            if self.nm:
                g2, i2 = gravity_inertia(self.chain, qn[:self.nm], payload)
                a_lim = np.minimum(self.max_accel, cap[:self.nm])     # the stop-on-target snap is not a real acceleration
                qdd = np.clip((vn[:self.nm] - self.qd[:self.nm]) / self.dt, -a_lim, a_lim)
                use = torque_use(g2, i2, qdd, self.max_torque)
                self.peak = np.maximum(self.peak, use)
                for k in np.nonzero(use > 1 + 1e-9)[0]:
                    self.hit(self.names[k], 'torque', f'needs {use[k]:.2f} x the available torque')
            self.q, self.qd = qn, vn
            self.record(phase, holding)
        return bool(np.all(np.abs(clipped - self.q) < 1e-6)) and bool(np.all(np.abs(clipped - target) < EPS))

    def _detail(self, i, kind):
        L = self.limits[i]
        return {'position': f'range {L.lower:.4f}..{L.upper:.4f} reached', 'speed': f'speed limit {L.max_speed:.4f} binds',
                'acceleration': f'acceleration limit {L.max_accel:.4f} binds',
                'torque': 'torque limits the acceleration'}[kind]

    def dwell(self, d, phase='', holding=''):
        n = max(1, math.ceil(d / self.dt - 1e-9))
        for _ in range(n):
            self.t += d / n
            self.record(phase, holding)

    # results
    def frames(self) -> list[Frame]:
        rows = self.rows
        if len(rows) > MAX_FRAMES:
            rows = [rows[i] for i in np.unique(np.linspace(0, len(rows) - 1, MAX_FRAMES).astype(int))]
        return [Frame(t=round(float(t), 6), joints={n: float(v) for n, v in zip(self.names, q)}, holding=h,
                      tcp=(float(x[0]), float(x[1]), float(x[2])), phase=ph) for t, q, x, ph, h in rows]

    def result(self, reached, ik_error=None) -> TwinMoveResult:
        torque = {n: float(v) for n, v in zip(self.names[:self.nm], self.peak)}
        return TwinMoveResult(frames=self.frames(), reached=bool(reached), duration_s=float(self.rows[-1][0]),
                              limits=self.hits, torque_use=torque, ik_error_m=ik_error)


def _q_vector(tw: _Twin, q0: dict[str, float]):
    q = np.array([float(q0.get(n, 0.0)) for n in tw.names])
    return np.array([min(max(v, l.lower), l.upper) for v, l in zip(q, tw.limits)])


def _to_base(tw: _Twin, xyz):
    return (np.linalg.inv(tw.base_T) @ np.append(np.asarray(xyz, float), 1.0))[:3]


def move(build: StudioBuild, version, req: TwinMoveRequest) -> TwinMoveResult:
    """Joint targets or a world-frame cartesian target (tool down) from `req.q0`, under the build's limits."""
    tw = _Twin(build, version, req.speed_scale)
    tw.set_q(_q_vector(tw, req.q0))
    tgt = tw.q.copy()
    for name, v in (req.target.joints or {}).items():
        if name not in tw.names:
            raise ValueError(f'unknown joint {name!r}')
        tgt[tw.names.index(name)] = float(v)
    if req.target.gripper is not None:
        if tw.gi is None:
            raise ValueError('this build has no gripper joint')
        tgt[tw.gi] = float(req.target.gripper)
    ik_err = None
    if req.target.cartesian is not None:
        if not tw.nm:
            tw.record('move')
            tw.hit('tcp', 'ik', 'this robot has no manipulator joints for a cartesian target')
            return tw.result(False, None)
        out = ik_solve(tw.chain, _to_base(tw, req.target.cartesian), tw.q[:tw.nm])
        ik_err = out['pos_error']
        if not out['ok']:
            tw.record('move')
            tw.hit('tcp', 'ik', f'target is {ik_err * 1000:.1f} mm from the closest reachable pose')
            return tw.result(False, ik_err)
        tgt[:tw.nm] = out['q']
    tw.record('move')
    reached = tw.segment(tgt, 'move')
    return tw.result(reached, ik_err)


def run_task(build: StudioBuild, version, speed_scale: float) -> TwinMoveResult:
    """The design's waypoints (`sim.task.waypoints`) replayed under the build's limits; phases come from the task.
    A robot without manipulator joints (mobile base) replays the design's own frames with the time stretched when
    the build's drive limits are lower than the design's."""
    tw = _Twin(build, version, speed_scale)
    spec = version.spec
    wps = waypoints(spec, version.scene, version.robot.base)
    objs = {o.id: o for o in spec.objects}
    if not tw.nm:
        return _replay_design(tw, version, speed_scale)
    moving = [w for w in wps if w.xyz is not None and w.kind != 'drive']
    start = np.zeros(tw.n)
    lo = np.array([l.lower for l in tw.limits])
    hi = np.array([l.upper for l in tw.limits])
    if tw.gi is not None:
        start[tw.gi] = hi[tw.gi]
    ik_worst = 0.0
    reached = True
    if moving:
        out = ik_solve(tw.chain, _to_base(tw, moving[0].xyz), np.zeros(tw.nm))
        start[:tw.nm] = out['q']
    tw.set_q(np.clip(start, lo, hi))
    home_q = tw.q.copy()
    holding, mass = '', 0.0
    tw.record(wps[0].phase if wps else '', '')
    for w in wps:
        if w.kind == 'drive':
            continue
        if w.kind == 'wait':
            tw.dwell(w.dwell_s, w.phase, holding)
            continue
        tgt = tw.q.copy()
        if w.kind == 'home':
            tgt = home_q.copy()
        elif w.xyz is not None:
            out = ik_solve(tw.chain, _to_base(tw, w.xyz), tw.q[:tw.nm])
            ik_worst = max(ik_worst, out['pos_error'])
            if not out['ok']:
                reached = False
                tw.hit(w.name, 'ik', f'{w.name}: {out["pos_error"] * 1000:.1f} mm from the closest reachable pose')
                continue
            tgt[:tw.nm] = out['q']
        reached &= tw.segment(tgt, w.phase, holding, mass)
        if w.kind in ('grasp', 'release'):
            if tw.gi is not None:
                o = objs.get(w.object)
                grip = hi[tw.gi] if w.kind == 'release' else min(max(o.size_m[0] if o else 0.0, lo[tw.gi]), hi[tw.gi])
                g = tw.q.copy()
                g[tw.gi] = grip
                reached &= tw.segment(g, w.phase, holding, mass)
            tw.dwell(w.dwell_s or GRIP_S, w.phase, holding)
            holding = w.object if w.kind == 'grasp' else ''
            mass = (objs[holding].mass_kg if holding in objs else spec.physics_inputs.object_mass_kg) if holding else 0.0
            tw.rows[-1] = (*tw.rows[-1][:4], holding)
        elif w.kind == 'place' and w.dwell_s:
            tw.dwell(w.dwell_s, w.phase, holding)
    return tw.result(reached, ik_worst if moving else None)


def _replay_design(tw: _Twin, version, speed_scale: float) -> TwinMoveResult:
    """Drive-only robots: the design's frames with the time scaled by the slower of the build's and design's wheel
    speed (and by 1 / speed_scale, at most the limits)."""
    design = families.get(version.robot.family)
    design_v = max((j.max_velocity for j in design.joints(tw.p)), default=1.0)
    build_v = min((l.max_speed for l in tw.limits), default=design_v)
    k = max(1.0, design_v / max(build_v, 1e-9)) / min(speed_scale, 1.0) if speed_scale < 1 else max(
        1.0, design_v / max(build_v, 1e-9))
    if build_v < design_v * (1 - 1e-9):
        tw.hit('drive', 'speed', f'build wheel speed {build_v:.3f} is below the design {design_v:.3f}')
    frames = [f.model_copy(update={'t': f.t * k, 'joints': {n: f.joints.get(n, 0.0) for n in tw.names}})
              for f in version.sim.frames]
    return TwinMoveResult(frames=frames[:MAX_FRAMES], reached=True, duration_s=frames[-1].t if frames else 0.0,
                          limits=tw.hits, torque_use={})
