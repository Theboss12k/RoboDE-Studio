import { useState, type FormEvent } from "react";
import { api } from "../api";
import { Dictation, StageHead } from "../components/common";
import { Next, TaskBench, TaskPalletise, TaskPickPlace, TaskTransport, UseExample } from "../components/icons";
import type { Project } from "../types";

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

export default function Brief({ project, onCreated, next }: {
  project: Project | null; onCreated: (id: string) => Promise<void>; next: () => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const canCreate = !busy && text.trim().length >= 10;

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canCreate) return;
    setBusy(true);
    setError("");
    try {
      const project = await api.post<Project>("/projects", { brief: text.trim() });
      await onCreated(project.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (project) return (
    <div className="page narrow">
      <StageHead step="Step 1 of 7" title="Task brief">The original task description for this project.</StageHead>
      <section className="panel">
        <p className="brief-text">{project.brief}</p>
        <div className="row"><button className="go" onClick={next}>Continue to reference <Next size={16} aria-hidden="true" /></button></div>
      </section>
    </div>
  );

  return (
    <div className="page brief-page">
      <header className="brief-hero">
        <div className="hero-copy">
          <p className="hero-step">Step 1 of 7 <span aria-hidden="true">/</span> Brief</p>
          <h1>What should the robot do?</h1>
          <p className="hero-intro">Start with a task in your own words. Studio uses it to design a robot and check how it would perform.</p>
        </div>
      </header>

      <div className="brief-grid">
        <form className="panel brief-form" onSubmit={(event) => void create(event)}>
          <Dictation label="Task brief" hint="Include what moves, how heavy it is, where it goes, and how often."
            big rows={5} value={text} onChange={setText} placeholder="e.g. Move a 300 g bottle between two points on a table…" disabled={busy} />
          <div className="brief-actions">
            <button className="go big" type="submit" disabled={!canCreate}>Create project <Next size={17} aria-hidden="true" /></button>
            {busy && <span className="busy small" role="status">Creating project</span>}
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
