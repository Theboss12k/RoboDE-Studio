package dev.robode.studio.api;

import dev.robode.studio.common.Json;
import dev.robode.studio.common.StudioException;
import dev.robode.studio.config.BodyLimitFilter;
import dev.robode.studio.jobs.JobService;
import dev.robode.studio.jobs.TaskResult;
import dev.robode.studio.jobs.TaskRunner;
import dev.robode.studio.llm.LlmSettings;
import dev.robode.studio.llm.UsageLedger;
import dev.robode.studio.meta.MetaService;
import dev.robode.studio.store.ProjectStore;
import jakarta.servlet.http.HttpServletRequest;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.UUID;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/** Every route of the former FastAPI app, under /api/v1, with the same bodies. */
@RestController
@RequestMapping("/api/v1")
class ApiController {
    private final ProjectStore store;
    private final ProjectService projects;
    private final LlmSettings llm;
    private final UsageLedger usage;
    private final JobService jobs;
    private final MetaService meta;
    private final TaskRunner runner;
    private final JobApi jobApi;

    ApiController(ProjectStore store, ProjectService projects, LlmSettings llm, UsageLedger usage, JobService jobs,
                  MetaService meta, TaskRunner runner, JobApi jobApi) {
        this.store = store;
        this.projects = projects;
        this.llm = llm;
        this.usage = usage;
        this.jobs = jobs;
        this.meta = meta;
        this.runner = runner;
        this.jobApi = jobApi;
    }

    // -- jobs, health, families, LLM settings -------------------------------------------------------------------
    @GetMapping("/jobs/{id}")
    ResponseEntity<JsonNode> job(@PathVariable String id, @RequestParam(defaultValue = "0") int wait,
                                 @RequestParam(required = false) String expand) {
        return jobApi.get(id, jobApi.options(wait, expand));
    }

    @DeleteMapping("/jobs/{id}")
    JsonNode cancelJob(@PathVariable String id) { return jobs.cancel(id); }

    @GetMapping("/projects/{pid}/jobs")
    JsonNode projectJobs(@PathVariable String pid) {
        store.require(pid);
        ArrayNode out = Json.arr();
        jobs.list(pid).forEach(out::add);
        return out;
    }

    @GetMapping("/jobs")
    JsonNode runningJobs(@RequestParam(defaultValue = "true") boolean running) {
        ArrayNode out = Json.arr();
        jobs.running().forEach(out::add);
        return out;
    }

    @GetMapping("/health")
    JsonNode health() { return healthBody(); }

    private ObjectNode healthBody() {
        ObjectNode out = Json.obj();
        out.put("ok", true);
        out.set("llm", llm.roles());
        return out;
    }

    @GetMapping("/families")
    JsonNode families() { return meta.families(); }

    @GetMapping("/llm")
    JsonNode llmSettings() { return llm.describe(); }

    @PostMapping("/llm/profiles")
    JsonNode addProfile(@RequestBody(required = false) JsonNode body) { return llm.upsert(body, null); }

    @PutMapping("/llm/profiles/{id}")
    JsonNode editProfile(@PathVariable String id, @RequestBody(required = false) JsonNode body) {
        llm.profile(id);
        return llm.upsert(body, id);
    }

    @DeleteMapping("/llm/profiles/{id}")
    JsonNode deleteProfile(@PathVariable String id) {
        llm.delete(id);
        return llm.describe();
    }

    @PutMapping("/llm/roles")
    JsonNode setRoles(@RequestBody(required = false) JsonNode body) {
        if (body == null || !body.has("roles")) throw StudioException.value("roles is required");
        return llm.setRoles(body.get("roles"));
    }

    @GetMapping({"/llm/usage", "/usage"})
    JsonNode llmUsage(@RequestParam(defaultValue = "7d") String period) { return usage.summary(period); }

    @PostMapping("/llm/usage/reset")
    JsonNode usageReset() {
        usage.reset();
        return usage.summary("all");
    }

    @PutMapping("/llm/keys/{provider}")
    JsonNode saveKey(@PathVariable String provider, @RequestBody(required = false) JsonNode body) {
        String key = body == null ? null : Json.text(body, "key");
        if (key == null || key.isEmpty() || key.length() > 400) throw StudioException.value("key must be 1 to 400 characters");
        llm.keys().set(provider, key);
        return llm.keys().status();
    }

    @DeleteMapping("/llm/keys/{provider}")
    JsonNode deleteKey(@PathVariable String provider) {
        llm.keys().delete(provider);
        return llm.keys().status();
    }

    @PostMapping("/llm/profiles/{id}/test")
    ResponseEntity<JsonNode> testProfile(@PathVariable String id, @RequestParam(defaultValue = "0") int wait, @RequestParam(required = false) String expand, @RequestHeader(value = "X-Studio-Callback", required = false) String callback) {
        JobApi.Options options = jobApi.options(wait, expand, callback);
        ObjectNode envelope = llm.forProfile(id); // 404 when the profile does not exist
        return jobApi.started(jobs.start("llm_test", null, job -> {
            try (TaskResult r = runner.run(job.id(), "llm_test", Json.obj(), envelope, null, "llm_test", job.listener())) {
                return r.data();
            }
        }), options);
    }

    // -- projects -----------------------------------------------------------------------------------------------
    @GetMapping("/projects")
    JsonNode listProjects() { return store.list(); }

    @PostMapping("/projects")
    JsonNode createProject(@RequestBody(required = false) JsonNode body) {
        String brief = body == null ? null : Json.text(body, "brief");
        if (brief == null || brief.isEmpty() || brief.length() > 8000) throw StudioException.value("brief must be 1 to 8000 characters");
        return store.create(brief);
    }

    @GetMapping("/projects/{pid}")
    JsonNode getProject(@PathVariable String pid) {
        store.require(pid);
        return store.view(pid);
    }

    @DeleteMapping("/projects/{pid}")
    JsonNode deleteProject(@PathVariable String pid) {
        store.delete(pid);
        return Json.obj().put("ok", true);
    }

    @PutMapping("/projects/{pid}/reference")
    JsonNode putReference(@PathVariable String pid, @RequestBody(required = false) JsonNode body) {
        String text = body == null || body.isNull() ? "" : Json.text(body, "text");
        if (text != null && text.length() > ProjectStore.MAX_TEXT) throw StudioException.value("text must be at most 60000 characters");
        store.require(pid);
        store.setReferenceText(pid, text);
        return store.view(pid);
    }

    @PutMapping("/projects/{pid}/brief")
    JsonNode putBrief(@PathVariable String pid, @RequestBody(required = false) JsonNode body) {
        JsonNode brief = body == null ? null : body.get("brief");
        if (brief == null || !brief.isString()) throw StudioException.value("brief must be a string of 10 to 20000 characters");
        return projects.updateBrief(pid, brief.asString());
    }

    // -- brief images -------------------------------------------------------------------------------------------
    @PostMapping("/projects/{pid}/brief/files")
    JsonNode uploadBriefImage(@PathVariable String pid, @RequestParam String filename, HttpServletRequest request) {
        store.require(pid);
        return projects.uploadBriefImage(pid, filename, body(request));
    }

    @DeleteMapping("/projects/{pid}/brief/files/{name}")
    JsonNode deleteBriefImage(@PathVariable String pid, @PathVariable String name) {
        store.require(pid);
        store.removeBriefImage(pid, dev.robode.studio.common.Ids.safeName(name));
        return store.briefImages(pid);
    }

    @GetMapping("/projects/{pid}/brief/files/{name}")
    ResponseEntity<byte[]> briefImage(@PathVariable String pid, @PathVariable String name) {
        store.require(pid);
        JsonNode entry = store.briefImage(pid, dev.robode.studio.common.Ids.safeName(name));
        try {
            return ResponseEntity.ok().contentType(MediaType.parseMediaType(Json.text(entry, "mime")))
                    .body(Files.readAllBytes(store.briefImagePath(pid, Json.text(entry, "name"))));
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    @PostMapping("/projects/{pid}/reference/files")
    JsonNode uploadReference(@PathVariable String pid, @RequestParam String filename, HttpServletRequest request) {
        store.require(pid);
        return projects.uploadReference(pid, filename, body(request));
    }

    @DeleteMapping("/projects/{pid}/reference")
    JsonNode clearReference(@PathVariable String pid) {
        store.require(pid);
        store.clearReference(pid);
        return store.view(pid);
    }

    // -- design -------------------------------------------------------------------------------------------------
    @PostMapping("/projects/{pid}/design")
    ResponseEntity<JsonNode> design(@PathVariable String pid, @RequestBody(required = false) JsonNode body, @RequestParam(defaultValue = "0") int wait, @RequestParam(required = false) String expand, @RequestHeader(value = "X-Studio-Callback", required = false) String callback) {
        JobApi.Options options = jobApi.options(wait, expand, callback);
        JsonNode answers = body == null ? null : body.get("answers");
        if (answers != null && !answers.isNull() && !answers.isObject()) throw StudioException.value("answers must be an object");
        JsonNode comments = body == null ? null : body.get("comments");
        if (comments != null && !comments.isNull()) {
            if (!comments.isObject()) throw StudioException.value("comments must be an object");
            for (var e : comments.properties()) {
                if (!COMMENT_KEY.matcher(e.getKey()).matches()) throw StudioException.value("comments has an invalid question id: " + e.getKey());
                if (!e.getValue().isString() || e.getValue().asString().length() > 1000)
                    throw StudioException.value("each comment must be a string of at most 1000 characters");
            }
        }
        JsonNode note = body == null ? null : body.get("note");
        if (note != null && !note.isNull() && (!note.isString() || note.asString().length() > 4000))
            throw StudioException.value("note must be a string of at most 4000 characters");
        return jobApi.started(projects.design(pid, answers, comments, note == null || note.isNull() ? null : note.asString(), autoRepair(body)), options);
    }

    private static final java.util.regex.Pattern COMMENT_KEY = java.util.regex.Pattern.compile("^[a-z][a-z0-9_]{0,40}$");

    /** The optional auto_repair flag of design and revise bodies; null when absent. */
    private static Boolean autoRepair(JsonNode body) {
        JsonNode v = body == null ? null : body.get("auto_repair");
        if (v == null || v.isNull()) return null;
        if (!v.isBoolean()) throw StudioException.value("auto_repair must be a boolean");
        return v.asBoolean();
    }

    // -- answer images ------------------------------------------------------------------------------------------
    @PostMapping("/projects/{pid}/answers/files")
    JsonNode uploadAnswerImage(@PathVariable String pid, @RequestParam String filename, @RequestParam String question, HttpServletRequest request) {
        store.require(pid);
        return projects.uploadAnswerImage(pid, filename, question, body(request));
    }

    @DeleteMapping("/projects/{pid}/answers/files/{name}")
    JsonNode deleteAnswerImage(@PathVariable String pid, @PathVariable String name) {
        store.require(pid);
        store.removeAnswerImage(pid, dev.robode.studio.common.Ids.safeName(name));
        return store.answerImages(pid);
    }

    @GetMapping("/projects/{pid}/answers/files/{name}")
    ResponseEntity<byte[]> answerImage(@PathVariable String pid, @PathVariable String name) {
        store.require(pid);
        JsonNode entry = store.answerImage(pid, dev.robode.studio.common.Ids.safeName(name));
        try {
            return ResponseEntity.ok().contentType(MediaType.parseMediaType(Json.text(entry, "mime")))
                    .body(Files.readAllBytes(store.answerImagePath(pid, Json.text(entry, "name"))));
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    @GetMapping("/projects/{pid}/versions/{n}")
    JsonNode version(@PathVariable String pid, @PathVariable int n) { return projects.versionView(pid, n); }

    @GetMapping("/projects/{pid}/versions/{n}/assets/{asset}")
    ResponseEntity<?> asset(@PathVariable String pid, @PathVariable int n, @PathVariable String asset) {
        store.require(pid);
        ObjectNode version = store.version(pid, n);
        return switch (asset) {
            case "design.urdf" -> ResponseEntity.ok().contentType(MediaType.APPLICATION_XML)
                    .body(Json.text(version, "urdf").getBytes(StandardCharsets.UTF_8));
            case "frames.json" -> ResponseEntity.ok().contentType(MediaType.APPLICATION_JSON).body(version.path("sim").path("frames"));
            case "design.glb" -> {
                try {
                    yield ResponseEntity.ok().contentType(MediaType.parseMediaType("model/gltf-binary"))
                            .body(Files.readAllBytes(projects.glb(pid, n)));
                } catch (IOException e) {
                    throw new UncheckedIOException(e);
                }
            }
            default -> throw StudioException.notFound("Not found");
        };
    }

    @PostMapping("/projects/{pid}/revise")
    ResponseEntity<JsonNode> revise(@PathVariable String pid, @RequestBody(required = false) JsonNode body, @RequestParam(defaultValue = "0") int wait, @RequestParam(required = false) String expand, @RequestHeader(value = "X-Studio-Callback", required = false) String callback) {
        JobApi.Options options = jobApi.options(wait, expand, callback);
        if (body == null || !body.isObject()) throw StudioException.value("request and base_version are required");
        JsonNode base = body.get("base_version");
        if (base != null && !base.isNull() && !base.canConvertToInt()) throw StudioException.value("base_version must be an integer");
        return jobApi.started(projects.revise(pid, Json.text(body, "request"), base == null || base.isNull() ? null : base.asInt(), body.get("t"), autoRepair(body)), options);
    }

    // -- fleet --------------------------------------------------------------------------------------------------
    @PutMapping("/projects/{pid}/fleet")
    JsonNode putFleet(@PathVariable String pid, @RequestBody(required = false) JsonNode body) {
        if (body == null || !body.isObject()) throw StudioException.value("enabled, limits or member are required");
        Boolean enabled = null;
        if (body.hasNonNull("enabled")) {
            if (!body.get("enabled").isBoolean()) throw StudioException.value("enabled must be a boolean");
            enabled = body.get("enabled").asBoolean();
        }
        boolean hasLimits = body.has("limits");
        JsonNode limits = hasLimits && !body.get("limits").isNull() ? fleetLimits(body.get("limits")) : null;
        boolean hasMember = body.has("member");
        String member = null;
        if (hasMember && !body.get("member").isNull()) {
            JsonNode m = body.get("member");
            if (!m.isString() || m.asString().isBlank() || m.asString().length() > 40) throw StudioException.value("member must be a robot id or null");
            member = m.asString();
        }
        return projects.setFleet(pid, enabled, hasLimits, limits, hasMember, member);
    }

    private static ObjectNode fleetLimits(JsonNode in) {
        if (!in.isObject()) throw StudioException.value("limits must be an object");
        ObjectNode out = Json.obj();
        for (var e : in.properties()) {
            String k = e.getKey();
            JsonNode v = e.getValue();
            if (v.isNull()) { if (FLEET_LIMIT_KEYS.contains(k)) out.putNull(k); continue; }
            switch (k) {
                case "max_robots" -> {
                    if (!v.isIntegralNumber() || v.asInt() < 2 || v.asInt() > 3) throw StudioException.value("limits.max_robots must be an integer from 2 to 3");
                }
                case "max_reach_m" -> {
                    if (!v.isNumber() || v.asDouble() <= 0 || v.asDouble() > 10) throw StudioException.value("limits.max_reach_m must be above 0 and at most 10");
                }
                case "max_payload_kg" -> {
                    if (!v.isNumber() || v.asDouble() <= 0 || v.asDouble() > 2000) throw StudioException.value("limits.max_payload_kg must be above 0 and at most 2000");
                }
                case "budget_usd" -> {
                    if (!v.isNumber() || v.asDouble() < 0) throw StudioException.value("limits.budget_usd must be 0 or more");
                }
                case "families", "brands" -> {
                    if (!v.isArray()) throw StudioException.value("limits." + k + " must be an array of strings");
                    for (JsonNode x : v) if (!x.isString()) throw StudioException.value("limits." + k + " must be an array of strings");
                }
                case "note" -> {
                    if (!v.isString() || v.asString().length() > 1000) throw StudioException.value("limits.note must be a string of at most 1000 characters");
                }
                default -> { continue; }
            }
            out.set(k, v);
        }
        return out;
    }

    private static final java.util.Set<String> FLEET_LIMIT_KEYS =
            java.util.Set.of("max_robots", "max_reach_m", "max_payload_kg", "budget_usd", "families", "brands", "note");

    @PostMapping("/projects/{pid}/fleet/assign")
    JsonNode fleetAssign(@PathVariable String pid, @RequestBody(required = false) JsonNode body) {
        JsonNode base = body == null ? null : body.get("base_version");
        if (base != null && !base.isNull() && !base.canConvertToInt()) throw StudioException.value("base_version must be an integer");
        JsonNode assignment = body == null ? null : body.get("assignment");
        if (assignment != null && !assignment.isNull() && !assignment.isObject()) throw StudioException.value("assignment must be an object");
        return projects.fleetAssign(pid, base == null || base.isNull() ? null : base.asInt(), assignment);
    }

    @PostMapping("/projects/{pid}/fleet/design")
    ResponseEntity<JsonNode> fleetDesign(@PathVariable String pid, @RequestBody(required = false) JsonNode body, @RequestParam(defaultValue = "0") int wait, @RequestParam(required = false) String expand, @RequestHeader(value = "X-Studio-Callback", required = false) String callback) {
        JobApi.Options options = jobApi.options(wait, expand, callback);
        JsonNode base = body == null ? null : body.get("base_version");
        if (base != null && !base.isNull() && !base.canConvertToInt()) throw StudioException.value("base_version must be an integer");
        return jobApi.started(projects.fleetDesign(pid, base == null || base.isNull() ? null : base.asInt()), options);
    }

    @PostMapping("/projects/{pid}/approve")
    JsonNode approve(@PathVariable String pid, @RequestBody(required = false) JsonNode body) {
        JsonNode v = body == null ? null : body.get("version");
        if (v == null || !v.canConvertToInt()) throw StudioException.value("version must be an integer");
        return projects.approve(pid, v.asInt());
    }

    // -- catalog ------------------------------------------------------------------------------------------------
    @PostMapping("/projects/{pid}/search")
    ResponseEntity<JsonNode> search(@PathVariable String pid, @RequestParam(defaultValue = "0") int wait, @RequestParam(required = false) String expand, @RequestHeader(value = "X-Studio-Callback", required = false) String callback) {
        JobApi.Options options = jobApi.options(wait, expand, callback);
        return jobApi.started(projects.search(pid), options);
    }

    @GetMapping("/projects/{pid}/candidates")
    JsonNode candidates(@PathVariable String pid) {
        store.require(pid);
        return store.candidates(pid);
    }

    @PostMapping("/projects/{pid}/candidates/{cid}/analyse")
    ResponseEntity<JsonNode> analyse(@PathVariable String pid, @PathVariable String cid, @RequestBody(required = false) JsonNode body, @RequestParam(defaultValue = "0") int wait, @RequestParam(required = false) String expand, @RequestHeader(value = "X-Studio-Callback", required = false) String callback) {
        JobApi.Options options = jobApi.options(wait, expand, callback);
        String url = body == null || body.isNull() ? null : Json.text(body, "datasheet_url");
        if (url != null && url.length() > 800) throw StudioException.value("datasheet_url must be at most 800 characters");
        return jobApi.started(projects.analyse(pid, cid, url), options);
    }

    @PostMapping("/projects/{pid}/candidates/{cid}/datasheet")
    JsonNode datasheet(@PathVariable String pid, @PathVariable String cid, @RequestParam String filename, HttpServletRequest request) {
        store.require(pid);
        store.candidate(pid, cid);
        return projects.uploadDatasheet(pid, cid, filename, body(request));
    }

    @GetMapping("/projects/{pid}/analysis/{cid}")
    JsonNode analysis(@PathVariable String pid, @PathVariable String cid) { return projects.analysisView(pid, cid); }

    @PostMapping("/projects/{pid}/decision")
    JsonNode decision(@PathVariable String pid, @RequestBody(required = false) JsonNode body) {
        String cid = body == null ? null : Json.text(body, "candidate_id");
        String action = body == null ? null : Json.text(body, "action");
        if (cid == null || action == null || !(action.equals("approve") || action.equals("robot_studio")))
            throw StudioException.value("candidate_id and action (approve or robot_studio) are required");
        store.require(pid);
        store.setDecision(pid, cid, action);
        return store.view(pid);
    }

    // -- export -------------------------------------------------------------------------------------------------
    @GetMapping("/projects/{pid}/export")
    ResponseEntity<byte[]> export(@PathVariable String pid) {
        ProjectService.Export zip = projects.export(pid);
        return ResponseEntity.ok().contentType(MediaType.parseMediaType("application/zip"))
                .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename=\"" + zip.filename() + "\"").body(zip.bytes());
    }

    private static byte[] body(HttpServletRequest request) {
        try {
            byte[] payload = request.getInputStream().readNBytes((int) BodyLimitFilter.MAX_BODY + 1);
            if (payload.length > BodyLimitFilter.MAX_BODY) throw StudioException.tooLarge("The upload is too large (64 MB at most)");
            return payload;
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }
}
