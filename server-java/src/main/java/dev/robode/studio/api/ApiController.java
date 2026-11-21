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
import dev.robode.studio.pairing.PairingService;
import dev.robode.studio.store.ProjectStore;
import jakarta.servlet.http.HttpServletRequest;
import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.UUID;
import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
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
    private final PairingService pairing;

    ApiController(ProjectStore store, ProjectService projects, LlmSettings llm, UsageLedger usage, JobService jobs,
                  MetaService meta, TaskRunner runner, PairingService pairing) {
        this.pairing = pairing;
        this.store = store;
        this.projects = projects;
        this.llm = llm;
        this.usage = usage;
        this.jobs = jobs;
        this.meta = meta;
        this.runner = runner;
    }

    // -- jobs, health, families, LLM settings -------------------------------------------------------------------
    @GetMapping("/jobs/{id}")
    JsonNode job(@PathVariable String id) { return jobs.get(id); }

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
    JsonNode testProfile(@PathVariable String id) {
        ObjectNode envelope = llm.forProfile(id); // 404 when the profile does not exist
        return jobs.start("llm_test", null, job -> {
            try (TaskResult r = runner.run(job.id(), "llm_test", Json.obj(), envelope, null, "llm_test", job.listener())) {
                return r.data();
            }
        });
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
    JsonNode design(@PathVariable String pid, @RequestBody(required = false) JsonNode body) {
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
        return projects.design(pid, answers, comments, note == null || note.isNull() ? null : note.asString(), autoRepair(body));
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
    JsonNode revise(@PathVariable String pid, @RequestBody(required = false) JsonNode body) {
        if (body == null || !body.isObject()) throw StudioException.value("request and base_version are required");
        JsonNode base = body.get("base_version");
        if (base != null && !base.isNull() && !base.canConvertToInt()) throw StudioException.value("base_version must be an integer");
        return projects.revise(pid, Json.text(body, "request"), base == null || base.isNull() ? null : base.asInt(), body.get("t"), autoRepair(body));
    }

    @PostMapping("/projects/{pid}/approve")
    JsonNode approve(@PathVariable String pid, @RequestBody(required = false) JsonNode body) {
        JsonNode v = body == null ? null : body.get("version");
        if (v == null || !v.canConvertToInt()) throw StudioException.value("version must be an integer");
        return projects.approve(pid, v.asInt());
    }

    /** The edited site scene as a new version (apply_scene: deterministic, no LLM); refused once the design is approved. */
    @PostMapping("/projects/{pid}/apply-scene")
    JsonNode applyScene(@PathVariable String pid, @RequestBody(required = false) JsonNode body) {
        if (body == null || !body.isObject()) throw StudioException.value("scene is required");
        JsonNode base = body.get("base_version");
        if (base != null && !base.isNull() && !base.canConvertToInt()) throw StudioException.value("base_version must be an integer");
        JsonNode scene = body.get("scene");
        if (scene == null || !scene.isObject()) throw StudioException.value("scene must be an object");
        JsonNode markers = body.get("markers");
        if (markers == null || markers.isNull()) markers = Json.arr();
        else if (!markers.isArray()) throw StudioException.value("markers must be a list");
        JsonNode note = body.get("note");
        if (note != null && !note.isNull() && (!note.isString() || note.asString().length() > 4000))
            throw StudioException.value("note must be a string of at most 4000 characters");
        return projects.applyScene(pid, base == null || base.isNull() ? null : base.asInt(), scene, markers,
                Boolean.TRUE.equals(autoRepair(body)), note == null || note.isNull() ? null : note.asString());
    }

    // -- catalog ------------------------------------------------------------------------------------------------
    @PostMapping("/projects/{pid}/search")
    JsonNode search(@PathVariable String pid) { return projects.search(pid); }

    @GetMapping("/projects/{pid}/candidates")
    JsonNode candidates(@PathVariable String pid) {
        store.require(pid);
        return store.candidates(pid);
    }

    @PostMapping("/projects/{pid}/candidates/{cid}/analyse")
    JsonNode analyse(@PathVariable String pid, @PathVariable String cid, @RequestBody(required = false) JsonNode body) {
        String url = body == null || body.isNull() ? null : Json.text(body, "datasheet_url");
        if (url != null && url.length() > 800) throw StudioException.value("datasheet_url must be at most 800 characters");
        return projects.analyse(pid, cid, url);
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
    // -- photo AR scenes ----------------------------------------------------------------------------------------
    @PostMapping("/projects/{pid}/ar/scenes")
    ResponseEntity<JsonNode> uploadArScene(@PathVariable String pid, @RequestParam String filename, HttpServletRequest request) {
        store.require(pid);
        return ResponseEntity.status(HttpStatus.CREATED).body(projects.uploadArScene(pid, filename, body(request)));
    }

    @GetMapping("/projects/{pid}/ar/scenes")
    JsonNode listArScenes(@PathVariable String pid) {
        store.require(pid);
        return store.listArScenes(pid);
    }

    @GetMapping("/projects/{pid}/ar/scenes/{sid}")
    JsonNode arScene(@PathVariable String pid, @PathVariable String sid) {
        store.require(pid);
        return store.getArScene(pid, sid);
    }

    @GetMapping("/projects/{pid}/ar/scenes/{sid}/photo")
    ResponseEntity<byte[]> arPhoto(@PathVariable String pid, @PathVariable String sid) {
        store.require(pid);
        ProjectStore.ArPhoto photo = store.arPhoto(pid, sid);
        try {
            return ResponseEntity.ok().contentType(MediaType.parseMediaType(photo.contentType())).body(Files.readAllBytes(photo.path()));
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    @PostMapping("/projects/{pid}/ar/scenes/{sid}/calibrate")
    JsonNode calibrateArScene(@PathVariable String pid, @PathVariable String sid, @RequestBody(required = false) JsonNode body) {
        store.require(pid);
        JsonNode reference = body == null ? null : body.get("reference");
        if (reference == null || !reference.isObject()) throw StudioException.value("reference must be an object");
        return projects.calibrateArScene(pid, sid, reference);
    }

    @PostMapping("/projects/{pid}/ar/scenes/{sid}/estimate")
    JsonNode estimateArScene(@PathVariable String pid, @PathVariable String sid) {
        store.require(pid);
        return projects.estimateArScene(pid, sid);
    }

    @PutMapping("/projects/{pid}/ar/scenes/{sid}/placement")
    JsonNode placeArScene(@PathVariable String pid, @PathVariable String sid, @RequestBody(required = false) JsonNode body) {
        store.require(pid);
        return projects.placeArScene(pid, sid, body);
    }

    @DeleteMapping("/projects/{pid}/ar/scenes/{sid}")
    ResponseEntity<Void> deleteArScene(@PathVariable String pid, @PathVariable String sid) {
        store.require(pid);
        store.deleteArScene(pid, sid);
        return ResponseEntity.noContent().build();
    }

    // -- site scans (video AR): the upload streams to disk; the body filter exempts only this POST ----------------
    @PostMapping("/projects/{pid}/scans")
    ResponseEntity<JsonNode> uploadScan(@PathVariable String pid, @RequestParam String filename, HttpServletRequest request) {
        store.require(pid);
        Path staged = projects.stageScan(pid, filename, stream(request), request.getContentLengthLong());
        return ResponseEntity.status(HttpStatus.CREATED).body(projects.uploadScan(pid, filename, staged));
    }

    @GetMapping("/projects/{pid}/scans")
    JsonNode listScans(@PathVariable String pid) {
        store.require(pid);
        return store.listScans(pid);
    }

    @GetMapping("/projects/{pid}/scans/{sid}")
    JsonNode scan(@PathVariable String pid, @PathVariable String sid) {
        store.require(pid);
        return store.getScan(pid, sid);
    }

    @DeleteMapping("/projects/{pid}/scans/{sid}")
    ResponseEntity<Void> deleteScan(@PathVariable String pid, @PathVariable String sid) {
        projects.deleteScan(pid, sid);
        return ResponseEntity.noContent().build();
    }

    /** The video, streamed; Spring answers Range requests (206) for a Resource body. */
    @GetMapping("/projects/{pid}/scans/{sid}/video")
    ResponseEntity<Resource> scanVideo(@PathVariable String pid, @PathVariable String sid) {
        store.require(pid);
        ProjectStore.ScanFile video = store.scanVideo(pid, sid);
        return ResponseEntity.ok().contentType(MediaType.parseMediaType(video.contentType()))
                .header(HttpHeaders.ACCEPT_RANGES, "bytes").body(new FileSystemResource(video.path()));
    }

    @GetMapping("/projects/{pid}/scans/{sid}/mesh")
    ResponseEntity<Resource> scanMesh(@PathVariable String pid, @PathVariable String sid) {
        store.require(pid);
        return ResponseEntity.ok().contentType(MediaType.parseMediaType("model/gltf-binary"))
                .body(new FileSystemResource(store.scanMesh(pid, sid)));
    }

    /** One frame; the index must be digits (anything else is not a frame). */
    @GetMapping("/projects/{pid}/scans/{sid}/frames/{index}")
    ResponseEntity<Resource> scanFrame(@PathVariable String pid, @PathVariable String sid, @PathVariable String index) {
        store.require(pid);
        if (!index.matches("[0-9]{1,6}")) throw StudioException.notFound("Frame not found");
        return ResponseEntity.ok().contentType(MediaType.IMAGE_JPEG)
                .body(new FileSystemResource(store.scanFrame(pid, sid, Integer.parseInt(index))));
    }

    @PostMapping("/projects/{pid}/scans/{sid}/reconstruct")
    JsonNode reconstructScan(@PathVariable String pid, @PathVariable String sid) {
        return projects.reconstructScan(pid, sid);
    }

    @PostMapping("/projects/{pid}/scans/{sid}/scale")
    JsonNode scaleScan(@PathVariable String pid, @PathVariable String sid, @RequestBody(required = false) JsonNode body) {
        if (body != null && !body.isNull() && !body.isObject()) throw StudioException.value("The scale request must be an object");
        return projects.scaleScan(pid, sid, body == null || body.isNull() ? Json.obj() : body);
    }

    @PutMapping("/projects/{pid}/scans/{sid}/floor")
    JsonNode floorScan(@PathVariable String pid, @PathVariable String sid, @RequestBody(required = false) JsonNode body) {
        return projects.floorScan(pid, sid, body);
    }

    @PutMapping("/projects/{pid}/scans/{sid}/placement")
    JsonNode placeScan(@PathVariable String pid, @PathVariable String sid, @RequestBody(required = false) JsonNode body) {
        return projects.placeScan(pid, sid, body);
    }

    // -- link a phone (the phone itself talks to the separate pairing listener, never to /api/v1) --------------
    @PostMapping("/projects/{pid}/ar/pairing")
    JsonNode startArPairing(@PathVariable String pid) {
        store.require(pid);
        return pairing.start(pid);
    }

    @GetMapping("/projects/{pid}/ar/pairing")
    JsonNode arPairing(@PathVariable String pid) {
        store.require(pid);
        JsonNode status = pairing.status(pid);
        if (status == null) throw StudioException.notFound("No phone link is active");
        return status;
    }

    @DeleteMapping("/projects/{pid}/ar/pairing")
    ResponseEntity<Void> endArPairing(@PathVariable String pid) {
        store.require(pid);
        pairing.end(pid);
        return ResponseEntity.noContent().build();
    }

    @GetMapping("/projects/{pid}/ar/pairing/qr")
    ResponseEntity<String> arPairingQr(@PathVariable String pid, @RequestParam String url) {
        store.require(pid);
        return ResponseEntity.ok().contentType(MediaType.parseMediaType("image/svg+xml")).body(pairing.qr(pid, url));
    }

    @GetMapping("/projects/{pid}/export")
    ResponseEntity<byte[]> export(@PathVariable String pid) {
        ProjectService.Export zip = projects.export(pid);
        return ResponseEntity.ok().contentType(MediaType.parseMediaType("application/zip"))
                .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename=\"" + zip.filename() + "\"").body(zip.bytes());
    }

    private static InputStream stream(HttpServletRequest request) {
        try {
            return request.getInputStream();
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
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
