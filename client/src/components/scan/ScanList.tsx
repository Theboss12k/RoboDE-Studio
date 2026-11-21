// The site scans of a project: add a video (with upload progress) or record one with a phone, reconstruct, delete.
import { useEffect, useRef, useState } from "react";
import { api } from "../../api";
import { useProjectJob } from "../../jobs";
import { Add, Delete, LinkPhone } from "../icons";
import PhoneLink from "../ar/PhoneLink";
import type { SiteScan } from "../../types";

const MAX_VIDEO_BYTES = 500 * 1024 * 1024;
const STATUS_LABEL: Record<SiteScan["status"], string> = { uploaded: "Uploaded", reconstructing: "Reconstructing", reconstructed: "Reconstructed", failed: "Failed" };

/** POST the video as the raw body. XHR, because a browser reports upload progress only for XHR. */
function sendVideo(url: string, file: File, onProgress: (fraction: number) => void): Promise<SiteScan> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");
    xhr.upload.onprogress = (e) => { if (e.lengthComputable && e.total > 0) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) { try { resolve(JSON.parse(xhr.responseText) as SiteScan); } catch { reject(new Error("The server answered with something unexpected")); } return; }
      let detail = xhr.statusText;
      try { const body = JSON.parse(xhr.responseText); detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail); } catch { /* not JSON */ }
      reject(new Error(detail || `The upload failed (${xhr.status})`));
    };
    xhr.onerror = () => reject(new Error("The upload failed. Check the connection and try again."));
    xhr.send(file);
  });
}

function ScanRow({ pid, scan, selected, onSelect, refresh, onError }: {
  pid: string; scan: SiteScan; selected: boolean; onSelect: () => void; refresh: () => Promise<void>; onError: (message: string) => void;
}) {
  // One reconstruction job per scan; the store keeps following it when the stage is left and finds it again on return.
  const job = useProjectJob(pid, "scan_reconstruct", scan.id);
  const wasBusy = useRef(false);
  useEffect(() => {
    if (wasBusy.current && !job.busy) void refresh();
    wasBusy.current = job.busy;
  }, [job.busy]); // eslint-disable-line react-hooks/exhaustive-deps

  async function reconstruct() {
    onError("");
    await job.run(() => api.reconstructScan(pid, scan.id));
    await refresh();
  }
  async function remove() {
    if (!window.confirm("Delete this site scan?")) return;
    onError("");
    try { await api.deleteScan(pid, scan.id); await refresh(); } catch (e) { onError((e as Error).message); }
  }

  const running = job.busy || scan.status === "reconstructing";
  const last = job.lines[job.lines.length - 1];
  return (
    <li className="scan-row">
      <div className="scan-line">
        <button type="button" aria-pressed={selected} onClick={onSelect}>
          {new Date(scan.created).toLocaleString()} · {scan.video.duration_s.toFixed(0)} s
        </button>
        <span className={`tag ${scan.status === "reconstructed" ? "ok" : scan.status === "failed" ? "bad" : ""}`}>{STATUS_LABEL[running && scan.status !== "failed" ? "reconstructing" : scan.status]}</span>
      </div>
      <div className="row">
        {scan.status !== "reconstructed" && (
          <button type="button" className="primary" disabled={running} onClick={() => void reconstruct()}>
            {scan.status === "failed" ? "Try again" : "Reconstruct"}
          </button>
        )}
        <button type="button" className="danger" disabled={running} onClick={() => void remove()}><Delete size={16} aria-hidden="true" /> Delete</button>
      </div>
      {job.busy && (
        <div className="stack" aria-live="polite">
          <div className="busy">Reconstructing{last ? `: ${last}` : "…"}</div>
          {job.lines.length > 1 && <ul className="small muted progress-lines">{job.lines.slice(-3).map((l, i) => <li key={`${job.lines.length}-${i}`}>{l}</li>)}</ul>}
        </div>
      )}
      {scan.status === "reconstructed" && scan.stats && <p className="small muted">Frames placed: {scan.stats.frames_registered} of {scan.stats.frames_total}.</p>}
      {scan.status === "failed" && scan.error && <div className="notice error" role="alert">{scan.error}</div>}
      {!job.busy && job.error && scan.status !== "failed" && <div className="notice error" role="alert">{job.error}</div>}
    </li>
  );
}

export default function ScanList({ pid, scans, selected, onSelect, refresh, onError }: {
  pid: string; scans: SiteScan[]; selected: string | null; onSelect: (id: string) => void; refresh: () => Promise<void>; onError: (message: string) => void;
}) {
  const [upload, setUpload] = useState<{ name: string; fraction: number } | null>(null);
  const [linking, setLinking] = useState(false);

  async function add(file: File | undefined) {
    if (!file) return;
    onError("");
    if (file.size > MAX_VIDEO_BYTES) { onError("This video is over 500 MB. Record a shorter one."); return; }
    setUpload({ name: file.name, fraction: 0 });
    try {
      const made = await sendVideo(api.scanUploadUrl(pid, file.name), file, (fraction) => setUpload({ name: file.name, fraction }));
      await refresh();
      onSelect(made.id);
    } catch (e) { onError((e as Error).message); } finally { setUpload(null); }
  }

  return (
    <div className="stack">
      <div className="panel-head">
        <h4>My site</h4>
        <label className="button">{upload ? "Uploading" : <><Add size={16} aria-hidden="true" /> Add video</>}
          <input type="file" accept="video/*" className="sr-only" disabled={!!upload} aria-label="Add video"
            onChange={(e) => { void add(e.target.files?.[0]); e.target.value = ""; }} /></label>
      </div>
      <div className="row">
        <button type="button" aria-expanded={linking} onClick={() => setLinking(!linking)}><LinkPhone size={16} aria-hidden="true" /> Record with phone</button>
      </div>
      {linking && <PhoneLink pid={pid} receivedLabel="Sent from the phone" onReceived={() => void refresh()} onClose={() => setLinking(false)} />}
      {upload && (
        <div className="stack" role="status">
          <label className="small" htmlFor="scan-upload">Uploading {upload.name}: {Math.round(upload.fraction * 100)}%</label>
          <progress id="scan-upload" max={1} value={upload.fraction} aria-label="Upload progress" />
        </div>
      )}
      <p className="small muted">Walk slowly round the site and film it, about a minute is enough. A visual 3D model, not a survey.</p>
      {scans.length > 0 && (
        <ul className="files scan-list" aria-label="Site scans">
          {scans.map((s) => <ScanRow key={s.id} pid={pid} scan={s} selected={selected === s.id} onSelect={() => onSelect(s.id)} refresh={refresh} onError={onError} />)}
        </ul>
      )}
    </div>
  );
}
