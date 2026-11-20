package dev.robode.studio.api;

import dev.robode.studio.common.Json;
import dev.robode.studio.common.StudioException;
import dev.robode.studio.jobs.Job;
import dev.robode.studio.jobs.JobService;
import dev.robode.studio.store.ProjectStore;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import org.springframework.http.HttpHeaders;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

/** What a caller can ask of a job call: wait for the result, get the resource in the record, be called back. */
@Component
class JobApi {
    /** Validated `wait` (seconds, 0 to 600), `expand=resource` and the X-Studio-Callback URL (null when absent). */
    record Options(int waitSeconds, boolean expand, String callback) {}

    private final JobService jobs;
    private final ProjectService projects;
    private final StudioService studio;
    private final ProjectStore store;
    private final CallbackService callbacks;

    JobApi(JobService jobs, ProjectService projects, StudioService studio, ProjectStore store, CallbackService callbacks) {
        this.jobs = jobs;
        this.projects = projects;
        this.studio = studio;
        this.store = store;
        this.callbacks = callbacks;
    }

    /** Checks the parameters before anything starts: 422 for a bad wait, expand or callback. */
    Options options(int wait, String expand, String callback) {
        if (wait < 0 || wait > 600) throw StudioException.value("wait must be from 0 to 600 seconds");
        if (expand != null && !expand.isEmpty() && !expand.equals("resource")) throw StudioException.value("expand must be resource");
        String url = callback == null || callback.isBlank() ? null : callbacks.validate(callback);
        return new Options(wait, "resource".equals(expand), url);
    }

    Options options(int wait, String expand) { return options(wait, expand, null); }

    /** The response of a job-starting POST: the record (final within `wait`), always with Location. */
    ResponseEntity<JsonNode> started(ObjectNode started, Options o) {
        return respond(jobs.job(started.get("id").asString()), o, true);
    }

    /** GET /jobs/{id}. */
    ResponseEntity<JsonNode> get(String id, Options o) { return respond(jobs.job(id), o, false); }

    private ResponseEntity<JsonNode> respond(Job job, Options o, boolean start) {
        if (o.callback() != null) callbacks.register(job, o.callback(), () -> record(job, true));
        boolean finished = !job.running();
        if (!finished && o.waitSeconds() > 0) {
            try {
                job.ended().get(o.waitSeconds(), TimeUnit.SECONDS);
                finished = true;
            } catch (TimeoutException e) {
                // still running: 202 below
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw StudioException.internal("Interrupted while waiting for the job", null);
            } catch (ExecutionException e) {
                throw StudioException.internal("The job could not be awaited", null);
            }
        }
        boolean waiting = !finished && o.waitSeconds() > 0;
        ResponseEntity.BodyBuilder builder = ResponseEntity.status(waiting ? 202 : 200);
        if (start || waiting) builder.header(HttpHeaders.LOCATION, "/api/v1/jobs/" + job.id());
        return builder.body(record(job, o.expand()));
    }

    /** The job record; a finished one gets `resource` when asked. */
    ObjectNode record(Job job, boolean expand) {
        ObjectNode out = job.json();
        if (expand && "done".equals(Json.text(out, "status"))) {
            try {
                JsonNode resource = resource(job, out);
                if (resource != null) out.set("resource", resource);
            } catch (StudioException e) {
                // the project or version is gone meanwhile: the record stays without a resource
            }
        }
        return out;
    }

    private JsonNode resource(Job job, ObjectNode record) {
        String pid = job.robotId();
        JsonNode result = record.get("result");
        if (pid == null || result == null || !result.isObject()) return null;
        return switch (job.kind()) {
            case "design" -> {
                if (!"questions".equals(Json.text(result, "status"))) yield projects.versionView(pid, result.path("version").asInt());
                ObjectNode q = Json.obj();
                q.set("questions", result.get("questions"));
                q.put("round", store.meta(pid).path("round").asInt(0));
                yield q;
            }
            case "revise", "fleet_design" -> projects.versionView(pid, result.path("version").asInt());
            case "search" -> store.candidates(pid);
            case "analyse" -> job.subject() == null ? null : projects.analysisView(pid, job.subject());
            case "studio_build", "studio_pdf" -> studio.buildView(pid, result.path("build").asInt());
            default -> null;
        };
    }
}
