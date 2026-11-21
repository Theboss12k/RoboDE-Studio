package dev.robode.studio.api;

import dev.robode.studio.common.Ids;
import dev.robode.studio.common.ImageSize;
import dev.robode.studio.common.Json;
import dev.robode.studio.common.StagedUpload;
import dev.robode.studio.common.StudioException;
import dev.robode.studio.jobs.JobService;
import dev.robode.studio.jobs.TaskResult;
import dev.robode.studio.jobs.TaskRunner;
import dev.robode.studio.llm.LlmSettings;
import dev.robode.studio.settings.PreferredVendors;
import dev.robode.studio.store.ProjectStore;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
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
import org.springframework.beans.factory.annotation.Value;
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
    private final PreferredVendors vendors;
    private final Map<String, Object> flights = new ConcurrentHashMap<>();
    private final long scanMaxBytes;

    public ProjectService(ProjectStore store, TaskRunner runner, JobService jobs, LlmSettings llm, PreferredVendors vendors,
                          @Value("${studio.scan-max-bytes:524288000}") long scanMaxBytes) {
        this.store = store;
        this.runner = runner;
        this.jobs = jobs;
        this.llm = llm;
        this.vendors = vendors;
        this.scanMaxBytes = scanMaxBytes;
    }

    private static String taskId(String prefix) { return prefix + UUID.randomUUID().toString().replace("-", "").substring(0, 10); }

    private Object flight(String key) { return flights.computeIfAbsent(key, k -> new Object()); }

    // -- design -------------------------------------------------------------------------------------------------
    public ObjectNode design(String pid, JsonNode answers) { return design(pid, answers, null, null, null); }

    /** `comments` (question id to text) and `note` (one per round) are already validated by the controller. */
    public ObjectNode design(String pid, JsonNode answers, JsonNode comments, String note, Boolean autoRepair) {
        store.require(pid);
        return jobs.start("design", pid, job -> {
            ObjectNode meta = store.meta(pid);
            ObjectNode merged = meta.get("answers") instanceof ObjectNode a ? a.deepCopy() : Json.obj();
            if (answers != null && answers.isObject()) answers.properties().forEach(e -> merged.set(e.getKey(), e.getValue()));
            int currentRound = meta.path("round").asInt(0);
            ObjectNode mergedComments = meta.get("answer_comments") instanceof ObjectNode c ? c.deepCopy() : Json.obj();
            if (comments != null && comments.isObject()) comments.properties().forEach(e -> {
                if (e.getValue().asString().isBlank()) mergedComments.remove(e.getKey()); else mergedComments.set(e.getKey(), e.getValue());
            });
            ArrayNode notes = Json.arr();
            if (meta.get("answer_notes") instanceof ArrayNode n)
                for (JsonNode old : n) if (!(note != null && !note.isBlank() && old.path("round").asInt(-1) == currentRound)) notes.add(old);
            if (note != null && !note.isBlank()) notes.add(Json.obj().put("round", currentRound).put("text", note.strip()));
            store.update(pid, m -> {
                if (mergedComments.isEmpty()) m.remove("answer_comments"); else m.set("answer_comments", mergedComments);
                if (notes.isEmpty()) m.remove("answer_notes"); else m.set("answer_notes", notes);
            });
            ObjectNode input = Json.obj();
            input.put("brief", Json.text(meta, "brief"));
            input.set("reference", store.referenceForTask(pid));
            input.set("answers", merged);
            input.put("round", currentRound);
            if (meta.get("questions") instanceof ArrayNode asked && !asked.isEmpty()) input.set("questions", asked);   // their defaults tell the baseline answers from untouched defaults
            if (meta.hasNonNull("spec")) input.set("spec", meta.get("spec"));
            if (!mergedComments.isEmpty()) input.set("comments", mergedComments);
            if (!notes.isEmpty()) input.set("notes", notes);
            ArrayNode images = store.answerImagesForTask(pid);
            if (!images.isEmpty()) input.set("answer_images", images);
            ArrayNode briefImages = store.briefImagesForTask(pid);
            if (!briefImages.isEmpty()) input.set("brief_images", briefImages);
            if (autoRepair != null) input.put("auto_repair", autoRepair);
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
                saveFleetParsed(pid, n, data.get("fleet_parsed"));
                store.applyFleetAdvice(pid, data.get("fleet_advice"));
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
        if (version.get("fleet") instanceof ObjectNode fleet) {
            JsonNode fp = store.fleetParsedSidecar(pid, n);
            if (fp == null) {
                synchronized (flight(pid + "/v" + n + "/fleet.parsed")) {
                    fp = store.fleetParsedSidecar(pid, n);
                    if (fp == null) {
                        ObjectNode made = Json.obj();
                        if (fleet.get("members") instanceof ArrayNode members)
                            for (JsonNode member : members)
                                if (Json.text(member, "id") != null && Json.text(member, "urdf") != null)
                                    made.set(Json.text(member, "id"), parsedFor(Json.text(member, "urdf")));
                        fp = made;
                        store.saveFleetParsed(pid, n, fp);
                    }
                }
            }
            version.set("fleet_parsed", fp);
        }
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

    public ObjectNode revise(String pid, String request, Integer baseVersion, JsonNode t) { return revise(pid, request, baseVersion, t, null); }

    public ObjectNode revise(String pid, String request, Integer baseVersion, JsonNode t, Boolean autoRepair) {
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
            if (autoRepair != null) input.put("auto_repair", autoRepair);
            try (TaskResult r = runner.run(job.id(), "revise", input, llm.resolveRoles(), pid, "revise", job.listener())) {
                int n = store.addVersion(pid, r.data().get("version"), baseVersionFinal, r.data().get("parsed"), meta -> {
                    JsonNode now = meta.get("approved_version");
                    if (now != null && !now.isNull() && now.asInt() != 0) throw StudioException.conflict(frozenMessage(now));
                });
                saveFleetParsed(pid, n, r.data().get("fleet_parsed"));
                store.applyFleetAdvice(pid, r.data().get("fleet_advice"));
                return Json.obj().put("version", n);
            }
        });
    }

    private void saveFleetParsed(String pid, int n, JsonNode fleetParsed) {
        if (fleetParsed != null && fleetParsed.isObject()) store.saveFleetParsed(pid, n, fleetParsed);
    }

    // -- fleet --------------------------------------------------------------------------------------------------
    private static final ObjectNode DEFAULT_LIMITS = Json.obj().put("max_robots", 2);

    private JsonNode fleetLimits(ObjectNode meta) {
        JsonNode limits = meta.path("fleet").get("limits");
        return limits == null || limits.isNull() ? DEFAULT_LIMITS.deepCopy() : limits;
    }

    /** Stores the user's choices (enabled, limits, member) in meta.fleet; the controller has validated them. */
    public ObjectNode setFleet(String pid, Boolean enabled, boolean hasLimits, JsonNode limits, boolean hasMember, String member) {
        store.require(pid);
        store.update(pid, m -> {
            ObjectNode fleet = m.get("fleet") instanceof ObjectNode f ? f : ProjectStore.fleetDefaults();
            if (enabled != null) fleet.put("enabled", enabled);
            if (hasLimits) fleet.set("limits", limits == null ? Json.MAPPER.nullNode() : limits);
            if (hasMember) { if (member == null) fleet.putNull("member"); else fleet.put("member", member); }
            m.set("fleet", fleet);
        });
        return store.view(pid);
    }

    private static final ObjectNode SAVINGS_DEFAULTS = Json.obj().put("hours_per_shift", 8).put("days_per_week", 5)
            .put("days_per_year", 250).put("supervision_min_per_shift", 15);

    /** Merges the validated changes into meta.savings_inputs (a null removes manual_cycle_s or operators, or restores a default). */
    public ObjectNode setBaseline(String pid, JsonNode changes) {
        store.require(pid);
        store.update(pid, m -> {
            ObjectNode inputs = m.get("savings_inputs") instanceof ObjectNode s ? s : SAVINGS_DEFAULTS.deepCopy();
            changes.properties().forEach(e -> {
                String k = e.getKey();
                if (!e.getValue().isNull()) inputs.set(k, e.getValue());
                else if (SAVINGS_DEFAULTS.has(k)) inputs.set(k, SAVINGS_DEFAULTS.get(k));
                else inputs.remove(k);
            });
            m.set("savings_inputs", inputs);
        });
        return store.view(pid);
    }

    /** Synchronous fleet_assign (no LLM): previews the assignment and stores it in meta.fleet.assignment. */
    public JsonNode fleetAssign(String pid, Integer baseVersion, JsonNode assignment) {
        store.require(pid);
        List<Integer> versions = store.versions(pid);
        Integer base;
        if (baseVersion != null) {
            base = baseVersion;
        } else {
            JsonNode ap = store.meta(pid).get("approved_version");
            base = ap != null && !ap.isNull() && ap.asInt() != 0 ? Integer.valueOf(ap.asInt()) : (versions.isEmpty() ? null : versions.get(versions.size() - 1));
        }
        if (base == null) throw StudioException.value("There is no design to plan a fleet for yet");
        if (!versions.contains(base)) throw StudioException.value("Unknown base version " + base);
        boolean user = assignment != null && !assignment.isNull();
        ObjectNode input = Json.obj();
        input.set("version", Json.withoutSchemaVersion(store.version(pid, base)));
        input.set("limits", fleetLimits(store.meta(pid)));
        input.set("assignment", user ? assignment : Json.MAPPER.nullNode());
        try (TaskResult r = runner.run(taskId("f-"), "fleet_assign", input, LlmSettings.none(), pid, "fleet_assign", null)) {
            JsonNode data = r.data();
            if (data.get("assignment") instanceof ObjectNode a) {
                ObjectNode stored = a.deepCopy();
                stored.put("confirmed", user);
                if (user) stored.put("source", "user");
                store.update(pid, m -> {
                    ObjectNode fleet = m.get("fleet") instanceof ObjectNode f ? f : ProjectStore.fleetDefaults();
                    fleet.set("assignment", stored);
                    m.set("fleet", fleet);
                });
            }
            return data.deepCopy();
        }
    }

    /** The fleet_design job: plans the fleet on a base version and commits the result as a child version. */
    public ObjectNode fleetDesign(String pid, Integer baseVersion) {
        store.require(pid);
        JsonNode frozen = store.meta(pid).get("approved_version");
        if (frozen != null && !frozen.isNull() && frozen.asInt() != 0) throw StudioException.conflict(frozenMessage(frozen));
        List<Integer> versions = store.versions(pid);
        Integer base0 = baseVersion != null ? baseVersion : (versions.isEmpty() ? null : versions.get(versions.size() - 1));
        if (base0 == null) throw StudioException.value("There is no design to change yet");
        if (!versions.contains(base0)) throw StudioException.value("Unknown base version " + base0);
        ObjectNode meta = store.meta(pid);
        JsonNode assignment = meta.path("fleet").get("assignment");
        if (assignment == null || assignment.isNull()) throw StudioException.conflict("Assign the robots first");
        ObjectNode base = Json.withoutSchemaVersion(store.version(pid, base0));
        ObjectNode input = Json.obj();
        input.set("base", base);
        input.set("limits", fleetLimits(meta));
        input.set("assignment", assignment);
        final int baseFinal = base0;
        return jobs.start("fleet_design", pid, "v" + base0, job -> {
            try (TaskResult r = runner.run(job.id(), "fleet_design", input, llm.resolveRoles(), pid, "fleet_design", job.listener())) {
                int n = store.addVersion(pid, r.data().get("version"), baseFinal, r.data().get("parsed"), m -> {
                    JsonNode now = m.get("approved_version");
                    if (now != null && !now.isNull() && now.asInt() != 0) throw StudioException.conflict(frozenMessage(now));
                });
                saveFleetParsed(pid, n, r.data().get("fleet_parsed"));
                return Json.obj().put("version", n);
            }
        });
    }

    /** `member` for search and analyse: set when the approved version is a fleet and the user picked a member. */
    private void addMember(String pid, ObjectNode version, ObjectNode input) {
        if (!version.hasNonNull("fleet")) return;
        String member = Json.text(store.meta(pid).path("fleet"), "member");
        if (member != null && !member.isBlank()) input.put("member", member);
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
            input.set("preferred_vendors", vendors.list());
            addMember(pid, version, input);
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
            addMember(pid, version, input);
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
        if (!urdf && store.specImageCount(pid) >= ProjectStore.MAX_SPEC_IMAGES && !store.hasReferenceImage(pid, name))
            throw StudioException.value(ProjectStore.BUDGET_MESSAGE);
        Staged staged = inspect(name, payload, urdf ? "urdf" : "image");
        try (TaskResult result = staged.result()) {
            if (urdf) store.setReferenceUrdf(pid, name, staged.file(), result.data().get("summary"));
            else store.addReferenceImage(pid, name, staged.file());
        }
        return store.view(pid);
    }

    /** An image attached to a question (or "_general") of the spec round; returns the updated list. */
    public ArrayNode uploadAnswerImage(String pid, String filename, String question, byte[] payload) {
        store.require(pid);
        String name = Ids.safeName(filename);
        ObjectNode meta = store.meta(pid);
        boolean known = ProjectStore.GENERAL.equals(question) || meta.path("answers").has(String.valueOf(question));
        if (!known && meta.path("questions") instanceof ArrayNode qs)
            for (JsonNode q : qs) if (String.valueOf(question).equals(Json.text(q, "id"))) known = true;
        if (!known) throw StudioException.value("Unknown question; use a question id or _general");
        if (store.specImageCount(pid) >= ProjectStore.MAX_SPEC_IMAGES && !store.hasAnswerImage(pid, name))
            throw StudioException.value(ProjectStore.BUDGET_MESSAGE);
        Staged staged = inspect(name, payload, "image");
        try (TaskResult result = staged.result()) {
            store.addAnswerImage(pid, name, question, staged.file());
        }
        return store.answerImages(pid);
    }

    /** Replaces the brief (10 to 20000 characters after trimming); 409 when approved or while a design step runs. */
    public ObjectNode updateBrief(String pid, String brief) {
        store.require(pid);
        String text = brief == null ? "" : brief.strip();
        if (text.length() < 10 || text.length() > 20000) throw StudioException.value("brief must be 10 to 20000 characters");
        store.locked(pid, () -> {
            if (jobs.busy(pid)) throw StudioException.conflict("This project is still working on another design step");
            store.setBrief(pid, text);
        });
        return store.view(pid);
    }

    /** A photo of the task, objects or workcell attached to the brief; returns the updated list. */
    public ArrayNode uploadBriefImage(String pid, String filename, byte[] payload) {
        store.require(pid);
        String name = Ids.safeName(filename);
        if (store.meta(pid).path("approved_version").asInt(0) != 0) throw StudioException.conflict(ProjectStore.BRIEF_FROZEN);
        if (store.specImageCount(pid) >= ProjectStore.MAX_SPEC_IMAGES && !store.hasBriefImage(pid, name))
            throw StudioException.value(ProjectStore.BUDGET_MESSAGE);
        Staged staged = inspect(name, payload, "image");
        try (TaskResult result = staged.result()) {
            store.addBriefImage(pid, name, staged.file());
        }
        return store.briefImages(pid);
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

    // -- AR scenes (photo over the robot) -----------------------------------------------------------------------
    /** Validates the photo with inspect_upload and stores it; width and height come from the header until calibration. */
    public ObjectNode uploadArScene(String pid, String filename, byte[] payload) {
        store.require(pid);
        String name = Ids.safeName(filename);
        ImageSize.Size size = ImageSize.read(payload);
        if (size == null) throw StudioException.value("The photo must be a PNG, JPEG or WebP image");
        if (store.arSceneCount(pid) >= ProjectStore.MAX_AR_SCENES) throw StudioException.value(ProjectStore.AR_LIMIT_MESSAGE);
        Staged staged = inspect(name, payload, "image");
        try (TaskResult result = staged.result()) {
            return store.createArScene(pid, payload, size.format(), size.width(), size.height());
        }
    }

    /**
     * Runs ar_calibrate synchronously on the stored photo, then writes the reference and the calibration into scene.json.
     * The Python result also sets width and height from the intrinsics. Returns the scene.
     */
    public ObjectNode calibrateArScene(String pid, String sid, JsonNode reference) {
        store.require(pid);
        ObjectNode scene = store.getArScene(pid, sid);
        JsonNode intrinsics = scene.path("estimate").path("intrinsics");
        ObjectNode input = Json.obj();
        input.set("image", Json.obj().put("path", store.arPhoto(pid, sid).path().toString()));
        input.set("reference", reference);
        input.set("intrinsics", intrinsics.isObject() ? intrinsics : Json.MAPPER.nullNode());
        try (TaskResult r = runner.run(taskId("ar-"), "ar_calibrate", input, LlmSettings.none(), null, "ar_calibrate", null)) {
            JsonNode calibration = r.data();
            JsonNode size = calibration.path("intrinsics");
            int width = size.path("width").asInt(0);
            int height = size.path("height").asInt(0);
            store.updateArScene(pid, sid, s -> {
                s.set("reference", reference);
                s.set("calibration", calibration);
                if (width > 0 && height > 0) {
                    s.put("width", width);
                    s.put("height", height);
                }
            });
        }
        return store.getArScene(pid, sid);
    }

    /** Starts an ar_estimate job; on success the store sets scene.estimate. The job record is returned. */
    public ObjectNode estimateArScene(String pid, String sid) {
        store.require(pid);
        store.getArScene(pid, sid);
        Path photo = store.arPhoto(pid, sid).path();
        return jobs.start("ar_estimate", pid, sid, job -> {
            ObjectNode input = Json.obj();
            input.set("image", Json.obj().put("path", photo.toString()));
            try (TaskResult r = runner.run(job.id(), "ar_estimate", input, LlmSettings.none(), pid, "ar_estimate", job.listener())) {
                JsonNode estimate = r.data();
                store.updateArScene(pid, sid, s -> s.set("estimate", estimate));
                return store.getArScene(pid, sid);
            }
        });
    }

    /** Stores the placement (x, y in metres and yaw_deg). Absent values are 0; a value that is not a finite number is a 400. */
    public ObjectNode placeArScene(String pid, String sid, JsonNode body) {
        store.require(pid);
        ObjectNode placement = Json.obj();
        for (String key : new String[] {"x", "y", "yaw_deg"}) placement.put(key, placementNumber(body, key));
        return store.updateArScene(pid, sid, s -> s.set("placement", placement));
    }

    private static double placementNumber(JsonNode body, String key) {
        JsonNode value = body == null ? null : body.get(key);
        if (value == null || value.isNull()) return 0.0;
        if (!value.isNumber() || !Double.isFinite(value.asDouble())) throw new StudioException(400, key + " must be a finite number", null);
        return value.asDouble();
    }

    // -- apply the edited scene (deterministic, no LLM) ---------------------------------------------------------
    /** Starts apply_scene: the edited scene becomes a new version of the base (the latest by default), as revise does. */
    public ObjectNode applyScene(String pid, Integer baseVersion, JsonNode scene, JsonNode markers, boolean autoRepair, String note) {
        store.require(pid);
        JsonNode frozen = store.meta(pid).get("approved_version");
        if (frozen != null && !frozen.isNull() && frozen.asInt() != 0) throw StudioException.conflict(frozenMessage(frozen));
        List<Integer> versions = store.versions(pid);
        Integer base0 = baseVersion != null ? baseVersion : (versions.isEmpty() ? null : versions.get(versions.size() - 1));
        if (base0 != null && !versions.contains(base0)) throw StudioException.value("Unknown base version " + base0);
        ObjectNode base = base0 == null ? null : Json.withoutSchemaVersion(store.version(pid, base0));
        final Integer baseFinal = base0;
        return jobs.start("apply_scene", pid, base0 == null ? null : "v" + base0, job -> {
            if (base == null) throw StudioException.value("There is no design to change yet");
            ObjectNode input = Json.obj();
            input.set("base", base);
            input.set("scene", scene);
            input.set("markers", markers);
            input.put("auto_repair", autoRepair);
            input.put("note", note == null ? "" : note);
            try (TaskResult r = runner.run(job.id(), "apply_scene", input, LlmSettings.none(), pid, "apply_scene", job.listener())) {
                int n = store.addVersion(pid, r.data().get("version"), baseFinal, r.data().get("parsed"), meta -> {
                    JsonNode now = meta.get("approved_version");
                    if (now != null && !now.isNull() && now.asInt() != 0) throw StudioException.conflict(frozenMessage(now));
                });
                return Json.obj().put("version", n);
            }
        });
    }

    // -- site scans (video AR) ----------------------------------------------------------------------------------
    /** Streams an uploaded video into a new runtime/tmp/jobs/u-* folder; refuses a bad name, a 6th scan or a body over the cap. */
    public Path stageScan(String pid, String filename, InputStream body, long declared) {
        store.require(pid);
        String name = Ids.safeName(filename);
        if (ProjectStore.videoExtension(name) == null) throw StudioException.value("The video must be an MP4, MOV or WebM file");
        if (store.scanCount(pid) >= ProjectStore.MAX_SCANS) throw StudioException.value(ProjectStore.SCAN_LIMIT_MESSAGE);
        String tooLarge = "The video is too large (" + (scanMaxBytes / (1024 * 1024)) + " MB at most)";
        return StagedUpload.stage(body, declared, scanMaxBytes, runner.jobsDir().resolve(taskId("u-")), name, tooLarge);
    }

    /**
     * Probes the staged video with scan_probe (synchronous) and stores it as a new site scan with status uploaded.
     * The staging folder is removed in every case.
     */
    public ObjectNode uploadScan(String pid, String filename, Path staged) {
        try {
            store.require(pid);
            String ext = ProjectStore.videoExtension(Ids.safeName(filename));
            if (ext == null) throw StudioException.value("The video must be an MP4, MOV or WebM file");
            ObjectNode input = Json.obj();
            input.set("video", Json.obj().put("path", staged.toString()));
            JsonNode probe;
            try (TaskResult r = runner.run(taskId("sc-"), "scan_probe", input, LlmSettings.none(), null, "scan_probe", null)) {
                probe = r.data();
            }
            return store.createScan(pid, staged, ext, probe, Files.size(staged));
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        } finally {
            ProjectStore.deleteTree(staged.getParent());
        }
    }

    /**
     * Starts scan_reconstruct as a job. Python may only write in the job's out dir, so the frames and the mesh are written
     * there and moved into the scan folder after success. The scan is "reconstructing" meanwhile.
     */
    public ObjectNode reconstructScan(String pid, String sid) {
        store.require(pid);
        ObjectNode scan = store.getScan(pid, sid);
        if ("reconstructing".equals(Json.text(scan, "status"))) throw StudioException.conflict("This site scan is already being reconstructed");
        Path video = store.scanVideo(pid, sid).path();
        int rotation = scan.path("video").path("rotation_deg").asInt(0);
        store.updateScan(pid, sid, s -> { s.put("status", "reconstructing"); s.putNull("error"); });
        return jobs.start("scan_reconstruct", pid, sid, job -> {
            Path out = runner.jobsDir().resolve(job.id());
            ObjectNode input = Json.obj();
            input.set("video", Json.obj().put("path", video.toString()));
            input.put("rotation_deg", rotation);
            input.set("out", Json.obj().put("frames_dir", out.resolve("frames").toString())
                    .put("mesh_path", out.resolve("scan.glb").toString()));
            try (TaskResult r = runner.run(job.id(), "scan_reconstruct", input, LlmSettings.none(), pid, "scan_reconstruct", job.listener())) {
                JsonNode data = r.data();
                if (!Files.isDirectory(out.resolve("frames"))) throw StudioException.internal("The reconstruction wrote no frames", "no_result");
                store.adoptScanOutputs(pid, sid, out.resolve("frames"), out.resolve("scan.glb"));
                store.updateScan(pid, sid, s -> {
                    s.set("camera", field(data, "camera"));
                    s.set("frames", field(data, "frames"));
                    s.set("floor", field(data, "floor"));
                    s.set("alignment", field(data, "alignment"));
                    s.set("mesh", field(data, "mesh"));
                    s.set("stats", field(data, "stats"));
                    s.putNull("scale"); // a scale belongs to one reconstruction; the new one needs its own
                    s.putNull("error");
                    s.put("status", "reconstructed");
                });
                return store.getScan(pid, sid);
            } catch (RuntimeException e) {
                markScanFailed(pid, sid, e);
                throw e;
            }
        });
    }

    private void markScanFailed(String pid, String sid, RuntimeException e) {
        String message = e instanceof StudioException ? e.getMessage() : e.getClass().getSimpleName() + ": " + e.getMessage();
        try {
            store.updateScan(pid, sid, s -> { s.put("status", "failed"); s.put("error", message); });
        } catch (StudioException gone) {
            // the scan was deleted while the job ran
        }
    }

    /** The scan as stored, which must be reconstructed (409 otherwise). */
    private ObjectNode reconstructed(String pid, String sid) {
        store.require(pid);
        ObjectNode scan = store.getScan(pid, sid);
        if (!"reconstructed".equals(Json.text(scan, "status"))) throw StudioException.conflict("Reconstruct this site scan first");
        return scan;
    }

    /** scan_scale (synchronous): the body plus the current scan; the result is stored as scan.scale. */
    public ObjectNode scaleScan(String pid, String sid, JsonNode body) {
        ObjectNode scan = reconstructed(pid, sid);
        ObjectNode input = ((ObjectNode) body).deepCopy();
        input.set("scan", scan);
        try (TaskResult r = runner.run(taskId("sc-"), "scan_scale", input, LlmSettings.none(), null, "scan_scale", null)) {
            JsonNode scale = r.data();
            return store.updateScan(pid, sid, s -> s.set("scale", scale));
        }
    }

    /** scan_floor (synchronous): re-picks the floor from clicked points; camera_up is computed here from the cameras. */
    public ObjectNode floorScan(String pid, String sid, JsonNode body) {
        ObjectNode scan = reconstructed(pid, sid);
        JsonNode points = body == null ? null : body.get("points");
        if (points == null || !points.isArray()) throw StudioException.value("points must be a list of points");
        ObjectNode input = Json.obj();
        input.set("points", points);
        input.set("camera_up", cameraUp(scan));
        try (TaskResult r = runner.run(taskId("sc-"), "scan_floor", input, LlmSettings.none(), null, "scan_floor", null)) {
            JsonNode data = r.data();
            return store.updateScan(pid, sid, s -> {
                s.set("floor", field(data, "floor"));
                s.set("alignment", field(data, "alignment"));
            });
        }
    }

    /** Mean of the registered cameras' up directions in R (minus the camera y axis, since OpenCV y points down), unit length. */
    static JsonNode cameraUp(JsonNode scan) {
        double x = 0;
        double y = 0;
        double z = 0;
        int n = 0;
        for (JsonNode frame : scan.path("frames")) {
            JsonNode m = frame.get("world_from_camera");
            if (m == null || !m.isArray() || m.size() < 3) continue;
            if (!(m.get(0).size() > 1 && m.get(1).size() > 1 && m.get(2).size() > 1)) continue;
            // world_from_camera is row-major 4x4: column 1 of the rotation block is the camera's y axis in R
            x -= m.get(0).get(1).asDouble();
            y -= m.get(1).get(1).asDouble();
            z -= m.get(2).get(1).asDouble();
            n++;
        }
        if (n == 0) throw StudioException.value("No camera is registered; reconstruct the scan first");
        double length = Math.sqrt(x * x + y * y + z * z);
        if (length < 1e-9) throw StudioException.value("The camera up directions cancel out");
        return Json.arr().add(x / length).add(y / length).add(z / length);
    }

    /** Stores the placement of a scan (x, y in metres and yaw_deg), validated like the photo placement. */
    public ObjectNode placeScan(String pid, String sid, JsonNode body) {
        store.require(pid);
        ObjectNode placement = Json.obj();
        for (String key : new String[] {"x", "y", "yaw_deg"}) placement.put(key, placementNumber(body, key));
        return store.updateScan(pid, sid, s -> s.set("placement", placement));
    }

    /** Removes a scan; a scan that is being reconstructed cannot be removed (409). */
    public void deleteScan(String pid, String sid) {
        store.require(pid);
        if ("reconstructing".equals(Json.text(store.getScan(pid, sid), "status")))
            throw StudioException.conflict("This site scan is still being reconstructed");
        store.deleteScan(pid, sid);
    }

    private static JsonNode field(JsonNode node, String name) {
        JsonNode value = node == null ? null : node.get(name);
        return value == null ? Json.MAPPER.nullNode() : value;
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
        JsonNode fleet = version.get("fleet");
        boolean isFleet = fleet != null && fleet.isObject();
        if (isFleet) {
            ObjectNode trimmedFleet = (ObjectNode) fleet.deepCopy();
            if (trimmedFleet.get("members") instanceof ArrayNode members)
                for (JsonNode member : members)
                    if (member instanceof ObjectNode mo) {
                        mo.remove("urdf");
                        if (mo.get("sim") instanceof ObjectNode ms) ms.remove("frames");
                    }
            put(files, "fleet.json", Json.pretty(trimmedFleet));
            if (fleet.get("members") instanceof ArrayNode members)
                for (JsonNode member : members) {
                    String rid = Json.text(member, "id");
                    if (rid != null && Json.text(member, "urdf") != null && rid.matches("[A-Za-z0-9_-]{1,40}"))
                        put(files, "members/" + rid + "/design.urdf", Json.text(member, "urdf"));
                }
            readme += "\nFleet: the top-level files describe member " + Json.text(fleet, "primary")
                    + "; the full task is in fleet.json\n";
        }
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
