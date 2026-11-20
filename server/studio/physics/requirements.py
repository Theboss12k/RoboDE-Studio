"""Base placement and requirement derivation from a TechnicalSpec. Every requirement carries its working."""
import itertools
import math

import numpy as np

from ..schemas import BasePose, Calc, Quantity, Requirement, TechnicalSpec
from . import grip, locomotion, motion
from .statics import G

TASK_VERBS = ('pick', 'place', 'move', 'press', 'inspect', 'push')
DWELL_VERBS_ALL = True          # every action's duration_s counts as dwell
MOBILE_DRIVE_SHARE = 0.6        # share of the non-dwell cycle time given to driving when no speed is specified


def _q(v, unit=''):
    return Quantity(value=float(v), unit=unit)


def _r(x, sig=3):
    """Round to `sig` significant digits (for display targets only)."""
    x = float(x)
    if x == 0 or not math.isfinite(x):
        return x
    return round(x, sig - 1 - int(math.floor(math.log10(abs(x)))))


def _task_points(spec):
    """Ordered [(marker_id, verb, np.array xyz)] for the task actions."""
    out = []
    objs = {o.id: o for o in spec.objects}
    for a in spec.actions:
        if a.verb not in TASK_VERBS:
            continue
        mid = a.target or (objs[a.object].start_marker if a.verb == 'pick' and a.object in objs else '')
        if not mid:
            continue
        try:
            m = spec.marker(mid)
        except KeyError:
            continue
        out.append((mid, a.verb, np.array(m.position, dtype=float)))
    if not out:
        out = [(m.id, 'move', np.array(m.position, dtype=float)) for m in spec.markers]
    return out


def _enclosing_circle(pts):
    """Smallest enclosing circle (centre, radius) of 2D points by brute force over pairs and triples."""
    pts = [np.asarray(p, dtype=float) for p in pts]
    if len(pts) == 1:
        return pts[0], 0.0
    cands = []
    for p, q in itertools.combinations(pts, 2):
        cands.append(((p + q) / 2, np.linalg.norm(p - q) / 2))
    for p, q, r in itertools.combinations(pts, 3):
        ax, ay = p
        bx, by = q
        cx, cy = r
        d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
        if abs(d) < 1e-12:
            continue
        ux = ((ax**2 + ay**2) * (by - cy) + (bx**2 + by**2) * (cy - ay) + (cx**2 + cy**2) * (ay - by)) / d
        uy = ((ax**2 + ay**2) * (cx - bx) + (bx**2 + by**2) * (ax - cx) + (cx**2 + cy**2) * (bx - ax)) / d
        c = np.array([ux, uy])
        cands.append((c, np.linalg.norm(c - p)))
    best = None
    for c, r in sorted(cands, key=lambda t: t[1]):
        if all(np.linalg.norm(c - p) <= r + 1e-9 for p in pts):
            best = (c, r)
            break
    return best


def place_base(spec: TechnicalSpec) -> BasePose:
    """Fixed robots: centre of the smallest circle enclosing the task points (xy), z = 0, facing the first pick.
    Mobile: first path point, facing the second."""
    loco = spec.locomotion
    if loco.required:
        path = [np.array(p, dtype=float) for p in loco.path]
        if path:
            pos = path[0]
            yaw = 0.0
            if len(path) > 1:
                yaw = math.atan2(path[1][1] - pos[1], path[1][0] - pos[0])
            return BasePose(position=(float(pos[0]), float(pos[1]), 0.0), yaw=yaw)
        p = _task_points(spec)[0][2]
        return BasePose(position=(float(p[0]), float(p[1]), 0.0), yaw=0.0)
    pts = _task_points(spec)
    centre, _ = _enclosing_circle([p[2][:2] for p in pts])
    first = next((p for p in pts if p[1] == 'pick'), pts[0])[2]
    d = first[:2] - centre
    yaw = math.atan2(d[1], d[0]) if np.linalg.norm(d) > 1e-9 else 0.0
    return BasePose(position=(float(centre[0]), float(centre[1]), 0.0), yaw=yaw)


def _path_length(path):
    return float(sum(np.linalg.norm(np.array(b) - np.array(a)) for a, b in zip(path, path[1:])))


def derive(spec: TechnicalSpec, base: BasePose) -> list[Requirement]:
    """Requirements with stable ids (see docs/CONTRACTS.md)."""
    pi = spec.physics_inputs
    sf = pi.safety_factor
    appr = pi.approach_height_m
    mobile = spec.locomotion.required
    mount = np.array(base.position, dtype=float)
    pts = _task_points(spec)
    reqs: list[Requirement] = []

    def add(id_, name, cat, cmp_, calc, target=None):
        reqs.append(Requirement(id=id_, name=name, category=cat, comparator=cmp_,
                                target=_r(calc.result if target is None else target), unit=calc.unit, calc=calc))

    # --- reach ---
    stops = [np.array(p, dtype=float) * np.array([1, 1, 0]) for p in spec.locomotion.path] if mobile else []
    stops = stops or [mount]
    dists, names = [], []
    for mid, _, p in pts:
        for label, q in ((mid, p), (f'{mid}+approach', p + np.array([0, 0, appr]))):
            dists.append(min(float(np.linalg.norm(q - s)) for s in stops))
            names.append(label)
    k = int(np.argmax(dists))
    clearance = pi.clearance_m
    ins = {f'd[{n}]': _q(d, 'm') for n, d in zip(names, dists)}
    ins.update({'clearance': _q(clearance, 'm'), 'base_x': _q(mount[0], 'm'), 'base_y': _q(mount[1], 'm'),
                'base_z': _q(mount[2], 'm')})
    how = ('distance from the nearest stop (path point) to the task point' if mobile
           else 'distance from the base mounting point to the task point')
    add('reach_max', 'Maximum reach', 'reach', '>=', Calc(
        formula='reach = max_i |P_i - S_i| + clearance', inputs=ins, result=max(dists) + clearance, unit='m',
        note=f'Governing point: {names[k]}; {how}; each marker and the same point raised by the approach height.'))
    dmin = min(float(np.linalg.norm(p - s)) for _, _, p in pts for s in stops)
    add('reach_min', 'Closest task point', 'reach', 'info', Calc(
        formula='reach_min = min_i |P_i - S_i|', inputs={'d_min': _q(dmin, 'm')}, result=dmin, unit='m',
        note='Nearest task point to the base; the robot must be able to fold in this far or stay clear.'))
    zs = [p[2] - mount[2] for _, _, p in pts]
    add('z_max', 'Highest task point', 'workspace', 'info', Calc(
        formula='z_max = max(z_i) + approach - z_base',
        inputs={'z_top': _q(max(zs), 'm'), 'approach': _q(appr, 'm')}, result=max(zs) + appr, unit='m'))
    add('z_min', 'Lowest task point', 'workspace', 'info', Calc(
        formula='z_min = min(z_i) - z_base', inputs={'z_low': _q(min(zs), 'm')}, result=min(zs), unit='m'))

    # --- payload ---
    m_obj = pi.object_mass_kg
    if pi.tool_mass_kg is None:
        tool = 0.3 + 0.15 * m_obj
        if m_obj <= 10:
            tool = min(tool, 3.0)
        tool_note = 'Tool mass estimated as 0.3 + 0.15 x object mass (capped at 3 kg for objects up to 10 kg).'
    else:
        tool, tool_note = pi.tool_mass_kg, 'Tool mass as specified.'
    add('payload', 'Payload at the tool flange', 'payload', '>=', Calc(
        formula='payload = (m_object + m_tool) * SF',
        inputs={'m_object': _q(m_obj, 'kg'), 'm_tool': _q(tool, 'kg'), 'SF': _q(sf)},
        result=(m_obj + tool) * sf, unit='kg', note=tool_note))

    # --- time budget and moves ---
    dwell = float(sum(a.duration_s for a in spec.actions))
    budget = max(pi.cycle_time_s - dwell, 0.1 * pi.cycle_time_s)
    drive_dist = 0.0
    if mobile:
        drive_dist = _path_length(spec.locomotion.path) if len(spec.locomotion.path) > 1 else pi.travel_distance_m
        arm_budget = budget * (1 - MOBILE_DRIVE_SHARE) if drive_dist > 0 else budget
    else:
        arm_budget = budget
    moves = []
    seq = [p for _, _, p in pts]
    if mobile:
        near = [int(np.argmin([np.linalg.norm(p - s) for s in stops])) for p in seq]
        for i in range(1, len(seq)):
            if near[i] == near[i - 1]:
                moves.append(float(np.linalg.norm(seq[i] - seq[i - 1])))
    else:
        for i in range(1, len(seq)):
            moves.append(float(np.linalg.norm(seq[i] - seq[i - 1])))
        if len(seq) > 1:
            moves.append(float(np.linalg.norm(seq[0] - seq[-1])))
    if appr > 0:
        moves += [appr] * (2 * sum(1 for _, v, _ in pts if v in ('pick', 'place')))
    moves = [d for d in moves if d > 1e-9]
    if moves:
        total, longest = sum(moves), max(moves)
        t_i = arm_budget * longest / total
        f = 1 / 3
        peak_a = pi.peak_acceleration_m_s2
        if peak_a is None:
            rf = motion.required_for(longest, t_i, f)
            v_req, a_req, anote = rf['v_peak'], rf['a'], 'Acceleration from the cycle time (accel phase = 1/3 of the move).'
        else:
            a_req = max(peak_a, 4 * longest / t_i**2)
            v_req = (a_req * t_i - math.sqrt(max(a_req**2 * t_i**2 - 4 * a_req * longest, 0.0))) / 2
            anote = 'Acceleration as specified (raised if the move is infeasible in the time).'
        base_in = {'d_longest': _q(longest, 'm'), 'sum_d': _q(total, 'm'), 'T_budget': _q(arm_budget, 's'),
                   'T_move': _q(t_i, 's'), 'dwell': _q(dwell, 's'), 'n_moves': _q(len(moves))}
        mv_note = ('Time budget (cycle time minus dwell) split over the moves by distance; moves are the legs '
                   'between task points, the return to the first point and the approach up/down at each pick/place.')
        add('tcp_speed', 'TCP speed', 'speed', '>=', Calc(
            formula='v = d / (T_move - t_a), t_a = T_move / 3, T_move = T_budget d_longest / sum_d',
            inputs=base_in if peak_a is None else {**base_in, 'a': _q(a_req, 'm/s^2')},
            result=v_req, unit='m/s', note=mv_note))
        add('tcp_accel', 'TCP acceleration', 'acceleration', '>=', Calc(
            formula='a = v / t_a, t_a = T_move / 3' if peak_a is None else 'a = specified peak acceleration',
            inputs={**base_in, 'v': _q(v_req, 'm/s')}, result=a_req, unit='m/s^2', note=anote))
    else:
        a_req = pi.peak_acceleration_m_s2 or 1.0

    # --- grip ---
    gcalc = grip.force(pi.grip, m_obj, a_req, pi.contact_count, pi.friction_coefficient, sf)
    gcalc.note = (gcalc.note + ' ' + f'Acceleration a = {a_req:.3g} m/s^2 from tcp_accel.').strip()
    add('grip_force', 'Grip force', 'grip', '>=' if pi.grip in ('parallel', 'vacuum', 'magnetic') else 'info', gcalc)

    add('cycle_time', 'Cycle time', 'cycle', '<=', Calc(
        formula='T_cycle = specified', inputs={'T_cycle': _q(pi.cycle_time_s, 's')}, result=pi.cycle_time_s, unit='s'))
    add('accuracy', 'Repeatability', 'accuracy', '<=', Calc(
        formula='repeatability = positional accuracy / 2', inputs={'accuracy': _q(pi.positional_accuracy_m, 'm')},
        result=pi.positional_accuracy_m / 2, unit='m',
        note='Repeatability is taken as half the positional accuracy tolerance.'))

    # --- locomotion ---
    if mobile:
        add('travel_distance', 'Travel distance', 'locomotion', 'info', Calc(
            formula='d = sum of path segment lengths' if len(spec.locomotion.path) > 1 else 'd = specified',
            inputs={'d': _q(drive_dist, 'm'), 'path_points': _q(len(spec.locomotion.path))}, result=drive_dist,
            unit='m'))
        slope = max(spec.locomotion.slope_deg, pi.slope_deg)
        t_drive = budget * MOBILE_DRIVE_SHARE
        if drive_dist > 0:
            vcalc, acalc = locomotion.drive(drive_dist, time_s=t_drive, speed_m_s=pi.travel_speed_m_s)
        else:
            vcalc = Calc(formula='v = specified', inputs={}, result=pi.travel_speed_m_s or 0.5, unit='m/s')
            acalc = Calc(formula='a = default', inputs={}, result=0.5, unit='m/s^2')
        vcalc.note = (vcalc.note + f' Driving gets {MOBILE_DRIVE_SHARE:.0%} of the non-dwell cycle time.').strip()
        add('drive_speed', 'Drive speed', 'locomotion', '>=', vcalc)
        add('drive_accel', 'Drive acceleration', 'locomotion', '>=', acalc)
        veh = pi.vehicle_mass_kg
        if veh is None:
            veh = locomotion.estimate_vehicle_mass(m_obj)
            mass_note = f'Vehicle mass estimated as 25 kg + 1.0 x payload = {veh:.3g} kg; total adds the payload.'
        else:
            mass_note = 'Vehicle mass as specified; total adds the payload.'
        r = locomotion.wheel_radius(pi.wheel_radius_m)
        if pi.wheel_radius_m is None:
            mass_note += f' Wheel radius assumed {r} m.'
        m_tot = veh + m_obj
        add('wheel_torque', 'Wheel torque per drive wheel', 'torque', '>=', locomotion.wheel_torque(
            m_tot, acalc.result, slope, pi.rolling_resistance, r, 2, sf, mass_note))
        add('slope', 'Climbable slope', 'locomotion', '>=', Calc(
            formula='slope = steepest segment', inputs={'slope': _q(slope, 'deg')}, result=slope, unit='deg'))
        cph = pi.cycles_per_hour if pi.cycles_per_hour is not None else 3600.0 / pi.cycle_time_s
        cps = cph * locomotion.SHIFT_HOURS
        bcalc = locomotion.battery(m_tot, drive_dist, vcalc.result, slope, pi.rolling_resistance, cps)
        bcalc.note += f' Cycles per hour {cph:.3g}' + (' (specified).' if pi.cycles_per_hour is not None
                                                      else ' (from the cycle time).')
        add('battery', 'Battery energy per shift', 'energy', '>=', bcalc)
    return reqs
