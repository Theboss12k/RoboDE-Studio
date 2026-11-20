import { useState } from "react";
import { api } from "../../api";
import type { FleetUpdate, Project } from "../../types";

const SOURCE: Record<string, string> = {
  spec: "spec agent proposal",
  code: "code check",
  both: "spec agent proposal, confirmed by a code check",
  user: "set by you",
};

/** Shown when a fleet was suggested or is on: why, who said so, and the on/off switch. */
export default function FleetBanner({ project, reload, disabled }: { project: Project; reload: () => Promise<void>; disabled?: boolean }) {
  const fleet = project.fleet;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!fleet?.suggested && !fleet?.enabled) return null;
  const on = !!fleet.enabled;
  async function toggle(enabled: boolean) {
    setBusy(true); setError("");
    try { await api.put(`/projects/${project.id}/fleet`, { enabled } satisfies FleetUpdate); await reload(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  const source = fleet.source ? SOURCE[fleet.source] ?? fleet.source : "";
  return (
    <div className={`notice fleet-banner ${on ? "ok" : "warning"}`}>
      <div className="panel-head">
        <b>{on ? "Multi-robot system is working on this task" : "One robot may not be enough for this task"}</b>
        <label className="check-line"><input type="checkbox" role="switch" checked={on} disabled={busy || disabled} onChange={(e) => void toggle(e.target.checked)} /> Plan with several robots</label>
      </div>
      {(fleet.reasons ?? []).length > 0 && (
        <ul className="plain small">{(fleet.reasons ?? []).map((r, i) => <li key={i}>{r}</li>)}</ul>
      )}
      {source && <p className="small muted">Reasons from: {source}. A model's proposal counts only after code checks it.</p>}
      {disabled && <p className="small muted">The switch is locked while a design step is running or the design is approved.</p>}
      {error && <div className="notice error" role="alert">{error}</div>}
    </div>
  );
}
