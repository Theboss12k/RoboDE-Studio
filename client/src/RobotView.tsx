// A three.js view of the robot built from its parsed URDF and driven by live joint positions.
import { useMemo } from "react";
import { Canvas } from "@react-three/fiber";
import { Grid, OrbitControls } from "@react-three/drei";
import { linkFrames, originMatrix, robotExtent, Z_UP } from "@robode/studio-core";
import type { Geometry, Parsed } from "./types";

// The same colours, in the same URDF link order, as the Gazebo twin (twin/sdf_builder.py PALETTE).
const PALETTE = ["#d95419", "#2178b5", "#2ca02c", "#9467bd", "#e377c2", "#bcbd22", "#17becf", "#8c564b", "#7f7f7f"];

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
