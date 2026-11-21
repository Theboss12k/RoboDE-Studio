// "My site" (docs/SITE_SCAN.md): the player over the reconstructed mesh of a real site, and the tools that edit the design in it.
// Every number shown or sent is computed in code (packages/core scan.ts); the mesh is only for looking and clicking.
import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import type { ThreeEvent } from "@react-three/fiber";
import { bestRobotFrame, distance, linkFrames, placementForBase, robotExtent, rToS, sFromR, sToR, sToW, wToS } from "@robode/studio-core";
import { api } from "../../api";
import { useProjectJob } from "../../jobs";
import { JobStatus } from "../common";
import { AddObject, Delete, Measure, PickFloor, PlaceRobot, SetScale, TaskPoint, type LucideIcon } from "../icons";
import Player, { type PlayerHandle } from "../Player";
import ArCalibrator from "../ar/ArCalibrator";
import ScanList from "./ScanList";
import { NO_EDITS, type P3, type SiteEdits, type SiteMark, type SiteMode, type SiteView, type ToolId } from "./types";
import type { ArPlacement, DesignVersion, SceneBox, SceneSpec, ScanIntrinsics, SiteScan } from "../../types";

type Kind = "table" | "station" | "box" | "keep-out";
const KINDS: Record<Kind, { label: string; box: SceneBox["kind"]; size: P3; color: string }> = {
  table: { label: "Table", box: "table", size: [1.2, 0.8, 0.75], color: "#8d6e63" },
  station: { label: "Station", box: "obstacle", size: [0.8, 0.8, 1.0], color: "#607d8b" },
  box: { label: "Box", box: "box", size: [0.4, 0.4, 0.4], color: "#c9a227" },
  "keep-out": { label: "Keep-out zone", box: "floor_zone", size: [1.0, 1.0, 2.0], color: "#d7263d" },
};
const TOOLS: { id: ToolId; label: string; icon: LucideIcon }[] = [
  { id: "robot", label: "Place robot", icon: PlaceRobot },
  { id: "object", label: "Add object", icon: AddObject },
  { id: "marker", label: "Task point", icon: TaskPoint },
  { id: "measure", label: "Measure", icon: Measure },
  { id: "scale", label: "Set scale", icon: SetScale },
  { id: "floor", label: "Pick floor", icon: PickFloor },
];
const round = (n: number, digits = 3) => Number(n.toFixed(digits));
const flat = (z: number) => (Math.abs(z) < 0.02 ? 0 : round(z));
const E2E = typeof window !== "undefined" && new URLSearchParams(window.location.search).get("e2e") === "1";

export default function SiteWorkspace({ pid, version, frozen, playerRef, edits, setEdits, onApplied, height = 520 }: {
  pid: string;
  version: DesignVersion;
  /** The project is approved: the server refuses new versions. */
  frozen: boolean;
  playerRef: RefObject<PlayerHandle | null>;
  edits: SiteEdits;
  setEdits: (change: (e: SiteEdits) => SiteEdits) => void;
  onApplied: (version: number) => Promise<void>;
  height?: number;
}) {
  const [scans, setScans] = useState<SiteScan[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [tool, setTool] = useState<ToolId | null>(null);
  // The scan whose mesh has been drawn (or failed to load): comparing with the selected scan needs no reset.
  const [readyFor, setReadyFor] = useState<string | null>(null);
  const [failed, setFailed] = useState<{ id: string; message: string } | null>(null);

  const [kind, setKind] = useState<Kind>("table");
  const [size, setSize] = useState<P3>(KINDS.table.size);
  const [markerId, setMarkerId] = useState("");
  const [measurePts, setMeasurePts] = useState<P3[]>([]);
  const [scaleMode, setScaleMode] = useState<"sheet" | "distance">("sheet");
  const [scalePts, setScalePts] = useState<P3[]>([]);
  const [scaleM, setScaleM] = useState("");
  const [frameIdx, setFrameIdx] = useState<number | null>(null);
  const [floorPts, setFloorPts] = useState<P3[]>([]);
  const [busy, setBusy] = useState(false);
  // "3D view" or "Through the camera"; the walk is the registered keyframe shown in the latter (an index into the registered frames).
  const [mode, setMode] = useState<SiteMode>("orbit");
  const [walkPos, setWalkPos] = useState(0);
  const [walking, setWalking] = useState(false);
  const [nav, setNav] = useState({ focus: 0, reset: 0 });
  const memory = useRef<{ url: string; position: P3; target: P3 } | null>(null);

  const refresh = useCallback(async () => {
    try {
      const list = await api.scans(pid);
      setScans((old) => list.map((s) => {
        const mine = old.find((o) => o.id === s.id);
        // A placement still waiting to be saved wins over the server's older one.
        return mine && pending.current.has(s.id) ? { ...s, placement: mine.placement } : s;
      }));
    } catch (e) { setError((e as Error).message); }
  }, [pid]);
  const pending = useRef(new Set<string>());
  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (sel && scans.some((s) => s.id === sel)) return;
    setSel((scans.find((s) => s.status === "reconstructed") ?? scans[0])?.id ?? null);
  }, [scans, sel]);

  const scan = scans.find((s) => s.id === sel) ?? null;
  const ready = !!scan && scan.status === "reconstructed" && !!scan.mesh && !!scan.alignment;
  const factor = scan?.scale?.factor ?? null;
  const sMat = useMemo(() => sFromR(scan?.alignment, factor), [scan?.alignment, factor]);
  const placement: ArPlacement = scan?.placement ?? { x: 0, y: 0, yaw_deg: 0 };
  const scanId = scan?.id;
  const meshReady = !!scanId && readyFor === scanId;
  const meshError = failed && failed.id === scanId ? failed.message : "";
  useEffect(() => { setMeasurePts([]); setScalePts([]); setFloorPts([]); setFrameIdx(null); setWalkPos(0); setWalking(false); memory.current = null; }, [scanId]);

  // The placement shows at once and is saved a moment after the last change (as photo AR does).
  const saved = useRef(new Map<string, string>());
  useEffect(() => {
    if (!scanId) return;
    const key = `${placement.x}|${placement.y}|${placement.yaw_deg}`;
    if (!saved.current.has(scanId)) { saved.current.set(scanId, key); return; }
    if (saved.current.get(scanId) === key) { pending.current.delete(scanId); return; }
    pending.current.add(scanId);
    const timer = window.setTimeout(() => {
      saved.current.set(scanId, key);
      api.setScanPlacement(pid, scanId, { x: placement.x, y: placement.y, yaw_deg: placement.yaw_deg })
        .then(() => { if (saved.current.get(scanId) === key) pending.current.delete(scanId); })
        .catch((e) => setError((e as Error).message));
    }, 400);
    return () => window.clearTimeout(timer);
  }, [pid, scanId, placement.x, placement.y, placement.yaw_deg]); // eslint-disable-line react-hooks/exhaustive-deps
  const setPlacement = (p: ArPlacement) => { if (scanId) setScans((all) => all.map((s) => (s.id === scanId ? { ...s, placement: p } : s))); };
  const replace = (updated: SiteScan) => setScans((all) => all.map((s) => (s.id === updated.id ? { ...updated, placement: pending.current.has(updated.id) ? s.placement : updated.placement } : s)));

  // The design as edited so far (W): the base, the boxes, the task points and the objects that start on them.
  const base = version.robot.base ?? version.scene.robot_base;
  const baseW: P3 = [base.position[0], base.position[1], edits.baseZ ?? base.position[2]];
  const names = useMemo(() => Object.fromEntries(version.scene.markers.map((m) => [m.id, m.name || m.id])), [version]);
  const markersEditable = version.spec.markers.filter((m) => m.id !== "robot_base");
  useEffect(() => { if (!markerId || !markersEditable.some((m) => m.id === markerId)) setMarkerId(markersEditable[0]?.id ?? ""); }, [version]); // eslint-disable-line react-hooks/exhaustive-deps
  const editedScene = useMemo<SceneSpec>(() => {
    const markers = version.scene.markers.map((m) => (edits.markers[m.id] ? { ...m, position: edits.markers[m.id] } : m));
    const at = Object.fromEntries(markers.map((m) => [m.id, m.position]));
    const start = Object.fromEntries(version.spec.objects.filter((o) => o.start_marker).map((o) => [o.id, o.start_marker as string]));
    const objects = version.scene.objects.map((o) => {
      const p = start[o.id] && edits.markers[start[o.id]] ? at[start[o.id]] : null;
      return p ? { ...o, center: [p[0], p[1], p[2] + o.size[2] / 2] as P3 } : o;
    });
    return { ...version.scene, boxes: [...version.scene.boxes.filter((b) => !edits.removed.includes(b.id)), ...edits.boxes], markers, objects, robot_base: { ...version.scene.robot_base, position: baseW } };
  }, [version, edits, baseW[2]]); // eslint-disable-line react-hooks/exhaustive-deps
  const robotBase = useMemo(() => ({ ...base, position: baseW }), [base, baseW[2]]); // eslint-disable-line react-hooks/exhaustive-deps

  // -- clicks on the scan or on the boxes: S point, then the tool's own step ---------------------------------------------
  function onSurface(s: P3) {
    const w = sToW(s, placement);
    if (tool === "robot") {
      const p = placementForBase(baseW, placement.yaw_deg, s);
      setPlacement({ x: round(p.x, 4), y: round(p.y, 4), yaw_deg: placement.yaw_deg });
      setEdits((e) => ({ ...e, baseZ: flat(s[2]), baseEdited: true }));
    } else if (tool === "object") {
      const z = flat(s[2]);
      const used = new Set([...version.scene.boxes, ...edits.boxes].map((b) => b.id));
      let n = edits.boxes.length + 1;
      const slug = kind.replace("-", "");
      while (used.has(`site_${slug}_${n}`)) n++;
      const box: SceneBox = { id: `site_${slug}_${n}`, kind: KINDS[kind].box, center: [round(w[0], 4), round(w[1], 4), round(z + size[2] / 2, 4)], size: [...size], yaw: 0, color: KINDS[kind].color, collides: true };
      setEdits((e) => ({ ...e, boxes: [...e.boxes, box] }));
    } else if (tool === "marker") {
      if (markerId) setEdits((e) => ({ ...e, markers: { ...e.markers, [markerId]: [round(w[0], 4), round(w[1], 4), flat(w[2])] } }));
    } else if (tool === "measure") {
      const r = sToR(s, sMat);
      setMeasurePts((p) => (p.length >= 2 ? [r] : [...p, r]));
    } else if (tool === "scale" && scaleMode === "distance") {
      const r = sToR(s, sMat);
      setScalePts((p) => (p.length >= 2 ? [r] : [...p, r]));
    } else if (tool === "floor") {
      const next = [...floorPts, sToR(s, sMat)];
      if (next.length < 3) { setFloorPts(next); return; }
      setFloorPts([]);
      void run(async () => { replace(await api.setScanFloor(pid, scan!.id, next)); setNotice("Floor updated."); });
    }
  }
  const picking = tool === "robot" || tool === "object" || tool === "marker" || tool === "measure" || tool === "floor" || (tool === "scale" && scaleMode === "distance");
  const pickRef = useRef(onSurface);
  pickRef.current = onSurface;
  const onPick = (e: ThreeEvent<MouseEvent>) => {
    if (!picking || !ready || e.delta > 4) return;
    e.stopPropagation();
    pickRef.current([e.point.x, -e.point.z, e.point.y]);
  };

  async function run(step: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await step(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  function cancel() { setTool(null); setMeasurePts([]); setScalePts([]); setFloorPts([]); }
  const cancelRef = useRef(cancel);
  cancelRef.current = cancel;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") cancelRef.current(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  function pickTool(id: ToolId) { setTool(tool === id ? null : id); setMeasurePts([]); setScalePts([]); setFloorPts([]); setNotice(""); }

  // -- what the 3D layer draws on top of the mesh --------------------------------------------------------------------------
  const shownPts: { pts: P3[]; color: string } = tool === "measure" ? { pts: measurePts, color: "#f2b705" }
    : tool === "scale" && scaleMode === "distance" ? { pts: scalePts, color: "#f2b705" } : tool === "floor" ? { pts: floorPts, color: "#2a9d8f" } : { pts: [], color: "" };
  const marks: SiteMark[] = shownPts.pts.map((p) => ({ s: rToS(p, sMat), color: shownPts.color }));
  const measured = measurePts.length === 2 ? distance(measurePts[0], measurePts[1], factor) : null;
  const measureText = measured == null ? "" : factor ? `${measured.toFixed(2)} m` : `${measured.toFixed(2)} units (unscaled)`;
  const linePts = tool === "measure" && measurePts.length === 2 ? measurePts : null;
  // The robot in S for "Focus robot" and "Best view": its base and the sphere that holds its links at the first moment of the task.
  const registered = useMemo(() => scan?.frames.filter((f) => f.world_from_camera) ?? [], [scan?.frames]);
  const first = version.sim.frames?.[0];
  const robot = useMemo(() => {
    const pose = first?.base ? { x: first.base[0], y: first.base[1], z: baseW[2], yaw: first.base[2] } : { x: baseW[0], y: baseW[1], z: baseW[2], yaw: base.yaw };
    let centre: P3 = [pose.x, pose.y, pose.z + 0.4], radius = 0.6;
    if (version.parsed) {
      const e = robotExtent(version.parsed, linkFrames(version.parsed, first?.joints ?? {}, pose));
      centre = [e.centre.x, e.centre.y, e.centre.z]; radius = e.radius;
    }
    return { base: wToS(baseW, placement), centre: wToS(centre, placement), radius };
  }, [version.parsed, first, baseW[0], baseW[1], baseW[2], base.yaw, placement.x, placement.y, placement.yaw_deg]); // eslint-disable-line react-hooks/exhaustive-deps
  const cam = scan?.camera;
  const k: ScanIntrinsics | null = cam ? { fx: cam.fx, fy: cam.fy, cx: cam.cx, cy: cam.cy, width: cam.width, height: cam.height } : null;
  const undistorted = (cam as unknown as { undistorted?: boolean } | null | undefined)?.undistorted === true;
  const walkAt = registered.length ? Math.min(Math.max(walkPos, 0), registered.length - 1) : 0;
  const walkFrame = registered[walkAt];
  const cameraView = mode === "camera" && k && walkFrame && scan ? { index: walkFrame.index, frameUrl: api.scanFrameUrl(pid, scan.id, walkFrame.index), k, worldFromCamera: walkFrame.world_from_camera! } : null;
  const bestView = () => {
    if (!k) return;
    const i = bestRobotFrame(registered, sMat, robot.base, k, factor != null);
    if (i == null) { setNotice("No frame of the walk sees the robot base. Place the robot where the video looked."); return; }
    setNotice(""); setWalking(false); setWalkPos(i);
  };
  const chooseMode = (next: SiteMode) => {
    setMode(next); setWalking(false);
    if (next === "camera" && k) { const i = bestRobotFrame(registered, sMat, robot.base, k, factor != null); if (i != null) setWalkPos(i); }
  };
  // "Play walk": the keyframes step at about 6 per second, like a video, while the robot task plays on its own clock.
  useEffect(() => {
    if (!walking || !registered.length) return;
    const timer = window.setInterval(() => setWalkPos((p) => (p + 1) % registered.length), 1000 / 6);
    return () => window.clearInterval(timer);
  }, [walking, registered.length]);
  useEffect(() => {
    // The next frames are fetched ahead, so that the walk does not stutter.
    if (!walking || !scan) return;
    for (let i = 1; i <= 4; i++) { const f = registered[(walkAt + i) % registered.length]; if (f) new Image().src = api.scanFrameUrl(pid, scan.id, f.index); }
  }, [walking, walkAt]); // eslint-disable-line react-hooks/exhaustive-deps

  const site: SiteView | undefined = ready ? {
    meshUrl: `${api.scanMeshUrl(pid, scan!.id)}?v=${scan!.mesh!.vertices}-${scan!.mesh!.triangles}`,
    sFromR: sMat, placement, unscaled: factor == null, tool: picking ? tool : null, onPick, e2e: E2E, marks,
    mode: cameraView ? "camera" : "orbit", camera: cameraView, robot, nav, memory,
    onFocusRobot: () => setNav((n) => ({ ...n, focus: n.focus + 1 })), onResetView: () => setNav((n) => ({ ...n, reset: n.reset + 1 })),
    line: linePts ? { a: rToS(linePts[0], sMat), b: rToS(linePts[1], sMat), label: measureText } : null,
    onReady: () => setReadyFor(scanId ?? null), onError: (message) => setFailed({ id: scanId ?? "", message }),
  } : undefined;

  // -- Apply to design -----------------------------------------------------------------------------------------------------
  const apply = useProjectJob<{ version: number }>(pid, "apply_scene", `v${version.version}`, async (r) => { await onApplied(r.version); });
  const moved = Object.keys(edits.markers);
  const hasEdits = edits.boxes.length > 0 || moved.length > 0 || edits.baseEdited || edits.removed.length > 0;
  async function applyToDesign() {
    setNotice(""); apply.setError("");
    if (!factor) { setNotice("Set a scale first"); return; }
    if (!hasEdits) { setNotice("Nothing to apply yet. Place the robot, add an object or move a task point."); return; }
    const mobile = version.spec.locomotion?.required;
    const markers: { id: string; position: P3 }[] = moved.map((id) => ({ id, position: edits.markers[id] }));
    if (edits.baseEdited && !mobile) markers.push({ id: "robot_base", position: baseW });
    // The floor must hold the markers, the base and the path with a margin (the server checks it).
    const pts: number[][] = [...editedScene.markers.map((m) => m.position), baseW, ...(version.spec.locomotion?.path ?? [])];
    const span = (i: number) => Math.max(...pts.map((p) => p[i])) - Math.min(...pts.map((p) => p[i])) + 0.5;
    const floor: [number, number] = [Math.max(editedScene.floor_size_m[0], Math.ceil(span(0) * 10) / 10), Math.max(editedScene.floor_size_m[1], Math.ceil(span(1) * 10) / 10)];
    const parts = [edits.baseEdited && "placed the robot", edits.boxes.length > 0 && `added ${edits.boxes.length} ${edits.boxes.length === 1 ? "object" : "objects"}`,
      moved.length > 0 && `moved ${moved.map((id) => names[id] ?? id).join(", ")}`, edits.removed.length > 0 && `removed ${edits.removed.join(", ")}`].filter(Boolean).join(", ");
    const result = await apply.run(() => api.applyScene(pid, {
      base_version: version.version, scene: { ...editedScene, floor_size_m: floor }, markers, auto_repair: false, note: parts,
    }), `v${version.version}`);
    if (!result) return;
    setEdits(() => NO_EDITS);
    await onApplied(result.version);
  }

  const yawChange = (yaw: number) => {
    const p = placementForBase(baseW, yaw, wToS(baseW, placement));
    setPlacement({ x: round(p.x, 4), y: round(p.y, 4), yaw_deg: yaw });
  };
  const sc = scan?.scale;

  return (
    <>
      {ready && (
        <div className="site-modes" role="group" aria-label="Site view">
          <button type="button" aria-pressed={!cameraView} onClick={() => chooseMode("orbit")}>3D view</button>
          <button type="button" aria-pressed={!!cameraView} disabled={!k || registered.length === 0} onClick={() => chooseMode("camera")}>Through the camera</button>
        </div>
      )}
      <Player ref={playerRef} scene={editedScene} parsed={version.parsed} sim={version.sim} robotBase={robotBase} height={height} site={site} />
      {ready && cameraView && (
        <div className="stack">
          <div className="walk-bar" role="group" aria-label="Walk">
            <button type="button" className="go" onClick={() => setWalking((w) => !w)}>{walking ? "Pause walk" : "Play walk"}</button>
            <input type="range" min={0} max={Math.max(registered.length - 1, 0)} step={1} value={walkAt} aria-label="Walk position" aria-valuetext={`Frame ${cameraView.index}`}
              onChange={(e) => { setWalking(false); setWalkPos(Number(e.target.value)); }} />
            <span className="small mono">Frame {cameraView.index} ({walkAt + 1} of {registered.length})</span>
            <button type="button" onClick={bestView}>Best view of the robot</button>
          </div>
          {!undistorted && <p className="small muted" role="note">Reconstruct again for exact alignment</p>}
        </div>
      )}
      <div className="stack site-panel" role="region" aria-label="My site">
        {ready && !meshReady && !meshError && <div className="busy" role="status">Loading the scan</div>}
        {meshError && <div className="notice error" role="alert">The scan could not be shown: {meshError}</div>}
        <ScanList pid={pid} scans={scans} selected={sel} onSelect={setSel} refresh={refresh} onError={setError} />
        {error && <div className="notice error" role="alert">{error}</div>}
        {!ready && <div className="notice small">{scan ? "Reconstruct this video to see your site in 3D." : "Add a video of your site to see the design in it."}</div>}
        {ready && (
          <>
            <div className="site-tools" role="toolbar" aria-label="Site tools">
              {TOOLS.map(({ id, label, icon: Icon }) => (
                <button key={id} type="button" aria-pressed={tool === id} onClick={() => pickTool(id)}><Icon size={16} aria-hidden="true" /> {label}</button>
              ))}
            </div>
            {tool && <p className="small muted">Press Escape to stop.</p>}

            {tool === "robot" && (
              <div className="stack">
                <p className="small">Click a surface in the view to put the robot's base there.</p>
                <div className="row">
                  <label className="field"><span>Robot turn (degrees)</span>
                    <input type="range" min={-180} max={180} step={1} value={Math.round(placement.yaw_deg)} onChange={(e) => yawChange(Number(e.target.value))} /></label>
                  <label className="field"><span>Turn</span>
                    <input type="number" step={1} value={Math.round(placement.yaw_deg)} onChange={(e) => yawChange(Number(e.target.value))} /></label>
                </div>
              </div>
            )}

            {tool === "object" && (
              <div className="stack">
                <p className="small">Choose what to add, then click a surface in the view.</p>
                <div className="row">
                  <label className="field"><span>Kind</span>
                    <select value={kind} onChange={(e) => { const k = e.target.value as Kind; setKind(k); setSize(KINDS[k].size); }}>
                      {(Object.keys(KINDS) as Kind[]).map((k) => <option key={k} value={k}>{KINDS[k].label}</option>)}
                    </select></label>
                  {(["Length (m)", "Width (m)", "Height (m)"] as const).map((label, i) => (
                    <label className="field" key={label}><span>{label}</span>
                      <input type="number" min={0.05} step={0.05} value={size[i]}
                        onChange={(e) => { const n = Number(e.target.value); setSize((s) => s.map((v, j) => (j === i ? (n > 0 ? n : v) : v)) as P3); }} /></label>
                  ))}
                </div>
              </div>
            )}

            {tool === "marker" && (
              <div className="stack">
                <label className="field"><span>Task point</span>
                  <select value={markerId} onChange={(e) => setMarkerId(e.target.value)}>
                    {markersEditable.map((m) => <option key={m.id} value={m.id}>{m.name || m.id}</option>)}
                  </select></label>
                <p className="small">Click a surface in the view to move the task point there.</p>
              </div>
            )}

            {tool === "measure" && (
              <div className="stack">
                <p className="small">Click two points in the view.</p>
                <p role="status" className="measure-result">{measured == null ? `Points: ${measurePts.length} of 2` : <>Distance: <b>{measureText}</b></>}</p>
                {measurePts.length > 0 && <div className="row"><button type="button" onClick={() => setMeasurePts([])}>Clear</button></div>}
              </div>
            )}

            {tool === "scale" && (
              <div className="stack">
                <div className="row" role="group" aria-label="How to set the scale">
                  <button type="button" aria-pressed={scaleMode === "sheet"} onClick={() => { setScaleMode("sheet"); setScalePts([]); }}>Sheet in a frame</button>
                  <button type="button" aria-pressed={scaleMode === "distance"} onClick={() => { setScaleMode("distance"); setFrameIdx(null); }}>Measured distance</button>
                </div>
                {scaleMode === "sheet" && (
                  <div className="stack">
                    <p className="small">Pick a frame where the whole sheet of paper lies on the floor, then mark its corners.</p>
                    <ul className="frame-strip" aria-label="Frames">
                      {registered.map((f) => (
                        <li key={f.index}>
                          <button type="button" aria-label={`Use frame ${f.index}`} title={`Frame ${f.index}, ${f.t_s.toFixed(1)} s`} aria-pressed={frameIdx === f.index} onClick={() => setFrameIdx(f.index)}>
                            <img src={api.scanFrameUrl(pid, scan!.id, f.index)} alt="" loading="lazy" width={96} />
                            <span className="small">{f.index}</span>
                          </button>
                        </li>
                      ))}
                    </ul>
                    {frameIdx != null && scan!.camera && (
                      <ArCalibrator key={`${scan!.id}-${frameIdx}`} pid={pid} scene={{ width: scan!.camera.width, height: scan!.camera.height }}
                        photoUrl={api.scanFrameUrl(pid, scan!.id, frameIdx)} submitLabel="Calculate scale" successText="Scale set."
                        onSubmit={async (corners, sz) => { replace(await api.scaleScan(pid, scan!.id, { method: "sheet", frame: frameIdx, corners_px: corners, size_m: sz })); }} />
                    )}
                  </div>
                )}
                {scaleMode === "distance" && (
                  <div className="stack">
                    <p className="small">Click two points a known distance apart, then type that distance.</p>
                    <div className="row">
                      <label className="field"><span>Real distance (m)</span>
                        <input type="number" min={0} step={0.01} value={scaleM} onChange={(e) => setScaleM(e.target.value)} /></label>
                      <button type="button" className="primary" disabled={busy || scalePts.length < 2 || !(Number(scaleM) > 0)}
                        onClick={() => void run(async () => { replace(await api.scaleScan(pid, scan!.id, { method: "distance", p1: scalePts[0], p2: scalePts[1], distance_m: Number(scaleM) })); setScalePts([]); setNotice("Scale set."); })}>
                        Calculate scale</button>
                    </div>
                    <p className="small muted">Points: {scalePts.length} of 2</p>
                  </div>
                )}
              </div>
            )}

            {tool === "floor" && (
              <div className="stack">
                <p className="small">Click three points on the floor, spread apart.</p>
                <p role="status" className="small">Points: {floorPts.length} of 3</p>
              </div>
            )}

            {sc ? <p className="small" role="status">Scale: factor {sc.factor.toFixed(4)}, residual {sc.residual.toFixed(3)} ({(sc.residual * 100).toFixed(1)}%), from {sc.method === "sheet" ? "a sheet in a frame" : "a measured distance"}.</p>
              : <p className="small muted">No scale yet. Sizes and distances are in scan units until you set one.</p>}
            {notice && <div className={`notice small${notice === "Floor updated." || notice === "Scale set." ? " ok" : ""}`} role="alert">{notice}</div>}

            {(edits.boxes.length > 0 || moved.length > 0 || edits.removed.length > 0) && (
              <ul className="files" aria-label="Edits">
                {edits.boxes.map((b) => (
                  <li key={b.id}><span>{b.id}: {b.size.join(" × ")} m</span>
                    <button type="button" aria-label={`Remove ${b.id}`} onClick={() => setEdits((e) => ({ ...e, boxes: e.boxes.filter((x) => x.id !== b.id) }))}><Delete size={16} aria-hidden="true" /> Remove</button></li>
                ))}
                {edits.removed.map((id) => (
                  <li key={id}><span>{id} removed from the design</span>
                    <button type="button" aria-label={`Put ${id} back`} onClick={() => setEdits((e) => ({ ...e, removed: e.removed.filter((x) => x !== id) }))}>Put back</button></li>
                ))}
                {moved.map((id) => (
                  <li key={id}><span>Task point {names[id] ?? id} moved</span>
                    <button type="button" aria-label={`Put ${names[id] ?? id} back`} onClick={() => setEdits((e) => { const { [id]: _gone, ...rest } = e.markers; return { ...e, markers: rest }; })}>Put back</button></li>
                ))}
              </ul>
            )}

            {version.scene.boxes.length > 0 && (
              <details className="design-boxes">
                <summary className="small">Boxes in the design ({version.scene.boxes.length - edits.removed.filter((id) => version.scene.boxes.some((b) => b.id === id)).length})</summary>
                <p className="small muted">A task point moved off its table leaves the table behind. Remove what is in the way.</p>
                <ul className="files" aria-label="Boxes in the design">
                  {version.scene.boxes.filter((b) => !edits.removed.includes(b.id)).map((b) => (
                    <li key={b.id}><span>{b.id}: {b.size.join(" × ")} m</span>
                      <button type="button" aria-label={`Remove ${b.id} from the design`} onClick={() => setEdits((e) => ({ ...e, removed: [...e.removed, b.id] }))}><Delete size={16} aria-hidden="true" /> Remove</button></li>
                  ))}
                </ul>
              </details>
            )}

            <div className="row">
              <button type="button" className="go" disabled={frozen || apply.busy || apply.blocked} onClick={() => void applyToDesign()}>Apply to design</button>
              {frozen && <span className="small muted">This design is approved and frozen.</span>}
            </div>
            <p className="small muted">Added objects are checked with the task. The scan itself is for looking, not for pass or fail.</p>
            <JobStatus busy={apply.busy} lines={apply.lines} error={apply.error} label="Applying" />
          </>
        )}
      </div>
    </>
  );
}
