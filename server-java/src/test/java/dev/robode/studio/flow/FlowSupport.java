package dev.robode.studio.flow;


import dev.robode.studio.common.Json;
import dev.robode.studio.support.SpecValidator;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/** Shared harness for the tests that run the real Python tasks with scripted (fixture) LLMs in a temp runtime. */
final class FlowSupport {
    static final Path RESOURCES = Path.of("src/test/resources").toAbsolutePath();
    static final Path REPO = Path.of("..").toAbsolutePath().normalize();
    static final Path DATASHEET = REPO.resolve("tests/fixtures/arm_datasheet.md");
    static final byte[] PNG = concat("\u0089PNG\r\n\u001a\n".getBytes(StandardCharsets.ISO_8859_1), "0".repeat(20).getBytes(StandardCharsets.ISO_8859_1));

    private FlowSupport() {}

    static byte[] concat(byte[] a, byte[] b) {
        byte[] out = new byte[a.length + b.length];
        System.arraycopy(a, 0, out, 0, a.length);
        System.arraycopy(b, 0, out, a.length, b.length);
        return out;
    }

    static Path newRuntime(String name) {
        try {
            return Files.createTempDirectory(name);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    /** Points the app at a temp runtime, keeps job dirs, allows scripted profiles and the offline acme.example download. */
    static void configure(DynamicPropertyRegistry registry, Path runtime) {
        registry.add("studio.runtime", runtime::toString);
        registry.add("studio.allow-scripted", () -> "true");
        registry.add("studio.keep-job-dirs", () -> "true");
        registry.add("studio.warm-meta", () -> "false");
        registry.add("studio.fake-download", DATASHEET::toString);
    }

    /** profiles.json with the two default profiles plus `fx` (scripted, rules.json); every role uses `fx`. */
    static void writeScriptedProfiles(Path runtime, String rulesFile) {
        ObjectNode data = Json.obj();
        ArrayNode profiles = Json.arr();
        for (String[] p : new String[][] {{"claude_opus", "Claude Opus 5.5 (Claude Code CLI)", "claude-opus-5-5"},
                {"claude_sonnet", "Claude Sonnet 5.5 (Claude Code CLI)", "claude-sonnet-5-5"}})
            profiles.add(Json.obj().put("id", p[0]).put("name", p[1]).put("kind", "claude_cli").put("model", p[2])
                    .put("effort", "").put("timeout_s", 240).put("cli_path", ""));
        profiles.add(Json.obj().put("id", "fx").put("name", "Scripted fixture").put("kind", "scripted").put("model", "fixture")
                .put("fixture", RESOURCES.resolve("scripted/" + rulesFile).toString()).put("effort", "").put("timeout_s", 240).put("cli_path", ""));
        data.set("profiles", profiles);
        ObjectNode roles = Json.obj();
        for (String r : new String[] {"spec", "scene", "robot", "revise", "search", "extract", "analysis", "objects", "ask", "checks"}) roles.put(r, "fx");
        data.set("roles", roles);
        Json.write(runtime.resolve("llm/profiles.json"), data);
    }

    record Res(int status, JsonNode body, byte[] bytes) {
        String detail() { return body == null ? null : Json.text(body, "detail"); }
    }

    /** MockMvc wrapper: calls /api/v1 paths and waits for jobs. */
    static final class Api {
        final MockMvc mvc;

        Api(MockMvc mvc) { this.mvc = mvc; }

        Res send(String method, String path, Object body) {
            try {
                URI uri = URI.create("/api/v1" + path);
                MockHttpServletRequestBuilder b = switch (method) {
                    case "GET" -> MockMvcRequestBuilders.get(uri);
                    case "POST" -> MockMvcRequestBuilders.post(uri);
                    case "PUT" -> MockMvcRequestBuilders.put(uri);
                    case "DELETE" -> MockMvcRequestBuilders.delete(uri);
                    default -> throw new IllegalArgumentException(method);
                };
                if (body instanceof byte[] raw) b.content(raw).contentType(MediaType.APPLICATION_OCTET_STREAM);
                else if (body != null) b.content(Json.MAPPER.writeValueAsString(body)).contentType(MediaType.APPLICATION_JSON);
                var response = mvc.perform(b).andReturn().getResponse();
                byte[] bytes = response.getContentAsByteArray();
                JsonNode json = null;
                String type = response.getContentType();
                if (SpecValidator.instance().hasOperation(method, path)) // unknown routes (404 probes) have no operation
                    SpecValidator.check(method, "/api/v1" + path, response.getStatus(), type, bytes); // every response matches contracts/openapi.yaml
                if (type != null && type.contains("json") && bytes.length > 0) json = Json.MAPPER.readTree(new String(bytes, StandardCharsets.UTF_8));
                return new Res(response.getStatus(), json, bytes);
            } catch (Exception e) {
                throw new IllegalStateException(e);
            }
        }

        Res get(String path) { return send("GET", path, null); }

        Res post(String path, Object body) { return send("POST", path, body); }

        /** Waits for the job in `started` (a job record) and returns the final record. */
        JsonNode finish(JsonNode started) {
            JsonNode job = started;
            long end = System.nanoTime() + 240_000_000_000L;
            while (job.get("status").asString().equals("running")) {
                if (System.nanoTime() > end) throw new AssertionError("job timed out: " + job);
                try { Thread.sleep(50); } catch (InterruptedException e) { throw new IllegalStateException(e); }
                job = get("/jobs/" + started.get("id").asString()).body();
            }
            return job;
        }

        /** POSTs a job route, asserts 200 and returns the finished job record. */
        JsonNode job(String path, Object body) {
            Res r = post(path, body);
            if (r.status() != 200) throw new AssertionError(path + " -> " + r.status() + " " + r.body());
            return finish(r.body());
        }
    }
}
