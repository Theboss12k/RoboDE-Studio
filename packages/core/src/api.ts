// REST client for the studio API. No DOM: `fetch` is injected so React Native, WebXR and tests can supply their own.
import type { Job } from "./types";
import type { ArCalibration, ArPlacement, ArReference, ArScene, SceneSpec, SiteScan } from "./generated/types";

// setTimeout exists in every JS runtime (browser, Node, React Native); declared here because the DOM lib is not used.
declare function setTimeout(handler: (...args: any[]) => void, ms: number): unknown;

export class ApiError extends Error {
  /** HTTP status of the failed response, when there was one. */
  status?: number;
  constructor(message: string, status?: number) { super(message); this.status = status; }
}

/** A phone link (docs/AR_PHOTO.md, "Phone link"). `qr_svg` is only in the answer to starting one. */
export type ArPairing = { url: string; urls: string[]; qr_svg?: string; expires_at: string; uploads: number; max_uploads: number };

/** Body of POST /projects/{pid}/scans/{sid}/scale (docs/SITE_SCAN.md): a sheet in a frame, or a measured distance (p1, p2 in R). */
export type ScanScaleRequest =
  | { method: "sheet"; frame: number; corners_px: number[][]; size_m: number[] }
  | { method: "distance"; p1: number[]; p2: number[]; distance_m: number };

/** Body of POST /projects/{pid}/apply-scene: positions are in the design world W, metres. */
export type ApplySceneRequest = {
  base_version: number;
  scene: SceneSpec;
  markers: { id: string; position: [number, number, number] }[];
  auto_repair: boolean;
  note: string;
};

export type FetchLike =(input: string, init?: { method?: string; headers?: Record<string, string>; body?: any }) => Promise<ResponseLike>;
export type ResponseLike = { ok: boolean; status: number; statusText: string; headers: { get(name: string): string | null }; json(): Promise<any>; text(): Promise<string> };

export type ApiOptions = {
  /** e.g. "/api/v1" in the browser or "http://192.168.1.5:8800/api/v1" from a phone. */
  baseUrl: string;
  fetch: FetchLike;
  /** Bearer token, when the server runs with STUDIO_TOKEN. */
  token?: string;
  /** Delay between job polls, ms (default 700). */
  pollMs?: number;
};

async function handle<T>(response: ResponseLike): Promise<T> {
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch { /* not JSON */ }
    throw new ApiError(detail || `Request failed (${response.status})`, response.status);
  }
  const type = response.headers.get("content-type") || "";
  return (type.includes("json") ? response.json() : response.text()) as Promise<T>;
}

export function createApi({ baseUrl, fetch, token, pollMs = 700 }: ApiOptions) {
  const base = baseUrl.replace(/\/+$/, "");
  const auth: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
  const json = (method: string, path: string, body?: unknown) =>
    fetch(base + path, { method, headers: { ...auth, "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });

  const api = {
    get: <T,>(path: string) => fetch(base + path, { headers: auth }).then((r) => handle<T>(r)),
    post: <T,>(path: string, body?: unknown) => json("POST", path, body).then((r) => handle<T>(r)),
    put: <T,>(path: string, body: unknown) => json("PUT", path, body).then((r) => handle<T>(r)),
    patch: <T,>(path: string, body: unknown) => json("PATCH", path, body).then((r) => handle<T>(r)),
    del: <T,>(path: string) => fetch(base + path, { method: "DELETE", headers: auth }).then((r) => handle<T>(r)),
    /** Upload a file body (a browser File/Blob, or any body the injected fetch accepts); `file.name` becomes `filename`. */
    upload: <T,>(path: string, file: { name: string }, params: Record<string, string> = {}) =>
      fetch(base + path + "?" + Object.entries({ filename: file.name, ...params }).map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&"), { method: "POST", headers: auth, body: file }).then((r) => handle<T>(r)),
  };

  /** Start a job and poll it until it finishes; `onProgress` gets each new progress line. */
  async function runJob<T>(start: Promise<Job<T>>, onProgress?: (line: string) => void): Promise<T> {
    let job = await start;
    let seen = 0;
    if (job.id === null) return job.result as T;  // answered at once, without a background job
    while (job.status === "running") {
      await new Promise((r) => setTimeout(r, pollMs));
      job = await api.get<Job<T>>(`/jobs/${job.id}`);
      for (; seen < job.progress.length; seen++) onProgress?.(job.progress[seen]);
    }
    if (job.status === "error") throw new ApiError(job.error || "The job failed");
    return job.result as T;
  }

  // Photo AR (docs/AR_PHOTO.md). The photo itself is a URL for <img>, so it is not fetched here.
  const arBase = (pid: string) => `/projects/${encodeURIComponent(pid)}/ar/scenes`;
  const arPairingBase = (pid: string) => `/projects/${encodeURIComponent(pid)}/ar/pairing`;
  const ar = {
    arScenes: (pid: string) => api.get<ArScene[]>(arBase(pid)),
    arScene: (pid: string, sid: string) => api.get<ArScene>(`${arBase(pid)}/${encodeURIComponent(sid)}`),
    uploadArScene: (pid: string, file: { name: string }) => api.upload<ArScene>(arBase(pid), file),
    arPhotoUrl: (pid: string, sid: string) => `${base}${arBase(pid)}/${encodeURIComponent(sid)}/photo`,
    calibrateArScene: (pid: string, sid: string, reference: ArReference) => api.post<ArScene>(`${arBase(pid)}/${encodeURIComponent(sid)}/calibrate`, { reference }),
    estimateArScene: (pid: string, sid: string) => api.post<Job<ArCalibration>>(`${arBase(pid)}/${encodeURIComponent(sid)}/estimate`, {}),
    setArPlacement: (pid: string, sid: string, placement: ArPlacement) => api.put<ArScene>(`${arBase(pid)}/${encodeURIComponent(sid)}/placement`, placement),
    deleteArScene: (pid: string, sid: string) => api.del<unknown>(`${arBase(pid)}/${encodeURIComponent(sid)}`),
    // Link a phone: the phone itself talks to a separate listener, never to this API.
    startArPairing: (pid: string) => api.post<ArPairing>(arPairingBase(pid)),
    arPairing: (pid: string) => api.get<ArPairing>(arPairingBase(pid)),
    stopArPairing: (pid: string) => api.del<unknown>(arPairingBase(pid)),
    /** The QR code as SVG text for one of the pairing's addresses. */
    arPairingQr: (pid: string, url: string) => api.get<string>(`${arPairingBase(pid)}/qr?url=${encodeURIComponent(url)}`),
  };

  // Site scan (docs/SITE_SCAN.md). The mesh, frames and video are URLs for the viewer, so they are not fetched here.
  const scanBase = (pid: string) => `/projects/${encodeURIComponent(pid)}/scans`;
  const scanPath = (pid: string, sid: string) => `${scanBase(pid)}/${encodeURIComponent(sid)}`;
  const scan = {
    scans: (pid: string) => api.get<SiteScan[]>(scanBase(pid)),
    scan: (pid: string, sid: string) => api.get<SiteScan>(scanPath(pid, sid)),
    /** The video goes up as the raw request body. Browsers report upload progress only for XHR, so the UI can post to `scanUploadUrl` itself. */
    uploadScan: (pid: string, file: { name: string }) => api.upload<SiteScan>(scanBase(pid), file),
    scanUploadUrl: (pid: string, filename: string) => `${base}${scanBase(pid)}?filename=${encodeURIComponent(filename)}`,
    reconstructScan: (pid: string, sid: string) => api.post<Job<unknown>>(`${scanPath(pid, sid)}/reconstruct`, {}),
    scaleScan: (pid: string, sid: string, request: ScanScaleRequest) => api.post<SiteScan>(`${scanPath(pid, sid)}/scale`, request),
    setScanFloor: (pid: string, sid: string, points: number[][]) => api.put<SiteScan>(`${scanPath(pid, sid)}/floor`, { points }),
    setScanPlacement: (pid: string, sid: string, placement: ArPlacement) => api.put<SiteScan>(`${scanPath(pid, sid)}/placement`, placement),
    deleteScan: (pid: string, sid: string) => api.del<unknown>(scanPath(pid, sid)),
    scanMeshUrl: (pid: string, sid: string) => `${base}${scanPath(pid, sid)}/mesh`,
    scanFrameUrl: (pid: string, sid: string, index: number) => `${base}${scanPath(pid, sid)}/frames/${index}`,
    scanVideoUrl: (pid: string, sid: string) => `${base}${scanPath(pid, sid)}/video`,
    /** Starts apply_scene (deterministic, no model): the edited scene becomes a new version of `base_version`. */
    applyScene: (pid: string, request: ApplySceneRequest) => api.post<Job<{ version: number }>>(`/projects/${encodeURIComponent(pid)}/apply-scene`, request),
  };

  return { ...api, ...ar, ...scan, runJob };
}

export type Api = ReturnType<typeof createApi>;
