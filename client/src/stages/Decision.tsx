import { useEffect, useMemo, useState } from "react";
import { useAskSelection } from "../components/AskBar";
import { api } from "../api";
import { StageHead } from "../components/common";
import { fmt } from "../components/common";
import { Download } from "../components/icons";
import { savingsInputsFor, resolveBaseline, savings as computeSavings, savingsLabel, robotCycleS } from "@robode/studio-core";
import type { Analysis, Candidate, Check, DesignVersion, Project, StudioBuildView } from "../types";

const STYLE: Record<string, string> = { extrusion_printed: "Aluminium extrusion with printed parts", machined_aluminium: "Machined aluminium" };

/** Estimated purchase cost of a build: priced shop items summed per currency; everything else is a defined spec. */
function cost(b: StudioBuildView) {
  const totals: Record<string, number> = {}; const dates = new Set<string>(); let defined = 0;
  const add = (price: { amount: number | null; currency: string; seen: string } | null, qty: number) => {
    if (!price || price.amount == null) { defined += 1; return; }
    totals[price.currency] = (totals[price.currency] ?? 0) + price.amount * qty;
    if (price.seen) dates.add(price.seen);
  };
  b.motors.forEach((m) => add(m.source === "shop" ? m.price : null, 1));
  b.electronics.forEach((e) => add(e.source === "shop" ? e.price : null, e.qty || 1));
  return { totals, dates: [...dates].sort(), defined };
}

function tally(checks: Check[]) {
  return { passed: checks.filter((c) => c.passed === true).length, none: checks.filter((c) => c.passed == null).length, failed: checks.filter((c) => c.passed === false).length };
}

function buildChecks(b: StudioBuildView): Check[] {
  return [...b.checks, ...b.style.checks, ...b.motors.flatMap((m) => m.checks), ...b.electronics.flatMap((e) => e.checks)];
}

/** One line of time saved, from a design version's spec and a robot cycle; null when there is no cycle. */
function savedLine(spec: DesignVersion["spec"] | undefined, cycle: number | null | undefined, project: Project) {
  if (!spec || !cycle || !(cycle > 0) || resolveBaseline(spec.baseline, project.savings_inputs).manual_cycle_s === null) return null;
  const { inputs, source } = savingsInputsFor(spec, cycle, project.savings_inputs);
  const weekly = computeSavings(inputs).hours_freed_per_week.result;
  return { text: weekly > 0 ? `Frees about ${fmt(weekly)} labour hours per week` : "Frees no labour time: the robot is slower than the manual baseline", title: savingsLabel(source) };
}

const pdfUrl = (pid: string, n: number) => `/api/v1/projects/${pid}/studio/builds/${n}/pdf`;
const packageUrl = (pid: string, n: number) => `/api/v1/projects/${pid}/studio/builds/${n}/package`;
const dl = <Download size={15} aria-hidden="true" style={{ verticalAlign: "-2px" }} />;

export default function Decision({ project, reload, cid, openStudio, openOptions }: { project: Project; reload: () => Promise<void>; cid: string | null; openStudio?: () => void; openOptions?: () => void }) {
  const pid = project.id;
  const analysed = project.analyses ?? [];
  const approvedBuild = project.studio?.approved_build ?? null;
  const [pick, setPick] = useState(cid && analysed.includes(cid) ? cid : analysed[0] ?? "");
  useAskSelection({ candidate: pick || undefined });
  const [cands, setCands] = useState<Candidate[]>([]);
  const [build, setBuild] = useState<StudioBuildView | null>(null);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [versions, setVersions] = useState<Record<number, DesignVersion>>({});
  const versionList = project.versions ?? [];
  const specVersion = project.approved_version ?? versionList[versionList.length - 1];
  const wantVersions = [build?.design_version, specVersion].filter((n): n is number => n != null);
  useEffect(() => {
    for (const n of new Set(wantVersions)) if (!versions[n])
      api.get<DesignVersion>(`/projects/${pid}/versions/${n}`).then((v) => setVersions((c) => ({ ...c, [n]: v }))).catch(() => undefined);
  }, [pid, wantVersions.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { api.get<Candidate[] | { candidates: Candidate[] }>(`/projects/${pid}/candidates`).then((r) => setCands(Array.isArray(r) ? r : r?.candidates ?? [])).catch(() => undefined); }, [pid]);
  useEffect(() => {
    if (approvedBuild == null) { setBuild(null); return; }
    let live = true;
    api.get<StudioBuildView>(`/projects/${pid}/studio/builds/${approvedBuild}`).then((b) => { if (live) setBuild(b); }).catch((e) => { if (live) setError((e as Error).message); });
    return () => { live = false; };
  }, [pid, approvedBuild]);
  useEffect(() => {
    if (!pick) { setAnalysis(null); return; }
    let live = true;
    setAnalysis(null);
    api.get<Analysis>(`/projects/${pid}/analysis/${pick}`).then((a) => { if (live) setAnalysis(a); }).catch(() => undefined);
    return () => { live = false; };
  }, [pid, pick]);
  const name = (id: string) => { const c = cands.find((x) => x.id === id); return c ? `${c.manufacturer} ${c.model}` : id; };
  const cand = cands.find((c) => c.id === pick);
  const money = useMemo(() => (build ? cost(build) : null), [build]);
  const bchecks = useMemo(() => (build ? tally(buildChecks(build)) : null), [build]);

  async function decide(body: Record<string, unknown>) {
    setBusy(true); setError("");
    try { await api.post(`/projects/${pid}/decision`, body); await reload(); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const d = project.decision;
  const chosenSelf = d?.action === "self";
  const candTally = analysis ? tally(analysis.sim.checks) : null;
  const ownVersion = build ? versions[build.design_version] : undefined;
  const ownSaved = savedLine(ownVersion?.spec, ownVersion ? robotCycleS(ownVersion) : null, project);
  const candSaved = savedLine(specVersion != null ? versions[specVersion]?.spec : undefined, analysis?.sim?.duration_s, project);
  const priceText = cand?.price && (cand.price.low != null || cand.price.high != null)
    ? `${cand.price.currency} ${cand.price.low != null && cand.price.high != null && cand.price.low !== cand.price.high ? `${fmt(cand.price.low)} to ${fmt(cand.price.high)}` : fmt(cand.price.low ?? cand.price.high)}`
    : null;
  return (
    <div className="page narrow">
      <StageHead artifact="decision" step="Step 7 of 7" title="Decision">Choose between building your own design and buying a robot from the catalog. Prices and specifications are web estimates; confirm them with the supplier before buying.</StageHead>
      {d && (
        <div className="notice ok" role="status">
          <div className="row">
            <b>{chosenSelf ? `Decision: our own design (build b${d.build})` : `Decision: buy ${name(d.candidate_id)}`}</b>
            {chosenSelf && <a className="button primary" href={pdfUrl(pid, d.build)} download>{dl} Download manufacturer PDF</a>}
          </div>
        </div>
      )}
      {error && <div className="notice error" role="alert">{error}</div>}
      <div className="grid-2 decision-cards">
        <section className={`panel decision-card${chosenSelf ? " chosen" : ""}`} aria-labelledby="own-design">
          <h3 id="own-design">Our own design</h3>
          {approvedBuild == null || !build ? (
            <>
              <p className="small muted">{approvedBuild == null ? "No approved build yet. Build and approve your design in Robot studio to choose it here and export the manufacturer PDF." : "Loading the approved build..."}</p>
              {approvedBuild == null && openStudio && <div><button className="primary" onClick={openStudio}>Open Robot studio</button></div>}
            </>
          ) : (
            <>
              <p className="small muted">Approved build b{build.build}, design version {build.design_version}.</p>
              <dl className="facts">
                <dt>Family</dt><dd>{build.family}</dd>
                <dt>Structure</dt><dd>{STYLE[build.style.style] ?? build.style.style}</dd>
                <dt>Motors and electronics</dt><dd>{build.motors.length} motors, {build.electronics.length} electronics items</dd>
                <dt>Parts</dt><dd>{build.parts.length}</dd>
                <dt>Estimated purchase cost</dt>
                <dd>
                  {money && Object.keys(money.totals).length
                    ? Object.entries(money.totals).map(([cur, v]) => <div key={cur}><b>{cur} {fmt(v)}</b></div>)
                    : <span className="muted">no priced items</span>}
                  <div className="small muted">web estimates{money?.dates.length ? `, seen ${money.dates.join(", ")}` : ""}; {money?.defined ?? 0} item{money?.defined === 1 ? "" : "s"} defined spec, no price</div>
                </dd>
                {ownSaved && <><dt>Time saved</dt><dd title={ownSaved.title}>{ownSaved.text}</dd></>}
                <dt>Requirement checks</dt><dd>{bchecks ? `${bchecks.passed} of ${bchecks.passed + bchecks.none + bchecks.failed} passed` : ""}{bchecks && bchecks.failed > 0 ? `, ${bchecks.failed} failed` : ""}</dd>
              </dl>
              <div className="row">
                <button className="go big" disabled={busy} onClick={() => void decide({ action: "self" })}>{chosenSelf ? "Chosen: our own design" : "Choose our own design"}</button>
              </div>
              <div className="row">
                <a className="button" href={pdfUrl(pid, build.build)} download>{dl} Download manufacturer PDF</a>
                <a className="button" href={packageUrl(pid, build.build)} download>{dl} Download build package</a>
              </div>
            </>
          )}
        </section>
        <section className={`panel decision-card${d && !chosenSelf ? " chosen" : ""}`} aria-labelledby="buy-robot">
          <h3 id="buy-robot">Buy a robot</h3>
          {!analysed.length ? (
            <>
              <p className="small muted">No robot has been analysed yet. Search and analyse a catalog robot in Robot options to compare it here.</p>
              {openOptions && <div><button className="primary" onClick={openOptions}>Open Robot options</button></div>}
            </>
          ) : (
            <>
              <label className="field"><span>Analysed robot</span>
                <select value={pick} onChange={(e) => setPick(e.target.value)}>{analysed.map((id) => <option key={id} value={id}>{name(id)}</option>)}</select></label>
              <dl className="facts">
                <dt>Price</dt>
                <dd>{priceText ? <>{priceText} <span className="small muted">as of {cand?.price?.date}{cand?.price?.source_url ? <>, <a href={cand.price.source_url} target="_blank" rel="noreferrer">source</a></> : ""}</span></> : <span className="muted">no price found</span>}</dd>
                {candSaved && <><dt>Time saved</dt><dd title={candSaved.title}>{candSaved.text}</dd></>}
                <dt>Verified checks</dt>
                <dd>{candTally ? `${candTally.passed} passed, ${candTally.none} not published, ${candTally.failed} failed` : <span className="muted">loading the analysis...</span>}</dd>
              </dl>
              <div className="row">
                <button className="go big" disabled={busy || !pick} onClick={() => void decide({ candidate_id: pick, action: "approve" })}>Choose this robot</button>
              </div>
            </>
          )}
        </section>
      </div>
      <div className="panel sunk">
        <h3>Design package</h3>
        <p className="small muted">The generic export: the generated URDF and 3D model, the specification, requirements, simulation summary and the analysis.</p>
        <div><a className="button" href={`/api/v1/projects/${pid}/export`} download>{dl} Download design package</a></div>
      </div>
    </div>
  );
}
