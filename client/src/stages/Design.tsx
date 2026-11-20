import { useEffect, useState } from "react";
import { api } from "../api";
import { Dictation, JobStatus, StageHead, useJob } from "../components/common";
import Player from "../components/Player";
import Requirements from "../components/Requirements";
import { ConceptPanel, SpecSummary } from "../components/SpecPanels";
import type { DesignVersion, Project } from "../types";

export default function Design({ project, reload, approved }: { project: Project; reload: () => Promise<void>; approved: () => void }) {
  const versions = project.versions ?? [];
  const latest = versions[versions.length - 1];
  const [current, setCurrent] = useState<number | undefined>(latest);
  const [cache, setCache] = useState<Record<number, DesignVersion>>({});
  const [loadError, setLoadError] = useState("");
  const [request, setRequest] = useState("");
  const revise = useJob();
  const approve = useJob();
  useEffect(() => { if (current === undefined || !versions.includes(current)) setCurrent(latest); }, [latest]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (current === undefined || cache[current]) return;
    api.get<DesignVersion>(`/projects/${project.id}/versions/${current}`)
      .then((v) => { setCache((c) => ({ ...c, [current]: v })); setLoadError(""); }).catch((e) => setLoadError(e.message));
  }, [current, project.id, cache]);
  const v = current !== undefined ? cache[current] : undefined;
  const frozen = project.approved_version != null;

  async function submitRevision() {
    const result = await revise.run<{ version: number }>(() => api.post(`/projects/${project.id}/revise`, { request: request.trim() }));
    if (!result) return;
    setRequest(""); setCurrent(result.version); await reload();
  }
  async function doApprove() {
    if (!v) return;
    try { await api.post(`/projects/${project.id}/approve`, { version: v.version }); await reload(); approved(); }
    catch (e) { approve.setError((e as Error).message); }
  }

  return (
    <div className="page wide">
      <StageHead step="Step 4 of 7" title="Design">A generated robot, sized from physics, simulated on your task. Change anything in plain words; every change makes a new version and keeps the old one.</StageHead>
      <div className="tabs" role="tablist" aria-label="Design versions">
        {versions.map((n) => (
          <button key={n} role="tab" aria-selected={current === n} onClick={() => setCurrent(n)}>v{n}{project.approved_version === n ? " ✓" : ""}</button>))}
      </div>
      {loadError && <div className="notice error" role="alert">{loadError}</div>}
      {!v && !loadError && <div className="busy">Loading version</div>}
      {v && (
        <>
          {v.change_request && <div className="notice small"><b>Change requested:</b> {v.change_request}{v.change_summary && <><br /><b>What changed:</b> {v.change_summary}</>}</div>}
          <div className="design-grid">
            <div className="panel">
              <div className="panel-head"><h3>Simulated task</h3><span className={`tag ${v.sim.ok ? "ok" : "bad"}`}>{v.sim.ok ? "All checks pass" : "Some checks fail"}</span></div>
              <Player scene={v.scene} parsed={v.parsed} sim={v.sim} robotBase={v.robot.base ?? v.scene.robot_base} />
              {v.scene.notes && <p className="small muted">{v.scene.notes}</p>}
            </div>
            <div className="panel"><h3>Requirements</h3><Requirements requirements={v.requirements} checks={v.sim.checks} /></div>
          </div>
          <div className="grid-2"><SpecSummary spec={v.spec} /><ConceptPanel robot={v.robot} /></div>
          <div className="panel">
            <h3>Suggest a change</h3>
            {frozen && <div className="notice ok small">Version {project.approved_version} is approved and frozen. Changes are disabled.</div>}
            <Dictation label="Change request" rows={3} value={request} onChange={setRequest} disabled={frozen || revise.busy}
              placeholder="e.g. Make the table 20 cm higher, or use a gantry instead of an arm." />
            <div className="row">
              <button className="primary" disabled={frozen || revise.busy || request.trim().length < 3} onClick={() => void submitRevision()}>Apply change</button>
              <button className="go" disabled={frozen || approve.busy || !v.sim} onClick={() => void doApprove()}>Approve this version</button>
              {frozen && <button onClick={approved}>Go to robot options</button>}
            </div>
            <JobStatus {...revise} label="Revising" />
            {approve.error && <div className="notice error" role="alert">{approve.error}</div>}
          </div>
        </>
      )}
    </div>
  );
}
