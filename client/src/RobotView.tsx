// A three.js view of the robot built from its parsed URDF and driven by live joint positions.
import { useMemo } from "react";
import { Canvas } from "@react-three/fiber";
import { Grid, OrbitControls } from "@react-three/drei";
import * as THREE from "three";
import type { Geometry, Parsed } from "./types";

// The same colours, in the same URDF link order, as the Gazebo twin (twin/sdf_builder.py PALETTE).
const PALETTE = ["#d95419", "#2178b5", "#2ca02c", "#9467bd", "#e377c2", "#bcbd22", "#17becf", "#8c564b", "#7f7f7f"];

function rpyMatrix(rpy: number[]) {
  return new THREE.Matrix4().makeRotationFromEuler(new THREE.Euler(rpy[0], rpy[1], rpy[2], "ZYX"));
}
function originMatrix(o: { xyz: number[]; rpy: number[] }) {
  return rpyMatrix(o.rpy).setPosition(o.xyz[0], o.xyz[1], o.xyz[2]);
}

/** World matrix of every link, given joint positions (metres / radians) and an optional base pose. */
export function linkFrames(parsed: Parsed, positions: Record<string, number>, base?: { x: number; y: number; z: number; yaw: number } | null) {
  const root = new THREE.Matrix4();
  if (base) root.makeRotationZ(base.yaw).setPosition(base.x, base.y, base.z);
  const frames: Record<string, THREE.Matrix4> = { [parsed.base_link]: root };
  for (const j of parsed.joints) {
    const parent = frames[j.parent];
    if (!parent) continue;
    const q = positions[j.name] ?? 0;
    const axis = new THREE.Vector3(...(j.axis as [number, number, number]));
    const motion = j.type === "prismatic" ? new THREE.Matrix4().makeTranslation(axis.x * q, axis.y * q, axis.z * q)
      : j.type === "fixed" ? new THREE.Matrix4() : new THREE.Matrix4().makeRotationAxis(axis, q);
    frames[j.child] = parent.clone().multiply(originMatrix(j.origin)).multiply(motion);
  }
  return frames;
}

function Shape({ g, color }: { g: Geometry; color: string }) {
  const p = g.parameters as Record<string, number & number[] & string>;
  if (g.shape === "mesh") return <mesh><boxGeometry args={[0.03, 0.03, 0.03]} /><meshBasicMaterial color="#f2b705" wireframe /></mesh>;
  return (
    <mesh rotation={g.shape === "cylinder" ? [Math.PI / 2, 0, 0] : [0, 0, 0]} castShadow>
      {g.shape === "box" ? <boxGeometry args={p.size as unknown as [number, number, number]} />
        : g.shape === "sphere" ? <sphereGeometry args={[Number(p.radius), 24, 16]} />
        : <cylinderGeometry args={[Number(p.radius), Number(p.radius), Number(p.length), 28]} />}
      <meshStandardMaterial color={color} roughness={0.55} metalness={0.15} />
    </mesh>
  );
}

/** Centre and radius of the robot at its current pose, from link frames and primitive sizes (meshes count as 5 cm). */
export function robotExtent(parsed: Parsed, frames: Record<string, THREE.Matrix4>) {
  const box = new THREE.Box3();
  for (const m of Object.values(frames)) box.expandByPoint(new THREE.Vector3().setFromMatrixPosition(m));
  for (const g of parsed.geometry) {
    if (g.kind !== "visual" || !frames[g.link]) continue;
    const p = g.parameters as Record<string, number & number[]>;
    const half = g.shape === "box" ? Math.max(...p.size) / 2 : g.shape === "mesh" ? 0.05 : Math.max(Number(p.radius) || 0, (Number(p.length) || 0) / 2);
    const centre = new THREE.Vector3().setFromMatrixPosition(frames[g.link].clone().multiply(originMatrix(g.origin)));
    box.expandByPoint(centre.clone().addScalar(half)).expandByPoint(centre.clone().addScalar(-half));
  }
  const sphere = box.getBoundingSphere(new THREE.Sphere());
  return { centre: sphere.center, radius: Math.max(sphere.radius, 0.08) };
}

type Base = { x: number; y: number; z: number; yaw: number } | null;

/** The robot's visual geometry in the z-up world frame. Place it inside a group that converts z-up to three.js y-up. */
export function RobotGeometry({ parsed, positions = {}, base }: { parsed: Parsed; positions?: Record<string, number>; base?: Base }) {
  const frames = useMemo(() => linkFrames(parsed, positions, base), [parsed, positions, base]);
  const colors = useMemo(() => Object.fromEntries(parsed.links.map((l, i) => [l, PALETTE[i % PALETTE.length]])), [parsed]);
  return (
    <>
      {parsed.geometry.filter((g) => g.kind === "visual" && frames[g.link]).map((g, i) => (
        <group key={i} matrixAutoUpdate={false} matrix={frames[g.link].clone().multiply(originMatrix(g.origin))}>
          <Shape g={g} color={colors[g.link]} />
        </group>
      ))}
    </>
  );
}

/** World z-up to three.js y-up. */
export const Z_UP = new THREE.Matrix4().makeRotationX(-Math.PI / 2);

export default function RobotView({ parsed, positions = {}, base, height = 340 }: { parsed: Parsed; positions?: Record<string, number>; base?: Base; height?: number }) {
  // Frame the camera on the robot's rest pose (z-up centre converted to three.js y-up).
  const view = useMemo(() => {
    const { centre, radius } = robotExtent(parsed, linkFrames(parsed, {}));
    const target: [number, number, number] = [centre.x, centre.z, -centre.y];
    const d = radius * 2.6;
    return { target, position: [target[0] + d * 0.75, target[1] + d * 0.6, target[2] + d * 0.9] as [number, number, number], radius };
  }, [parsed]);
  return (
    <div style={{ height, borderRadius: 8, overflow: "hidden", background: "#dfe6ec" }}>
      <Canvas shadows camera={{ position: view.position, fov: 45, near: view.radius / 50, far: view.radius * 200 }} gl={{ preserveDrawingBuffer: true }}>
        <ambientLight intensity={0.7} />
        <directionalLight position={[3, 5, 2]} intensity={1.2} castShadow />
        <Grid args={[10, 10]} cellSize={view.radius < 0.3 ? 0.01 : 0.1} sectionSize={view.radius < 0.3 ? 0.1 : 0.5} cellColor="#b5c0ca" sectionColor="#8593a2" fadeDistance={view.radius * 20} infiniteGrid />
        <group matrixAutoUpdate={false} matrix={Z_UP}><RobotGeometry parsed={parsed} positions={positions} base={base} /></group>
        <OrbitControls target={view.target} makeDefault />
      </Canvas>
    </div>
  );
}
