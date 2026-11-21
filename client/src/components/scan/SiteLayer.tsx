// The 3D part of "My site", drawn inside SceneView's canvas.
// 3D view: the scan mesh (frame R) under Z_UP * S_from_R, lit, with orbit controls that zoom to the cursor, a subtle fog and
// "Focus robot" / "Reset view" moves. Through the camera: the mesh is only an occluder (depth, no colour) and a shadow catcher over
// the keyframe photo. The dots and measuring line of the tools are in S, so under Z_UP.
// The design world itself (robot, objects, markers) is drawn by SceneView under Z_UP * T_SW.
import { Component, Suspense, useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import { useFrame, useThree } from "@react-three/fiber";
import { Html, Line, OrbitControls, useGLTF } from "@react-three/drei";
import { Box3, DoubleSide, Matrix4, Mesh, MeshBasicMaterial, MeshStandardMaterial, Raycaster, ShadowMaterial, Vector2, Vector3, type BufferGeometry, type Group, type Material, type Object3D } from "three";
import { applyPoint, frameSphere, Z_UP } from "@robode/studio-core";
import type { P3, SiteView } from "./types";

class MeshBoundary extends Component<{ onError: (message: string) => void; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error) { this.props.onError(error.message || "The mesh could not be loaded"); }
  render() { return this.state.failed ? null : this.props.children; }
}

type TexturedMaterial = { map?: MeshBasicMaterial["map"] };
const meshesOf = (root: Object3D) => { const out: Mesh[] = []; root.traverse((o) => { if ((o as Mesh).isMesh) out.push(o as Mesh); }); return out; };
const oldMap = (m: Mesh) => ((Array.isArray(m.material) ? m.material[0] : m.material) as unknown as TexturedMaterial | undefined)?.map ?? null;

/** A clone of the GLB scene with its own materials; `make` builds one per mesh. The geometry is shared (and given normals when the GLB has none). */
function dressed(scene: Object3D, make: (m: Mesh, geometry: BufferGeometry) => Material, tweak: (m: Mesh) => void) {
  const copy = scene.clone(true);
  for (const m of meshesOf(copy)) {
    // Old scans carry no vertex normals: compute them, and light the mesh all the same.
    if (!m.geometry.getAttribute("normal")) m.geometry.computeVertexNormals();
    m.material = make(m, m.geometry);
    tweak(m);
  }
  return copy;
}

function ScanMesh({ site, group, onBox, onDouble }: { site: SiteView; group: RefObject<Group | null>; onBox: (box: Box3) => void; onDouble: (e: { point: Vector3; stopPropagation: () => void }) => void }) {
  const { scene } = useGLTF(site.meshUrl);
  const camera = site.mode === "camera";
  // 3D view: vertex colours, lit (roughness about 0.9), seen from both sides, receiving the robot's shadow.
  const lit = useMemo(() => camera ? null : dressed(scene,
    (m, g) => new MeshStandardMaterial({ vertexColors: !!g.getAttribute("color"), roughness: 0.9, metalness: 0, side: DoubleSide, map: oldMap(m) }),
    (m) => { m.receiveShadow = true; m.castShadow = false; }), [scene, camera]);
  // Through the camera: real walls and tables hide the robot (depth only, drawn first) ...
  const occluder = useMemo(() => !camera ? null : dressed(scene,
    () => new MeshBasicMaterial({ colorWrite: false, side: DoubleSide }),
    (m) => { m.renderOrder = -10; m.castShadow = false; m.receiveShadow = false; }), [scene, camera]);
  // ... and a shadow-only copy of the same mesh catches the shadow of the robot on the real floor and tables.
  const catcher = useMemo(() => !camera ? null : dressed(scene,
    () => new ShadowMaterial({ opacity: 0.38, side: DoubleSide, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2 }),
    (m) => { m.renderOrder = -9; m.receiveShadow = true; m.castShadow = false; }), [scene, camera]);
  useEffect(() => () => {
    for (const root of [lit, occluder, catcher]) if (root) for (const m of meshesOf(root)) (m.material as Material).dispose();
  }, [lit, occluder, catcher]);
  const matrix = useMemo(() => Z_UP.clone().multiply(new Matrix4().fromArray(site.sFromR)), [site.sFromR]);
  const { onReady } = site;
  useEffect(() => {
    const g = group.current;
    if (!g) return;
    g.matrix.copy(matrix);
    g.updateMatrixWorld(true);
    onBox(new Box3().setFromObject(g));
  }, [matrix, lit, occluder]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { onReady(); }, [scene]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <group ref={group} matrixAutoUpdate={false} matrix={matrix} onClick={site.onPick} onDoubleClick={onDouble}>
      {lit && <primitive object={lit} />}
      {occluder && <primitive object={occluder} />}
      {catcher && <primitive object={catcher} />}
    </group>
  );
}

/** A CSS colour variable of the page, following the light and dark theme. */
function useCssColor(name: string, fallback: string) {
  const read = () => (getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback);
  const [value, setValue] = useState(read);
  useEffect(() => {
    const update = () => setValue(read());
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    query.addEventListener("change", update);
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "class"] });
    update();
    return () => { query.removeEventListener("change", update); observer.disconnect(); };
  }, [name]); // eslint-disable-line react-hooks/exhaustive-deps
  return value;
}

const toThree = (s: ArrayLike<number>) => new Vector3(s[0], s[2], -s[1]);
type Orbit = { target: Vector3; update: () => void; minDistance: number; maxDistance: number };
type Fly = { fromP: Vector3; fromT: Vector3; toP: Vector3; toT: Vector3; t0: number; ms: number };

export default function SiteLayer({ site, robotRef }: { site: SiteView; robotRef: RefObject<Group | null> }) {
  const group = useRef<Group>(null);
  const [box, setBox] = useState<Box3 | null>(null);
  const { camera, gl, controls, size } = useThree();
  const orbitMode = site.mode === "orbit";
  const framed = useRef("");
  const live = useRef(site);
  live.current = site;
  const orbit = () => controls as unknown as Orbit | null;
  const fly = useRef<Fly | null>(null);
  const bg = useCssColor("--scene-bg", "#e9edf0");

  const diagonal = box && !box.isEmpty() ? box.getSize(new Vector3()).length() || 1 : 10;
  // Scale-aware limits: S is in metres once scaled (0.05 m at the closest); unscaled it is in scan units of unknown size.
  const minDistance = site.unscaled ? Math.max(diagonal * 0.01, 1e-4) : 0.05;
  const maxDistance = Math.max(diagonal * 3, 5);

  function flyTo(position: Vector3, target: Vector3, ms = 850) {
    const o = orbit();
    if (!o) return;
    fly.current = { fromP: camera.position.clone(), fromT: o.target.clone(), toP: position, toT: target, t0: performance.now(), ms };
  }
  useFrame(() => {
    const f = fly.current, o = orbit();
    if (!f || !o) return;
    const k = Math.min(1, (performance.now() - f.t0) / f.ms);
    const e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
    camera.position.lerpVectors(f.fromP, f.toP, e);
    o.target.lerpVectors(f.fromT, f.toT, e);
    o.update();
    if (k >= 1) fly.current = null;
  });
  // The user taking over stops a move in progress.
  useEffect(() => {
    const stop = () => { fly.current = null; };
    gl.domElement.addEventListener("pointerdown", stop);
    gl.domElement.addEventListener("wheel", stop, { passive: true });
    return () => { gl.domElement.removeEventListener("pointerdown", stop); gl.domElement.removeEventListener("wheel", stop); };
  }, [gl]);

  /** Where "Focus robot" goes: the base and its reach sphere fill the view, seen from the room's side, a little from above. */
  function robotView(): { position: Vector3; target: Vector3 } {
    const { centre, radius, base } = live.current.robot;
    const room = box && !box.isEmpty() ? box.getCenter(new Vector3()) : new Vector3();
    // horizontal direction from the robot toward the middle of the scan (S: x = three x, y = -three z)
    let dx = room.x - centre[0], dy = -room.z - centre[1];
    if (Math.hypot(dx, dy) < 0.3) { dx = 0.6; dy = -0.8; }
    const n = Math.hypot(dx, dy);
    const lift = (22 * Math.PI) / 180;
    const direction: P3 = [(dx / n) * Math.cos(lift), (dy / n) * Math.cos(lift), Math.sin(lift)];
    const r = Math.max(radius, Math.hypot(centre[0] - base[0], centre[1] - base[1], centre[2] - base[2]) + 0.1, 0.15);
    const fov = "fov" in camera ? (camera as unknown as { fov: number }).fov : 45;
    const { eye } = frameSphere(centre, r, fov, size.width / Math.max(size.height, 1), direction, 0.8);
    return { position: toThree(eye), target: toThree(centre) };
  }
  function frameScan() {
    if (!box || box.isEmpty()) return;
    const c = box.getCenter(new Vector3());
    const d = box.getSize(new Vector3()).length() || 1;
    camera.position.set(c.x + d * 0.55, c.y + d * 0.7, c.z + d * 0.55);
    const o = orbit();
    o?.target.copy(c);
    o?.update();
  }
  const focusRobot = (ms?: number) => { const v = robotView(); flyTo(v.position, v.target, ms); };

  // Near and far follow the scan, so that the closest zoom (5 cm) is not clipped.
  useEffect(() => {
    if (!orbitMode || !("far" in camera)) return;
    const c = camera as unknown as { near: number; far: number; updateProjectionMatrix: () => void };
    c.near = Math.max(minDistance * 0.4, 1e-4);
    c.far = Math.max(200, diagonal * 20);
    c.updateProjectionMatrix();
  }, [camera, orbitMode, minDistance, diagonal]);

  // First load of a mesh: frame the scan, then move to the robot. Coming back from "Through the camera" restores the old view.
  useEffect(() => {
    if (!orbitMode || !box || framed.current === site.meshUrl || box.isEmpty() || !orbit()) return;
    framed.current = site.meshUrl;
    const memory = site.memory.current;
    if (memory && memory.url === site.meshUrl) {
      camera.position.set(...memory.position);
      orbit()!.target.set(...memory.target);
      orbit()!.update();
      return;
    }
    frameScan();
    focusRobot();
  }, [box, site.meshUrl, camera, controls, orbitMode]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => {
    const o = orbit();
    if (live.current.mode === "orbit" && o && framed.current) {
      live.current.memory.current = { url: framed.current, position: [camera.position.x, camera.position.y, camera.position.z], target: [o.target.x, o.target.y, o.target.z] };
    }
  }, [controls]); // eslint-disable-line react-hooks/exhaustive-deps

  // The buttons.
  const seen = useRef({ focus: site.nav.focus, reset: site.nav.reset });
  useEffect(() => {
    if (!orbitMode) return;
    if (seen.current.reset !== site.nav.reset) { seen.current.reset = site.nav.reset; frameScan(); focusRobot(1000); }
    else if (seen.current.focus !== site.nav.focus) { seen.current.focus = site.nav.focus; focusRobot(); }
  }, [site.nav.focus, site.nav.reset]); // eslint-disable-line react-hooks/exhaustive-deps

  const radius = box ? Math.max(box.getSize(new Vector3()).length() * 0.006, 1e-3) : 0.04;

  // Test hook (only with ?e2e=1 in the page address): lets an automated test aim the camera and read where things are on screen.
  useEffect(() => {
    if (!site.e2e) return;
    const ray = new Raycaster();
    const debug = {
      ready: () => !!group.current,
      mode: () => live.current.mode,
      /** True while a "Focus robot" or "Reset view" move is under way. */
      moving: () => !!fly.current,
      /** Mesh vertices in S (metres once scaled), every `step`-th one. */
      vertices(step = 1): P3[] {
        const out: P3[] = [];
        let n = 0;
        group.current?.traverse((o) => {
          const m = o as Mesh;
          if (!m.isMesh) return;
          const a = m.geometry.getAttribute("position");
          for (let i = 0; i < a.count; i++) if (n++ % step === 0) out.push(applyPoint(live.current.sFromR, [a.getX(i), a.getY(i), a.getZ(i)]));
        });
        return out;
      },
      /** Puts the camera at `eye`, looking at `target` (both in S). */
      view(eye: P3, look: P3) {
        fly.current = null;
        camera.position.copy(toThree(eye));
        const o = orbit();
        o?.target.copy(toThree(look));
        o?.update();
        camera.updateMatrixWorld(true);
      },
      /** Page (client) coordinates of a point in S, or null when it is behind the camera. */
      screen(s: P3): { x: number; y: number } | null {
        camera.updateMatrixWorld(true);
        const v = toThree(s).project(camera);
        if (Math.abs(v.z) > 1) return null;
        const r = gl.domElement.getBoundingClientRect();
        return { x: r.left + ((v.x + 1) / 2) * r.width, y: r.top + ((1 - v.y) / 2) * r.height };
      },
      /** Through the camera: the pixel of the frame image (at the camera's resolution) where the page camera draws a point of S. */
      pixel(s: P3): { u: number; v: number } | null {
        const cam = live.current.camera;
        if (!cam) return null;
        camera.updateMatrixWorld(true);
        const v = toThree(s).project(camera);
        return { u: ((v.x + 1) / 2) * cam.k.width, v: ((1 - v.y) / 2) * cam.k.height };
      },
      /** Through the camera: the point of the floor (z = 0 in S) that the page camera sees at a pixel of the frame image. */
      floorPoint(u: number, v: number): P3 | null {
        const cam = live.current.camera;
        if (!cam) return null;
        camera.updateMatrixWorld(true);
        const o = new Vector3().setFromMatrixPosition(camera.matrixWorld);
        const dir = new Vector3((u / cam.k.width) * 2 - 1, 1 - (v / cam.k.height) * 2, 0.5).unproject(camera).sub(o);
        if (Math.abs(dir.y) < 1e-9) return null;
        const t = -o.y / dir.y;
        return [o.x + dir.x * t, -(o.z + dir.z * t), 0];
      },
      /** The canvas size on the page, and the frame shown (index in the scan's frame list). */
      canvas() {
        const r = gl.domElement.getBoundingClientRect();
        return { width: r.width, height: r.height, frame: live.current.camera?.index ?? null };
      },
      /** The robot's box on screen, in canvas pixels: its height as a fraction of the canvas height. */
      robotScreen(): { height: number; width: number; canvasHeight: number; fraction: number; cx: number; cy: number; canvasWidth: number } | null {
        const g = robotRef.current;
        if (!g) return null;
        camera.updateMatrixWorld(true);
        g.updateMatrixWorld(true);
        const b = new Box3().setFromObject(g);
        if (b.isEmpty()) return null;
        const r = gl.domElement.getBoundingClientRect();
        let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
        for (const x of [b.min.x, b.max.x]) for (const y of [b.min.y, b.max.y]) for (const z of [b.min.z, b.max.z]) {
          const v = new Vector3(x, y, z).project(camera);
          const px = ((v.x + 1) / 2) * r.width, py = ((1 - v.y) / 2) * r.height;
          x0 = Math.min(x0, px); x1 = Math.max(x1, px); y0 = Math.min(y0, py); y1 = Math.max(y1, py);
        }
        return { height: y1 - y0, width: x1 - x0, canvasHeight: r.height, canvasWidth: r.width, fraction: (y1 - y0) / r.height, cx: (x0 + x1) / 2, cy: (y0 + y1) / 2 };
      },
      /** The point of the mesh in S under page coordinates, as a click would pick it. */
      hit(x: number, y: number): P3 | null {
        const r = gl.domElement.getBoundingClientRect();
        camera.updateMatrixWorld(true);
        ray.setFromCamera(new Vector2(((x - r.left) / r.width) * 2 - 1, -(((y - r.top) / r.height) * 2 - 1)), camera);
        const h = group.current ? ray.intersectObject(group.current, true)[0] : undefined;
        return h ? [h.point.x, -h.point.z, h.point.y] : null;
      },
    };
    (window as unknown as { __site?: typeof debug }).__site = debug;
    return () => { const w = window as unknown as { __site?: typeof debug }; if (w.__site === debug) delete w.__site; };
  }, [site.e2e, camera, gl, controls, robotRef]);

  // Double-click on a surface: the orbit goes around that point (the camera stays where it is and turns toward it).
  const onDouble = (e: { point: Vector3; stopPropagation: () => void }) => {
    if (!orbitMode || live.current.tool) return;
    e.stopPropagation();
    flyTo(camera.position.clone(), e.point.clone(), 450);
  };

  return (
    <>
      <MeshBoundary key={`${site.meshUrl}-${site.mode}`} onError={site.onError}>
        <Suspense fallback={null}>
          <ScanMesh site={site} group={group} onBox={setBox} onDouble={onDouble} />
        </Suspense>
      </MeshBoundary>
      {orbitMode && (
        <>
          <OrbitControls makeDefault enableDamping dampingFactor={0.09} zoomToCursor zoomSpeed={1.1} minDistance={minDistance} maxDistance={maxDistance} />
          <fog attach="fog" args={[bg, diagonal * 1.1, diagonal * 4]} />
        </>
      )}
      <group matrixAutoUpdate={false} matrix={Z_UP}>
        {site.marks.map((m, i) => (
          <mesh key={i} position={m.s} renderOrder={10}>
            <sphereGeometry args={[radius, 16, 12]} />
            <meshBasicMaterial color={m.color} depthTest={false} transparent />
          </mesh>
        ))}
        {site.line && (
          <>
            <Line points={[site.line.a, site.line.b]} color="#f2b705" lineWidth={3} depthTest={false} renderOrder={9} />
            <Html position={[(site.line.a[0] + site.line.b[0]) / 2, (site.line.a[1] + site.line.b[1]) / 2, (site.line.a[2] + site.line.b[2]) / 2]}
              center style={{ pointerEvents: "none" }}>
              <span className="site-label">{site.line.label}</span>
            </Html>
          </>
        )}
      </group>
    </>
  );
}
