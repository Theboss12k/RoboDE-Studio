"""Shared data contracts of RoboDE Studio. Every module and agent speaks these types.

Frames and units
- World frame: metres, right-handed, z up. The origin is on the floor; x and y are free (the scene places things).
- The robot base pose (`BasePose`) is given in the world frame. Families work in their own base frame, whose origin
  is the robot's mounting point (`BasePose.position`) and whose x axis points along `BasePose.yaw`.
- Angles are radians in code and data; degrees only in text shown to people.
- Masses in kg, forces in N, torques in N*m, speeds in m/s (or rad/s), times in s, energy in Wh.

Numbers that decide pass/fail come from code (physics/, families/, sim/), never from an LLM.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

Vec3 = tuple[float, float, float]


class Model(BaseModel):
    model_config = {'extra': 'ignore'}


# --- Technical specification (spec agent) -------------------------------------------------------------------------

class Marker(Model):
    """A named point in the world frame: pick-up and drop-off points, waypoints, docking stations."""
    id: str = Field(pattern=r'^[a-z][a-z0-9_]{0,40}$')
    name: str = Field(max_length=120)
    position: Vec3
    note: str = Field(default='', max_length=400)


class TaskObject(Model):
    id: str = Field(pattern=r'^[a-z][a-z0-9_]{0,40}$')
    name: str = Field(max_length=120)
    mass_kg: float = Field(gt=0, le=5000)
    size_m: Vec3                                     # bounding box x, y, z
    shape: Literal['box', 'cylinder', 'sphere'] = 'box'
    grip: Literal['parallel', 'vacuum', 'magnetic', 'hook', 'fork', 'none'] = 'parallel'
    fragile: bool = False
    start_marker: str = ''                           # Marker id where the object starts


class TaskAction(Model):
    """One step of the task, in order. `target` is a Marker id."""
    verb: Literal['pick', 'place', 'move', 'drive', 'wait', 'inspect', 'push', 'press', 'home']
    object: str = ''                                 # TaskObject id, when the step handles an object
    target: str = ''                                 # Marker id
    duration_s: float = Field(default=0.0, ge=0)     # dwell in seconds, ONLY for wait/inspect/press; 0 for other verbs (their time comes from the cycle time)
    description: str = Field(default='', max_length=400)


class Locomotion(Model):
    required: bool = False
    path: list[Vec3] = []                            # world points the base drives through, in order
    terrain: str = Field(default='flat indoor floor', max_length=200)
    slope_deg: float = Field(default=0.0, ge=0, le=45)


class PhysicsInputs(Model):
    """The numeric part of the payload and motion description. The physics engine reads only this."""
    object_mass_kg: float = Field(gt=0, le=5000)
    object_size_m: Vec3
    tool_mass_kg: float | None = Field(default=None, ge=0)   # gripper/tool; None = estimated by physics
    grip: Literal['parallel', 'vacuum', 'magnetic', 'hook', 'fork', 'none'] = 'parallel'
    friction_coefficient: float = Field(default=0.4, gt=0, le=2)
    contact_count: int = Field(default=2, ge=1, le=8)
    cycle_time_s: float = Field(gt=0, le=86400)              # one full task cycle
    positional_accuracy_m: float = Field(default=0.005, gt=0, le=1)
    safety_factor: float = Field(default=1.5, ge=1, le=5)
    clearance_m: float = Field(default=0.05, ge=0, le=2)     # extra reach margin around waypoints
    approach_height_m: float = Field(default=0.10, ge=0, le=2)  # above pick/place points
    peak_acceleration_m_s2: float | None = Field(default=None, gt=0)  # None = from the cycle time
    # Locomotion (used only when TechnicalSpec.locomotion.required)
    travel_distance_m: float = Field(default=0.0, ge=0)
    travel_speed_m_s: float | None = Field(default=None, gt=0)
    slope_deg: float = Field(default=0.0, ge=0, le=45)
    rolling_resistance: float = Field(default=0.015, ge=0, le=0.5)
    vehicle_mass_kg: float | None = Field(default=None, gt=0)  # None = estimated
    wheel_radius_m: float | None = Field(default=None, gt=0)
    shifts_per_day: float = Field(default=1.0, ge=0, le=3)
    cycles_per_hour: float | None = Field(default=None, ge=0)


class Question(Model):
    id: str = Field(pattern=r'^[a-z][a-z0-9_]{0,40}$')
    text: str = Field(max_length=400)
    kind: Literal['choice', 'number', 'text', 'boolean'] = 'text'
    options: list[str] = []
    unit: str = ''
    default: Any = None
    why: str = Field(default='', max_length=300)


class TechnicalSpec(Model):
    title: str = Field(max_length=160)
    summary: str = Field(max_length=2000)
    frame: str = Field(default='World frame: metres, z up, origin on the floor.', max_length=400)
    actions: list[TaskAction] = Field(min_length=1)
    objects: list[TaskObject] = []
    markers: list[Marker] = Field(min_length=1)
    locomotion: Locomotion = Locomotion()
    environment: str = Field(default='indoor', max_length=400)
    duty_cycle: str = Field(default='', max_length=400)
    accuracy_m: float = Field(default=0.005, gt=0)
    speed_target: str = Field(default='', max_length=400)
    payload_description: str = Field(default='', max_length=1000)
    physics_inputs: PhysicsInputs
    assumptions: list[str] = []
    reference: str = Field(default='', max_length=4000)     # what the reference robot (if any) contributed

    def marker(self, marker_id: str) -> Marker:
        for m in self.markers:
            if m.id == marker_id:
                return m
        raise KeyError(f'Unknown marker {marker_id}')


# --- Scene (scene agent) -------------------------------------------------------------------------------------------

class SceneBox(Model):
    id: str = Field(pattern=r'^[a-z][a-z0-9_]{0,40}$')
    kind: Literal['table', 'shelf', 'wall', 'obstacle', 'conveyor', 'pallet', 'box', 'floor_zone'] = 'obstacle'
    center: Vec3                                      # world frame, box centre
    size: Vec3                                        # full extents x, y, z
    yaw: float = 0.0
    color: str = Field(default='#9aa5b1', pattern=r'^#[0-9a-fA-F]{6}$')
    collides: bool = True                             # False for floor markings


class SceneObject(Model):
    id: str                                           # TaskObject id
    shape: Literal['box', 'cylinder', 'sphere'] = 'box'
    center: Vec3
    size: Vec3
    color: str = Field(default='#2f80ed', pattern=r'^#[0-9a-fA-F]{6}$')


class BasePose(Model):
    position: Vec3 = (0.0, 0.0, 0.0)                  # mounting point in the world frame
    yaw: float = 0.0


class SceneSpec(Model):
    floor_size_m: tuple[float, float] = (4.0, 4.0)
    boxes: list[SceneBox] = []
    objects: list[SceneObject] = []
    markers: list[Marker] = []                        # copied from the spec, possibly adjusted onto surfaces
    robot_base: BasePose = BasePose()
    notes: str = Field(default='', max_length=1000)


# --- Robot concept (robot agent) -----------------------------------------------------------------------------------

class RobotConcept(Model):
    family: str                                       # registry name: serial_arm, gantry, mobile_base, mobile_manipulator
    name: str = Field(max_length=120)
    rationale: str = Field(default='', max_length=2000)
    params: dict = {}                                 # family-specific, validated by the family's params model
    base: BasePose = BasePose()


# --- Requirements and checks (physics + sim) -----------------------------------------------------------------------

class Quantity(Model):
    value: float
    unit: str = ''


class Calc(Model):
    """A traceable calculation: what was computed, how, from what."""
    formula: str                                      # e.g. "F = m (g + a) / (n mu) * SF"
    inputs: dict[str, Quantity]
    result: float
    unit: str
    note: str = ''


class Requirement(Model):
    id: str                                           # e.g. reach_max, payload, grip_force, joint_torque_j2
    name: str
    category: Literal['reach', 'payload', 'grip', 'speed', 'acceleration', 'torque', 'locomotion', 'accuracy',
                      'energy', 'workspace', 'cycle']
    comparator: Literal['>=', '<=', 'info'] = '>='  # the robot's value must be >= / <= the target; info = no check
    target: float
    unit: str
    calc: Calc


class Check(Model):
    requirement_id: str
    name: str
    required: float | None
    actual: float | None
    unit: str
    passed: bool | None                               # None = could not be checked (missing or unverified value)
    detail: str = ''
    calc: Calc | None = None                          # how `actual` was obtained


# --- Simulation result ---------------------------------------------------------------------------------------------

class Frame(Model):
    t: float                                          # s from the start
    joints: dict[str, float]                          # URDF joint name -> position (rad or m)
    base: tuple[float, float, float] | None = None    # world x, y, yaw for mobile bases; None = fixed base
    holding: str = ''                                 # object id carried at this instant
    tcp: Vec3 | None = None                           # tool centre point in the world frame (the client hangs the
                                                      # held object here, offset by the grasp point)
    phase: str = ''                                   # action label, e.g. "pick bottle at A"


class SimEvent(Model):
    t: float
    kind: Literal['info', 'warning', 'collision', 'unreachable', 'limit']
    text: str


class SimResult(Model):
    frames: list[Frame]
    events: list[SimEvent] = []
    checks: list[Check] = []
    duration_s: float = 0.0
    ok: bool = False                                  # every checkable requirement passed


# --- Repair and fleet (collision auto-repair, multi-robot plans) ----------------------------------------------------

class RepairAttempt(Model):
    attempt: int = Field(ge=1, le=10)
    request: str = Field(max_length=4000)            # the fix request code sent to the revise agent
    t: float | None = None                           # first collision time (s) of the version it was sent with
    summary: str = Field(default='', max_length=600) # the model's summary (a proposal)
    collisions: int | None = None                    # collision episodes left after code re-simulated (None = unusable)
    checks_passed: int = 0
    checks_total: int = 0
    outcome: Literal['kept', 'worse', 'no_change', 'changed_task', 'invalid']
    note: str = Field(default='', max_length=600)


class RepairLog(Model):
    trigger: Literal['collision'] = 'collision'
    collisions_before: int
    attempts: list[RepairAttempt] = []
    kept: int = 0                                    # attempt number kept; 0 = the original was best
    stop_reason: str = Field(default='', max_length=200)


class FleetHint(Model):                              # spec agent's proposal (lives in SpecAnswer, not TechnicalSpec)
    robots_needed: int = Field(ge=1, le=4)
    reason: str = Field(max_length=400)


class FleetLimits(Model):                            # code-defined form: the user's limits for their preferred robots
    max_robots: int = Field(default=2, ge=2, le=3)
    families: list[str] = []                         # allowed families; [] = any fixed-base family
    max_reach_m: float | None = Field(default=None, gt=0, le=10)
    max_payload_kg: float | None = Field(default=None, gt=0, le=2000)
    budget_usd: float | None = Field(default=None, ge=0)   # stored and shown, not checked in Design
    brands: list[str] = []                                  # stored and shown, not checked in Design
    note: str = Field(default='', max_length=1000)


class FleetMemberAssignment(Model):
    id: str = Field(pattern=r'^r[1-9]$')
    name: str = Field(default='', max_length=80)
    actions: list[int]                               # indices into the full task's actions
    family: str = ''                                 # optional user preference
    base: Vec3 | None = None                         # user-fixed base position (None = planner decides)
    start: Vec3 | None = None                        # park pose at start (None = planner)
    end: Vec3 | None = None                          # park pose at end (None = planner)


class FleetHandover(Model):
    id: str = Field(pattern=r'^h[1-9]$')
    object: str
    from_member: str
    to_member: str
    position: Vec3 | None = None
    fixed: bool = False                              # user fixed the position


class FleetAssignment(Model):
    members: list[FleetMemberAssignment]
    handovers: list[FleetHandover] = []
    source: Literal['auto', 'user'] = 'auto'
    confirmed: bool = False


class FleetMember(Model):
    id: str
    name: str = ''
    spec: TechnicalSpec                              # the member's sub-task (robot_base, handover and park markers included)
    requirements: list[Requirement]
    robot: RobotConcept
    urdf: str
    sim: SimResult                                   # member clock, t from 0
    t_offset_s: float = 0.0                          # start on the shared fleet clock
    limit_checks: list[Check] = []


class FleetPlan(Model):
    task: TechnicalSpec                              # the user's full task, unchanged
    limits: FleetLimits
    assignment: FleetAssignment
    members: list[FleetMember]
    primary: str                                     # member id mirrored in the version's top-level fields
    checks: list[Check] = []                         # fleet-level checks (computed in code)
    events: list[SimEvent] = []                      # robot-robot events on the shared clock
    duration_s: float = 0.0
    period_s: float = 0.0
    ok: bool = False
    trigger: list[str] = []                          # e.g. "spec agent: ...", "code: ...", "user"
    planner_log: list[str] = []


# --- Project versions ----------------------------------------------------------------------------------------------

class DesignVersion(Model):
    version: int
    parent_version: int | None = None                 # the version this one was revised from (None for v1)
    created: str
    change_request: str = ''                          # the user's words that produced this version ('' for v1)
    change_summary: str = ''
    spec: TechnicalSpec
    requirements: list[Requirement]
    scene: SceneSpec
    robot: RobotConcept
    urdf: str                                         # generated URDF XML
    sim: SimResult
    approved: bool = False
    repair: RepairLog | None = Field(default=None, json_schema_extra={'x-optional': True})
    fleet: FleetPlan | None = Field(default=None, json_schema_extra={'x-optional': True})


# --- Catalog (search, extract, analysis) ---------------------------------------------------------------------------

class SourcedValue(Model):
    value: str = Field(max_length=200)
    unit: str = Field(default='', max_length=40)
    quote: str = Field(default='', max_length=600)
    source_url: str = Field(default='', max_length=800)


class PriceEstimate(Model):
    low: float | None = None
    high: float | None = None
    currency: str = Field(default='USD', max_length=8)
    date: str = Field(default='', max_length=40)      # when the price was published or seen
    source_url: str = Field(max_length=800)
    quote: str = Field(max_length=600)
    note: str = Field(default='', max_length=400)     # e.g. "list price, arm only, excluding controller"

    @field_validator('source_url')
    @classmethod
    def http_only(cls, value):
        if not value.startswith(('https://', 'http://')):
            raise ValueError('source_url must be an http(s) URL')
        return value


class Candidate(Model):
    id: str = Field(pattern=r'^[a-z][a-z0-9_]{0,62}$')
    manufacturer: str = Field(max_length=120)
    model: str = Field(max_length=120)
    family: str                                       # serial_arm | gantry | mobile_base | mobile_manipulator | other
    product_url: str = Field(max_length=800)
    image_url: str = Field(default='', max_length=800)  # linked, never stored
    datasheet_url: str = Field(default='', max_length=800)
    price: PriceEstimate | None = None
    payload_kg: SourcedValue | None = None
    reach_m: SourcedValue | None = None
    dof: SourcedValue | None = None
    repeatability_mm: SourcedValue | None = None
    key_specs: list[SourcedValue] = []
    confidence: Literal['high', 'medium', 'low'] = 'medium'
    screen: list[Check] = []                          # quick payload/reach screen, filled by code


class ManufacturerValue(Model):
    key: str                                          # e.g. payload_kg, reach_m, j1_range_deg, j2_max_speed_deg_s,
                                                      # link_a2_m, link_d4_m, mass_kg, repeatability_mm
    value: float
    unit: str
    quote: str = Field(max_length=600)                # verbatim text of the document that states it
    page: str = Field(default='', max_length=40)
    verified: bool = False                            # set by code: quote found in the document AND value in quote


class ManufacturerSpec(Model):
    candidate_id: str
    document_url: str
    document_title: str = ''
    family: str
    values: list[ManufacturerValue]
    kinematics_note: str = Field(default='', max_length=2000)


class Difference(Model):
    topic: str
    generated: str                                    # the generated robot's value, as text
    candidate: str                                    # the candidate's value, as text
    impact: str = ''                                  # plain words: what it means for the task (analysis agent)
    severity: Literal['ok', 'minor', 'major', 'blocking'] = 'minor'   # set by code from the checks


class Analysis(Model):
    candidate_id: str
    manufacturer_spec: ManufacturerSpec
    robot: RobotConcept                               # candidate model built from verified values
    urdf: str
    sim: SimResult
    differences: list[Difference]
    unverified: list[str] = []                        # values shown but excluded from pass/fail
    provenance: dict[str, str] = {}                   # param -> 'verified|derived|carried|default: how the candidate model got it'
    created: str = ''


# --- Photo AR (docs/AR_PHOTO.md) -------------------------------------------------------------------------------------
# Lists, not tuples: the Claude CLI rejects `prefixItems`.

class ArIntrinsics(Model):
    """Pinhole camera in pixels of the stored photo (top-left origin, no distortion)."""
    fx: float
    fy: float
    cx: float
    cy: float
    width: int
    height: int


class ArReference(Model):
    """A known rectangle on the floor: 4 corner pixels [u, v] in order around it, and its side lengths in metres."""
    corners_px: list[list[float]]                    # exactly 4 [u, v]
    size_m: list[float]                              # [length of corner0->1, length of corner1->2], each > 0

    @field_validator('corners_px')
    @classmethod
    def four_pairs(cls, value):
        if len(value) != 4 or any(len(p) != 2 for p in value):
            raise ValueError('corners_px must be exactly 4 [u, v] pairs')
        return value

    @field_validator('size_m')
    @classmethod
    def two_positive(cls, value):
        if len(value) != 2 or not all(v > 0 for v in value):
            raise ValueError('size_m must be 2 positive values')
        return value


class ArCalibration(Model):
    intrinsics: ArIntrinsics
    world_from_camera: list[list[float]]             # 4x4 T_FC, row-major
    scale_source: Literal['reference', 'estimate']
    focal_source: Literal['reference', 'estimate', 'default']
    residual_px: float | None = None                 # RMS reprojection of the 4 corners; None for an estimate
    camera_height_m: float                           # camera centre above the floor
    warnings: list[str] = []


class ArPlacement(Model):
    x: float = 0.0
    y: float = 0.0
    yaw_deg: float = 0.0


class ArScene(Model):
    id: str
    image: str                                       # file name in the scene folder
    width: int
    height: int
    created: str                                     # ISO 8601 UTC
    reference: ArReference | None = None
    calibration: ArCalibration | None = None         # from the reference (trusted scale)
    estimate: ArCalibration | None = None            # from the depth model (approximate scale)
    placement: ArPlacement = ArPlacement()


# --- Site scan (docs/SITE_SCAN.md) ------------------------------------------------------------------------------------
# Lists, not tuples. R is the structure-from-motion frame, A the aligned frame (z up, floor z = 0), S metres.

class ScanVideo(Model):
    file: str
    bytes: int
    duration_s: float
    width: int
    height: int
    fps: float
    rotation_deg: int


class ScanCamera(Model):
    """ONE camera for the whole clip, at keyframe resolution (OpenCV convention)."""
    model: Literal['SIMPLE_RADIAL'] = 'SIMPLE_RADIAL'
    fx: float
    fy: float
    cx: float
    cy: float
    k1: float
    width: int
    height: int
    undistorted: bool = False        # True: the stored frames are pure pinhole images (k1 is informational)


class ScanFrame(Model):
    index: int
    t_s: float
    image: str                                       # frames/NNNN.jpg
    world_from_camera: list[list[float]] | None = None   # 4x4 R_from_C, row-major; None = not registered


class ScanPlane(Model):
    normal: list[float]                              # 3, unit, in R
    d: float                                         # normal . p + d = 0


class ScanScale(Model):
    method: Literal['sheet', 'distance']
    factor: float = Field(gt=0)
    residual: float                                  # relative side disagreement; 0 for distance
    frame: int | None = None
    corners_px: list[list[float]] | None = None
    size_m: list[float] | None = None
    p1: list[float] | None = None                    # in R
    p2: list[float] | None = None
    distance_m: float | None = None


class ScanMesh(Model):
    file: str                                        # "scan.glb"
    vertices: int
    triangles: int


class ScanStats(Model):
    frames_total: int
    frames_registered: int
    reproj_px: float
    points3d: int
    extent: list[float]                              # A-frame size, scan units


class SiteScan(Model):
    id: str
    created: str
    video: ScanVideo
    status: Literal['uploaded', 'reconstructing', 'reconstructed', 'failed']
    error: str | None = None
    camera: ScanCamera | None = None
    frames: list[ScanFrame] = []
    floor: ScanPlane | None = None
    alignment: list[list[float]] | None = None       # A_from_R, 4x4 row-major
    scale: ScanScale | None = None
    mesh: ScanMesh | None = None
    placement: ArPlacement = ArPlacement()
    stats: ScanStats | None = None
