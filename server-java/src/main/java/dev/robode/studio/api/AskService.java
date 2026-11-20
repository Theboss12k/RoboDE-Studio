package dev.robode.studio.api;

import dev.robode.studio.common.Json;
import dev.robode.studio.common.StudioException;
import dev.robode.studio.jobs.Job;
import dev.robode.studio.jobs.JobService;
import dev.robode.studio.jobs.TaskResult;
import dev.robode.studio.jobs.TaskRunner;
import dev.robode.studio.llm.LlmSettings;
import dev.robode.studio.store.ProjectStore;
import dev.robode.studio.store.StudioStore;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * The Ask bar (POST /ask). The Python `ask` task answers from the grounding and PROPOSES actions; it performs nothing.
 * After the job's result arrives, this class validates each proposal and executes it through the same controller methods
 * the REST routes use (a method call, never HTTP), so every proposal is validated exactly as the route validates it.
 * Approvals and decisions are never executed: they come back as confirm chips for the user to click.
 */
@Component
class AskService {
    private static final Logger LOG = LoggerFactory.getLogger(AskService.class);
    static final int MAX_ACTIONS = 2;
    static final int MAX_CONFIRM = 3;
    static final int MAX_HISTORY = 6;
    private static final Set<String> CITATION_KINDS = Set.of("version", "check", "requirement", "build", "motor", "candidate", "brief", "spec");
    private static final Set<String> CONFIRM_KINDS = Set.of("approve", "approve_build", "decision");

    private final ProjectStore store;
    private final StudioStore studio;
    private final JobService jobs;
    private final TaskRunner runner;
    private final LlmSettings llm;
    private final AskGrounding grounding;
    private final ApiController api;
    private final StudioController studioApi;

    AskService(ProjectStore store, StudioStore studio, JobService jobs, TaskRunner runner, LlmSettings llm, AskGrounding grounding,
               ApiController api, StudioController studioApi) {
        this.store = store;
        this.studio = studio;
        this.jobs = jobs;
        this.runner = runner;
        this.llm = llm;
        this.grounding = grounding;
        this.api = api;
        this.studioApi = studioApi;
    }

    // -- request ------------------------------------------------------------------------------------------------
    private record Parsed(String question, ArrayNode history, AskGrounding.Context context) {}

    private static String string(JsonNode body, String field, int min, int max, String where) {
        JsonNode v = body.get(field);
        if (v == null || v.isNull() || !v.isString()) throw StudioException.value(where + field + " must be a string of " + min + " to " + max + " characters");
        String text = v.asString();
        if (text.strip().length() < min || text.length() > max) throw StudioException.value(where + field + " must be a string of " + min + " to " + max + " characters");
        return text;
    }

    private static Integer positive(JsonNode ctx, String field) {
        JsonNode v = ctx.get(field);
        if (v == null || v.isNull()) return null;
        if (!v.isIntegralNumber() || !v.canConvertToInt() || v.asInt() < 1) throw StudioException.value("context." + field + " must be a positive integer");
        return v.asInt();
    }

    private static String optionalText(JsonNode ctx, String field, int max) {
        JsonNode v = ctx.get(field);
        if (v == null || v.isNull()) return null;
        if (!v.isString() || v.asString().length() > max || v.asString().isBlank())
            throw StudioException.value("context." + field + " must be a string of 1 to " + max + " characters");
        return v.asString();
    }

    private Parsed parse(JsonNode body) {
        if (body == null || !body.isObject()) throw StudioException.value("question is required");
        String question = string(body, "question", 1, 2000, "");
        ArrayNode history = Json.arr();
        JsonNode h = body.get("history");
        if (h != null && !h.isNull()) {
            if (!h.isArray()) throw StudioException.value("history must be an array");
            for (JsonNode turn : h) {
                String role = turn.isObject() ? Json.text(turn, "role") : null;
                if (!"user".equals(role) && !"assistant".equals(role)) throw StudioException.value("history[].role must be user or assistant");
                history.add(Json.obj().put("role", role).put("text", string(turn, "text", 0, 4000, "history[].")));
            }
            while (history.size() > MAX_HISTORY) history.remove(0);   // older turns are dropped
        }
        JsonNode c = body.get("context");
        if (c != null && !c.isNull() && !c.isObject()) throw StudioException.value("context must be an object");
        JsonNode ctx = c == null || c.isNull() ? Json.obj() : c;
        Double t = null;
        if (ctx.hasNonNull("t")) {
            if (!ctx.get("t").isNumber() || ctx.get("t").asDouble() < 0 || !Double.isFinite(ctx.get("t").asDouble()))
                throw StudioException.value("context.t must be a number of 0 or more");
            t = ctx.get("t").asDouble();
        }
        AskGrounding.Context context = new AskGrounding.Context(optionalText(ctx, "project_id", 40), optionalText(ctx, "stage", 40),
                positive(ctx, "version"), t, optionalText(ctx, "candidate", 120), positive(ctx, "build"), optionalText(ctx, "selection", 200));
        return new Parsed(question, history, context);
    }

    // -- the job ------------------------------------------------------------------------------------------------
    /** Validates the body (422) and the referenced ids (404), then starts the `ask` job. */
    ObjectNode start(JsonNode body) {
        Parsed p = parse(body);
        grounding.check(p.context());
        return jobs.start("ask", p.context().projectId(), job -> {
            ObjectNode input = Json.obj();
            input.put("question", p.question());
            input.set("history", p.history());
            input.set("context", contextJson(p.context()));
            input.set("grounding", grounding.build(p.context()));   // numbers come from storage, never from the client
            try (TaskResult r = runner.run(job.id(), "ask", input, llm.resolveRoles(), p.context().projectId(), "ask", job.listener())) {
                return finish(job, p.context(), r.data());
            }
        });
    }

    private static ObjectNode contextJson(AskGrounding.Context c) {
        ObjectNode out = Json.obj();
        if (c.projectId() != null) out.put("project_id", c.projectId());
        if (c.stage() != null) out.put("stage", c.stage());
        if (c.version() != null) out.put("version", c.version());
        if (c.t() != null) out.put("t", c.t());
        if (c.candidate() != null) out.put("candidate", c.candidate());
        if (c.build() != null) out.put("build", c.build());
        if (c.selection() != null) out.put("selection", c.selection());
        return out;
    }

    /** Executes the proposals and builds the AskResponse. */
    private ObjectNode finish(Job job, AskGrounding.Context c, JsonNode data) {
        ObjectNode out = Json.obj();
        String answer = Json.text(data, "answer");
        out.put("answer", answer == null ? "" : answer);
        out.set("citations", citations(data.get("citations")));
        ArrayNode taken = Json.arr();
        JsonNode proposals = data.get("proposals");
        if (proposals != null && proposals.isArray()) {
            int index = 0;
            for (JsonNode proposal : proposals) {
                index++;
                String kind = proposal.isObject() && proposal.path("kind").isString() ? proposal.get("kind").asString() : "unknown";
                if (kind.length() > 40) kind = kind.substring(0, 40);
                ObjectNode row;
                if (index > MAX_ACTIONS) row = refused(kind, "limit: " + MAX_ACTIONS + " actions per question");
                else if (!job.running()) row = refused(kind, "the question was cancelled");
                else row = execute(c, kind, proposal.path("args"));
                log(c, row);
                taken.add(row);
            }
        }
        out.set("actions_taken", taken);
        out.set("confirm", confirm(c, data.get("confirm")));
        JsonNode nav = navigate(data.get("navigate"));
        if (nav == null) out.putNull("navigate"); else out.set("navigate", nav);
        return out;
    }

    private void log(AskGrounding.Context c, ObjectNode row) {
        if (c.projectId() == null) return;
        try {
            store.appendActivity(c.projectId(), "ask", row.get("kind").asString(), row.get("ok").asBoolean(),
                    row.has("job_id") ? row.get("job_id").asString() : null, row.get("summary").asString());
        } catch (StudioException e) {
            LOG.warn("Could not log the ask action to the activity of {}: {}", c.projectId(), e.getMessage());
        }
    }

    // -- actions ------------------------------------------------------------------------------------------------
    private static ObjectNode refused(String kind, String error) {
        return Json.obj().put("kind", kind).put("ok", false).put("summary", "Not done: " + kind).put("error", error);
    }

    private record Done(String jobId, String summary) {}

    private ObjectNode execute(AskGrounding.Context c, String kind, JsonNode args) {
        if (!ALLOWED.contains(kind)) return refused(kind, "not allowed");
        if (c.projectId() == null) return refused(kind, "this action needs a project: open one first");
        try {
            Done done = run(c.projectId(), c, kind, args.isObject() ? args : Json.obj());
            ObjectNode row = Json.obj().put("kind", kind).put("ok", true);
            if (done.jobId() != null) row.put("job_id", done.jobId());
            return row.put("summary", done.summary());
        } catch (StudioException e) {
            return refused(kind, e.getMessage()).put("summary", "Not done: " + attempt(kind, args));
        } catch (RuntimeException e) {
            LOG.error("Ask action {} failed", kind, e);
            return refused(kind, e.getClass().getSimpleName()).put("summary", "Not done: " + attempt(kind, args));
        }
    }

    static final Set<String> ALLOWED = Set.of("revise", "design", "search", "analyse", "studio_build", "update_brief", "set_baseline", "cancel_job");

    private static String clip(String text, int max) {
        String one = text == null ? "" : text.replaceAll("\\s+", " ").strip();
        return one.length() > max ? one.substring(0, max - 1) + "…" : one;
    }

    /** What the action was asked to do, in words (for the refusal summary). */
    private static String attempt(String kind, JsonNode args) {
        return switch (kind) {
            case "revise" -> "change request \"" + clip(Json.text(args, "request"), 100) + "\"";
            case "design" -> "design round";
            case "search" -> "robot search";
            case "analyse" -> "analysis of " + clip(Json.text(args, "candidate"), 60);
            case "studio_build" -> "robot studio build";
            case "update_brief" -> "brief update";
            case "set_baseline" -> "time-saved inputs update";
            case "cancel_job" -> "cancelling job " + clip(Json.text(args, "id"), 40);
            default -> kind;
        };
    }

    private static ObjectNode copy(JsonNode args, String... keys) {
        ObjectNode body = Json.obj();
        for (String k : keys) if (args.has(k) && !args.get(k).isNull()) body.set(k, args.get(k));
        return body;
    }

    private static String jobId(ResponseEntity<JsonNode> response) {
        JsonNode record = response.getBody();
        return record == null ? null : Json.text(record, "id");
    }

    /** Runs one allowed action through the route's own method; the route's validation errors are StudioExceptions. */
    private Done run(String pid, AskGrounding.Context c, String kind, JsonNode args) {
        switch (kind) {
            case "revise": {
                ObjectNode body = copy(args, "request", "base_version", "t");
                if (!body.has("base_version") && c.version() != null) body.put("base_version", c.version());
                return new Done(jobId(api.revise(pid, body, 0, null, null)), "Started a change request: \"" + clip(Json.text(body, "request"), 100) + "\"");
            }
            case "design":
                return new Done(jobId(api.design(pid, copy(args, "answers", "comments"), 0, null, null)), "Started a design round");
            case "search":
                return new Done(jobId(api.search(pid, 0, null, null)), "Started a robot search");
            case "analyse": {
                String cid = args.path("candidate").isString() ? args.get("candidate").asString() : null;
                if (cid == null || cid.isBlank()) throw StudioException.value("candidate is required");
                return new Done(jobId(api.analyse(pid, cid, Json.obj(), 0, null, null)), "Started the analysis of " + clip(cid, 60));
            }
            case "studio_build":
                return new Done(jobId(studioApi.startBuild(pid, copy(args, "request", "pinned"), 0, null, null)), "Started a robot studio build");
            case "update_brief": {
                JsonNode text = args.get("text");
                api.putBrief(pid, Json.obj().set("brief", text == null ? Json.MAPPER.nullNode() : text));
                return new Done(null, "Replaced the project brief");
            }
            case "set_baseline": {
                api.putBaseline(pid, copy(args, "manual_cycle_s", "operators", "hours_per_shift", "days_per_week", "days_per_year", "supervision_min_per_shift"));
                return new Done(null, "Updated the time-saved inputs");
            }
            case "cancel_job": {
                String id = args.path("id").isString() ? args.get("id").asString() : null;
                if (id == null || id.isBlank()) throw StudioException.value("id is required");
                Job target = jobs.job(id);
                if (!pid.equals(target.robotId())) throw StudioException.value("That job does not belong to this project");
                jobs.cancel(id);
                return new Done(id, "Cancelled job " + id);
            }
            default:
                throw StudioException.value("not allowed");
        }
    }

    // -- the rest of the response -------------------------------------------------------------------------------
    private static ArrayNode citations(JsonNode in) {
        ArrayNode out = Json.arr();
        if (in == null || !in.isArray()) return out;
        for (JsonNode c : in) {
            String kind = c.path("kind").isString() ? c.get("kind").asString() : null;
            if (kind == null || !CITATION_KINDS.contains(kind) || !c.path("ref").isString() || !c.path("label").isString()) continue;
            out.add(Json.obj().put("kind", kind).put("ref", clip(c.get("ref").asString(), 200)).put("label", clip(c.get("label").asString(), 200)));
        }
        return out;
    }

    /** Confirm chips are never executed. Each must match the body of the existing route and point at something that exists. */
    private ArrayNode confirm(AskGrounding.Context c, JsonNode in) {
        ArrayNode out = Json.arr();
        if (in == null || !in.isArray() || c.projectId() == null) return out;
        for (JsonNode chip : in) {
            if (out.size() >= MAX_CONFIRM) break;
            String kind = chip.path("kind").isString() ? chip.get("kind").asString() : null;
            if (kind == null || !CONFIRM_KINDS.contains(kind) || !chip.path("label").isString()) continue;
            JsonNode request = chip.path("request");
            ObjectNode body = Json.obj();
            try {
                switch (kind) {
                    case "approve" -> {
                        if (!request.path("version").isIntegralNumber() || !store.versions(c.projectId()).contains(request.get("version").asInt())) continue;
                        body.put("version", request.get("version").asInt());
                    }
                    case "approve_build" -> {
                        int build = request.path("build").isIntegralNumber() ? request.get("build").asInt() : (c.build() == null ? 0 : c.build());
                        if (build < 1 || !studio.builds(c.projectId()).contains(build)) continue;
                        body.put("build", build);   // goes into the path: POST /projects/{pid}/studio/builds/{build}/approve with an empty body
                    }
                    default -> {
                        String action = request.path("action").isString() ? request.get("action").asString() : "";
                        if (action.equals("self")) {
                            body.put("action", "self");
                            if (request.hasNonNull("build")) {
                                if (!request.get("build").isIntegralNumber() || !studio.builds(c.projectId()).contains(request.get("build").asInt())) continue;
                                body.put("build", request.get("build").asInt());
                            }
                        } else if ((action.equals("approve") || action.equals("robot_studio")) && request.path("candidate_id").isString()) {
                            store.candidate(c.projectId(), request.get("candidate_id").asString());   // 404 when unknown
                            body.put("action", action).put("candidate_id", request.get("candidate_id").asString());
                        } else {
                            continue;
                        }
                    }
                }
            } catch (StudioException e) {
                continue;
            }
            out.add(Json.obj().put("kind", kind).put("label", clip(chip.get("label").asString(), 200)).set("request", body));
        }
        return out;
    }

    private static JsonNode navigate(JsonNode in) {
        if (in == null || !in.isObject() || !in.path("stage").isString() || in.get("stage").asString().isBlank() || in.get("stage").asString().length() > 40) return null;
        ObjectNode out = Json.obj().put("stage", in.get("stage").asString());
        for (String k : List.of("version", "build"))
            if (in.path(k).isIntegralNumber() && in.get(k).asInt() >= 1) out.put(k, in.get(k).asInt());
        if (in.path("candidate").isString() && !in.get("candidate").asString().isBlank() && in.get("candidate").asString().length() <= 120)
            out.put("candidate", in.get("candidate").asString());
        return out;
    }
}
