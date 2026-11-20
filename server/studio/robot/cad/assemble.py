"""Builds the robot's CAD from an approved design, the chosen motors and the structure style.

Every part is built in its URDF link frame (joint axis through the link origin, +z along the link where the family
defines it), so the per-link meshes move with the joints. Output (relative to `out_dir`): `assembly.step`,
`parts/<id>.step|.stl`, `urdf/robot.urdf`, `urdf/meshes/<link>.stl` (one merged mesh per link).

Concept-level CAD: parts are simplified envelopes (T-slot profile, plates, motor envelopes) for layout, mass and the
bill of materials. No interference analysis, no FEA. Style: `extrusion_printed` = T-slot extrusion links with printed
(PETG) brackets and motor mounts; `machined_aluminium` = round tube / plate links with machined 6061 mounts.

Checks (code, not a model):
  cad_valid        every shape passes BRepCheck_Analyzer with volume > 0;
  cad_link_frames  per link the CAD bounding box (link frame, q = 0) is within BBOX_TOL_M of the design's own link
                   envelope (the family's URDF primitives), on each of the six faces, plus the length of the longest
                   chosen motor on a joint touching that link (the motor body and its mount hang beside or behind the
                   thin design envelope; this allowance comes from the motor's own dimensions, not a fixed slack);
  cad_joint_chain  every child joint origin lies within JOINT_TOL_M of the CAD bounding box of its parent link;
  cad_motor_axes   each motor pilot and output shaft that should sit on its joint axis does, within AXIS_TOL_MM.
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ... import families
from ...ingest import urdf as urdf_intake
from ...schemas import BuildFile, BuildPart, Check, DesignVersion, MotorChoice, StructureStyle
from .. import urdf_mesh
from . import kernel as K
from . import parts as P

BBOX_TOL_M = 0.10          # per face, CAD bbox vs the design's link envelope (CAD adds hubs, mounts, motor envelopes)
JOINT_TOL_M = 0.05         # joint origin to the parent link's CAD bbox
AXIS_TOL_MM = 0.1          # shaft / pilot axis to joint axis
HEARTBEAT_S = 10.0


@dataclass
class CadResult:
    parts: list[BuildPart]
    files: list[BuildFile]
    urdf: str
    checks: list[Check]
    gripper: dict | None = None            # the prismatic gripper joint in the studio URDF (None = no gripper joint)
    link_meshes: dict[str, str] = field(default_factory=dict)


# --- helpers -----------------------------------------------------------------------------------------------------------

class Progress:
    """Calls `emit(text)` at every step and repeats the current step text every HEARTBEAT_S while one runs long."""

    def __init__(self, emit):
        self.emit, self.text, self.last = emit or (lambda t: None), '', time.monotonic()
        self.stop, self.lock = threading.Event(), threading.Lock()
        self.thread = threading.Thread(target=self._beat, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(timeout=1)

    def __call__(self, text: str) -> None:
        with self.lock:
            self.text, self.last = text, time.monotonic()
            self.emit(text)

    def _beat(self):
        while not self.stop.wait(1.0):
            with self.lock:
                if time.monotonic() - self.last >= HEARTBEAT_S:
                    self.last = time.monotonic()
                    self.emit(f'{self.text} (still working)')


def rpy_matrix(rpy) -> np.ndarray:
    r, p, y = rpy
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    return np.array([[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
                     [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
                     [-sp, cp * sr, cp * cr]])


def joint_matrix_mm(j: dict) -> np.ndarray:
    m = np.eye(4)
    m[:3, :3] = rpy_matrix(j['origin']['rpy'])
    m[:3, 3] = np.array(j['origin']['xyz']) * 1000.0
    return m


def world_transforms(parsed: dict) -> dict[str, np.ndarray]:
    """Link -> 4x4 world transform (mm) at q = 0."""
    T = {parsed['base_link']: np.eye(4)}
    for j in parsed['joints']:
        T[j['child']] = T[j['parent']] @ joint_matrix_mm(j)
    return T


@dataclass
class Ctx:
    version: DesignVersion
    family: str
    p: object
    style: str
    motors: dict[str, MotorChoice]
    parsed: dict
    progress: Progress
    parts: list = field(default_factory=list)
    axis_checks: list = field(default_factory=list)       # (part, link, point mm, direction, radius mm or None)
    problems: list = field(default_factory=list)          # strings: missing motors etc.
    gripper: dict | None = None

    def __post_init__(self):
        printed = self.style == 'extrusion_printed'
        self.mat, self.proc = ('petg', 'printed') if printed else ('aluminium', 'machined')
        self.tp, self.th = (8.0, 6.0) if printed else (6.0, 5.0)
        self.joints = {j['name']: j for j in self.parsed['joints']}

    def add(self, *parts):
        self.parts.extend(x for x in parts if x is not None)

    def children(self, link: str) -> list[dict]:
        return [j for j in self.parsed['joints'] if j['parent'] == link]

    def motor_geom(self, joint: str):
        m = self.motors.get(joint)
        return (m, P.motor_geometry(m)) if m is not None else (None, None)


def _drive_info(m: MotorChoice):
    d = m.drive
    return d.type, d.ratio, d.lead_m


# --- motor sites (generic) ---------------------------------------------------------------------------------------------

def add_motor_site(c: Ctx, jname: str, F: np.ndarray, *, stub_end: float | None, hub: dict | None, mount: str = 'plate',
                   plate_side: float | None = None, stroke_m: float = 0.0, check_axis: bool = True,
                   stub_link: str | None = None) -> None:
    """Motor, mounting plate, shaft stub, joint plate, bearings and bolts of one joint.

    `F` is the joint frame in the PARENT link frame (z = shaft direction, mount plate face at z = 0). The motor and
    plate belong to the parent; the shaft stub and joint plate to the child (revolute) or parent (stub_link)."""
    j = c.joints[jname]
    motor, g = c.motor_geom(jname)
    if motor is None:
        c.problems.append(f'no motor chosen for joint {jname}: its CAD has no motor, mount or shaft')
        return
    parent, child = j['parent'], j['child']
    c.progress(f'CAD: motor site {jname} ({g.frame})')
    revolute = j['type'] in ('revolute', 'continuous')
    Fc = np.linalg.inv(joint_matrix_mm(j)) @ F
    axis = np.array(j['axis'], float)
    tp = c.tp
    c.add(P.motor_part(motor, g, jname, parent, K.place(P.motor_shape(g), F @ P.translate(0, 0, -tp))))
    if mount == 'plate':
        side = plate_side or g.flange + 16
        mp = P.mount_plate(g, tp, side, c.mat, c.proc, f'mount_{jname}', parent, jname)
        mp.shape = K.place(mp.shape, F)
        c.add(mp)
    if stub_end is not None:
        link = stub_link or (child if revolute else parent)
        M = Fc if link == child else F
        st = P.shaft_stub(g.shaft_d, -tp, stub_end, f'shaft_{jname}', link, jname)
        st.shape = K.place(st.shape, M)
        c.add(st)
        if check_axis:
            z = M[:3, 2]
            c.axis_checks.append((st.name, link, M[:3, 3].copy(), z.copy(), g.shaft_d / 2))
    if check_axis:
        z = F[:3, 2]
        c.axis_checks.append((f'motor_{jname}', parent, F[:3, 3].copy(), z.copy(), g.pilot / 2))
    if hub is not None:
        h = P.hub_plate(hub['hw'], c.th, 1.0, g.shaft_d, f'hub_{jname}', child, jname, c.mat, c.proc,
                        back=hub.get('back', 0.0))
        h.shape = K.place(h.shape, Fc)
        c.add(h)
        if hub.get('cap'):
            cap = hub['cap']
            blk = P.block((2 * hub['hw'], 4.0, 1.0 + c.th + tp + cap), (-hub['hw'], -(hub['hw'] + hub['back'] + 4.0),
                                                                        -tp - cap), f'cap_{jname}', child, c.mat,
                          c.proc, label=f'Link end cap {jname}',
                          spec=f'end plate {2 * hub["hw"]:g} x 4 mm joining the link section to the joint plate')
            blk.shape = K.place(blk.shape, Fc)
            c.add(blk)
    dtype, ratio, lead = _drive_info(motor)
    c.add(*P.standard_for_joint(jname, child if revolute else parent, g, dtype, revolute, ratio, lead, stroke_m))


def beam(c: Ctx, length: float, section: tuple[float, float], name: str, link: str, M: np.ndarray, label: str = ''):
    """Link section along the canonical +z of `M` (T-slot extrusion for the printed style, round tube otherwise)."""
    if c.style == 'extrusion_printed':
        pt = P.extrusion(section[0], section[1], length, name, link, label)
    else:
        od = float(max(section))
        pt = P.link_tube(od, 3.0 if od <= 60 else 4.0, length, name, link, label)
    pt.shape = K.place(pt.shape, M)
    c.add(pt)
    if c.style == 'extrusion_printed':
        have = next((x for x in c.parts if x.name == f'tslot_hardware_{link}'), None)
        if have:
            have.qty += 4
        else:
            c.add(P.tslot_hardware(link, 4))
    return pt


# --- serial arm --------------------------------------------------------------------------------------------------------

def _arm(c: Ctx) -> None:
    p, fam = c.p, families.get('serial_arm')
    r_mm = p.link_radius_m * 1000
    names = [j['name'] for j in c.parsed['joints'] if j['type'] == 'revolute']
    zs_by_joint, cfg = {}, {}
    for jn in names:
        j = c.joints[jn]
        a = np.array(j['axis'], float)
        motor, g = c.motor_geom(jn)
        g = g or P.motor_geometry(MotorChoice(joint=jn, source='defined', name='default', drive={'type': 'direct', 'ratio': 1, 'efficiency': 1}))
        parallel = abs(a[2]) > 0.9
        zs = 0.0
        if jn == 'j1':
            zs = c.tp + g.length            # first motor stands on the floor below its plate
        o = np.array(j['origin']['xyz']) * 1000
        if parallel:
            F = P.frame(o + a * zs, a, (0, 1, 0))
        else:
            F = P.frame(o, a, (0, 0, -1))
        hw = g.flange / 2 + 8
        hub = ({'hw': hw} if parallel else {'hw': hw, 'back': 30.0, 'cap': 20.0})
        stub_end = 1.0 + c.th + 3.0
        cfg[jn] = dict(g=g, parallel=parallel, zs=zs, hw=hw)
        if jn == 'j1':
            if motor is None:
                c.problems.append('no motor chosen for joint j1: its CAD has no motor, mount or shaft')
            else:
                bp = P.base_plate(r_mm * 1.8, c.tp, zs, g, 'base_plate', 'base_link', 'j1', c.mat, c.proc)
                c.add(bp)
                add_motor_site(c, jn, F, stub_end=stub_end, hub=hub, mount='none')
        else:
            add_motor_site(c, jn, F, stub_end=stub_end, hub=hub)
    # link sections
    for j in c.parsed['joints']:
        child = j['child']
        if child in ('tcp',) or j['type'] == 'fixed':
            continue
        jn = j['name']
        out = [o for o in c.children(child) if o['type'] == 'revolute']
        L = out[0]['origin']['xyz'][2] * 1000 if out else None
        if L is None:                                          # last wrist link: it carries the tool
            tool_j = next((o for o in c.children(child) if o['name'] == 'tool_mount'), None)
            L = tool_j['origin']['xyz'][2] * 1000 if tool_j else 30.0
        cf = cfg[jn]
        hw, parallel = cf['hw'], cf['parallel']
        t0 = cf['zs'] + 1 + c.th if parallel else hw + 34.0
        out_cfg = cfg.get(out[0]['name']) if out else None
        if out_cfg is None:
            t1 = L
            shell = False
        elif out_cfg['parallel']:
            t1 = L - c.tp - out_cfg['g'].length
            shell = True
        else:
            t1 = L - (out_cfg['g'].flange / 2 + 8) + 6
            shell = False
        if shell or t1 - t0 < 30:
            top = (L - c.tp) if out_cfg is not None else L
            if top - t0 >= 5:
                sh = P.housing_shell(hw, top - t0, 3.0, f'{child}_housing', child, c.mat, c.proc)
                sh.shape = K.place(sh.shape, P.translate(0, 0, t0))
                c.add(sh)
            continue
        W = 40.0 if L > 500 else 20.0
        if parallel:
            M = P.translate(0, 0, t0)
            name = f'{child}_extrusion' if c.style == 'extrusion_printed' else f'{child}_tube'
            od = float(np.clip(r_mm * 1.6, 30, 80)) if c.style != 'extrusion_printed' else W
            beam(c, t1 - t0, (W, 20.0) if c.style == 'extrusion_printed' else (od, od), name, child, M,
                 f'{child} section')
        else:
            T = np.eye(4)
            T[:3, 0], T[:3, 1], T[:3, 2] = (1, 0, 0), (0, 0, 1), (0, -1, 0)
            T[:3, 3] = (0, -t0, -c.tp - 10.0)
            # frame of the child at the joint (origin 0): z = axis, y toward the parent body
            Fchild = P.frame((0, 0, 0), c.joints[jn]['axis'], (0, 0, -1))
            od = float(np.clip(r_mm * 1.6, 30, 80))
            beam(c, t1 - t0, (W, 20.0) if c.style == 'extrusion_printed' else (od, od),
                 f'{child}_extrusion' if c.style == 'extrusion_printed' else f'{child}_tube', child, Fchild @ T,
                 f'{child} section')


def _tool(c: Ctx, link: str, direction, length_m: float, gripper_kind: str, stroke_m: float, force_n: float) -> None:
    """Tool body in the tool link frame, built along +z and turned to `direction`."""
    L = length_m * 1000
    M = P.frame((0, 0, 0), direction, (0, 1, 0)) if direction[2] > 0 else np.diag([1.0, -1.0, -1.0, 1.0])
    c.progress(f'CAD: tool ({gripper_kind})')
    if gripper_kind == 'parallel' and stroke_m > 0:
        b, f1, f2 = P.gripper_parallel(stroke_m * 1000, L, 'gripper', link, 'gripper_finger', c.mat, c.proc)
        for x in (b, f1, f2):
            x.shape = K.place(x.shape, M)
        c.add(b, f1, f2, P.standard('gripper_actuator', f'servo or linear actuator for a {stroke_m * 1000:g} mm '
                                    f'parallel gripper stroke, about {force_n:g} N grip force', link, None, 1,
                                    'Gripper actuator', 0.0, source='purchased'))
        c.gripper = {'joint': 'gripper', 'parent': link, 'link': 'gripper_finger', 'xyz': [0.0, 0.0, 0.0],
                     'axis': [0.0, 1.0, 0.0], 'lower': 0.0, 'upper': stroke_m, 'velocity': 0.1, 'effort': force_n,
                     'mass_kg': round(f2.mass_kg, 4)}
    elif gripper_kind == 'vacuum':
        cup = P.suction_cup(15.0, max(L, 20.0), 'suction_cup', link)
        cup.shape = K.place(cup.shape, M)
        c.add(cup)
    else:
        tb = P.tool_block(L, 'tool_body', link, c.mat, c.proc)
        tb.shape = K.place(tb.shape, M)
        c.add(tb)


# --- gantry ------------------------------------------------------------------------------------------------------------

def _gantry(c: Ctx) -> None:
    p = c.p
    b = float(np.clip(round(p.beam_size_m * 1000 / 20) * 20, 20, 100))
    X, Y, H = (p.x_stroke_m + 0.2) * 1000, (p.y_stroke_m + 0.2) * 1000, p.frame_height_m * 1000
    zoff = p.z_offset_m * 1000
    sec = (min(b, 40.0), min(b, 40.0)) if c.style == 'extrusion_printed' else (b, b)   # printed style: 4040 profile
    c.progress('CAD: gantry frame')
    for i, (sx, sy) in enumerate(((-1, -1), (-1, 1), (1, -1), (1, 1)), 1):
        beam(c, H, sec, f'post_{i}', 'base_link', P.translate(sx * X / 2, sy * Y / 2, 0), f'Frame post {i}')
    for i, sy in enumerate((-1, 1), 1):
        beam(c, X + b, sec, f'x_rail_{i}', 'base_link',
             P.frame((-X / 2 - b / 2, sy * Y / 2, H), (1, 0, 0), (0, 0, 1)), f'X rail {i}')
    for i, (sx, sy) in enumerate(((-1, -1), (-1, 1), (1, -1), (1, 1)), 1):
        br = P.bracket_l(60, 60, 6, b, f'corner_bracket_{i}', 'base_link', c.mat, c.proc)
        br.shape = K.place(br.shape, P.frame((sx * (X / 2 + b / 2), sy * (Y / 2 - b / 2), H - b / 2 - 60 - 6),
                                             (0, 0, 1), (0, 1, 0)))
        c.add(br)
    # x carriage: a cross beam along y with a plate at each end riding on the rails
    beam(c, Y, sec, 'x_beam', 'x_carriage', P.frame((0, -Y / 2, 0), (0, 1, 0), (0, 0, 1)), 'X carriage beam')
    for i, sy in enumerate((-1, 1), 1):
        c.add(P.gantry_carriage(b + 40, 60, 10, f'x_slider_{i}', 'x_carriage', c.mat, c.proc,
                                center=(0, sy * (Y / 2 - 30 + 0), -b / 2 - 5), label=f'X slider {i}'))
    # y carriage: plate under the beam plus the Z guide block
    c.add(P.gantry_carriage(b * 1.6, b * 1.6, 10, 'y_plate', 'y_carriage', c.mat, c.proc, center=(0, 0, -b / 2 - 5)))
    c.add(P.block((40, 40, zoff + b / 2 + 10), (-20, -20, -zoff - 10), 'z_guide', 'y_carriage', c.mat, c.proc,
                  label='Z guide block'))
    # z carriage: slide beside the tool
    ztot = (p.z_offset_m + p.z_stroke_m + p.tool_length_m) * 1000
    beam(c, ztot, (20.0, 20.0) if c.style == 'extrusion_printed' else (30.0, 30.0), 'z_slide', 'z_carriage',
         P.frame((35, 0, 0), (0, 0, -1), (0, 1, 0)), 'Z slide')
    # motors (shaft perpendicular to the travel except a lead screw, which sits on the axis)
    for jn, parent in (('x_axis', 'base_link'), ('y_axis', 'x_carriage'), ('z_axis', 'y_carriage')):
        motor, g = c.motor_geom(jn)
        if motor is None:
            c.problems.append(f'no motor chosen for joint {jn}: its CAD has no motor, mount or shaft')
            continue
        dtype = motor.drive.type
        if jn == 'x_axis':
            F = P.frame((X / 2, Y / 2 + b / 2, H), (0, -1, 0), (0, 0, 1))
            stroke = p.x_stroke_m
        elif jn == 'y_axis':
            F = P.frame((b / 2, Y / 2 - 30, 0), (-1, 0, 0), (0, 0, 1))
            stroke = p.y_stroke_m
        else:
            stroke = p.z_stroke_m
            if dtype == 'leadscrew':
                F = P.frame((0, 0, b / 2 + 10), (0, 0, -1), (0, 1, 0))
            else:
                F = P.frame((b * 0.8 + 20, 0, 0), (-1, 0, 0), (0, 0, 1))
        on_axis = dtype == 'leadscrew'
        add_motor_site(c, jn, F, stub_end=20.0 if on_axis or True else None, hub=None, stroke_m=stroke,
                       check_axis=on_axis, stub_link=parent)
    if p.tool_yaw:
        j = c.joints['tool_yaw']
        F = P.frame((0, 0, 0), (0, 0, -1), (0, 1, 0))      # shaft down, motor above the plane
        add_motor_site(c, 'tool_yaw', F, stub_end=1 + c.th + 3, hub={'hw': 28.0}, plate_side=None)


# --- mobile base -------------------------------------------------------------------------------------------------------

def _mobile(c: Ctx) -> None:
    p = c.p
    from ...families.mobile_base import wheel_layout
    L, W, D = p.length_m * 1000, p.width_m * 1000, p.deck_height_m * 1000
    r, w = p.wheel_radius_m * 1000, min(0.05, p.track_m / 8) * 1000
    z0 = r * 0.5
    c.progress('CAD: chassis')
    inset = p.track_m * 500 - w / 2 - 25
    if c.style == 'extrusion_printed':
        sec = (20.0, 20.0)
        for i, sy in enumerate((-1, 1), 1):
            beam(c, L - 40, sec, f'chassis_rail_{i}', 'base_link', P.frame((-(L - 40) / 2, sy * inset, z0 + 10),
                                                                            (1, 0, 0), (0, 0, 1)), f'Chassis rail {i}')
        for i, sx in enumerate((-1, 1), 1):
            beam(c, 2 * inset - 20, sec, f'chassis_cross_{i}', 'base_link',
                 P.frame((sx * (L / 2 - 30), -(inset - 10), z0 + 10), (0, 1, 0), (0, 0, 1)), f'Chassis cross member {i}')
        for i, (sx, sy) in enumerate(((-1, -1), (-1, 1), (1, -1), (1, 1)), 1):
            br = P.bracket_l(40, 40, 5, 20, f'chassis_bracket_{i}', 'base_link', c.mat, c.proc)
            br.shape = K.place(br.shape, P.frame((sx * (L / 2 - 30), sy * (inset - 10), z0 + 20), (0, 0, 1), (0, 1, 0)))
            c.add(br)
    else:
        c.add(P.block((L - 40, 2 * inset + 20, 4.0), (-(L - 40) / 2, -(inset + 10), z0), 'chassis_floor_plate',
                      'base_link', 'aluminium', 'machined', label='Chassis floor plate'))
        for i, (sx, sy) in enumerate(((-1, -1), (-1, 1), (1, -1), (1, 1)), 1):
            pt = P.link_tube(20.0, 2.0, D - z0 - 4, f'chassis_post_{i}', 'base_link', f'Chassis post {i}')
            pt.shape = K.place(pt.shape, P.translate(sx * (L / 2 - 30), sy * (inset - 10), z0 + 4))
            c.add(pt)
    c.add(P.block((L, W, 5.0), (-L / 2, -W / 2, D - 5), 'deck_plate', 'base_link', 'aluminium', 'cut',
                  label='Deck plate', spec=f'aluminium sheet {L:g} x {W:g} x 5 mm'))
    layout = {n: (x, y) for n, x, y in wheel_layout(p)}
    for jn, (x, y) in layout.items():
        j = c.joints[jn]
        link = j['child']
        c.progress(f'CAD: wheel {jn}')
        c.add(P.wheel(r, w, f'wheel_{jn}', link, jn))
        sgn = 1.0 if y > 0 else -1.0
        zF = np.array([0.0, sgn, 0.0])
        o = np.array(j['origin']['xyz']) * 1000 - zF * (1 + w / 2)
        motor, g = c.motor_geom(jn)
        hw = max((g.flange / 2 + 8) if g else 30.0, 30.0)
        add_motor_site(c, jn, P.frame(o, zF, (0, 0, 1)), stub_end=1 + c.th + 3, hub={'hw': hw})


# --- checks ------------------------------------------------------------------------------------------------------------

def design_envelopes(parsed: dict) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Link -> (min, max) in metres of the design URDF's visual primitives, in the link frame."""
    out: dict[str, list[np.ndarray]] = {}
    for g in parsed['geometry']:
        if g['kind'] != 'visual':
            continue
        R = rpy_matrix(g['origin']['rpy'])
        t = np.array(g['origin']['xyz'])
        prm = g['parameters']
        if g['shape'] == 'box':
            h = np.array(prm['size']) / 2
        elif g['shape'] == 'cylinder':
            h = np.array([prm['radius'], prm['radius'], prm['length'] / 2])
        else:
            continue
        corners = np.array([[sx * h[0], sy * h[1], sz * h[2]] for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)])
        out.setdefault(g['link'], []).append(corners @ R.T + t)
    return {k: (np.min(np.vstack(v), 0), np.max(np.vstack(v), 0)) for k, v in out.items()}


def motor_allowance_m(c: Ctx, link: str) -> float:
    """Extra face tolerance (m) for `link`: the longest chosen motor (its own length value or NEMA row) on a joint that
    has the link as parent (motor body and mount are built there) or child (hub and shaft stub)."""
    lengths = [P.motor_geometry(c.motors[j['name']]).length for j in c.parsed['joints']
               if link in (j['parent'], j['child']) and j['name'] in c.motors]
    return max(lengths, default=0.0) / 1000.0


def axis_error(shape, radius: float, point, direction) -> float | None:
    """Worst distance (mm) of the cylinders of `radius` from the line (point, direction); also counts a tilt as error
    over 100 mm. None when the shape has no such cylinder."""
    errs = []
    d0 = np.array(direction, float) / np.linalg.norm(direction)
    for rad, pt, d in K.cylinder_axes(shape):
        if abs(rad - radius) > 1e-3:
            continue
        v = np.array(pt) - np.array(point)
        lateral = v - d0 * float(v @ d0)
        d = np.array(d)
        tilt = float(np.linalg.norm(np.cross(d, d0))) * 100.0
        errs.append(max(float(np.linalg.norm(lateral)), tilt))
    return max(errs) if errs else None


def run_checks(c: Ctx, shapes_by_link: dict[str, list], by_name: dict[str, P.CadPart]) -> list[Check]:
    checks = []
    bad = [x.name for x in c.parts if x.shape is not None and not K.valid(x.shape)]
    n = sum(1 for x in c.parts if x.shape is not None)
    checks.append(Check(requirement_id='cad_valid', name='CAD shapes valid', required=0, actual=len(bad), unit='parts',
                        passed=not bad, detail=(f'{n} shapes checked with BRepCheck_Analyzer and volume > 0'
                                                + (f'; invalid: {", ".join(bad)}' if bad else ''))))
    env = design_envelopes(c.parsed)
    worst, where, details = 0.0, '', []
    boxes = {}
    for link, shapes in shapes_by_link.items():
        lo, hi = K.bbox(K.compound(shapes))
        boxes[link] = (np.array(lo) / 1000, np.array(hi) / 1000)
        if link in env:
            e_lo, e_hi = env[link]
            dev = float(max(np.max(np.abs(boxes[link][0] - e_lo)), np.max(np.abs(boxes[link][1] - e_hi))))
            allow = motor_allowance_m(c, link)
            details.append(f'{link} {dev * 1000:.0f} (motor allowance {allow * 1000:.0f})')
            if dev - allow > worst:
                worst, where = dev - allow, link
    checks.append(Check(requirement_id='cad_link_frames', name='CAD link boxes match the design envelopes',
                        required=BBOX_TOL_M * 1000, actual=round(worst * 1000, 1), unit='mm', passed=worst <= BBOX_TOL_M,
                        detail=(f'worst face deviation beyond the motor allowance {worst * 1000:.0f} mm on {where or "-"}; per link (mm): '
                                + ', '.join(details))))
    far, wj = 0.0, ''
    for j in c.parsed['joints']:
        if j['parent'] not in boxes:
            continue
        lo, hi = boxes[j['parent']]
        o = np.array(j['origin']['xyz'])
        d = float(np.linalg.norm(np.maximum(np.maximum(lo - o, o - hi), 0.0)))
        if d > far:
            far, wj = d, j['name']
    checks.append(Check(requirement_id='cad_joint_chain', name='Joint origins lie on their parent link CAD',
                        required=JOINT_TOL_M * 1000, actual=round(far * 1000, 1), unit='mm', passed=far <= JOINT_TOL_M,
                        detail=f'worst joint {wj or "-"}: {far * 1000:.1f} mm outside the parent link CAD box'))
    err, wa, missing = 0.0, '', []
    for name, link, point, direction, radius in c.axis_checks:
        e = axis_error(by_name[name].shape, radius, point, direction)
        if e is None:
            missing.append(name)
        elif e > err:
            err, wa = e, name
    checks.append(Check(requirement_id='cad_motor_axes', name='Motor shaft axes on the joint axes',
                        required=AXIS_TOL_MM, actual=round(err, 4), unit='mm', passed=err <= AXIS_TOL_MM and not missing,
                        detail=(f'{len(c.axis_checks)} pilots and shafts checked, worst {wa or "-"}'
                                + (f'; no cylinder found on {", ".join(missing)}' if missing else ''))))
    if c.problems:
        checks.append(Check(requirement_id='cad_motors', name='Every motorised joint has a motor in the CAD',
                            required=0, actual=len(c.problems), unit='joints', passed=False, detail='; '.join(c.problems)))
    return checks


# --- build -------------------------------------------------------------------------------------------------------------

def _file(out: Path, rel: str, kind: str) -> BuildFile:
    path = out / rel
    return BuildFile(path=rel, kind=kind, sha256=K.sha256(path), bytes=path.stat().st_size)


def build_cad(version: DesignVersion, motors: list[MotorChoice], style: StructureStyle, out_dir: Path,
              progress=None) -> CadResult:
    """Build every part in its link frame and write the files. `version` is the member view of a fleet design."""
    fam = families.get(version.robot.family)
    if version.robot.family not in ('serial_arm', 'gantry', 'mobile_base'):
        raise ValueError(f'{version.robot.family} is not in v1 of the robot studio')
    out = Path(out_dir)
    p = fam.parse(version.robot.params)
    parsed = urdf_intake.parse_urdf(version.urdf.encode())
    with Progress(progress) as prog:
        c = Ctx(version, version.robot.family, p, style.style, {m.joint: m for m in motors}, parsed, prog)
        prog(f'CAD: building {version.robot.family} parts, style {style.style}')
        if c.family == 'serial_arm':
            _arm(c)
            _tool(c, 'tool', (0, 0, 1), p.tool_length_m, p.gripper, p.gripper_stroke_m, p.gripper_force_n)
        elif c.family == 'gantry':
            _gantry(c)
            _tool(c, 'tool', (0, 0, -1), p.tool_length_m, p.gripper, p.gripper_stroke_m, p.gripper_force_n)
        else:
            _mobile(c)
        names = [x.name for x in c.parts]
        if len(set(names)) != len(names):
            raise RuntimeError(f'duplicate part ids: {sorted({n for n in names if names.count(n) > 1})}')
        by_name = {x.name: x for x in c.parts}
        shapes_by_link: dict[str, list] = {}
        for x in c.parts:
            if x.shape is not None and x.link:
                shapes_by_link.setdefault(x.link, []).append(x.shape)
        prog('CAD: checking shapes, link frames and motor axes')
        checks = run_checks(c, shapes_by_link, by_name)
        files: list[BuildFile] = []
        T = world_transforms(parsed)
        if c.gripper:
            T[c.gripper['link']] = T[c.gripper['parent']] @ P.translate(*(np.array(c.gripper['xyz']) * 1000))
        build_parts: list[BuildPart] = []
        shaped = [x for x in c.parts if x.shape is not None]
        for i, x in enumerate(shaped, 1):
            prog(f'CAD: writing part {i}/{len(shaped)}: {x.name}')
            K.write_step([(x.name, x.shape)], out / f'parts/{x.name}.step')
            K.write_stl(x.shape, out / f'parts/{x.name}.stl')
        link_meshes = {}
        for link, shapes in sorted(shapes_by_link.items()):
            prog(f'CAD: link mesh {link}')
            K.write_stl(K.compound(shapes), out / f'urdf/meshes/{link}.stl')
            link_meshes[link] = f'meshes/{link}.stl'
        prog('CAD: writing assembly.step')
        K.write_step([(x.name, K.place(x.shape, T[x.link])) for x in shaped], out / 'assembly.step')
        text = urdf_mesh.studio_urdf(version, link_meshes, c.gripper)
        (out / 'urdf').mkdir(parents=True, exist_ok=True)
        (out / 'urdf/robot.urdf').write_text(text)
        for x in c.parts:
            pf = []
            if x.shape is not None:
                pf = [_file(out, f'parts/{x.name}.step', 'step'), _file(out, f'parts/{x.name}.stl', 'stl')]
            files += pf
            build_parts.append(BuildPart(id=x.name, name=x.label[:200], qty=x.qty, material=x.material[:120],
                                         process=x.process, source=x.source, mass_kg=round(x.mass_kg, 4), link=x.link,
                                         joint=x.joint, files=pf, spec=x.spec[:400],
                                         url=''))
        files.append(_file(out, 'assembly.step', 'step'))
        files += [_file(out, f'urdf/meshes/{link}.stl', 'stl') for link in sorted(shapes_by_link)]
        files.append(_file(out, 'urdf/robot.urdf', 'urdf'))
        for m in motors:
            for bp in build_parts:
                if bp.id == f'motor_{m.joint}':
                    bp.url = m.url
        prog('CAD: done')
    return CadResult(build_parts, files, text, checks, c.gripper, link_meshes)
