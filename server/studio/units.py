"""Unit handling: convert documented units to the SI units the twin uses (m, rad, m/s, rad/s)."""
import math
import re

LENGTH = {'m': 1.0, 'meter': 1.0, 'meters': 1.0, 'metre': 1.0, 'metres': 1.0, 'mm': 1e-3, 'millimeter': 1e-3,
          'millimeters': 1e-3, 'millimetre': 1e-3, 'millimetres': 1e-3, 'cm': 1e-2, 'um': 1e-6, 'µm': 1e-6,
          'in': 0.0254, 'inch': 0.0254, 'inches': 0.0254, 'ft': 0.3048}
ANGLE = {'rad': 1.0, 'radian': 1.0, 'radians': 1.0, 'deg': math.pi / 180, 'degree': math.pi / 180,
         'degrees': math.pi / 180, '°': math.pi / 180, 'mrad': 1e-3, 'rev': 2 * math.pi, 'revolution': 2 * math.pi,
         'revolutions': 2 * math.pi, 'turn': 2 * math.pi}
MASS = {'kg': 1.0, 'kilogram': 1.0, 'kilograms': 1.0, 'g': 1e-3, 'gram': 1e-3, 'grams': 1e-3, 'lb': 0.45359237,
        'lbs': 0.45359237, 'pound': 0.45359237, 'pounds': 0.45359237}
PER_MINUTE = {'rpm': 2 * math.pi / 60}


def parse(unit):
    """(dimension, factor to SI) for a unit string, or None. Dimensions: length, angle, linear_speed, angular_speed."""
    text = (unit or '').strip().lower().replace(' ', '').replace('per', '/').replace('sec', 's')
    text = text.removesuffix('ond').removesuffix('onds')
    if text in PER_MINUTE:
        return 'angular_speed', PER_MINUTE[text]
    if text in LENGTH:
        return 'length', LENGTH[text]
    if text in ANGLE:
        return 'angle', ANGLE[text]
    if text in MASS:
        return 'mass', MASS[text]
    match = re.fullmatch(r'(.+?)/(s|min)', text) or re.fullmatch(r'(.+?)_?(?:per)?_?(s|min)', text)
    if match:
        base, per = match.group(1), 60.0 if match.group(2) == 'min' else 1.0
        if base in LENGTH:
            return 'linear_speed', LENGTH[base] / per
        if base in ANGLE:
            return 'angular_speed', ANGLE[base] / per
    return None


def twin_scale(param_unit, joint_type, mode):
    """Magnitude of the factor from the documented unit to the joint's SI unit, or None if they don't match."""
    parsed = parse(param_unit)
    if not parsed:
        return None
    dimension, factor = parsed
    linear = joint_type == 'prismatic'
    expected = ('length' if linear else 'angle') if mode == 'joint_position' else ('linear_speed' if linear else 'angular_speed')
    return factor if dimension == expected else None


def number(text):
    """The first number in a text (thousands separators and decimal commas handled), or None."""
    match = re.search(r'[-+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[-+]?\d+(?:[.,]\d+)?', str(text))
    if not match:
        return None
    raw = match.group(0)
    raw = raw.replace(',', '') if re.search(r',\d{3}', raw) else raw.replace(',', '.')
    return float(raw)


def to_si(value, unit, dimension):
    """A number from `value` (a string such as "1,300 mm" or "5") in SI for `dimension`, using `unit` or the unit
    written after the number. None if it can't be read or the unit has another dimension."""
    num = number(value)
    if num is None:
        return None
    tail = re.sub(r'^\s*[-+]?[\d.,]+\s*', '', str(value)).strip()
    parsed = parse(unit) or parse(tail.split(' ')[0] if tail else '')
    if not parsed:
        return None
    return num * parsed[1] if parsed[0] == dimension else None
