// Shared shapes of the "My site" view (docs/SITE_SCAN.md).
import type { MutableRefObject } from "react";
import type { ThreeEvent } from "@react-three/fiber";
import type { ArPlacement, SceneBox, ScanIntrinsics } from "../../types";

export type P3 = [number, number, number];
export type ToolId = "robot" | "object" | "marker" | "measure" | "scale" | "floor";
/** "3D view" orbits the mesh; "Through the camera" draws the design over a registered keyframe. */
export type SiteMode = "orbit" | "camera";

/** What the user has changed in the site view of one design version, not yet applied to the design (all in W, metres). */
export type SiteEdits = {
  boxes: SceneBox[];
  /** Task points moved by the user, by marker id. */
  markers: Record<string, P3>;
  /** The robot base height set by the Place robot tool (the placement carries x, y and yaw). */
  baseZ: number | null;
  baseEdited: boolean;
  /** Ids of boxes of the design that the user took out (a task point moved off its table leaves the table behind). */
  removed: string[];
};
export const NO_EDITS: SiteEdits = { boxes: [], markers: {}, baseZ: null, baseEdited: false, removed: [] };

/** A dot drawn in the site view, in S. */
export type SiteMark = { s: P3; color: string };

/** A registered keyframe seen as a camera: its image, intrinsics (at the frame resolution) and R_from_C. */
export type CameraView = { index: number; frameUrl: string; k: ScanIntrinsics; worldFromCamera: number[][] };

/** The robot in S: the base, and the sphere (centre, radius) that holds its links and reach. */
export type SiteRobot = { base: P3; centre: P3; radius: number };

/** Everything the 3D layer needs to draw and pick in the site scan. */
export type SiteView = {
  meshUrl: string;
  /** S_from_R, column-major. */
  sFromR: number[];
  placement: ArPlacement;
  /** True while the scan has no scale (the badge says so). */
  unscaled: boolean;
  mode: SiteMode;
  /** The frame of "Through the camera"; null in the 3D view. */
  camera: CameraView | null;
  robot: SiteRobot;
  /** Counters of the "Focus robot" and "Reset view" buttons; the layer acts when one changes. */
  nav: { focus: number; reset: number };
  onFocusRobot: () => void;
  onResetView: () => void;
  /** The orbit pose when the user left the 3D view, so that coming back keeps it. */
  memory: MutableRefObject<{ url: string; position: P3; target: P3 } | null>;
  tool: ToolId | null;
  /** Click handler for the mesh and the boxes. The point is `e.point` (three.js world); delta filters orbit drags. */
  onPick: (e: ThreeEvent<MouseEvent>) => void;
  onReady: () => void;
  onError: (message: string) => void;
  marks: SiteMark[];
  line: { a: P3; b: P3; label: string } | null;
  /** Test hook, only with `?e2e=1` in the page address. */
  e2e: boolean;
};
