// Scene view with a playback bar: play/pause, scrub over time, speed, phase label, loop, event markers.
import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { baseAt, fleetObjectCenters, frameAt, memberFrameAt } from "@robode/studio-core";
import { Pause, Play } from "./icons";
import SceneView, { type ArView, type FleetOverlay, type FleetRobot, type SceneViewHandle } from "./SceneView";
import type { MutableRefObject } from "react";
import type { SiteView } from "./scan/types";
import { fmt } from "./common";
import type { BasePose, FleetPlan, Parsed, SceneSpec, SimResult } from "../types";

/** What a parent can read from the player without re-rendering at the playback rate. */
export type PlayerHandle = { getMoment: () => { t: number } };

/** A fleet to play on one shared clock: the plan and each member's parsed URDF (by member id). */
export type PlayerFleet = { plan: FleetPlan; parsed: Record<string, Parsed> };

const Player = forwardRef<PlayerHandle, { scene: SceneSpec; parsed?: Parsed; sim: SimResult; robotBase: BasePose; height?: number; fleet?: PlayerFleet; overlay?: FleetOverlay; meshBase?: string; handle?: MutableRefObject<SceneViewHandle | null>; ar?: ArView; site?: SiteView }>(function Player({ scene, parsed, sim, robotBase, height, fleet, overlay, meshBase, handle, ar, site }, ref) {
  const frames = sim.frames ?? [];
  const times = useMemo(() => frames.map((f) => f.t), [frames]);
  const members = fleet?.plan.members ?? [];
  const lastT = (fleet ? Math.max(0, ...members.map((m) => (m.t_offset_s || 0) + (m.sim.frames?.[m.sim.frames.length - 1]?.t ?? 0))) : times[times.length - 1]) ?? 0;
  const duration = Math.max((fleet ? fleet.plan.duration_s : sim.duration_s) || 0, lastT, 0.001);
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [loop, setLoop] = useState(true);
  const state = useRef({ speed, loop, duration });
  state.current = { speed, loop, duration };
  const moment = useRef(0);
  moment.current = t;
  useImperativeHandle(ref, () => ({ getMoment: () => ({ t: Math.min(Math.max(moment.current, 0), state.current.duration) }) }), []);
  useEffect(() => { setT(0); setPlaying(false); }, [sim, fleet?.plan]);
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
  const base = baseAt(frame, robotBase);
  const memberFrames = members.map((m) => memberFrameAt(m, t));
  const robots: FleetRobot[] | undefined = fleet ? members.flatMap((m, i) => {
    const mp = fleet.parsed[m.id];
    return mp ? [{ id: m.id, parsed: mp, positions: memberFrames[i]?.joints, base: baseAt(memberFrames[i], m.robot.base ?? scene.robot_base) }] : [];
  }) : undefined;
  const fleetCentres = useMemo(() => fleet ? fleetObjectCenters(scene.objects, members, t) : undefined, [fleet, scene.objects, t]); // eslint-disable-line react-hooks/exhaustive-deps
  const bad = ((fleet ? fleet.plan.events : sim.events) ?? []).filter((e) => e.kind === "collision" || e.kind === "unreachable");
  return (
    <div className="stack">
      <SceneView scene={scene} parsed={parsed} frame={frame} frames={frames} index={index} base={base} initialBase={robotBase} height={height} robots={robots} centres={fleetCentres} overlay={overlay} meshBase={meshBase} handle={handle} ar={ar} site={site} />
      {!frames.length && !fleet ? <div className="notice warning small">This simulation has no frames to play.</div> : (
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
      {fleet ? (
        <ul className="small plain fleet-phases" aria-label="What each robot is doing now">
          {members.map((m, i) => { const f = memberFrames[i]; return (
            <li key={m.id}><b>{m.id} {m.name}:</b> {f?.phase || "idle"}{f?.holding ? <span className="muted"> (carrying {f.holding})</span> : null}</li>); })}
        </ul>
      ) : frame?.phase && <div className="small"><b>Phase:</b> {frame.phase}{frame.holding ? <span className="muted"> (carrying {frame.holding})</span> : null}</div>}
      {bad.length > 0 && <ul className="small event-list">{bad.map((e, i) => <li key={i}><span className="tag bad">{e.kind}</span> {fmt(e.t, "s")}: {e.text}</li>)}</ul>}
    </div>
  );
});
export default Player;
