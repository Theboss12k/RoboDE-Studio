// Questions the AI asks while drafting (twin wizard, robot model, command matrix), and the inputs to answer them.
import { useEffect, useState } from "react";
import { api } from "./api";
import { Add } from "./components/icons";
import { AttachButton, Thumbs, imageFrom } from "./components/ImageAttach";
import { Dictation } from "./components/common";
import type { AnswerImage, Question } from "./types";
export type { AnswerImage };

/** One image attached to a question (or "_general") in the project view. */
export const MAX_SPEC_IMAGES = 8;

export function Answer({ q, value, onChange }: { q: Question; value: unknown; onChange: (v: unknown) => void }) {
  if (q.kind === "choice") return <div className="choices">{(q.options ?? []).map((o) => <button key={o} aria-pressed={value === o} onClick={() => onChange(o)}>{o}</button>)}</div>;
  if (q.kind === "boolean") return <div className="choices">{[true, false].map((o) => <button key={String(o)} aria-pressed={value === o} onClick={() => onChange(o)}>{o ? "Yes" : "No"}</button>)}</div>;
  if (q.kind === "number") return <span className="row"><input type="number" step="any" value={(value as number) ?? ""} onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))} /> <span className="muted">{q.unit}</span></span>;
  return <input value={(value as string) ?? ""} onChange={(e) => onChange(e.target.value)} style={{ width: "100%" }} />;
}

/** The unanswered questions, with a Send button. Defaults count as answers when left unchanged. */
export function QuestionPanel({ projectId, questions, answered, busy, images, referenceImages, reload, onSubmit }: {
  projectId: string; questions: Question[]; answered: Record<string, unknown>; busy: boolean;
  images: AnswerImage[]; referenceImages: number; reload: () => Promise<void>;
  onSubmit: (answers: Record<string, unknown>, comments: Record<string, string>, note: string) => void;
}) {
  const open = questions.filter((q) => !(q.id in answered));
  const [answers, setAnswers] = useState<Record<string, unknown>>({});
  const [comments, setComments] = useState<Record<string, string>>({});
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState("");
  const [thumbs, setThumbs] = useState<Record<string, string>>({});
  useEffect(() => { setAnswers({}); setComments({}); setNote(""); }, [questions.length]);
  const used = referenceImages + images.length;
  const ids = new Set(open.map((q) => q.id));
  const general = images.filter((i) => !ids.has(i.question));

  async function attach(question: string, file: File) {
    setProblem("");
    try {
      await api.upload(`/projects/${projectId}/answers/files`, file, { question });
      setThumbs((t) => ({ ...t, [file.name.replace(/[^A-Za-z0-9._ -]+/g, "_")]: URL.createObjectURL(file) }));
      await reload();
    } catch (e) { setProblem(e instanceof Error ? e.message : "The image could not be added"); }
  }
  async function detach(name: string) {
    setProblem("");
    try { await api.del(`/projects/${projectId}/answers/files/${encodeURIComponent(name)}`); await reload(); }
    catch (e) { setProblem(e instanceof Error ? e.message : "The image could not be removed"); }
  }
  const src = (name: string) => thumbs[name] ?? `/api/v1/projects/${projectId}/answers/files/${encodeURIComponent(name)}`;

  if (!open.length) return null;
  const submit = () => {
    const given = Object.entries(comments).map(([k, v]) => [k, v.trim()] as const).filter(([k, v]) => v && ids.has(k));
    onSubmit(Object.fromEntries(open.map((q) => [q.id, answers[q.id] ?? q.default]).filter(([, v]) => v !== undefined && v !== null && v !== "")),
      Object.fromEntries(given), note.trim());
  };
  return (
    <div className="panel">
      <h3>The AI needs to know</h3>
      {open.map((q) => (
        <QuestionCard key={q.id} q={q} value={answers[q.id] ?? q.default} onChange={(v) => setAnswers({ ...answers, [q.id]: v })}
          comment={comments[q.id] ?? ""} onComment={(v) => setComments({ ...comments, [q.id]: v })}
          images={images.filter((i) => i.question === q.id)} src={src} full={used >= MAX_SPEC_IMAGES}
          onAttach={(f) => void attach(q.id, f)} onRemove={(n) => void detach(n)} />))}
      <div className="answer-extra">
        <Dictation label="Anything else?" hint="Anything else the AI should know for this round? (optional)" rows={3} value={note} onChange={setNote} />
        <AttachButton label="Attach an image" disabled={used >= MAX_SPEC_IMAGES} onFile={(f) => void attach("_general", f)} />
        <Thumbs images={general} src={src} onRemove={(n) => void detach(n)} />
      </div>
      <p className="muted small image-budget">{used} of {MAX_SPEC_IMAGES} images used (reference photos count too)</p>
      {problem && <div className="notice warning small" role="alert">{problem}</div>}
      <div><button className="primary" disabled={busy} onClick={submit}>Send answers</button></div>
    </div>
  );
}

function QuestionCard({ q, value, onChange, comment, onComment, images, src, full, onAttach, onRemove }: {
  q: Question; value: unknown; onChange: (v: unknown) => void; comment: string; onComment: (v: string) => void;
  images: AnswerImage[]; src: (name: string) => string; full: boolean; onAttach: (f: File) => void; onRemove: (name: string) => void;
}) {
  const [over, setOver] = useState(false);
  const take = (files: FileList | File[] | null | undefined) => { const f = imageFrom(files); if (f && !full) onAttach(f); };
  return (
    <div className={"question" + (over ? " drop-over" : "")}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); take(e.dataTransfer.files); }}
      onPaste={(e) => take(e.clipboardData.files)}>
      <b>{q.text}</b>
      {q.why && <span className="why">{q.why}</span>}
      <Answer q={q} value={value} onChange={onChange} />
      <div className="question-extras">
        <details open={comment !== ""}>
          <summary><Add size={14} aria-hidden="true" /> Add a comment</summary>
          <textarea rows={2} maxLength={1000} aria-label={`Comment on: ${q.text}`} value={comment} onChange={(e) => onComment(e.target.value)} style={{ width: "100%" }} />
        </details>
        <AttachButton label="Attach image" disabled={full} onFile={onAttach} />
      </div>
      <Thumbs images={images} src={src} onRemove={onRemove} />
    </div>
  );
}
