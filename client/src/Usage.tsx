// Token usage for every model call, on the AI models page: totals, per role, per job, per project and the latest calls.
import { useEffect, useState } from "react";
import { api } from "./api";

type Bucket = { calls: number; input: number; cache_write: number; cache_read: number; output: number; cost_usd: number; seconds: number; images: number };
type Entry = Bucket & { at: string; role: string; profile: string; model: string; robot_id: string | null; task: string | null; turns: number | null; structured: boolean; ok: boolean };
type Summary = {
  period: string; totals: Bucket & { cache_hit_rate: number | null }; by_role: Record<string, Bucket>; by_model: Record<string, Bucket>;
  by_robot_id?: Record<string, Bucket>; by_task: Record<string, Bucket>; by_day: Record<string, Bucket>; recent: Entry[];
};

const PERIODS: [string, string][] = [["today", "Today"], ["7d", "7 days"], ["30d", "30 days"], ["all", "All time"]];
const ROLE_NAMES: Record<string, string> = {
  spec: "Specification", scene: "Scene", robot: "Robot concept", revise: "Revisions", search: "Robot search", extract: "Datasheet extraction",
  analysis: "Analysis", objects: "Object models", ask: "Ask bar", checks: "Task-specific checks", test: "Connection tests", other: "Other",
};
const TASK_NAMES: Record<string, string> = {
  spec: "Specification and questions", scene: "Scene layout", robot: "Robot concept", revise: "Change requests", search: "Web search for robots",
  extract: "Datasheet extraction", analysis: "Impact analysis", ask: "Ask bar questions", llm_test: "Connection test", other: "Other",
};

async function fetchUsage(period: string): Promise<Summary> {
  try { return await api.get<Summary>("/llm/usage?period=" + period); } catch { return api.get<Summary>("/usage?period=" + period); }
}

/** Compact usage counter for the rail: today's calls and tokens. */
export function UsageCounter() {
  const [t, setT] = useState<Summary["totals"] | null>(null);
  useEffect(() => {
    const load = () => fetchUsage("today").then((s) => setT(s.totals)).catch(() => setT(null));
    void load();
    const timer = setInterval(load, 15000);
    return () => clearInterval(timer);
  }, []);
  if (!t) return <span className="small">Model usage today: –</span>;
  return <span className="small" title="Model calls and tokens today (input + output)">Today: {t.calls} model calls, {tokens(inputOf(t) + t.output)} tokens</span>;
}

export function tokens(n: number) {
  if (n >= 1e6) return (n / 1e6).toFixed(2) + "M";
  if (n >= 1e4) return (n / 1e3).toFixed(1) + "k";
  return n.toLocaleString();
}
const inputOf = (b: Bucket) => b.input + b.cache_write + b.cache_read;
const money = (n: number) => "$" + (n < 0.01 && n > 0 ? n.toFixed(4) : n.toFixed(2));
const cachedShare = (b: Bucket) => (inputOf(b) ? Math.round((b.cache_read / inputOf(b)) * 100) + "%" : "–");

function Table({ title, rows, names }: { title: string; rows: Record<string, Bucket>; names?: Record<string, string> }) {
  const entries = Object.entries(rows).sort((a, b) => inputOf(b[1]) + b[1].output - inputOf(a[1]) - a[1].output);
  if (!entries.length) return null;
  return (
    <div>
      <h3>{title}</h3>
      <table>
        <thead><tr><th>{title.replace("By ", "").replace(/^./, (c) => c.toUpperCase())}</th><th>Calls</th><th>Input</th><th>From cache</th><th>Output</th><th>Est. cost</th></tr></thead>
        <tbody>{entries.map(([key, b]) => (
          <tr key={key}><td>{names?.[key] ?? key}</td><td>{b.calls}</td><td>{tokens(inputOf(b))}</td>
            <td>{cachedShare(b)}</td><td>{tokens(b.output)}</td><td>{money(b.cost_usd)}</td></tr>))}</tbody>
      </table>
    </div>
  );
}

export default function Usage() {
  const [period, setPeriod] = useState("7d");
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState("");
  const load = () => fetchUsage(period).then((s) => { setSummary(s); setError(""); }).catch((e) => setError(e.message));
  useEffect(() => {
    void load();
    const timer = setInterval(load, 10000);
    return () => clearInterval(timer);
  }, [period]); // eslint-disable-line react-hooks/exhaustive-deps
  async function reset() {
    if (!window.confirm("Start a new usage count? The current one is kept as a file in the server runtime folder.")) return;
    setSummary(await api.post<Summary>("/llm/usage/reset"));
  }
  const t = summary?.totals;
  return (
    <div className="panel">
      <div className="panel-head">
        <h3>Token usage</h3>
        <div className="row">
          <div className="choices" role="group" aria-label="Period">{PERIODS.map(([id, label]) => (
            <button key={id} aria-pressed={period === id} onClick={() => setPeriod(id)}>{label}</button>))}</div>
          <button className="link" onClick={() => void reset()}>Start a new count</button>
        </div>
      </div>
      {error && <div className="notice error small">{error}</div>}
      {summary && t && (
        <>
          <div className="meters">
            <div><span className="muted small">Calls</span><b>{t.calls}</b></div>
            <div><span className="muted small">Input tokens</span><b>{tokens(inputOf(t))}</b></div>
            <div><span className="muted small">Served from cache</span><b>{t.cache_hit_rate === null ? "–" : Math.round(t.cache_hit_rate * 100) + "%"}</b></div>
            <div><span className="muted small">Output tokens</span><b>{tokens(t.output)}</b></div>
            <div><span className="muted small">Images sent</span><b>{t.images}</b></div>
            <div><span className="muted small">Estimated cost</span><b>{money(t.cost_usd)}</b></div>
          </div>
          <p className="small muted">Counts come from the Claude CLI's own report for each call. Input read from the prompt cache costs about a tenth of fresh input. The cost is an estimate at list prices; on a subscription plan it is not a charge.</p>
          {t.calls === 0 ? <p className="muted">No model calls in this period.</p> : (
            <>
              <div className="grid-2">
                <Table title="By role" rows={summary.by_role} names={ROLE_NAMES} />
                <Table title="By job" rows={summary.by_task} names={TASK_NAMES} />
              </div>
              <div className="grid-2">
                <Table title="By project" rows={summary.by_robot_id ?? {}} />
                <Table title="By model" rows={summary.by_model} />
              </div>
              <details>
                <summary><b>Latest calls</b></summary>
                <table>
                  <thead><tr><th>When</th><th>Job</th><th>Model</th><th>Input</th><th>Cached</th><th>Output</th><th>Images</th><th>Time</th></tr></thead>
                  <tbody>{summary.recent.map((e, i) => (
                    <tr key={i} className={e.ok ? "" : "off"}>
                      <td className="small">{new Date(e.at).toLocaleTimeString()}</td>
                      <td className="small">{TASK_NAMES[e.task ?? "other"] ?? e.task}{e.robot_id ? " (" + e.robot_id + ")" : ""}</td>
                      <td className="small">{e.model.replace("claude-", "")}</td><td>{tokens(inputOf(e))}</td><td>{tokens(e.cache_read)}</td>
                      <td>{tokens(e.output)}</td><td>{e.images || ""}</td><td className="small">{e.seconds}s</td>
                    </tr>))}</tbody>
                </table>
              </details>
            </>
          )}
        </>
      )}
    </div>
  );
}
