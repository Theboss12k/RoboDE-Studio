package dev.robode.studio.api;

import static org.assertj.core.api.Assertions.assertThat;

import dev.robode.studio.common.Json;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.Map;
import java.util.zip.ZipInputStream;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

/** The fleet routes against the fake Python engine in src/test/resources/fake_engine (fixture answers, not a model evaluation). */
@SpringBootTest
@AutoConfigureMockMvc
class FleetApiTest {
    static final Path TEMP;
    static final Path RUNTIME;
    static final Path REPO;

    static {
        try {
            TEMP = Files.createTempDirectory("studio-fleet");
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
        registry.add("studio.python", () -> python());
        registry.add("studio.warm-meta", () -> "false");
    }

    static String python() {
        for (String candidate : new String[] {"../.venv/bin/python", "/usr/bin/python3", "/opt/homebrew/bin/python3", "/usr/local/bin/python3"})
            if (Files.isExecutable(Path.of(candidate))) return Path.of(candidate).toAbsolutePath().toString();
        org.junit.jupiter.api.Assumptions.abort("no python3 available");
        return null;
    }

    @Autowired MockMvc mvc;


    // a small local client (FlowSupport is package-private in another package)
    private record Res(int status, JsonNode body, byte[] bytes) {}

    private Res call(String method, String path, Object body) throws Exception {
        var uri = java.net.URI.create("/api/v1" + path);
        var b = switch (method) {
            case "GET" -> org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get(uri);
            case "PUT" -> org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put(uri);
            default -> org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post(uri);
        };
        if (body != null) b.content(Json.MAPPER.writeValueAsString(body)).contentType(org.springframework.http.MediaType.APPLICATION_JSON);
        var response = mvc.perform(b).andReturn().getResponse();
        byte[] bytes = response.getContentAsByteArray();
        String type = response.getContentType();
        JsonNode json = type != null && type.contains("json") && bytes.length > 0
                ? Json.MAPPER.readTree(new String(bytes, StandardCharsets.UTF_8)) : null;
        return new Res(response.getStatus(), json, bytes);
    }

    private JsonNode finish(JsonNode started) throws Exception {
        JsonNode job = started;
        long end = System.nanoTime() + 60_000_000_000L;
        while (job.get("status").asString().equals("running")) {
            if (System.nanoTime() > end) throw new AssertionError("job timed out: " + job);
            Thread.sleep(30);
            job = call("GET", "/jobs/" + started.get("id").asString(), null).body();
        }
        return job;
    }

    /** A project with one designed version (the fake engine's design answers directly). */
    private String designed(String brief) throws Exception {
        String pid = call("POST", "/projects", Json.obj().put("brief", brief)).body().get("id").asString();
        JsonNode job = finish(call("POST", "/projects/" + pid + "/design", Json.obj().set("answers", Json.obj())).body());
        assertThat(job.get("status").asString()).as(job.toString()).isEqualTo("done");
        return pid;
    }

    @Test
    void adviceSetsFleetAndPlainProjectsHaveNone() throws Exception {
        String plain = designed("Move a box");
        assertThat(call("GET", "/projects/" + plain, null).body().has("fleet")).isFalse();
        String pid = designed("FLEET_MARKER move a long beam");
        JsonNode fleet = call("GET", "/projects/" + pid, null).body().get("fleet");
        assertThat(fleet.get("suggested").asBoolean()).isTrue();
        assertThat(fleet.get("enabled").asBoolean()).isTrue();
        assertThat(fleet.get("source").asString()).isEqualTo("spec");
        assertThat(fleet.get("reasons").get(0).asString()).contains("reach");
    }

    @Test
    void putValidatesAndStores() throws Exception {
        String pid = designed("Move a box");
        String url = "/projects/" + pid + "/fleet";
        assertThat(call("PUT", url, Json.obj().set("limits", Json.obj().put("max_robots", 4))).status()).isEqualTo(422);
        assertThat(call("PUT", url, Json.obj().set("limits", Json.obj().put("max_reach_m", 0))).status()).isEqualTo(422);
        assertThat(call("PUT", url, Json.obj().set("limits", Json.obj().put("max_payload_kg", 2001))).status()).isEqualTo(422);
        assertThat(call("PUT", url, Json.obj().set("limits", Json.obj().put("budget_usd", -1))).status()).isEqualTo(422);
        assertThat(call("PUT", url, Json.obj().set("limits", Json.obj().put("note", "x".repeat(1001)))).status()).isEqualTo(422);
        assertThat(call("PUT", url, Json.obj().set("limits", Json.obj().set("families", Json.arr().add(3)))).status()).isEqualTo(422);
        assertThat(call("PUT", url, Json.obj().put("enabled", "yes")).status()).isEqualTo(422);
        assertThat(call("GET", "/projects/" + pid, null).body().has("fleet")).isFalse();

        ObjectNode limits = Json.obj().put("max_robots", 3).put("max_reach_m", 2.5).put("note", "keep it cheap");
        limits.set("brands", Json.arr().add("acme"));
        Res ok = call("PUT", url, Json.obj().put("enabled", true).put("member", "r2").set("limits", limits));
        assertThat(ok.status()).isEqualTo(200);
        JsonNode fleet = ok.body().get("fleet");
        assertThat(fleet.get("enabled").asBoolean()).isTrue();
        assertThat(fleet.get("source").asString()).isEqualTo("user");
        assertThat(fleet.get("member").asString()).isEqualTo("r2");
        assertThat(fleet.get("limits").get("max_robots").asInt()).isEqualTo(3);
        assertThat(call("GET", "/projects/" + pid, null).body().get("fleet").get("limits").get("brands").get(0).asString()).isEqualTo("acme");
        assertThat(call("PUT", url, Json.obj().putNull("member")).body().get("fleet").get("member").isNull()).isTrue();
    }

    @Test
    void assignAutoAndUser() throws Exception {
        String pid = designed("Move a box");
        String url = "/projects/" + pid + "/fleet/assign";
        Res auto = call("POST", url, Json.obj());
        assertThat(auto.status()).isEqualTo(200);
        assertThat(auto.body().get("feasible").asBoolean()).isTrue();
        JsonNode stored = call("GET", "/projects/" + pid, null).body().get("fleet").get("assignment");
        assertThat(stored.get("confirmed").asBoolean()).isFalse();
        assertThat(stored.get("members")).hasSize(2);

        ObjectNode mine = Json.obj().set("members", Json.arr().add(Json.obj().put("id", "r1")).add(Json.obj().put("id", "r2")).add(Json.obj().put("id", "r3")));
        Res user = call("POST", url, Json.obj().put("base_version", 1).set("assignment", mine));
        assertThat(user.status()).isEqualTo(200);
        stored = call("GET", "/projects/" + pid, null).body().get("fleet").get("assignment");
        assertThat(stored.get("confirmed").asBoolean()).isTrue();
        assertThat(stored.get("source").asString()).isEqualTo("user");
        assertThat(stored.get("members")).hasSize(3);
        assertThat(call("POST", url, Json.obj().put("base_version", 9)).status()).isEqualTo(422);
    }

    @Test
    void designNeedsAssignmentThenCommitsAChildVersion() throws Exception {
        String pid = designed("Move a box");
        String base = "/projects/" + pid;
        assertThat(call("POST", base + "/fleet/design", Json.obj()).status()).isEqualTo(409);
        call("POST", base + "/fleet/assign", Json.obj());
        JsonNode job = finish(call("POST", base + "/fleet/design", Json.obj().put("base_version", 1)).body());
        assertThat(job.get("kind").asString()).isEqualTo("fleet_design");
        assertThat(call("GET", base + "/jobs", null).body().get(0).get("subject").asString()).isEqualTo("v1");
        assertThat(job.get("status").asString()).as(job.toString()).isEqualTo("done");
        int n = job.get("result").get("version").asInt();
        assertThat(n).isEqualTo(2);
        JsonNode v = call("GET", base + "/versions/" + n, null).body();
        assertThat(v.get("parent_version").asInt()).isEqualTo(1);
        assertThat(v.get("fleet").get("members")).hasSize(2);
        assertThat(v.get("fleet_parsed").has("r1") && v.get("fleet_parsed").has("r2")).isTrue();
        assertThat(call("GET", base + "/versions/1", null).body().has("fleet_parsed")).isFalse();

        // a missing sidecar is generated lazily, one parsed structure per member
        Files.delete(RUNTIME.resolve("projects/" + pid + "/versions/v" + n + ".fleet.parsed.json"));
        JsonNode again = call("GET", base + "/versions/" + n, null).body();
        assertThat(again.get("fleet_parsed").has("r2")).isTrue();
        assertThat(Files.exists(RUNTIME.resolve("projects/" + pid + "/versions/v" + n + ".fleet.parsed.json"))).isTrue();

        // revise on the fleet version keeps working and the approval guard refuses fleet_design
        assertThat(call("POST", base + "/approve", Json.obj().put("version", n)).status()).isEqualTo(200);
        assertThat(call("POST", base + "/fleet/design", Json.obj()).status()).isEqualTo(409);
    }

    @Test
    void designAndReviseAreRefusedWhileFleetDesignRuns() throws Exception {
        String pid = designed("Move a box");
        String base = "/projects/" + pid;
        Path release = TEMP.resolve("release-" + pid);
        call("PUT", base + "/fleet", Json.obj().put("enabled", true).set("limits", Json.obj().put("note", "wait:" + release)));
        call("POST", base + "/fleet/assign", Json.obj());
        Res started = call("POST", base + "/fleet/design", Json.obj());
        assertThat(started.status()).isEqualTo(200);
        assertThat(call("POST", base + "/design", Json.obj().set("answers", Json.obj())).status()).isEqualTo(409);
        assertThat(call("POST", base + "/revise", Json.obj().put("request", "faster")).status()).isEqualTo(409);
        assertThat(call("POST", base + "/fleet/design", Json.obj()).status()).isEqualTo(409);
        Files.writeString(release, "go");
        assertThat(finish(started.body()).get("status").asString()).isEqualTo("done");
    }

    @Test
    void exportHasFleetFiles() throws Exception {
        String pid = designed("Move a box");
        String base = "/projects/" + pid;
        Map<String, String> plain = unzip(call("GET", base + "/export", null).bytes());
        assertThat(plain).doesNotContainKeys("fleet.json", "members/r1/design.urdf");
        assertThat(plain.get("README.txt")).doesNotContain("Fleet:");
        call("POST", base + "/fleet/assign", Json.obj());
        finish(call("POST", base + "/fleet/design", Json.obj()).body());
        Map<String, String> files = unzip(call("GET", base + "/export", null).bytes());
        assertThat(files).containsKeys("fleet.json", "members/r1/design.urdf", "members/r2/design.urdf", "design.urdf");
        JsonNode fleet = Json.parse(files.get("fleet.json"));
        assertThat(fleet.get("members")).hasSize(2);
        assertThat(fleet.get("members").get(0).has("urdf")).isFalse();
        assertThat(fleet.get("members").get(0).get("sim").has("frames")).isFalse();
        assertThat(files.get("README.txt")).contains("Fleet: the top-level files describe member r1; the full task is in fleet.json");
    }

    private static Map<String, String> unzip(byte[] zip) throws IOException {
        Map<String, String> out = new HashMap<>();
        try (ZipInputStream in = new ZipInputStream(new ByteArrayInputStream(zip))) {
            for (var e = in.getNextEntry(); e != null; e = in.getNextEntry())
                out.put(e.getName(), new String(in.readAllBytes(), StandardCharsets.UTF_8));
        }
        return out;
    }
}
