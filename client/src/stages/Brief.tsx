import { useEffect, useRef, useState, type FormEvent } from "react";
import { api } from "../api";
import { Dictation, StageHead } from "../components/common";
import { AttachButton, ImageDrop, Thumbs } from "../components/ImageAttach";
import { useProjectJob } from "../jobs";
import RobotArtifact from "../components/RobotArtifact";
import { Next, TaskBench, TaskPalletise, TaskPickPlace, TaskTransport, UseExample } from "../components/icons";
import type { BriefImage, Project } from "../types";

const EXAMPLES = [
  { title: "Pick and place", detail: "300 g bottles · 10 per minute", icon: TaskPickPlace,
    brief: "Move a 300 g bottle from point A to point B on a table, about 10 per minute." },
  { title: "Palletising", detail: "10 kg cartons · every 12 seconds", icon: TaskPalletise,
    brief: "Palletise 10 kg cartons from a conveyor onto a pallet, one carton every 12 seconds." },
  { title: "Warehouse transport", detail: "25 kg totes · 40 m trips", icon: TaskTransport,
    brief: "Carry a 25 kg tote across a warehouse aisle, 40 m each way, and lift it onto a shelf at 1.2 m." },
  { title: "Bench operation", detail: "Button panel and a 2 kg tool", icon: TaskBench,
    brief: "Press a button panel and pick up a 2 kg tool from a bench, once a minute." },
];

const MAX_IMAGES = 8;
const BUDGET = "Up to 8 images across brief, reference and answers.";
const imageUrl = (pid: string, name: string) => `/api/v1/projects/${pid}/brief/files/${encodeURIComponent(name)}`;

type Pending = { file: File; url: string };

/** The brief page of an existing project: images and the editable text. */
function BriefView({ project, reload, notice, next }: { project: Project; reload: () => Promise<void>; notice: string; next: () => void }) {
  const design = useProjectJob(project.id, "design");
  const revise = useProjectJob(project.id, "revise");
  const running = design.busy || revise.busy;
  const approved = project.approved_version != null;
  const locked = approved || running;
  const images: BriefImage[] = project.brief_images ?? [];
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(project.brief ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(notice);
  const [thumbs, setThumbs] = useState<Record<string, string>>({});
  const asked = (project.versions?.length ?? 0) > 0 || Object.keys(project.answers ?? {}).length > 0 || (project.answer_notes?.length ?? 0) > 0;
  const canSave = !busy && text.trim().length >= 10 && text.trim() !== (project.brief ?? "").trim();

  async function attempt(work: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await work(); await reload(); return true; } catch (e) { setError((e as Error).message); return false; } finally { setBusy(false); }
  }
  const add = (files: File[]) => void attempt(async () => {
    for (const f of files.slice(0, Math.max(0, MAX_IMAGES - images.length))) {
      await api.upload(`/projects/${project.id}/brief/files`, f);
      setThumbs((t) => ({ ...t, [f.name.replace(/[^A-Za-z0-9._ -]+/g, "_")]: URL.createObjectURL(f) }));
    }
  });
  const remove = (name: string) => void attempt(() => api.del(`/projects/${project.id}/brief/files/${encodeURIComponent(name)}`));
  const save = async () => { if (await attempt(() => api.put(`/projects/${project.id}/brief`, { brief: text.trim() }))) setEditing(false); };
  useEffect(() => () => { Object.values(thumbs).forEach((u) => URL.revokeObjectURL(u)); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="page narrow">
      <StageHead artifact="brief" step="Step 1 of 7" title="Task brief">The original task description for this project.</StageHead>
      <section className="panel">
        {editing ? (
          <div className="brief-edit">
            <Dictation label="Task brief" big rows={5} value={text} onChange={setText} disabled={busy} />
            {asked && <div className="notice small">Studio will ask its questions again for the new brief; existing versions stay as they are.</div>}
            <div className="row">
              <button className="go" disabled={!canSave || locked} onClick={() => void save()}>Save</button>
              <button disabled={busy} onClick={() => { setEditing(false); setText(project.brief ?? ""); setError(""); }}>Cancel</button>
              {busy && <span className="busy small" role="status">Saving</span>}
            </div>
          </div>
        ) : (
          <>
            <p className="brief-text">{project.brief}</p>
            {project.brief_updated && <p className="small muted">Edited {new Date(project.brief_updated).toLocaleString()}</p>}
            <div className="row">
              <button disabled={locked} onClick={() => { setText(project.brief ?? ""); setEditing(true); setError(""); }}>Edit brief</button>
              {approved && <span className="small muted">The design is approved, so the brief is frozen.</span>}
              {!approved && running && <span className="small muted">A design is being worked on; you can edit the brief when it finishes.</span>}
            </div>
          </>
        )}
        <div className="brief-images">
          <h3>Brief images</h3>
          <p className="small muted">Photos of the task, the objects or the workcell. {BUDGET}</p>
          <Thumbs images={images} src={(n) => thumbs[n] ?? imageUrl(project.id, n)} onRemove={remove} disabled={locked || busy} />
          {!approved && (
            <ImageDrop onFiles={add} disabled={locked || busy}>
              <b>Drop images here, or paste one</b>
              <AttachButton label="Add images" disabled={locked || busy || images.length >= MAX_IMAGES} onFiles={add} />
            </ImageDrop>
          )}
        </div>
        {error && <div className="notice error" role="alert">{error}</div>}
        <div className="row"><button className="go" onClick={next}>Continue to reference <Next size={16} aria-hidden="true" /></button></div>
      </section>
    </div>
  );
}

export default function Brief({ project, onCreated, reload, notice, next }: {
  project: Project | null; onCreated: (id: string, notice?: string) => Promise<void>; reload: () => Promise<void>; notice: string; next: () => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState<Pending[]>([]);
  const urls = useRef<string[]>([]);
  const canCreate = !busy && text.trim().length >= 10;
  useEffect(() => () => urls.current.forEach((u) => URL.revokeObjectURL(u)), []);

  const addFiles = (files: File[]) => setPending((p) => {
    const room = files.slice(0, Math.max(0, MAX_IMAGES - p.length)).map((file) => { const url = URL.createObjectURL(file); urls.current.push(url); return { file, url }; });
    return [...p, ...room];
  });
  const removeFile = (name: string) => setPending((p) => p.filter((x) => x.url !== name));

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canCreate) return;
    setBusy(true);
    setError("");
    let created: Project | null = null;
    let problem = "";
    try {
      created = await api.post<Project>("/projects", { brief: text.trim() });
      for (const [i, { file }] of pending.entries()) {
        setProgress(`Uploading image ${i + 1} of ${pending.length}`);
        try { await api.upload(`/projects/${created.id}/brief/files`, file); }
        catch (e) { problem = `${file.name} could not be added: ${(e as Error).message}`; break; }
      }
      await onCreated(created.id, problem);
    } catch (e) {
      setError(created ? `The project was created but could not be opened: ${(e as Error).message}` : (e as Error).message);
    } finally {
      setBusy(false);
      setProgress("");
    }
  }

  if (project) return <BriefView key={project.id} project={project} reload={reload} notice={notice} next={next} />;

  return (
    <div className="page brief-page">
      <header className="brief-hero">
        <div className="hero-copy">
          <p className="hero-step">Step 1 of 7 <span aria-hidden="true">/</span> Brief</p>
          <h1>What should the robot do?</h1>
          <p className="hero-intro">Start with a task in your own words. Studio uses it to design a robot and check how it would perform.</p>
        </div>
        <RobotArtifact page="brief" hero />
      </header>

      <div className="brief-grid">
        <form className="panel brief-form" onSubmit={(event) => void create(event)}>
          <Dictation label="Task brief" hint="Include what moves, how heavy it is, where it goes, and how often."
            big rows={5} value={text} onChange={setText} placeholder="e.g. Move a 300 g bottle between two points on a table…" disabled={busy} />
          <div className="brief-images">
            <span className="small muted">Optional photos of the task, objects or workcell. {BUDGET}</span>
            <ImageDrop onFiles={addFiles} disabled={busy}>
              <b>Drop images here, or paste one</b>
              <AttachButton label="Add images" disabled={busy || pending.length >= MAX_IMAGES} onFiles={addFiles} />
            </ImageDrop>
            <Thumbs images={pending.map((p) => ({ name: p.url }))} src={(u) => u} onRemove={removeFile} disabled={busy} />
          </div>
          <div className="brief-actions">
            <button className="go big" type="submit" disabled={!canCreate}>Create project <Next size={17} aria-hidden="true" /></button>
            {busy && <span className="busy small" role="status">{progress || "Creating project"}</span>}
            {!busy && text.trim().length > 0 && !canCreate && <span className="small muted">Enter at least 10 characters.</span>}
          </div>
          {error && <div className="notice error" role="alert">{error}</div>}
        </form>

        <section className="panel example-panel" aria-labelledby="examples-title">
          <div className="example-heading">
            <h2 id="examples-title">Example tasks</h2>
            <p className="small muted">Choose one to fill in the brief.</p>
          </div>
          <div className="examples">
            {EXAMPLES.map(({ title, detail, icon: Icon, brief }) => (
              <button key={title} type="button" className="example" aria-pressed={text === brief} disabled={busy} onClick={() => setText(brief)}>
                <span className="example-icon"><Icon size={17} aria-hidden="true" /></span>
                <span className="example-copy"><strong>{title}</strong><span>{detail}</span></span>
                <UseExample size={16} className="example-arrow" aria-hidden="true" />
              </button>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
