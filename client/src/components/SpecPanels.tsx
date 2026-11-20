// Read-only summaries of the technical specification and the robot concept.
import { Fragment } from "react";
import { fmt } from "./common";
import type { RobotConcept, TechnicalSpec } from "../types";

export function SpecSummary({ spec }: { spec: TechnicalSpec }) {
  const name = (id: string) => spec.objects?.find((o) => o.id === id)?.name ?? id;
  const marker = (id: string) => spec.markers?.find((m) => m.id === id)?.name ?? id;
  return (
    <div className="panel">
      <h3>Specification</h3>
      <p>{spec.summary}</p>
      <div><b className="small">Actions, in order</b>
        <ol className="plain">{(spec.actions ?? []).map((a, i) => (
          <li key={i}>{a.verb}{a.object ? ` ${name(a.object)}` : ""}{a.target ? ` at ${marker(a.target)}` : ""}{a.duration_s ? ` (${fmt(a.duration_s, "s")})` : ""}{a.description ? <span className="muted"> – {a.description}</span> : null}</li>))}
        </ol></div>
      {(spec.objects?.length ?? 0) > 0 && <div><b className="small">Objects</b>
        <ul className="plain">{spec.objects.map((o) => <li key={o.id}>{o.name}: {fmt(o.mass_kg, "kg")}, {o.size_m.map((v) => fmt(v * 1000)).join(" × ")} mm, {o.grip} grip{o.fragile ? ", fragile" : ""}</li>)}</ul></div>}
      <div className="kv small">
        <span className="muted">Environment</span><span>{spec.environment || "–"}</span>
        {spec.duty_cycle && <><span className="muted">Duty cycle</span><span>{spec.duty_cycle}</span></>}
        <span className="muted">Accuracy</span><span>{fmt(spec.accuracy_m * 1000, "mm")}</span>
      </div>
      {(spec.assumptions?.length ?? 0) > 0 && <div><b className="small">Assumptions made by the AI</b>
        <ul className="plain small">{spec.assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul></div>}
    </div>
  );
}

export function ConceptPanel({ robot, title = "Robot concept" }: { robot: RobotConcept; title?: string }) {
  const params = Object.entries(robot.params ?? {}).filter(([, v]) => typeof v === "number" || typeof v === "string" || typeof v === "boolean");
  return (
    <div className="panel">
      <h3>{title}</h3>
      <div className="row"><b>{robot.name}</b><span className="tag">{robot.family.replace("_", " ")}</span></div>
      {robot.rationale && <p className="small">{robot.rationale}</p>}
      <div className="kv small">{params.map(([k, v]) => <Fragment key={k}><span className="muted">{k.replace(/_/g, " ")}</span><span>{typeof v === "number" ? fmt(v) : String(v)}</span></Fragment>)}</div>
    </div>
  );
}
