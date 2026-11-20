"""Parameter models of the v1 robot families. Families, the robot agent and the catalog (which builds a candidate's
model from datasheet values) all use these. Lengths in metres, angles in degrees here (people read and edit them),
converted to radians inside the family code.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Range = tuple[float, float]


class P(BaseModel):
    model_config = {'extra': 'ignore'}


class SerialArmParams(P):
    """An anthropomorphic arm: J1 base yaw (z), J2 shoulder pitch, J3 elbow pitch, J4 wrist pitch, then optional
    J5 wrist roll about the tool axis and J6 (6 DOF: J4 wrist pitch, J5 wrist yaw-in-plane, J6 tool roll). The tool
    points down (-z) at every pick and place when the arm has a wrist pitch joint (dof >= 4).
    Zero pose: upper arm and forearm straight up, tool pointing up along +z.
    """
    dof: int = Field(default=6, ge=4, le=6)
    base_height_m: float = Field(default=0.15, gt=0, le=3)        # floor/mount to J2 axis
    upper_arm_m: float = Field(default=0.40, gt=0, le=5)          # J2 to J3
    forearm_m: float = Field(default=0.35, gt=0, le=5)            # J3 to wrist centre (J4)
    wrist_m: float = Field(default=0.08, ge=0, le=1)              # wrist centre to tool flange
    tool_length_m: float = Field(default=0.12, ge=0, le=1)        # flange to tool centre point (gripper fingertips)
    link_radius_m: float = Field(default=0.05, gt=0, le=0.5)      # for drawing and capsule collision
    joint_limits_deg: list[Range] = []                            # one per joint; [] = family defaults
    max_speed_deg_s: list[float] = []                             # one per joint; [] = family defaults
    max_torque_nm: list[float] = []                               # one per joint; [] = sized from physics
    base_mass_kg: float = Field(default=4.0, ge=0)
    upper_arm_mass_kg: float = Field(default=3.0, ge=0)
    forearm_mass_kg: float = Field(default=2.0, ge=0)
    wrist_mass_kg: float = Field(default=1.0, ge=0)
    tool_mass_kg: float = Field(default=0.5, ge=0)                # gripper or tool
    gripper: Literal['parallel', 'vacuum', 'magnetic', 'hook', 'fork', 'none'] = 'parallel'
    gripper_force_n: float = Field(default=40.0, ge=0)
    gripper_stroke_m: float = Field(default=0.08, ge=0)
    payload_kg: float = Field(default=3.0, ge=0)                  # rated payload at the flange
    repeatability_m: float = Field(default=0.0005, gt=0)
    max_tcp_speed_m_s: float = Field(default=1.0, gt=0)

    @model_validator(mode='after')
    def lists_match_dof(self):
        for field in ('joint_limits_deg', 'max_speed_deg_s', 'max_torque_nm'):
            values = getattr(self, field)
            if values and len(values) != self.dof:
                raise ValueError(f'{field} needs {self.dof} entries (one per joint), got {len(values)}')
        for lo, hi in self.joint_limits_deg:
            if lo >= hi:
                raise ValueError('Each joint limit needs lower < upper')
        return self


class GantryParams(P):
    """A Cartesian gantry: X and Y prismatic axes on a frame, Z prismatic axis hanging down, optional tool yaw.
    Base frame origin: centre of the footprint on the floor (or mount surface). X and Y travel are centred on it.
    Tool tip height = frame_height_m - z_offset_m - q_z - tool_length_m, where q_z in [0, z_stroke_m] moves the tool
    down.
    """
    x_stroke_m: float = Field(default=1.0, gt=0, le=50)
    y_stroke_m: float = Field(default=0.8, gt=0, le=50)
    z_stroke_m: float = Field(default=0.5, gt=0, le=10)
    frame_height_m: float = Field(default=1.4, gt=0, le=20)       # height of the X/Y beams above the base
    z_offset_m: float = Field(default=0.1, ge=0, le=5)            # beam to Z carriage at q_z = 0
    tool_length_m: float = Field(default=0.12, ge=0, le=2)
    tool_yaw: bool = False                                        # a 4th revolute axis about z
    beam_size_m: float = Field(default=0.08, gt=0, le=1)
    max_speed_m_s: tuple[float, float, float] = (1.0, 1.0, 0.5)
    max_accel_m_s2: tuple[float, float, float] = (3.0, 3.0, 2.0)
    max_force_n: tuple[float, float, float] = (300.0, 300.0, 300.0)
    x_moving_mass_kg: float = Field(default=12.0, ge=0)           # everything the X axis carries
    y_moving_mass_kg: float = Field(default=6.0, ge=0)
    z_moving_mass_kg: float = Field(default=3.0, ge=0)
    tool_mass_kg: float = Field(default=0.5, ge=0)
    gripper: Literal['parallel', 'vacuum', 'magnetic', 'hook', 'fork', 'none'] = 'parallel'
    gripper_force_n: float = Field(default=40.0, ge=0)
    gripper_stroke_m: float = Field(default=0.08, ge=0)
    payload_kg: float = Field(default=10.0, ge=0)
    repeatability_m: float = Field(default=0.0002, gt=0)


class MobileBaseParams(P):
    """A differential-drive base. Base frame origin: centre of the footprint on the floor; deck at deck_height_m."""
    length_m: float = Field(default=0.7, gt=0, le=10)
    width_m: float = Field(default=0.5, gt=0, le=10)
    deck_height_m: float = Field(default=0.3, gt=0, le=3)
    wheel_radius_m: float = Field(default=0.08, gt=0, le=1)
    track_m: float = Field(default=0.45, gt=0, le=10)             # distance between drive wheels
    mass_kg: float = Field(default=40.0, gt=0)                    # without payload
    max_speed_m_s: float = Field(default=1.2, gt=0, le=20)
    max_accel_m_s2: float = Field(default=0.8, gt=0, le=20)
    max_slope_deg: float = Field(default=5.0, ge=0, le=45)
    wheel_torque_nm: float = Field(default=10.0, gt=0)            # per drive wheel, continuous
    drive_wheels: int = Field(default=2, ge=2, le=8)
    battery_wh: float = Field(default=500.0, gt=0)
    payload_kg: float = Field(default=50.0, ge=0)


class MobileManipulatorParams(P):
    base: MobileBaseParams = MobileBaseParams()
    arm: SerialArmParams = SerialArmParams()
    arm_mount_m: tuple[float, float, float] = (0.15, 0.0, 0.0)   # arm mount on top of the deck, base frame
                                                                  # (z is added to deck_height_m)
