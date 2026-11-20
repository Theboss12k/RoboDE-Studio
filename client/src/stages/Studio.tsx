// Robot studio: build a manufacturable robot from the approved design (motors and electronics, CAD, digital twin),
// change it in words, and approve it for the manufacturer PDF and build package.
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { Dictation, JobStatus, PassChip, StageHead, fmt } from "../components/common";
import { Complete } from "../components/icons";
import BuildTabs from "../components/studio/BuildTabs";
import Downloads from "../components/studio/Downloads";
import { ElectronicsTable, MotorTable } from "../components/studio/MotorTable";
import PartList from "../components/studio/PartList";
import TwinPanel, { type TwinHandle } from "../components/twin/TwinPanel";
import { useProjectJob } from "../jobs";
import type { Check, DesignVersion, Project, StudioBuildRequest, StudioBuildSummary, StudioBuildView } from "../types";

const VIEWS = ["iso", "front", "side", "top"] as const;

export default function Studio({ project, reload }: { project: Project; reload: () => Promise<void> }) {
  const pid = project.id;
  const base = `/projects/${pid}/studio/builds`;
  const [list, setList] = useState<StudioBuildSummary[]>([]);
  const [current, setCurrent] = useState<number | undefined>(undefined);
  const [builds, setBuilds] = useState<Record<number, StudioBuildView>>({});
  const [versions, setVersions] = useState<Record<number, DesignVersion>>({});
  const [loadError, setLoadError] = useState("");
  const [request, setRequest] = useState("");
  const [approving, setApproving] = useState(false);
  const [approveError, setApproveError] = useState("");
  const twin = useRef<TwinHandle>(null);

  const refresh = useCallback(async (select?: number) => {
    try {
      const l = await api.get<StudioBuildSummary[]>(base);
      setList(l);
      setCurrent((c) => select ?? (c !== undefined && l.some((b) => b.build === c) ? c : l[l.length - 1]?.build));
      setLoadError("");
    } catch (e) { setLoadError((e as Error).message); }
  }, [base]);
  useEffect(() => { void refresh(); }, [refresh, project.studio?.builds.join(",")]); // eslint-disable-line react-hooks/exhaustive-deps

  const loadBuild = useCallback(async (n: number) => {
    const b = await api.get<StudioBuildView>(`${base}/${n}`);
    setBuilds((c) => ({ ...c, [n]: b }));
    return b;
  }, [base]);
  const summary = list.find((b) => b.build === current);
  const b = current !== undefined ? builds[current] : undefined;
  useEffect(() => {
    if (current === undefined || (builds[current] && builds[current].approved === !!summary?.approved)) return;
    loadBuild(current).then(() => setLoadError("")).catch((e) => setLoadError(e.message));
  }, [current, summary?.approved]); // eslint-disable-line react-hooks/exhaustive-deps
  const dv = b?.design_version;
  useEffect(() => {
    if (dv === undefined || versions[dv]) return;
    api.get<DesignVersion>(`/projects/${pid}/versions/${dv}`).then((v) => setVersions((c) => ({ ...c, [dv]: v }))).catch((e) => setLoadError(e.message));
  }, [dv, pid]); // eslint-disable-line react-hooks/exhaustive-deps
  const version = dv !== undefined ? versions[dv] : undefined;

  const finished = async (r: { build: number }) => { setRequest(""); await reload(); await refresh(r.build); await loadBuild(r.build).catch(() => undefined); };
  const build = useProjectJob<{ build: number }>(pid, "studio_build", null, finished);
  const pdf = useProjectJob<{ build: number }>(pid, "studio_pdf", current !== undefined ? `b${current}` : null, async (r) => { await loadBuild(r.build); await reload(); await refresh(r.build); });
  const working = build.busy || approving || pdf.busy;
  const frozen = !!b?.approved;
  const hasApproved = project.approved_version != null;

  async function start(body: StudioBuildRequest) {
    const r = await build.run<{ build: number }>(() => api.post(base, body));
    if (r) await finished(r);
  }
  const pin = (joint: string, id: string) => {
    if (!b) return;
    const pinned = Object.fromEntries(b.motors.filter((m) => m.pinned && m.alternative_id).map((m) => [m.joint, m.alternative_id!]));
    void start({ base_build: b.build, pinned: { ...pinned, [joint]: id } });
  };

  async function approve() {
    if (!b || !twin.current) return;
    setApproving(true); setApproveError("");
    try {
      for (const view of VIEWS) {
        const blob = await twin.current.capture(view);
        await api.upload(`${base}/${b.build}/snapshots`, new File([blob], `${view}.png`, { type: "image/png" }), { view });
      }
      await twin.current.capture("iso").catch(() => undefined);  // leave the camera as the user had it
      const r = await pdf.run<{ build: number }>(() => api.post(`${base}/${b.build}/approve`, {}), `b${b.build}`);
      if (r) { await loadBuild(r.build); await reload(); await refresh(r.build); }
    } catch (e) { setApproveError((e as Error).message); }
    finally { setApproving(false); }
  }

  const groups: { title: string; checks: Check[] }[] = b ? [
    { title: "Build", checks: b.checks },
    { title: "Structure style", checks: b.style.checks },
    { title: "Motor sizing", checks: b.motors.flatMap((m) => m.checks) },
    { title: "Electronics", checks: b.electronics.flatMap((e) => e.checks) },
  ] : [];

  return (
    <div className="page wide">
      <StageHead artifact="design" step="Robot studio" title="Robot studio">Turn the approved design into a manufacturable robot: sized motors and electronics from real shops, CAD parts, and a digital twin you can move. Every change makes a new build and keeps the old one.</StageHead>
      {!hasApproved && <div className="notice warning">Approve a design version first; Robot studio builds the approved version.</div>}
      <div className="panel stack">
        <div className="row">
          <button className="go big" disabled={!hasApproved || working || build.blocked} onClick={() => void start({})}>{list.length ? "Build again from the approved design" : "Build this robot"}</button>
          {list.length === 0 && <span className="small muted">Sizes the joints, searches the shops, verifies motor specs, chooses the structure style, generates the CAD, assembles the URDF and checks.</span>}
        </div>
        <JobStatus busy={build.busy} lines={build.lines} error={build.error} label="Building" />
      </div>
      {list.length > 0 && <BuildTabs builds={list} current={current} onSelect={setCurrent} />}
      {loadError && <div className="notice error" role="alert">{loadError}</div>}
      {current !== undefined && !b && !loadError && <div className="busy">Loading build</div>}
      {b && (
        <>
          <div className="notice small">
            <b>b{b.build}</b> from design v{b.design_version}{b.member ? `, member ${b.member}` : ""}, {b.family.replace(/_/g, " ")}. {b.summary}
            {b.request && <><br /><b>Change requested:</b> {b.request}</>}
            {frozen && <><br /><Complete size={14} aria-hidden="true" style={{ verticalAlign: "-2px" }} /> <b>Approved</b>{b.approved_at ? ` on ${b.approved_at.slice(0, 10)}` : ""} and frozen. Make a new build from another tab to change anything.</>}
          </div>

          <section className="panel stack" aria-labelledby="motors-h">
            <h3 id="motors-h">Motors and electronics</h3>
            <p className="small muted">Prices are web estimates as listed on the date shown, not quotes. "verified" values are quoted on the shop page and checked in code; "assumed" come from standard tables; "defined" are specs to procure to. Choosing an alternative repeats only the CAD step, with no new shop search.</p>
            <MotorTable motors={b.motors} sizing={b.sizing} frozen={frozen || working} onPin={pin} />
            <h4>Driver, controller and power supply</h4>
            <ElectronicsTable items={b.electronics} />
            <p className="small"><b>Structure style:</b> {b.style.style.replace(/_/g, " ")}{b.style.forced && " (forced by a failed check)"}. {b.style.reasons.join(" ")}</p>
          </section>

          <section className="panel stack" aria-labelledby="cad-h">
            <h3 id="cad-h">CAD and digital twin</h3>
            {version
              ? <TwinPanel ref={twin} pid={pid} build={b} scene={version.scene} robotBase={version.robot.base ?? version.scene.robot_base} />
              : <div className="busy">Loading the design scene</div>}
            <h4>Parts</h4>
            <PartList pid={pid} build={b.build} parts={b.parts} />
            <h4>Downloads</h4>
            <Downloads pid={pid} build={b} />
          </section>

          <section className="panel stack" aria-labelledby="checks-h">
            <h3 id="checks-h">Checks</h3>
            <div className="table-wrap">
              <table>
                <caption className="sr-only">Sizing, style and CAD checks of build b{b.build}</caption>
                <thead><tr><th>Group</th><th>Check</th><th>Required</th><th>Actual</th><th>Result</th></tr></thead>
                <tbody>
                  {groups.flatMap((g) => g.checks.map((c, i) => (
                    <tr key={`${g.title}-${c.requirement_id}-${i}`}>
                      <td className="small muted">{g.title}</td>
                      <td>{c.name}{c.detail && <div className="small muted">{c.detail}</div>}</td>
                      <td className="mono">{c.required == null ? "–" : fmt(c.required, c.unit)}</td><td className="mono">{c.actual == null ? "–" : fmt(c.actual, c.unit)}</td>
                      <td><PassChip passed={c.passed} detail={c.detail} /></td>
                    </tr>)))}
                </tbody>
              </table>
            </div>
            <p className="small muted">Kinematic and static-plus-acceleration sizing only: no finite-element analysis, no dynamics simulation, and not tested on hardware. Prices are dated web estimates, never quotes.</p>
          </section>

          <section className="panel stack" aria-labelledby="change-h">
            <h3 id="change-h">Change the build</h3>
            <Dictation label="Change request" rows={3} value={request} onChange={setRequest} disabled={frozen || working}
              placeholder="e.g. Use machined aluminium, or choose cheaper motors." />
            <div className="row">
              <button className="primary" disabled={frozen || working || build.blocked || request.trim().length < 3} onClick={() => void start({ base_build: b.build, request: request.trim() })}>Apply change</button>
              <button className="go" disabled={frozen || working || !version} onClick={() => void approve()}>Approve this build</button>
            </div>
            <p className="small muted">Approving takes four pictures of the twin (iso, front, side, top), freezes the build and makes the manufacturer PDF and the package.</p>
            <JobStatus busy={pdf.busy || approving} lines={pdf.lines} error={approveError || pdf.error} label="Approving" />
          </section>
        </>
      )}
    </div>
  );
}
