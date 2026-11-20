import { useState } from "react";
import { api } from "../api";
import { StageHead } from "../components/common";
import type { Project } from "../types";

export default function Reference({ project, reload, next }: { project: Project; reload: () => Promise<void>; next: () => void }) {
  const [text, setText] = useState(project.reference?.text ?? "");
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const files = project.reference?.files ?? [];
  const has = !!(project.reference?.text || files.length);
  async function attempt(work: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await work(); await reload(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const upload = (list: FileList | File[]) => attempt(async () => {
    for (const f of Array.from(list)) {
      if (!/^image\//.test(f.type) && !/\.(urdf|zip)$/i.test(f.name)) throw new Error(`${f.name}: use an image, a .urdf or a .zip`);
      await api.upload(`/projects/${project.id}/reference/files`, f);
    }
  });
  const saveText = () => attempt(() => api.put(`/projects/${project.id}/reference`, { text }));
  const clear = () => attempt(async () => { await api.del(`/projects/${project.id}/reference`); setText(""); });
  return (
    <div className="page narrow">
      <StageHead artifact="reference" step="Step 2 of 7 (optional)" title="Reference">
        Is there a robot or a similar setup you already have in mind? Add photos, a URDF or zip, or describe it. You can skip this.
      </StageHead>
      <div className="panel">
        <div className={`drop ${over ? "over" : ""}`} tabIndex={0}
          onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
          onDrop={(e) => { e.preventDefault(); setOver(false); void upload(e.dataTransfer.files); }}
          onPaste={(e) => { const imgs = Array.from(e.clipboardData.files).filter((f) => f.type.startsWith("image/")); if (imgs.length) { e.preventDefault(); void upload(imgs); } }}>
          <b>Drop images or a .urdf / .zip here, or paste an image</b>
          <label className="button">Choose files
            <input type="file" multiple accept="image/*,.urdf,.zip" className="sr-only" disabled={busy} onChange={(e) => { if (e.target.files) void upload(e.target.files); e.target.value = ""; }} /></label>
        </div>
        {files.length > 0 && <ul className="files">{files.map((f) => <li key={f.name}><span>{f.name}</span><span className="muted small">{f.kind}</span></li>)}</ul>}
        <label className="field"><span>Or describe it</span>
          <textarea rows={4} value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. We have a small 6-axis arm with a 500 mm reach; we would like something similar." /></label>
        <div className="row">
          <button disabled={busy || !text.trim()} onClick={() => void saveText()}>Save description</button>
          {has && <button className="danger" disabled={busy} onClick={() => void clear()}>Remove reference</button>}
        </div>
        {error && <div className="notice error" role="alert">{error}</div>}
      </div>
      <div className="row"><button className="go big" onClick={next}>{has ? "Continue" : "Skip"}</button></div>
    </div>
  );
}
