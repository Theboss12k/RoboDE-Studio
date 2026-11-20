package dev.robode.studio.flow;

import static org.assertj.core.api.Assertions.assertThat;

import dev.robode.studio.common.Json;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.stream.Stream;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import tools.jackson.databind.JsonNode;

/**
 * The whole product flow through the Java API with real Python subprocesses (`.venv/bin/python -m studio.tasks`) and
 * scripted fixture LLMs (src/test/resources/scripted/rules.json). Fixture results, not model results.
 */
@SpringBootTest
@AutoConfigureMockMvc
class FullFlowTest {
    static final Path RUNTIME = FlowSupport.newRuntime("studio-flow");
    static final String FAKE_KEY = "AIza-FAKE-flow-test-key-0001";

    static {
        FlowSupport.writeScriptedProfiles(RUNTIME, "rules.json");
    }

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry registry) { FlowSupport.configure(registry, RUNTIME); }

    @Autowired MockMvc mvc;

    @Test
    void designReviseApproveSearchAnalyseExport() throws Exception {
        FlowSupport.Api api = new FlowSupport.Api(mvc);
        assertThat(api.send("PUT", "/llm/keys/gemini", Json.obj().put("key", FAKE_KEY)).status()).isEqualTo(200);

        // connection test keeps the old response shape the Settings page reads
        JsonNode test = api.job("/llm/profiles/fx/test", null);
        assertThat(test.get("status").asString()).as(test.toString()).isEqualTo("done");
        assertThat(test.get("result").propertyNames()).contains("text", "text_seconds", "images");
        assertThat(test.get("result").get("text").asString()).isEqualTo("ready");

        // brief -> questions -> answers -> v1
        String pid = api.post("/projects", Json.obj().put("brief", "Move a 0.3 kg bottle from A to B")).body().get("id").asString();
        JsonNode q = api.job("/projects/" + pid + "/design", Json.obj().set("answers", Json.obj()));
        assertThat(q.get("status").asString()).as(q.toString()).isEqualTo("done");
        assertThat(q.get("result").get("status").asString()).isEqualTo("questions");
        JsonNode project = api.get("/projects/" + pid).body();
        assertThat(project.get("questions").get(0).get("id").asString()).isEqualTo("cycle");
        assertThat(project.get("stage").asString()).isEqualTo("questions");

        JsonNode designed = api.job("/projects/" + pid + "/design", Json.obj().set("answers", Json.obj().put("cycle", 6)));
        assertThat(designed.get("status").asString()).as(designed.toString()).isEqualTo("done");
        assertThat(designed.get("result").toString()).isEqualTo("{\"status\":\"designed\",\"version\":1}");
        project = api.get("/projects/" + pid).body();
        assertThat(project.get("versions").toString()).isEqualTo("[1]");
        assertThat(project.get("answers").get("cycle").asInt()).isEqualTo(6);
        assertThat(project.get("stage").asString()).isEqualTo("designed");

        JsonNode v1 = api.get("/projects/" + pid + "/versions/1").body();
        assertThat(v1.has("schemaVersion")).isFalse();
        assertThat(v1.get("parent_version").isNull()).isTrue();
        Set<String> moving = new HashSet<>();
        v1.get("parsed").get("joints").forEach(j -> { if (!j.get("type").asString().equals("fixed")) moving.add(j.get("name").asString()); });
        assertThat(moving).isNotEmpty().containsAll(v1.get("sim").get("frames").get(0).get("joints").propertyNames());
        assertThat(Files.exists(RUNTIME.resolve("projects/" + pid + "/versions/v1.parsed.json"))).isTrue();

        // reference material: text, a URDF (v1's own) and an image
        assertThat(api.send("PUT", "/projects/" + pid + "/reference", Json.obj().put("text", "A bench-top task.")).status()).isEqualTo(200);
        var withUrdf = api.send("POST", "/projects/" + pid + "/reference/files?filename=arm.urdf", v1.get("urdf").asString().getBytes(StandardCharsets.UTF_8));
        assertThat(withUrdf.status()).as(withUrdf.body().toString()).isEqualTo(200);
        var withImage = api.send("POST", "/projects/" + pid + "/reference/files?filename=photo.png", FlowSupport.PNG);
        assertThat(withImage.status()).as(withImage.body().toString()).isEqualTo(200);
        assertThat(withImage.body().get("reference").get("files").toString())
                .isEqualTo("[{\"name\":\"arm.urdf\",\"kind\":\"urdf\"},{\"name\":\"photo.png\",\"kind\":\"image\"}]");
        assertThat(api.send("POST", "/projects/" + pid + "/reference/files?filename=bad.urdf", "<robot><oops".getBytes()).status()).isEqualTo(422);

        // revise v1 at t = 1.0 s -> v2 (parent 1) and the model saw v1's params and the moment
        JsonNode rev = api.job("/projects/" + pid + "/revise", Json.obj().put("request", "make it reach 20 cm further").put("base_version", 1).put("t", 1.0));
        assertThat(rev.get("status").asString()).as(rev.toString()).isEqualTo("done");
        assertThat(rev.get("result").get("version").asInt()).isEqualTo(2);
        JsonNode v2 = api.get("/projects/" + pid + "/versions/2").body();
        assertThat(v2.get("parent_version").asInt()).isEqualTo(1);
        JsonNode lastCall = lastCall(rev.get("id").asString());
        String prompt = lastCall.get("input").asString();
        assertThat(prompt).contains("\"moment\"").contains("\"checks\"").contains("\"requirement_targets\"").contains("\"reach_max_m\"");
        JsonNode frame = null;
        for (JsonNode f : v1.get("sim").get("frames"))
            if (frame == null || Math.abs(f.get("t").asDouble() - 1.0) < Math.abs(frame.get("t").asDouble() - 1.0)) frame = f;
        if (!frame.get("phase").asString().isEmpty()) assertThat(prompt).contains(Json.MAPPER.writeValueAsString(frame.get("phase").asString()));
        List<String> matched = new ArrayList<>();
        v1.get("robot").get("params").properties().forEach(e -> {
            if (e.getValue().isNumber() && prompt.contains("\"" + e.getKey() + "\":" + e.getValue())) matched.add(e.getKey());
        });
        assertThat(matched).as("v1 robot params present in the prompt").isNotEmpty();

        // a second revise from v1 while v2 exists -> v3 (parent 1); v2 untouched
        JsonNode v2Before = api.get("/projects/" + pid + "/versions/2").body();
        JsonNode rev2 = api.job("/projects/" + pid + "/revise", Json.obj().put("request", "and a bit more").put("base_version", 1).put("t", 2.0));
        assertThat(rev2.get("result").get("version").asInt()).as(rev2.toString()).isEqualTo(3);
        assertThat(api.get("/projects/" + pid + "/versions/3").body().get("parent_version").asInt()).isEqualTo(1);
        assertThat(api.get("/projects/" + pid + "/versions/2").body()).isEqualTo(v2Before);
        assertThat(api.send("POST", "/projects/" + pid + "/revise", Json.obj().put("request", "x").put("base_version", 9)).detail()).isEqualTo("Unknown base version 9");
        var badT = api.send("POST", "/projects/" + pid + "/revise", Json.obj().put("request", "x").put("base_version", 1).put("t", 9999));
        assertThat(badT.status()).isEqualTo(422);
        assertThat(badT.detail()).startsWith("The moment t must be between 0 and ");

        // approve v1: the others stay unapproved, later changes are refused
        JsonNode approved = api.post("/projects/" + pid + "/approve", Json.obj().put("version", 1)).body();
        assertThat(approved.get("approved_version").asInt()).isEqualTo(1);
        assertThat(api.get("/projects/" + pid + "/versions/1").body().get("approved").asBoolean()).isTrue();
        assertThat(api.get("/projects/" + pid + "/versions/3").body().get("approved").asBoolean()).isFalse();
        var frozen = api.send("POST", "/projects/" + pid + "/revise", Json.obj().put("request", "more").put("base_version", 1));
        assertThat(frozen.status()).isEqualTo(409);
        assertThat(frozen.detail()).isEqualTo("This design is approved (version 1) and frozen; changes are disabled");
        assertThat(api.post("/projects/" + pid + "/approve", Json.obj().put("version", 2)).status()).isEqualTo(409);

        // search -> candidates
        JsonNode search = api.job("/projects/" + pid + "/search", null);
        assertThat(search.get("status").asString()).as(search.toString()).isEqualTo("done");
        assertThat(search.get("result").get("candidates").get(0).get("id").asString()).isEqualTo("acme_ra_6");
        assertThat(api.get("/projects/" + pid + "/candidates").body().get(0).get("id").asString()).isEqualTo("acme_ra_6");

        // analyse with an uploaded datasheet
        String base = "/projects/" + pid + "/candidates/acme_ra_6";
        byte[] sheet = Files.readAllBytes(FlowSupport.DATASHEET);
        var up = api.send("POST", base + "/datasheet?filename=ra6.md", sheet);
        assertThat(up.body().toString()).startsWith("{\"ok\":true,\"characters\":");
        JsonNode analysis = api.job(base + "/analyse", null);
        assertThat(analysis.get("status").asString()).as(analysis.toString()).isEqualTo("done");
        JsonNode result = analysis.get("result");
        assertThat(result.get("parsed").get("joints")).isNotEmpty();
        assertThat(result.get("differences")).isNotEmpty();
        assertThat(result.has("schemaVersion")).isFalse();
        JsonNode again = api.get("/projects/" + pid + "/analysis/acme_ra_6").body();
        assertThat(again.get("candidate_id").asString()).isEqualTo("acme_ra_6");
        assertThat(again.get("parsed")).isNotNull();
        assertThat(Files.exists(RUNTIME.resolve("projects/" + pid + "/analysis/acme_ra_6.parsed.json"))).isTrue();

        // a blocked datasheet URL fails the job with its code
        JsonNode blocked = api.job(base + "/analyse", Json.obj().put("datasheet_url", "http://127.0.0.1/x"));
        assertThat(blocked.get("status").asString()).isEqualTo("error");
        assertThat(blocked.get("error_code").asString()).isEqualTo("datasheet_download");

        // decision and export
        JsonNode decided = api.post("/projects/" + pid + "/decision", Json.obj().put("candidate_id", "acme_ra_6").put("action", "approve")).body();
        assertThat(decided.get("decision").toString()).isEqualTo("{\"candidate_id\":\"acme_ra_6\",\"action\":\"approve\"}");
        var export = api.get("/projects/" + pid + "/export");
        assertThat(export.status()).isEqualTo(200);
        assertThat(zipEntries(export.bytes())).containsExactlyInAnyOrder("README.txt", "candidates/acme_ra_6/analysis.json",
                "candidates/acme_ra_6/candidate.urdf", "candidates/acme_ra_6/manufacturer_values.json", "decision.json", "design.glb",
                "design.urdf", "requirements.json", "scene.json", "sim_summary.json", "spec.json");

        // usage: labelled per project, and no key anywhere on disk
        JsonNode usage = api.get("/llm/usage?period=all").body();
        assertThat(usage.get("by_robot_id").propertyNames()).contains(pid);
        assertThat(usage.get("by_task").propertyNames()).contains("design", "revise", "search", "analyse");
        assertThat(api.get("/llm").body().toString()).doesNotContain(FAKE_KEY);
        assertThat(api.get("/llm").body().get("keys").get("gemini").get("saved").asBoolean()).isTrue();
        try (Stream<Path> files = Files.walk(RUNTIME)) {
            for (Path f : files.filter(Files::isRegularFile).toList()) {
                if (f.getFileName().toString().equals("keys.json")) continue; // the one place a saved key may live
                assertThat(Files.readString(f, StandardCharsets.ISO_8859_1)).as(f.toString()).doesNotContain(FAKE_KEY);
            }
        }
        assertThat(Files.isDirectory(RUNTIME.resolve("logs/jobs"))).isTrue();
    }

    static JsonNode lastCall(String jobId) throws IOException {
        List<String> lines = Files.readAllLines(RUNTIME.resolve("tmp/jobs/" + jobId + "/llm_calls.jsonl"));
        return Json.parse(lines.get(lines.size() - 1));
    }

    static List<String> zipEntries(byte[] zip) throws IOException {
        List<String> names = new ArrayList<>();
        try (ZipInputStream in = new ZipInputStream(new ByteArrayInputStream(zip))) {
            for (ZipEntry e; (e = in.getNextEntry()) != null; ) names.add(e.getName());
        }
        return names;
    }
}
