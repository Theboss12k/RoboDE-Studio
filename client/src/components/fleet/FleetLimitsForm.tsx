import { useEffect, useState } from "react";
import { api } from "../../api";
import type { FleetLimits, FleetUpdate, Project } from "../../types";

export const FAMILIES: [string, string][] = [["serial_arm", "Serial arm"], ["gantry", "Gantry"]];
export const DEFAULT_LIMITS: FleetLimits = { max_robots: 2, families: ["serial_arm", "gantry"], max_reach_m: null, max_payload_kg: null, budget_usd: null, brands: [], note: "" };

const num = (s: string) => (s.trim() === "" || Number.isNaN(Number(s)) ? null : Number(s));

/** The user's limits for the fleet. Code-defined fields, no AI: they are stored with the project and checked in code. */
export default function FleetLimitsForm({ project, reload, disabled }: { project: Project; reload: () => Promise<void>; disabled?: boolean }) {
  const saved = project.fleet?.limits ?? DEFAULT_LIMITS;
  const [limits, setLimits] = useState<FleetLimits>(saved);
  const [brands, setBrands] = useState(saved.brands.join(", "));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  const key = JSON.stringify(project.fleet?.limits ?? null);
  useEffect(() => { const s = project.fleet?.limits ?? DEFAULT_LIMITS; setLimits(s); setBrands(s.brands.join(", ")); }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const set = (patch: Partial<FleetLimits>) => { setLimits((l) => ({ ...l, ...patch })); setDone(false); };
  async function save() {
    setBusy(true); setError(""); setDone(false);
    const body: FleetLimits = { ...limits, brands: brands.split(",").map((b) => b.trim()).filter(Boolean) };
    try { await api.put(`/projects/${project.id}/fleet`, { limits: body } satisfies FleetUpdate); await reload(); setDone(true); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  const off = disabled || busy;
  return (
    <div className="panel stack">
      <h3>Fleet limits</h3>
      <p className="small muted">Your limits for the robots in the fleet. Max robots, families, reach and payload are checked in code when the fleet is planned.</p>
      <div className="row fleet-fields">
        <label className="field"><span>Max robots</span>
          <select value={limits.max_robots} disabled={off} onChange={(e) => set({ max_robots: Number(e.target.value) })}>{[2, 3].map((n) => <option key={n} value={n}>{n}</option>)}</select></label>
        <label className="field"><span>Max reach (m)</span>
          <input type="number" min={0} step={0.1} value={limits.max_reach_m ?? ""} disabled={off} onChange={(e) => set({ max_reach_m: num(e.target.value) })} /></label>
        <label className="field"><span>Max payload (kg)</span>
          <input type="number" min={0} step={0.5} value={limits.max_payload_kg ?? ""} disabled={off} onChange={(e) => set({ max_payload_kg: num(e.target.value) })} /></label>
        <label className="field"><span>Budget (USD)</span>
          <input type="number" min={0} step={100} value={limits.budget_usd ?? ""} disabled={off} onChange={(e) => set({ budget_usd: num(e.target.value) })} />
          <span className="small muted">Stored, not checked in Design.</span></label>
      </div>
      <fieldset className="row" disabled={off}>
        <legend className="small">Allowed robot families</legend>
        {FAMILIES.map(([id, label]) => (
          <label key={id} className="check-line"><input type="checkbox" checked={limits.families.includes(id)}
            onChange={(e) => set({ families: e.target.checked ? [...limits.families, id] : limits.families.filter((f) => f !== id) })} /> {label}</label>
        ))}
      </fieldset>
      <label className="field"><span>Preferred brands (comma separated)</span>
        <input type="text" value={brands} disabled={off} onChange={(e) => { setBrands(e.target.value); setDone(false); }} placeholder="e.g. Universal Robots, FANUC" />
        <span className="small muted">Stored, not checked.</span></label>
      <label className="field"><span>Note</span>
        <textarea rows={2} value={limits.note} disabled={off} onChange={(e) => set({ note: e.target.value })} /></label>
      <div className="row">
        <button className="primary" disabled={off} onClick={() => void save()}>Save limits</button>
        {done && <span className="small muted" role="status">Saved.</span>}
      </div>
      {error && <div className="notice error" role="alert">{error}</div>}
    </div>
  );
}
