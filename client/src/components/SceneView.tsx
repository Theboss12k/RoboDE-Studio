// The task scene in 3D: floor, boxes, marker pins, task objects and the robot, all in one canvas.
// World coordinates are z-up (as the server uses); one group converts them to three.js y-up.
import { useMemo, useRef, type RefObject } from "react";
import { Canvas, useFrame } from "@react-three/fiber";
import { Grid, OrbitControls } from "@react-three/drei";
import { Vector3 } from "three";
import { linkFrames, objectCenters, robotExtent, Z_UP } from "@robode/studio-core";
import { RobotGeometry } from "../RobotView";
import type { BasePose, Frame, Marker, Parsed, SceneObject, SceneSpec, Vec3 } from "../types";

type Base = { x: number; y: number; z: number; yaw: number };

function ObjectMesh({ o, centre }: { o: SceneObject; centre: Vec3 }) {
  return (
    <mesh position={centre} rotation={o.shape === "cylinder" ? [Math.PI / 2, 0, 0] : [0, 0, 0]} castShadow>
      {o.shape === "sphere" ? <sphereGeometry args={[o.size[0] / 2, 24, 16]} />
        : o.shape === "cylinder" ? <cylinderGeometry args={[o.size[0] / 2, o.size[0] / 2, o.size[2], 28]} />
        : <boxGeometry args={o.size} />}
      <meshStandardMaterial color={o.color} roughness={0.5} />
    </mesh>
  );
}

function Pin({ marker, labels }: { marker: Marker; labels: RefObject<Map<string, HTMLSpanElement>> }) {
  const [x, y, z] = marker.position;
  const projected = useMemo(() => new Vector3(), []);
  useFrame(({ camera, size }) => {
    const label = labels.current.get(marker.id);
    if (!label) return;
    projected.set(x, z + 0.1, -y).project(camera);
    const left = (projected.x + 1) * size.width / 2;
    const top = (1 - projected.y) * size.height / 2;
    label.style.visibility = Math.abs(projected.z) <= 1 ? "visible" : "hidden";
    label.style.transform = `translate(${left}px, ${top}px) translate(-50%, -50%)`;
  });
  return (
    <group position={marker.position}>
      <mesh position={[0, 0, 0.03]} rotation={[Math.PI / 2, 0, 0]}><coneGeometry args={[0.02, 0.06, 12]} /><meshStandardMaterial color="#d7263d" /></mesh>
      <mesh position={[0, 0, 0.075]}><sphereGeometry args={[0.012, 12, 8]} /><meshStandardMaterial color="#f2b705" /></mesh>
    </group>
  );
}

function bounds(scene: SceneSpec, parsed: Parsed | undefined, initialBase: BasePose, first: Frame | undefined) {
  const xs: number[] = [], ys: number[] = [], zs: number[] = [0];
  function add([x, y, z]: Vec3, width = 0, height = width) {
    xs.push(x - width, x + width);
    ys.push(y - width, y + width);
    zs.push(z - height, z + height);
  }
  for (const box of scene.boxes) add(box.center, Math.max(box.size[0], box.size[1]) / 2, box.size[2] / 2);
  for (const marker of scene.markers) add(marker.position);
  add(scene.robot_base.position, 0.4);
  const floorX = (Math.min(...xs) + Math.max(...xs)) / 2;
  const floorY = (Math.min(...ys) + Math.max(...ys)) / 2;
  for (const object of scene.objects) add(object.center, Math.max(object.size[0], object.size[1]) / 2, object.size[2] / 2);

  const [x, y, z] = initialBase.position;
  const pose = first?.base ? { x: first.base[0], y: first.base[1], z, yaw: first.base[2] } : { x, y, z, yaw: initialBase.yaw };
  if (parsed) {
    const { centre, radius } = robotExtent(parsed, linkFrames(parsed, first?.joints ?? {}, pose));
    add([centre.x, centre.y, centre.z], radius);
  } else {
    add([pose.x, pose.y, pose.z], 0.4);
  }

  const [fx, fy] = scene.floor_size_m;
  const min = [Math.min(...xs), Math.min(...ys), Math.min(...zs)];
  const max = [Math.max(...xs), Math.max(...ys), Math.max(...zs)];
  const [cx, cy, cz] = min.map((value, i) => (value + max[i]) / 2);
  const radius = Math.max(0.8, ...max.map((value, i) => (value - min[i]) / 2));
  return { cx, cy, cz, fx, fy, floorX, floorY, radius };
}

export default function SceneView({ scene, parsed, frame, frames = [], index = 0, base, initialBase, height = 420 }: {
  scene: SceneSpec; parsed: Parsed | undefined; frame?: Frame; frames?: Frame[]; index?: number; base: Base; initialBase: BasePose; height?: number;
}) {
  const labels = useRef(new Map<string, HTMLSpanElement>());
  const first = frames[0];
  const b = useMemo(() => bounds(scene, parsed, initialBase, first), [scene, parsed, initialBase, first]);
  const centres = useMemo(() => objectCenters(scene.objects, frames, index), [scene.objects, frames, index]);
  const objectMap = Object.fromEntries(scene.objects.map((o) => [o.id, o]));
  const target: [number, number, number] = [b.cx, b.cz, -b.cy];
  const d = b.radius * 2.8;
  return (
    <div className="scene" style={{ height }} role="img" aria-label="3D view of the scene and the robot. Drag to orbit, scroll to zoom.">
      <Canvas shadows camera={{ position: [b.cx + d * 0.7, b.cz + d * 0.7, -b.cy + d * 0.9], fov: 45, near: 0.05, far: 200 }} gl={{ preserveDrawingBuffer: true }}>
        <ambientLight intensity={0.75} />
        <directionalLight position={[3, 5, 2]} intensity={1.2} castShadow />
        <Grid position={[b.floorX, 0.001, -b.floorY]} args={[b.fx, b.fy]} cellSize={0.1} sectionSize={0.5} cellColor="#b5c0ca" sectionColor="#8593a2" fadeDistance={b.radius * 14} />
        <group matrixAutoUpdate={false} matrix={Z_UP}>
          <mesh position={[b.floorX, b.floorY, -0.002]} receiveShadow><planeGeometry args={[b.fx, b.fy]} /><meshStandardMaterial color="#cfd6dd" /></mesh>
          {scene.boxes.map((box) => (
            <mesh key={box.id} position={box.center} rotation={[0, 0, box.yaw]} castShadow receiveShadow>
              <boxGeometry args={box.size} />
              <meshStandardMaterial color={box.color} transparent={!box.collides} opacity={box.collides ? 1 : 0.35} roughness={0.7} />
            </mesh>
          ))}
          {scene.markers.map((m) => <Pin key={m.id} marker={m} labels={labels} />)}
          {scene.objects.map((o) => <ObjectMesh key={o.id} o={objectMap[o.id]} centre={centres[o.id]} />)}
          {parsed && <RobotGeometry parsed={parsed} positions={frame?.joints} base={base} />}
        </group>
        <OrbitControls target={target} makeDefault />
      </Canvas>
      <div className="pin-labels" aria-hidden="true">
        {scene.markers.map(({ id, name }) => (
          <span key={id} className="pin-label" ref={(node) => {
            if (node) labels.current.set(id, node);
            else labels.current.delete(id);
          }}>{name || id}</span>
        ))}
      </div>
    </div>
  );
}
