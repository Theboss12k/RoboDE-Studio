// Forward kinematics and extents of a parsed URDF. World frame: metres, radians, z-up (see README.md). No DOM.
import { Euler, Matrix4, Box3, Sphere, Vector3 } from "three";
import type { Parsed } from "./types";

export type BaseTransform = { x: number; y: number; z: number; yaw: number };

/** World z-up to three.js / glTF y-up: rotation of -90 degrees about X. */
export const Z_UP = new Matrix4().makeRotationX(-Math.PI / 2);

export function rpyMatrix(rpy: number[]) {
  return new Matrix4().makeRotationFromEuler(new Euler(rpy[0], rpy[1], rpy[2], "ZYX"));
}
export function originMatrix(o: { xyz: number[]; rpy: number[] }) {
  return rpyMatrix(o.rpy).setPosition(o.xyz[0], o.xyz[1], o.xyz[2]);
}

/** World matrix of every link, given joint positions (metres / radians) and an optional base pose. */
export function linkFrames(parsed: Parsed, positions: Record<string, number>, base?: BaseTransform | null) {
  const root = new Matrix4();
  if (base) root.makeRotationZ(base.yaw).setPosition(base.x, base.y, base.z);
  const frames: Record<string, Matrix4> = { [parsed.base_link]: root };
  for (const j of parsed.joints) {
    const parent = frames[j.parent];
    if (!parent) continue;
    const q = positions[j.name] ?? 0;
    const axis = new Vector3(...(j.axis as [number, number, number]));
    const motion = j.type === "prismatic" ? new Matrix4().makeTranslation(axis.x * q, axis.y * q, axis.z * q)
      : j.type === "fixed" ? new Matrix4() : new Matrix4().makeRotationAxis(axis, q);
    frames[j.child] = parent.clone().multiply(originMatrix(j.origin)).multiply(motion);
  }
  return frames;
}

/** Centre and radius of the robot at its current pose, from link frames and primitive sizes (meshes count as 5 cm). */
export function robotExtent(parsed: Parsed, frames: Record<string, Matrix4>) {
  const box = new Box3();
  for (const m of Object.values(frames)) box.expandByPoint(new Vector3().setFromMatrixPosition(m));
  for (const g of parsed.geometry) {
    if (g.kind !== "visual" || !frames[g.link]) continue;
    const p = g.parameters as Record<string, number & number[]>;
    const half = g.shape === "box" ? Math.max(...p.size) / 2 : g.shape === "mesh" ? 0.05 : Math.max(Number(p.radius) || 0, (Number(p.length) || 0) / 2);
    const centre = new Vector3().setFromMatrixPosition(frames[g.link].clone().multiply(originMatrix(g.origin)));
    box.expandByPoint(centre.clone().addScalar(half)).expandByPoint(centre.clone().addScalar(-half));
  }
  const sphere = box.getBoundingSphere(new Sphere());
  return { centre: sphere.center, radius: Math.max(sphere.radius, 0.08) };
}
