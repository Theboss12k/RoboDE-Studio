// "View in my space": photo scenes of a project, calibration, placement of the design in the photo, snapshot.
// The selected scene is owned by the parent (Design) so the player can render it; this panel edits it.
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api";
import { useProjectJob } from "../../jobs";
import { Add, Delete, Download, LinkPhone } from "../icons";
import { JobStatus } from "../common";
import ArCalibrator from "./ArCalibrator";
import PhoneLink from "./PhoneLink";
import { normalizePhoto } from "./normalizePhoto";
import type { ArCalibration, ArPlacement, ArScene } from "../../types";

export default function ArPanel({ pid, scene, onScene, showBoxes, onShowBoxes, getCanvas }: {
  pid: string;
  scene: ArScene | null;
  onScene: (scene: ArScene | null) => void;
  showBoxes: boolean;
  onShowBoxes: (on: boolean) => void;
  /** The WebGL canvas of the AR view, for the snapshot. */
  getCanvas: () => HTMLCanvasElement | null;
}) {
  const [scenes, setScenes] = useState<ArScene[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [calibrating, setCalibrating] = useState(false);
  const [linking, setLinking] = useState(false);
  const sceneRef = useRef(scene);
  sceneRef.current = scene;

  const refreshList = useCallback(async () => {
    try { setScenes(await api.arScenes(pid)); } catch (e) { setError((e as Error).message); }
  }, [pid]);
  useEffect(() => { void refreshList(); }, [refreshList]);
  useEffect(() => { if (!sceneRef.current && scenes.length) onScene(scenes[0]); }, [scenes]); // eslint-disable-line react-hooks/exhaustive-deps

  const reloadSelected = useCallback(async () => {
    const cur = sceneRef.current;
    if (!cur) return;
    try { const fresh = await api.arScene(pid, cur.id); onScene(fresh); await refreshList(); } catch (e) { setError((e as Error).message); }
  }, [pid, onScene, refreshList]);

  // The estimate runs as a background job; a result that arrives while this panel was away reloads the scene.
  const estimate = useProjectJob<ArCalibration>(pid, "ar_estimate", undefined, async () => { await reloadSelected(); });

  async function upload(file: File | undefined) {
    if (!file) return;
    setBusy(true); setError("");
    try {
      const made = await api.uploadArScene(pid, await normalizePhoto(file));
      await refreshList();
      onScene(made);
      setCalibrating(true);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function remove() {
    if (!scene || !window.confirm("Delete this photo?")) return;
    setBusy(true); setError("");
    try {
      await api.deleteArScene(pid, scene.id);
      const rest = (await api.arScenes(pid));
      setScenes(rest); onScene(rest[0] ?? null); setCalibrating(false);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function runEstimate() {
    if (!scene) return;
    setError("");
    const done = await estimate.run(() => api.estimateArScene(pid, scene.id));
    if (done) await reloadSelected();
  }

  // Placement changes show at once and are saved a moment after the last change.
  const saved = useRef(new Map<string, string>());
  const place = scene?.placement;
  const sid = scene?.id;
  useEffect(() => {
    if (!sid || !place) return;
    const key = `${place.x}|${place.y}|${place.yaw_deg}`;
    if (!saved.current.has(sid)) { saved.current.set(sid, key); return; }
    if (saved.current.get(sid) === key) return;
    const timer = window.setTimeout(() => {
      saved.current.set(sid, key);
      api.setArPlacement(pid, sid, { x: place.x, y: place.y, yaw_deg: place.yaw_deg }).catch((e) => setError((e as Error).message));
    }, 400);
    return () => window.clearTimeout(timer);
  }, [pid, sid, place?.x, place?.y, place?.yaw_deg]); // eslint-disable-line react-hooks/exhaustive-deps
  function setPlacement(change: Partial<ArPlacement>) {
    if (!scene) return;
    onScene({ ...scene, placement: { ...scene.placement, ...change } });
  }

  async function snapshot() {
    if (!scene) return;
    setError("");
    try {
      const gl = getCanvas();
      if (!gl) throw new Error("The view is not ready");
      const photo = new Image();
      photo.src = api.arPhotoUrl(pid, scene.id);
      await photo.decode();
      const out = document.createElement("canvas");
      out.width = scene.width; out.height = scene.height;
      const ctx = out.getContext("2d");
      if (!ctx) throw new Error("This browser cannot make the picture");
      ctx.drawImage(photo, 0, 0, scene.width, scene.height);
      ctx.drawImage(gl, 0, 0, scene.width, scene.height);
      const blob = await new Promise<Blob | null>((resolve) => out.toBlob(resolve, "image/png"));
      if (!blob) throw new Error("The picture could not be saved");
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = `view-in-my-space-${scene.id}.png`;
      document.body.appendChild(a); a.click(); a.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) { setError((e as Error).message); }
  }

  const cal = scene ? (scene.calibration ?? scene.estimate) : null;
  return (
    <div className="stack ar-panel">
      <div className="panel-head">
        <h4>View in my space</h4>
        <label className="button">{busy ? "Working" : <><Add size={16} aria-hidden="true" /> Add photo</>}
          <input type="file" accept="image/*" className="sr-only" disabled={busy}
            onChange={(e) => { void upload(e.target.files?.[0]); e.target.value = ""; }} /></label>
      </div>
      <div className="row">
        <button type="button" aria-expanded={linking} onClick={() => setLinking(!linking)}><LinkPhone size={16} aria-hidden="true" /> Link a phone</button>
      </div>
      {linking && <PhoneLink pid={pid} onReceived={() => void refreshList()} onClose={() => setLinking(false)} />}
      <p className="small muted">A visual check only. Pass or fail still comes from the task check.</p>
      {scenes.length > 0 && (
        <ul className="files" aria-label="Photos">
          {scenes.map((s) => (
            <li key={s.id}>
              <button type="button" aria-pressed={scene?.id === s.id} onClick={() => { onScene(s); setCalibrating(false); }}>
                {new Date(s.created).toLocaleString()}</button>
              <span className="tag">{s.calibration ? "Calibrated" : s.estimate ? "Estimated" : "Not set"}</span>
            </li>
          ))}
        </ul>
      )}
      {scene && (
        <>
          <div className="row">
            <button disabled={estimate.busy} onClick={() => void runEstimate()}>Estimate automatically</button>
            <button onClick={() => setCalibrating(!calibrating)} aria-expanded={calibrating}>{calibrating ? "Close calibrator" : "Calibrate with a rectangle"}</button>
            <button className="danger" disabled={busy} onClick={() => void remove()}><Delete size={16} aria-hidden="true" /> Delete</button>
          </div>
          <JobStatus busy={estimate.busy} lines={estimate.lines} error={estimate.error} label="Estimating" />
          {calibrating && <ArCalibrator key={scene.id} pid={pid} scene={scene} photoUrl={api.arPhotoUrl(pid, scene.id)} onScene={(s) => { onScene(s); void refreshList(); }} />}
          {!cal && <div className="notice small">Calibrate with a rectangle, or estimate automatically, to see the design in the photo.</div>}
          {cal && !scene.calibration && <div className="notice warning small">Approximate scale. Calibrate with a rectangle of known size for true scale.</div>}
          {!calibrating && cal && scene.calibration?.residual_px != null && <p className="small muted">Calibration residual {scene.calibration.residual_px.toFixed(2)} px.</p>}
          <div className="row">
            <label className="field"><span>Turn (degrees)</span>
              <input type="range" min={-180} max={180} step={1} value={scene.placement.yaw_deg} onChange={(e) => setPlacement({ yaw_deg: Number(e.target.value) })} /></label>
            <label className="field"><span>Turn</span>
              <input type="number" step={1} value={scene.placement.yaw_deg} onChange={(e) => setPlacement({ yaw_deg: Number(e.target.value) })} /></label>
            <label className="field"><span>x (m)</span>
              <input type="number" step={0.05} value={Number(scene.placement.x.toFixed(3))} onChange={(e) => setPlacement({ x: Number(e.target.value) })} /></label>
            <label className="field"><span>y (m)</span>
              <input type="number" step={0.05} value={Number(scene.placement.y.toFixed(3))} onChange={(e) => setPlacement({ y: Number(e.target.value) })} /></label>
          </div>
          <p className="small muted">You can also drag on the floor in the view to move the design.</p>
          <div className="row">
            <label className="check-line"><input type="checkbox" checked={showBoxes} onChange={(e) => onShowBoxes(e.target.checked)} /> Show scene boxes</label>
            <button disabled={!cal} onClick={() => void snapshot()}><Download size={16} aria-hidden="true" /> Save snapshot</button>
          </div>
        </>
      )}
      {error && <div className="notice error" role="alert">{error}</div>}
    </div>
  );
}
