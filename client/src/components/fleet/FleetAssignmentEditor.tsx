import { useEffect, useState } from "react";
import { api, ApiError } from "../../api";
import { JobStatus } from "../common";
import { useProjectJob } from "../../jobs";
import type { FleetAssignResponse, FleetAssignment, FleetHandover, FleetMemberAssignment, Project, TaskAction, Vec3 } from "../../types";
import type { FleetOverlay } from "../SceneView";

type Vec = Vec3 | null;

/** Three optional number boxes for a point; the value is null until all three are filled in. */
function VecInput({ label, value, onChange, disabled }: { label: string; value: Vec; onChange: (v: Vec) => void; disabled?: boolean }) {
  const [text, setText] = useState<string[]>(() => value ? value.map(String) : ["", "", ""]);
  const key = JSON.stringify(value);
  useEffect(() => {
    if (!value) return;
    const parsed = text.map((t) => (t.trim() === "" ? NaN : Number(t)));
    if (parsed.some((n, i) => n !== value[i])) setText(value.map(String));
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  function edit(i: number, s: string) {
    const next = text.map((t, j) => (j === i ? s : t));
    setText(next);
    const nums = next.map((t) => (t.trim() === "" ? NaN : Number(t)));
    onChange(nums.some(Number.isNaN) ? null : (nums as Vec3));
  }
  return (
    <span className="vec-input" role="group" aria-label={label}>
      {["x", "y", "z"].map((axis, i) => (
        <input key={axis} type="number" step={0.05} value={text[i]} disabled={disabled} aria-label={`${label} ${axis}`} placeholder={axis} onChange={(e) => edit(i, e.target.value)} />
      ))}
    </span>
  );
}

function detail(e: unknown) {
  if (e instanceof ApiError || e instanceof Error) return e.message;
  return String(e);
}

/** Auto-assign, edit and confirm who does which action, then plan the fleet. Preview goes to the scene via `onPreview`. */
export default function FleetAssignmentEditor({ project, reload, baseVersion, actions, initial, onPreview, onPlanned, frozen }: {
  project: Project; reload: () => Promise<void>; baseVersion: number; actions: TaskAction[]; initial?: FleetAssignment | null;
  onPreview: (overlay: FleetOverlay | null) => void; onPlanned: (version: number) => void; frozen?: boolean;
}) {
  const [assignment, setAssignment] = useState<FleetAssignment | null>(initial ?? project.fleet?.assignment ?? null);
  const [feasible, setFeasible] = useState<boolean | null>(null);
  const [notes, setNotes] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const design = useProjectJob<{ version: number }>(project.id, "fleet_design", `v${baseVersion}`, async (r) => { await reload(); onPlanned(r.version); });
  const locked = frozen || design.busy || design.blocked;

  function apply(r: FleetAssignResponse) {
    setAssignment(r.assignment); setFeasible(r.feasible); setNotes(r.notes ?? []);
    const p = r.preview;
    onPreview({ bases: p?.bases, handovers: p?.handovers, samples: p?.workspace_samples });
  }
  async function call(body: object) {
    setBusy(true); setError("");
    try { apply(await api.post<FleetAssignResponse>(`/projects/${project.id}/fleet/assign`, { base_version: baseVersion, ...body })); return true; }
    catch (e) { setError(detail(e)); return false; }
    finally { setBusy(false); }
  }
  const auto = () => call({});
  async function confirm() {
    if (!assignment) return;
    if (await call({ assignment: { ...assignment, source: "user" } })) await reload();
  }
  async function plan() {
    const r = await design.run<{ version: number }>(() => api.post(`/projects/${project.id}/fleet/design`, { base_version: baseVersion }), `v${baseVersion}`);
    if (!r) return;
    await reload(); onPlanned(r.version);
  }
  const edit = (next: FleetAssignment) => setAssignment({ ...next, source: "user", confirmed: false });
  const ids = assignment?.members.map((m) => m.id) ?? [];
  const owner = (i: number) => assignment?.members.find((m) => m.actions.includes(i))?.id ?? "";
  const move = (i: number, to: string) => assignment && edit({ ...assignment, members: assignment.members.map((m) => ({ ...m, actions: [...m.actions.filter((a) => a !== i), ...(m.id === to ? [i] : [])].sort((a, b) => a - b) })) });
  const setMember = (id: string, patch: Partial<FleetMemberAssignment>) => assignment && edit({ ...assignment, members: assignment.members.map((m) => (m.id === id ? { ...m, ...patch } : m)) });
  const setHandover = (id: string, patch: Partial<FleetHandover>) => assignment && edit({ ...assignment, handovers: assignment.handovers.map((h) => (h.id === id ? { ...h, ...patch } : h)) });

  return (
    <div className="panel stack">
      <div className="panel-head"><h3>Who does what</h3>
        {assignment && <span className={`tag ${assignment.confirmed ? "ok" : "warn"}`}>{assignment.confirmed ? "confirmed" : "not confirmed"} ({assignment.source})</span>}</div>
      <p className="small muted">Split the task between the robots. Auto-assign is computed in code from reach and payload checks, and you can change anything before planning.</p>
      <div className="row"><button disabled={busy || locked} onClick={() => void auto()}>{assignment ? "Auto-assign again" : "Auto-assign"}</button></div>
      {feasible !== null && <div className={`notice small ${feasible ? "ok" : "warning"}`}>{feasible ? "The assignment looks feasible by the code checks." : "The code checks found a problem with this assignment."}</div>}
      {notes.length > 0 && <ul className="plain small">{notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
      {assignment && (
        <>
          <div className="table-wrap">
            <table>
              <caption className="sr-only">Task actions and the robot that does each</caption>
              <thead><tr><th>#</th><th>Action</th><th>Robot</th></tr></thead>
              <tbody>
                {actions.map((a, i) => (
                  <tr key={i}>
                    <td>{i + 1}</td>
                    <td>{a.verb} {a.object}{a.target ? ` to ${a.target}` : ""}<div className="small muted">{a.description}</div></td>
                    <td><select aria-label={`Robot for action ${i + 1}`} value={owner(i)} disabled={locked} onChange={(e) => move(i, e.target.value)}>
                      <option value="">unassigned</option>
                      {assignment.members.map((m) => <option key={m.id} value={m.id}>{m.id} {m.name}</option>)}
                    </select></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {assignment.handovers.length > 0 && (
            <div className="table-wrap">
              <table>
                <caption className="sr-only">Handover points between robots</caption>
                <thead><tr><th>Handover</th><th>Position (m)</th><th>Fixed</th></tr></thead>
                <tbody>
                  {assignment.handovers.map((h) => (
                    <tr key={h.id}>
                      <td>{h.id}: {h.object}, {h.from_member} to {h.to_member}</td>
                      <td><VecInput label={`Handover ${h.id}`} value={h.position} disabled={locked} onChange={(v) => setHandover(h.id, { position: v, ...(v ? { fixed: true } : {}) })} /></td>
                      <td><label className="check-line"><input type="checkbox" checked={h.fixed} disabled={locked || !h.position} onChange={(e) => setHandover(h.id, { fixed: e.target.checked })} /> fixed</label></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <details>
            <summary className="small">Base, start and end positions per robot (optional)</summary>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Robot</th><th>Base</th><th>Start</th><th>End</th></tr></thead>
                <tbody>
                  {assignment.members.map((m) => (
                    <tr key={m.id}>
                      <td>{m.id} {m.name}<div className="small muted">{m.family || "any family"}</div></td>
                      {(["base", "start", "end"] as const).map((k) => (
                        <td key={k}><VecInput label={`${m.id} ${k}`} value={m[k]} disabled={locked} onChange={(v) => setMember(m.id, { [k]: v })} /></td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="small muted">Leave a position empty to let the planner choose it. A position needs all of x, y and z.</p>
          </details>
          <div className="row">
            <button disabled={busy || locked || ids.length === 0} onClick={() => void confirm()}>Confirm assignment</button>
            <button className="primary" disabled={locked || busy || !assignment.confirmed} onClick={() => void plan()}>Plan the fleet</button>
            {!assignment.confirmed && <span className="small muted">Confirm the assignment first.</span>}
          </div>
        </>
      )}
      {error && <div className="notice error" role="alert">{error}</div>}
      <JobStatus {...design} label="Planning the fleet" />
    </div>
  );
}
