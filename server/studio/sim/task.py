"""Task simulation: waypoints from the spec, IK at every waypoint, joint-space motion with per-joint trapezoid
limits, base driving along the path for mobile robots, then checks against the requirements.

Simplifications (also listed in docs): no collision-free planning (collisions are only reported), the arm starts
each run at the first waypoint's pose, a heading change of a mobile base is rate limited but costs no extra time,
grasping and releasing take GRIP_S each.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .. import families
from ..physics import motion
from ..physics.statics import G
from ..schemas import (BasePose, Calc, Check, Frame, Quantity, Requirement, RobotConcept, SceneSpec, SimEvent,
                       SimResult, TechnicalSpec)
from . import collision, report

GRIP_S = 0.1                  # time to close or open the gripper
ACCEL_TIME_S = 0.25           # default joint acceleration: full speed in this time
YAW_RATE = 2.0                # rad/s, heading change of a mobile base
MAX_FRAMES = 1500
CONTACT = ('grasp', 'place', 'release')


@dataclass
class Waypoint:
    name: str
    xyz: tuple[float, float, float] | None     # world frame; None = no position (wait, home, drive to path end)
    kind: str                                  # approach|grasp|lift|carry|place|release|retreat|home|drive|wait|rotate
    object: str = ''
    phase: str = ''
    dwell_s: float = 0.0
    limit_s: float = 0.0                       # carry (move): optional upper time for the move; rotate: for the check
    turns: float = 0.0                         # rotate only
    rpm: float = 0.0
    ramp_s: float = 0.0
    axis: str = 'tool_yaw'
    index: int = -1                            # index of the action in spec.actions


def waypoints(spec: TechnicalSpec, scene: SceneSpec, base: BasePose) -> list[Waypoint]:
    """Ordered waypoints of the task in the world frame (see docs/CONTRACTS.md)."""
    pi = spec.physics_inputs
    objs = {o.id: o for o in spec.objects}
    marks = {m.id: m for m in spec.markers}
    attached = next((o.id for o in spec.objects if o.attached), '')
    out, carried_z, held = [], None, attached
    has_pick = any(a.verb == 'pick' for a in spec.actions)
    for ai, a in enumerate(spec.actions):
        m = marks.get(a.target)
        o = objs.get(a.object)
        label = a.description or f'{a.verb} {a.object} {a.target}'.strip()
        if a.verb == 'pick' and o is not None and not m:
            m = marks.get(o.start_marker)
        if a.verb in ('pick', 'place', 'press', 'push') and m is not None:
            size_z = o.size_m[2] if o else pi.object_size_m[2]
            top = (o.grip if o else pi.grip) in ('vacuum', 'magnetic')
            x, y, z = m.position
            zg = z + (size_z if top else size_z / 2) if a.verb in ('pick', 'place') else z
            za = zg + pi.approach_height_m
            tag = f'{a.verb}_{a.object or m.id}_{m.id}'
            oid = a.object
            if a.verb == 'pick':
                out += [Waypoint(f'{tag}_approach', (x, y, za), 'approach', '', label),
                        Waypoint(f'{tag}_grasp', (x, y, zg), 'grasp', oid, label, GRIP_S),
                        Waypoint(f'{tag}_lift', (x, y, za), 'lift', oid, label)]
                carried_z, held = za, oid
            elif a.verb == 'place':
                zc = max(carried_z, za) if carried_z is not None else za
                out += [Waypoint(f'{tag}_carry', (x, y, zc), 'carry', oid, label),
                        Waypoint(f'{tag}_approach', (x, y, za), 'approach', oid, label),
                        Waypoint(f'{tag}_place', (x, y, zg), 'place', oid, label),
                        Waypoint(f'{tag}_release', (x, y, zg), 'release', oid, label, GRIP_S),
                        Waypoint(f'{tag}_retreat', (x, y, za), 'retreat', '', label)]
                carried_z, held = None, ''
            else:
                out += [Waypoint(f'{tag}_approach', (x, y, za), 'approach', '', label),
                        Waypoint(f'{tag}_place', (x, y, zg), 'place', '', label, a.duration_s),
                        Waypoint(f'{tag}_retreat', (x, y, za), 'retreat', '', label)]
        elif a.verb == 'move' and m is not None:
            # Never to the surface: carry height above the marker (object height when one is or will be held).
            ob = o or objs.get(held)                       # the held (attached) object sets the carry height
            size_z = ob.size_m[2] if ob else (pi.object_size_m[2] if has_pick else 0.0)
            x, y, z = m.position
            out.append(Waypoint(f'move_{m.id}', (x, y, z + size_z + pi.approach_height_m), 'carry', held, label,
                                limit_s=a.duration_s, index=ai))
        elif a.verb == 'rotate':
            out.append(Waypoint(f'rotate_{ai}', None, 'rotate', held, label, limit_s=a.duration_s, turns=a.turns,
                                rpm=a.rpm, ramp_s=a.ramp_s, axis=a.axis, index=ai))
        elif a.verb == 'drive':
            out.append(Waypoint(f'drive_{m.id if m else "end"}', tuple(m.position) if m else None, 'drive', '', label))
        elif a.verb == 'home':
            out.append(Waypoint('home', None, 'home', '', label))
        elif a.verb in ('wait', 'inspect'):
            out.append(Waypoint(f'{a.verb}_{len(out)}', None, 'wait', '', label, a.duration_s))
    return out


def _times(T, dt, n):
    """Sample times of motion.sample: every dt, the last sample at T."""
    t = np.minimum(np.arange(n) * dt, T)
    t[-1] = T
    return t


def _heading(a, b):
    return math.atan2(b[1] - a[1], b[0] - a[0])


def _wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


TOOL_PATH_SAMPLES = 20       # segments of a move's tool path sampled for its speed and acceleration bounds

class _Run:
    """Timeline builder: rows of (t, q, base pose, wheel angles, holding, phase, boxes to ignore)."""

    def __init__(self, dt, q, pose, bp, slow=1.0):
        self.dt, self.q, self.pose, self.bp, self.slow = dt, np.array(q, float), pose, bp, slow
        self.dwelled = 0.0                       # total standing-still time (not stretched)
        self.t, self.holding, self.phase, self.skip = 0.0, '', '', frozenset()
        self.wheels = [0.0, 0.0]
        self.rows = []
        self.driven = 0.0
        self.tcp = None                          # (fk position fn, max tool speed, max tool accel) at this run's stretch
        self.add()

    def _tool_limits(self, dq, vf, af, k=1.0):
        """vf, af (path-parameter speed and acceleration of a joint-linear move by dq) lowered so the tool centre point
        stays within its speed and acceleration limits (divided by k and k^2 for a stretched timed move). The tool
        path's first and second derivatives along the move (FK sampled at 21 points) bound the tool speed by
        |p'| s' and the acceleration by |p'| s'' + |p''| s'^2."""
        if self.tcp is None:
            return vf, af
        fk, vmax, amax = self.tcp
        n = TOOL_PATH_SAMPLES
        pts = np.array([fk(self.q + u * dq) for u in np.linspace(0.0, 1.0, n + 1)])
        d1 = np.linalg.norm(np.diff(pts, axis=0), axis=1) * n               # |p'| on each of the n segments
        if d1.max() < 1e-9:
            return vf, af
        l1 = np.maximum(d1[:-1], d1[1:])                                     # |p'| and |p''| at the inner points
        l2 = np.linalg.norm(np.diff(pts, n=2, axis=0), axis=1) * n * n
        if vmax:
            vf = min(vf, vmax / k / float(d1.max()))
        if amax:
            a = amax / k ** 2
            if l2.max() * vf * vf > 0.5 * a:      # leave at least half the budget for speeding up along the path
                vf = math.sqrt(0.5 * a / float(l2.max()))
            # at every point of the path: |p'| s'' + |p''| s'^2 <= a (both terms at the same point)
            af = min(af, float(np.min((a - l2 * vf * vf) / np.maximum(l1, 1e-9))), a / float(d1.max()))
        return vf, af

    def add(self, q=None):
        self.rows.append(dict(t=self.t, q=np.array(self.q if q is None else q, float), pose=self.pose,
                              wheels=tuple(self.wheels), holding=self.holding, phase=self.phase, skip=self.skip))

    def dwell(self, d, holding=None):
        """Stand still for d seconds; `holding` (when given) applies from the last frame of the dwell."""
        self.dwelled += d
        n = max(1, math.ceil(d / self.dt))
        for i in range(n):
            self.t += d / n
            if holding is not None and i == n - 1:
                self.holding = holding
            self.add()

    def move_time(self, qt, vel, acc):
        """Time of move(qt, vel, acc) without playing it."""
        dq = np.asarray(qt, float) - self.q
        mv = np.abs(dq) > 1e-9
        if not mv.any():
            return 0.0
        vf, af = self._tool_limits(dq, min(vel[mv] / np.abs(dq[mv])), min(acc[mv] / np.abs(dq[mv])))
        return float(motion.trapezoid(1.0, vf, af)['t_total'])

    def rotate(self, j, travel, omega, alpha):
        """Turn joint j by the signed `travel` (rad) with a trapezoid profile (peak omega, acceleration alpha); no
        unwinding. A fixed-time segment: it is never stretched. Returns its duration."""
        d, sign = abs(travel), 1.0 if travel >= 0 else -1.0
        f = motion.sample(d, omega, alpha, self.dt)
        T = motion.trapezoid(d, omega, alpha)['t_total']
        qj, t0 = float(self.q[j]), self.t
        for fk, tk in zip(f[1:], _times(T, self.dt, len(f))[1:]):
            self.t = t0 + tk
            q = self.q.copy()
            q[j] = qj + sign * fk
            self.add(q)
        self.q[j] = qj + travel
        self.dwelled += T
        return T

    def move(self, qt, vel, acc, k=1.0):
        dq = np.asarray(qt, float) - self.q
        mv = np.abs(dq) > 1e-9
        if not mv.any():
            return
        vf, af = self._tool_limits(dq, min(vel[mv] / np.abs(dq[mv])), min(acc[mv] / np.abs(dq[mv])), k)
        f = motion.sample(1.0, vf, af, self.dt)
        T = motion.trapezoid(1.0, vf, af)['t_total']
        q0, t0 = self.q.copy(), self.t
        for fk, tk in zip(f[1:], _times(T, self.dt, len(f))[1:]):
            self.t = t0 + tk
            self.add(q0 + fk * dq)
        self.q = np.array(qt, float)

    def drive(self, pts):
        """Drive the polyline `pts` (list of xy) with one trapezoid profile; the heading follows the segments."""
        pts = [np.asarray(p, float) for p in pts]
        seg = np.array([np.linalg.norm(b - a) for a, b in zip(pts, pts[1:])])
        cum = np.concatenate([[0.0], np.cumsum(seg)])
        D = cum[-1]
        if D < 1e-9:
            return
        bp = self.bp
        v, a = bp.max_speed_m_s / self.slow, bp.max_accel_m_s2 / self.slow ** 2
        s = motion.sample(D, v, a, self.dt)
        T = motion.trapezoid(D, v, a)['t_total']
        times = _times(T, self.dt, len(s))
        x, y, yaw = self.pose
        t0, prev = self.t, 0.0
        for k in range(1, len(s)):
            i = min(int(np.searchsorted(cum, s[k], side='left')) - 1, len(seg) - 1)
            i = max(i, 0)
            u = (s[k] - cum[i]) / max(seg[i], 1e-12)
            pos = pts[i] + u * (pts[i + 1] - pts[i])
            dts = times[k] - times[k - 1]
            dyaw = float(np.clip(_wrap(_heading(pts[i], pts[i + 1]) - yaw), -YAW_RATE * dts, YAW_RATE * dts))
            yaw += dyaw
            ds = s[k] - prev
            prev = s[k]
            self.wheels[0] += (ds - dyaw * bp.track_m / 2) / bp.wheel_radius_m
            self.wheels[1] += (ds + dyaw * bp.track_m / 2) / bp.wheel_radius_m
            self.pose = (float(pos[0]), float(pos[1]), float(yaw))
            self.t = t0 + times[k]
            self.add()
        self.driven += D


def simulate(spec: TechnicalSpec, requirements: list[Requirement], scene: SceneSpec, concept: RobotConcept,
             params: dict | None = None, dt: float = 0.04, _slow: float = 1.0, _fastest: float | None = None) -> SimResult:
    """Simulate the task. When the fastest feasible run is shorter than the cycle time, the run is repeated with the
    motion stretched uniformly (speeds / k, accelerations / k^2) so that it fills the cycle; `_slow` is that k."""
    fam = families.get(concept.family)
    p = fam.parse(concept.params if params is None else params)
    joints = fam.joints(p)
    act = [j for j in joints if j.actuated]
    wheel_names = [j.name for j in joints if not j.actuated]
    caps = fam.capabilities(p)
    bp = fam.base_params(p) if fam.mobile else None
    vel = np.array([j.max_velocity for j in act])
    acc = (np.array(fam.joint_accels(p)) if hasattr(fam, 'joint_accels') else vel / ACCEL_TIME_S) if act else vel
    lo = np.array([j.lower for j in act])
    hi = np.array([j.upper for j in act])
    wps = waypoints(spec, scene, concept.base)
    objs = {o.id: o for o in spec.objects}
    marks = {m.id: m for m in spec.markers}
    events: list[SimEvent] = []

    path = [np.array(pt[:2], float) for pt in spec.locomotion.path]
    mobile_path = bool(fam.mobile and spec.locomotion.required and len(path) >= 2)
    bpos = np.array(concept.base.position, float)
    if mobile_path:
        pose0 = (float(path[0][0]), float(path[0][1]), _heading(path[0], path[1]))
        bz = 0.0
    else:
        pose0 = (float(bpos[0]), float(bpos[1]), float(concept.base.yaw))
        bz = float(bpos[2])

    def stop_for(xy, cur):
        if not mobile_path:
            return 0
        d = [np.linalg.norm(path[i] - np.asarray(xy[:2])) for i in range(cur, len(path))]
        return cur + int(np.argmin(d))

    def stop_pose(i):
        h = _heading(path[i], path[i + 1]) if i == 0 else _heading(path[i - 1], path[i])
        return (float(path[i][0]), float(path[i][1]), h)

    def to_base(xyz, pose):
        c, s = math.cos(-pose[2]), math.sin(-pose[2])
        d = np.array(xyz, float) - np.array([pose[0], pose[1], bz])
        return np.array([c * d[0] - s * d[1], s * d[0] + c * d[1], d[2]])

    boxes = [b for b in scene.boxes if b.collides]

    def rest_box(xyz):
        if not boxes:
            return None
        d = [collision.point_box_distance([xyz], b.center, b.size, b.yaw)[0] for b in boxes]
        return boxes[int(np.argmin(d))].id

    # --- initial pose: the arm starts at the first waypoint, the base at the first stop ---
    q0 = np.clip(np.zeros(len(act)), lo, hi) if act else np.zeros(0)
    first = next((w for w in wps if w.xyz is not None and w.kind != 'drive'), None)
    attached = next((o for o in spec.objects if o.attached), None)
    if attached is not None and attached.start_marker in marks:     # starts where the attached object starts
        ax, ay, az = marks[attached.start_marker].position
        first = Waypoint('start', (ax, ay, az + attached.size_m[2] + spec.physics_inputs.approach_height_m), 'carry')
    if act and first is not None:
        pose_first = stop_pose(stop_for(first.xyz, 0)) if mobile_path else pose0
        q0 = fam.ik(p, to_base(first.xyz, pose_first)).q
    run = _Run(dt, q0, pose0, bp, _slow)
    if attached is not None:
        run.holding = attached.id                # held by the tool from t=0: its mass is in every load
    run.rows[0]['holding'] = run.holding
    mv_vel, mv_acc = vel / _slow, acc / _slow ** 2
    if act:
        # the tool centre point keeps the robot's rated tool speed and the task's "at most" acceleration limit
        # (max_accel; the rated tool acceleration is a rule-of-thumb figure, not a cap); a stretched run lowers them
        # with the joints (uniform stretch)
        amax = next((r.target for r in requirements if r.id == 'max_accel' and r.target and r.target > 0), None)
        vmax = caps.max_tcp_speed_m_s if caps.max_tcp_speed_m_s and caps.max_tcp_speed_m_s > 0 else None
        if vmax or amax:
            run.tcp = (lambda q: fam.fk(p, q)[:3, 3], vmax / _slow if vmax else None,
                       amax / _slow ** 2 if amax else None)
    start_q = q0.copy()
    cur_stop = 0
    reach, torque, limits_ok = [], np.zeros(len(act)), True
    free = np.ones(len(act), bool)               # joints a rotate has not moved (only these are limit-checked)
    rotated: dict[int, list[float]] = {}         # joint index -> [min, max] angle swept by rotates
    rotates, rotate_errors, timed_fail = [], [], []

    def rotate_joint(axis):
        names = [j.name for j in act]
        if axis == 'tool_yaw':
            if 'tool_yaw' in names:
                return names.index('tool_yaw')
            ok = [i for i, j in enumerate(act) if j.type in ('revolute', 'continuous')]
            return ok[-1] if ok else None
        return names.index(axis) if axis in names and act[names.index(axis)].type != 'prismatic' else None

    def unwound(q, ref):
        """q with the rotated joints at the 2*pi-equivalent angle nearest their current value in `ref`."""
        q = np.array(q, float)
        for j in rotated:
            q[j] = ref[j] + _wrap(q[j] - ref[j])
        return q
    prev_kind, last_rest = '', None

    def drive_to(dest):
        nonlocal cur_stop
        if dest > cur_stop:
            run.drive([np.array(run.pose[:2])] + [path[i] for i in range(cur_stop + 1, dest + 1)])
            cur_stop = dest

    for w in wps:
        run.phase, run.skip = w.phase, frozenset()
        if w.kind == 'drive':
            if not fam.mobile or not mobile_path:
                events.append(SimEvent(t=run.t, kind='warning', text=f'{w.phase}: this robot does not drive; skipped'))
            else:
                drive_to(stop_for(w.xyz, cur_stop) if w.xyz else len(path) - 1)
        elif w.kind == 'wait':
            run.dwell(w.dwell_s)
        elif w.kind == 'home':
            run.move(unwound(start_q, run.q), mv_vel, mv_acc) if act else None
        elif w.kind == 'rotate':
            j = rotate_joint(w.axis)
            omega = abs(w.rpm) * 2 * math.pi / 60
            if j is None:
                why = (f'axis "{w.axis}" is not a rotating joint of this robot' if w.axis != 'tool_yaw' else
                       'this robot has no tool_yaw or other rotating joint')
                rotate_errors.append(f'{w.phase}: {why}')
                events.append(SimEvent(t=run.t, kind='warning', text=f'{w.phase}: {why}; rotate skipped'))
            elif omega <= 0 or abs(w.turns) < 1e-12:
                rotate_errors.append(f'{w.phase}: a rotate needs rpm > 0 and turns != 0')
                events.append(SimEvent(t=run.t, kind='warning', text=f'{w.phase}: needs rpm > 0 and turns != 0; skipped'))
            else:
                travel = w.turns * 2 * math.pi
                alpha = omega / w.ramp_s if w.ramp_s > 0 else float(acc[j])
                q_from = float(run.q[j])
                T = run.rotate(j, travel, omega, alpha)
                free[j] = False
                span = rotated.setdefault(j, [q_from, q_from])
                span[0], span[1] = min(span[0], q_from, q_from + travel), max(span[1], q_from, q_from + travel)
                oid = run.holding or (attached.id if attached is not None else '')
                rotates.append(dict(n=w.index, j=j, turns=w.turns, rpm=abs(w.rpm), ramp=w.ramp_s, T=T, limit=w.limit_s,
                                    omega=omega, alpha=alpha, d=abs(travel), obj=objs.get(oid)))
        else:
            if mobile_path:
                drive_to(stop_for(w.xyz, cur_stop))
            if act:
                tb = to_base(w.xyz, run.pose)
                res = fam.ik(p, tb, seed=run.q)
                reach.append((w.name, res.ok, float(np.linalg.norm(tb)), res, run.t))
                if not res.ok:
                    events.append(SimEvent(t=run.t, kind='unreachable', text=(
                        f'{w.name}: {np.linalg.norm(tb):.2f} m from the base cannot be reached '
                        f'(IK error {res.error_m * 1000:.0f} mm{"; " + res.note if res.note else ""})')))
                if w.kind in CONTACT:
                    run.skip = frozenset([b for b in [rest_box(w.xyz)] if b])
                    last_rest = rest_box(w.xyz)
                elif prev_kind in CONTACT and last_rest:
                    run.skip = frozenset([last_rest])
                if rotated:
                    res.q = unwound(res.q, run.q)
                limits_ok &= bool(np.all((res.q >= lo - 1e-6) | ~free) and np.all((res.q <= hi + 1e-6) | ~free))
                if w.limit_s > 0:
                    # timed move: the fastest time at the unscaled limits, stretched to the allowed time when shorter
                    T = run.move_time(res.q, vel, acc)
                    t_start = run.t
                    if T > 1e-9 and T <= w.limit_s:
                        k = w.limit_s / T
                        run.move(res.q, vel / k, acc / k ** 2, k)
                    else:
                        run.move(res.q, vel, acc)
                        if T > w.limit_s:
                            timed_fail.append((w.index, T, w.limit_s, w.phase))
                    run.dwelled += run.t - t_start        # fixed-time segment: not stretched with the cycle
                else:
                    run.move(res.q, mv_vel, mv_acc)
                payload = objs[run.holding].mass_kg if run.holding in objs else (
                    spec.physics_inputs.object_mass_kg if run.holding else 0.0)
                torque = np.maximum(torque, np.abs(fam.gravity_torques(p, res.q, payload)))
            if w.kind in ('grasp', 'release'):
                run.dwell(w.dwell_s or GRIP_S, w.object if w.kind == 'grasp' else '')
            elif w.kind == 'place' and w.dwell_s:      # press / push: hold at the surface
                run.dwell(w.dwell_s)
        prev_kind = w.kind
    run.skip = frozenset()

    # --- realistic playback: a run faster than the cycle time is stretched to fill it ---
    cycle = spec.physics_inputs.cycle_time_s
    fastest = run.rows[-1]['t'] if _fastest is None else _fastest
    moving = run.rows[-1]['t'] - run.dwelled
    if _slow == 1.0 and moving > 1e-6 and run.rows[-1]['t'] < cycle and cycle > run.dwelled:
        return simulate(spec, requirements, scene, concept, params, dt, (cycle - run.dwelled) / moving, fastest)

    # --- collisions ---
    pw = np.array([0.0, 0.0, bz])
    radii = fam.chain_radii(p)
    active, hits = set(), 0
    # a fixed robot mounted on a table or plinth stands on that box: its first segment (the base) may touch it
    mount_box = {b.id for b in boxes if bz > 0.02 and not mobile_path
                 and collision.point_box_distance([[pose0[0], pose0[1], bz]], b.center, b.size, b.yaw)[0] <= 0.02}
    for r in run.rows[::2]:
        x, y, yaw = r['pose']
        c, s = math.cos(yaw), math.sin(yaw)
        pts = [np.array([x + c * a[0] - s * a[1], y + s * a[0] + c * a[1], a[2] + pw[2]])
               for a in fam.chain_points(p, r['q'])]
        now = set()
        for b in boxes:
            if b.id in r['skip']:
                continue
            for i in range(len(pts) - 1):
                if i == 0 and b.id in mount_box:
                    continue
                if collision.capsule_box_distance(pts[i], pts[i + 1], radii[i], b.center, b.size, b.yaw) <= 0:
                    now.add((b.id, i))
        for key in now - active:
            hits += 1
            if hits <= 20:
                events.append(SimEvent(t=r['t'], kind='collision', text=(
                    f'Robot segment {key[1] + 1} collides with box {key[0]} during "{r["phase"]}"')))
        active = now

    # --- frames ---
    rows = run.rows
    if len(rows) > MAX_FRAMES:
        rows = [rows[i] for i in np.unique(np.linspace(0, len(rows) - 1, MAX_FRAMES).astype(int))]
    frames = []
    for r in rows:
        jd = {j.name: float(v) for j, v in zip(act, r['q'])}
        for n in wheel_names:
            jd[n] = float(r['wheels'][0 if 'left' in n else 1])
        x, y, yaw = r['pose']
        tcp = fam.fk(p, r['q'])[:3, 3]
        c, s = math.cos(yaw), math.sin(yaw)
        frames.append(Frame(t=float(r['t']), joints=jd, holding=r['holding'], phase=r['phase'],
                            base=(x, y, yaw) if fam.mobile else None,
                            tcp=(float(x + c * tcp[0] - s * tcp[1]), float(y + s * tcp[0] + c * tcp[1]),
                                 float(bz + tcp[2]))))
    duration = float(run.rows[-1]['t'])

    # --- checks ---
    checks: list[Check] = []
    ex = caps.extra
    actual = dict(reach_max=caps.reach_max_m, reach_min=caps.reach_min_m, z_min=caps.z_min_m, z_max=caps.z_max_m,
                  payload=caps.payload_kg, grip_force=caps.gripper_force_n, tcp_speed=caps.max_tcp_speed_m_s,
                  tcp_accel=caps.max_tcp_accel_m_s2, accuracy=caps.repeatability_m, cycle_time=duration,
                  travel_distance=run.driven if fam.mobile else None, drive_speed=ex.get('max_speed_m_s'),
                  drive_accel=ex.get('max_accel_m_s2'), wheel_torque=ex.get('wheel_torque_nm'),
                  slope=ex.get('max_slope_deg'), battery=ex.get('battery_wh'))
    max_accel = _max_accel(run.rows, fam, p, act, bz, rotates) if any(r.id == 'max_accel' for r in requirements) else None
    for rq in requirements:
        a = actual.get(rq.id)
        if rq.id == 'max_accel':
            a = max_accel[0]
        if not act and rq.id in ('reach_max', 'reach_min', 'z_min', 'z_max', 'grip_force', 'tcp_speed', 'tcp_accel',
                                 'accuracy'):
            a = None                               # no manipulator: the load rides on the deck
        calc, detail = None, ''
        if rq.id == 'cycle_time':
            calc = Calc(formula='t = sum of simulated move, drive and dwell times',
                        inputs={'moves': Quantity(value=len(run.rows), unit='samples')}, result=duration, unit='s')
        if rq.id == 'max_accel':
            calc, detail = max_accel[1], max_accel[2]
        if rq.id == 'cycle_time':
            detail = (f'fastest feasible {fastest:.1f} s; played at the {cycle:.1f} s cycle' if _slow != 1.0
                      else f'fastest feasible {fastest:.1f} s')
        c = report.check(rq, a, calc, detail)
        if rq.id == 'max_jerk':
            c.detail = 'trapezoid profiles: jerk is not bounded in this simulation'
        elif rq.id == 'reach_max' and act:
            bad = [r for r in reach if not r[1]]
            c.detail = (f'{len(reach) - len(bad)} of {len(reach)} waypoints reachable'
                        + (': unreachable ' + ', '.join(r[0] for r in bad) if bad else ''))
            if bad and c.passed is not None:
                c.passed = False
        elif a is None:
            c.detail = ('no manipulator: the load is carried on the deck' if not act
                        else 'not evaluated for this robot family')
        checks.append(c)
    if act:
        bad = [r for r in reach if not r[1]]
        if not any(c.requirement_id == 'reach_max' for c in checks):
            checks.append(report.custom('reach_max', 'Reach', None, None, 'm', passed=not bad,
                                        detail=f'{len(reach) - len(bad)} of {len(reach)} waypoints reachable'))
        checks.append(report.custom('joint_limits', 'Joint limits', None, None, '', passed=limits_ok and not bad,
                                    detail='all waypoint poses inside the joint ranges' if limits_ok else
                                    'a waypoint pose needs a joint outside its range'))
        payload_kg = max([o.mass_kg for o in spec.objects] + [spec.physics_inputs.object_mass_kg])
        for j, tq in zip(act, torque):
            unit = 'N' if j.type == 'prismatic' else 'N*m'
            calc = Calc(formula='tau = -sum(J_i^T F_i), F_i = (0, 0, -m_i g), links and payload at the worst waypoint',
                        inputs={'payload': Quantity(value=payload_kg, unit='kg'), 'g': Quantity(value=G, unit='m/s^2')},
                        result=float(tq), unit=unit)
            c = report.custom(f'joint_torque_{j.name}', f'Static load {j.name}', float(tq), j.max_effort, unit,
                              detail='gravity load at the worst waypoint vs the joint rating', calc=calc)
            checks.append(c)
            if c.passed is False:
                events.append(SimEvent(t=0.0, kind='limit', text=f'{j.name} needs {tq:.1f} {unit}, rated {j.max_effort:.1f}'))
        if len(run.rows) > 1:
            qs = np.array([r['q'] for r in run.rows])
            ts = np.array([r['t'] for r in run.rows])
            dtq = np.maximum(np.diff(ts), 1e-9)[:, None]
            peak = (np.abs(np.diff(qs, axis=0)) / dtq).max(axis=0)
            k = int(np.argmax(peak / vel))
            checks.append(report.custom('joint_speed', f'Peak joint speed ({act[k].name})', float(peak[k]), float(vel[k]),
                                        'rad/s or m/s', detail='peak speed in the simulated motion vs the rating',
                                        passed=bool(np.all(peak <= vel * 1.02))))
    for r in rotates:
        j = act[r['j']]
        ramp = f"ramp {r['ramp']:g} s" if r['ramp'] > 0 else 'the joint acceleration'
        checks.append(report.custom(
            f"rotate_{r['n']}", f"Rotate {r['turns']:g} turns on {j.name}", r['limit'] if r['limit'] > 0 else None, r['T'],
            's', comparator='<=', detail=f"{r['turns']:g} turns at {r['rpm']:g} rpm, {ramp} on {j.name}: {r['T']:.1f} s"))
    for jx, (a0, a1) in rotated.items():
        j = act[jx]
        turns = (a1 - a0) / (2 * math.pi)
        if j.type == 'continuous':
            checks.append(report.custom(f'rotate_range_{j.name}', f'Rotation range of {j.name}', None, a1 - a0, 'rad',
                                        passed=True, detail=f'{turns:.3g} turns on a continuous joint'))
        else:
            fits = a0 >= j.lower - 1e-6 and a1 <= j.upper + 1e-6
            lim = (f'\u00b1{math.degrees(j.upper):.0f}\u00b0' if abs(j.lower + j.upper) < 1e-6 else
                   f'{math.degrees(j.lower):.0f}\u00b0 to {math.degrees(j.upper):.0f}\u00b0')
            checks.append(report.custom(
                f'rotate_range_{j.name}', f'Rotation range of {j.name}', j.upper - j.lower, a1 - a0, 'rad', comparator='<=',
                passed=fits, detail=(f'{turns:.3g} turns fit inside the joint range ({lim})' if fits else
                                     f'{turns:.3g} turns need a continuous joint; {j.name} is limited to {lim}')))
    if rotate_errors:
        checks.append(report.custom('rotate', 'Rotate action', None, None, '', passed=False,
                                    detail='; '.join(rotate_errors)))
    for n, T, D, label in timed_fail:
        checks.append(report.custom(f'move_time_{n}', f'Move time: {label}'[:120], D, T, 's', comparator='<=',
                                    detail=f'needs {T:.1f} s, allowed {D:.1f} s'))
    checks.append(report.custom('collision_free', 'No collisions with the scene', None, None, '',
                                passed=hits == 0, detail=report.collision_detail(hits)))
    if mobile_path:
        xy = np.array([r['pose'][:2] for r in run.rows])
        dev = max(_polyline_distance(pt, path) for pt in xy[::5])
        checks.append(report.custom('path_follow', 'Base stays on the path', 0.005, dev, 'm', comparator='<=',
                                    detail='largest distance of the base from the path polyline'))
        stops = [r for r in run.rows]
        sp = np.linalg.norm(np.diff(xy, axis=0), axis=1) / np.maximum(np.diff([r['t'] for r in stops]), 1e-9)
        checks.append(report.custom('drive_speed_limit', 'Driving speed within the rating', float(sp.max()) if len(sp) else 0.0,
                                    bp.max_speed_m_s, 'm/s', passed=bool(len(sp) == 0 or sp.max() <= bp.max_speed_m_s * 1.02),
                                    detail='peak simulated base speed'))
    return SimResult(frames=frames, events=sorted(events, key=lambda e: e.t), checks=checks, duration_s=duration,
                     ok=report.all_ok(checks))


def _median3(x):
    if len(x) < 3:
        return x
    y = x.copy()
    y[1:-1] = np.median(np.stack([x[:-2], x[1:-1], x[2:]]), axis=0)
    return y


def _max_accel(rows, fam, p, act, bz, rotates):
    """(peak, Calc, detail) of the largest acceleration: the tool centre point of the run (second difference of its
    world positions, median-of-3 smoothed) and the rim of an object a rotate turns."""
    pos, ts = [], []
    for r in rows:
        x, y, yaw = r['pose']
        if act:
            tcp = fam.fk(p, r['q'])[:3, 3]
            c, s = math.cos(yaw), math.sin(yaw)
            pos.append((x + c * tcp[0] - s * tcp[1], y + s * tcp[0] + c * tcp[1], bz + tcp[2]))
        else:
            pos.append((x, y, 0.0))
        ts.append(r['t'])
    pos, ts = np.array(pos, float), np.array(ts, float)
    keep = np.concatenate([[True], np.diff(ts) > 1e-6])
    pos, ts = pos[keep], ts[keep]
    a_tcp = 0.0
    if len(ts) >= 3:
        v = np.diff(pos, axis=0) / np.diff(ts)[:, None]
        tm = (ts[1:] + ts[:-1]) / 2
        acc = np.linalg.norm(np.diff(v, axis=0) / np.diff(tm)[:, None], axis=1)
        a_tcp = float(_median3(acc).max())
    best, calc, detail = a_tcp, Calc(
        formula='a = |d2p/dt2| of the simulated tool centre point (second difference, median of 3)',
        inputs={'samples': Quantity(value=len(ts), unit='samples')}, result=a_tcp, unit='m/s^2'), \
        f'peak tool centre point acceleration {a_tcp:.3g} m/s^2'
    for r in rotates:
        o = r['obj']
        if o is None:
            continue
        rad = max(o.size_m[0], o.size_m[1]) / 2
        w = motion.trapezoid(r['d'], r['omega'], r['alpha'])['v_peak']
        at, ac = r['alpha'] * rad, w * w * rad
        a_rim = math.hypot(at, ac)
        if a_rim > best:
            gov = 'centripetal w^2 r' if ac >= at else 'tangential alpha r'
            best = a_rim
            calc = Calc(formula='a_rim = sqrt((alpha r)^2 + (w^2 r)^2) at the end of the ramp, r = half the larger horizontal size',
                        inputs={'w': Quantity(value=w, unit='rad/s'), 'alpha': Quantity(value=r['alpha'], unit='rad/s^2'),
                                'r': Quantity(value=rad, unit='m'), 'a_tangential': Quantity(value=at, unit='m/s^2'),
                                'a_centripetal': Quantity(value=ac, unit='m/s^2')},
                        result=a_rim, unit='m/s^2', note=f'The {gov} term governs at the rim of {o.id}.')
            detail = (f'peak rim acceleration of {o.id}: {a_rim:.3g} m/s^2 ({gov} governs: centripetal {ac:.3g}, '
                      f'tangential {at:.3g}); tool centre point {a_tcp:.3g} m/s^2')
    return best, calc, detail


def _polyline_distance(pt, path):
    best = math.inf
    for a, b in zip(path, path[1:]):
        ab = b - a
        u = np.clip(np.dot(pt - a, ab) / max(np.dot(ab, ab), 1e-12), 0, 1)
        best = min(best, float(np.linalg.norm(pt - (a + u * ab))))
    return best
