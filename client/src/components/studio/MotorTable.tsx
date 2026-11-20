// Motors and electronics: what each joint needs next to what is chosen (product, shop link, dated price, gear ratio,
// verified / assumed / defined values, margins), the alternatives found, and the driver, controller and supply rows.
import { useState } from "react";
import { fmt } from "../common";
import { Collapse, Expand, PreferredVendor, ShopLink } from "../icons";
import type { ElectronicsItem, JointSizing, MotorAlternative, MotorChoice, ShopPrice, SpecValue } from "../../types";

// Fields the sourcing step adds to rows and prices. Older stored builds do not have them, so every read tolerates absence.
export type Rejected = { id?: string; name?: string; shop?: string; url?: string; reason?: string };
export const rejectedOf = (row: unknown): Rejected[] => { const r = (row as { rejected?: unknown } | null)?.rejected; return Array.isArray(r) ? (r as Rejected[]) : []; };
export const isPreferred = (row: unknown) => (row as { preferred?: boolean } | null)?.preferred === true;
export const isUnverified = (price: unknown) => (price as { status?: string } | null)?.status === "unverified";
/** The build's own notes about the search (for example shops that blocked the server); empty for older builds. */
export const sourcingNotes = (build: unknown): string[] => { const n = (build as { sourcing_notes?: unknown } | null)?.sourcing_notes; return Array.isArray(n) ? n.filter((x): x is string => typeof x === "string" && !!x) : []; };

export function Price({ price }: { price: ShopPrice | null }) {
  if (!price || price.amount == null) return <span className="muted small">no price listed</span>;
  const unverified = isUnverified(price);
  return (
    <span>{price.currency} {fmt(price.amount)}{" "}
      {unverified && <span className="tag warn" title="The server could not open this shop page itself, so the figure is the model's quote and was not checked. It is not counted in totals or used to rank the product.">unverified</span>}{" "}
      <span className="small muted">as listed, seen {price.seen}{price.shop ? ` on ${price.shop}` : ""}{unverified ? ". Not counted in totals" : ""}</span></span>
  );
}

export function PreferredBadge({ row }: { row: unknown }) {
  if (!isPreferred(row)) return null;
  return <span className="tag ok" title="Sold by one of your preferred vendors (Settings)"><PreferredVendor size={12} aria-hidden="true" />Preferred vendor</span>;
}

const REASONS: Record<string, string> = {
  no_price: "No price on the shop page",
  page_blocked: "The shop page could not be opened (blocked)",
  quote_not_found: "The model's quote was not found on the page",
  price_not_in_quote: "The price is not in the quote",
  missing_values: "Required values are missing",
};
export function rejectReason(reason: string | undefined): string {
  const r = (reason ?? "").trim();
  if (!r) return "No reason recorded";
  if (REASONS[r]) return REASONS[r];
  const m = /^fails_margin:?\s*(.*)$/.exec(r);
  if (m) return `Does not meet the margin${m[1] ? `: ${m[1]}` : ""}`;
  const v = /^missing_values:?\s*(.*)$/.exec(r);
  return v ? `Required values are missing${v[1] ? `: ${v[1]}` : ""}` : r;
}

/** "Why other products were not used": the products found for this row that were set aside, each with its reason. */
export function RejectedList({ row }: { row: unknown }) {
  const [open, setOpen] = useState(false);
  const items = rejectedOf(row);
  if (!items.length) return null;
  return (
    <div className="small">
      <button type="button" className="link" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? <Collapse size={16} aria-hidden="true" /> : <Expand size={16} aria-hidden="true" />} Why other products were not used ({items.length})</button>
      {open && (
        <ul className="plain alt-list">
          {items.map((x, i) => (
            <li key={x.id ?? `${x.name}-${i}`} className="alt">
              <b>{x.name ? <Shop name={x.name} url={x.url ?? ""} /> : "Unnamed product"}</b>{x.shop ? <span className="muted"> · {x.shop}</span> : null}
              <div className="muted" title={x.reason}>{rejectReason(x.reason)}</div>
            </li>))}
        </ul>)}
    </div>
  );
}

export function SourcingNotes({ build }: { build: unknown }) {
  const notes = sourcingNotes(build);
  if (!notes.length) return null;
  return <div className="notice small" role="note"><b>Sourcing notes</b><ul className="plain">{notes.map((n, i) => <li key={i}>{n}</li>)}</ul></div>;
}

const MARK = { verified: "ok", assumed: "warn", defined: "" } as const;
export function ValueMarks({ values }: { values: SpecValue[] }) {
  if (!values.length) return null;
  return (
    <ul className="spec-values small">
      {values.map((v) => (
        <li key={v.key} title={v.quote ? `"${v.quote}"` : v.note || undefined}>
          {v.key.replace(/_/g, " ")}: <b>{v.value != null ? fmt(v.value, v.unit) : v.text || "–"}</b> <span className={`tag ${MARK[v.status]}`}>{v.status}</span>
        </li>))}
    </ul>
  );
}

const Margins = ({ margins }: { margins: Record<string, number> }) => (
  <ul className="small plain">
    {Object.entries(margins).map(([k, v]) => <li key={k}>{k.replace(/_/g, " ")}: <b className={k === "inertia_ratio" ? undefined : v >= 1 ? undefined : "warn-text"}>{k === "inertia_ratio" ? fmt(v) : `${fmt(v)}x`}</b></li>)}
  </ul>
);

function Shop({ name, url }: { name: string; url: string }) {
  return url ? <a href={url} target="_blank" rel="noreferrer noopener">{name} <ShopLink size={12} aria-hidden="true" style={{ verticalAlign: "-1px" }} /><span className="sr-only"> (opens the shop page)</span></a> : <>{name}</>;
}

function Alternative({ alt, chosen, disabled, onUse }: { alt: MotorAlternative; chosen: boolean; disabled: boolean; onUse: () => void }) {
  return (
    <li className="alt">
      <div className="row"><b><Shop name={alt.name} url={alt.url} /></b> <PreferredBadge row={alt} /> <span className={`tag ${alt.passes ? "ok" : "bad"}`}>{alt.passes ? "passes" : "fails"}</span>
        {chosen ? <span className="tag">in use</span> : <button type="button" disabled={disabled} onClick={onUse}>Use this one</button>}</div>
      <div className="small"><Price price={alt.price} /> {alt.drive && <span className="muted"> · {alt.drive.name || alt.drive.type}, ratio {fmt(alt.drive.ratio)}</span>}</div>
      {alt.reason && <div className="small muted">{alt.reason}</div>}
      <ValueMarks values={alt.values} />
    </li>
  );
}

function MotorRow({ m, need, frozen, onPin }: { m: MotorChoice; need: JointSizing | undefined; frozen: boolean; onPin: (joint: string, id: string) => void }) {
  const [open, setOpen] = useState(false);
  const defined = m.source === "defined";
  return (
    <>
      <tr>
        <td><b>{m.joint}</b>{m.pinned && <div><span className="tag">pinned</span></div>}</td>
        <td className="small">{need ? <>continuous {fmt(need.continuous, need.torque_unit)}<br />peak {fmt(need.peak, need.torque_unit)}<br />speed {fmt(need.speed, need.speed_unit)}</> : "–"}</td>
        <td>
          {defined
            ? <><span className="tag warn">Defined spec (not found in shops)</span><div className="small">{m.name}</div><div className="small muted">Procure to this spec.</div></>
            : <><Shop name={m.name} url={m.url} /> <PreferredBadge row={m} /><div className="small"><Price price={m.price} /></div></>}
          <div className="small muted">{m.motor_type.replace("_", " ")} · {m.drive.name || m.drive.type}, gear ratio {fmt(m.drive.ratio)}, efficiency {fmt(m.drive.efficiency)}</div>
          <ValueMarks values={m.values} />
          <RejectedList row={m} />
        </td>
        <td>{Object.keys(m.margins).length ? <Margins margins={m.margins} /> : <span className="muted small">–</span>}
          {m.checks.some((c) => c.passed === false) && <div className="small warn-text">{m.checks.filter((c) => c.passed === false).map((c) => c.name).join(", ")} fails</div>}</td>
        <td>{m.alternatives.length
          ? <button type="button" className="link" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? <Collapse size={16} aria-hidden="true" /> : <Expand size={16} aria-hidden="true" />} {m.alternatives.length} alternative{m.alternatives.length === 1 ? "" : "s"}</button>
          : <span className="muted small">none found</span>}</td>
      </tr>
      {open && <tr><td colSpan={5}><ul className="plain alt-list">{m.alternatives.map((a) => <Alternative key={a.id} alt={a} chosen={a.id === m.alternative_id} disabled={frozen} onUse={() => onPin(m.joint, a.id)} />)}</ul></td></tr>}
    </>
  );
}

export function MotorTable({ motors, sizing, frozen, onPin }: { motors: MotorChoice[]; sizing: JointSizing[]; frozen: boolean; onPin: (joint: string, id: string) => void }) {
  return (
    <div className="table-wrap">
      <table>
        <caption className="sr-only">Motors: required against chosen</caption>
        <thead><tr><th>Joint</th><th>Needs (joint side)</th><th>Chosen</th><th>Margins</th><th>Alternatives</th></tr></thead>
        <tbody>{motors.map((m) => <MotorRow key={m.joint} m={m} need={sizing.find((s) => s.joint === m.joint)} frozen={frozen} onPin={onPin} />)}</tbody>
      </table>
    </div>
  );
}

export function ElectronicsTable({ items }: { items: ElectronicsItem[] }) {
  if (!items.length) return null;
  return (
    <div className="table-wrap">
      <table>
        <caption className="sr-only">Driver, controller and power supply</caption>
        <thead><tr><th>Item</th><th>For</th><th>Chosen</th><th>Checks</th></tr></thead>
        <tbody>
          {items.map((e) => (
            <tr key={e.id}>
              <td><b>{e.role.replace("_", " ")}</b>{e.qty > 1 && <span className="muted"> × {e.qty}</span>}</td>
              <td className="small">{e.for_joints.join(", ") || "all"}</td>
              <td>{e.source === "defined" ? <><span className="tag warn">Defined spec (not found in shops)</span><div className="small">{e.name}</div><div className="small muted">Procure to this spec.</div></>
                : <><Shop name={e.name} url={e.url} /> <PreferredBadge row={e} /><div className="small"><Price price={e.price} /></div></>}
                <ValueMarks values={e.values} />
                <RejectedList row={e} /></td>
              <td className="small">{e.checks.length ? e.checks.map((c) => <div key={c.requirement_id}><span className={`tag ${c.passed === true ? "ok" : c.passed === false ? "bad" : ""}`}>{c.passed === true ? "pass" : c.passed === false ? "fail" : "n/a"}</span> {c.name}{c.detail ? <span className="muted"> – {c.detail}</span> : null}</div>) : "–"}</td>
            </tr>))}
        </tbody>
      </table>
    </div>
  );
}
