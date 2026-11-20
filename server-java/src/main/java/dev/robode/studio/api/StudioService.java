package dev.robode.studio.api;

import dev.robode.studio.common.Json;
import dev.robode.studio.common.StudioException;
import dev.robode.studio.jobs.JobService;
import dev.robode.studio.jobs.TaskResult;
import dev.robode.studio.jobs.TaskRunner;
import dev.robode.studio.llm.LlmSettings;
import dev.robode.studio.store.ProjectStore;
import dev.robode.studio.store.StudioStore;
import java.nio.file.Path;
import java.util.List;
import java.util.UUID;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/** The robot studio: builds (a job), the digital twin (synchronous tasks), snapshots and the approval package (a job). */
@Service
public class StudioService {
    private final ProjectStore store;
    private final StudioStore studio;
    private final TaskRunner runner;
    private final JobService jobs;
    private final LlmSettings llm;

    public StudioService(ProjectStore store, StudioStore studio, TaskRunner runner, JobService jobs, LlmSettings llm) {
        this.store = store;
        this.studio = studio;
        this.runner = runner;
        this.jobs = jobs;
        this.llm = llm;
    }

    private static String taskId(String prefix) { return prefix + UUID.randomUUID().toString().replace("-", "").substring(0, 10); }

    private static Integer approvedVersion(ObjectNode meta) {
        JsonNode v = meta.get("approved_version");
        return v == null || v.isNull() || v.asInt() == 0 ? null : v.asInt();
    }

    // -- builds -------------------------------------------------------------------------------------------------
    /** Starts a studio_build job. The controller has validated the field types. */
    public ObjectNode startBuild(String pid, Integer designVersion, Integer baseBuild, String request, JsonNode pinned) {
        store.require(pid);
        Integer approved = approvedVersion(store.meta(pid));
        if (approved == null) throw StudioException.conflict("Approve a design version first");
        if (designVersion != null && !designVersion.equals(approved))
            throw StudioException.conflict("Design version " + designVersion + " is not approved; version " + approved + " is");
        final int dv = approved;
        if (baseBuild != null) studio.build(pid, baseBuild); // 404 when unknown
        final String text = request == null ? "" : request;
        return jobs.start("studio_build", pid, job -> {
            ObjectNode version = Json.withoutSchemaVersion(store.version(pid, dv));
            ObjectNode input = Json.obj();
            input.set("version", version);
            String member = version.hasNonNull("fleet") ? Json.text(store.meta(pid).path("fleet"), "member") : null;
            if (member == null || member.isBlank()) input.putNull("member"); else input.put("member", member);
            input.set("base_build", baseBuild == null ? Json.MAPPER.nullNode() : studio.build(pid, baseBuild));
            input.put("request", text);
            input.set("pinned", pinned == null || pinned.isNull() ? Json.obj() : pinned);
            input.put("build", studio.nextBuild(pid));
            int n = input.get("build").asInt();
            try (TaskResult r = runner.run(job.id(), "studio_build", input, llm.resolveRoles(), pid, "studio_build", job.listener())) {
                JsonNode built = r.data().get("build");
                if (built == null || !built.isObject()) throw StudioException.internal("The studio build produced no build", "no_result");
                studio.commit(pid, n, dv, baseBuild, built, r.data().get("parsed"), r.outDir());
                return Json.obj().put("build", n);
            }
        });
    }

    public ArrayNode builds(String pid) {
        store.require(pid);
        ArrayNode out = Json.arr();
        for (int n : studio.builds(pid)) {
            ObjectNode b = studio.build(pid, n);
            ObjectNode row = Json.obj();
            row.put("build", n);
            row.set("design_version", b.path("design_version"));
            row.set("member", b.hasNonNull("member") ? b.get("member") : Json.MAPPER.nullNode());
            row.set("parent_build", b.hasNonNull("parent_build") ? b.get("parent_build") : Json.MAPPER.nullNode());
            row.set("created", b.path("created"));
            row.put("summary", Json.text(b, "summary") == null ? "" : Json.text(b, "summary"));
            row.put("approved", b.path("approved").asBoolean(false));
            out.add(row);
        }
        return out;
    }

    /** The StudioBuild plus `parsed` (the studio URDF parsed once and kept next to the build). */
    public ObjectNode buildView(String pid, int n) {
        ObjectNode view = studio.build(pid, n);
        JsonNode parsed = studio.parsed(pid, n);
        if (parsed == null) {
            try (TaskResult r = runner.run(taskId("x-"), "export_assets", Json.obj().put("urdf", Json.text(view, "urdf") == null ? "" : Json.text(view, "urdf"))
                    .set("want", Json.arr().add("parsed")), LlmSettings.none(), null, "export_assets", null)) {
                parsed = r.data().get("parsed");
            }
            studio.saveParsed(pid, n, parsed);
        }
        view.set("parsed", parsed);
        return view;
    }

    // -- the twin (synchronous, no LLM) -------------------------------------------------------------------------
    public JsonNode twinMove(String pid, int n, JsonNode request) {
        ObjectNode build = studio.build(pid, n);
        ObjectNode input = Json.obj();
        input.set("build", build);
        input.set("version", Json.withoutSchemaVersion(store.version(pid, build.path("design_version").asInt())));
        input.set("request", request);
        try (TaskResult r = runner.run(taskId("t-"), "studio_twin_move", input, LlmSettings.none(), pid, "studio_twin_move", null)) {
            return r.data().deepCopy();
        }
    }

    public JsonNode twinRun(String pid, int n, double speedScale) {
        ObjectNode build = studio.build(pid, n);
        int dv = build.path("design_version").asInt();
        ObjectNode input = Json.obj();
        input.set("build", build);
        input.set("version", Json.withoutSchemaVersion(store.version(pid, dv)));
        input.put("speed_scale", speedScale);
        try (TaskResult r = runner.run(taskId("t-"), "studio_run_task", input, LlmSettings.none(), pid, "studio_run_task", null)) {
            return r.data().deepCopy();
        }
    }

    // -- snapshots and approval ---------------------------------------------------------------------------------
    public ObjectNode saveSnapshot(String pid, int n, String view, byte[] png) {
        ObjectNode build = studio.build(pid, n);
        if (build.path("approved").asBoolean(false)) throw StudioException.conflict("Build " + n + " is approved; its snapshots are frozen");
        if (!StudioStore.VIEWS.contains(view)) throw StudioException.value("view must be iso, front, side or top");
        if (png.length < 8 || (png[0] & 0xFF) != 0x89 || png[1] != 'P' || png[2] != 'N' || png[3] != 'G'
                || png[4] != 0x0D || png[5] != 0x0A || png[6] != 0x1A || png[7] != 0x0A)
            throw StudioException.value("The snapshot must be a PNG image");
        studio.saveSnapshot(pid, n, view, png);
        return Json.obj().set("views", studio.views(pid, n));
    }

    /** Starts the studio_pdf job: the manufacturer PDF and the package; on success the build is approved. */
    public ObjectNode approveBuild(String pid, int n) {
        store.require(pid);
        ObjectNode build = studio.build(pid, n);
        if (build.path("approved").asBoolean(false)) throw StudioException.conflict("Build " + n + " is already approved");
        return jobs.start("studio_pdf", pid, "b" + n, job -> {
            ObjectNode current = studio.build(pid, n);
            if (current.path("approved").asBoolean(false)) throw StudioException.conflict("Build " + n + " is already approved");
            ObjectNode meta = store.meta(pid);
            Path dir = studio.buildDir(pid, n);
            ObjectNode project = Json.obj();
            project.put("id", pid);
            project.set("title", meta.hasNonNull("title") ? meta.get("title") : Json.MAPPER.nullNode());
            project.set("brief", meta.hasNonNull("brief") ? meta.get("brief") : Json.MAPPER.nullNode());
            ArrayNode snapshots = Json.arr();
            for (JsonNode view : studio.views(pid, n))
                snapshots.add(Json.obj().put("view", view.asString())
                        .put("path", dir.resolve("snapshots").resolve(view.asString() + ".png").toAbsolutePath().toString()));
            ObjectNode input = Json.obj();
            input.set("build", current);
            input.set("version", Json.withoutSchemaVersion(store.version(pid, current.path("design_version").asInt())));
            input.set("project", project);
            input.put("build_dir", dir.toAbsolutePath().toString());
            input.set("snapshots", snapshots);
            try (TaskResult r = runner.run(job.id(), "studio_pdf", input, LlmSettings.none(), pid, "studio_pdf", job.listener())) {
                if (!java.nio.file.Files.isRegularFile(r.file("manufacturer.pdf")) || !java.nio.file.Files.isRegularFile(r.file("package.zip")))
                    throw StudioException.internal("The approval produced no manufacturer.pdf or package.zip", "no_result");
                studio.approve(pid, n, r.outDir(), List.of("manufacturer.pdf", "package.zip", "bom.csv"), r.data().get("files"));
                return Json.obj().put("build", n);
            }
        });
    }

    /** manufacturer.pdf or package.zip of an approved build; 404 until then. */
    public Path approvedFile(String pid, int n, String name) {
        ObjectNode build = studio.build(pid, n);
        Path path = studio.buildDir(pid, n).resolve(name);
        if (!build.path("approved").asBoolean(false) || !java.nio.file.Files.isRegularFile(path))
            throw StudioException.notFound("Build " + n + " is not approved yet");
        return path;
    }

    public Path file(String pid, int n, String path) { return studio.file(pid, n, path); }
}
