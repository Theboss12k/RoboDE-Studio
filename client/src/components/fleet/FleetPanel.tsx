import { useState } from "react";
import { api } from "../../api";
import Requirements from "../Requirements";
import { screenChips } from "../common";
import type { DesignVersion, FleetUpdate, Project } from "../../types";

const KEY_PARAMS = 5;
function params(p: Record<string, unknown>) {
  return Object.entries(p).filter(([, v]) => typeof v === "number" || typeof v === "string").slice(0, KEY_PARAMS)
    .map(([k, v]) => `${k.replace(/_/g, " ")}: ${typeof v === "number" ? Number(v.toPrecision(4)) : v}`).join(", ");
}

/** The members, the fleet checks, the planner log and the member that robot search will use. */
export default function FleetPanel({ project, version, reload }: { project: Project; version: DesignVersion; reload: () => Promise<void> }) {
  const fleet = version.fleet;
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  if (!fleet) return null;
  const chosen = project.fleet?.member ?? fleet.primary;
  async function choose(member: string) {
    setBusy(true); setError("");
    try { await api.put(`/projects/${project.id}/fleet`, { member } satisfies FleetUpdate); await reload(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return (
    <div className="stack">
      <div className="panel stack">
        <div className="panel-head"><h3>Fleet</h3>
          <span className={`tag ${fleet.ok ? "ok" : "bad"}`}>{fleet.ok ? "Fleet checks pass" : "Some fleet checks fail"}</span></div>
        <p className="small muted">{fleet.members.length} robots on one shared clock, {Number(fleet.duration_s.toPrecision(4))} s per run, repeating every {Number(fleet.period_s.toPrecision(4))} s. Numbers are computed in code (kinematic and static-load checks, not dynamics or hardware tests).</p>
        <div className="member-list">
          {fleet.members.map((m) => (
            <article key={m.id} className="member-card">
              <h4>{m.id}: {m.name}{m.id === fleet.primary && <span className="tag"> primary</span>}</h4>
              <div className="small"><span className="tag">{m.robot.family.replace("_", " ")}</span> {params(m.robot.params)}</div>
              <div className="row" aria-label={`Limit checks for ${m.id}`}>{m.limit_checks.length ? screenChips(m.limit_checks) : <span className="small muted">No limit checks for this member.</span>}</div>
              <details><summary className="small">Requirements and checks</summary>
                <Requirements requirements={m.requirements} checks={m.sim.checks} caption={`Requirements for ${m.name}`} /></details>
            </article>
          ))}
        </div>
      </div>
      <div className="panel stack"><h3>Fleet checks</h3>
        <Requirements requirements={[]} checks={fleet.checks} caption="Checks across the whole fleet" /></div>
      <div className="panel stack">
        <label className="field"><span>Use for robot search</span>
          <select value={chosen} disabled={busy} onChange={(e) => void choose(e.target.value)}>
            {fleet.members.map((m) => <option key={m.id} value={m.id}>{m.id} {m.name}</option>)}
          </select></label>
        <p className="small muted">Robot search and analysis work on one robot at a time: the one chosen here.</p>
        {error && <div className="notice error" role="alert">{error}</div>}
      </div>
      {fleet.planner_log.length > 0 && (
        <details className="panel"><summary>Planner log ({fleet.planner_log.length} lines)</summary>
          <ol className="small muted progress">{fleet.planner_log.map((l, i) => <li key={i}>{l}</li>)}</ol></details>
      )}
    </div>
  );
}
