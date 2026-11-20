"""Anthropomorphic serial arm (4-6 DOF). Joint names j1..j<dof>; zero pose straight up, tool up; tool down at
q4 = pi - q2 - q3. dof 5: J5 rolls about the tool axis; dof 6: J5 rotates about the wrist x axis, J6 rolls."""
from __future__ import annotations

import math

import numpy as np

from ..physics import statics
from ..schemas import Requirement, TechnicalSpec
from . import ik as ikm
from . import urdf as U
from .base import Capabilities, IKResult, Joint
from .params import SerialArmParams

ACCEL_TIME_S = 0.25          # time to reach full joint speed (accelerations everywhere = v / ACCEL_TIME_S)
DEF_LIMITS = [(-180, 180), (-110, 110), (-150, 150), (-200, 200), (-180, 180), (-360, 360)]
DEF_SPEEDS = [180, 180, 180, 240, 240, 300]
GREY, DARK, ORANGE = (0.62, 0.66, 0.72, 1), (0.25, 0.28, 0.32, 1), (0.95, 0.55, 0.15, 1)


class SerialArm:
    name = 'serial_arm'
    title = 'Serial arm'
    description = ('Anthropomorphic 4-6 DOF arm on a fixed base: base yaw, shoulder, elbow, wrist pitch, optional '
                   'wrist joints. Keeps the tool pointing down at picks and places.')
    Params = SerialArmParams
    mobile = False

    def parse(self, params):
        return SerialArmParams.model_validate(params or {})

    # --- kinematics ---------------------------------------------------------------------------------------------
    def _limits(self, p):
        lim = p.joint_limits_deg or DEF_LIMITS[:p.dof]
        a = np.radians(np.array(lim, float))
        return a[:, 0], a[:, 1]

    def _frames(self, p, q):
        q = np.asarray(q, float)
        T1 = ikm.rz(q[0])
        T2 = T1 @ ikm.tr(0, 0, p.base_height_m) @ ikm.ry(q[1])
        T3 = T2 @ ikm.tr(0, 0, p.upper_arm_m) @ ikm.ry(q[2])
        T4 = T3 @ ikm.tr(0, 0, p.forearm_m) @ ikm.ry(q[3])
        Tf = T4 @ ikm.tr(0, 0, p.wrist_m)
        T5 = Tf
        if p.dof == 5:
            T5 = T5 @ ikm.rz(q[4])
        elif p.dof == 6:
            T5 = T5 @ ikm.rx(q[4]) @ ikm.rz(q[5])
        return dict(T1=T1, T2=T2, T3=T3, T4=T4, Tf=Tf, T5=T5, tcp=T5 @ ikm.tr(0, 0, p.tool_length_m))

    def fk(self, p, q):
        return self._frames(p, q)['tcp']

    def chain_points(self, p, q):
        f = self._frames(p, q)
        return [np.zeros(3), f['T2'][:3, 3], f['T3'][:3, 3], f['T4'][:3, 3], f['Tf'][:3, 3], f['tcp'][:3, 3]]

    def chain_radii(self, p):
        r = p.link_radius_m
        return [r * 1.1, r, r * 0.9, r * 0.7, max(0.02, r * 0.6)]

    def jacobian(self, p, q):
        return statics.numeric_jacobian(lambda x: self.fk(p, x)[:3, 3], q)

    def _mass_points(self, p, q, payload_kg):
        f = self._frames(p, q)
        pts = [(p.upper_arm_mass_kg, f['T2'] @ [0, 0, p.upper_arm_m / 2, 1]),
               (p.forearm_mass_kg, f['T3'] @ [0, 0, p.forearm_m / 2, 1]),
               (p.wrist_mass_kg, f['T4'] @ [0, 0, p.wrist_m / 2, 1]),
               (p.tool_mass_kg, f['T5'] @ [0, 0, p.tool_length_m / 2, 1]),
               (payload_kg, f['tcp'] @ [0, 0, 0, 1])]
        return [m for m, _ in pts], [x[:3] for _, x in pts]

    def gravity_torques(self, p, q, payload_kg):
        q = np.asarray(q, float)
        masses = self._mass_points(p, q, payload_kg)[0]
        jacs = []
        for i in range(len(masses)):
            jacs.append(statics.numeric_jacobian(lambda x, i=i: self._mass_points(p, x, payload_kg)[1][i], q))
        return statics.gravity_torques(jacs, masses)

    # --- inverse kinematics -------------------------------------------------------------------------------------
    def _analytic_guesses(self, p, target):
        """Closed-form planar solutions for the tool-down wrist point (elbow up first); DLS polishes them."""
        x, y, z = target
        L1, L2 = p.upper_arm_m, p.forearm_m
        dz = z + p.tool_length_m + p.wrist_m - p.base_height_m
        yaw = math.atan2(y, x)
        guesses = []
        for q1, r in ((yaw, math.hypot(x, y)), (yaw - math.copysign(math.pi, yaw), -math.hypot(x, y))):
            D = max(math.hypot(r, dz), 1e-9)
            phi = math.atan2(r, dz)
            alpha = math.acos(np.clip((L1 ** 2 + D ** 2 - L2 ** 2) / (2 * L1 * D), -1, 1))
            for s in (1, -1):                           # elbow up first
                a2 = phi - s * alpha
                a3 = math.atan2(r - L1 * math.sin(a2), dz - L1 * math.cos(a2))
                q = np.zeros(p.dof)
                q[:4] = [q1, a2, a3 - a2, math.pi - a3]
                guesses.append(q)
        return guesses

    def ik(self, p, target, seed=None):
        lo, hi = self._limits(p)
        target = np.asarray(target, float)
        q, pos, ori, it, used = ikm.solve(lambda x: self.fk(p, x), lo, hi, target, seed=seed,
                                          guesses=self._analytic_guesses(p, target), active=[0, 1, 2, 3],
                                          tool_down=True)
        ok = pos < ikm.TOL_M
        note = '' if used else 'tool cannot point straight down here'
        return IKResult(q=q, ok=ok, error_m=pos, orientation_error_rad=ori, iterations=it, note=note)

    # --- ratings ------------------------------------------------------------------------------------------------
    def joints(self, p):
        lo, hi = self._limits(p)
        speeds = np.radians(p.max_speed_deg_s or DEF_SPEEDS[:p.dof])
        torques = p.max_torque_nm or self._default_torques(p)
        return [Joint(f'j{i + 1}', 'revolute', float(lo[i]), float(hi[i]), float(speeds[i]), float(torques[i]))
                for i in range(p.dof)]

    def _stretched(self, p):
        q = np.zeros(p.dof)
        q[1] = math.pi / 2
        return q

    def _default_torques(self, p, sf=1.5):
        tau = np.abs(self.gravity_torques(p, self._stretched(p), p.payload_kg)) * sf
        out = [math.ceil(max(t, 1.0)) for t in tau]
        out[0] = math.ceil(max(0.5 * out[1], 1.0))
        return out

    def _total(self, p):
        return p.upper_arm_m + p.forearm_m + p.wrist_m + p.tool_length_m

    def capabilities(self, p):
        S, h = self._total(p), p.base_height_m
        return Capabilities(payload_kg=p.payload_kg, reach_max_m=math.hypot(S, h), reach_min_m=0.0,
                            z_min_m=max(0.0, h - S), z_max_m=h + S, max_tcp_speed_m_s=p.max_tcp_speed_m_s,
                            max_tcp_accel_m_s2=p.max_tcp_speed_m_s / ACCEL_TIME_S,
                            repeatability_m=p.repeatability_m,
                            gripper_force_n=p.gripper_force_n)

    def workspace(self, p, n=400):
        lo, hi = self._limits(p)
        rng = np.random.default_rng(1)
        pts = np.array([self.fk(p, rng.uniform(lo, hi))[:3, 3] for _ in range(n)])
        cap = self.capabilities(p)
        return dict(reach_max=cap.reach_max_m, reach_min=cap.reach_min_m, z_min=float(pts[:, 2].min()),
                    z_max=float(pts[:, 2].max()), samples=np.round(pts, 3).tolist())

    # --- sizing -------------------------------------------------------------------------------------------------
    def size_from_physics(self, requirements: list[Requirement], spec: TechnicalSpec, hint: dict, mount_z=None):
        """`mount_z`: height of the arm base above the floor when it sits on a vehicle (reach is then taken from the
        requirements only)."""
        t = lambda rid, d=None: ikm.req_target(requirements, rid, d)  # noqa: E731
        p = SerialArmParams.model_validate(hint or {})
        sf = spec.physics_inputs.safety_factor
        upd = {'gripper': (hint or {}).get('gripper', spec.physics_inputs.grip)}
        reach, zmin, zmax = t('reach_max'), t('z_min'), t('z_max')
        zw_lo, zw_hi, pts, bpos = ikm.task_extent(spec)           # the real waypoints (grasp points, carry heights)
        off = mount_z if mount_z is not None else 0.0
        zmin = min(zmin, zw_lo - off) if zmin is not None else zw_lo - off
        zmax = max(zmax, zw_hi - off) if zmax is not None else zw_hi - off
        if mount_z is None and reach is not None:
            reach = max(reach, float(np.linalg.norm(pts - bpos, axis=1).max()) + spec.physics_inputs.clearance_m)
        if reach is not None:
            zlo = zmin if zmin is not None else 0.0
            zhi = zmax if zmax is not None else zlo
            h = float(np.clip((zlo + zhi) / 2, 0.1, 1.5))
            lw, lt = p.wrist_m, p.tool_length_m
            zabs = 0.0 if zlo <= 0 <= zhi else min(abs(zlo), abs(zhi))
            rho = math.sqrt(max(reach ** 2 - zabs ** 2, 0.0))
            dz = max(abs(zhi + lt + lw - h), abs(zlo + lt + lw - h))
            s12 = max(math.hypot(rho, dz) / 0.85, math.sqrt(max(reach ** 2 / 0.85 ** 2 - h ** 2, 0.0)) - lw - lt, 0.3)
            s12 = min(s12, 9.5)
            upd.update(base_height_m=h, upper_arm_m=0.52 * s12, forearm_m=0.48 * s12,
                       link_radius_m=float(np.clip(0.06 * s12, 0.03, 0.15)))
        pay = t('payload')
        upd['payload_kg'] = pay if pay is not None else p.payload_kg
        f = 1 + 0.05 * upd['payload_kg']
        s12 = upd.get('upper_arm_m', p.upper_arm_m) + upd.get('forearm_m', p.forearm_m)
        for key, base_m, share in (('upper_arm_mass_kg', 3.0, 0.52), ('forearm_mass_kg', 2.0, 0.48)):
            if key not in (hint or {}):
                upd[key] = base_m * (s12 * share / 0.4) * f
        g = t('grip_force')
        if g:
            upd['gripper_force_n'] = float(math.ceil(g))
        acc = t('accuracy')
        if acc:
            upd['repeatability_m'] = min(p.repeatability_m, acc * 0.8)
        v = max(p.max_tcp_speed_m_s if 'max_tcp_speed_m_s' in (hint or {}) else 1.0,
                1.3 * t('tcp_speed', 0.0), 1.3 * ACCEL_TIME_S * t('tcp_accel', 0.0))
        upd['max_tcp_speed_m_s'] = v
        if not p.max_speed_deg_s:
            w = max(180.0, math.degrees(1.5 * v / max(s12, 0.1)))
            upd['max_speed_deg_s'] = [w, w, w] + [w * 1.3] * (p.dof - 3)
        q = p.model_copy(update=upd)
        if not p.max_torque_nm:
            upd['max_torque_nm'] = self._default_torques(q, sf)
        return p.model_copy(update=upd)

    # --- URDF ---------------------------------------------------------------------------------------------------
    def add_to_urdf(self, u: U.Urdf, p, root: str):
        """Adds the arm links and joints below link `root` (which must already exist, joint frame = base frame)."""
        r, h, L1, L2, Lw, Lt = (p.link_radius_m, p.base_height_m, p.upper_arm_m, p.forearm_m, p.wrist_m,
                                p.tool_length_m)
        js = {j.name: j for j in self.joints(p)}

        def joint(name, parent, child, xyz, axis):
            j = js[name]
            u.joint(name, 'revolute', parent, child, xyz=xyz, axis=axis, lower=j.lower, upper=j.upper,
                    velocity=j.max_velocity, effort=j.max_effort)

        u.link('turret', [U.vcyl(r * 1.2, 0, h, color=DARK)], mass=p.base_mass_kg, com=(0, 0, h / 2))
        joint('j1', root, 'turret', (0, 0, 0), (0, 0, 1))
        u.link('upper_arm', [U.vcyl(r, 0, L1, color=GREY)], p.upper_arm_mass_kg, (0, 0, L1 / 2))
        joint('j2', 'turret', 'upper_arm', (0, 0, h), (0, 1, 0))
        u.link('forearm', [U.vcyl(r * 0.9, 0, L2, color=GREY)], p.forearm_mass_kg, (0, 0, L2 / 2))
        joint('j3', 'upper_arm', 'forearm', (0, 0, L1), (0, 1, 0))
        u.link('wrist', [U.vcyl(r * 0.7, 0, max(Lw, 0.02), color=DARK)], p.wrist_mass_kg, (0, 0, Lw / 2))
        joint('j4', 'forearm', 'wrist', (0, 0, L2), (0, 1, 0))
        last, off = 'wrist', Lw
        if p.dof >= 5:
            u.link('wrist_2', [U.vcyl(r * 0.6, 0, 0.03, color=DARK)])
            joint('j5', 'wrist', 'wrist_2', (0, 0, Lw), (0, 0, 1) if p.dof == 5 else (1, 0, 0))
            last, off = 'wrist_2', 0.0
        if p.dof == 6:
            u.link('wrist_3', [U.vcyl(r * 0.5, 0, 0.03, color=DARK)])
            joint('j6', 'wrist_2', 'wrist_3', (0, 0, 0), (0, 0, 1))
            last = 'wrist_3'
        w = max(0.02, 0.5 * p.gripper_stroke_m + 0.02)
        shape = (U.vcyl(0.03, 0, Lt, color=ORANGE) if p.gripper == 'vacuum'
                 else U.box((w, 0.03, max(Lt, 0.01)), (0, 0, Lt / 2), color=ORANGE))
        u.link('tool', [shape], p.tool_mass_kg, (0, 0, Lt / 2))
        u.joint('tool_mount', 'fixed', last, 'tool', xyz=(0, 0, off))
        u.link('tcp')
        u.joint('tcp_joint', 'fixed', 'tool', 'tcp', xyz=(0, 0, Lt))

    def build_urdf(self, p, name):
        u = U.Urdf(name)
        u.link('base_link', [U.vcyl(p.link_radius_m * 1.8, 0, 0.02, color=DARK)])
        self.add_to_urdf(u, p, 'base_link')
        return u.xml()
