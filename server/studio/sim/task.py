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
    kind: str                                  # approach|grasp|lift|carry|place|release|retreat|home|drive|wait
    object: str = ''
    phase: str = ''
    dwell_s: float = 0.0


def waypoints(spec: TechnicalSpec, scene: SceneSpec, base: BasePose) -> list[Waypoint]:
    """Ordered waypoints of the task in the world frame (see docs/CONTRACTS.md)."""
    pi = spec.physics_inputs
    objs = {o.id: o for o in spec.objects}
    marks = {m.id: m for m in spec.markers}
    out, carried_z = [], None
    for a in spec.actions:
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
                carried_z = za
            elif a.verb == 'place':
                zc = max(carried_z, za) if carried_z is not None else za
                out += [Waypoint(f'{tag}_carry', (x, y, zc), 'carry', oid, label),
                        Waypoint(f'{tag}_approach', (x, y, za), 'approach', oid, label),
                        Waypoint(f'{tag}_place', (x, y, zg), 'place', oid, label),
                        Waypoint(f'{tag}_release', (x, y, zg), 'release', oid, label, GRIP_S),
                        Waypoint(f'{tag}_retreat', (x, y, za), 'retreat', '', label)]
                carried_z = None
            else:
                out += [Waypoint(f'{tag}_approach', (x, y, za), 'approach', '', label),
                        Waypoint(f'{tag}_place', (x, y, zg), 'place', '', label, a.duration_s),
                        Waypoint(f'{tag}_retreat', (x, y, za), 'retreat', '', label)]
        elif a.verb == 'move' and m is not None:
            out.append(Waypoint(f'move_{m.id}', tuple(m.position), 'carry', a.object, label))
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


class _Run:
    """Timeline builder: rows of (t, q, base pose, wheel angles, holding, phase, boxes to ignore)."""

    def __init__(self, dt, q, pose, bp):
        self.dt, self.q, self.pose, self.bp = dt, np.array(q, float), pose, bp
        self.t, self.holding, self.phase, self.skip = 0.0, '', '', frozenset()
        self.wheels = [0.0, 0.0]
        self.rows = []
        self.driven = 0.0
        self.add()

    def add(self, q=None):
        self.rows.append(dict(t=self.t, q=np.array(self.q if q is None else q, float), pose=self.pose,
                              wheels=tuple(self.wheels), holding=self.holding, phase=self.phase, skip=self.skip))

    def dwell(self, d, holding=None):
        """Stand still for d seconds; `holding` (when given) applies from the last frame of the dwell."""
        n = max(1, math.ceil(d / self.dt))
        for i in range(n):
            self.t += d / n
            if holding is not None and i == n - 1:
                self.holding = holding
            self.add()

    def move(self, qt, vel, acc):
        dq = np.asarray(qt, float) - self.q
        mv = np.abs(dq) > 1e-9
        if not mv.any():
            return
        vf = min(vel[mv] / np.abs(dq[mv]))
        af = min(acc[mv] / np.abs(dq[mv]))
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
        s = motion.sample(D, bp.max_speed_m_s, bp.max_accel_m_s2, self.dt)
        T = motion.trapezoid(D, bp.max_speed_m_s, bp.max_accel_m_s2)['t_total']
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
             params: dict | None = None, dt: float = 0.04) -> SimResult:
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
    if act and first is not None:
        pose_first = stop_pose(stop_for(first.xyz, 0)) if mobile_path else pose0
        q0 = fam.ik(p, to_base(first.xyz, pose_first)).q
    run = _Run(dt, q0, pose0, bp)
    start_q = q0.copy()
    cur_stop = 0
    reach, torque, limits_ok = [], np.zeros(len(act)), True
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
            run.move(start_q, vel, acc) if act else None
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
                limits_ok &= bool(np.all(res.q >= lo - 1e-6) and np.all(res.q <= hi + 1e-6))
                run.move(res.q, vel, acc)
                payload = objs[run.holding].mass_kg if run.holding in objs else (
                    spec.physics_inputs.object_mass_kg if run.holding else 0.0)
                torque = np.maximum(torque, np.abs(fam.gravity_torques(p, res.q, payload)))
            if w.kind in ('grasp', 'release'):
                run.dwell(w.dwell_s or GRIP_S, w.object if w.kind == 'grasp' else '')
        prev_kind = w.kind
    run.skip = frozenset()

    # --- collisions ---
    pw = np.array([0.0, 0.0, bz])
    radii = fam.chain_radii(p)
    active, hits = set(), 0
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
    for rq in requirements:
        a = actual.get(rq.id)
        if not act and rq.id in ('reach_max', 'reach_min', 'z_min', 'z_max', 'grip_force', 'tcp_speed', 'tcp_accel',
                                 'accuracy'):
            a = None                               # no manipulator: the load rides on the deck
        calc, detail = None, ''
        if rq.id == 'cycle_time':
            calc = Calc(formula='t = sum of simulated move, drive and dwell times',
                        inputs={'moves': Quantity(value=len(run.rows), unit='samples')}, result=duration, unit='s')
        c = report.check(rq, a, calc, detail)
        if rq.id == 'reach_max' and act:
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
    checks.append(report.custom('collision_free', 'No collisions with the scene', None, None, '',
                                passed=hits == 0, detail=f'{hits} collision episode(s)' if hits else 'no contact found'))
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


def _polyline_distance(pt, path):
    best = math.inf
    for a, b in zip(path, path[1:]):
        ab = b - a
        u = np.clip(np.dot(pt - a, ab) / max(np.dot(ab, ab), 1e-12), 0, 1)
        best = min(best, float(np.linalg.norm(pt - (a + u * ab))))
    return best
