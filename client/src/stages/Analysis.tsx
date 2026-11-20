import { useEffect, useState } from "react";
import { api } from "../api";
import { JobStatus, StageHead, fmt, useJob } from "../components/common";
import Player from "../components/Player";
import Requirements from "../components/Requirements";
import { ConceptPanel } from "../components/SpecPanels";
import type { Analysis as AnalysisT, Candidate, DesignVersion, Difference, ManufacturerSpec, Project } from "../types";

const SEVERITY: Record<Difference["severity"], string> = { ok: "ok", minor: "", major: "warn", blocking: "bad" };

function Differences({ list }: { list: Difference[] }) {
  if (!list.length) return <p className="muted">No differences were reported.</p>;
  return (
    <div className="table-wrap"><table>
      <thead><tr><th>Topic</th><th>Generated robot</th><th>This robot</th><th>Impact</th><th>Severity</th></tr></thead>
      <tbody>{list.map((d, i) => (
        <tr key={i}><td>{d.topic}</td><td>{d.generated}</td><td>{d.candidate}</td><td className="small">{d.impact || "–"}</td><td><span className={`tag ${SEVERITY[d.severity]}`}>{d.severity}</span></td></tr>))}</tbody>
    </table></div>
  );
}

function ManufacturerValues({ spec, unverified }: { spec: ManufacturerSpec; unverified: string[] }) {
  return (
    <div className="panel">
      <h3>Manufacturer values</h3>
      <p className="small muted">From <a href={spec.document_url} target="_blank" rel="noreferrer noopener">{spec.document_title || spec.document_url}</a>.
        A value is verified only when its quote is found in the document and contains the number. <b>Unverified values are shown but excluded from pass/fail.</b></p>
      <div className="table-wrap"><table>
        <thead><tr><th>Value</th><th>Quote from the document</th><th>Status</th></tr></thead>
        <tbody>{(spec.values ?? []).map((v) => (
          <tr key={v.key} className={v.verified ? "" : "unverified"}>
            <td><code>{v.key}</code><div><b>{fmt(v.value, v.unit)}</b></div></td>
            <td><div className="quote">{v.quote}{v.page && <span> (page {v.page})</span>}</div></td>
            <td><span className={`tag ${v.verified ? "ok" : "warn"}`}>{v.verified ? "verified" : "unverified"}</span></td></tr>))}</tbody>
      </table></div>
      {spec.kinematics_note && <p className="small">{spec.kinematics_note}</p>}
      {unverified.length > 0 && <div className="notice warning small"><b>Estimated, not from the manufacturer:</b> {unverified.join(", ")}. These were scaled from the generated robot.</div>}
    </div>
  );
}

export default function Analysis({ project, reload, cid, setCid, decide }: {
  project: Project; reload: () => Promise<void>; cid: string | null; setCid: (id: string) => void; decide: () => void;
}) {
  const [cands, setCands] = useState<Candidate[]>([]);
  const [version, setVersion] = useState<DesignVersion | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisT | null>(null);
  const [url, setUrl] = useState("");
  const [uploading, setUploading] = useState("");
  const job = useJob();
  const analysed = project.analyses ?? [];
  const vNum = project.approved_version ?? project.versions?.[project.versions.length - 1];
  const cand = cands.find((c) => c.id === cid);
  const needsUpload = /download|datasheet|fetch|unsafe|unreachable/i.test(job.error);

  useEffect(() => {
    api.get<Candidate[] | { candidates: Candidate[] }>(`/projects/${project.id}/candidates`).then((r) => setCands(Array.isArray(r) ? r : r?.candidates ?? [])).catch(() => undefined);
  }, [project.id]);
  useEffect(() => {
    if (vNum === undefined) return;
    api.get<DesignVersion>(`/projects/${project.id}/versions/${vNum}`).then(setVersion).catch(() => undefined);
  }, [project.id, vNum]);
  useEffect(() => {
    setAnalysis(null);
    if (cid && analysed.includes(cid)) api.get<AnalysisT>(`/projects/${project.id}/analysis/${cid}`).then(setAnalysis).catch((e) => job.setError(e.message));
  }, [cid, project.id, analysed.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

  async function run() {
    if (!cid) return;
    const r = await job.run<AnalysisT>(() => api.post(`/projects/${project.id}/candidates/${cid}/analyse`, url.trim() ? { datasheet_url: url.trim() } : {}));
    if (r) { await reload(); setAnalysis(r.parsed ? r : await api.get<AnalysisT>(`/projects/${project.id}/analysis/${cid}`)); }
  }
  async function upload(file: File) {
    if (!cid) return;
    setUploading("Uploading…");
    try { await api.upload(`/projects/${project.id}/candidates/${cid}/datasheet`, file); setUploading(""); job.setError(""); await run(); }
    catch (e) { setUploading((e as Error).message); }
  }

  return (
    <div className="page wide">
      <StageHead step="Step 6 of 7" title="Analysis">
        The real robot's published values replace the generated ones, and the same task is simulated again. Kinematic and static-load checks, not dynamics or hardware tests.
      </StageHead>
      <div className="tabs" role="tablist" aria-label="Robots to analyse">
        {[...new Set([...analysed, ...(cid ? [cid] : [])])].map((id) => {
          const c = cands.find((x) => x.id === id);
          return <button key={id} role="tab" aria-selected={cid === id} onClick={() => setCid(id)}>{c ? `${c.manufacturer} ${c.model}` : id}</button>;
        })}
      </div>
      {!cid && <div className="notice">Choose a robot on the Robot options page and press Analyse.</div>}
      {cid && (
        <div className="panel">
          <h3>{cand ? `${cand.manufacturer} ${cand.model}` : cid}</h3>
          <label className="field"><span>Datasheet URL (optional; the search result is used if left empty)</span>
            <input type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder={cand?.datasheet_url || "https://…/datasheet.pdf"} /></label>
          <div className="row"><button className="go" disabled={job.busy} onClick={() => void run()}>{analysis ? "Run the analysis again" : "Run analysis"}</button></div>
          <JobStatus {...job} label="Analysing" />
          {needsUpload && !job.busy && (
            <div className="notice warning">
              <p>The datasheet could not be downloaded. If you have it as a PDF or text file, upload it and the analysis will run again.</p>
              <label className="button">Upload datasheet<input type="file" accept=".pdf,.txt,.html,.htm,application/pdf,text/*" className="sr-only" onChange={(e) => { const f = e.target.files?.[0]; if (f) void upload(f); e.target.value = ""; }} /></label>
              {uploading && <span className="small"> {uploading}</span>}
            </div>)}
        </div>
      )}
      {cid && analysis && version && (
        <>
          <div className="design-grid">
            <div className="panel"><h3>The same task with this robot</h3>
              <Player scene={version.scene} parsed={analysis.parsed} sim={analysis.sim} robotBase={analysis.robot.base ?? version.scene.robot_base} /></div>
            <div className="panel"><h3>Requirements</h3><Requirements requirements={version.requirements} checks={analysis.sim.checks} caption="Requirements and this robot's values" /></div>
          </div>
          <div className="panel"><h3>Differences from the generated robot</h3><Differences list={analysis.differences} /></div>
          <div className="grid-2"><ManufacturerValues spec={analysis.manufacturer_spec} unverified={analysis.unverified ?? []} /><ConceptPanel robot={analysis.robot} title="Model built from the datasheet" /></div>
          <div className="row"><button className="go big" onClick={decide}>Continue to decision</button></div>
        </>
      )}
    </div>
  );
}
