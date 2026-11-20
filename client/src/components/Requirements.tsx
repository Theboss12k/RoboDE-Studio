// Requirement table: required value, the robot's value, PASS/FAIL/—, and each row's traceable calculation.
import { useState } from "react";
import { PassChip, fmt } from "./common";
import type { Calc, Check, Requirement } from "../types";

function CalcView({ title, calc }: { title: string; calc: Calc }) {
  return (
    <div className="calc">
      <b className="small">{title}</b>
      <code>{calc.formula}</code>
      <ul className="small">
        {Object.entries(calc.inputs ?? {}).map(([k, q]) => <li key={k}><code>{k}</code> = {fmt(q.value, q.unit)}</li>)}
      </ul>
      <div className="small">Result: <b>{fmt(calc.result, calc.unit)}</b></div>
      {calc.note && <div className="small muted">{calc.note}</div>}
    </div>
  );
}

type Row = { id: string; name: string; required: string; actual: string; passed: boolean | null; requirement?: Requirement; check?: Check };

function rows(requirements: Requirement[], checks: Check[]): Row[] {
  const byId = new Map(checks.map((c) => [c.requirement_id, c]));
  const out: Row[] = requirements.map((r) => {
    const c = byId.get(r.id);
    return {
      id: r.id, name: r.name, requirement: r, check: c, passed: c ? c.passed : null,
      required: r.comparator === "info" ? `info: ${fmt(r.target, r.unit)}` : `${r.comparator} ${fmt(r.target, r.unit)}`,
      actual: c ? fmt(c.actual, c.unit) : "–",
    };
  });
  const known = new Set(requirements.map((r) => r.id));
  for (const c of checks) if (!known.has(c.requirement_id)) out.push({ id: c.requirement_id, name: c.name, check: c, passed: c.passed, required: c.required === null ? "–" : fmt(c.required, c.unit), actual: fmt(c.actual, c.unit) });
  return out;
}

export default function Requirements({ requirements, checks, caption }: { requirements: Requirement[]; checks: Check[]; caption?: string }) {
  const [open, setOpen] = useState<string | null>(null);
  const list = rows(requirements, checks);
  return (
    <div className="stack">
      <div className="table-wrap">
        <table>
          <caption className="sr-only">{caption ?? "Requirements and the robot's values"}</caption>
          <thead><tr><th>Requirement</th><th>Required</th><th>Robot</th><th>Result</th></tr></thead>
          <tbody>
            {list.map((r) => (
              <RowView key={r.id} row={r} open={open === r.id} toggle={() => setOpen(open === r.id ? null : r.id)} />
            ))}
          </tbody>
        </table>
      </div>
      <p className="small muted">Kinematic and static-load checks, not dynamics or hardware tests. A dash means the value could not be checked.</p>
    </div>
  );
}

function RowView({ row, open, toggle }: { row: Row; open: boolean; toggle: () => void }) {
  const has = !!(row.requirement?.calc || row.check?.calc || row.check?.detail);
  return (
    <>
      <tr>
        <td>{has ? <button type="button" className="link" aria-expanded={open} onClick={toggle}>{open ? "▾" : "▸"} {row.name}</button> : row.name}</td>
        <td>{row.required}</td><td>{row.actual}</td><td><PassChip passed={row.passed} /></td>
      </tr>
      {open && (
        <tr><td colSpan={4}>
          <div className="calc-grid">
            {row.requirement?.calc && <CalcView title="How the requirement was derived" calc={row.requirement.calc} />}
            {row.check?.calc && <CalcView title="How the robot's value was obtained" calc={row.check.calc} />}
            {row.check?.detail && <div className="small">{row.check.detail}</div>}
          </div>
        </td></tr>
      )}
    </>
  );
}
