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

from pydantic import BaseModel, Field, field_validator, model_serializer

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
    attached: bool = Field(default=False, json_schema_extra={'x-optional': True})   # held by the tool from t=0 (a cabin bolted to the tool, a fixture): its mass counts in every load; dropped from JSON when false

    @model_serializer(mode='wrap')
    def _drop_attached_false(self, handler):
        out = handler(self)
        if not out.get('attached'):
            out.pop('attached', None)
        return out


class TaskAction(Model):
    """One step of the task, in order. `target` is a Marker id."""
    verb: Literal['pick', 'place', 'move', 'drive', 'wait', 'inspect', 'push', 'press', 'home', 'rotate']
    object: str = ''                                 # TaskObject id, when the step handles an object
    target: str = ''                                 # Marker id (none for rotate)
    duration_s: float = Field(default=0.0, ge=0)     # dwell for wait/inspect/press; for move and rotate an optional UPPER time for that motion (0 = none); 0 for pick/place/drive/home/push
    description: str = Field(default='', max_length=400)
    turns: float = Field(default=0.0, json_schema_extra={'x-optional': True})    # rotate: signed number of turns (may be fractional); dropped from JSON when 0
    rpm: float = Field(default=0.0, ge=0, json_schema_extra={'x-optional': True})     # rotate: peak speed in turns per minute (> 0 for rotate); dropped when 0
    ramp_s: float = Field(default=0.0, ge=0, json_schema_extra={'x-optional': True})  # rotate: time to reach (and leave) that speed; 0 = the joint's own acceleration; dropped when 0
    axis: str = Field(default='tool_yaw', json_schema_extra={'x-optional': True})     # rotate: 'tool_yaw' or a joint name; dropped when 'tool_yaw'

    @model_serializer(mode='wrap')
    def _drop_rotate_defaults(self, handler):
        out = handler(self)
        for key, default in (('turns', 0.0), ('rpm', 0.0), ('ramp_s', 0.0), ('axis', 'tool_yaw')):
            if out.get(key) == default:
                out.pop(key, None)
        return out


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
    peak_acceleration_m_s2: float | None = Field(default=None, gt=0)  # None = from the cycle time; the capability the robot needs (>=)
    max_accel_m_s2: float | None = Field(default=None, gt=0, json_schema_extra={'x-optional': True})  # comfort / fragile limit, at most (<=); dropped from JSON when None
    max_jerk_m_s3: float | None = Field(default=None, gt=0, json_schema_extra={'x-optional': True})   # comfort / fragile limit, at most (<=); dropped from JSON when None
    # Locomotion (used only when TechnicalSpec.locomotion.required)
    travel_distance_m: float = Field(default=0.0, ge=0)
    travel_speed_m_s: float | None = Field(default=None, gt=0)
    slope_deg: float = Field(default=0.0, ge=0, le=45)
    rolling_resistance: float = Field(default=0.015, ge=0, le=0.5)
    vehicle_mass_kg: float | None = Field(default=None, gt=0)  # None = estimated
    wheel_radius_m: float | None = Field(default=None, gt=0)
    shifts_per_day: float = Field(default=1.0, ge=0, le=3)
    cycles_per_hour: float | None = Field(default=None, ge=0)

    @model_serializer(mode='wrap')
    def _drop_limits_none(self, handler):
        out = handler(self)
        for key in ('max_accel_m_s2', 'max_jerk_m_s3'):
            if out.get(key) is None:
                out.pop(key, None)
        return out


class Question(Model):
    id: str = Field(pattern=r'^[a-z][a-z0-9_]{0,40}$')
    text: str = Field(max_length=400)
    kind: Literal['choice', 'number', 'text', 'boolean'] = 'text'
    options: list[str] = []
    unit: str = ''
    default: Any = None
    why: str = Field(default='', max_length=300)


class Baseline(Model):
    """The manual way of doing the task, for the time-saved estimate (not for any pass/fail check)."""
    manual_cycle_s: float = Field(gt=0, le=86400)      # seconds one person needs per item
    operators: int = Field(ge=1, le=100)                # people doing the task by hand today
    source: Literal['user', 'estimate']                 # user = given in an answer or in the Time saved editor
    note: str = Field(default='', max_length=400)


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
    baseline: Baseline | None = Field(default=None, json_schema_extra={'x-optional': True})   # dropped from JSON when None

    @model_serializer(mode='wrap')
    def _drop_baseline_none(self, handler):
        out = handler(self)
        if out.get('baseline') is None:
            out.pop('baseline', None)
        return out

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


class ObjectModelPart(Model):
    """One primitive of an object model, in the object's own frame: origin at the centre of the object's box, z up."""
    shape: Literal['box', 'cylinder', 'sphere', 'cone', 'capsule', 'torus']
    center: Vec3                                      # metres
    size: Vec3                                        # full extents; cylinder/cone/capsule axis along z; torus: x = outer diameter, z = tube diameter
    rotation: Vec3 = (0.0, 0.0, 0.0)                  # radians, XYZ
    color: str = Field(pattern=r'^#[0-9a-fA-F]{6}$')
    roughness: float = Field(default=0.6, ge=0, le=1)
    metalness: float = Field(default=0.0, ge=0, le=1)
    opacity: float = Field(default=1.0, ge=0.1, le=1)
    label: str = Field(default='', max_length=60)


class ObjectModel(Model):
    """A recognisable model of a task object made of simple primitives (objects agent)."""
    object_id: str
    parts: list[ObjectModelPart] = Field(min_length=1, max_length=24)
    note: str = Field(default='', max_length=300)


class SceneObject(Model):
    id: str                                           # TaskObject id
    shape: Literal['box', 'cylinder', 'sphere'] = 'box'
    center: Vec3
    size: Vec3
    color: str = Field(default='#2f80ed', pattern=r'^#[0-9a-fA-F]{6}$')
    name: str = Field(default='', json_schema_extra={'x-optional': True})   # the TaskObject's name; dropped from JSON when empty
    model: ObjectModel | None = Field(default=None, json_schema_extra={'x-optional': True})   # dropped from JSON when None

    @model_serializer(mode='wrap')
    def _drop_empty(self, handler):
        out = handler(self)
        for key in ('name', 'model'):
            if not out.get(key):
                out.pop(key, None)
        return out


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


class SavingsInputs(Model):
    """Inputs of the time-saved formulas (physics/savings.py and packages/core/src/savings.ts)."""
    robot_cycle_s: float = Field(gt=0)
    manual_cycle_s: float = Field(gt=0)
    operators: float = Field(ge=1)
    shifts_per_day: float = Field(default=1.0, ge=0)
    hours_per_shift: float = Field(default=8.0, ge=0)
    days_per_week: float = Field(default=5.0, ge=0)
    days_per_year: float = Field(default=250.0, ge=0)
    supervision_min_per_shift: float = Field(default=15.0, ge=0)


class Savings(Model):
    """Every output of the time-saved estimate as a traceable calculation (`result` is the value)."""
    robot_items_per_hour: Calc
    manual_items_per_hour: Calc
    items_per_day: Calc
    manual_hours_per_day: Calc
    supervision_hours_per_day: Calc
    hours_freed_per_day: Calc
    hours_freed_per_week: Calc
    hours_freed_per_year: Calc
    throughput_change: Calc


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
    source: Literal['sim', 'planned'] = Field(default='sim', json_schema_extra={'x-optional': True})   # 'planned' = AI-planned, code-computed (checks agent); dropped from JSON when 'sim'

    @model_serializer(mode='wrap')
    def _drop_sim_source(self, handler):
        out = handler(self)
        if out.get('source') == 'sim':
            out.pop('source', None)
        return out


class PlanConstant(Model):
    """A number the checks agent declares for one planned check, with the reason for it."""
    name: str = Field(max_length=40)
    value: float
    unit: str = Field(max_length=40)
    justification: str = Field(max_length=400)


class CheckPlan(Model):
    """A task-specific check the checks agent proposes: expressions over catalogue names (physics/quantities.py) and
    its own constants. Code verifies and evaluates it (physics/formulas.py); the model never supplies a result."""
    id: str = Field(max_length=60)                    # ^[a-z][a-z0-9_]{0,40}$, checked in code
    name: str = Field(max_length=120)
    why: str = Field(default='', max_length=500)
    expression: str = Field(max_length=400)
    unit: str = Field(max_length=40)                  # the expression's unit
    comparator: Literal['>=', '<=']                   # the expression must be >= / <= the limit
    limit: str = Field(max_length=400)                # an expression too
    unit_limit: str = Field(max_length=40)
    constants: list[PlanConstant] = []


class RejectedPlan(Model):
    id: str = Field(default='', max_length=60)
    reason: str = Field(max_length=600)


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
    check_plan: list[CheckPlan] = Field(default=[], json_schema_extra={'x-optional': True})    # accepted plans of the checks agent, re-evaluated in code on every re-simulation; dropped from JSON when empty
    check_plan_rejected: list[RejectedPlan] = Field(default=[], json_schema_extra={'x-optional': True})   # plans code rejected, with the reason; dropped when empty

    @model_serializer(mode='wrap')
    def _drop_empty_plans(self, handler):
        out = handler(self)
        for key in ('check_plan', 'check_plan_rejected'):
            if not out.get(key):
                out.pop(key, None)
        return out


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
    preferred: bool = Field(default=False, json_schema_extra={'x-optional': True})   # sold by a preferred vendor; dropped from JSON when false

    @model_serializer(mode='wrap')
    def _drop_preferred_false(self, handler):
        out = handler(self)
        if not out.get('preferred'):
            out.pop('preferred', None)
        return out


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


# --- Robot studio (an approved design -> a buildable robot: motors, CAD, digital twin, manufacturer PDF) -------------
# Numbers that decide pass or fail come from code (robot/sizing.py, style.py, cad checks); a model's motor or style choice is a
# proposal until code has checked it. Torques are N*m for revolute joints and N for prismatic joints; speeds are rad/s
# or m/s; all on the JOINT side unless a field says motor side.

ID_PATTERN = r'^[a-z][a-z0-9_]{0,62}$'


class ShopPrice(Model):
    """A price as a shop listed it on a date: an estimate for planning, never a quote."""
    amount: float | None = None
    currency: str = Field(default='INR', max_length=8)
    seen: str = Field(max_length=40)                  # date the page was read (ISO date)
    shop: str = Field(max_length=80)
    source_url: str = Field(max_length=800)
    quote: str = Field(default='', max_length=600)    # verbatim price text from the page
    # unverified = the server's own fetch of the page was blocked (401/403/429 or a bot-challenge page) and the figure is
    # only the model's quote: shown, never used for ranking or sums. Dropped from JSON when verified (the default).
    status: Literal['verified', 'unverified'] = Field(default='verified', json_schema_extra={'x-optional': True})

    @model_serializer(mode='wrap')
    def _drop_verified(self, handler):
        out = handler(self)
        if out.get('status') == 'verified':
            out.pop('status', None)
        return out

    @field_validator('source_url')
    @classmethod
    def http_only(cls, value):
        if not value.startswith(('https://', 'http://')):
            raise ValueError('source_url must be an http(s) URL')
        return value


class SpecValue(Model):
    """One value of a motor or part. verified = quoted on the shop/datasheet page and the quote contains the number
    (checked in code); assumed = from a standard table or rule (e.g. NEMA frame size); defined = a value we specify
    for procurement because nothing suitable was found."""
    key: str = Field(pattern=ID_PATTERN)              # e.g. rated_torque_nm, rated_speed_rpm, current_a, frame
    value: float | None = None
    text: str = Field(default='', max_length=120)     # non-numeric values (frame "NEMA 23", shaft "8 mm D-cut")
    unit: str = Field(default='', max_length=40)
    status: Literal['verified', 'assumed', 'defined']
    quote: str = Field(default='', max_length=600)
    source_url: str = Field(default='', max_length=800)
    note: str = Field(default='', max_length=300)


class JointSizing(Model):
    """What a joint needs, computed in code from the design (gravity_torques over the task frames and a stretched pose,
    link inertia times acceleration, RMS over the frames, safety factors)."""
    joint: str
    kind: Literal['revolute', 'prismatic', 'wheel']
    static_torque: float                              # worst static load (N*m or N)
    accel_torque: float                               # (I + m r^2) * alpha
    continuous: float                                 # RMS over the task * SF 1.25
    peak: float                                       # worst instant * SF 1.5
    speed: float                                      # peak joint speed (rad/s or m/s)
    torque_unit: str = 'N*m'
    speed_unit: str = 'rad/s'
    calcs: list[Calc] = []


class Drive(Model):
    """The transmission between motor and joint. ratio = motor turns per joint turn; for a leadscrew or belt-driven
    linear axis, ratio = motor rad per metre of travel (2*pi/lead or 2*pi/(pulley pitch circumference))."""
    type: Literal['direct', 'planetary', 'harmonic', 'belt', 'leadscrew', 'rack', 'wheel_gearbox']
    ratio: float = Field(gt=0)
    efficiency: float = Field(gt=0, le=1)
    lead_m: float | None = None                       # leadscrew lead or belt pitch-circle travel per motor turn
    name: str = Field(default='', max_length=200)     # gearbox product or standard description


class Rejected(Model):
    """A shop candidate that was not used for a motor or electronics row, and why (code decides the reason)."""
    id: str = Field(max_length=80)
    name: str = Field(max_length=200)
    shop: str = Field(default='', max_length=80)
    url: str = Field(default='', max_length=800)
    reason: str = Field(max_length=400)               # no_price | page_blocked | quote_not_found | price_not_in_quote |
                                                      # missing_values | fails_margin: <detail>


def _drop_empty_keys(out, keys):
    for key in keys:
        if not out.get(key):
            out.pop(key, None)
    return out


class MotorAlternative(Model):
    id: str = Field(pattern=ID_PATTERN)               # stable within a build; used to pin it in a rebuild
    name: str = Field(max_length=200)
    shop: str = Field(default='', max_length=80)
    url: str = Field(default='', max_length=800)
    price: ShopPrice | None = None
    motor_type: Literal['stepper', 'bldc', 'servo', 'dc_gear', 'other'] = 'other'
    values: list[SpecValue] = []
    drive: Drive | None = None                        # the gearing code would pair it with
    passes: bool = False                              # code: verified critical values and margins pass
    reason: str = Field(default='', max_length=400)   # why it passes or fails (code)
    preferred: bool = Field(default=False, json_schema_extra={'x-optional': True})    # sold by a preferred vendor
    price_issue: str = Field(default='', max_length=40, json_schema_extra={'x-optional': True})   # why it has no verified price: no_price | page_blocked | quote_not_found | price_not_in_quote

    @model_serializer(mode='wrap')
    def _drop_empty(self, handler):
        return _drop_empty_keys(handler(self), ('preferred', 'price_issue'))


class MotorChoice(Model):
    joint: str
    source: Literal['shop', 'defined']                # defined = no suitable product found: procure to this spec
    alternative_id: str | None = None                 # the chosen MotorAlternative (None for defined)
    name: str = Field(max_length=200)
    shop: str = Field(default='', max_length=80)
    url: str = Field(default='', max_length=800)
    price: ShopPrice | None = None
    motor_type: Literal['stepper', 'bldc', 'servo', 'dc_gear', 'other'] = 'other'
    values: list[SpecValue] = []                      # rated/peak torque, rated speed, voltage, current, mass, frame...
    drive: Drive
    required: dict[str, float] = {}                   # joint side: continuous, peak, speed
    provided: dict[str, float] = {}                   # joint side, through the drive
    margins: dict[str, float] = {}                    # provided / required (and inertia_ratio, as a value)
    checks: list[Check] = []
    alternatives: list[MotorAlternative] = []
    pinned: bool = False                              # the user chose it ("Use this one")
    preferred: bool = Field(default=False, json_schema_extra={'x-optional': True})    # sold by a preferred vendor
    rejected: list[Rejected] = Field(default=[], json_schema_extra={'x-optional': True})   # candidates not used, with reasons
    calcs: list[Calc] = Field(default=[], json_schema_extra={'x-optional': True})     # motor-side torque and speed after the ratio

    @model_serializer(mode='wrap')
    def _drop_empty(self, handler):
        return _drop_empty_keys(handler(self), ('preferred', 'rejected', 'calcs'))


class ElectronicsItem(Model):
    id: str = Field(pattern=ID_PATTERN)
    role: Literal['driver', 'controller', 'power_supply', 'battery', 'other']
    name: str = Field(max_length=200)
    source: Literal['shop', 'defined']
    qty: int = Field(default=1, ge=1)
    for_joints: list[str] = []
    shop: str = Field(default='', max_length=80)
    url: str = Field(default='', max_length=800)
    price: ShopPrice | None = None
    values: list[SpecValue] = []
    checks: list[Check] = []                          # e.g. driver current >= 1.2 x motor current
    preferred: bool = Field(default=False, json_schema_extra={'x-optional': True})    # sold by a preferred vendor
    rejected: list[Rejected] = Field(default=[], json_schema_extra={'x-optional': True})   # candidates not used, with reasons

    @model_serializer(mode='wrap')
    def _drop_empty(self, handler):
        return _drop_empty_keys(handler(self), ('preferred', 'rejected'))


class StructureStyle(Model):
    style: Literal['extrusion_printed', 'machined_aluminium']
    proposed: Literal['extrusion_printed', 'machined_aluminium']   # the style agent's proposal
    reasons: list[str] = []                           # the agent's reasons (a proposal)
    forced: bool = False                              # code overrode the proposal because a check failed
    checks: list[Check] = []                          # bending, deflection, print envelope, printed-part loads


class BuildFile(Model):
    path: str = Field(max_length=300)                 # relative to the build folder, e.g. parts/link_2.step
    kind: Literal['step', 'stl', 'urdf', 'pdf', 'zip', 'csv', 'png', 'json']
    sha256: str = Field(default='', max_length=64)
    bytes: int = 0


class BuildPart(Model):
    id: str = Field(pattern=ID_PATTERN)
    name: str = Field(max_length=200)
    qty: int = Field(default=1, ge=1)
    material: str = Field(default='', max_length=120)
    process: Literal['printed', 'machined', 'cut', 'purchased', 'standard']
    source: Literal['custom', 'standard', 'purchased']
    mass_kg: float = 0.0
    link: str | None = None                           # URDF link the part belongs to (built in that link's frame)
    joint: str | None = None                          # for motors, gearboxes, bearings
    files: list[BuildFile] = []
    spec: str = Field(default='', max_length=400)     # e.g. "6805-2RS, 25 x 37 x 7 mm" for standard parts
    url: str = Field(default='', max_length=800)


class TwinJointLimit(Model):
    joint: str
    kind: Literal['revolute', 'prismatic', 'wheel']
    lower: float
    upper: float
    max_speed: float                                  # min(URDF limit, motor rated speed / ratio), joint side
    max_accel: float
    max_torque: float                                 # motor peak through the drive, joint side


class TwinLimits(Model):
    joints: list[TwinJointLimit]
    gripper: TwinJointLimit | None = None             # the studio URDF's prismatic `gripper` joint (opening, m)
    dt_s: float = 0.02                                # twin step
    accel_ramp_s: float = 0.2


class StudioBuild(Model):
    build: int                                        # b1, b2, ... within the project
    design_version: int                               # the approved DesignVersion it was built from
    member: str | None = None                         # fleet member built (None = single robot)
    parent_build: int | None = None
    created: str
    request: str = Field(default='', max_length=2000)  # the user's change request ('' for b1)
    summary: str = Field(default='', max_length=2000)
    family: str
    search_date: str = ''                             # when the shops were searched (prices are as seen then)
    sizing: list[JointSizing]
    motors: list[MotorChoice]
    electronics: list[ElectronicsItem] = []
    style: StructureStyle
    parts: list[BuildPart]
    urdf: str                                         # studio URDF: mesh geometry + `gripper` joint
    twin_limits: TwinLimits
    checks: list[Check] = []                          # CAD validity, frames vs joint chain, motor axes, sizing margins
    files: list[BuildFile] = []                       # assembly.step, urdf/robot.urdf, urdf/meshes/*.stl, ...
    approved: bool = False
    approved_at: str | None = None
    sourcing_notes: list[str] = Field(default=[], json_schema_extra={'x-optional': True})   # what the shop search found and why

    @model_serializer(mode='wrap')
    def _drop_empty(self, handler):
        return _drop_empty_keys(handler(self), ('sourcing_notes',))


# --- Digital twin API (stateless, deterministic, no LLM) ---

class TwinTarget(Model):
    joints: dict[str, float] | None = None            # joint targets (rad or m)
    cartesian: Vec3 | None = None                     # tool centre point target in the WORLD frame (tool down)
    gripper: float | None = None                      # gripper opening (m)


class TwinMoveRequest(Model):
    q0: dict[str, float]                              # current joint positions (gripper included when present)
    target: TwinTarget
    speed_scale: float = Field(default=1.0, gt=0, le=2.0)   # >1 asks for more than the limits allow: they bind


class TwinLimitHit(Model):
    t: float
    joint: str
    limit: Literal['position', 'speed', 'acceleration', 'torque', 'ik']
    detail: str = Field(default='', max_length=300)


class TwinMoveResult(Model):
    frames: list[Frame]                               # at twin_limits.dt_s (downsampled to <= 1500)
    reached: bool
    duration_s: float
    limits: list[TwinLimitHit] = []                   # which limits bound, first instants
    torque_use: dict[str, float] = {}                 # peak required / available torque per joint (1.0 = at limit)
    ik_error_m: float | None = None                   # for a cartesian target


# --- Ask bar ---

_OPT = {'x-optional': True}


class AskTurn(Model):
    role: Literal['user', 'assistant']
    text: str = Field(max_length=4000)


class AskContext(Model):
    """What the client is looking at. Only ids and the moment: every number is read from storage by the server."""
    project_id: str | None = Field(default=None, max_length=40, json_schema_extra=_OPT)
    stage: str | None = Field(default=None, max_length=40, json_schema_extra=_OPT)
    version: int | None = Field(default=None, ge=1, json_schema_extra=_OPT)
    t: float | None = Field(default=None, ge=0, json_schema_extra=_OPT)
    candidate: str | None = Field(default=None, max_length=120, json_schema_extra=_OPT)
    build: int | None = Field(default=None, ge=1, json_schema_extra=_OPT)
    selection: str | None = Field(default=None, max_length=200, json_schema_extra=_OPT)


class AskRequest(Model):
    question: str = Field(min_length=1, max_length=2000)
    history: list[AskTurn] = Field(default=[], max_length=6, json_schema_extra=_OPT)   # older turns are dropped by the server
    context: AskContext = Field(default_factory=AskContext, json_schema_extra=_OPT)


class AskCitation(Model):
    kind: Literal['version', 'check', 'requirement', 'build', 'motor', 'candidate', 'brief', 'spec']
    ref: str = Field(max_length=200)
    label: str = Field(max_length=200)


class AskActionTaken(Model):
    kind: str = Field(max_length=40)
    ok: bool
    job_id: str | None = Field(default=None, json_schema_extra=_OPT)
    summary: str = Field(max_length=400)
    error: str | None = Field(default=None, json_schema_extra=_OPT)


class AskConfirm(Model):
    kind: Literal['approve', 'approve_build', 'decision']
    label: str = Field(max_length=200)
    request: dict[str, Any]                            # the body of the existing route (see docs/API_DESIGN.md section 11)


class AskNavigate(Model):
    stage: str = Field(max_length=40)
    version: int | None = Field(default=None, ge=1, json_schema_extra=_OPT)
    build: int | None = Field(default=None, ge=1, json_schema_extra=_OPT)
    candidate: str | None = Field(default=None, max_length=120, json_schema_extra=_OPT)


class AskResponse(Model):
    answer: str                                        # markdown
    citations: list[AskCitation] = []
    actions_taken: list[AskActionTaken] = []           # filled by Java after it executed or refused the proposals
    confirm: list[AskConfirm] = []
    navigate: AskNavigate | None = None
