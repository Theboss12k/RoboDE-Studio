// Hand-written types that are not domain models: URDF parse output and the project/job envelopes.
// Domain models (DesignVersion, SimResult, Candidate, ...) are generated: see ./generated/types.ts.
import type { Analysis as GeneratedAnalysis, DesignVersion as GeneratedDesignVersion, FleetAssignment, FleetLimits, Question, StudioBuild } from "./generated/types";

export type Vec3 = [number, number, number];

export type Joint = {
  name: string; type: string; parent: string; child: string; axis: number[];
  origin: { xyz: number[]; rpy: number[] }; min: number | null; max: number | null; velocity: number | null; effort: number | null;
};
export type Geometry = { link: string; kind: "visual" | "collision"; shape: "box" | "sphere" | "cylinder" | "mesh"; parameters: Record<string, unknown>; origin: { xyz: number[]; rpy: number[] } };
/** The parsed URDF the server attaches to versions and analyses (not part of the stored domain model). */
export type Parsed = { name: string; base_link: string; links: string[]; joints: Joint[]; geometry: Geometry[]; mesh_uris: string[] };

/** A version as served by the API: the stored model plus its parsed URDF. */
export type DesignVersion = GeneratedDesignVersion & { parsed?: Parsed; /** Parsed URDF per fleet member id; present when the version has a fleet. */ fleet_parsed?: Record<string, Parsed> };
export type Analysis = GeneratedAnalysis & { parsed?: Parsed };

/** GET /projects/{id}. Everything but id is read defensively. */
export type Project = {
  id: string; title?: string; brief?: string;
  /** Photos of the task, objects or workcell; present only when non-empty. */
  brief_images?: BriefImage[]; /** Set only once the brief has been edited. */ brief_updated?: string;
  reference?: { text?: string; files?: { name: string; kind?: string }[] } | null;
  stage?: string; questions?: Question[]; answers?: Record<string, unknown>; versions?: number[]; approved_version?: number | null;
  candidates_count?: number; selected_candidate?: string | null; analyses?: string[];
  decision?: { candidate_id: string; action: "approve" | "robot_studio" } | null;
  /** Present only when a fleet was suggested or the user changed fleet settings. */
  fleet?: FleetState;
  /** Present only when the project has at least one Robot studio build. */
  studio?: StudioState;
  /** Present only when the user added them on the Questions stage. */
  answer_comments?: Record<string, string>; answer_notes?: { round: number; text: string }[]; answer_images?: AnswerImage[];
};
/** An image the user attached to an answer (question id, or "_general" for the round's note). */
export type AnswerImage = { name: string; question: string; round?: number; mime?: string };
export type BriefImage = { name: string; mime: string };
export type ProjectSummary = { id: string; title?: string; stage?: string };

export type Job<T = unknown> = { id: string | null; status: "running" | "done" | "error"; progress: string[]; result: T | null; error: string | null };

/** Body of POST /projects/{id}/revise: the version being viewed and the moment on screen (seconds into its simulation). */
export type ReviseRequest = { request: string; base_version: number; t: number; auto_repair?: boolean };

/** Project view `fleet`: why a fleet was suggested, whether it is on, the limits, the assignment and the member used for robot search. */
export type FleetState = {
  suggested?: boolean; enabled?: boolean; reasons?: string[]; source?: "spec" | "code" | "both" | "user" | string;
  limits?: FleetLimits | null; assignment?: FleetAssignment | null; member?: string | null;
};
/** Body of PUT /projects/{id}/fleet; every field is optional. */
export type FleetUpdate = { enabled?: boolean; limits?: FleetLimits; member?: string };
/** What POST /projects/{id}/fleet/assign previews: bases, handover points, parking poses and reachable-workspace samples. */
export type FleetPreview = {
  bases: Record<string, { position: Vec3; yaw: number }>;
  handovers: Record<string, Vec3>;
  parks: Record<string, { start: Vec3; end: Vec3 }>;
  workspace_samples: Record<string, Vec3[]>;
};
export type FleetAssignResponse = { assignment: FleetAssignment; preview: FleetPreview; feasible: boolean; notes: string[] };
export type FleetAssignRequest = { base_version?: number; assignment?: FleetAssignment };

/** Project view `studio`: build numbers (ascending) and the approved build, if any. */
export type StudioState = { builds: number[]; approved_build: number | null };
/** One row of GET /projects/{id}/studio/builds. */
export type StudioBuildSummary = { build: number; design_version: number; member: string | null; parent_build: number | null; created: string; summary: string; approved: boolean };
/** GET /projects/{id}/studio/builds/{n}: the stored build plus its parsed studio URDF (mesh uris relative to `urdf/`). */
export type StudioBuildView = StudioBuild & { parsed: Parsed };
/** Body of POST /projects/{id}/studio/builds. */
export type StudioBuildRequest = { design_version?: number | null; base_build?: number | null; request?: string | null; pinned?: Record<string, string> | null };
