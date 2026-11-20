import { useEffect, useState } from "react";
import { api } from "../api";
import { JobStatus, StageHead, fmt, screenChips } from "../components/common";
import { useProjectJob } from "../jobs";
import { RobotPlaceholder } from "../components/icons";
import type { Candidate, PriceEstimate, Project, SourcedValue } from "../types";

function Image({ url }: { url: string }) {
  const [failed, setFailed] = useState(false);
  if (!url || failed) return <div className="cand-img fallback" aria-hidden="true"><RobotPlaceholder size={36} /></div>;
  return <img className="cand-img" src={url} alt="" referrerPolicy="no-referrer" loading="lazy" onError={() => setFailed(true)} />;
}

function Price({ price }: { price: PriceEstimate | null }) {
  if (!price || (price.low == null && price.high == null)) return <span className="muted">No published price</span>;
  const range = price.low != null && price.high != null && price.low !== price.high ? `${fmt(price.low)} to ${fmt(price.high)}` : fmt(price.low ?? price.high);
  return (
    <span><b>{range} {price.currency}</b> <span className="tag warn">web estimate</span>
      <span className="small muted"> {price.date && `${/^seen\b/i.test(price.date) ? price.date : `dated ${price.date}`}, `}<a href={price.source_url} target="_blank" rel="noreferrer noopener">source</a>{price.note ? `. ${price.note}` : ""}</span></span>
  );
}

function Spec({ label, v }: { label: string; v: SourcedValue }) {
  return (
    <li><span className="muted">{label}:</span> {v.value}{v.unit ? " " + v.unit : ""}{v.source_url && <> <a className="small" href={v.source_url} target="_blank" rel="noreferrer noopener" title={v.quote}>source</a></>}</li>
  );
}

function Card({ c, onAnalyse, analysed, supported }: { c: Candidate; onAnalyse: () => void; analysed: boolean; supported: boolean }) {
  const specs: [string, SourcedValue | null][] = [["Payload (kg)", c.payload_kg], ["Reach (m)", c.reach_m], ["Axes", c.dof], ["Repeatability (mm)", c.repeatability_mm]];
  return (
    <article className="panel cand">
      <Image url={c.image_url} />
      <div>
        <h3>{c.manufacturer} {c.model}</h3>
        <div className="row"><span className="tag">{c.family.replace("_", " ")}</span><span className={`tag ${c.confidence === "high" ? "ok" : c.confidence === "low" ? "warn" : ""}`}>confidence: {c.confidence}</span></div>
      </div>
      <div className="small"><Price price={c.price} /></div>
      <ul className="plain small">
        {specs.filter(([, v]) => v).map(([k, v]) => <Spec key={k} label={k} v={v!} />)}
        {c.key_specs.map((s, i) => <Spec key={i} label="Spec" v={s} />)}
      </ul>
      <div className="row" aria-label="Quick screen against the task">{screenChips(c.screen ?? [])}</div>
      <div className="row">
        {supported || analysed
          ? <button className="go" onClick={onAnalyse}>{analysed ? "View analysis" : "Analyse"}</button>
          : <span className="small muted">{c.model} is a {c.family.replace("_", " ")} robot; Studio can model serial arms, gantries, mobile bases and mobile manipulators. Its listed specs and the quick screen still apply.</span>}
        {c.product_url && <a className="small" href={c.product_url} target="_blank" rel="noreferrer noopener">Product page</a>}
      </div>
      <p className="small muted">Image linked from the vendor's site, not stored here.</p>
    </article>
  );
}

export default function Options({ project, reload, analyse }: { project: Project; reload: () => Promise<void>; analyse: (cid: string) => void }) {
  const [cands, setCands] = useState<Candidate[]>([]);
  const [loadError, setLoadError] = useState("");
  const [families, setFamilies] = useState<string[] | null>(null);
  // A search that finished while this stage was away: show its candidates once.
  const job = useProjectJob<{ candidates: Candidate[] }>(project.id, "search", undefined, async (r) => { setCands(r.candidates ?? []); await reload(); });
  useEffect(() => { api.get<{ name: string }[]>("/families").then((f) => setFamilies(f.map((x) => x.name))).catch(() => setFamilies(null)); }, []);
  const load = () => api.get<Candidate[] | { candidates: Candidate[] }>(`/projects/${project.id}/candidates`)
    .then((r) => { setCands(Array.isArray(r) ? r : r?.candidates ?? []); setLoadError(""); }).catch((e) => setLoadError(e.message));
  useEffect(() => { void load(); }, [project.id]); // eslint-disable-line react-hooks/exhaustive-deps
  async function search() {
    const r = await job.run<{ candidates: Candidate[] }>(() => api.post(`/projects/${project.id}/search`));
    if (r) { setCands(r.candidates ?? []); await reload(); }
  }
  return (
    <div className="page wide">
      <StageHead step="Step 5 of 7" title="Robot options">
        Search the web for real robots that could do this task. Prices are web estimates with a date and a source, not quotes. The quick screen compares only the published payload and reach.
      </StageHead>
      <div className="row"><button className="go big" disabled={job.busy} onClick={() => void search()}>{cands.length ? "Search again" : "Find real robots"}</button></div>
      <JobStatus {...job} label="Searching" />
      {loadError && <div className="notice error" role="alert">{loadError}</div>}
      <div className="cand-grid">{cands.map((c) => <Card key={c.id} c={c} analysed={(project.analyses ?? []).includes(c.id)} supported={c.family !== "other" && (families === null || families.includes(c.family))} onAnalyse={() => analyse(c.id)} />)}</div>
    </div>
  );
}
