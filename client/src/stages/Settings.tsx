import { useEffect, useState } from "react";
import { api, runJob } from "../api";
import Usage from "../Usage";
import { StageHead } from "../components/common";
import ThemeSwitch from "../components/ThemeSwitch";
import PreferredVendors from "../components/settings/PreferredVendors";

type Profile = { id: string; name: string; kind: string; model: string; effort: string; timeout_s: number; cli_path: string; location?: string; project?: string };
type KeyStatus = { saved: boolean; environment: string | null; ready: boolean };
type Settings = {
  profiles: Profile[]; roles: Record<string, string>; kinds: string[]; models: Record<string, string[]>;
  keys: Record<string, KeyStatus>;
};

const ROLES: Record<string, string> = {
  spec: "Turns your brief into a technical specification and asks questions",
  scene: "Lays out the scene: table, obstacles, markers and objects",
  robot: "Chooses the robot type and its starting parameters",
  revise: "Turns your change requests into design changes",
  search: "Searches the web for real robots and their prices",
  extract: "Reads manufacturer datasheets and quotes the values it finds",
  analysis: "Explains what each difference means for your task",
  ask: "Ask bar: answers questions about your project and can start changes",
  checks: "Task-specific checks: plans extra checks that code computes",
  objects: "Builds simple 3D models of the task objects (a small, fast model is enough)",
};
const ROLE_LABELS: Record<string, string> = { objects: "Object models" };

const PROVIDERS: Record<string, { label: string; note: string }> = {
  gemini: { label: "Google Gemini (API key)", note: "Uses the Gemini API with your key from Google AI Studio. Billed by Google to that key." },
  gemini_cli: { label: "Gemini CLI (Google sign-in)", note: "Uses the Gemini CLI signed in with your Google account on this computer (install it with npm install -g @google/gemini-cli, then run gemini once and sign in). No key needed. Text only: roles that read images need a Gemini API model." },
  antigravity_cli: { label: "Google Antigravity CLI (signed-in, no key)", note: "Uses the Antigravity CLI (agy) signed in with your Google account on this computer (run agy once and sign in). No key needed; calls count against your Antigravity quota, not a price per token. The studio turns every tool off, so it only answers text. Text only: roles that read images need a Gemini API model." },
  vertex_gemini: { label: "Gemini on Vertex AI (Google Cloud project, no key)", note: "Uses Gemini on Vertex AI, billed to your Google Cloud project (and its credits, subject to your credit terms). No key: on Cloud Run it uses the service account (needs roles/aiplatform.user); on this computer it uses `gcloud auth application-default login`. Leave Project empty to use the current Google Cloud project." },
};

const DEFAULT_MODEL: Record<string, string> = { gemini: "gemini-3.8-flash", gemini_cli: "gemini-3.8-flash", vertex_gemini: "gemini-3.8-flash", antigravity_cli: "gemini-3.8-flash-medium" };
const isCli = (kind: string) => kind === "gemini_cli" || kind === "antigravity_cli";
const isGemini = (kind: string) => kind === "gemini" || kind === "vertex_gemini" || isCli(kind);

export default function Settings() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [kind, setKind] = useState("gemini");
  const [draft, setDraft] = useState({ name: "", model: DEFAULT_MODEL.gemini, effort: "", timeout_s: 240, cli_path: "", location: "global", project: "" });
  const [key, setKey] = useState("");
  const [tests, setTests] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const load = () => api.get<Settings>("/llm").then(setSettings).catch((e) => setError(e.message));
  useEffect(() => { void load(); }, []);
  if (!settings) return <div className="page">{error ? <div className="notice error">{error}</div> : <div className="busy">Loading</div>}</div>;

  const gemini = settings.keys?.gemini;
  const profileById = Object.fromEntries(settings.profiles.map((p) => [p.id, p]));
  const needsKey = (p?: Profile) => p?.kind === "gemini" && !gemini?.ready;

  async function attempt(work: () => Promise<unknown>) {
    setError("");
    try { await work(); await load(); } catch (e) { setError((e as Error).message); }
  }
  const setRole = (role: string, id: string) => attempt(() => api.put("/llm/roles", { roles: { [role]: id } }));
  const add = () => attempt(async () => {
    await api.post("/llm/profiles", { ...draft, kind, cli_path: isCli(kind) ? draft.cli_path : "", effort: kind === "gemini" || kind === "vertex_gemini" ? draft.effort : "", ...(kind === "vertex_gemini" ? { location: draft.location || "global", project: draft.project } : { location: undefined, project: undefined }), name: draft.name || draft.model });
    setDraft({ ...draft, name: "" });
  });
  const saveKey = () => attempt(async () => { await api.put("/llm/keys/gemini", { key }); setKey(""); });
  const forgetKey = () => attempt(() => api.del("/llm/keys/gemini"));
  const remove = (id: string) => attempt(() => api.del(`/llm/profiles/${id}`));
  function choose(next: string) {
    setKind(next);
    setDraft({ ...draft, model: DEFAULT_MODEL[next], effort: "" });
  }
  async function test(id: string) {
    setTests({ ...tests, [id]: "Testing…" });
    try {
      const r = await runJob<{ text: string; text_seconds: number; images: string | null; image_seconds?: number }>(api.post(`/llm/profiles/${id}/test`));
      setTests((t) => ({ ...t, [id]: `Text: “${r.text}” in ${r.text_seconds} s. Images: ${r.images ? `“${r.images}” in ${r.image_seconds} s` : "not supported"}.` }));
    } catch (e) { setTests((t) => ({ ...t, [id]: (e as Error).message })); }
  }

  return (
    <div className="page">
      <StageHead artifact="settings" title="Settings and AI models">Assign models to workflow stages, manage provider connections, and review usage.</StageHead>
      {error && <div className="notice error">{error}</div>}
      <div className="panel">
        <h3>Appearance</h3>
        <p className="muted small">Light, dark, or follow this computer.</p>
        <ThemeSwitch />
      </div>
      <Usage />
      <PreferredVendors />
      <div className="panel">
        <h3>Who does what</h3>
        <table><tbody>{Object.entries(ROLES).map(([role, text]) => (
          <tr key={role}><td><b>{ROLE_LABELS[role] ?? role[0].toUpperCase() + role.slice(1)}</b><div className="small muted">{text}</div>
            {needsKey(profileById[settings.roles[role]]) && <div className="small warn-text">Needs a Gemini API key (below)</div>}</td>
            <td><select aria-label={`Model for ${role}`} value={settings.roles[role]} onChange={(e) => void setRole(role, e.target.value)}>{settings.profiles.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></td></tr>))}</tbody></table>
      </div>
      <div className="panel">
        <h3>Saved models</h3>
        <div className="table-wrap" tabIndex={0} role="region" aria-label="Saved models table">
          <table><thead><tr><th>Name</th><th>Provider</th><th>Model</th><th>Effort</th><th /></tr></thead>
          <tbody>{settings.profiles.map((p) => (
            <tr key={p.id}><td>{p.name}{tests[p.id] && <div className="small muted">{tests[p.id]}</div>}</td>
              <td><span className={`tag ${isGemini(p.kind) ? "google" : ""}`}>{p.kind === "gemini" ? "Gemini" : p.kind === "vertex_gemini" ? "Vertex AI" : p.kind === "gemini_cli" ? "Gemini CLI" : p.kind === "antigravity_cli" ? "Antigravity CLI" : p.kind === "claude_cli" ? "Claude" : p.kind}</span></td>
              <td className="mono small">{p.model}</td><td>{p.effort || "default"}</td>
              <td><div className="row"><button disabled={needsKey(p)} title={needsKey(p) ? "Add a Gemini API key first" : undefined} onClick={() => void test(p.id)}>Test</button>
                <button className="link" onClick={() => void remove(p.id)}>Remove</button></div></td></tr>))}</tbody></table>
        </div>
      </div>
      <div className="panel">
        <h3>Add a model</h3>
        <div className="tabs" role="tablist" aria-label="Provider">
          {Object.entries(PROVIDERS).filter(([k]) => settings.kinds.includes(k)).map(([k, p]) => (
            <button key={k} role="tab" aria-selected={kind === k} onClick={() => choose(k)}>{p.label}</button>))}
        </div>
        <p className="small muted">{PROVIDERS[kind]?.note}</p>
        {kind === "gemini" && (
          <div className="key-box">
            <div>
              <b>Gemini API key</b>{" "}
              {gemini?.saved ? <span className="tag ok">saved on this computer</span>
                : gemini?.environment ? <span className="tag ok">from {gemini.environment}</span>
                : <span className="tag warn">not set</span>}
              <p className="small muted">Create one at <a href="https://aistudio.google.com/apikey" target="_blank" rel="noreferrer noopener">aistudio.google.com/apikey</a>. It is stored in the server runtime folder, readable only by your user account, never shown again and never sent anywhere but Google.</p>
            </div>
            <div className="row">
              <label className="field">{gemini?.saved ? "Replace key" : "Key"}
                <input type="password" autoComplete="off" spellCheck={false} value={key} onChange={(e) => setKey(e.target.value)} placeholder="Paste your key" /></label>
              <button disabled={!key.trim()} onClick={() => void saveKey()}>Save key</button>
              {gemini?.saved && <button className="link" onClick={() => void forgetKey()}>Remove saved key</button>}
            </div>
          </div>
        )}
        <div className="row">
          <label className="field">Name<input value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} placeholder={kind === "gemini" || kind === "vertex_gemini" ? "Gemini search" : kind === "antigravity_cli" ? "Antigravity fast" : "Gemini CLI fast"} /></label>
          <label className="field">Model
            <input list="gemini-models" value={draft.model} onChange={(e) => setDraft({ ...draft, model: e.target.value })} spellCheck={false} />
            <datalist id="gemini-models">{(settings.models[kind] ?? settings.models.gemini ?? []).map((m) => <option key={m} value={m} />)}</datalist>
          </label>
          {(kind === "gemini" || kind === "vertex_gemini") && <label className="field">Thinking
            <select value={draft.effort} onChange={(e) => setDraft({ ...draft, effort: e.target.value })}>
              <option value="">Model default</option>
              {["low", "medium", "high"].map((m) => <option key={m}>{m}</option>)}
            </select></label>}
          {kind === "vertex_gemini" && <>
            <label className="field">Location<input value={draft.location} onChange={(e) => setDraft({ ...draft, location: e.target.value })} placeholder="global" spellCheck={false} /></label>
            <label className="field">Project (optional)<input value={draft.project} onChange={(e) => setDraft({ ...draft, project: e.target.value })} placeholder="current Google Cloud project" spellCheck={false} /></label>
          </>}
          {isCli(kind) && <label className="field">CLI path (optional)<input value={draft.cli_path} onChange={(e) => setDraft({ ...draft, cli_path: e.target.value })} placeholder="found automatically" /></label>}
        </div>
        <div><button onClick={() => void add()}>Add model</button></div>
        {kind === "gemini" && <p className="small muted">Suggested: a fast model for search and extraction, a stronger one for the specification, robot and revise roles. Web search uses Grounding with Google Search: 5,000 searches a month free, then $14 per 1,000 (Google's prices, November 2026).</p>}
      </div>
    </div>
  );
}
