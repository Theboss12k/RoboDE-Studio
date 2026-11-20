// The task scene in 3D: floor, boxes, marker pins, task objects and the robot, all in one canvas.
// World coordinates are z-up (as the server uses); one group converts them to three.js y-up.
import { useEffect, useMemo, useRef, type MutableRefObject, type RefObject } from "react";
import { BufferGeometry, Float32BufferAttribute } from "three";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Grid, OrbitControls } from "@react-three/drei";
import { Vector3 } from "three";
import { linkFrames, objectCenters, robotExtent, Z_UP } from "@robode/studio-core";
import { RobotGeometry } from "../RobotView";
import { ObjectModel, objectKind } from "./objectModels";
import { ModelParts } from "./objectParts";
import type { BasePose, Frame, Marker, Parsed, SceneObject, SceneSpec, Vec3 } from "../types";

type Base = { x: number; y: number; z: number; yaw: number };
/** One robot of a fleet: its parsed URDF, current joint positions and base pose. */
export type FleetRobot = { id: string; parsed: Parsed; positions?: Record<string, number>; base: Base };
/** The assignment preview drawn over the scene: base markers, handover points and reachable-workspace samples. */
export type FleetOverlay = {
  bases?: Record<string, { position: Vec3; yaw: number }>; handovers?: Record<string, Vec3>; samples?: Record<string, Vec3[]>;
};
const FALLBACK_COLORS = ["#b54423", "#286c52", "#1c65b6"];
/** One colour per member from the design tokens (accent, ok, focus), in member order. */
export function memberColors(): string[] {
  try {
    const css = getComputedStyle(document.documentElement);
    return ["--accent", "--ok", "--focus"].map((name, i) => css.getPropertyValue(name).trim() || FALLBACK_COLORS[i]);
  } catch { return FALLBACK_COLORS; }
}

function Cloud({ points, color }: { points: Vec3[]; color: string }) {
  const geometry = useMemo(() => {
    const g = new BufferGeometry();
    g.setAttribute("position", new Float32BufferAttribute(points.flat(), 3));
    return g;
  }, [points]);
  return <points geometry={geometry}><pointsMaterial color={color} size={0.02} sizeAttenuation transparent opacity={0.55} /></points>;
}

function overlayIds(overlay: FleetOverlay, robots?: FleetRobot[]) {
  return robots?.map((r) => r.id) ?? [...new Set([...Object.keys(overlay.bases ?? {}), ...Object.keys(overlay.samples ?? {})])];
}

function Overlay({ overlay, ids }: { overlay: FleetOverlay; ids: string[] }) {
  const colors = useMemo(memberColors, []);
  const colorOf = (id: string) => colors[Math.max(0, ids.indexOf(id)) % colors.length];
  return (
    <>
      {Object.entries(overlay.samples ?? {}).map(([id, pts]) => pts.length ? <Cloud key={`s${id}`} points={pts} color={colorOf(id)} /> : null)}
      {Object.entries(overlay.bases ?? {}).map(([id, b]) => (
        <group key={`b${id}`} position={[b.position[0], b.position[1], 0.01]} rotation={[0, 0, b.yaw]}>
          <mesh><cylinderGeometry args={[0.12, 0.12, 0.02, 24]} /><meshStandardMaterial color={colorOf(id)} transparent opacity={0.8} /></mesh>
          <mesh position={[0.16, 0, 0]} rotation={[0, 0, -Math.PI / 2]}><coneGeometry args={[0.05, 0.12, 12]} /><meshStandardMaterial color={colorOf(id)} /></mesh>
        </group>
      ))}
      {Object.entries(overlay.handovers ?? {}).map(([id, p]) => (
        <mesh key={`h${id}`} position={p}><octahedronGeometry args={[0.05]} /><meshStandardMaterial color="#f2b705" /></mesh>
      ))}
    </>
  );
}

function ObjectMesh({ o, centre }: { o: SceneObject; centre: Vec3 }) {
  // The agent's model (own frame: box centre, z up), else the keyword models, else the plain primitive.
  if (o.model) return <group position={centre}><ModelParts model={o.model} /></group>;
  const kind = objectKind(o.name, o.id, o.shape);
  if (kind !== "primitive") return <group position={centre}><ObjectModel o={o} name={o.name} kind={kind} /></group>;
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

/** Camera views the studio uses for snapshots; `capture` renders one frame and returns the canvas as a PNG. */
export type SceneViewName = "iso" | "front" | "side" | "top";
export type SceneViewHandle = { capture: (view: SceneViewName) => Promise<Blob> };

/** Inside the canvas: gives the parent a handle that moves the camera round `target` at distance `d` and grabs a PNG. */
function ViewProbe({ handle, target, d }: { handle: MutableRefObject<SceneViewHandle | null>; target: [number, number, number]; d: number }) {
  const { camera, gl, scene, controls } = useThree();
  useEffect(() => {
    handle.current = {
      capture: (view) => {
        const [tx, ty, tz] = target;
        const at: Record<SceneViewName, [number, number, number]> = {
          iso: [tx + d * 0.7, ty + d * 0.7, tz + d * 0.9], front: [tx, ty, tz + d], side: [tx + d, ty, tz], top: [tx + 1e-4, ty + d, tz],
        };
        camera.position.set(...at[view]);
        camera.up.set(0, 1, 0);
        camera.lookAt(tx, ty, tz);
        (controls as { update?: () => void } | null)?.update?.();
        gl.render(scene, camera);
        return new Promise<Blob>((done, fail) => gl.domElement.toBlob((blob) => (blob ? done(blob) : fail(new Error("The 3D view could not be captured"))), "image/png"));
      },
    };
    return () => { handle.current = null; };
  }, [handle, target[0], target[1], target[2], d, camera, gl, scene, controls]); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}

function bounds(scene: SceneSpec, parsed: Parsed | undefined, initialBase: BasePose, first: Frame | undefined, robots?: FleetRobot[], overlay?: FleetOverlay) {
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
  if (robots?.length) {
    for (const r of robots) {
      const { centre, radius } = robotExtent(r.parsed, linkFrames(r.parsed, r.positions ?? {}, r.base));
      add([centre.x, centre.y, centre.z], radius);
    }
  } else if (parsed) {
    const { centre, radius } = robotExtent(parsed, linkFrames(parsed, first?.joints ?? {}, pose));
    add([centre.x, centre.y, centre.z], radius);
  } else {
    add([pose.x, pose.y, pose.z], 0.4);
  }

  for (const b of Object.values(overlay?.bases ?? {})) add(b.position, 0.3);
  for (const p of Object.values(overlay?.handovers ?? {})) add(p, 0.1);
  for (const pts of Object.values(overlay?.samples ?? {})) for (const p of pts) add(p);

  const [fx, fy] = scene.floor_size_m;
  const min = [Math.min(...xs), Math.min(...ys), Math.min(...zs)];
  const max = [Math.max(...xs), Math.max(...ys), Math.max(...zs)];
  const [cx, cy, cz] = min.map((value, i) => (value + max[i]) / 2);
  const radius = Math.max(0.8, ...max.map((value, i) => (value - min[i]) / 2));
  return { cx, cy, cz, fx, fy, floorX, floorY, radius };
}

export default function SceneView({ scene, parsed, frame, frames = [], index = 0, base, initialBase, height = 420, robots, centres: given, overlay, meshBase, handle }: {
  scene: SceneSpec; parsed: Parsed | undefined; frame?: Frame; frames?: Frame[]; index?: number; base: Base; initialBase: BasePose; height?: number;
  /** A fleet: every robot is drawn (replaces `parsed`/`frame`/`base`), and `centres` carries the objects on the shared clock. */
  robots?: FleetRobot[]; centres?: Record<string, Vec3>; overlay?: FleetOverlay;
  /** Url folder that URDF mesh uris are relative to (Robot studio builds); without it meshes show as placeholders. */
  meshBase?: string;
  /** Receives a handle for snapshots from set camera views (Robot studio approval). */
  handle?: MutableRefObject<SceneViewHandle | null>;
}) {
  const labels = useRef(new Map<string, HTMLSpanElement>());
  const first = frames[0];
  const b = useMemo(() => bounds(scene, parsed, initialBase, first, robots?.map((r) => ({ ...r, positions: {} })), overlay), [scene, parsed, initialBase, first, robots?.map((r) => r.id).join(","), overlay]); // eslint-disable-line react-hooks/exhaustive-deps
  const own = useMemo(() => objectCenters(scene.objects, frames, index), [scene.objects, frames, index]);
  const centres = given ?? own;
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
          {robots ? robots.map((r) => <RobotGeometry key={r.id} parsed={r.parsed} positions={r.positions} base={r.base} meshBase={meshBase} />)
            : parsed && <RobotGeometry parsed={parsed} positions={frame?.joints} base={base} meshBase={meshBase} />}
          {overlay && <Overlay overlay={overlay} ids={overlayIds(overlay, robots)} />}
        </group>
        <OrbitControls target={target} makeDefault />
        {handle && <ViewProbe handle={handle} target={target} d={d} />}
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
