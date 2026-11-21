// Hand-written types that are not domain models: URDF parse output and the project/job envelopes.
// Domain models (DesignVersion, SimResult, Candidate, ...) are generated: see ./generated/types.ts.
import type { Analysis as GeneratedAnalysis, DesignVersion as GeneratedDesignVersion, Question } from "./generated/types";

export type Vec3 = [number, number, number];

export type Joint = {
  name: string; type: string; parent: string; child: string; axis: number[];
  origin: { xyz: number[]; rpy: number[] }; min: number | null; max: number | null; velocity: number | null; effort: number | null;
};
export type Geometry = { link: string; kind: "visual" | "collision"; shape: "box" | "sphere" | "cylinder" | "mesh"; parameters: Record<string, unknown>; origin: { xyz: number[]; rpy: number[] } };
/** The parsed URDF the server attaches to versions and analyses (not part of the stored domain model). */
export type Parsed = { name: string; base_link: string; links: string[]; joints: Joint[]; geometry: Geometry[]; mesh_uris: string[] };

/** A version as served by the API: the stored model plus its parsed URDF. */
export type DesignVersion = GeneratedDesignVersion & { parsed?: Parsed };
export type Analysis = GeneratedAnalysis & { parsed?: Parsed };

/** GET /projects/{id}. Everything but id is read defensively. */
export type Project = {
  id: string; title?: string; brief?: string;
  reference?: { text?: string; files?: { name: string; kind?: string }[] } | null;
  stage?: string; questions?: Question[]; answers?: Record<string, unknown>; versions?: number[]; approved_version?: number | null;
  candidates_count?: number; selected_candidate?: string | null; analyses?: string[];
  decision?: { candidate_id: string; action: "approve" | "robot_studio" } | null;
};
export type ProjectSummary = { id: string; title?: string; stage?: string };

export type Job<T = unknown> = { id: string | null; status: "running" | "done" | "error"; progress: string[]; result: T | null; error: string | null };

/** Body of POST /projects/{id}/revise: the version being viewed and the moment on screen (seconds into its simulation). */
export type ReviseRequest = { request: string; base_version: number; t: number; auto_repair?: boolean };
