"""Differential-drive mobile base without an arm. The tool centre point is the centre of the deck top; goods are
carried on the deck. Joints: wheels only (actuated=False, animated by the base motion)."""
from __future__ import annotations

import math

import numpy as np

from ..physics import statics
from ..schemas import Requirement, TechnicalSpec
from . import ik as ikm
from . import urdf as U
from .base import Capabilities, IKResult, Joint
from .params import MobileBaseParams

MAX_SPEED_M_S = 3.0             # plausible family limits: rated values are capped here, so an absurd
MAX_ACCEL_M_S2 = 3.0            # requirement fails visibly instead of being 'met'
BODY, WHEEL, DECK = (0.35, 0.4, 0.5, 1), (0.1, 0.1, 0.1, 1), (0.8, 0.8, 0.82, 1)


def wheel_layout(p):
    """[(joint name, x, y)] for the drive wheels: alternating left (+y) and right (-y), spread along the length."""
    n = p.drive_wheels
    out = []
    for i in range(n):
        side = 'left' if i % 2 == 0 else 'right'
        k = i // 2
        per = (n + 1) // 2 if side == 'left' else n // 2
        x = 0.0 if per == 1 else (-0.5 + k / (per - 1)) * 0.6 * p.length_m
        name = f'wheel_{side}' if k == 0 else f'wheel_{side}_{k + 1}'
        out.append((name, x, (1 if side == 'left' else -1) * p.track_m / 2))
    return out


def _hinted(hint, key, default=0.0):
    return hint[key] if key in hint else default


class MobileBase:
    name = 'mobile_base'
    title = 'Mobile base'
    description = 'Differential-drive base that carries goods on its deck along a path. No arm.'
    Params = MobileBaseParams
    mobile = True

    def parse(self, params):
        return MobileBaseParams.model_validate(params or {})

    def base_params(self, p):
        return p

    def joints(self, p):
        w = p.max_speed_m_s / p.wheel_radius_m
        return [Joint(n, 'continuous', -math.inf, math.inf, w, p.wheel_torque_nm, actuated=False)
                for n, _, _ in wheel_layout(p)]

    def fk(self, p, q=None):
        T = np.eye(4)
        T[2, 3] = p.deck_height_m
        return T

    def chain_points(self, p, q=None):
        x = max(p.length_m / 2 - p.width_m / 2, 0.0)
        z = p.deck_height_m / 2
        return [np.array([-x, 0.0, z]), np.array([x, 0.0, z])]

    def chain_radii(self, p):
        return [p.width_m / 2]

    def jacobian(self, p, q=None):
        return np.zeros((3, 0))

    def gravity_torques(self, p, q, payload_kg):
        return np.zeros(0)

    def ik(self, p, target, seed=None):
        t = np.asarray(target, float)
        err = float(np.linalg.norm(t - self.fk(p)[:3, 3]))
        on_deck = abs(t[0]) <= p.length_m / 2 and abs(t[1]) <= p.width_m / 2 and abs(t[2] - p.deck_height_m) < 1.0
        return IKResult(q=np.zeros(0), ok=bool(on_deck), error_m=err,
                        note='' if on_deck else 'a base without an arm only reaches its own deck')

    def capabilities(self, p):
        r = math.hypot(p.length_m, p.width_m) / 2
        return Capabilities(payload_kg=p.payload_kg, reach_max_m=r, z_min_m=p.deck_height_m, z_max_m=p.deck_height_m,
                            max_tcp_speed_m_s=p.max_speed_m_s, max_tcp_accel_m_s2=p.max_accel_m_s2,
                            repeatability_m=0.01,
                            extra=dict(max_speed_m_s=p.max_speed_m_s, max_accel_m_s2=p.max_accel_m_s2,
                                       max_slope_deg=p.max_slope_deg, battery_wh=p.battery_wh,
                                       wheel_torque_nm=p.wheel_torque_nm))

    def workspace(self, p):
        r = math.hypot(p.length_m, p.width_m) / 2
        pts = [[sx * p.length_m / 2, sy * p.width_m / 2, p.deck_height_m] for sx in (-1, 1) for sy in (-1, 1)]
        return dict(reach_max=r, reach_min=0.0, z_min=p.deck_height_m, z_max=p.deck_height_m, samples=pts)

    def size_from_physics(self, requirements: list[Requirement], spec: TechnicalSpec, hint: dict, extra_mass_kg=0.0):
        t = lambda rid, d=0.0: ikm.req_target(requirements, rid, d)  # noqa: E731
        hint = hint or {}
        p = MobileBaseParams.model_validate(hint)
        pi, sf = spec.physics_inputs, spec.physics_inputs.safety_factor
        upd = {}
        upd['length_m'] = max(p.length_m, pi.object_size_m[0] + 0.2)
        upd['width_m'] = max(p.width_m, pi.object_size_m[1] + 0.2)
        if 'track_m' not in hint:
            upd['track_m'] = min(p.track_m, 0.9 * upd['width_m'])
        if pi.wheel_radius_m:
            upd['wheel_radius_m'] = pi.wheel_radius_m
        upd['mass_kg'] = (pi.vehicle_mass_kg or p.mass_kg) + extra_mass_kg
        upd['payload_kg'] = max(t('payload', p.payload_kg), _hinted(hint, 'payload_kg'))
        upd['max_speed_m_s'] = min(max(1.1 * t('drive_speed'), _hinted(hint, 'max_speed_m_s'), 0.1), MAX_SPEED_M_S)
        upd['max_accel_m_s2'] = min(max(1.1 * t('drive_accel'), _hinted(hint, 'max_accel_m_s2'), 0.1), MAX_ACCEL_M_S2)
        slope = max(t('slope'), pi.slope_deg)
        upd['max_slope_deg'] = max(1.1 * slope, _hinted(hint, 'max_slope_deg'))
        q = p.model_copy(update=upd)
        # wheel torque for the vehicle actually described: (m (a + g sin + g Crr cos) r / n) * SF
        th = math.radians(slope)
        m = q.mass_kg + q.payload_kg
        tq = (m * (q.max_accel_m_s2 + statics.G * math.sin(th) + statics.G * pi.rolling_resistance * math.cos(th))
              * q.wheel_radius_m / q.drive_wheels * sf)
        upd['wheel_torque_nm'] = float(math.ceil(max(tq, 1.1 * t('wheel_torque'), _hinted(hint, 'wheel_torque_nm'))))
        upd['battery_wh'] = max(math.ceil(1.1 * t('battery')), _hinted(hint, 'battery_wh')) or p.battery_wh
        return p.model_copy(update=upd)

    SIZED_FIELDS = frozenset({'length_m', 'width_m', 'track_m', 'max_speed_m_s', 'max_accel_m_s2', 'max_slope_deg',
                              'wheel_torque_nm', 'battery_wh', 'payload_kg'})

    def clamp_to_caps(self, params: dict) -> dict:
        """Drive speed <= MAX_SPEED_M_S and acceleration <= MAX_ACCEL_M_S2, whatever their source."""
        p = self.parse(params)
        return p.model_copy(update={'max_speed_m_s': min(p.max_speed_m_s, MAX_SPEED_M_S),
                                    'max_accel_m_s2': min(p.max_accel_m_s2, MAX_ACCEL_M_S2)}).model_dump()

    def add_to_urdf(self, u: U.Urdf, p):
        """Creates `base_link` (chassis and deck) and the wheel links and joints."""
        r = p.wheel_radius_m
        h = max(p.deck_height_m - r * 0.5, 0.05)
        u.link('base_link', [U.box((p.length_m, p.width_m, h), (0, 0, r * 0.5 + h / 2), color=BODY),
                             U.box((p.length_m, p.width_m, 0.01), (0, 0, p.deck_height_m - 0.005), color=DECK)],
               mass=p.mass_kg, com=(0, 0, p.deck_height_m / 2))
        w = min(0.05, p.track_m / 8)
        layout = {n: (x, y) for n, x, y in wheel_layout(p)}
        for j in self.joints(p):
            x, y = layout[j.name]
            u.link(f'{j.name}_link', [U.cylinder(r, w, rpy=U.RPY_Y_AXIS_CYLINDER, color=WHEEL)], mass=1.0)
            u.joint(j.name, 'continuous', 'base_link', f'{j.name}_link', xyz=(x, y, r), axis=(0, 1, 0),
                    velocity=j.max_velocity, effort=j.max_effort)

    def build_urdf(self, p, name):
        u = U.Urdf(name)
        self.add_to_urdf(u, p)
        u.link('tcp')
        u.joint('tcp_joint', 'fixed', 'base_link', 'tcp', xyz=(0, 0, p.deck_height_m))
        return u.xml()
