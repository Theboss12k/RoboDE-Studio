"""Point-to-point motion profiles: trapezoidal velocity, collapsing to triangular for short moves."""
import math

import numpy as np


def trapezoid(distance, v_max, a_max):
    """Time-optimal move of `distance` under v_max and a_max.

    Returns dict(t_total, t_accel, v_peak, triangular). Accel and decel phases are equal.
    """
    d, v, a = float(distance), float(v_max), float(a_max)
    if d < 0 or v <= 0 or a <= 0:
        raise ValueError('distance >= 0, v_max > 0 and a_max > 0 are required')
    if d == 0:
        return dict(t_total=0.0, t_accel=0.0, v_peak=0.0, triangular=True)
    if d >= v * v / a:
        t_acc = v / a
        return dict(t_total=d / v + t_acc, t_accel=t_acc, v_peak=v, triangular=False)
    v_peak = math.sqrt(d * a)
    t_acc = v_peak / a
    return dict(t_total=2 * t_acc, t_accel=t_acc, v_peak=v_peak, triangular=True)


def required_for(distance, time, accel_fraction=1 / 3):
    """Peak speed and acceleration to cover `distance` in `time` with accel (and decel) phases of
    accel_fraction*time each: d = v (T - t_a), t_a = f T, a = v / t_a. Returns dict(v_peak, a)."""
    if distance < 0 or time <= 0 or not 0 < accel_fraction <= 0.5:
        raise ValueError('distance >= 0, time > 0 and 0 < accel_fraction <= 0.5 are required')
    t_acc = accel_fraction * time
    v = distance / (time - t_acc)
    return dict(v_peak=v, a=v / t_acc)


def sample(distance, v_max, a_max, dt):
    """Positions along the move every dt seconds, from 0 to exactly `distance` (monotonic)."""
    if dt <= 0:
        raise ValueError('dt must be > 0')
    prof = trapezoid(distance, v_max, a_max)
    T, ta, vp = prof['t_total'], prof['t_accel'], prof['v_peak']
    if T == 0:
        return np.array([0.0, float(distance)])
    t = np.arange(0.0, T, dt)
    t = np.append(t, T)
    a = float(a_max)
    s = np.where(
        t < ta, 0.5 * a * t**2,
        np.where(t <= T - ta, 0.5 * a * ta**2 + vp * (t - ta),
                 distance - 0.5 * a * (T - t) ** 2))
    s = np.clip(np.maximum.accumulate(s), 0.0, distance)
    s[0], s[-1] = 0.0, float(distance)
    return s
