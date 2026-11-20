// "Time saved": labour hours a robot frees compared with doing the same items by hand, from the version's simulated cycle.
// The formulas are packages/core/src/savings.ts (identical to the Python used for the manufacturer PDF).
import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { fmt } from "./common";
import { CalcView } from "./Requirements";
import { Collapse, Expand } from "./icons";
import { robotCycleS, savingsLabel, versionSavings, type SavingsOverrides } from "@robode/studio-core";
import type { DesignVersion, Project } from "../types";

type Saved = SavingsOverrides;
const FIELDS: { key: keyof SavingsOverrides; label: string; unit: string; step: number; min: number; whole?: boolean }[] = [
  { key: "manual_cycle_s", label: "Manual time per item", unit: "s", step: 0.5, min: 0.1 },
  { key: "operators", label: "People doing it by hand", unit: "", step: 1, min: 1, whole: true },
  { key: "hours_per_shift", label: "Hours per shift", unit: "h", step: 0.5, min: 0.5 },
  { key: "days_per_week", label: "Days per week", unit: "", step: 1, min: 1 },
  { key: "days_per_year", label: "Days per year", unit: "", step: 1, min: 1 },
  { key: "supervision_min_per_shift", label: "Supervision per shift", unit: "min", step: 5, min: 0 },
];
const ORDER = ["hours_freed_per_day", "hours_freed_per_week", "hours_freed_per_year", "robot_items_per_hour", "manual_items_per_hour", "items_per_day", "manual_hours_per_day", "supervision_hours_per_day", "throughput_change"] as const;
const TITLES: Record<(typeof ORDER)[number], string> = {
  hours_freed_per_day: "Labour hours freed per day", hours_freed_per_week: "Labour hours freed per week", hours_freed_per_year: "Labour hours freed per year",
  robot_items_per_hour: "Robot items per hour", manual_items_per_hour: "Manual items per hour", items_per_day: "Robot items per day",
  manual_hours_per_day: "Person-hours to do those items by hand, per day", supervision_hours_per_day: "Supervision per day", throughput_change: "Throughput change",
};

export default function TimeSaved({ project, version, reload, disabled }: { project: Project; version: DesignVersion; reload: () => Promise<void>; disabled?: boolean }) {
  const [saved, setSaved] = useState<Saved | undefined>(project.savings_inputs);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  const key = JSON.stringify(project.savings_inputs ?? null);
  useEffect(() => { setSaved(project.savings_inputs); setDraft({}); }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const result = useMemo(() => versionSavings(version, saved), [version, saved]);
  const cycle = robotCycleS(version);
  if (!result && cycle === null) return null;
  const s = result?.savings, inputs = result?.inputs, source = result?.source ?? "estimate";
  const r = (k: (typeof ORDER)[number]) => s![k].result;
  const change = s ? s.throughput_change.result : 0;
  const fallback = { manual_cycle_s: "", operators: "1", hours_per_shift: "8", days_per_week: "5", days_per_year: "250", supervision_min_per_shift: "15" };
  const shown = (k: keyof SavingsOverrides) => draft[k] ?? (inputs ? String(inputs[k]) : String(saved?.[k] ?? fallback[k]));

  async function save() {
    const body: Record<string, number> = {};
    for (const f of FIELDS) {
      const raw = draft[f.key]; if (raw === undefined) continue;
      const n = Number(raw);
      if (raw.trim() === "" || Number.isNaN(n)) { setError(`${f.label} needs a number.`); return; }
      body[f.key] = n;
    }
    if (!Object.keys(body).length) return;
    setBusy(true); setError(""); setDone(false);
    try {
      const p = await api.put<Project>(`/projects/${project.id}/baseline`, body);
      setSaved(p.savings_inputs); setDraft({}); setDone(true);      // recomputed here with savings.ts, no new simulation
      await reload();
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

  return (
    <div className="panel stack">
      <div className="panel-head"><h3>Time saved</h3><span className="small muted">robot cycle {fmt(cycle, "s")} per item</span></div>
      {!result || !s || !inputs ? <p>Add today's manual time to see time saved.</p> : <>
      <dl className="facts">
        <dt>Labour hours freed</dt><dd><b>{fmt(r("hours_freed_per_day"))}</b> per day, <b>{fmt(r("hours_freed_per_week"))}</b> per week, <b>{fmt(r("hours_freed_per_year"))}</b> per year</dd>
        <dt>Items per hour</dt><dd>robot <b>{fmt(r("robot_items_per_hour"))}</b> against <b>{fmt(r("manual_items_per_hour"))}</b> by hand</dd>
        <dt>Throughput change</dt><dd><b>{change >= 0 ? "+" : ""}{fmt(change * 100)} %</b></dd>
        <dt>Supervision</dt><dd>{fmt(inputs.supervision_min_per_shift, "min")} per shift ({fmt(r("supervision_hours_per_day"), "h")} per day)</dd>
      </dl>
      <p className="small muted">{savingsLabel(source)}</p>
      <div>
        <button type="button" className="link" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? <Collapse size={16} aria-hidden="true" /> : <Expand size={16} aria-hidden="true" />} Working</button>
        {open && <div className="calc-grid">{ORDER.map((k) => <CalcView key={k} title={TITLES[k]} calc={s[k]} />)}</div>}
      </div></>}
      <details>
        <summary>{result ? "Change the manual baseline and working pattern" : "Add today's manual time"}</summary>
        <div className="stack">
          <div className="row fleet-fields">
            {FIELDS.map((f) => (
              <label key={f.key} className="field"><span>{f.label}{f.unit ? ` (${f.unit})` : ""}</span>
                <input type="number" min={f.min} step={f.step} value={shown(f.key)} disabled={disabled || busy}
                  onChange={(e) => { setDraft({ ...draft, [f.key]: e.target.value }); setDone(false); }} /></label>
            ))}
          </div>
          <div className="row">
            <button className="primary" disabled={disabled || busy || !Object.keys(draft).length} onClick={() => void save()}>Save and recalculate</button>
            {done && <span className="small muted" role="status">Saved.</span>}
          </div>
          {error && <div className="notice error" role="alert">{error}</div>}
        </div>
      </details>
    </div>
  );
}
