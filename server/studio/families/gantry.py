"""Cartesian gantry: x_axis, y_axis (prismatic, centred on the base origin), z_axis (prismatic, down from the beam),
optional tool_yaw. Inverse kinematics is closed form."""
from __future__ import annotations

import math

import numpy as np

from ..physics import statics
from ..schemas import Requirement, TechnicalSpec
from . import ik as ikm
from . import urdf as U
from .base import Capabilities, IKResult, Joint
from .params import GantryParams

STEEL, DARK, ORANGE = (0.72, 0.75, 0.78, 1), (0.25, 0.28, 0.32, 1), (0.95, 0.55, 0.15, 1)


class Gantry:
    name = 'gantry'
    title = 'Cartesian gantry'
    description = ('Frame with X and Y beams and a vertical Z axis (optional tool yaw). Straight-line moves over a '
                   'box-shaped workspace; good for pallets and long, repeatable transfers.')
    Params = GantryParams
    mobile = False

    def parse(self, params):
        return GantryParams.model_validate(params or {})

    def joints(self, p):
        f, v = p.max_force_n, p.max_speed_m_s
        out = [Joint('x_axis', 'prismatic', -p.x_stroke_m / 2, p.x_stroke_m / 2, v[0], f[0]),
               Joint('y_axis', 'prismatic', -p.y_stroke_m / 2, p.y_stroke_m / 2, v[1], f[1]),
               Joint('z_axis', 'prismatic', 0.0, p.z_stroke_m, v[2], f[2])]
        if p.tool_yaw:
            out.append(Joint('tool_yaw', 'revolute', -math.pi, math.pi, 3.0, 5.0))
        return out

    def joint_accels(self, p):
        return list(p.max_accel_m_s2) + ([10.0] if p.tool_yaw else [])

    def _z(self, p, qz):
        return p.frame_height_m - p.z_offset_m - qz - p.tool_length_m

    def fk(self, p, q):
        T = np.eye(4)
        T[:3, 3] = (q[0], q[1], self._z(p, q[2]))
        T[:3, :3] = np.diag([1.0, -1.0, -1.0])               # tool z axis points down
        if p.tool_yaw:
            T = T @ ikm.rz(q[3])
        return T

    def chain_points(self, p, q):
        return [np.array([q[0], q[1], p.frame_height_m]), np.array([q[0], q[1], self._z(p, q[2]) + p.tool_length_m]),
                np.array([q[0], q[1], self._z(p, q[2])])]

    def chain_radii(self, p):
        return [p.beam_size_m / 2, 0.03]

    def jacobian(self, p, q):
        J = np.zeros((3, 4 if p.tool_yaw else 3))
        J[0, 0], J[1, 1], J[2, 2] = 1.0, 1.0, -1.0
        return J

    def gravity_torques(self, p, q, payload_kg):
        tau = np.zeros(4 if p.tool_yaw else 3)
        tau[2] = -(p.z_moving_mass_kg + p.tool_mass_kg + payload_kg) * statics.G   # holds the Z carriage up
        return tau

    def ik(self, p, target, seed=None):
        x, y, z = (float(v) for v in target)
        lo = [j.lower for j in self.joints(p)]
        hi = [j.upper for j in self.joints(p)]
        want = np.array([x, y, p.frame_height_m - p.z_offset_m - p.tool_length_m - z])
        q = np.clip(want, lo[:3], hi[:3])
        full = np.concatenate([q, [0.0]]) if p.tool_yaw else q
        if p.tool_yaw and seed is not None and len(seed) == 4:
            full[3] = seed[3]
        err = float(np.linalg.norm(self.fk(p, full)[:3, 3] - np.array([x, y, z])))
        return IKResult(q=full, ok=err < ikm.TOL_M, error_m=err, iterations=1,
                        note='' if err < ikm.TOL_M else 'outside the strokes')

    def _tcp_z_range(self, p):
        return self._z(p, p.z_stroke_m), self._z(p, 0.0)

    def capabilities(self, p):
        zlo, zhi = self._tcp_z_range(p)
        v = np.array(p.max_speed_m_s)
        a = np.array(p.max_accel_m_s2)
        return Capabilities(payload_kg=p.payload_kg,
                            reach_max_m=math.sqrt((p.x_stroke_m / 2) ** 2 + (p.y_stroke_m / 2) ** 2 + max(zlo, zhi) ** 2),
                            reach_min_m=0.0, z_min_m=zlo, z_max_m=zhi,
                            max_tcp_speed_m_s=float(np.linalg.norm(v[:2])), max_tcp_accel_m_s2=float(np.linalg.norm(a[:2])),
                            repeatability_m=p.repeatability_m, gripper_force_n=p.gripper_force_n)

    def workspace(self, p):
        zlo, zhi = self._tcp_z_range(p)
        pts = [[sx * p.x_stroke_m / 2, sy * p.y_stroke_m / 2, z] for sx in (-1, 0, 1) for sy in (-1, 0, 1)
               for z in (zlo, zhi)]
        c = self.capabilities(p)
        return dict(reach_max=c.reach_max_m, reach_min=0.0, z_min=zlo, z_max=zhi, samples=pts)

    def size_from_physics(self, requirements: list[Requirement], spec: TechnicalSpec, hint: dict):
        t = lambda rid, d=None: ikm.req_target(requirements, rid, d)  # noqa: E731
        p = GantryParams.model_validate(hint or {})
        sf = spec.physics_inputs.safety_factor
        pts = np.array([m.position for m in spec.markers], float)
        try:
            from ..physics import place_base
            b = np.array(place_base(spec).position[:2], float)
        except Exception:                                    # physics not available: bounding-box centre
            b = (pts[:, :2].min(0) + pts[:, :2].max(0)) / 2
        margin = max(0.1, spec.physics_inputs.clearance_m)
        span = np.abs(pts[:, :2] - b).max(0)
        zw_lo, zw_hi, _, _ = ikm.task_extent(spec)
        zlo = min(t('z_min', zw_lo), zw_lo)
        zhi = max(t('z_max', zw_hi), zw_hi)
        top = zhi + margin                                   # highest TCP height needed
        upd = dict(x_stroke_m=max(2 * span[0] + 2 * margin, 0.3), y_stroke_m=max(2 * span[1] + 2 * margin, 0.3),
                   frame_height_m=top + p.z_offset_m + p.tool_length_m,
                   z_stroke_m=max(top - max(zlo, 0.0) + margin, 0.2),
                   gripper=(hint or {}).get('gripper', spec.physics_inputs.grip))
        pay = t('payload')
        upd['payload_kg'] = pay if pay is not None else p.payload_kg
        g = t('grip_force')
        if g:
            upd['gripper_force_n'] = float(math.ceil(g))
        acc = t('accuracy')
        if acc:
            upd['repeatability_m'] = min(p.repeatability_m, acc * 0.8)
        vt, at = t('tcp_speed', 0.0), t('tcp_accel', 0.0)
        vxy = max(p.max_speed_m_s[0], 1.3 * vt)
        axy = max(p.max_accel_m_s2[0], 1.3 * at)
        upd['max_speed_m_s'] = (vxy, vxy, max(p.max_speed_m_s[2], 0.5 * vxy))
        upd['max_accel_m_s2'] = (axy, axy, max(p.max_accel_m_s2[2], 0.7 * axy))
        m = upd['payload_kg'] + p.tool_mass_kg
        fx = (p.x_moving_mass_kg + p.y_moving_mass_kg + p.z_moving_mass_kg + m) * axy * sf
        fy = (p.y_moving_mass_kg + p.z_moving_mass_kg + m) * axy * sf
        az = upd['max_accel_m_s2'][2]
        fz = (p.z_moving_mass_kg + m) * (statics.G + az) * sf
        upd['max_force_n'] = tuple(float(math.ceil(f / 10) * 10) for f in (fx, fy, fz))
        return p.model_copy(update=upd)

    def build_urdf(self, p, name):
        u = U.Urdf(name)
        X, Y, H, b = p.x_stroke_m + 0.2, p.y_stroke_m + 0.2, p.frame_height_m, p.beam_size_m
        shapes = [U.box((b, b, H), (sx * X / 2, sy * Y / 2, H / 2), color=STEEL) for sx in (-1, 1) for sy in (-1, 1)]
        shapes += [U.box((b, Y, b), (sx * X / 2, 0, H), color=STEEL) for sx in (-1, 1)]
        u.link('base_link', shapes, mass=60.0, com=(0, 0, H / 2))
        js = {j.name: j for j in self.joints(p)}

        def prismatic(n, parent, child, xyz, axis):
            j = js[n]
            u.joint(n, 'prismatic', parent, child, xyz=xyz, axis=axis, lower=j.lower, upper=j.upper,
                    velocity=j.max_velocity, effort=j.max_effort)

        u.link('x_carriage', [U.box((b, Y, b), (0, 0, 0), color=DARK)], p.x_moving_mass_kg)
        prismatic('x_axis', 'base_link', 'x_carriage', (0, 0, H), (1, 0, 0))
        u.link('y_carriage', [U.box((b * 1.6, b * 1.6, b), (0, 0, 0), color=DARK)], p.y_moving_mass_kg)
        prismatic('y_axis', 'x_carriage', 'y_carriage', (0, 0, 0), (0, 1, 0))
        ztot = p.z_offset_m + p.z_stroke_m + p.tool_length_m
        u.link('z_carriage', [U.vcyl(0.025, -ztot, 0.0, color=STEEL)], p.z_moving_mass_kg, (0, 0, -ztot / 2))
        prismatic('z_axis', 'y_carriage', 'z_carriage', (0, 0, -p.z_offset_m), (0, 0, -1))
        last = 'z_carriage'
        if p.tool_yaw:
            u.link('tool_head', [U.vcyl(0.035, -0.04, 0.0, color=DARK)])
            j = js['tool_yaw']
            u.joint('tool_yaw', 'revolute', last, 'tool_head', axis=(0, 0, 1), lower=j.lower, upper=j.upper,
                    velocity=j.max_velocity, effort=j.max_effort)
            last = 'tool_head'
        Lt = p.tool_length_m
        u.link('tool', [U.vcyl(0.03, -Lt, 0.0, color=ORANGE)], p.tool_mass_kg, (0, 0, -Lt / 2))
        u.joint('tool_mount', 'fixed', last, 'tool')
        u.link('tcp')
        u.joint('tcp_joint', 'fixed', 'tool', 'tcp', xyz=(0, 0, -Lt))
        return u.xml()
