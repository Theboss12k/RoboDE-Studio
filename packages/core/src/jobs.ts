// Observable store of the studio's background jobs. No DOM: it only needs the injected api. Jobs run on the server
// whatever the UI does; this store keeps polling them, so a stage (or a whole project view) can come and go and still
// find its job, its progress lines and its result. A finished result is "consumed" once a stage has handled it.
import type { Api } from "./api";
import type { Job } from "./types";

declare function setTimeout(handler: (...args: any[]) => void, ms: number): unknown;

/** A job record as the list routes return it: the polled record plus `subject` and `finished`. */
export type JobSummary<T = unknown> = Omit<Job<T>, "id"> & {
  id: string;
  kind: string;
  robot_id: string | null;
  error_code?: string | null;
  started: string;
  /** Candidate id (analyse), "v<n>" base version (revise), otherwise null. */
  subject: string | null;
  finished: string | null;
};

/** A job the store tracks. `consumed` is true once a result (or an old record found on adopt) was handled. */
export type TrackedJob<T = unknown> = JobSummary<T> & { consumed: boolean };

export type JobStoreSnapshot = { readonly jobs: readonly TrackedJob[] };
export type JobStoreOptions = { pollMs?: number; retries?: number };

export function createJobStore(api: Api, opts: JobStoreOptions = {}) {
  const pollMs = opts.pollMs ?? 700;
  const retries = opts.retries ?? 3;
  const jobs = new Map<string, TrackedJob>();
  const polling = new Set<string>();
  const listeners = new Set<() => void>();
  const finishers = new Set<(job: TrackedJob) => void>();
  let snap: JobStoreSnapshot = { jobs: [] };
  let local = 0;

  function emit() {
    snap = { jobs: [...jobs.values()] };
    listeners.forEach((fn) => fn());
  }
  function put(job: TrackedJob) { jobs.set(job.id, job); }
  function patch(id: string, change: Partial<TrackedJob>) {
    const old = jobs.get(id);
    if (old) put({ ...old, ...change });
  }
  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

  function normalise(job: Partial<JobSummary> & { id: string }, fallback: { kind: string; pid: string | null; subject: string | null }): JobSummary {
    return {
      id: job.id, kind: job.kind ?? fallback.kind, robot_id: job.robot_id ?? fallback.pid, status: job.status ?? "running",
      progress: job.progress ?? [], result: job.result ?? null, error: job.error ?? null, error_code: job.error_code ?? null,
      started: job.started ?? new Date().toISOString(), subject: job.subject ?? fallback.subject, finished: job.finished ?? null,
    };
  }

  async function poll(id: string) {
    if (polling.has(id)) return;
    polling.add(id);
    let failures = 0;
    try {
      for (;;) {
        await sleep(pollMs);
        try {
          const now = await api.get<Partial<JobSummary> & { id: string }>(`/jobs/${id}`);
          failures = 0;
          const old = jobs.get(id);
          if (!old) return;
          const status = now.status ?? "running";
          patch(id, { status, progress: now.progress ?? old.progress, result: now.result ?? null, error: now.error ?? null, finished: status === "running" ? null : (now.finished ?? new Date().toISOString()) });
          emit();
          if (status !== "running") { const done = jobs.get(id)!; finishers.forEach((fn) => fn(done)); return; }
        } catch (e) {
          if (++failures < retries) continue;
          patch(id, { status: "error", error: (e as Error).message || "Lost contact with the server", finished: new Date().toISOString() });
          emit();
          const lost = jobs.get(id)!;
          finishers.forEach((fn) => fn(lost));
          return;
        }
      }
    } finally { polling.delete(id); }
  }

  const store = {
    subscribe(fn: () => void) { listeners.add(fn); return () => { listeners.delete(fn); }; },
    /** The same object until something changes (for useSyncExternalStore). */
    snapshot(): JobStoreSnapshot { return snap; },

    /**
     * Registers a job whose start request is in flight (so the project is busy at once), then tracks and polls it.
     * Resolves with the job's final record; a failed start resolves with an error record instead of rejecting.
     */
    async start<T = unknown>(pid: string, kind: string, subject: string | null | undefined, startPromise: Promise<Job<T>>): Promise<TrackedJob<T>> {
      const fallback = { kind, pid, subject: subject ?? null };
      const pendingId = `pending:${++local}`;
      put({ ...normalise({ id: pendingId }, fallback), consumed: false });
      emit();
      let first: Job<T>;
      try {
        first = await startPromise;
      } catch (e) {
        jobs.delete(pendingId);
        const failed: TrackedJob = { ...normalise({ id: `failed:${++local}`, status: "error", error: (e as Error).message }, fallback), finished: new Date().toISOString(), consumed: false };
        put(failed); emit();
        return failed as TrackedJob<T>;
      }
      jobs.delete(pendingId);
      const id = first.id ?? `local:${++local}`;
      const known = jobs.get(id);  // joined a job the store already tracks
      const record: TrackedJob = { ...normalise({ ...first, id } as any, fallback), consumed: known?.consumed ?? false };
      if (first.id === null) { record.status = first.status; record.finished = new Date().toISOString(); }
      put(record); emit();
      if (record.status !== "running") {
        finishers.forEach((fn) => fn(record));
        return record as TrackedJob<T>;
      }
      const result = new Promise<TrackedJob<T>>((resolve) => {
        const off = store.subscribe(() => {
          const j = jobs.get(id);
          if (j && j.status !== "running") { off(); resolve(j as TrackedJob<T>); }
        });
      });
      void poll(id);
      return result;
    },

    /** Takes over a project's jobs after a switch: polls the running ones; finished ones count as already handled. */
    async adopt(pid: string) {
      const list = await api.get<JobSummary[]>(`/projects/${pid}/jobs`);
      store.track(list);
    },
    /** Takes over every running job on the server (any project), e.g. after a page reload. */
    async adoptAllRunning() {
      const list = await api.get<JobSummary[]>("/jobs?running=true");
      store.track(list);
    },
    /** Adds server records the store does not know yet. */
    track(list: JobSummary[]) {
      let changed = false;
      for (const j of [...list].sort((a, b) => a.started.localeCompare(b.started))) {
        if (jobs.has(j.id)) continue;
        put({ ...normalise(j, { kind: j.kind, pid: j.robot_id, subject: j.subject }), consumed: j.status !== "running" });
        changed = true;
        if (j.status === "running") void poll(j.id);
      }
      if (changed) emit();
    },

    forProject(pid: string): TrackedJob[] { return snap.jobs.filter((j) => j.robot_id === pid); },
    /** The most recently started job of this kind (and subject, when given) for the project. */
    latest(pid: string, kind: string, subject?: string | null): TrackedJob | undefined {
      return latestOf(snap, pid, kind, subject);
    },
    consume(id: string) {
      const j = jobs.get(id);
      if (!j || j.consumed) return;
      patch(id, { consumed: true });
      emit();
    },
    isConsumed(id: string) { return jobs.get(id)?.consumed ?? false; },
    /** Called when a tracked job ends (done or error) while this store watched it. */
    onFinish(fn: (job: TrackedJob) => void) { finishers.add(fn); return () => { finishers.delete(fn); }; },
  };
  return store;
}

/** Pure lookup on a snapshot; later entries win ties, so the newest job of a second wins. */
export function latestOf(snap: JobStoreSnapshot, pid: string, kind: string, subject?: string | null): TrackedJob | undefined {
  let best: TrackedJob | undefined;
  for (const j of snap.jobs) {
    if (j.robot_id !== pid || j.kind !== kind || (subject != null && j.subject !== subject)) continue;
    if (!best || Date.parse(j.started) >= Date.parse(best.started)) best = j;
  }
  return best;
}

export type JobStore = ReturnType<typeof createJobStore>;
