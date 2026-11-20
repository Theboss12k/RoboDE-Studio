import { Fail, Pass } from "./icons";
import type { RepairAttempt, RepairLog as RepairLogData } from "../types";

const OUTCOME: Record<RepairAttempt["outcome"], string> = {
  kept: "kept",
  worse: "not better",
  no_change: "no change",
  changed_task: "changed the task (discarded)",
  invalid: "unusable answer",
};

/** What the automatic collision repair tried. Counts are computed in code; the summary is the model's proposal. */
export default function RepairLog({ log }: { log: RepairLogData }) {
  const attempts = log.attempts ?? [];
  return (
    <div className="repair-log">
      <h4>Automatic collision repair</h4>
      <p className="small">
        {log.collisions_before} collision episode(s) found; {log.kept > 0 ? `kept attempt ${log.kept}` : "original kept"}. {log.stop_reason}
      </p>
      {attempts.length > 0 && (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Attempt</th><th>Outcome</th><th>Collisions left</th><th>Checks passed</th><th>Model's summary (proposal)</th></tr></thead>
            <tbody>
              {attempts.map((a) => (
                <tr key={a.attempt} className={a.outcome === "kept" ? "" : "off"}>
                  <td>{a.attempt}</td>
                  <td>{a.outcome === "kept" ? <Pass size={14} aria-hidden="true" /> : a.outcome === "invalid" ? <Fail size={14} aria-hidden="true" /> : null} {OUTCOME[a.outcome] ?? a.outcome}</td>
                  <td>{a.collisions ?? "n/a"}</td>
                  <td>{a.checks_passed} of {a.checks_total}</td>
                  <td>
                    {a.summary || <span className="muted">none</span>}
                    {a.note && <div className="small muted">{a.note}</div>}
                    <details><summary className="small">Request sent</summary><p className="small">{a.request}</p></details>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="small muted">Collisions and checks are computed in code after each attempt (kinematic checks, not dynamics).</p>
    </div>
  );
}
