import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { JobsProvider, useJobStore, useRunningJobs } from "./jobs";
import {
  Add, Breadcrumb, Complete, Delete, Dropdown, Locked, ProjectFolder, SettingsGear,
  StageAnalysis, StageBrief, StageDecision, StageDesign, StageOptions, StageQuestions, StageReference, StageStudio,
  type LucideIcon,
} from "./components/icons";
import { api } from "./api";
import { UsageCounter } from "./Usage";
import { AskBar, AskProvider, type AskNavigate } from "./components/AskBar";
import Analysis from "./stages/Analysis";
import Brief from "./stages/Brief";
import Decision from "./stages/Decision";
import Design from "./stages/Design";
import Options from "./stages/Options";
import Questions from "./stages/Questions";
import Reference from "./stages/Reference";
import Settings from "./stages/Settings";
import RobotStudio from "./stages/Studio";
import type { TrackedJob } from "@robode/studio-core";
import type { Project, ProjectSummary } from "./types";

type StageId = "brief" | "reference" | "questions" | "design" | "studio" | "options" | "analysis" | "decision" | "settings";
const STAGES: { id: Exclude<StageId, "settings">; title: string; icon: LucideIcon }[] = [
  { id: "brief", title: "Brief", icon: StageBrief },
  { id: "reference", title: "Reference", icon: StageReference },
  { id: "questions", title: "Questions", icon: StageQuestions },
  { id: "design", title: "Design", icon: StageDesign },
  { id: "studio", title: "Robot studio", icon: StageStudio },
  { id: "options", title: "Robot options", icon: StageOptions },
  { id: "analysis", title: "Analysis", icon: StageAnalysis },
  { id: "decision", title: "Decision", icon: StageDecision },
];

/** Which stages are open, and which are finished, from what the server says about the project. */
function stageState(p: Project | null, cid: string | null) {
  const designed = (p?.versions?.length ?? 0) > 0;
  const approved = p?.approved_version != null;
  const analysed = (p?.analyses?.length ?? 0) > 0;
  const open: Record<string, boolean> = {
    brief: true, reference: !!p, questions: !!p, design: designed, studio: approved, options: approved, analysis: approved && (analysed || !!cid), decision: analysed || p?.studio?.approved_build != null,
  };
  const done: Record<string, boolean> = {
    brief: !!p, reference: !!(p?.reference?.text || p?.reference?.files?.length), questions: designed, design: approved, studio: p?.studio?.approved_build != null, options: (p?.candidates_count ?? 0) > 0,
    analysis: analysed, decision: !!p?.decision,
  };
  return { open, done };
}

function startStage(p: Project): StageId {
  if (p.decision) return "decision";
  if (p.analyses?.length) return "analysis";
  if (p.approved_version != null) return "options";
  if (p.versions?.length) return "design";
  return "questions";
}

/** Where a job's result is shown. */
function jobStage(job: TrackedJob, hasVersions: boolean): StageId {
  if (job.kind === "revise" || job.kind === "ar_estimate" || job.kind === "scan_reconstruct" || job.kind === "apply_scene") return "design";
  if (job.kind === "search") return "options";
  if (job.kind === "studio_build" || job.kind === "studio_pdf") return "studio";
  if (job.kind === "analyse") return "analysis";
  const status = (job.result as { status?: string } | null)?.status;
  return status === "designed" || (status === undefined && hasVersions) ? "design" : "questions";
}
const JOB_NAMES: Record<string, string> = { design: "Design", revise: "Revision", search: "Robot search", analyse: "Analysis", studio_build: "Robot build", studio_pdf: "Build approval", ar_estimate: "Photo estimate", scan_reconstruct: "Site scan", apply_scene: "Site edits" };

function toastText(job: TrackedJob) {
  const name = JOB_NAMES[job.kind] ?? "Job";
  if (job.status === "error") return `${name} failed`;
  const version = (job.result as { version?: number } | null)?.version;
  if (job.kind === "apply_scene") return version ? `Site edits applied: v${version}` : "Site edits applied";
  if (job.kind === "design" || job.kind === "revise") return version ? `${name === "Revision" ? "Design" : name} finished: v${version}` : `${name} finished`;
  return `${name} finished`;
}

type Toast = { text: string; pid: string; stage: StageId; subject: string | null };

export default function App() {
  return <JobsProvider><AskProvider><Studio /></AskProvider></JobsProvider>;
}

function Studio() {
  const store = useJobStore();
  const running = useRunningJobs();
  const [briefNotice, setBriefNotice] = useState("");
  const [toast, setToast] = useState<Toast | null>(null);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [stage, setStage] = useState<StageId>("brief");
  const [cid, setCid] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [opening, setOpening] = useState(false);

  const stageList = useRef<HTMLOListElement>(null);
  useEffect(() => {
    window.scrollTo(0, 0);
    const list = stageList.current;
    const current = list?.querySelector('[aria-current="step"]');
    if (!list || !current || list.scrollWidth <= list.clientWidth) return;
    const viewport = list.getBoundingClientRect();
    const item = current.getBoundingClientRect();
    if (item.left < viewport.left || item.right > viewport.right) {
      list.scrollLeft += item.left - viewport.left - (viewport.width - item.width) / 2;
    }
  }, [stage, project?.id]);

  const loadList = useCallback(async () => {
    try { const r = await api.get<ProjectSummary[] | { projects: ProjectSummary[] }>("/projects"); setProjects(Array.isArray(r) ? r : r?.projects ?? []); setError(""); }
    catch (e) { setError((e as Error).message); }
  }, []);
  useEffect(() => { void loadList(); }, [loadList]);

  const load = useCallback(async (id: string) => {
    const p = await api.get<Project>(`/projects/${id}`);
    setProject(p);
    return p;
  }, []);
  const reload = useCallback(async () => { if (project) { await load(project.id); await loadList(); } }, [project, load, loadList]);

  // Jobs run on the server whatever the UI does: pick up the ones already running when the app starts.
  useEffect(() => { void store.adoptAllRunning().catch(() => undefined); }, [store]);
  const now = useRef({ project, stage });
  now.current = { project, stage };
  useEffect(() => store.onFinish((job) => {
    void loadList();
    const pid = job.robot_id;
    if (!pid) return;
    const current = now.current;
    const here = current.project?.id === pid;
    if (here) void load(pid).catch(() => undefined);
    const target = jobStage(job, here && (current.project?.versions?.length ?? 0) > 0);
    if (here && current.stage === target) { setToast(null); return; }
    setToast({ text: toastText(job), pid, stage: target, subject: job.subject });
  }), [store, load, loadList]);

  // The toast is moot once the user is looking at the stage it points to.
  useEffect(() => { if (toast && project?.id === toast.pid && stage === toast.stage) setToast(null); }, [toast, project?.id, stage]);

  async function open(id: string, target?: StageId, subject?: string | null) {
    setOpening(true);
    setBriefNotice("");
    try {
      void store.adopt(id).catch(() => undefined);
      const p = await load(id);
      setCid(subject ?? p.selected_candidate ?? p.analyses?.[0] ?? null); setStage(target ?? startStage(p)); setError("");
    }
    catch (e) { setError((e as Error).message); }
    finally { setOpening(false); }
  }
  async function created(id: string, notice = "") { await loadList(); try { await load(id); setCid(null); setBriefNotice(notice); setStage(notice ? "brief" : "reference"); } catch (e) { setError((e as Error).message); } }
  function newProject() { setProject(null); setCid(null); setStage("brief"); }
  async function remove() {
    if (!project || !window.confirm(`Delete the project "${project.title || project.id}" and all its versions?`)) return;
    try { await api.del(`/projects/${project.id}`); newProject(); await loadList(); } catch (e) { setError((e as Error).message); }
  }

  const { open: unlocked, done } = stageState(project, cid);
  const go = (s: StageId) => setStage(s);
  const workingProjects = new Set(running.map((j) => j.robot_id));
  const stageBusy = new Set(running.filter((j) => j.robot_id === project?.id).map((j) => jobStage(j, (project?.versions?.length ?? 0) > 0)));
  const projectTitle = (id: string | null) => { const p = projects.find((x) => x.id === id); return p ? p.title || p.id : id ?? ""; };
  const analyse = (id: string) => { setCid(id); setStage("analysis"); };

  const askNavigate = (n: AskNavigate) => {
    const s = n.stage as StageId;
    if (!STAGES.some((x) => x.id === s) && s !== "settings") return;
    if (s !== "settings" && !unlocked[s]) return;
    if (n.candidate) setCid(n.candidate);
    go(s);
  };

  let content;
  if (stage === "settings") content = <Settings />;
  else if (stage === "brief" || !project) content = <Brief project={project} onCreated={created} reload={reload} notice={briefNotice} next={() => go("reference")} />;
  else if (stage === "reference") content = <Reference project={project} reload={reload} next={() => go("questions")} />;
  else if (stage === "questions") content = <Questions project={project} reload={reload} designed={() => go("design")} />;
  else if (stage === "design") content = <Design project={project} reload={reload} approved={() => go("options")} />;
  else if (stage === "studio") content = <RobotStudio project={project} reload={reload} />;
  else if (stage === "options") content = <Options project={project} reload={reload} analyse={analyse} />;
  else if (stage === "analysis") content = <Analysis project={project} reload={reload} cid={cid} setCid={setCid} decide={() => go("decision")} />;
  else content = <Decision project={project} reload={reload} cid={cid} openStudio={() => go("studio")} openOptions={() => go("options")} />;

  return (
    <div className="shell">
      <a className="skip-link" href="#workspace-content">Skip to content</a>
      <aside className="rail" aria-label="Studio workspace">
        <div className="rail-head">
          <img className="studio-logo" src={`${import.meta.env.BASE_URL}robode-logo.svg`} alt="RoboDE Studio" width={208} height={66} />
        </div>

        <div className="rail-section">
          <label className="rail-label" htmlFor="project-pick">Project</label>
          <div className="project-picker">
            <ProjectFolder size={16} aria-hidden="true" />
            <select id="project-pick" value={project?.id ?? ""} disabled={opening} onChange={(e) => (e.target.value ? void open(e.target.value) : newProject())}>
              <option value="">{projects.length ? "Choose a project" : "No saved projects"}</option>
              {projects.map((p) => <option key={p.id} value={p.id}>{(p.title || p.id) + (workingProjects.has(p.id) ? " (working)" : "")}</option>)}
            </select>
            <Dropdown size={14} aria-hidden="true" />
          </div>
          <button className="new-project" disabled={opening} onClick={newProject}><Add size={17} aria-hidden="true" /> New project</button>
        </div>

        {running.some((j) => j.robot_id) && (
          <div className="rail-section running-jobs" role="status">
            <div className="rail-label">Running</div>
            <ul>
              {running.filter((j) => j.robot_id).map((j) => (
                <li key={j.id}>
                  <button className="rail-link" onClick={() => void open(j.robot_id!, jobStage(j, j.robot_id === project?.id && (project?.versions?.length ?? 0) > 0), j.kind === "analyse" ? j.subject : null)}>
                    <span className="job-badge" aria-hidden="true" />
                    <span className="job-label">{JOB_NAMES[j.kind] ?? j.kind}<span className="muted"> · {projectTitle(j.robot_id)}</span></span>
                  </button>
                </li>
              ))}
            </ul>
          </div>)}

        <nav className="workflow" aria-label="Design workflow">
          <div className="rail-label">Workflow</div>
          <ol className="stages" ref={stageList}>
            {STAGES.map(({ id, title, icon: Icon }) => (
              <li key={id}>
                <button disabled={!unlocked[id]} aria-current={stage === id ? "step" : undefined} onClick={() => go(id)}>
                  <Icon size={18} aria-hidden="true" />
                  <span className="step-name">{title}{id === "reference" && <span className="sr-only"> (optional)</span>}</span>
                  {stageBusy.has(id) ? <span className="step-status"><span className="job-badge" aria-hidden="true" /><span className="sr-only">Working</span></span>
                    : done[id] ? <span className="step-status complete"><Complete size={15} aria-hidden="true" /><span className="sr-only">Completed</span></span>
                    : !unlocked[id] && <span className="step-status"><Locked size={13} aria-hidden="true" /><span className="sr-only">Locked</span></span>}
                </button>
              </li>
            ))}
          </ol>
        </nav>

        <div className="rail-foot">
          <button className="rail-link" aria-current={stage === "settings" ? "page" : undefined} onClick={() => go("settings")}>
            <SettingsGear size={17} aria-hidden="true" /> Settings
          </button>
          {project && <button className="rail-link delete-project" disabled={opening} onClick={() => void remove()}><Delete size={16} aria-hidden="true" /> Delete project</button>}
          <div className="rail-status">
            <span className="local-status"><span aria-hidden="true" /> Runs on this computer</span>
            <UsageCounter />
          </div>
        </div>
      </aside>

      <div className="workspace">
        <header className="workspace-bar">
          <div className="breadcrumbs">
            <span>Workspace</span>
            <Breadcrumb size={15} aria-hidden="true" />
            <span title={project?.title || undefined}>{stage === "settings" ? "Settings" : project ? project.title || project.id : "New project"}</span>
          </div>
        </header>
        <main id="workspace-content" className="main" tabIndex={-1} aria-busy={opening}>
          {toast && (
            <div className="toast" role="status">
              <span>{toast.text}</span>
              <button className="primary" onClick={() => { const t = toast; setToast(null); if (project?.id === t.pid) { if (t.subject) setCid(t.subject); go(t.stage); } else void open(t.pid, t.stage, t.subject); }}>Open</button>
              <button onClick={() => setToast(null)} aria-label="Dismiss">Dismiss</button>
            </div>)}
          {error && <div className="workspace-error"><div className="notice error" role="alert">{error}</div></div>}
          <Fragment key={project?.id ?? "new"}>{content}</Fragment>
        </main>
      </div>
      <AskBar projectId={project?.id ?? null} stage={stage} candidate={cid} reload={reload} navigate={askNavigate} />
    </div>
  );
}
