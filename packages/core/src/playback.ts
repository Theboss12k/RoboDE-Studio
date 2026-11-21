// Playback helpers over a simulation's frames. No DOM, no React.
import type { Frame, SceneObject } from "./generated/types";
import type { Vec3 } from "./types";
import type { BaseTransform } from "./kinematics";

/** Index of the last frame whose time is <= t (frames are in time order). */
export function frameAt(times: number[], t: number) {
  let lo = 0, hi = times.length - 1;
  while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (times[mid] <= t) lo = mid; else hi = mid - 1; }
  return lo;
}

/** Base pose at a frame: the driven base when the frame has one, else the robot's mounting pose. */
export function baseAt(frame: Frame | undefined, robotBase: { position: number[]; yaw: number }): BaseTransform {
  return frame?.base
    ? { x: frame.base[0], y: frame.base[1], z: robotBase.position[2], yaw: frame.base[2] }
    : { x: robotBase.position[0], y: robotBase.position[1], z: robotBase.position[2], yaw: robotBase.yaw };
}

/** Where each task object is at frame `index`: its scene position, carried with the tool while held (keeping the
 *  object-to-tool offset from the moment of the grasp: centre for a parallel gripper, top for suction), or where it
 *  was released. */
export function objectCenters(objects: SceneObject[], frames: Frame[], index: number) {
  const centres: Record<string, Vec3> = Object.fromEntries(objects.map((o) => [o.id, o.center]));
  const offsets: Record<string, Vec3> = {};
  let held = "";
  for (let i = 0; i <= index && i < frames.length; i++) {
    const f = frames[i];
    if (f.holding !== held) held = f.holding && f.tcp && centres[f.holding] ? f.holding : "";
    if (!held || !f.tcp) continue;
    if (!offsets[held] || frames[i - 1]?.holding !== held) {
      const c = centres[held];
      offsets[held] = [c[0] - f.tcp[0], c[1] - f.tcp[1], c[2] - f.tcp[2]];
    }
    const d = offsets[held];
    centres[held] = [f.tcp[0] + d[0], f.tcp[1] + d[1], f.tcp[2] + d[2]];
  }
  return centres;
}
