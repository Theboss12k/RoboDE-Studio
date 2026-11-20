package dev.robode.studio.api;

import dev.robode.studio.common.Ids;
import dev.robode.studio.common.Json;
import dev.robode.studio.common.StudioException;
import dev.robode.studio.jobs.JobService;
import dev.robode.studio.jobs.TaskResult;
import dev.robode.studio.jobs.TaskRunner;
import dev.robode.studio.llm.LlmSettings;
import dev.robode.studio.store.ProjectStore;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/** The design, revise, search, analyse, upload and export flows: Python tasks for the work, the store for the commits. */
@Service
public class ProjectService {
    static final String README = """
            RoboDE Studio design export

            What this is: a rough robot concept for the task in spec.json, with its requirements and the simulated task.
            design.urdf and design.glb show the robot in its zero pose.

            Limits you should know:
            - Checks are kinematic and static only (reach, joint limits, static joint torques, speed, cycle time, coarse
              collisions). There is no dynamics, no contact simulation and no controller.
            - Nothing here was tested on hardware.
            - Requirement numbers come from the code in physics/ and the robot families; the AI chose the task layout and the
              robot family. Check the assumptions in spec.json.
            - Prices of real robots are web estimates with a source and a date; they change.
            - Manufacturer values are marked verified only when their quote was found in the datasheet text.
            """;

    private final ProjectStore store;
    private final TaskRunner runner;
    private final JobService jobs;
    private final LlmSettings llm;
    private final Map<String, Object> flights = new ConcurrentHashMap<>();

    public ProjectService(ProjectStore store, TaskRunner runner, JobService jobs, LlmSettings llm) {
        this.store = store;
        this.runner = runner;
        this.jobs = jobs;
        this.llm = llm;
    }

    private static String taskId(String prefix) { return prefix + UUID.randomUUID().toString().replace("-", "").substring(0, 10); }

    private Object flight(String key) { return flights.computeIfAbsent(key, k -> new Object()); }

    // -- design -------------------------------------------------------------------------------------------------
    public ObjectNode design(String pid, JsonNode answers) {
        store.require(pid);
        return jobs.start("design", pid, job -> {
            ObjectNode meta = store.meta(pid);
            ObjectNode merged = meta.get("answers") instanceof ObjectNode a ? a.deepCopy() : Json.obj();
            if (answers != null && answers.isObject()) answers.properties().forEach(e -> merged.set(e.getKey(), e.getValue()));
            ObjectNode input = Json.obj();
            input.put("brief", Json.text(meta, "brief"));
            input.set("reference", store.referenceForTask(pid));
            input.set("answers", merged);
            input.put("round", meta.path("round").asInt(0));
            if (meta.hasNonNull("spec")) input.set("spec", meta.get("spec"));
            try (TaskResult r = runner.run(job.id(), "design", input, llm.resolveRoles(), pid, "design", job.listener())) {
                JsonNode data = r.data();
                int round = data.path("round").asInt(meta.path("round").asInt(0) + 1);
                if ("questions".equals(Json.text(data, "status"))) {
                    store.update(pid, m -> {
                        m.set("questions", data.get("questions"));
                        m.set("answers", merged);
                        m.put("round", round);
                        m.put("stage", "questions");
                    });
                    return questionsResult(data);
                }
                ObjectNode version = (ObjectNode) data.get("version");
                JsonNode spec = version.get("spec");
                String title = Json.text(spec, "title") == null ? "" : Json.text(spec, "title");
                store.update(pid, m -> {
                    m.set("spec", spec);
                    m.set("answers", merged);
                    m.put("round", round);
                    m.set("questions", Json.arr());
                    m.put("title", title.substring(0, Math.min(80, title.length())));
                });
                int n = store.addVersion(pid, version, null, data.get("parsed"), null);
                return Json.obj().put("status", "designed").put("version", n);
            }
        });
    }

    private static ObjectNode questionsResult(JsonNode data) {
        ObjectNode out = Json.obj();
        out.put("status", "questions");
        out.set("questions", data.get("questions"));
        return out;
    }

    // -- versions -----------------------------------------------------------------------------------------------
    public ObjectNode versionView(String pid, int n) {
        store.require(pid);
        ObjectNode version = Json.withoutSchemaVersion(store.version(pid, n));
        if (!version.has("parent_version")) version.putNull("parent_version");
        JsonNode parsed = store.parsedSidecar(pid, n);
        if (parsed == null) {
            synchronized (flight(pid + "/v" + n + "/parsed")) {
                parsed = store.parsedSidecar(pid, n);
                if (parsed == null) {
                    parsed = parsedFor(Json.text(version, "urdf"));
                    store.saveParsed(pid, n, parsed);
                }
            }
        }
        version.set("parsed", parsed);
        return version;
    }

    private JsonNode parsedFor(String urdf) {
        try (TaskResult r = runner.run(taskId("x-"), "export_assets", Json.obj().put("urdf", urdf).set("want", Json.arr().add("parsed")),
                LlmSettings.none(), null, "export_assets", null)) {
            return r.data().get("parsed");
        }
    }

    /** design.glb for a version, produced once by the export_assets task and cached next to the version. */
    public Path glb(String pid, int n) {
        Path cache = store.versionPath(pid, n, ".glb");
        if (Files.isRegularFile(cache)) return cache;
        synchronized (flight(pid + "/v" + n + "/glb")) {
            if (Files.isRegularFile(cache)) return cache;
            String urdf = Json.text(store.version(pid, n), "urdf");
            try (TaskResult r = runner.run(taskId("x-"), "export_assets",
                    Json.obj().put("urdf", urdf).set("want", Json.arr().add("glb")), LlmSettings.none(), null, "export_assets", null)) {
                Path made = r.file("design.glb");
                if (!Files.isRegularFile(made)) throw StudioException.internal("The mesh export produced no design.glb", "no_result");
                Files.createDirectories(cache.getParent());
                Json.move(made, cache);
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
            return cache;
        }
    }

    // -- revise / approve ---------------------------------------------------------------------------------------
    private static String frozenMessage(JsonNode frozen) {
        return "This design is approved (version " + frozen.asInt() + ") and frozen; changes are disabled";
    }

    public ObjectNode revise(String pid, String request, Integer baseVersion, JsonNode t) {
        store.require(pid);
        if (request == null || request.isEmpty() || request.length() > 2000)
            throw StudioException.value("request must be 1 to 2000 characters");
        JsonNode frozen = store.meta(pid).get("approved_version");
        if (frozen != null && !frozen.isNull() && frozen.asInt() != 0) throw StudioException.conflict(frozenMessage(frozen));
        List<Integer> versions = store.versions(pid);
        Integer base0 = baseVersion != null ? baseVersion : (versions.isEmpty() ? null : versions.get(versions.size() - 1));
        if (base0 != null && !versions.contains(base0)) throw StudioException.value("Unknown base version " + base0);
        ObjectNode base = base0 == null ? null : Json.withoutSchemaVersion(store.version(pid, base0));
        if (base != null && t != null && !t.isNull()) {
            double duration = base.path("sim").path("duration_s").asDouble(0);
            if (!t.isNumber() || t.asDouble() < 0 || t.asDouble() > duration)
                throw StudioException.value("The moment t must be between 0 and " + g(duration) + " s (the length of version " + base0 + ")");
        }
        final Integer baseVersionFinal = base0;
        return jobs.start("revise", pid, base0 == null ? null : "v" + base0, job -> {
            if (base == null) throw StudioException.value("There is no design to change yet");
            ObjectNode input = Json.obj();
            input.set("base", base);
            input.put("request", request);
            input.set("t", t == null ? Json.MAPPER.nullNode() : t);
            try (TaskResult r = runner.run(job.id(), "revise", input, llm.resolveRoles(), pid, "revise", job.listener())) {
                int n = store.addVersion(pid, r.data().get("version"), baseVersionFinal, r.data().get("parsed"), meta -> {
                    JsonNode now = meta.get("approved_version");
                    if (now != null && !now.isNull() && now.asInt() != 0) throw StudioException.conflict(frozenMessage(now));
                });
                return Json.obj().put("version", n);
            }
        });
    }

    /** Python's {:g} for the moment limit message. */
    static String g(double value) {
        return new java.math.BigDecimal(value).round(new java.math.MathContext(6)).stripTrailingZeros().toPlainString();
    }

    public ObjectNode approve(String pid, int version) {
        store.require(pid);
        store.locked(pid, () -> {
            JsonNode frozen = store.meta(pid).get("approved_version");
            boolean has = frozen != null && !frozen.isNull() && frozen.asInt() != 0;
            if (has && frozen.asInt() != version)
                throw StudioException.conflict("Version " + frozen.asInt() + " is already approved; this design is frozen");
            if (!has) store.approve(pid, version);
        });
        return store.view(pid);
    }

    // -- catalog ------------------------------------------------------------------------------------------------
    public ObjectNode search(String pid) {
        store.require(pid);
        return jobs.start("search", pid, job -> {
            ObjectNode version = store.approved(pid);
            if (version == null) throw StudioException.value("Approve a design version first");
            ObjectNode input = Json.obj();
            input.set("version", Json.withoutSchemaVersion(version));
            input.put("limit", 6);
            try (TaskResult r = runner.run(job.id(), "find", input, llm.resolveRoles(), pid, "search", job.listener())) {
                JsonNode candidates = r.data().get("candidates");
                store.saveCandidates(pid, candidates);
                return Json.obj().set("candidates", candidates);
            }
        });
    }

    public ObjectNode analyse(String pid, String cid, String datasheetUrl) {
        store.require(pid);
        store.candidate(pid, cid);
        return jobs.start("analyse", pid, cid, job -> {
            ObjectNode version = store.approved(pid);
            if (version == null) throw StudioException.value("Approve a design version first");
            ObjectNode candidate = store.candidate(pid, cid);
            String url = datasheetUrl == null ? "" : datasheetUrl.strip();
            ObjectNode input = Json.obj();
            input.set("version", Json.withoutSchemaVersion(version));
            input.set("candidate", candidate);
            if (url.isEmpty()) input.putNull("datasheet_url"); else input.put("datasheet_url", url);
            var stored = url.isEmpty() ? store.documentPath(pid, cid) : java.util.Optional.<Path>empty();
            if (stored.isPresent()) {
                input.put("document_path", stored.get().toString());
                input.put("document_source", store.documentSource(pid, cid));
            } else {
                input.putNull("document_path");
            }
            try (TaskResult r = runner.run(job.id(), "analyse", input, llm.resolveRoles(), pid, "analyse", job.listener())) {
                JsonNode data = r.data();
                JsonNode document = data.get("document");
                if (document != null && document.isObject()) {
                    String text = Files.readString(r.file(Json.text(document, "file") == null ? "document.txt" : Json.text(document, "file")),
                            StandardCharsets.UTF_8);
                    store.saveDocument(pid, cid, text, Json.text(document, "url") == null ? "" : Json.text(document, "url"));
                }
                store.saveAnalysis(pid, cid, data.get("analysis"), data.get("parsed"));
                ObjectNode view = Json.withoutSchemaVersion(data.get("analysis"));
                view.set("parsed", data.get("parsed"));
                return view;
            }
        });
    }

    public ObjectNode analysisView(String pid, String cid) {
        store.require(pid);
        ObjectNode analysis = Json.withoutSchemaVersion(store.analysis(pid, cid));
        JsonNode parsed = store.analysisParsed(pid, cid);
        if (parsed == null) {
            synchronized (flight(pid + "/analysis/" + cid)) {
                parsed = store.analysisParsed(pid, cid);
                if (parsed == null) {
                    parsed = parsedFor(Json.text(analysis, "urdf"));
                    store.saveAnalysisParsed(pid, cid, parsed);
                }
            }
        }
        analysis.set("parsed", parsed);
        return analysis;
    }

    // -- uploads ------------------------------------------------------------------------------------------------
    /** Validates an upload with the inspect_upload task in a staging folder; returns the staged file and the task's data. */
    private record Staged(Path file, TaskResult result) {}

    private Staged inspect(String name, byte[] payload, String kind) {
        String id = taskId("u-");
        Path staged = runner.jobsDir().resolve(id).resolve(name);
        try {
            Files.createDirectories(staged.getParent());
            Files.write(staged, payload);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
        ObjectNode input = Json.obj().put("path", staged.toString()).put("kind", kind);
        return new Staged(staged, runner.run(id, "inspect_upload", input, LlmSettings.none(), null, "inspect_upload", null));
    }

    public ObjectNode uploadReference(String pid, String filename, byte[] payload) {
        store.require(pid);
        String name = Ids.safeName(filename);
        boolean urdf = name.toLowerCase().endsWith(".urdf") || name.toLowerCase().endsWith(".zip");
        if (!urdf && store.imageCount(pid) >= ProjectStore.MAX_IMAGES)
            throw StudioException.value("At most " + ProjectStore.MAX_IMAGES + " reference images");
        Staged staged = inspect(name, payload, urdf ? "urdf" : "image");
        try (TaskResult result = staged.result()) {
            if (urdf) store.setReferenceUrdf(pid, name, staged.file(), result.data().get("summary"));
            else store.addReferenceImage(pid, name, staged.file());
        }
        return store.view(pid);
    }

    public ObjectNode uploadDatasheet(String pid, String cid, String filename, byte[] payload) {
        store.require(pid);
        store.candidate(pid, cid);
        String name = Ids.safeName(filename);
        Staged staged = inspect(name, payload, "datasheet");
        try (TaskResult result = staged.result()) {
            store.saveUploadedDocument(pid, cid, name, staged.file());
            return Json.obj().put("ok", true).put("characters", result.data().path("text_chars").asInt(0));
        }
    }

    // -- export -------------------------------------------------------------------------------------------------
    public record Export(String filename, byte[] bytes) {}

    public Export export(String pid) {
        store.require(pid);
        ObjectNode version = store.approved(pid);
        if (version == null) version = store.latest(pid);
        if (version == null) throw StudioException.value("There is no design to export yet");
        int n = version.get("version").asInt();
        JsonNode sim = version.get("sim");
        Map<String, byte[]> files = new LinkedHashMap<>();
        String readme = README;
        put(files, "design.urdf", Json.text(version, "urdf"));
        put(files, "spec.json", Json.pretty(version.get("spec")));
        put(files, "requirements.json", Json.pretty(version.get("requirements")));
        put(files, "scene.json", Json.pretty(version.get("scene")));
        ObjectNode summary = Json.obj();
        summary.set("version", version.get("version"));
        summary.set("approved", version.path("approved"));
        summary.set("ok", sim.path("ok"));
        summary.set("duration_s", sim.path("duration_s"));
        ObjectNode robot = Json.obj();
        for (String k : new String[] {"family", "name", "params"}) robot.set(k, version.path("robot").path(k));
        summary.set("robot", robot);
        summary.set("checks", sim.path("checks"));
        summary.set("events", sim.path("events"));
        put(files, "sim_summary.json", Json.pretty(summary));
        String glbNote = null;
        byte[] glb = null;
        try {
            glb = Files.readAllBytes(glb(pid, n));
        } catch (Exception e) { // the export is still useful without the mesh
            glbNote = "\ndesign.glb could not be written: " + e.getMessage() + "\n";
        }
        if (glb != null) files.put("design.glb", glb);
        for (String cid : store.analyses(pid)) {
            ObjectNode analysis = Json.withoutSchemaVersion(store.analysis(pid, cid));
            String urdf = Json.text(analysis, "urdf") == null ? "" : Json.text(analysis, "urdf");
            ObjectNode trimmed = analysis.deepCopy();
            trimmed.remove("urdf");
            if (trimmed.get("sim") instanceof ObjectNode s) s.remove("frames");
            put(files, "candidates/" + cid + "/analysis.json", Json.pretty(trimmed));
            put(files, "candidates/" + cid + "/manufacturer_values.json", Json.pretty(analysis.path("manufacturer_spec")));
            put(files, "candidates/" + cid + "/candidate.urdf", urdf);
        }
        JsonNode decision = store.meta(pid).get("decision");
        if (decision != null && !decision.isNull()) put(files, "decision.json", Json.pretty(decision));
        if (glbNote != null) readme += glbNote;
        // README.txt sits before design.glb in the Python archive order
        Map<String, byte[]> ordered = new LinkedHashMap<>();
        for (var e : files.entrySet()) {
            ordered.put(e.getKey(), e.getValue());
            if (e.getKey().equals("sim_summary.json")) ordered.put("README.txt", readme.getBytes(StandardCharsets.UTF_8));
        }
        ByteArrayOutputStream buffer = new ByteArrayOutputStream();
        try (ZipOutputStream zip = new ZipOutputStream(buffer)) {
            for (var e : ordered.entrySet()) {
                zip.putNextEntry(new ZipEntry(e.getKey()));
                zip.write(e.getValue());
                zip.closeEntry();
            }
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
        return new Export(pid + "-v" + n + ".zip", buffer.toByteArray());
    }

    private static void put(Map<String, byte[]> files, String name, String text) {
        files.put(name, text.getBytes(StandardCharsets.UTF_8));
    }
}
