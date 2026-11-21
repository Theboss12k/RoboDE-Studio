// Small shared pieces: job runner hook, progress lines, chips, number formatting, and the dictation text box.
import { useId, useState } from "react";
import { Dictate, Fail, NoVerdict, NotPublished, Pass, StopDictation } from "./icons";
import { runJob, type Job } from "../api";
import { useVoice } from "../useVoice";
import type { Check } from "../types";

export function fmt(n: number | null | undefined, unit = "") {
  if (n === null || n === undefined || Number.isNaN(n)) return "–";
  const text = Math.abs(n) >= 1000 ? n.toFixed(0) : Number(n.toPrecision(4)).toString();
  return unit ? `${text} ${unit}` : text;
}

/** Runs a server job, collecting its progress lines. `run` returns undefined when the job failed (the error is in `error`). */
export function useJob() {
  const [busy, setBusy] = useState(false);
  const [lines, setLines] = useState<string[]>([]);
  const [error, setError] = useState("");
  async function run<T>(start: () => Promise<Job<T>>): Promise<T | undefined> {
    setBusy(true); setError(""); setLines([]);
    try { return await runJob<T>(start(), (line) => setLines((l) => [...l, line])); }
    catch (e) { setError((e as Error).message); return undefined; }
    finally { setBusy(false); }
  }
  return { busy, lines, error, setError, run };
}

export function JobStatus({ busy, lines, error, label = "Working" }: { busy: boolean; lines: string[]; error: string; label?: string }) {
  return (
    <div className="stack" aria-live="polite">
      {busy && <div className="busy">{label}{lines.length ? ": " + lines[lines.length - 1] : "…"}</div>}
      {busy && lines.length > 1 && <details><summary className="small muted">Progress ({lines.length} steps)</summary><ol className="small muted progress">{lines.map((l, i) => <li key={i}>{l}</li>)}</ol></details>}
      {error && <div className="notice error" role="alert">{error}</div>}
    </div>
  );
}

export function PassChip({ passed, unpublished, detail }: { passed: boolean | null | undefined; unpublished?: boolean; detail?: string }) {
  if (passed === true) return <span className="tag ok"><Pass size={14} aria-hidden="true" />PASS</span>;
  if (passed === false) return <span className="tag bad"><Fail size={14} aria-hidden="true" />FAIL</span>;
  if (unpublished) return <span className="tag warn" title={detail || "The manufacturer does not publish this value"}><NotPublished size={14} aria-hidden="true" />not published</span>;
  return <span className="tag" title="Could not be checked"><NoVerdict size={14} aria-hidden="true" /><span className="sr-only">Could not be checked</span></span>;
}

export function screenChips(screen: Check[]) {
  return screen.map((c) => (
    <span key={c.requirement_id} className={`tag ${c.passed === true ? "ok" : c.passed === false ? "bad" : ""}`} title={c.detail}>
      {c.passed === true ? <Pass size={14} aria-hidden="true" /> : c.passed === false ? <Fail size={14} aria-hidden="true" /> : <NoVerdict size={14} aria-hidden="true" />}
      {c.name}: {c.passed === true ? "pass" : c.passed === false ? "fail" : "unknown"}
    </span>
  ));
}

/** A text box with a microphone button (browser speech recognition). */
export function Dictation({ value, onChange, placeholder, rows = 4, label, disabled, big, hint }: {
  value: string; onChange: (v: string) => void; placeholder?: string; rows?: number; label: string; disabled?: boolean; big?: boolean; hint?: string;
}) {
  const id = useId();
  const [problem, setProblem] = useState("");
  const voice = useVoice(value, (t) => { setProblem(""); onChange(t); }, setProblem);
  return (
    <div className="composer">
      <label className={hint ? "composer-label" : "sr-only"} htmlFor={id}>{label}</label>
      {hint && <p className="composer-hint" id={id + "-hint"}>{hint}</p>}
      <div className="row dict">
        <textarea id={id} aria-describedby={hint ? id + "-hint" : undefined} className={big ? "big-text" : ""} rows={rows} value={value} disabled={disabled} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
        <button type="button" className="mic" aria-pressed={voice.listening} aria-label={voice.listening ? "Stop dictation" : "Dictate with the microphone"} disabled={disabled} onClick={voice.toggle}>
          {voice.listening ? <StopDictation size={18} aria-hidden="true" /> : <Dictate size={18} aria-hidden="true" />}
        </button>
      </div>
      {problem && <div className="notice warning small" role="status">{problem}</div>}
    </div>
  );
}

export function StageHead({ step, title, children }: { step?: string; title: string; children?: React.ReactNode }) {
  return (
    <header className="page-head">
      {step && <p className="stage-step">{step}</p>}
      <h1>{title}</h1>
      {children && <p className="page-intro">{children}</p>}
    </header>
  );
}
