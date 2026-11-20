// Move the tool to x, y, z (metres, in the robot base frame), or jog it by a fixed step along an axis. The move itself
// is answered by the server (`/twin/move`), which solves the IK and applies the build's limits.
import { useEffect, useState } from "react";
import { fmt } from "../common";

type V3 = [number, number, number];
const AXES = ["x", "y", "z"] as const;

export default function ToolMove({ tcp, busy, disabled, onMove }: { tcp: V3 | null; busy: boolean; disabled: boolean; onMove: (xyz: V3) => void }) {
  const [text, setText] = useState<[string, string, string]>(["", "", ""]);
  const [step, setStep] = useState(20);  // mm
  useEffect(() => { if (tcp) setText(tcp.map((v) => String(Number(v.toFixed(3)))) as [string, string, string]); }, [tcp?.[0], tcp?.[1], tcp?.[2]]); // eslint-disable-line react-hooks/exhaustive-deps
  const xyz = text.map(Number) as V3;
  const valid = text.every((t) => t.trim() !== "" && Number.isFinite(Number(t)));
  const jog = (axis: number, dir: 1 | -1) => { if (!tcp) return; const t = [...tcp] as V3; t[axis] += (dir * step) / 1000; onMove(t); };
  return (
    <div className="stack">
      <div className="row tool-xyz">
        {AXES.map((a, i) => (
          <label key={a} className="field"><span>{a} (m)</span>
            <input type="number" step={0.01} value={text[i]} disabled={disabled} onChange={(e) => setText((t) => t.map((v, k) => (k === i ? e.target.value : v)) as [string, string, string])} /></label>))}
        <button type="button" className="primary" disabled={disabled || busy || !valid} onClick={() => onMove(xyz)}>Move tool to x, y, z</button>
      </div>
      <div className="row tool-jog" role="group" aria-label="Jog the tool">
        <label className="field"><span>Jog step (mm)</span>
          <input type="number" min={1} max={200} step={1} value={step} disabled={disabled} onChange={(e) => setStep(Math.min(200, Math.max(1, Number(e.target.value) || 1)))} /></label>
        {AXES.map((a, i) => (
          <span key={a} className="row" style={{ gap: 4 }}>
            <button type="button" disabled={disabled || busy || !tcp} aria-label={`Jog tool ${a} down`} onClick={() => jog(i, -1)}>−{a}</button>
            <button type="button" disabled={disabled || busy || !tcp} aria-label={`Jog tool ${a} up`} onClick={() => jog(i, 1)}>+{a}</button>
          </span>))}
      </div>
      <p className="small muted">{tcp ? <>Tool now at x {fmt(tcp[0])}, y {fmt(tcp[1])}, z {fmt(tcp[2])} m.</> : "Tool position not known yet."}</p>
    </div>
  );
}
