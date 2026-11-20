import { useEffect, useState } from "react";
import { Download } from "lucide-react";
import { api } from "../api";
import { StageHead } from "../components/common";
import type { Candidate, Project } from "../types";

export default function Decision({ project, reload, cid }: { project: Project; reload: () => Promise<void>; cid: string | null }) {
  const analysed = project.analyses ?? [];
  const [pick, setPick] = useState(cid && analysed.includes(cid) ? cid : analysed[0] ?? "");
  const [cands, setCands] = useState<Candidate[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.get<Candidate[] | { candidates: Candidate[] }>(`/projects/${project.id}/candidates`).then((r) => setCands(Array.isArray(r) ? r : r?.candidates ?? [])).catch(() => undefined); }, [project.id]);
  const name = (id: string) => { const c = cands.find((x) => x.id === id); return c ? `${c.manufacturer} ${c.model}` : id; };
  async function decide(action: "approve" | "robot_studio") {
    setBusy(true); setError("");
    try { await api.post(`/projects/${project.id}/decision`, { candidate_id: pick, action }); await reload(); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const d = project.decision;
  return (
    <div className="page narrow">
      <StageHead step="Step 7 of 7" title="Decision">Choose the robot to go forward with, based on the analysis. Prices and specifications are web estimates; confirm them with the manufacturer before buying.</StageHead>
      {d && <div className="notice ok" role="status">Decision recorded: {d.action === "approve" ? "approved" : "opened in Robot studio"} for {name(d.candidate_id)}.</div>}
      <div className="panel">
        <label className="field"><span>Analysed robot</span>
          <select value={pick} onChange={(e) => setPick(e.target.value)}>{analysed.map((id) => <option key={id} value={id}>{name(id)}</option>)}</select></label>
        {!analysed.length && <div className="notice">Analyse at least one robot first.</div>}
        <div className="row">
          <button className="go big" disabled={busy || !pick} onClick={() => void decide("approve")}>Approve this robot</button>
          <button className="primary big" disabled={busy || !pick} onClick={() => void decide("robot_studio")}>Open in Robot studio</button>
        </div>
        {error && <div className="notice error" role="alert">{error}</div>}
      </div>
      <div className="panel sunk">
        <h3>Robot studio is coming later</h3>
        <p className="small muted">Robot studio will take the chosen robot through control set-up and testing. For now, download the design package: the generated URDF and 3D model, the specification, requirements, simulation summary and the analysis.</p>
        <div><a className="button" href={`/api/projects/${project.id}/export`} download><Download size={15} aria-hidden="true" style={{ verticalAlign: "-2px" }} /> Download design package</a></div>
      </div>
    </div>
  );
}
