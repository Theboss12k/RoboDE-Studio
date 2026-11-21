// The task scene in 3D: floor, boxes, marker pins, task objects and the robot, all in one canvas.
// World coordinates are z-up (as the server uses); one group converts them to three.js y-up.
import { useEffect, useMemo, useRef, type MutableRefObject, type RefObject } from "react";
import { BufferGeometry, Float32BufferAttribute } from "three";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Grid, OrbitControls } from "@react-three/drei";
import { DirectionalLight, Matrix4, Object3D, Vector3, type Group } from "three";
import { cameraMatrixWorld, linkFrames, objectCenters, placementMatrix, projectionFromIntrinsics, robotExtent, scanCameraMatrixWorld, Z_UP } from "@robode/studio-core";
import { RobotGeometry } from "../RobotView";
import { ObjectModel, objectKind } from "./objectModels";
import { ModelParts } from "./objectParts";
import SiteLayer from "./scan/SiteLayer";
import type { SiteView } from "./scan/types";
import type { ArCalibration, ArPlacement, ArScene, BasePose, Frame, Marker, Parsed, SceneObject, SceneSpec, Vec3 } from "../types";

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

/** Photo AR (docs/AR_PHOTO.md): the scene is drawn over a photo with the camera taken from its calibration. */
export type ArView = { photoUrl: string; scene: ArScene; showBoxes: boolean; onPlace?: (p: ArPlacement) => void };

/** A camera with a given projection and pose (column-major). Set every frame so the canvas size can never overwrite it. */
function FixedCamera({ proj, world }: { proj: number[]; world: number[] }) {
  useFrame(({ camera }) => {
    camera.matrixAutoUpdate = false;
    camera.matrix.fromArray(world);
    camera.matrixWorld.fromArray(world);
    camera.matrixWorldInverse.copy(camera.matrixWorld).invert();
    camera.projectionMatrix.fromArray(proj);
    camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();
  });
  return null;
}

/** Camera from the photo's intrinsics and pose. */
function ArCamera({ cal }: { cal: ArCalibration }) {
  const proj = useMemo(() => {
    const i = cal.intrinsics;
    return projectionFromIntrinsics(i.fx, i.fy, i.cx, i.cy, i.width, i.height, 0.05, 200);
  }, [cal]);
  const world = useMemo(() => cameraMatrixWorld(cal.world_from_camera), [cal]);
  return <FixedCamera proj={proj} world={world} />;
}

/** Through the camera (My site): the scan camera's intrinsics at the frame resolution, posed by Z_UP * S_from_R * R_from_C * flip. */
function SiteCamera({ site }: { site: SiteView }) {
  const cam = site.camera!;
  const proj = useMemo(() => projectionFromIntrinsics(cam.k.fx, cam.k.fy, cam.k.cx, cam.k.cy, cam.k.width, cam.k.height, 0.02, 300), [cam.k]);
  const world = useMemo(() => scanCameraMatrixWorld(site.sFromR, cam.worldFromCamera), [site.sFromR, cam.worldFromCamera]);
  return <FixedCamera proj={proj} world={world} />;
}

/**
 * Lights of My site: a hemisphere light and one soft directional light that casts the robot's shadow. Its shadow camera covers
 * the design's extent (the sphere `centre`, `radius`, three.js world), so the shadow stays sharp.
 */
function SiteLights({ centre, radius }: { centre: Vector3; radius: number }) {
  const light = useRef<DirectionalLight>(null);
  const target = useMemo(() => new Object3D(), []);
  const r = radius * 1.25 + 0.4, d = r * 2.5;
  useEffect(() => {
    const l = light.current;
    if (!l) return;
    target.position.copy(centre);
    target.updateMatrixWorld();
    l.target = target;
    l.position.copy(centre).add(new Vector3(0.45, 0.8, 0.35).normalize().multiplyScalar(d));
    l.shadow.camera.updateProjectionMatrix();
  }, [centre, d, target]);
  return (
    <>
      <hemisphereLight args={["#f4f7fa", "#8a8f94", 0.95]} />
      <directionalLight ref={light} intensity={1.15} castShadow
        shadow-camera-left={-r} shadow-camera-right={r} shadow-camera-top={r} shadow-camera-bottom={-r}
        shadow-camera-near={0.1} shadow-camera-far={d + r * 3} shadow-mapSize={[2048, 2048]} shadow-bias={-0.0004} shadow-normalBias={0.015} shadow-radius={3} />
      <primitive object={target} />
    </>
  );
}
/** Shadow-only floor at z = 0 of the floor frame F; dragging on it moves the placement. */
function ArFloor({ placement, onPlace }: { placement: ArPlacement; onPlace?: (p: ArPlacement) => void }) {
  const grab = useRef<{ dx: number; dy: number } | null>(null);
  // three.js y-up point to F: (x, y, z) -> (x, -z, y)
  return (
    <group matrixAutoUpdate={false} matrix={Z_UP}>
      <mesh receiveShadow
        onPointerDown={(e) => {
          if (!onPlace) return;
          e.stopPropagation();
          try { (e.target as unknown as Element).setPointerCapture(e.pointerId); } catch { /* not capturable */ }
          grab.current = { dx: e.point.x - placement.x, dy: -e.point.z - placement.y };
        }}
        onPointerMove={(e) => {
          if (!grab.current || !onPlace) return;
          onPlace({ ...placement, x: e.point.x - grab.current.dx, y: -e.point.z - grab.current.dy });
        }}
        onPointerUp={(e) => {
          grab.current = null;
          try { (e.target as unknown as Element).releasePointerCapture(e.pointerId); } catch { /* not captured */ }
        }}>
        <planeGeometry args={[200, 200]} />
        <shadowMaterial opacity={0.3} />
      </mesh>
    </group>
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

function Pin({ marker, labels, world }: { marker: Marker; labels: RefObject<Map<string, HTMLSpanElement>>; world?: Matrix4 }) {
  const [x, y, z] = marker.position;
  const projected = useMemo(() => new Vector3(), []);
  useFrame(({ camera, size }) => {
    const label = labels.current.get(marker.id);
    if (!label) return;
    // World z-up to three.js; in AR the placement is part of `world`.
    if (world) projected.set(x, y, z + 0.1).applyMatrix4(world).project(camera);
    else projected.set(x, z + 0.1, -y).project(camera);
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

export default function SceneView({ scene, parsed, frame, frames = [], index = 0, base, initialBase, height = 420, robots, centres: given, overlay, meshBase, handle, ar, site }: {
  scene: SceneSpec; parsed: Parsed | undefined; frame?: Frame; frames?: Frame[]; index?: number; base: Base; initialBase: BasePose; height?: number; ar?: ArView; site?: SiteView;
  /** A fleet: every robot is drawn (replaces `parsed`/`frame`/`base`), and `centres` carries the objects on the shared clock. */
  robots?: FleetRobot[]; centres?: Record<string, Vec3>; overlay?: FleetOverlay;
  /** Url folder that URDF mesh uris are relative to (Robot studio builds); without it meshes show as placeholders. */
  meshBase?: string;
  /** Receives a handle for snapshots from set camera views (Robot studio approval). */
  handle?: MutableRefObject<SceneViewHandle | null>;
}) {
  const arCal = ar ? (ar.scene.calibration ?? ar.scene.estimate ?? null) : null;
  const useAr = !!(ar && arCal);
  const worldMatrix = useMemo(() => {
    // My site: the design world sits in the site through the placement, Z_UP * T_SW (docs/SITE_SCAN.md).
    if (site) return Z_UP.clone().multiply(new Matrix4().fromArray(placementMatrix(site.placement)));
    if (!useAr) return Z_UP;
    const p = ar!.scene.placement;
    // F -> three.js is Z_UP; the design world sits in F through the placement: Z_UP * T_FW.
    return Z_UP.clone().multiply(new Matrix4().fromArray(placementMatrix(p)));
  }, [useAr, ar?.scene.placement.x, ar?.scene.placement.y, ar?.scene.placement.yaw_deg, site?.placement.x, site?.placement.y, site?.placement.yaw_deg]); // eslint-disable-line react-hooks/exhaustive-deps
  const labels = useRef(new Map<string, HTMLSpanElement>());
  const first = frames[0];
  const b = useMemo(() => bounds(scene, parsed, initialBase, first, robots?.map((r) => ({ ...r, positions: {} })), overlay), [scene, parsed, initialBase, first, robots?.map((r) => r.id).join(","), overlay]); // eslint-disable-line react-hooks/exhaustive-deps
  const own = useMemo(() => objectCenters(scene.objects, frames, index), [scene.objects, frames, index]);
  const centres = given ?? own;
  const objectMap = Object.fromEntries(scene.objects.map((o) => [o.id, o]));
  const target: [number, number, number] = [b.cx, b.cz, -b.cy];
  const d = b.radius * 2.8;
  const robotRef = useRef<Group>(null);
  const cam = site?.mode === "camera" ? site.camera : null;
  // The design's extent as a sphere in the three.js world (for the shadow camera).
  const designCentre = useMemo(() => new Vector3(b.cx, b.cy, b.cz).applyMatrix4(worldMatrix), [b.cx, b.cy, b.cz, worldMatrix]);
  const frameStyle = cam ? {
    aspectRatio: `${cam.k.width} / ${cam.k.height}`, width: `min(100%, calc(78vh * ${cam.k.width / cam.k.height}))`,
    backgroundImage: `url("${cam.frameUrl}")`, backgroundSize: "100% 100%",
  } : null;
  return (
    <div className={`scene${site ? (cam ? " site-camera" : " site-3d") : ""}${site?.tool ? " site-tool" : ""}`} style={useAr
      ? { aspectRatio: `${ar!.scene.width} / ${ar!.scene.height}`, backgroundImage: `url("${ar!.photoUrl}")`, backgroundSize: "100% 100%" }
      : frameStyle ?? { height }} role="img"
      aria-label={cam ? "Your design drawn over a frame of the site video, seen through the scan camera."
        : site ? "3D view of your site scan with the design placed in it. Drag to orbit, scroll to zoom to the cursor, double-click a surface to orbit around it, pick a tool to click on surfaces."
        : useAr ? "The design drawn over your photo. Drag on the floor to move it." : "3D view of the scene and the robot. Drag to orbit, scroll to zoom."}>
      <Canvas key={cam ? "site-camera" : site ? "site-3d" : "plain"} shadows camera={{ position: [b.cx + d * 0.7, b.cz + d * 0.7, -b.cy + d * 0.9], fov: 45, near: 0.05, far: 200 }}
        gl={useAr || site ? { preserveDrawingBuffer: true, alpha: true } : { preserveDrawingBuffer: true }}>
        {useAr && <ArCamera cal={arCal!} />}
        {cam && <SiteCamera site={site!} />}
        {site ? <SiteLights centre={designCentre} radius={b.radius} /> : (
          <>
            <ambientLight intensity={0.75} />
            <directionalLight position={[3, 5, 2]} intensity={1.2} castShadow
              {...(useAr ? { "shadow-camera-left": -8, "shadow-camera-right": 8, "shadow-camera-top": 8, "shadow-camera-bottom": -8, "shadow-camera-far": 30, "shadow-mapSize": [2048, 2048] as [number, number] } : {})} />
          </>
        )}
        {useAr && <ArFloor placement={ar!.scene.placement} onPlace={ar!.onPlace} />}
        {site && <SiteLayer site={site} robotRef={robotRef} />}
        {!useAr && !site && <Grid position={[b.floorX, 0.001, -b.floorY]} args={[b.fx, b.fy]} cellSize={0.1} sectionSize={0.5} cellColor="#b5c0ca" sectionColor="#8593a2" fadeDistance={b.radius * 14} />}
        <group matrixAutoUpdate={false} matrix={worldMatrix}>
          {!useAr && !site && <mesh position={[b.floorX, b.floorY, -0.002]} receiveShadow><planeGeometry args={[b.fx, b.fy]} /><meshStandardMaterial color="#cfd6dd" /></mesh>}
          {(!useAr || ar!.showBoxes) && scene.boxes.map((box) => {
            // A keep-out zone is a collision box drawn see-through, so the scan stays visible behind it.
            const ghost = !box.collides || box.kind === "floor_zone";
            return (
              <mesh key={box.id} position={box.center} rotation={[0, 0, box.yaw]} castShadow receiveShadow onClick={site?.onPick}>
                <boxGeometry args={box.size} />
                <meshStandardMaterial color={box.color} transparent={ghost} opacity={ghost ? 0.35 : 1} roughness={0.7} />
              </mesh>
            );
          })}
          {scene.markers.map((m) => <Pin key={m.id} marker={m} labels={labels} world={useAr || site ? worldMatrix : undefined} />)}
          {scene.objects.map((o) => <ObjectMesh key={o.id} o={objectMap[o.id]} centre={centres[o.id]} />)}
          <group ref={robotRef}>
            {robots ? robots.map((r) => <RobotGeometry key={r.id} parsed={r.parsed} positions={r.positions} base={r.base} meshBase={meshBase} />)
              : parsed && <RobotGeometry parsed={parsed} positions={frame?.joints} base={base} meshBase={meshBase} />}
          </group>
          {overlay && <Overlay overlay={overlay} ids={overlayIds(overlay, robots)} />}
        </group>
        {!useAr && !site && <OrbitControls target={target} makeDefault />}
        {handle && <ViewProbe handle={handle} target={target} d={d} />}
      </Canvas>
      {useAr && !ar!.scene.calibration && <span className="ar-badge">Approximate scale</span>}
      {site?.unscaled && <span className="ar-badge">Unscaled — set a scale</span>}
      {site && !cam && (
        <div className="scene-nav">
          <button type="button" onClick={site.onFocusRobot}>Focus robot</button>
          <button type="button" onClick={site.onResetView}>Reset view</button>
        </div>
      )}
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