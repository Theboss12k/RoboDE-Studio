import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import {
  Activity, Bot, Boxes, Check, ChevronDown, ChevronRight, CircleCheck, ClipboardList,
  FileBox, FolderOpen, Lock, MessageSquare, Plus, Settings as SettingsIcon, Trash2, Workflow,
  type LucideIcon,
} from "lucide-react";
import { api } from "./api";
import { UsageCounter } from "./Usage";
import Analysis from "./stages/Analysis";
import Brief from "./stages/Brief";
import Decision from "./stages/Decision";
import Design from "./stages/Design";
import Options from "./stages/Options";
import Questions from "./stages/Questions";
import Reference from "./stages/Reference";
import Settings from "./stages/Settings";
import type { Project, ProjectSummary } from "./types";

type StageId = "brief" | "reference" | "questions" | "design" | "options" | "analysis" | "decision" | "settings";
const STAGES: { id: Exclude<StageId, "settings">; title: string; icon: LucideIcon }[] = [
  { id: "brief", title: "Brief", icon: ClipboardList },
  { id: "reference", title: "Reference", icon: FileBox },
  { id: "questions", title: "Questions", icon: MessageSquare },
  { id: "design", title: "Design", icon: Workflow },
  { id: "options", title: "Robot options", icon: Boxes },
  { id: "analysis", title: "Analysis", icon: Activity },
  { id: "decision", title: "Decision", icon: CircleCheck },
];

/** Which stages are open, and which are finished, from what the server says about the project. */
function stageState(p: Project | null, cid: string | null) {
  const designed = (p?.versions?.length ?? 0) > 0;
  const approved = p?.approved_version != null;
  const analysed = (p?.analyses?.length ?? 0) > 0;
  const open: Record<string, boolean> = {
    brief: true, reference: !!p, questions: !!p, design: designed, options: approved, analysis: approved && (analysed || !!cid), decision: analysed,
  };
  const done: Record<string, boolean> = {
    brief: !!p, reference: !!(p?.reference?.text || p?.reference?.files?.length), questions: designed, design: approved, options: (p?.candidates_count ?? 0) > 0,
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

export default function App() {
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

  async function open(id: string) {
    setOpening(true);
    try { const p = await load(id); setCid(p.selected_candidate ?? p.analyses?.[0] ?? null); setStage(startStage(p)); setError(""); }
    catch (e) { setError((e as Error).message); }
    finally { setOpening(false); }
  }
  async function created(id: string) { await loadList(); try { await load(id); setCid(null); setStage("reference"); } catch (e) { setError((e as Error).message); } }
  function newProject() { setProject(null); setCid(null); setStage("brief"); }
  async function remove() {
    if (!project || !window.confirm(`Delete the project "${project.title || project.id}" and all its versions?`)) return;
    try { await api.del(`/projects/${project.id}`); newProject(); await loadList(); } catch (e) { setError((e as Error).message); }
  }

  const { open: unlocked, done } = stageState(project, cid);
  const go = (s: StageId) => setStage(s);
  const analyse = (id: string) => { setCid(id); setStage("analysis"); };

  let content;
  if (stage === "settings") content = <Settings />;
  else if (stage === "brief" || !project) content = <Brief project={project} onCreated={created} next={() => go("reference")} />;
  else if (stage === "reference") content = <Reference project={project} reload={reload} next={() => go("questions")} />;
  else if (stage === "questions") content = <Questions project={project} reload={reload} designed={() => go("design")} />;
  else if (stage === "design") content = <Design project={project} reload={reload} approved={() => go("options")} />;
  else if (stage === "options") content = <Options project={project} reload={reload} analyse={analyse} />;
  else if (stage === "analysis") content = <Analysis project={project} reload={reload} cid={cid} setCid={setCid} decide={() => go("decision")} />;
  else content = <Decision project={project} reload={reload} cid={cid} />;

  return (
    <div className="shell">
      <a className="skip-link" href="#workspace-content">Skip to content</a>
      <aside className="rail" aria-label="Studio workspace">
        <div className="rail-head">
          <span className="brand-icon"><Bot size={22} aria-hidden="true" /></span>
          <span className="wordmark">Robo<span>DE</span> Studio</span>
        </div>

        <div className="rail-section">
          <label className="rail-label" htmlFor="project-pick">Project</label>
          <div className="project-picker">
            <FolderOpen size={16} aria-hidden="true" />
            <select id="project-pick" value={project?.id ?? ""} disabled={opening} onChange={(e) => (e.target.value ? void open(e.target.value) : newProject())}>
              <option value="">{projects.length ? "Choose a project" : "No saved projects"}</option>
              {projects.map((p) => <option key={p.id} value={p.id}>{p.title || p.id}</option>)}
            </select>
            <ChevronDown size={14} aria-hidden="true" />
          </div>
          <button className="new-project" disabled={opening} onClick={newProject}><Plus size={17} aria-hidden="true" /> New project</button>
        </div>

        <nav className="workflow" aria-label="Design workflow">
          <div className="rail-label">Workflow</div>
          <ol className="stages" ref={stageList}>
            {STAGES.map(({ id, title, icon: Icon }) => (
              <li key={id}>
                <button disabled={!unlocked[id]} aria-current={stage === id ? "step" : undefined} onClick={() => go(id)}>
                  <Icon size={18} aria-hidden="true" />
                  <span className="step-name">{title}{id === "reference" && <span className="sr-only"> (optional)</span>}</span>
                  {done[id] ? <span className="step-status complete"><Check size={15} aria-hidden="true" /><span className="sr-only">Completed</span></span>
                    : !unlocked[id] && <span className="step-status"><Lock size={13} aria-hidden="true" /><span className="sr-only">Locked</span></span>}
                </button>
              </li>
            ))}
          </ol>
        </nav>

        <div className="rail-foot">
          <button className="rail-link" aria-current={stage === "settings" ? "page" : undefined} onClick={() => go("settings")}>
            <SettingsIcon size={17} aria-hidden="true" /> Settings
          </button>
          {project && <button className="rail-link delete-project" disabled={opening} onClick={() => void remove()}><Trash2 size={16} aria-hidden="true" /> Delete project</button>}
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
            <ChevronRight size={15} aria-hidden="true" />
            <span title={project?.title || undefined}>{stage === "settings" ? "Settings" : project ? project.title || project.id : "New project"}</span>
          </div>
        </header>
        <main id="workspace-content" className="main" tabIndex={-1} aria-busy={opening}>
          {error && <div className="workspace-error"><div className="notice error" role="alert">{error}</div></div>}
          <Fragment key={project?.id ?? "new"}>{content}</Fragment>
        </main>
      </div>
    </div>
  );
}
