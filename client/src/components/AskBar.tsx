// The Ask bar: a floating button (and Cmd/Ctrl-K) that opens a small conversation panel on every screen.
// The conversation lives in React state for this session only. Answers are markdown, rendered without innerHTML.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import { AskIcon, Fail, Pass } from "./icons";

import type { AskTurn, AskContext as AskContextBody, AskCitation, AskActionTaken as AskAction, AskConfirm, AskNavigate,
  AskResponse as AskResult } from "@robode/studio-core";
export type { AskTurn, AskContextBody, AskCitation, AskAction, AskConfirm, AskNavigate, AskResult };

export interface AskSelection { version?: number; build?: number; candidate?: string; t?: number }
interface AskCtx {
  selection: AskSelection; setSelection: (s: AskSelection) => void;
  requested: AskNavigate | null; request: (n: AskNavigate | null) => void;
}
const Ctx = createContext<AskCtx>({ selection: {}, setSelection: () => undefined, requested: null, request: () => undefined });

export function AskProvider({ children }: { children: ReactNode }) {
  const [selection, setSelection] = useState<AskSelection>({});
  const [requested, request] = useState<AskNavigate | null>(null);
  const value = useMemo(() => ({ selection, setSelection, requested, request }), [selection, requested]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/** A stage calls this with what it is showing (version, build, candidate); it clears when the stage goes away. */
export function useAskSelection(sel: AskSelection) {
  const { setSelection } = useContext(Ctx);
  const { version, build, candidate, t } = sel;
  useEffect(() => { setSelection({ version, build, candidate, t }); }, [setSelection, version, build, candidate, t]);
  useEffect(() => () => setSelection({}), [setSelection]);
}

/** A stage calls this to learn that the Ask bar asked for a version or build; `take` is called once with it. */
export function useAskRequest(take: (n: AskNavigate) => void) {
  const { requested, request } = useContext(Ctx);
  const taker = useRef(take); taker.current = take;
  useEffect(() => { if (requested && (requested.version != null || requested.build != null)) { taker.current(requested); request(null); } }, [requested, request]);
}

// ---- A tiny, safe markdown renderer: paragraphs, lists, **bold**, `code`, line breaks. ----
function inline(text: string, key: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).filter(Boolean).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) return <strong key={`${key}${i}`}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) return <code key={`${key}${i}`}>{part.slice(1, -1)}</code>;
    return part;
  });
}
export function Markdown({ text }: { text: string }) {
  const blocks: ReactNode[] = [];
  let para: string[] = []; let list: { ordered: boolean; items: string[] } | null = null;
  const flushPara = () => { if (para.length) { const k = blocks.length; blocks.push(<p key={k}>{para.flatMap((l, i) => (i ? [<br key={`b${i}`} />, ...inline(l, `${k}-${i}`)] : inline(l, `${k}-${i}`)))}</p>); para = []; } };
  const flushList = () => { if (list) { const k = blocks.length; const items = list.items.map((it, i) => <li key={i}>{inline(it, `${k}-${i}`)}</li>); blocks.push(list.ordered ? <ol key={k}>{items}</ol> : <ul key={k}>{items}</ul>); list = null; } };
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line); const number = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (!line.trim()) { flushPara(); flushList(); }
    else if (bullet || number) { flushPara(); const ordered = !!number; if (list && list.ordered !== ordered) flushList(); list = list ?? { ordered, items: [] }; list.items.push((bullet ?? number)![1]); }
    else { flushList(); para.push(line.trim()); }
  }
  flushPara(); flushList();
  return <div className="ask-md">{blocks}</div>;
}

type Msg = { role: "user" | "assistant"; text: string; result?: AskResult };

interface Props {
  projectId: string | null; stage: string; candidate: string | null;
  reload: () => Promise<void>;
  navigate: (n: AskNavigate) => void;
}

export function AskBar({ projectId, stage, candidate, reload, navigate }: Props) {
  const { selection, request } = useContext(Ctx);
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pending, setPending] = useState<{ msg: number; chip: number } | null>(null);
  const [chipError, setChipError] = useState("");
  const input = useRef<HTMLTextAreaElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const log = useRef<HTMLDivElement>(null);
  const wasOpen = useRef(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setOpen((o) => !o); }
      else if (e.key === "Escape") setOpen((o) => o ? false : o);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => {
    if (open) input.current?.focus(); else if (wasOpen.current) button.current?.focus();
    wasOpen.current = open;
  }, [open]);
  useEffect(() => { log.current?.scrollTo({ top: log.current.scrollHeight }); }, [msgs, busy, pending]);

  const go = useCallback((n: AskNavigate) => { navigate(n); request(n); }, [navigate, request]);

  async function send() {
    const question = text.trim().slice(0, 2000);
    if (!question || busy) return;
    const history: AskTurn[] = msgs.slice(-6).map((m) => ({ role: m.role, text: m.text }));
    const context: AskContextBody = { stage };
    if (projectId) context.project_id = projectId;
    if (selection.version != null) context.version = selection.version;
    if (selection.t != null) context.t = selection.t;
    const cand = selection.candidate ?? candidate ?? undefined; if (cand) context.candidate = cand;
    if (selection.build != null) context.build = selection.build;
    setMsgs((m) => [...m, { role: "user", text: question }]); setText(""); setBusy(true); setError(""); setPending(null); setChipError("");
    try {
      const r = await api.runJob<AskResult>(api.post("/ask?wait=120", { question, history, context }));
      setMsgs((m) => [...m, { role: "assistant", text: r.answer, result: r }]);
      if (r.actions_taken?.some((a) => a.ok)) void reload().catch(() => undefined);
      if (r.navigate) go(r.navigate);
    } catch (e) { setError((e as Error).message || "The question failed"); }
    finally { setBusy(false); }
  }

  async function confirm(c: AskConfirm) {
    if (!projectId) return;
    setChipError("");
    try {
      if (c.kind === "approve") await api.post(`/projects/${projectId}/approve`, c.request);
      else if (c.kind === "approve_build") await api.post(`/projects/${projectId}/studio/builds/${String((c.request as { build?: unknown }).build)}/approve`, {});
      else await api.post(`/projects/${projectId}/decision`, c.request);
      setMsgs((m) => [...m, { role: "assistant", text: `Done: ${c.label}` }]);
      setPending(null);
      await reload();
    } catch (e) { setChipError((e as Error).message || "That failed"); }
  }

  return (
    <>
      {!open && <button ref={button} type="button" className="ask-fab" aria-label="Ask about your project (Ctrl or Cmd K)" aria-expanded={false} aria-haspopup="dialog" onClick={() => setOpen(true)}><AskIcon size={22} aria-hidden="true" /></button>}
      {open && (
        <div className="ask-panel" role="dialog" aria-modal="false" aria-label="Ask">
          <div className="ask-head"><strong>Ask</strong><span className="muted small">Answers about your project</span>
            <button type="button" className="ask-close" aria-label="Close Ask" onClick={() => setOpen(false)}><Fail size={16} aria-hidden="true" /></button></div>
          <div className="ask-log" ref={log} aria-live="polite">
            {!msgs.length && <p className="muted small">Ask why a check failed, what changed between versions, or tell it a change to make. It will ask you before approving anything.</p>}
            {msgs.map((m, i) => (
              <div key={i} className={`ask-msg ${m.role}`}>
                {m.role === "user" ? <p>{m.text}</p> : <Markdown text={m.text} />}
                {m.result && <Extras result={m.result} onGo={go} canConfirm={!!projectId}
                  pending={pending?.msg === i ? pending.chip : null} chipError={pending?.msg === i ? chipError : ""}
                  onAsk={(chip) => { setChipError(""); setPending({ msg: i, chip }); }} onCancel={() => setPending(null)} onConfirm={(c) => void confirm(c)} />}
              </div>))}
            {busy && <div className="ask-msg assistant busy" role="status">Thinking</div>}
          </div>
          {error && <div className="notice error ask-error" role="alert">{error}</div>}
          <form className="ask-form" onSubmit={(e) => { e.preventDefault(); void send(); }}>
            <textarea ref={input} rows={2} maxLength={2000} value={text} aria-label="Your question" placeholder="Ask a question or request a change"
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); } }} />
            <button type="submit" className="primary" disabled={busy || !text.trim()}>Send</button>
          </form>
        </div>)}
    </>
  );
}

function Extras({ result, onGo, canConfirm, pending, chipError, onAsk, onCancel, onConfirm }: {
  result: AskResult; onGo: (n: AskNavigate) => void; canConfirm: boolean; pending: number | null; chipError: string;
  onAsk: (chip: number) => void; onCancel: () => void; onConfirm: (c: AskConfirm) => void;
}) {
  const jump = (c: AskCitation): AskNavigate | null => {
    const n = Number(c.ref);
    if (c.kind === "version" && Number.isFinite(n)) return { stage: "design", version: n };
    if (c.kind === "build" && Number.isFinite(n)) return { stage: "studio", build: n };
    if (c.kind === "candidate" && c.ref) return { stage: "analysis", candidate: c.ref };
    return null;
  };
  return (
    <>
      {!!result.citations?.length && <ul className="ask-cites" aria-label="Sources">
        {result.citations.map((c, i) => { const to = jump(c); return <li key={i}>{to ? <button type="button" className="ask-chip" onClick={() => onGo(to)}>{c.label}</button> : <span className="ask-chip">{c.label}</span>}</li>; })}
      </ul>}
      {!!result.actions_taken?.length && <div className="ask-actions"><div className="small muted">Actions taken</div>
        <ul>{result.actions_taken.map((a, i) => (
          <li key={i} className={a.ok ? "ok" : "failed"}>
            {a.ok ? <Pass size={14} aria-hidden="true" /> : <Fail size={14} aria-hidden="true" />}
            <span>{a.summary}{!a.ok && a.error ? `: ${a.error}` : ""}{a.job_id && a.ok ? " (see Running in the left panel)" : ""}<span className="sr-only">{a.ok ? " (done)" : " (failed)"}</span></span>
          </li>))}</ul></div>}
      {canConfirm && !!result.confirm?.length && <div className="ask-confirm">
        {result.confirm.map((c, i) => pending === i
          ? <span key={i} className="ask-sure" role="group" aria-label={c.label}><span>{c.label}?</span>
              <button type="button" className="primary" onClick={() => onConfirm(c)}>Confirm</button>
              <button type="button" onClick={onCancel}>Cancel</button></span>
          : <button key={i} type="button" className="ask-chip action" onClick={() => onAsk(i)}>{c.label}</button>)}
        {chipError && <div className="notice error" role="alert">{chipError}</div>}
      </div>}
    </>
  );
}
