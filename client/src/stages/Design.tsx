import { useEffect, useRef, useState } from "react";
import { useAskRequest, useAskSelection } from "../components/AskBar";
import { api } from "../api";
import { Dictation, JobStatus, StageHead, useJob } from "../components/common";
import { useProjectJob } from "../jobs";
import { Complete, Fail, Pass } from "../components/icons";
import Player, { type PlayerHandle } from "../components/Player";
import FleetAssignmentEditor from "../components/fleet/FleetAssignmentEditor";
import FleetBanner from "../components/fleet/FleetBanner";
import FleetLimitsForm from "../components/fleet/FleetLimitsForm";
import FleetPanel from "../components/fleet/FleetPanel";
import type { FleetOverlay } from "../components/SceneView";
import RepairLog from "../components/RepairLog";
import Requirements from "../components/Requirements";
import { ConceptPanel, SpecSummary } from "../components/SpecPanels";
import TimeSaved from "../components/TimeSaved";
import type { DesignVersion, Project, ReviseRequest } from "../types";

export default function Design({ project, reload, approved }: { project: Project; reload: () => Promise<void>; approved: () => void }) {
  const versions = project.versions ?? [];
  const latest = versions[versions.length - 1];
  const [current, setCurrent] = useState<number | undefined>(latest);
  const [cache, setCache] = useState<Record<number, DesignVersion>>({});
  const [loadError, setLoadError] = useState("");
  const [request, setRequest] = useState("");
  const [overlay, setOverlay] = useState<FleetOverlay | null>(null);
  // A revision that finished while this stage was away: show the new version once.
  const revise = useProjectJob<{ version: number }>(project.id, "revise", undefined, async (r) => { setRequest(""); setCurrent(r.version); await reload(); });
  const approve = useJob();
  const [autoRepair, setAutoRepair] = useState<boolean>(() => { try { return localStorage.getItem("studio.autoRepair") !== "0"; } catch { return true; } });
  function changeAutoRepair(on: boolean) { setAutoRepair(on); try { localStorage.setItem("studio.autoRepair", on ? "1" : "0"); } catch { /* not remembered */ } }
  useEffect(() => { if (current === undefined || !versions.includes(current)) setCurrent(latest); }, [latest]); // eslint-disable-line react-hooks/exhaustive-deps
  const player = useRef<PlayerHandle>(null);
  // Load the viewed version first, then the rest (the tabs show each version's parent).
  useEffect(() => {
    const wanted = [current, ...versions].filter((n): n is number => n !== undefined && !cache[n]);
    for (const n of new Set(wanted)) {
      api.get<DesignVersion>(`/projects/${project.id}/versions/${n}`)
        .then((v) => { setCache((c) => c[n] ? c : { ...c, [n]: v }); if (n === current) setLoadError(""); })
        .catch((e) => { if (n === current) setLoadError(e.message); });
    }
  }, [current, project.id, versions.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps
  const v = current !== undefined ? cache[current] : undefined;
  useAskSelection({ version: current });
  useAskRequest((n) => { if (n.version != null && versions.includes(n.version)) setCurrent(n.version); });
  const frozen = project.approved_version != null;
  const fleet = v?.fleet ?? null;
  const fleetOn = !!project.fleet?.enabled;
  const stepRunning = revise.busy || revise.blocked;

  async function submitRevision() {
    if (!v) return;
    const result = await revise.run<{ version: number }>(() => api.post(`/projects/${project.id}/revise`, {
      request: request.trim(), base_version: v.version, t: player.current?.getMoment().t ?? 0, auto_repair: autoRepair,
    } satisfies ReviseRequest), `v${v.version}`);
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
      <StageHead artifact="design" step="Step 4 of 7" title="Design">A generated robot, sized from physics, simulated on your task. Change anything in plain words; every change makes a new version and keeps the old one.</StageHead>
      <div className="tabs" role="tablist" aria-label="Design versions">
        {versions.map((n) => {
          const parent = cache[n]?.parent_version;
          return (
            <button key={n} role="tab" aria-selected={current === n} onClick={() => setCurrent(n)}>v{n}
              {parent != null && <span className="small muted"> based on v{parent}</span>}
              {project.approved_version === n && <><Complete size={14} aria-hidden="true" /><span className="sr-only"> (approved)</span></>}</button>);
        })}
      </div>
      <FleetBanner project={project} reload={reload} disabled={frozen || stepRunning} />
      {loadError && <div className="notice error" role="alert">{loadError}</div>}
      {!v && !loadError && <div className="busy">Loading version</div>}
      {v && (
        <>
          {v.change_request && <div className="notice small"><b>Change requested:</b> {v.change_request}{v.change_summary && <><br /><b>What changed:</b> {v.change_summary}</>}</div>}
          {(v.repair ?? null) && <RepairLog log={v.repair!} />}
          <div className="design-grid">
            <div className="panel">
              <div className="panel-head"><h3>{fleet ? "Simulated task (all robots)" : "Simulated task"}</h3><span className={`tag ${(fleet ? fleet.ok : v.sim.ok) ? "ok" : "bad"}`}>{(fleet ? fleet.ok : v.sim.ok) ? <Pass size={14} aria-hidden="true" /> : <Fail size={14} aria-hidden="true" />}{(fleet ? fleet.ok : v.sim.ok) ? "All checks pass" : "Some checks fail"}</span></div>
              <Player ref={player} scene={v.scene} parsed={v.parsed} sim={v.sim} robotBase={v.robot.base ?? v.scene.robot_base}
                fleet={fleet && v.fleet_parsed ? { plan: fleet, parsed: v.fleet_parsed } : undefined} overlay={fleet ? undefined : overlay ?? undefined} />
              {fleet && !v.fleet_parsed && <div className="notice warning small">The fleet's robot models are not available, so only the primary robot is drawn.</div>}
              {v.scene.notes && <p className="small muted">{v.scene.notes}</p>}
            </div>
            <div className="panel"><h3>{fleet ? `Requirements (primary robot ${fleet.primary})` : "Requirements"}</h3><Requirements requirements={v.requirements} checks={v.sim.checks} /></div>
          </div>
          <TimeSaved project={project} version={v} reload={reload} />
          {fleet && <FleetPanel project={project} version={v} reload={reload} />}
          {fleet && !frozen && (
            <details className="panel"><summary>Change the assignment and plan the fleet again</summary>
              <FleetAssignmentEditor key={v.version} project={project} reload={reload} baseVersion={v.version} actions={fleet.task.actions} initial={fleet.assignment}
                onPreview={setOverlay} onPlanned={setCurrent} /></details>
          )}
          {!fleet && fleetOn && (
            <>
              <FleetLimitsForm project={project} reload={reload} disabled={frozen || stepRunning} />
              <FleetAssignmentEditor key={v.version} project={project} reload={reload} baseVersion={v.version} actions={v.spec.actions}
                onPreview={setOverlay} onPlanned={setCurrent} frozen={frozen} />
            </>
          )}
          <div className="grid-2"><SpecSummary spec={v.spec} /><ConceptPanel robot={v.robot} /></div>
          <div className="panel">
            <h3>Suggest a change</h3>
            {frozen && <div className="notice ok small">Version {project.approved_version} is approved and frozen. Changes are disabled.</div>}
            {v.version !== latest && <div className="notice small">Change will be based on v{v.version}</div>}
            <Dictation label="Change request" rows={3} value={request} onChange={setRequest} disabled={frozen || revise.busy || revise.blocked}
              placeholder="e.g. Make the table 20 cm higher, or use a gantry instead of an arm." />
            <label className="check-line"><input type="checkbox" checked={autoRepair} onChange={(e) => changeAutoRepair(e.target.checked)} disabled={frozen || revise.busy || revise.blocked} /> Fix collisions automatically</label>
            <div className="row">
              <button className="primary" disabled={frozen || revise.busy || revise.blocked || request.trim().length < 3} onClick={() => void submitRevision()}>Apply change</button>
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
