"""Mobile manipulator: differential-drive base + serial arm mounted on the deck at `arm_mount_m`.
fk/ik work in the mobile base frame (the arm frame shifted by the mount)."""
from __future__ import annotations

import math

import numpy as np

from ..schemas import Requirement, TechnicalSpec
from . import urdf as U
from .base import Capabilities, IKResult, Joint
from .mobile_base import MobileBase
from .params import MobileBaseParams, MobileManipulatorParams, SerialArmParams
from .serial_arm import SerialArm

ARM, BASE = SerialArm(), MobileBase()


class MobileManipulator:
    name = 'mobile_manipulator'
    title = 'Mobile manipulator'
    description = 'Differential-drive base with a serial arm on the deck: drives to a stop, then works with the arm.'
    Params = MobileManipulatorParams
    mobile = True

    def parse(self, params):
        return MobileManipulatorParams.model_validate(params or {})

    def base_params(self, p):
        return p.base

    def _mount(self, p):
        mx, my, mz = p.arm_mount_m
        return np.array([mx, my, p.base.deck_height_m + mz])

    def _shift(self, p):
        T = np.eye(4)
        T[:3, 3] = self._mount(p)
        return T

    def joints(self, p):
        return ARM.joints(p.arm) + BASE.joints(p.base)

    def fk(self, p, q):
        return self._shift(p) @ ARM.fk(p.arm, q)

    def chain_points(self, p, q):
        return [x + self._mount(p) for x in ARM.chain_points(p.arm, q)]

    def chain_radii(self, p):
        return ARM.chain_radii(p.arm)

    def jacobian(self, p, q):
        return ARM.jacobian(p.arm, q)

    def gravity_torques(self, p, q, payload_kg):
        return ARM.gravity_torques(p.arm, q, payload_kg)

    def ik(self, p, target, seed=None):
        return ARM.ik(p.arm, np.asarray(target, float) - self._mount(p), seed)

    def capabilities(self, p):
        a, b = ARM.capabilities(p.arm), BASE.capabilities(p.base)
        m = self._mount(p)
        return Capabilities(payload_kg=min(a.payload_kg, b.payload_kg),
                            reach_max_m=max(a.reach_max_m - float(np.linalg.norm(m)), 0.0), reach_min_m=0.0,
                            z_min_m=max(0.0, m[2] + a.z_min_m), z_max_m=m[2] + a.z_max_m,
                            max_tcp_speed_m_s=a.max_tcp_speed_m_s, max_tcp_accel_m_s2=a.max_tcp_accel_m_s2,
                            repeatability_m=a.repeatability_m, gripper_force_n=a.gripper_force_n, extra=b.extra)

    def workspace(self, p):
        w = ARM.workspace(p.arm)
        m = self._mount(p)
        c = self.capabilities(p)
        w.update(reach_max=c.reach_max_m, z_min=w['z_min'] + m[2], z_max=w['z_max'] + m[2],
                 samples=[(np.array(s) + m).round(3).tolist() for s in w['samples']])
        return w

    def size_from_physics(self, requirements: list[Requirement], spec: TechnicalSpec, hint: dict):
        hint = hint or {}
        mount = np.array(hint.get('arm_mount_m', (0.15, 0.0, 0.0)), float)
        base_hint = hint.get('base', {})
        deck = MobileBaseParams.model_validate(base_hint).deck_height_m
        rel = []
        for r in requirements:                      # arm requirements are relative to the arm mount, not the floor
            if r.id == 'reach_max':
                r = r.model_copy(update=dict(target=r.target + math.hypot(mount[0], mount[1], deck + mount[2])))
            rel.append(r)
        arm = ARM.size_from_physics(rel, spec, hint.get('arm', {}), mount_z=deck + mount[2])
        arm_mass = (arm.base_mass_kg + arm.upper_arm_mass_kg + arm.forearm_mass_kg + arm.wrist_mass_kg
                    + arm.tool_mass_kg)
        base = BASE.size_from_physics(requirements, spec, base_hint, extra_mass_kg=arm_mass)
        return MobileManipulatorParams(base=base, arm=arm, arm_mount_m=tuple(float(x) for x in mount))

    def build_urdf(self, p, name):
        u = U.Urdf(name)
        BASE.add_to_urdf(u, p.base)
        u.link('arm_base', [U.vcyl(p.arm.link_radius_m * 1.8, 0, 0.02, color=(0.25, 0.28, 0.32, 1))])
        u.joint('arm_mount', 'fixed', 'base_link', 'arm_base', xyz=tuple(float(x) for x in self._mount(p)))
        ARM.add_to_urdf(u, p.arm, 'arm_base')
        return u.xml()
