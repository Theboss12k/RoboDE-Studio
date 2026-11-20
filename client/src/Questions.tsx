// Questions the AI asks while drafting (twin wizard, robot model, command matrix), and the inputs to answer them.
import { useEffect, useState } from "react";
import type { Question } from "./types";

export function Answer({ q, value, onChange }: { q: Question; value: unknown; onChange: (v: unknown) => void }) {
  if (q.kind === "choice") return <div className="choices">{(q.options ?? []).map((o) => <button key={o} aria-pressed={value === o} onClick={() => onChange(o)}>{o}</button>)}</div>;
  if (q.kind === "boolean") return <div className="choices">{[true, false].map((o) => <button key={String(o)} aria-pressed={value === o} onClick={() => onChange(o)}>{o ? "Yes" : "No"}</button>)}</div>;
  if (q.kind === "number") return <span className="row"><input type="number" step="any" value={(value as number) ?? ""} onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))} /> <span className="muted">{q.unit}</span></span>;
  return <input value={(value as string) ?? ""} onChange={(e) => onChange(e.target.value)} style={{ width: "100%" }} />;
}

/** The unanswered questions, with a Send button. Defaults count as answers when left unchanged. */
export function QuestionPanel({ questions, answered, busy, onSubmit }: {
  questions: Question[]; answered: Record<string, unknown>; busy: boolean; onSubmit: (answers: Record<string, unknown>) => void;
}) {
  const open = questions.filter((q) => !(q.id in answered));
  const [answers, setAnswers] = useState<Record<string, unknown>>({});
  useEffect(() => { setAnswers({}); }, [questions.length]);
  if (!open.length) return null;
  const submit = () => onSubmit(Object.fromEntries(open.map((q) => [q.id, answers[q.id] ?? q.default]).filter(([, v]) => v !== undefined && v !== null && v !== "")));
  return (
    <div className="panel">
      <h3>The AI needs to know</h3>
      {open.map((q) => (
        <div key={q.id} className="question">
          <b>{q.text}</b>
          {q.why && <span className="why">{q.why}</span>}
          <Answer q={q} value={answers[q.id] ?? q.default} onChange={(v) => setAnswers({ ...answers, [q.id]: v })} />
        </div>))}
      <div><button className="primary" disabled={busy} onClick={submit}>Send answers</button></div>
    </div>
  );
}
