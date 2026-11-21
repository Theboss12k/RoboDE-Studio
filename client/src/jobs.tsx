// Background jobs for the whole app: one store above the per-project view, so a job started in a stage keeps being
// followed when the user changes stage, task or project, and the stage finds it again on return.
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { createJobStore, latestOf, type JobStore, type TrackedJob } from "@robode/studio-core";
import { api, type Job } from "./api";

const JobsContext = createContext<JobStore | null>(null);

export function JobsProvider({ children }: { children: ReactNode }) {
  const [store] = useState(() => createJobStore(api));
  return <JobsContext.Provider value={store}>{children}</JobsContext.Provider>;
}

export function useJobStore(): JobStore {
  const store = useContext(JobsContext);
  if (!store) throw new Error("useJobStore needs a JobsProvider");
  return store;
}

/** Design steps that exclude each other on one project (the server answers a second one with 409). */
const EXCLUSIVE = ["design", "revise", "fleet_design", "apply_scene"];

/**
 * The newest job of `kind` (and `subject`, when given) for a project, in the shape the stages used from `useJob`.
 * `run` starts a job and resolves with its result (undefined when it failed or the stage went away meanwhile).
 * `onResume` receives the result of a job that finished while the stage was away, once, and consumes it.
 */
export function useProjectJob<T = unknown>(pid: string, kind: string, subject?: string | null, onResume?: (result: T, job: TrackedJob) => void | Promise<void>) {
  const store = useJobStore();
  const snap = useSyncExternalStore(store.subscribe, store.snapshot);
  const job = latestOf(snap, pid, kind, subject);
  const [localError, setLocalError] = useState("");
  const mounted = useRef(true);
  const inflight = useRef(false);
  const resume = useRef(onResume);
  resume.current = onResume;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  const busy = job?.status === "running";
  const blocked = EXCLUSIVE.includes(kind) && snap.jobs.some((j) => j.robot_id === pid && j.status === "running" && j.kind !== kind && EXCLUSIVE.includes(j.kind));
  const error = localError || (job?.status === "error" && !job.consumed ? job.error ?? "The job failed" : "");

  const run = useCallback(async <R,>(start: () => Promise<Job<R>>, runSubject?: string | null): Promise<R | undefined> => {
    setLocalError(""); inflight.current = true;
    try {
      const done = await store.start<R>(pid, kind, runSubject ?? subject, start());
      if (!mounted.current || done.status !== "done") return undefined;
      store.consume(done.id);
      return done.result as R;
    } finally { inflight.current = false; }
  }, [store, pid, kind, subject]);

  const jobId = job?.id, jobStatus = job?.status, jobConsumed = job?.consumed;
  useEffect(() => {
    if (!job || job.status !== "done" || job.consumed || inflight.current || !resume.current) return;
    store.consume(job.id);
    void resume.current(job.result as T, job);
  }, [jobId, jobStatus, jobConsumed]); // eslint-disable-line react-hooks/exhaustive-deps

  return { busy, blocked, lines: job?.progress ?? [], error, setError: setLocalError, run, job, consume: store.consume };
}

/** Every running job on the server that this tab knows about (any project), oldest first. */
export function useRunningJobs(): TrackedJob[] {
  const store = useJobStore();
  const snap = useSyncExternalStore(store.subscribe, store.snapshot);
  return useMemo(() => snap.jobs.filter((j) => j.status === "running"), [snap]);
}
