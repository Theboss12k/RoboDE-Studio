// REST client for the studio API. No DOM: `fetch` is injected so React Native, WebXR and tests can supply their own.
import type { Job } from "./types";

// setTimeout exists in every JS runtime (browser, Node, React Native); declared here because the DOM lib is not used.
declare function setTimeout(handler: (...args: any[]) => void, ms: number): unknown;

export class ApiError extends Error {}

export type FetchLike = (input: string, init?: { method?: string; headers?: Record<string, string>; body?: any }) => Promise<ResponseLike>;
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
    throw new ApiError(detail || `Request failed (${response.status})`);
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

  return { ...api, runJob };
}

export type Api = ReturnType<typeof createApi>;
