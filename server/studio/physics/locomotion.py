"""Mobile base: drive speed and acceleration, wheel torque on a slope, battery energy per shift."""
import math

from ..schemas import Calc, Quantity
from .motion import required_for
from .statics import G

DEFAULT_WHEEL_RADIUS_M = 0.08
EFFICIENCY = 0.7
HOTEL_FRACTION = 0.15
SHIFT_HOURS = 8.0


def _q(v, unit=''):
    return Quantity(value=float(v), unit=unit)


def estimate_vehicle_mass(payload_kg):
    """Vehicle mass (kg) when unknown: 25 kg + 1.0 x payload."""
    return 25.0 + 1.0 * payload_kg


def wheel_radius(radius_m):
    return DEFAULT_WHEEL_RADIUS_M if radius_m is None else float(radius_m)


def drive(distance_m, time_s=None, speed_m_s=None, accel_fraction=1 / 3):
    """Drive speed and acceleration: (speed Calc, accel Calc).

    With a time budget: trapezoid with accel phases of accel_fraction*T. With a given speed v the implied
    time is T = d / (v (1 - f))."""
    d, f = float(distance_m), accel_fraction
    if speed_m_s is not None:
        v = float(speed_m_s)
        t = d / (v * (1 - f))
        note = 'Speed given; time implied by the trapezoid profile.'
    elif time_s is not None:
        t = float(time_s)
        v = required_for(d, t, f)['v_peak']
        note = 'Speed from the time budget.'
    else:
        raise ValueError('time_s or speed_m_s is required')
    a = v / (f * t)
    ins = {'d': _q(d, 'm'), 'T': _q(t, 's'), 'f': _q(f)}
    return (Calc(formula='v = d / (T (1 - f))', inputs=ins, result=v, unit='m/s', note=note),
            Calc(formula='a = v / (f T)', inputs={**ins, 'v': _q(v, 'm/s')}, result=a, unit='m/s^2'))


def wheel_torque(total_mass_kg, accel_m_s2, slope_deg, rolling_resistance, wheel_radius_m, n_wheels=2,
                 safety_factor=1.5, mass_note=''):
    """Torque per drive wheel: m (a + g sin(th) + g Crr cos(th)) r / n * SF."""
    th = math.radians(slope_deg)
    force = total_mass_kg * (accel_m_s2 + G * math.sin(th) + G * rolling_resistance * math.cos(th))
    tau = force * wheel_radius_m / n_wheels * safety_factor
    ins = {'m_total': _q(total_mass_kg, 'kg'), 'a': _q(accel_m_s2, 'm/s^2'), 'g': _q(G, 'm/s^2'),
           'theta': _q(slope_deg, 'deg'), 'Crr': _q(rolling_resistance), 'r': _q(wheel_radius_m, 'm'),
           'n_wheels': _q(n_wheels, 'wheels'), 'SF': _q(safety_factor)}
    return Calc(formula='tau = m_total (a + g sin(theta) + g Crr cos(theta)) r / n_wheels * SF', inputs=ins,
                result=tau, unit='N*m', note=mass_note)


def battery(total_mass_kg, distance_m, speed_m_s, slope_deg, rolling_resistance, cycles_per_shift,
            efficiency=EFFICIENCY, hotel_fraction=HOTEL_FRACTION):
    """Battery energy per shift (Wh): E_cycle = (m (g Crr cos(th) + g sin(th)) d + m v^2 / 2) / eta * (1 + h),
    times cycles per shift. One-way drive per cycle; no regeneration."""
    th = math.radians(slope_deg)
    e_j = total_mass_kg * (G * rolling_resistance * math.cos(th) + G * math.sin(th)) * distance_m \
        + 0.5 * total_mass_kg * speed_m_s ** 2
    e_cycle_wh = e_j / efficiency * (1 + hotel_fraction) / 3600.0
    ins = {'m_total': _q(total_mass_kg, 'kg'), 'd': _q(distance_m, 'm'), 'v': _q(speed_m_s, 'm/s'),
           'theta': _q(slope_deg, 'deg'), 'Crr': _q(rolling_resistance), 'eta': _q(efficiency),
           'hotel': _q(hotel_fraction), 'cycles': _q(cycles_per_shift, 'cycles/shift')}
    return Calc(formula='E = cycles * (m (g Crr cos(theta) + g sin(theta)) d + m v^2 / 2) / eta * (1 + hotel) / 3600',
                inputs=ins, result=e_cycle_wh * cycles_per_shift, unit='Wh',
                note='Per shift of 8 h; one-way drive per cycle, no regeneration.')
