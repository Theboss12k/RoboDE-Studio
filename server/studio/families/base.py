"""The robot-family plug-in protocol. Adding a robot type means one module implementing `Family` and one line in
`families/__init__.py` (REGISTRY).

A family works in its own base frame (see schemas.py). The simulator converts world targets into the base frame
with the robot's `BasePose` before calling `ik`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar, Protocol

import numpy as np
from pydantic import BaseModel

from ..schemas import Requirement, TechnicalSpec


@dataclass
class Joint:
    name: str                       # URDF joint name; Frame.joints uses these names
    type: str                       # 'revolute' | 'prismatic' | 'continuous'
    lower: float                    # rad or m
    upper: float
    max_velocity: float             # rad/s or m/s
    max_effort: float               # N*m or N
    actuated: bool = True           # False for wheels animated by the base motion


@dataclass
class IKResult:
    q: np.ndarray
    ok: bool                        # reached within tolerance and inside joint limits
    error_m: float                  # position error at the end
    orientation_error_rad: float = 0.0
    iterations: int = 0
    note: str = ''


@dataclass
class Capabilities:
    """Rated values of a robot, compared against the requirements by code."""
    payload_kg: float
    reach_max_m: float
    reach_min_m: float = 0.0
    z_min_m: float = 0.0
    z_max_m: float = 0.0
    max_tcp_speed_m_s: float = 0.0
    max_tcp_accel_m_s2: float = 0.0
    repeatability_m: float = 0.0
    gripper_force_n: float = 0.0
    extra: dict = field(default_factory=dict)   # e.g. mobile: max_speed_m_s, max_slope_deg, battery_wh, wheel_torque_nm


class Family(Protocol):
    name: ClassVar[str]             # registry key
    title: ClassVar[str]
    description: ClassVar[str]
    Params: ClassVar[type[BaseModel]]
    mobile: ClassVar[bool]          # True: the base drives (Frame.base is set)

    def parse(self, params: dict) -> BaseModel: ...
    def size_from_physics(self, requirements: list[Requirement], spec: TechnicalSpec, hint: dict) -> BaseModel:
        """Params that meet the requirements, starting from the robot agent's `hint` (partial params). Code decides
        the numbers: lengths from reach, torques from statics, gripper force from grip, and so on."""
    def joints(self, p: BaseModel) -> list[Joint]: ...          # q order = actuated joints in this order
    def fk(self, p: BaseModel, q: np.ndarray) -> np.ndarray: ...  # 4x4 tool-centre-point pose in the base frame
    def chain_points(self, p: BaseModel, q: np.ndarray) -> list[np.ndarray]:
        """Base-frame points base -> tool centre point; consecutive pairs are capsules for collision checks."""
    def chain_radii(self, p: BaseModel) -> list[float]: ...     # one capsule radius per segment
    def jacobian(self, p: BaseModel, q: np.ndarray) -> np.ndarray: ...  # 3 x n positional Jacobian of the TCP
    def gravity_torques(self, p: BaseModel, q: np.ndarray, payload_kg: float) -> np.ndarray:
        """Static joint torques/forces (N*m or N) holding the links and the payload at q (physics.statics)."""
    def ik(self, p: BaseModel, target: np.ndarray, seed: np.ndarray | None = None) -> IKResult:
        """Put the TCP on `target` (base frame), tool pointing down where the family can. Multiple seeds inside."""
    def workspace(self, p: BaseModel) -> dict: ...  # {reach_max, reach_min, z_min, z_max, samples: [[x,y,z],...]}
    def capabilities(self, p: BaseModel) -> Capabilities: ...
    def build_urdf(self, p: BaseModel, name: str) -> str:
        """URDF XML from primitive boxes and cylinders; joint names equal `joints()` names (wheels included)."""
