// Thin fetch helpers for the studio API (proxied to 127.0.0.1:8800 in development).
export class ApiError extends Error {}

async function handle<T>(response: Response): Promise<T> {
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

export const api = {
  get: <T,>(path: string) => fetch("/api" + path).then((r) => handle<T>(r)),
  post: <T,>(path: string, body?: unknown) =>
    fetch("/api" + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) }).then((r) => handle<T>(r)),
  put: <T,>(path: string, body: unknown) =>
    fetch("/api" + path, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then((r) => handle<T>(r)),
  patch: <T,>(path: string, body: unknown) =>
    fetch("/api" + path, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then((r) => handle<T>(r)),
  del: <T,>(path: string) => fetch("/api" + path, { method: "DELETE" }).then((r) => handle<T>(r)),
  upload: <T,>(path: string, file: File, params: Record<string, string> = {}) =>
    fetch("/api" + path + "?" + new URLSearchParams({ filename: file.name, ...params }), { method: "POST", body: file }).then((r) => handle<T>(r)),
};

export type Job<T = unknown> = { id: string | null; status: "running" | "done" | "error"; progress: string[]; result: T | null; error: string | null };

/** Start a job and poll it until it finishes; `onProgress` gets each new progress line. */
export async function runJob<T>(start: Promise<Job<T>>, onProgress?: (line: string) => void): Promise<T> {
  let job = await start;
  let seen = 0;
  if (job.id === null) return job.result as T;  // answered at once, without a background job
  while (job.status === "running") {
    await new Promise((r) => setTimeout(r, 700));
    job = await api.get<Job<T>>(`/jobs/${job.id}`);
    for (; seen < job.progress.length; seen++) onProgress?.(job.progress[seen]);
  }
  if (job.status === "error") throw new ApiError(job.error || "The job failed");
  return job.result as T;
}
