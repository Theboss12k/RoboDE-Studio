// Scene view with a playback bar: play/pause, scrub over time, speed, phase label, loop, event markers.
import { useEffect, useMemo, useRef, useState } from "react";
import { Pause, Play } from "lucide-react";
import SceneView from "./SceneView";
import { fmt } from "./common";
import type { BasePose, Parsed, SceneSpec, SimResult } from "../types";

/** Index of the last frame whose time is <= t (frames are in time order). */
function frameAt(times: number[], t: number) {
  let lo = 0, hi = times.length - 1;
  while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (times[mid] <= t) lo = mid; else hi = mid - 1; }
  return lo;
}

export default function Player({ scene, parsed, sim, robotBase, height }: { scene: SceneSpec; parsed?: Parsed; sim: SimResult; robotBase: BasePose; height?: number }) {
  const frames = sim.frames ?? [];
  const times = useMemo(() => frames.map((f) => f.t), [frames]);
  const duration = Math.max(sim.duration_s || 0, times[times.length - 1] ?? 0, 0.001);
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [loop, setLoop] = useState(true);
  const state = useRef({ speed, loop, duration });
  state.current = { speed, loop, duration };
  useEffect(() => { setT(0); setPlaying(false); }, [sim]);
  useEffect(() => {
    if (!playing) return;
    let raf = 0, last = performance.now();
    const tick = (now: number) => {
      const dt = ((now - last) / 1000) * state.current.speed; last = now;
      setT((cur) => {
        const next = cur + dt;
        if (next < state.current.duration) return next;
        if (state.current.loop) return next % state.current.duration;
        setPlaying(false); return state.current.duration;
      });
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing]);
  const index = frames.length ? frameAt(times, t) : 0;
  const frame = frames[index];
  const base = frame?.base
    ? { x: frame.base[0], y: frame.base[1], z: robotBase.position[2], yaw: frame.base[2] }
    : { x: robotBase.position[0], y: robotBase.position[1], z: robotBase.position[2], yaw: robotBase.yaw };
  const bad = (sim.events ?? []).filter((e) => e.kind === "collision" || e.kind === "unreachable");
  return (
    <div className="stack">
      <SceneView scene={scene} parsed={parsed} frame={frame} frames={frames} index={index} base={base} initialBase={robotBase} height={height} />
      {!frames.length ? <div className="notice warning small">This simulation has no frames to play.</div> : (
        <div className="playbar">
          <button type="button" className="go" aria-label={playing ? "Pause" : "Play"} onClick={() => { if (!playing && t >= duration) setT(0); setPlaying(!playing); }}>
            {playing ? <Pause size={16} aria-hidden="true" /> : <Play size={16} aria-hidden="true" />}
          </button>
          <div className="timeline">
            <input type="range" min={0} max={duration} step={duration / 500} value={Math.min(t, duration)} aria-label="Time in the task"
              onChange={(e) => { setPlaying(false); setT(Number(e.target.value)); }} />
            <div className="events" aria-hidden="true">
              {bad.map((e, i) => <span key={i} className="event-mark" style={{ left: `${Math.min(100, (e.t / duration) * 100)}%` }} title={`${fmt(e.t, "s")}: ${e.text}`} />)}
            </div>
          </div>
          <span className="small mono time">{fmt(t)} / {fmt(duration, "s")}</span>
          <label className="small row" style={{ gap: 4 }}>Speed
            <select aria-label="Playback speed" value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>{[0.5, 1, 2].map((s) => <option key={s} value={s}>{s}x</option>)}</select></label>
          <label className="check small"><input type="checkbox" checked={loop} onChange={(e) => setLoop(e.target.checked)} /> Loop</label>
        </div>
      )}
      {frame?.phase && <div className="small"><b>Phase:</b> {frame.phase}{frame.holding ? <span className="muted"> (carrying {frame.holding})</span> : null}</div>}
      {bad.length > 0 && <ul className="small event-list">{bad.map((e, i) => <li key={i}><span className="tag bad">{e.kind}</span> {fmt(e.t, "s")}: {e.text}</li>)}</ul>}
    </div>
  );
}
