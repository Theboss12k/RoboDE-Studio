// Types shared by the Studio client. They mirror server/studio/schemas.py; `Parsed` is the parsed URDF the server attaches.
export type Vec3 = [number, number, number];

export type Joint = {
  name: string; type: string; parent: string; child: string; axis: number[];
  origin: { xyz: number[]; rpy: number[] }; min: number | null; max: number | null; velocity: number | null; effort: number | null;
};
export type Geometry = { link: string; kind: "visual" | "collision"; shape: "box" | "sphere" | "cylinder" | "mesh"; parameters: Record<string, unknown>; origin: { xyz: number[]; rpy: number[] } };
export type Parsed = { name: string; base_link: string; links: string[]; joints: Joint[]; geometry: Geometry[]; mesh_uris: string[] };

export type Question = { id: string; text: string; kind: "choice" | "number" | "text" | "boolean"; options?: string[] | null; unit?: string; default?: unknown; why?: string };

export type Marker = { id: string; name: string; position: Vec3; note?: string };
export type TaskObject = { id: string; name: string; mass_kg: number; size_m: Vec3; shape: "box" | "cylinder" | "sphere"; grip: string; fragile: boolean; start_marker: string };
export type TaskAction = { verb: string; object: string; target: string; duration_s: number; description: string };
export type TechnicalSpec = {
  title: string; summary: string; frame: string; actions: TaskAction[]; objects: TaskObject[]; markers: Marker[];
  locomotion: { required: boolean; path: Vec3[]; terrain: string; slope_deg: number };
  environment: string; duty_cycle: string; accuracy_m: number; speed_target: string; payload_description: string;
  physics_inputs: Record<string, unknown>; assumptions: string[]; reference: string;
};

export type SceneBox = { id: string; kind: string; center: Vec3; size: Vec3; yaw: number; color: string; collides: boolean };
export type SceneObject = { id: string; shape: "box" | "cylinder" | "sphere"; center: Vec3; size: Vec3; color: string };
export type BasePose = { position: Vec3; yaw: number };
export type SceneSpec = { floor_size_m: [number, number]; boxes: SceneBox[]; objects: SceneObject[]; markers: Marker[]; robot_base: BasePose; notes: string };

export type RobotConcept = { family: string; name: string; rationale: string; params: Record<string, unknown>; base: BasePose };

export type Quantity = { value: number; unit: string };
export type Calc = { formula: string; inputs: Record<string, Quantity>; result: number; unit: string; note: string };
export type Requirement = { id: string; name: string; category: string; comparator: ">=" | "<=" | "info"; target: number; unit: string; calc: Calc };
export type Check = { requirement_id: string; name: string; required: number | null; actual: number | null; unit: string; passed: boolean | null; detail: string; calc: Calc | null };

export type Frame = { t: number; joints: Record<string, number>; base: [number, number, number] | null; holding: string; tcp: Vec3 | null; phase: string };
export type SimEvent = { t: number; kind: "info" | "warning" | "collision" | "unreachable" | "limit"; text: string };
export type SimResult = { frames: Frame[]; events: SimEvent[]; checks: Check[]; duration_s: number; ok: boolean };

export type DesignVersion = {
  version: number; created: string; change_request: string; change_summary: string; spec: TechnicalSpec; requirements: Requirement[];
  scene: SceneSpec; robot: RobotConcept; urdf: string; sim: SimResult; approved: boolean; parsed?: Parsed;
};

export type SourcedValue = { value: string; unit: string; quote: string; source_url: string };
export type PriceEstimate = { low: number | null; high: number | null; currency: string; date: string; source_url: string; quote: string; note: string };
export type Candidate = {
  id: string; manufacturer: string; model: string; family: string; product_url: string; image_url: string; datasheet_url: string;
  price: PriceEstimate | null; payload_kg: SourcedValue | null; reach_m: SourcedValue | null; dof: SourcedValue | null; repeatability_mm: SourcedValue | null;
  key_specs: SourcedValue[]; confidence: "high" | "medium" | "low"; screen: Check[];
};
export type ManufacturerValue = { key: string; value: number; unit: string; quote: string; page: string; verified: boolean };
export type ManufacturerSpec = { candidate_id: string; document_url: string; document_title: string; family: string; values: ManufacturerValue[]; kinematics_note: string };
export type Difference = { topic: string; generated: string; candidate: string; impact: string; severity: "ok" | "minor" | "major" | "blocking" };
export type Analysis = {
  candidate_id: string; manufacturer_spec: ManufacturerSpec; robot: RobotConcept; urdf: string; sim: SimResult; differences: Difference[];
  unverified: string[]; created: string; parsed?: Parsed;
};

/** GET /api/projects/{id}. Everything but id is read defensively. */
export type Project = {
  id: string; title?: string; brief?: string;
  reference?: { text?: string; files?: { name: string; kind?: string }[] } | null;
  stage?: string; questions?: Question[]; answers?: Record<string, unknown>; versions?: number[]; approved_version?: number | null;
  candidates_count?: number; selected_candidate?: string | null; analyses?: string[];
  decision?: { candidate_id: string; action: "approve" | "robot_studio" } | null;
};
export type ProjectSummary = { id: string; title?: string; stage?: string };
