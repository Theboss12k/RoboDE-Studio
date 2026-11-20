// Live readouts: each joint's position, speed and torque use, plus "limited by" chips for the limits that bind.
import { fmt } from "../common";
import type { TwinJointLimit } from "../../types";
import { speedUnitOf, toDisplay, unitOf } from "./units";

export type LimitChip = { joint: string; limit: string; detail: string };

export default function Readouts({ limits, q, qd, torqueUse, chips }: {
  limits: TwinJointLimit[]; q: Record<string, number>; qd: Record<string, number>; torqueUse: Record<string, number> | null; chips: LimitChip[];
}) {
  return (
    <div className="stack">
      <div className="table-wrap">
        <table>
          <caption className="sr-only">Joint position, speed and torque use</caption>
          <thead><tr><th>Joint</th><th>Position</th><th>Speed</th><th>Torque use</th></tr></thead>
          <tbody>
            {limits.map((l) => {
              const use = torqueUse?.[l.joint];
              return (
                <tr key={l.joint}>
                  <td>{l.joint}</td>
                  <td className="mono">{fmt(toDisplay(l.kind, q[l.joint] ?? 0))} {unitOf(l.kind)}</td>
                  <td className="mono">{fmt(toDisplay(l.kind, qd[l.joint] ?? 0))} {speedUnitOf(l.kind)}</td>
                  <td className="mono">{use === undefined || !Number.isFinite(use) ? "–" : <span className={use > 1 ? "warn-text" : undefined}>{Math.round(use * 100)} % of {fmt(l.max_torque)} {l.kind === "prismatic" ? "N" : "N·m"}</span>}</td>
                </tr>);
            })}
          </tbody>
        </table>
      </div>
      <div className="row" aria-live="polite">
        {chips.map((c) => <span key={`${c.joint}-${c.limit}`} className="tag warn" title={c.detail}>limited by {c.joint} {c.limit}</span>)}
        {!chips.length && <span className="small muted">No limit is binding.</span>}
      </div>
      <p className="small muted">Torque use is a static plus acceleration estimate from the chosen motors, computed by the server for the last settled pose, move or run.</p>
    </div>
  );
}
