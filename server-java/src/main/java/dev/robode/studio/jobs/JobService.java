package dev.robode.studio.jobs;

import dev.robode.studio.common.StudioException;
import dev.robode.studio.llm.UsageLedger;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.Map;
import java.util.Set;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

/** Runs long work in the background (virtual threads); POST returns the record, clients poll GET /jobs/{id}. */
@Component
public class JobService {
    private static final Logger LOG = LoggerFactory.getLogger(JobService.class);
    private static final int KEEP_FINISHED = 500;

    /** The body of a job. */
    @FunctionalInterface
    public interface Work {
        JsonNode run(Job job) throws Exception;
    }

    private final Map<String, Job> jobs = new ConcurrentHashMap<>();
    private final ExecutorService executor = Executors.newVirtualThreadPerTaskExecutor();
    private final UsageLedger ledger;

    public JobService(UsageLedger ledger) { this.ledger = ledger; }

    private static final Set<String> EXCLUSIVE = Set.of("design", "revise", "fleet_design", "apply_scene");
    private static final int LIST_MAX = 20;

    public ObjectNode start(String kind, String projectId, Work work) { return start(kind, projectId, null, work); }

    /**
     * Starts the job and returns its record. A second design/revise/fleet_design on the same project is a 409; a second
     * search (same project) or analyse (same project and subject) joins the running job and returns its record.
     */
    public ObjectNode start(String kind, String projectId, String subject, Work work) {
        Job job;
        synchronized (this) {
            if (projectId != null && EXCLUSIVE.contains(kind)
                    && jobs.values().stream().anyMatch(j -> projectId.equals(j.robotId()) && j.running() && EXCLUSIVE.contains(j.kind())))
                throw StudioException.conflict("This project is still working on another design step");
            if (projectId != null && (kind.equals("search") || kind.equals("analyse"))) {
                Job same = jobs.values().stream().filter(j -> projectId.equals(j.robotId()) && j.running() && j.kind().equals(kind)
                        && (kind.equals("search") || java.util.Objects.equals(subject, j.subject()))).findFirst().orElse(null);
                if (same != null) return same.json();
            }
            job = new Job(UUID.randomUUID().toString().replace("-", "").substring(0, 12), kind, projectId, subject, ledger);
            jobs.put(job.id(), job);
            evict();
        }
        Job running = job;
        executor.execute(() -> {
            try {
                running.done(work.run(running));
            } catch (StudioException e) {
                running.failed(e.getMessage(), e.code());
            } catch (Exception e) {
                LOG.error("Job {} ({}) failed", running.id(), kind, e);
                running.failed(e.getClass().getSimpleName() + ": " + e.getMessage(), null);
            }
        });
        return job.json();
    }

    public ObjectNode get(String id) {
        Job job = jobs.get(id);
        if (job == null) throw StudioException.notFound("Job not found");
        return job.json();
    }

    /** A project's jobs: running first (oldest first), then finished (newest first), at most 20. */
    public List<ObjectNode> list(String projectId) {
        List<Job> mine = jobs.values().stream().filter(j -> projectId.equals(j.robotId())).toList();
        List<Job> out = new java.util.ArrayList<>(mine.stream().filter(Job::running)
                .sorted(java.util.Comparator.comparing((Job j) -> j.json().get("started").asString()).thenComparing(Job::id)).toList());
        out.addAll(mine.stream().filter(j -> !j.running())
                .sorted(java.util.Comparator.comparing((Job j) -> String.valueOf(j.finishedAt())).reversed().thenComparing(Job::id)).toList());
        return out.stream().limit(LIST_MAX).map(Job::summary).toList();
    }

    /** Every running job, any project. */
    public List<ObjectNode> running() {
        return jobs.values().stream().filter(Job::running)
                .sorted(java.util.Comparator.comparing((Job j) -> j.json().get("started").asString()).thenComparing(Job::id))
                .map(Job::summary).toList();
    }

    private void evict() {
        List<Job> finished = jobs.values().stream().filter(j -> !j.running()).sorted(java.util.Comparator.comparing(j -> j.json().get("started").asString())).toList();
        for (int i = 0; i < finished.size() - KEEP_FINISHED; i++) jobs.remove(finished.get(i).id());
    }
}
