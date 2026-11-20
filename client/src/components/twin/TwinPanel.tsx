// The digital twin of a build: the robot with its STL meshes in the design's scene, joint sliders and jog at a fixed
// 20 ms step under the build's limits (clampStep from core), tool moves and "Run the task" answered by the server.
// Limits, torque use and "limited by" notes come from the server's result; the local step only previews slider motion.
import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { baseAt, clampStep, type JointLimit } from "@robode/studio-core";
import { api } from "../../api";
import { Reset } from "../icons";
import Player from "../Player";
import SceneView, { type SceneViewHandle, type SceneViewName } from "../SceneView";
import type { BasePose, Frame, SceneSpec, SimResult, StudioBuildView, TwinMoveRequest, TwinMoveResult } from "../../types";
import JointSliders, { type Jog } from "./JointSliders";
import Readouts, { type LimitChip } from "./Readouts";
import ToolMove from "./ToolMove";

export type TwinHandle = { capture: (view: SceneViewName) => Promise<Blob> };
type Rec = Record<string, number>;
type V3 = [number, number, number];
const clip = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), hi);
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

const TwinPanel = forwardRef<TwinHandle, { pid: string; build: StudioBuildView; scene: SceneSpec; robotBase: BasePose }>(function TwinPanel({ pid, build, scene, robotBase }, ref) {
  const tl = build.twin_limits;
  const arm = tl.joints;
  const all = useMemo(() => (tl.gripper ? [...arm, tl.gripper] : arm), [tl]);
  const dt = tl.dt_s > 0 ? tl.dt_s : 0.02;
  const url = `/projects/${pid}/studio/builds/${build.build}`;
  const meshBase = `/api/v1${url}/files/urdf/`;
  const rest = useCallback((): Rec => Object.fromEntries(all.map((l) => [l.joint, clip(0, l.lower, l.upper)])), [all]);

  const state = useRef({ q: rest(), t: rest(), v: Object.fromEntries(all.map((l) => [l.joint, 0])) as Rec, jog: null as Jog, play: null as { frames: Frame[]; start: number } | null, wasMoving: false });
  const [pose, setPose] = useState<Rec>(state.current.q);
  const [speeds, setSpeeds] = useState<Rec>(state.current.v);
  const [target, setTarget] = useState<Rec>(state.current.t);
  const [liveHits, setLiveHits] = useState<Map<string, LimitChip>>(new Map());
  const [result, setResult] = useState<TwinMoveResult | null>(null);
  const [tcp, setTcp] = useState<V3 | null>(null);
  const [scale, setScale] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [run, setRun] = useState<SimResult | null>(null);
  const handle = useRef<SceneViewHandle | null>(null);
  const alive = useRef(true);
  const scaleRef = useRef(scale);
  scaleRef.current = scale;
  // Set true on every mount: React's StrictMode (dev) mounts, unmounts and mounts again.
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const armQ = () => Object.fromEntries(arm.map((l) => [l.joint, state.current.q[l.joint]])) as Rec;
  const gripperQ = () => (tl.gripper ? state.current.q[tl.gripper.joint] : null);

  async function ask(target: TwinMoveRequest["target"], speed = scaleRef.current): Promise<TwinMoveResult | undefined> {
    try {
      const r = await api.post<TwinMoveResult>(`${url}/twin/move`, { q0: armQ(), target, speed_scale: speed } satisfies TwinMoveRequest);
      if (alive.current) setError("");
      return r;
    } catch (e) { if (alive.current) setError((e as Error).message); return undefined; }
  }
  /** What the server says about the pose we are in (torque use, tool position): a move to where we already are. */
  const probe = useCallback(async () => {
    const r = await ask({ joints: armQ(), cartesian: null, gripper: gripperQ() });
    if (!r || !alive.current) return;
    setResult(r);
    const last = r.frames[r.frames.length - 1];
    if (last?.tcp) setTcp(last.tcp as V3);
  }, [build.build]); // eslint-disable-line react-hooks/exhaustive-deps

  const publish = () => { const s = state.current; setPose({ ...s.q }); setSpeeds({ ...s.v }); };
  const settleTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  // The fixed-step loop: plays server frames when there are some, else one clampStep per tick toward the target.
  useEffect(() => {
    const limits: JointLimit[] = all.map((l) => ({ lower: l.lower, upper: l.upper, max_speed: l.max_speed, max_accel: l.max_accel, max_torque: l.max_torque }));
    const id = setInterval(() => {
      const s = state.current;
      if (s.play) {
        const el = (performance.now() - s.play.start) / 1000, fr = s.play.frames;
        let i = 0;
        while (i + 1 < fr.length && fr[i + 1].t <= el) i++;
        for (const l of all) if (l.joint in fr[i].joints) s.q[l.joint] = fr[i].joints[l.joint];
        s.v = Object.fromEntries(all.map((l) => [l.joint, 0]));
        if (i >= fr.length - 1) { s.play = null; s.t = { ...s.q }; setTarget({ ...s.t }); s.wasMoving = true; }
        publish();
        return;
      }
      if (s.jog) {
        const l = all.find((x) => x.joint === s.jog!.joint)!;
        s.t[l.joint] = clip(s.q[l.joint] + s.jog.dir * l.max_speed * 0.3, l.lower, l.upper);
        setTarget({ ...s.t });
      }
      const q = all.map((l) => s.q[l.joint]), tg = all.map((l) => s.t[l.joint]), v = all.map((l) => s.v[l.joint]);
      const idle = q.every((x, i) => Math.abs(tg[i] - x) < 1e-9) && v.every((x) => x === 0);
      if (idle) {
        if (s.wasMoving) { s.wasMoving = false; clearTimeout(settleTimer.current); settleTimer.current = setTimeout(() => void probe(), 150); }
        return;
      }
      s.wasMoving = true;
      const r = clampStep(q, tg, dt, limits, v, scaleRef.current);
      all.forEach((l, i) => { s.q[l.joint] = r.q[i]; s.v[l.joint] = r.qd[i]; });
      if (r.hits.length) setLiveHits((m) => { const n = new Map(m); for (const [i, kind] of r.hits) n.set(`${all[i].joint}-${kind}`, { joint: all[i].joint, limit: kind, detail: "Hit while moving the sliders; the twin held the joint within its limit." }); return n; });
      publish();
    }, dt * 1000);
    return () => { clearInterval(id); clearTimeout(settleTimer.current); };
  }, [all, dt, probe]);

  // A new build: back to the rest pose, and ask where the tool is.
  useEffect(() => {
    const s = state.current;
    s.q = rest(); s.t = rest(); s.v = Object.fromEntries(all.map((l) => [l.joint, 0])); s.jog = null; s.play = null;
    setTarget({ ...s.t }); setLiveHits(new Map()); setResult(null); setRun(null); setTcp(null); publish();
    void probe();
  }, [build.build]); // eslint-disable-line react-hooks/exhaustive-deps

  async function moveTool(xyz: V3) {
    if (state.current.play || busy) return;
    setBusy(true); setRun(null);
    const r = await ask({ joints: null, cartesian: xyz, gripper: gripperQ() });
    if (r && alive.current) {
      setResult(r); setLiveHits(new Map());
      if (r.frames.length) state.current.play = { frames: r.frames, start: performance.now() };
      if (!r.reached) setError(`The tool could not reach that point${r.ik_error_m != null ? ` (off by ${(r.ik_error_m * 1000).toFixed(1)} mm)` : ""}.`);
    }
    if (alive.current) setBusy(false);
  }
  async function runTask() {
    setBusy(true); setError("");
    try {
      const r = await api.post<TwinMoveResult>(`${url}/twin/run`, { speed_scale: scale });
      if (!alive.current) return;
      setResult(r); setLiveHits(new Map());
      setRun({ frames: r.frames, events: [], checks: [], duration_s: r.duration_s, ok: r.reached });
    } catch (e) { if (alive.current) setError((e as Error).message); }
    finally { if (alive.current) setBusy(false); }
  }
  function reset() {
    const s = state.current;
    s.q = rest(); s.t = rest(); s.v = Object.fromEntries(all.map((l) => [l.joint, 0])); s.jog = null; s.play = null;
    setTarget({ ...s.t }); setLiveHits(new Map()); setRun(null); setError(""); publish();
    void probe();
  }
  const setJointTarget = (joint: string, value: number) => { state.current.t[joint] = value; state.current.play = null; setTarget({ ...state.current.t }); setRun(null); };
  const nudge = (joint: string, dir: 1 | -1) => {
    const l = all.find((x) => x.joint === joint)!;
    setJointTarget(joint, clip(state.current.q[joint] + dir * (l.upper - l.lower) / 20, l.lower, l.upper));
  };
  const setJog = (jog: Jog) => {
    const s = state.current;
    s.jog = jog;
    if (jog) { s.play = null; setRun(null); }
    else { s.t = { ...s.q }; setTarget({ ...s.t }); }  // released: stop where the joint is
  };

  useImperativeHandle(ref, () => ({
    capture: async (view) => { if (run) { setRun(null); await sleep(250); } if (!handle.current) throw new Error("The 3D view is not ready"); return handle.current.capture(view); },
  }), [run]);

  const chips = useMemo(() => {
    const m = new Map<string, LimitChip>();
    for (const h of result?.limits ?? []) m.set(`${h.joint}-${h.limit}`, { joint: h.joint, limit: h.limit, detail: h.detail });
    for (const [k, c] of liveHits) m.set(k, c);
    return [...m.values()];
  }, [result, liveHits]);
  const frame: Frame = { t: 0, joints: pose, base: null, holding: "", tcp: null, phase: "" };
  const base = baseAt(undefined, robotBase);
  const gripper = tl.gripper;

  return (
    <div className="twin">
      <div className="twin-view stack">
        {run
          ? <Player scene={scene} parsed={build.parsed} sim={run} robotBase={robotBase} meshBase={meshBase} />
          : <SceneView scene={scene} parsed={build.parsed} frame={frame} frames={[]} index={0} base={base} initialBase={robotBase} meshBase={meshBase} handle={handle} />}
        {run && <div className="row"><span className={`tag ${result?.reached ? "ok" : "warn"}`}>Task played with this build's limits ({run.duration_s.toFixed(1)} s)</span><button type="button" onClick={() => setRun(null)}>Back to the live twin</button></div>}
      </div>
      <div className="twin-controls stack">
        <div className="panel sunk stack">
          <h4>Joints</h4>
          <JointSliders limits={arm} target={target} disabled={busy || !!run} onTarget={setJointTarget} onJog={setJog} onNudge={nudge} />
          {gripper && (
            <div className="row">
              <b className="small">Gripper</b>
              <button type="button" disabled={busy || !!run} onClick={() => setJointTarget(gripper.joint, gripper.upper)}>Open</button>
              <button type="button" disabled={busy || !!run} onClick={() => setJointTarget(gripper.joint, gripper.lower)}>Close</button>
              <span className="small mono">{Math.round((pose[gripper.joint] ?? 0) * 1000)} mm</span>
            </div>)}
        </div>
        <div className="panel sunk stack">
          <h4>Tool</h4>
          <ToolMove tcp={tcp} busy={busy} disabled={!!run} onMove={(xyz) => void moveTool(xyz)} />
        </div>
        <div className="panel sunk stack">
          <h4>Run the task</h4>
          <div className="row">
            <label className="field"><span>Speed scale</span>
              <input type="number" min={0.1} max={3} step={0.1} value={scale} onChange={(e) => setScale(Math.min(3, Math.max(0.1, Number(e.target.value) || 1)))} /></label>
            <button type="button" className="primary" disabled={busy} onClick={() => void runTask()}>Run the task</button>
            <button type="button" disabled={busy && !run} onClick={reset}><Reset size={15} aria-hidden="true" /> Reset</button>
          </div>
          <p className="small muted">Above 1 asks for more speed than the motors allow; the limits then bind and are listed below. The task is the design's own waypoints, played with this build's limits.</p>
        </div>
        {error && <div className="notice error" role="alert">{error}</div>}
        {busy && <div className="busy">Asking the twin</div>}
      </div>
      <div className="twin-readouts">
        <h4>Readouts</h4>
        <Readouts limits={all} q={pose} qd={speeds} torqueUse={result?.torque_use ?? null} chips={chips} />
      </div>
    </div>
  );
});
export default TwinPanel;
