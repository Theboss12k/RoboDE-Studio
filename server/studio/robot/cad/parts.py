"""Part builders: shapes in millimetres in a canonical local frame, wrapped as `CadPart`.

Canonical frames used by the motor sites (`assemble.py` places them with a 4x4 matrix):
  F  = a joint frame: z is the motor shaft direction, the mounting-plate face is z = 0, the plate occupies
       z in [-tp, 0], the motor body z in [-tp - length, -tp], the shaft stub starts at z = -tp.
  B  = a link body frame: z runs along the link toward the next joint.
Masses are volume x density; printed parts use an effective density (shells plus infill) and are estimates.
Standard parts (bearings, bolts, belts, pulleys, lead screws) have no shape: they are BOM rows with a spec string.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

from ...schemas import MotorChoice
from . import kernel as K

DENSITY = {'aluminium': 2700.0, 'steel': 7850.0, 'petg': 1270.0 * 0.40, 'rubber': 1200.0}   # kg/m^3 (PETG effective)
MATERIAL_NAME = {'aluminium': '6061 aluminium', 'steel': 'steel', 'petg': 'PETG (printed, ~40 % effective density)',
                 'rubber': 'rubber'}

# NEMA frame -> flange mm, bolt pitch mm (square), pilot mm, shaft mm, shaft length mm, body length mm, bolt hole mm,
# mass kg. Typical catalogue values for the common single-stack motors (assumed, not a specific product).
NEMA_TABLE = {
    8: (20.3, 16.0, 15.0, 4.0, 10.0, 28.0, 2.5, 0.10), 11: (28.2, 23.0, 22.0, 5.0, 20.0, 31.0, 2.9, 0.12),
    14: (35.2, 26.0, 22.0, 5.0, 20.0, 36.0, 3.0, 0.20), 17: (42.3, 31.0, 22.0, 5.0, 24.0, 40.0, 3.4, 0.28),
    23: (56.4, 47.14, 38.1, 6.35, 21.0, 56.0, 5.1, 0.70), 24: (60.0, 49.8, 38.1, 8.0, 24.0, 60.0, 5.1, 1.00),
    34: (86.4, 69.6, 73.0, 14.0, 35.0, 80.0, 6.6, 3.00)}
DEFAULT_FRAME = 23           # used when a motor carries no frame or flange values (documented in the part spec)
SHAFT_KEYS = ('shaft_d_mm', 'shaft_diameter_mm', 'shaft_mm', 'shaft')
FLANGE_KEYS = ('flange_mm', 'flange_size_mm', 'flange')
PITCH_KEYS = ('bolt_pitch_mm', 'bolt_spacing_mm', 'bolt_circle_mm', 'bolt_pattern_mm', 'bolt_pattern')
PILOT_KEYS = ('pilot_mm', 'pilot_diameter_mm', 'pilot')
LENGTH_KEYS = ('length_mm', 'body_length_mm', 'motor_length_mm')
MASS_KEYS = ('mass_kg', 'weight_kg', 'motor_mass_kg')

# deep-groove bearings by bore: bore mm -> (designation, outer mm, width mm)
BEARINGS = {5: ('625-2RS', 16, 5), 6: ('626-2RS', 19, 6), 8: ('608-2RS', 22, 7), 10: ('6000-2RS', 26, 8),
            12: ('6001-2RS', 28, 8), 15: ('6002-2RS', 32, 9), 20: ('6004-2RS', 42, 12), 25: ('6005-2RS', 47, 12)}
BOLTS = {2.5: 'M2.5', 2.9: 'M2.5', 3.0: 'M2.5', 3.4: 'M3', 4.5: 'M4', 5.1: 'M5', 5.5: 'M5', 6.6: 'M6'}


@dataclass
class CadPart:
    """One BOM row, with a shape when it is modelled. `name` is the part id (also the STEP part name)."""
    name: str
    shape: object | None
    material: str
    process: str                 # printed | machined | cut | purchased | standard
    mass_kg: float
    link: str | None
    joint: str | None
    source: str                  # custom | standard | purchased
    spec: str = ''
    qty: int = 1
    label: str = ''              # human name for the BOM
    role: str = ''               # structure | motor | shaft | mount | hub | tool | wheel | standard


def part(name, shape, material, process, link, source='custom', joint=None, spec='', qty=1, label='', role='',
         mass_kg=None) -> CadPart:
    """Wrap a shape; mass from volume x density of `material` ('aluminium', 'steel', 'petg', 'rubber')."""
    if mass_kg is None:
        mass_kg = round(K.volume(shape) * 1e-9 * DENSITY[material], 4) if shape is not None else 0.0
    return CadPart(name, shape, MATERIAL_NAME.get(material, material), process, mass_kg, link, joint, source, spec,
                   qty, label or name.replace('_', ' '), role)


def standard(name, spec, link=None, joint=None, qty=1, label='', mass_kg=0.0, source='standard') -> CadPart:
    return CadPart(name, None, '', 'standard' if source == 'standard' else 'purchased', mass_kg, link, joint, source,
                   spec, qty, label or name.replace('_', ' '), 'standard')


# --- motors ------------------------------------------------------------------------------------------------------------

@dataclass
class MotorGeom:
    frame: str
    flange: float
    pitch: float
    pilot: float
    shaft_d: float
    shaft_len: float
    length: float
    hole_d: float
    mass_kg: float
    note: str                    # where the numbers came from


def _num(motor: MotorChoice, keys) -> float | None:
    for v in motor.values:
        if v.key in keys:
            if v.value is not None:
                return float(v.value)
            m = re.search(r'(\d+(?:\.\d+)?)', v.text or '')
            if m:
                return float(m.group(1))
    return None


def _frame_number(motor: MotorChoice) -> int | None:
    for v in motor.values:
        if v.key == 'frame':
            m = re.search(r'(\d+)', v.text or '') or (re.search(r'(\d+)', str(int(v.value))) if v.value else None)
            if m and int(m.group(1)) in NEMA_TABLE:
                return int(m.group(1))
    return None


def motor_geometry(motor: MotorChoice) -> MotorGeom:
    """Flange, bolt pattern, pilot, shaft and length of the chosen motor. Order: values on the choice (quoted or
    assumed), else the NEMA table row of its `frame`, else the documented default frame (NEMA 23)."""
    n = _frame_number(motor)
    flange = _num(motor, FLANGE_KEYS)
    if n is None and flange is not None:
        n = min(NEMA_TABLE, key=lambda k: abs(NEMA_TABLE[k][0] - flange))
    default = n is None
    row = NEMA_TABLE[n or DEFAULT_FRAME]
    got = {'flange': _num(motor, FLANGE_KEYS), 'pitch': _num(motor, PITCH_KEYS), 'pilot': _num(motor, PILOT_KEYS),
           'shaft_d': _num(motor, SHAFT_KEYS), 'length': _num(motor, LENGTH_KEYS), 'mass': _num(motor, MASS_KEYS)}
    flange, pitch, pilot, shaft_d, shaft_len, length, hole, mass = row
    flange = got['flange'] or flange
    pitch = got['pitch'] or (pitch if got['flange'] is None or abs(got['flange'] - row[0]) < 3 else flange * 0.83)
    pilot = min(got['pilot'] or pilot, flange - 4)
    shaft_d = got['shaft_d'] or shaft_d
    length = got['length'] or length
    mass = got['mass'] or mass
    used = [k for k, v in got.items() if v]
    note = (f'NEMA {n or DEFAULT_FRAME} table' + (' (default frame: the motor gave no frame or flange)' if default
                                                    else '') + (f'; from motor values: {", ".join(used)}' if used else ''))
    return MotorGeom(f'NEMA {n or DEFAULT_FRAME}', flange, pitch, pilot, shaft_d, shaft_len, length, hole, mass, note)


def bolt_size(hole_d: float) -> str:
    return BOLTS[min(BOLTS, key=lambda k: abs(k - hole_d))]


def bearing_for(shaft_d: float) -> tuple[str, float, float, float]:
    """Smallest standard bearing whose bore is >= the shaft: (designation, bore, outer, width) in mm."""
    bore = min((b for b in BEARINGS if b >= shaft_d - 0.01), default=max(BEARINGS))
    des, od, w = BEARINGS[bore]
    return des, bore, od, w


def motor_shape(g: MotorGeom):
    """Motor envelope: square body (z in [-length, 0]), 2 mm pilot boss, four blind bolt holes in the front face."""
    f = g.flange
    body = K.box((f, f, g.length), (-f / 2, -f / 2, -g.length))
    boss = K.cylinder(g.pilot / 2, 2.0)
    pts = K.square_pattern(g.pitch)
    return K.bolt_holes(K.fuse(body, boss), pts, g.hole_d, 5, (0, 0, -1))


def motor_part(motor: MotorChoice, g: MotorGeom, joint: str, link: str, shape) -> CadPart:
    spec = (f'{g.frame} envelope: flange {g.flange:g} mm, bolt pitch {g.pitch:g} mm, pilot {g.pilot:g} mm, '
            f'shaft {g.shaft_d:g} mm, length {g.length:g} mm ({g.note})')
    return CadPart(f'motor_{joint}', shape, 'purchased motor (envelope only)', 'purchased', round(g.mass_kg, 4), link,
                   joint, 'purchased', spec, 1, motor.name or f'Motor {joint}', 'motor')


def mount_plate(g: MotorGeom, tp: float, side: float, material: str, process: str, name: str, link: str, joint: str):
    """Motor mounting plate, face at z = 0, thickness tp, `side` mm square, pilot bore and four through holes."""
    shape = K.box((side, side, tp), (-side / 2, -side / 2, -tp))
    shape = K.cut(shape, K.cylinder(g.pilot / 2 + 0.2, tp + 2, (0, 0, -tp - 1)))
    shape = K.bolt_holes(shape, K.square_pattern(g.pitch, -tp - 1), g.hole_d + 0.2, tp + 2)
    return part(name, shape, material, process, link, joint=joint, role='mount',
                spec=f'{tp:g} mm plate, {side:g} mm square, pilot bore {g.pilot + 0.4:g} mm, 4 x {bolt_size(g.hole_d)} '
                     f'on a {g.pitch:g} mm pitch', label=f'Motor mount {joint}')


def shaft_stub(d: float, z0: float, z1: float, name: str, link: str, joint: str):
    """Output shaft stub (steel): couples to the motor shaft and carries the joint. Axis is the F z axis."""
    shape = K.cylinder(d / 2, z1 - z0, (0, 0, z0))
    return part(name, shape, 'steel', 'machined', link, joint=joint, role='shaft',
                spec=f'shaft {d:g} mm x {z1 - z0:g} mm', label=f'Output shaft {joint}')


def hub_plate(hw: float, th: float, z0: float, shaft_d: float, name: str, link: str, joint: str, material: str,
              process: str, back: float = 0.0):
    """Child-side plate at z in [z0, z0+th] with the shaft bore and the bearing seat on its motor side. `back` extends
    the plate toward -y (toward the link body)."""
    des, bore, od, bw = bearing_for(shaft_d)
    shape = K.box((2 * hw, 2 * hw + back, th), (-hw, -hw - back, z0))
    shape = K.cut(shape, K.cylinder(shaft_d / 2 + 0.2, th + 2, (0, 0, z0 - 1)))
    th_seat = min(bw, th - 1)
    shape = K.cut(shape, K.cylinder(od / 2, th_seat, (0, 0, z0)))
    return part(name, shape, material, process, link, joint=joint, role='hub',
                spec=f'plate {2 * hw:g} mm, bearing seat for {des} ({od:g} x {th_seat:g} mm), bore {shaft_d + 0.4:g} mm',
                label=f'Joint plate {joint}')


def bracket_l(a: float, b: float, t: float, width: float, name: str, link: str, material: str, process: str,
              joint: str | None = None):
    """L bracket: leg 1 a x width x t on z = 0, leg 2 t x width x b standing on its end."""
    shape = K.fuse(K.box((a, width, t), (0, -width / 2, 0)), K.box((t, width, b), (0, -width / 2, 0)))
    shape = K.bolt_holes(shape, [(a * 0.7, 0, -1)], 4.5, t + 2)
    return part(name, shape, material, process, link, joint=joint, role='structure',
                spec=f'L bracket {a:g} x {b:g} x {width:g} mm, {t:g} mm', label=name.replace('_', ' '))


# --- link structure ----------------------------------------------------------------------------------------------------

def extrusion(width: float, height: float, length: float, name: str, link: str, label: str = ''):
    """Cut length of T-slot extrusion along +z from 0 (cross-section centred)."""
    shape = K.tslot_extrusion(width, height, length)
    return part(name, shape, 'aluminium', 'cut', link, source='purchased', role='structure',
                spec=f'T-slot extrusion {round(width / 20) * 20:g}x{round(height / 20) * 20:g}, cut to {length:g} mm',
                label=label or f'{name.replace("_", " ")}')


def link_tube(od: float, wall: float, length: float, name: str, link: str, label: str = ''):
    """Round aluminium tube along +z from 0."""
    shape = K.tube(od / 2, od / 2 - wall, length)
    return part(name, shape, 'aluminium', 'cut', link, source='purchased', role='structure',
                spec=f'round tube {od:g} x {wall:g} mm, cut to {length:g} mm', label=label or name.replace('_', ' '))


def housing_shell(hw: float, length: float, wall: float, name: str, link: str, material: str, process: str):
    """Open square shell along +z (short links that hold a motor)."""
    shape = K.cut(K.box((2 * hw, 2 * hw, length), (-hw, -hw, 0)),
                  K.box((2 * hw - 2 * wall, 2 * hw - 2 * wall, length + 2), (-hw + wall, -hw + wall, -1)))
    return part(name, shape, material, process, link, role='structure',
                spec=f'square housing {2 * hw:g} mm, wall {wall:g} mm, length {length:g} mm',
                label=name.replace('_', ' '))


def block(size, corner, name, link, material, process, role='structure', label='', spec=''):
    shape = K.box(size, corner)
    return part(name, shape, material, process, link, role=role,
                spec=spec or f'block {size[0]:g} x {size[1]:g} x {size[2]:g} mm', label=label or name.replace('_', ' '))


def base_plate(radius: float, tp: float, z_top: float, g: MotorGeom, name: str, link: str, joint: str, material: str,
               process: str):
    """Arm base: disc plate (top at z_top, thickness tp) that carries the first motor on four legs to the floor."""
    disc = K.cylinder(radius, tp, (0, 0, z_top - tp))
    disc = K.cut(disc, K.cylinder(g.pilot / 2 + 0.2, tp + 2, (0, 0, z_top - tp - 1)))
    disc = K.bolt_holes(disc, K.square_pattern(g.pitch, z_top - tp - 1), g.hole_d + 0.2, tp + 2)
    r_leg = g.flange / 2 + 6
    legs = [K.cylinder(5, z_top - tp, (sx * r_leg * 0.9, sy * r_leg * 0.9, 0)) for sx in (-1, 1) for sy in (-1, 1)]
    shape = K.fuse(disc, *legs)
    return part(name, shape, material, process, link, joint=joint, role='mount',
                spec=f'base plate dia {2 * radius:g} mm, {tp:g} mm, standing on four legs; carries the joint motor',
                label='Base plate')


def gantry_carriage(sx: float, sy: float, sz: float, name: str, link: str, material: str, process: str,
                    center=(0.0, 0.0, 0.0), label: str = ''):
    """Carriage plate with four linear-bearing seats (round pockets) in its top face."""
    shape = K.box((sx, sy, sz), (center[0] - sx / 2, center[1] - sy / 2, center[2] - sz / 2))
    pts = [(center[0] + a * sx * 0.3, center[1] + b * sy * 0.3, center[2] + sz / 2 - 6) for a in (-1, 1) for b in (-1, 1)]
    shape = K.bolt_holes(shape, pts, 8.2, 7)
    return part(name, shape, material, process, link, role='structure',
                spec=f'carriage plate {sx:g} x {sy:g} x {sz:g} mm with four 8 mm linear-bearing seats',
                label=label or name.replace('_', ' '))


def suction_cup(radius: float, length: float, name: str, link: str):
    """Vacuum cup on a stem, along +z from 0 to `length` (tip at z = length)."""
    stem = K.cylinder(5.0, length - 8)
    cup = K.cylinder(radius, 8, (0, 0, length - 8))
    cup = K.cut(cup, K.cylinder(radius - 3, 4, (0, 0, length - 4)))
    shape = K.fuse(stem, cup)
    return part(name, shape, 'rubber', 'purchased', link, source='purchased', role='tool',
                spec=f'vacuum cup dia {2 * radius:g} mm on a 10 mm stem', label='Suction cup')


def gripper_parallel(stroke: float, length: float, name_prefix: str, link: str, finger_link: str, material: str,
                     process: str):
    """Parallel gripper in a tool frame whose +z runs to the fingertips at z = length. Returns (body, fixed finger,
    moving finger). The fixed finger lies at y in [-6, 0], the moving finger (own link, own frame at the tool
    origin) at y in [0, 6] when closed and slides along +y by the opening."""
    fl = max(min(40.0, length * 0.45), 12.0)
    body_len = max(length - fl, 8.0)
    housing = K.box((50, stroke + 30, body_len - 6 if body_len > 12 else body_len), (-25, -15, 6 if body_len > 12 else 0))
    flange = K.box((56, 56, 6), (-28, -28, 0))
    flange = K.bolt_holes(flange, K.square_pattern(40.0, -1), 4.5, 8)
    body = K.fuse(housing, flange) if body_len > 12 else housing
    fixed = K.box((20, 6, fl), (-10, -6, length - fl))
    moving = K.box((20, 6, fl), (-10, 0, length - fl))
    b = part(f'{name_prefix}_body', body, material, process, link, role='tool',
             spec=f'parallel gripper body, stroke {stroke:g} mm, tool flange on 40 mm pitch', label='Gripper body')
    f1 = part(f'{name_prefix}_finger_fixed', fixed, material, process, link, role='tool',
              spec='fixed finger 20 x 6 mm', label='Fixed finger')
    f2 = part(f'{name_prefix}_finger_moving', moving, material, process, finger_link, role='tool',
              spec='moving finger 20 x 6 mm, on the prismatic gripper joint', label='Moving finger')
    return b, f1, f2


def tool_block(length: float, name: str, link: str, material: str, process: str):
    flange = K.bolt_holes(K.box((56, 56, 6), (-28, -28, 0)), K.square_pattern(40.0, -1), 4.5, 8)
    stem = K.box((36, 36, max(length - 6, 4.0)), (-18, -18, 6)) if length > 10 else None
    shape = K.fuse(flange, stem) if stem is not None else flange
    return part(name, shape, material, process, link, role='tool',
                spec=f'tool body {length:g} mm with a 40 mm pitch flange', label='Tool body')


def wheel(radius: float, width: float, name: str, link: str, joint: str):
    """Rubber wheel (axis y) with a 2 mm bore, centred on the link origin."""
    shape = K.cylinder(radius, width, (0, -width / 2, 0), (0, 1, 0))
    return part(name, shape, 'rubber', 'purchased', link, source='purchased', joint=joint, role='wheel',
                spec=f'wheel dia {2 * radius:g} x {width:g} mm (envelope)', label=f'Wheel {joint}')


# --- standard parts (BOM only) -----------------------------------------------------------------------------------------

def standard_for_joint(joint: str, link: str, g: MotorGeom, drive_type: str, revolute: bool, ratio: float,
                       lead_m: float | None, stroke_m: float, belt_pitch_mm: float = 2.0) -> list[CadPart]:
    """Bearings by shaft size, bolts by pattern and the transmission parts for one motorised joint."""
    out = []
    des, bore, od, bw = bearing_for(g.shaft_d)
    if revolute:
        out.append(standard(f'bearing_{joint}', f'{des}, {bore:g} x {od:g} x {bw:g} mm deep-groove bearing',
                            link, joint, 2, f'Bearing {joint}', 0.01 * od / 22))
    out.append(standard(f'bolts_{joint}', f'{bolt_size(g.hole_d)} x 12 ISO 4762 socket-head screw, steel (motor flange)',
                        link, joint, 4, f'Motor bolts {joint}', 0.004))
    if drive_type == 'belt':
        length_m = round(2 * stroke_m + 0.3, 2)
        out += [standard(f'belt_{joint}', f'GT2 belt, 6 mm wide, about {length_m:g} m closed or cut length',
                         link, joint, 1, f'Belt {joint}', round(0.01 * length_m, 4)),
                standard(f'pulley_{joint}', f'GT2 20-tooth pulley, 6 mm belt, bore {g.shaft_d:g} mm',
                         link, joint, 1, f'Drive pulley {joint}', 0.012),
                standard(f'idler_{joint}', 'GT2 20-tooth idler pulley, 3 mm bore, with bearing', link, joint, 1,
                         f'Idler {joint}', 0.01)]
    elif drive_type == 'leadscrew':
        lead = f'{(lead_m or 0.008) * 1000:g} mm lead'
        out += [standard(f'leadscrew_{joint}', f'trapezoidal lead screw T8, {lead}, {round(stroke_m + 0.1, 2):g} m long',
                         link, joint, 1, f'Lead screw {joint}', round(0.4 * (stroke_m + 0.1), 3)),
                standard(f'leadnut_{joint}', f'T8 {lead} brass nut with flange', link, joint, 1, f'Lead nut {joint}', 0.03),
                standard(f'coupler_{joint}', f'flexible shaft coupler {g.shaft_d:g} mm to 8 mm', link, joint, 1,
                         f'Coupler {joint}', 0.02)]
    elif drive_type == 'rack':
        out += [standard(f'rack_{joint}', f'module 1 steel rack, {round(stroke_m + 0.1, 2):g} m', link, joint, 1,
                         f'Rack {joint}', round(0.8 * (stroke_m + 0.1), 3)),
                standard(f'pinion_{joint}', f'module 1 steel pinion, 20 teeth, bore {g.shaft_d:g} mm', link, joint, 1,
                         f'Pinion {joint}', 0.05)]
    elif drive_type in ('planetary', 'harmonic', 'wheel_gearbox'):
        out.append(standard(f'gearbox_{joint}', f'{drive_type.replace("_", " ")} gearbox, ratio {ratio:g}:1, input '
                            f'{g.frame} flange (mass not modelled)', link, joint, 1, f'Gearbox {joint}', 0.0,
                            source='purchased'))
    return out


def tslot_hardware(link: str, count: int) -> CadPart:
    return standard(f'tslot_hardware_{link}', 'M5 T-nut and screw set for 20-series T-slot extrusion, with corner '
                    'brackets', link, None, count, f'T-slot hardware {link}', 0.012)


# --- frames ------------------------------------------------------------------------------------------------------------

def frame(origin, z_axis, y_hint) -> np.ndarray:
    """4x4 matrix (mm) whose z column is `z_axis` and whose y column is `y_hint` made perpendicular to it."""
    z = np.asarray(z_axis, float)
    z = z / np.linalg.norm(z)
    y = np.asarray(y_hint, float)
    y = y - z * float(y @ z)
    if np.linalg.norm(y) < 1e-6:
        y = np.array([0.0, 1.0, 0.0]) if abs(z[1]) < 0.9 else np.array([1.0, 0.0, 0.0])
        y = y - z * float(y @ z)
    y = y / np.linalg.norm(y)
    x = np.cross(y, z)
    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = np.round(x, 9), np.round(y, 9), np.round(z, 9), np.round(origin, 4)
    return m


def translate(x=0.0, y=0.0, z=0.0) -> np.ndarray:
    m = np.eye(4)
    m[:3, 3] = (x, y, z)
    return m
