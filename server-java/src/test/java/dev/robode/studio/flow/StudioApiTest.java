package dev.robode.studio.flow;

import static org.assertj.core.api.Assertions.assertThat;

import dev.robode.studio.common.Json;
import dev.robode.studio.flow.FlowSupport.Api;
import dev.robode.studio.flow.FlowSupport.Res;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

/** The robot studio routes against the fake Python engine (fixture answers, not a model evaluation); responses are checked against the spec. */
@SpringBootTest
@AutoConfigureMockMvc
class StudioApiTest {
    static final Path TEMP;
    static final Path RUNTIME;
    static final Path REPO;

    static {
        try {
            TEMP = Files.createTempDirectory("studio-robot");
            RUNTIME = TEMP.resolve("runtime");
            REPO = TEMP.resolve("repo");
            Path tasks = REPO.resolve("server/studio/tasks");
            Files.createDirectories(tasks);
            Files.writeString(REPO.resolve("server/studio/__init__.py"), "");
            Files.writeString(tasks.resolve("__init__.py"), "");
            Files.copy(Path.of("src/test/resources/fake_engine/tasks_main.py"), tasks.resolve("__main__.py"));
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
    }

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry registry) {
        registry.add("studio.repo-root", REPO::toString);
        registry.add("studio.runtime", RUNTIME::toString);
        registry.add("studio.python", StudioApiTest::python);
        registry.add("studio.warm-meta", () -> "false");
    }

    static String python() {
        for (String candidate : new String[] {"../.venv/bin/python", "/usr/bin/python3", "/opt/homebrew/bin/python3", "/usr/local/bin/python3"})
            if (Files.isExecutable(Path.of(candidate))) return Path.of(candidate).toAbsolutePath().toString();
        org.junit.jupiter.api.Assumptions.abort("no python3 available");
        return null;
    }

    @Autowired MockMvc mvc;

    private Api api() { return new Api(mvc); }

    private String designed(Api api, boolean approve) {
        String pid = api.post("/projects", Json.obj().put("brief", "Move a box")).body().get("id").asString();
        JsonNode job = api.job("/projects/" + pid + "/design", Json.obj().set("answers", Json.obj()));
        assertThat(job.get("status").asString()).as(job.toString()).isEqualTo("done");
        if (approve) assertThat(api.post("/projects/" + pid + "/approve", Json.obj().put("version", 1)).status()).isEqualTo(200);
        return pid;
    }

    private static String base(String pid) { return "/projects/" + pid + "/studio"; }

    @Test
    void buildNeedsAnApprovedVersion() {
        Api api = api();
        String pid = designed(api, false);
        assertThat(api.post(base(pid) + "/builds", Json.obj()).status()).isEqualTo(409);
        api.post("/projects/" + pid + "/approve", Json.obj().put("version", 1));
        assertThat(api.post(base(pid) + "/builds", Json.obj().put("design_version", 2)).status()).isEqualTo(409);
        assertThat(api.post(base(pid) + "/builds", Json.obj().put("base_build", 3)).status()).isEqualTo(404);
        assertThat(api.post(base(pid) + "/builds", Json.obj().put("request", 5)).status()).isEqualTo(422);
        assertThat(api.get(base(pid) + "/builds/1").status()).isEqualTo(404);
        assertThat(api.get("/projects/" + pid).body().has("studio")).isFalse();
    }

    @Test
    void buildTwinSnapshotsApproveAndRebuild() throws Exception {
        Api api = api();
        String plain = designed(api, false);
        String pid = designed(api, true);
        String s = base(pid);

        Res started = api.post(s + "/builds?wait=60&expand=resource", null);
        assertThat(started.status()).isEqualTo(200);
        JsonNode job = started.body();
        assertThat(job.get("status").asString()).as(job.toString()).isEqualTo("done");
        assertThat(job.get("kind").asString()).isEqualTo("studio_build");
        assertThat(job.get("result").get("build").asInt()).isEqualTo(1);
        assertThat(job.get("resource").get("build").asInt()).isEqualTo(1);
        assertThat(job.get("resource").has("parsed")).isTrue();

        JsonNode studio = api.get("/projects/" + pid).body().get("studio");
        assertThat(studio.get("builds").toString()).isEqualTo("[1]");
        assertThat(studio.get("approved_build").isNull()).isTrue();
        assertThat(api.get("/projects/" + plain).body().has("studio")).isFalse();

        JsonNode list = api.get(s + "/builds").body();
        assertThat(list.size()).isEqualTo(1);
        assertThat(list.get(0).get("approved").asBoolean()).isFalse();
        assertThat(list.get(0).get("design_version").asInt()).isEqualTo(1);

        JsonNode build = api.get(s + "/builds/1").body();
        assertThat(build.get("build").asInt()).isEqualTo(1);
        assertThat(build.get("parent_build").isNull()).isTrue();
        assertThat(build.get("parsed").has("links")).isTrue();
        Path dir = RUNTIME.resolve("projects").resolve(pid).resolve("studio/b1");
        assertThat(dir.resolve("build.json")).exists();
        assertThat(dir.resolve("parsed.json")).exists();
        assertThat(dir.resolve("parts/p1.stl")).exists();

        // files
        Res step = api.get(s + "/builds/1/files/assembly.step");
        assertThat(step.status()).isEqualTo(200);
        assertThat(new String(step.bytes(), StandardCharsets.UTF_8)).contains("assembly.step");
        assertThat(mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get("/api/v1" + s + "/builds/1/files/parts/p1.stl"))
                .andReturn().getResponse().getContentType()).isEqualTo("model/stl");
        assertThat(mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get("/api/v1" + s + "/builds/1/files/assembly.step"))
                .andReturn().getResponse().getContentType()).isEqualTo("model/step");
        assertThat(api.get(s + "/builds/1/files/missing.step").status()).isEqualTo(404);
        assertThat(api.get(s + "/builds/1/files/..%2F..%2Fproject.json").status()).isIn(404, 422);
        assertThat(api.get(s + "/builds/1/files/parts/../../../project.json").status()).isIn(404, 422);
        assertThat(api.get(s + "/builds/1/files/parts/..%5C..%5Cproject.json").status()).isIn(404, 422);
        assertThat(api.get(s + "/builds/7/files/assembly.step").status()).isEqualTo(404);

        // twin
        ObjectNode move = Json.obj();
        move.set("q0", Json.obj().put("j1", 0.0));
        move.set("target", Json.obj().set("joints", Json.obj().put("j1", 0.5)));
        Res moved = api.post(s + "/builds/1/twin/move", move);
        assertThat(moved.status()).isEqualTo(200);
        assertThat(moved.body().get("frames").size()).isEqualTo(2);
        assertThat(api.post(s + "/builds/1/twin/move", Json.arr()).status()).isEqualTo(422);
        assertThat(api.post(s + "/builds/1/twin/move", Json.obj().put("q0", 1)).status()).isEqualTo(422);
        ObjectNode far = Json.obj();
        far.set("q0", Json.obj().put("j1", 0.0));
        far.set("target", Json.obj().set("cartesian", Json.arr().add(9).add(9).add(9)));
        Res bad = api.post(s + "/builds/1/twin/move", far);
        assertThat(bad.status()).isEqualTo(422);
        assertThat(bad.detail()).contains("out of reach");
        assertThat(api.post(s + "/builds/9/twin/move", move).status()).isEqualTo(404);
        Res run = api.post(s + "/builds/1/twin/run", Json.obj().put("speed_scale", 0.5));
        assertThat(run.status()).isEqualTo(200);
        assertThat(run.body().get("duration_s").asDouble()).isEqualTo(2.0);
        assertThat(api.post(s + "/builds/1/twin/run", null).status()).isEqualTo(200);
        assertThat(api.post(s + "/builds/1/twin/run", Json.obj().put("speed_scale", 5)).status()).isEqualTo(422);

        // snapshots
        Res snap = api.post(s + "/builds/1/snapshots?view=iso", FlowSupport.PNG);
        assertThat(snap.status()).isEqualTo(200);
        assertThat(snap.body().get("views").toString()).isEqualTo("[\"iso\"]");
        assertThat(api.post(s + "/builds/1/snapshots?view=iso", "not a png".getBytes(StandardCharsets.UTF_8)).status()).isEqualTo(422);
        assertThat(api.post(s + "/builds/1/snapshots?view=left", FlowSupport.PNG).status()).isEqualTo(422);
        assertThat(api.post(s + "/builds/1/snapshots?view=front", FlowSupport.PNG).body().get("views").toString()).isEqualTo("[\"iso\",\"front\"]");
        assertThat(dir.resolve("snapshots/iso.png")).exists();

        // not approved yet
        assertThat(api.get(s + "/builds/1/pdf").status()).isEqualTo(404);
        assertThat(api.get(s + "/builds/1/package").status()).isEqualTo(404);

        // approve
        JsonNode approved = api.job(s + "/builds/1/approve", null);
        assertThat(approved.get("status").asString()).as(approved.toString()).isEqualTo("done");
        assertThat(approved.get("kind").asString()).isEqualTo("studio_pdf");
        assertThat(approved.get("result").get("build").asInt()).isEqualTo(1);
        assertThat(api.get(s + "/builds/1").body().get("approved").asBoolean()).isTrue();
        assertThat(api.get(s + "/builds/1").body().get("approved_at").isNull()).isFalse();
        assertThat(api.get("/projects/" + pid).body().get("studio").get("approved_build").asInt()).isEqualTo(1);
        assertThat(dir.resolve("bom.csv")).exists();
        Res pdf = api.get(s + "/builds/1/pdf");
        assertThat(pdf.status()).isEqualTo(200);
        assertThat(new String(pdf.bytes(), StandardCharsets.UTF_8)).startsWith("%PDF");
        Res pkg = api.get(s + "/builds/1/package");
        assertThat(pkg.status()).isEqualTo(200);
        try (var zip = new java.util.zip.ZipInputStream(new ByteArrayInputStream(pkg.bytes()))) {
            assertThat(zip.getNextEntry().getName()).isEqualTo("bom.csv");
        }
        assertThat(api.post(s + "/builds/1/approve", null).status()).isEqualTo(409);
        assertThat(api.post(s + "/builds/1/snapshots?view=top", FlowSupport.PNG).status()).isEqualTo(409);

        // rebuild from build 1 with a pinned alternative
        ObjectNode rebuild = Json.obj().put("base_build", 1).put("request", "stronger shoulder");
        rebuild.set("pinned", Json.obj().put("j1", "robu_nema23"));
        JsonNode second = api.job(s + "/builds", rebuild);
        assertThat(second.get("status").asString()).as(second.toString()).isEqualTo("done");
        assertThat(second.get("result").get("build").asInt()).isEqualTo(2);
        JsonNode b2 = api.get(s + "/builds/2").body();
        assertThat(b2.get("parent_build").asInt()).isEqualTo(1);
        assertThat(b2.get("approved").asBoolean()).isFalse();
        assertThat(b2.get("summary").asString()).contains("j1");
        assertThat(api.get("/projects/" + pid).body().get("studio").get("builds").toString()).isEqualTo("[1,2]");
        assertThat(api.get(s + "/builds").body().size()).isEqualTo(2);
        assertThat(api.get(s + "/builds/2/pdf").status()).isEqualTo(404);
        assertThat(api.post(s + "/builds", Json.obj().put("pinned", "x")).status()).isEqualTo(422);
    }

    @Test
    void oneStudioBuildAtATime() throws Exception {
        Api api = api();
        String pid = designed(api, true);
        String s = base(pid);
        Path gate = TEMP.resolve("gate-" + pid);
        Res first = api.post(s + "/builds", Json.obj().put("request", "wait:" + gate));
        assertThat(first.status()).isEqualTo(200);
        assertThat(api.post(s + "/builds", Json.obj()).status()).isEqualTo(409);
        // design and revise are not blocked by a build (only studio_build excludes itself)
        Files.writeString(gate, "go");
        JsonNode done = api.finish(first.body());
        assertThat(done.get("status").asString()).as(done.toString()).isEqualTo("done");
        assertThat(api.job(s + "/builds", Json.obj()).get("result").get("build").asInt()).isEqualTo(2);
    }
}
