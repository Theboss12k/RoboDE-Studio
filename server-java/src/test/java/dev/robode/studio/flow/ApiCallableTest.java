package dev.robode.studio.flow;

import static org.assertj.core.api.Assertions.assertThat;

import com.sun.net.httpserver.HttpServer;
import dev.robode.studio.common.Json;
import dev.robode.studio.support.SpecValidator;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HexFormat;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import org.springframework.test.web.servlet.request.MockMvcRequestBuilders;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * The callable API (wait, expand, callbacks, cancel, idempotent starts) with real Python tasks and scripted fixture LLMs.
 * Fixture results, not model results. The "slow" profile holds the extract role for 25 s (delay_s of the Python fixture
 * adapter) so a job stays running long enough to wait on and cancel.
 */
@SpringBootTest
@AutoConfigureMockMvc
class ApiCallableTest {
    static final Path RUNTIME = FlowSupport.newRuntime("studio-callable");
    static final String SECRET = "callback-test-secret";
    static final BlockingQueue<Received> RECEIVED = new LinkedBlockingQueue<>();
    static final HttpServer RECEIVER;

    record Received(String path, String job, String event, String signature, byte[] body) {}

    static {
        try {
            FlowSupport.writeScriptedProfiles(RUNTIME, "rules.json");
            // rules.json with a 25 s delay on the datasheet extraction rule, as profile "slow"
            ObjectNode rules = (ObjectNode) Json.read(FlowSupport.RESOURCES.resolve("scripted/rules.json"));
            for (JsonNode rule : rules.get("rules"))
                if (rule.get("match").asString().equals("manufacturer's datasheet")) ((ObjectNode) rule).put("delay_s", 25);
            Path slowRules = RUNTIME.resolve("rules_slow.json");
            Json.write(slowRules, rules);
            Path profiles = RUNTIME.resolve("llm/profiles.json");
            ObjectNode data = Json.readObject(profiles);
            ((ArrayNode) data.get("profiles")).add(Json.obj().put("id", "slow").put("name", "Slow fixture").put("kind", "scripted")
                    .put("model", "fixture").put("fixture", slowRules.toString()).put("effort", "").put("timeout_s", 240).put("cli_path", ""));
            Json.write(profiles, data);

            RECEIVER = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            RECEIVER.createContext("/", exchange -> {
                byte[] body = exchange.getRequestBody().readAllBytes();
                RECEIVED.add(new Received(exchange.getRequestURI().getPath(), exchange.getRequestHeaders().getFirst("X-Studio-Job"),
                        exchange.getRequestHeaders().getFirst("X-Studio-Event"), exchange.getRequestHeaders().getFirst("X-Studio-Signature"), body));
                exchange.sendResponseHeaders(204, -1);
                exchange.close();
            });
            RECEIVER.start();
        } catch (Exception e) {
            throw new IllegalStateException(e);
        }
    }

    @AfterAll
    static void stop() { RECEIVER.stop(0); }

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry registry) {
        FlowSupport.configure(registry, RUNTIME);
        registry.add("studio.callback-secret", () -> SECRET);
        registry.add("studio.callback-retry-delays", () -> "100ms,200ms");
    }

    @Autowired MockMvc mvc;

    record Res(int status, JsonNode body, MockHttpServletResponse raw) {
        String header(String name) { return raw.getHeader(name); }
    }

    Res call(String method, String path, Object body, String... headers) throws Exception {
        MockHttpServletRequestBuilder b = switch (method) {
            case "GET" -> MockMvcRequestBuilders.get("/api/v1" + path);
            case "POST" -> MockMvcRequestBuilders.post("/api/v1" + path);
            case "PUT" -> MockMvcRequestBuilders.put("/api/v1" + path);
            default -> MockMvcRequestBuilders.delete("/api/v1" + path);
        };
        if (body instanceof byte[] raw) b.content(raw).contentType(MediaType.APPLICATION_OCTET_STREAM);
        else if (body != null) b.content(Json.MAPPER.writeValueAsString(body)).contentType(MediaType.APPLICATION_JSON);
        for (int i = 0; i < headers.length; i += 2) b.header(headers[i], headers[i + 1]);
        MockHttpServletResponse response = mvc.perform(b).andReturn().getResponse();
        byte[] bytes = response.getContentAsByteArray();
        SpecValidator.check(method, "/api/v1" + path, response.getStatus(), response.getContentType(), bytes); // matches contracts/openapi.yaml
        JsonNode json = bytes.length > 0 && response.getContentType() != null && response.getContentType().contains("json")
                ? Json.MAPPER.readTree(new String(bytes, StandardCharsets.UTF_8)) : null;
        return new Res(response.getStatus(), json, response);
    }

    @Test
    void callableFlow() throws Exception {
        // -- idempotent project creation
        ObjectNode brief = Json.obj().put("brief", "Move a 0.3 kg bottle from A to B");
        Res created = call("POST", "/projects", brief, "Idempotency-Key", "proj-1");
        assertThat(created.status()).isEqualTo(200);
        String pid = created.body().get("id").asString();
        Res again = call("POST", "/projects", brief, "Idempotency-Key", "proj-1");
        assertThat(again.status()).isEqualTo(200);
        assertThat(again.body()).isEqualTo(created.body());
        assertThat(call("GET", "/projects", null).body().size()).isEqualTo(1);
        Res mismatch = call("POST", "/projects", Json.obj().put("brief", "Something else"), "Idempotency-Key", "proj-1");
        assertThat(mismatch.status()).isEqualTo(422);
        assertThat(mismatch.body().get("detail").asString()).isEqualTo("Idempotency-Key reused with a different request");
        assertThat(call("POST", "/projects", brief, "Idempotency-Key", "k".repeat(201)).status()).isEqualTo(422);
        assertThat(call("GET", "/projects", null).body().size()).isEqualTo(1);

        // -- questions in the response
        Res q = call("POST", "/projects/" + pid + "/design?wait=120&expand=resource", Json.obj().set("answers", Json.obj()));
        assertThat(q.status()).as(q.body().toString()).isEqualTo(200);
        assertThat(q.header("Location")).isEqualTo("/api/v1/jobs/" + q.body().get("id").asString());
        assertThat(q.body().get("status").asString()).as(q.body().toString()).isEqualTo("done");
        assertThat(q.body().get("result").get("status").asString()).isEqualTo("questions");
        assertThat(q.body().get("resource").get("questions").get(0).get("id").asString()).isEqualTo("cycle");
        assertThat(q.body().get("resource").get("round").asInt()).isGreaterThanOrEqualTo(1);
        assertThat(q.body().has("callback")).isFalse();

        // -- the full version in the response
        Res designed = call("POST", "/projects/" + pid + "/design?wait=120&expand=resource", Json.obj().set("answers", Json.obj().put("cycle", 6)));
        assertThat(designed.status()).as(designed.body().toString()).isEqualTo(200);
        assertThat(designed.body().get("status").asString()).as(designed.body().toString()).isEqualTo("done");
        JsonNode v1 = designed.body().get("resource");
        assertThat(v1).isEqualTo(call("GET", "/projects/" + pid + "/versions/1", null).body());
        assertThat(v1.get("parent_version").isNull()).isTrue();
        assertThat(v1.get("parsed").get("joints")).isNotEmpty();

        // -- without expand the record is unchanged
        Res plain = call("GET", "/jobs/" + designed.body().get("id").asString(), null);
        assertThat(plain.status()).isEqualTo(200);
        assertThat(plain.body().has("resource")).isFalse();
        Res expanded = call("GET", "/jobs/" + designed.body().get("id").asString() + "?wait=5&expand=resource", null);
        assertThat(expanded.status()).isEqualTo(200);
        assertThat(expanded.body().get("resource")).isEqualTo(v1);
        assertThat(expanded.header("Location")).isNull();

        // -- revise from v1 at a moment
        Res revised = call("POST", "/projects/" + pid + "/revise?wait=120&expand=resource",
                Json.obj().put("request", "make it reach 20 cm further").put("base_version", 1).put("t", 1.0));
        assertThat(revised.status()).as(revised.body().toString()).isEqualTo(200);
        assertThat(revised.body().get("status").asString()).as(revised.body().toString()).isEqualTo("done");
        assertThat(revised.body().get("resource").get("parent_version").asInt()).isEqualTo(1);
        assertThat(revised.body().get("resource")).isEqualTo(call("GET", "/projects/" + pid + "/versions/2", null).body());

        // -- bad parameters are 422
        assertThat(call("POST", "/projects/" + pid + "/search?wait=601", null).status()).isEqualTo(422);
        assertThat(call("POST", "/projects/" + pid + "/search?wait=-1", null).status()).isEqualTo(422);
        assertThat(call("POST", "/projects/" + pid + "/search?expand=everything", null).status()).isEqualTo(422);
        assertThat(call("GET", "/jobs/nope", null).status()).isEqualTo(404);
        assertThat(call("GET", "/jobs/nope", null).body().get("detail").asString()).isEqualTo("Job not found");

        assertThat(call("POST", "/projects/" + pid + "/approve", Json.obj().put("version", 1)).status()).isEqualTo(200);

        // -- callback host and URL checks come first: nothing is started
        for (String bad : new String[] {"http://evil.example/hook", "ftp://127.0.0.1/x", "not a url", "http://127.0.0.1.evil.example/x"}) {
            Res refused = call("POST", "/projects/" + pid + "/search", null, "X-Studio-Callback", bad);
            assertThat(refused.status()).as(bad).isEqualTo(422);
            assertThat(refused.body().has("detail")).isTrue();
        }
        assertThat(call("GET", "/projects/" + pid + "/jobs", null).body().toString()).doesNotContain("\"search\"");

        // -- search with a callback and an idempotency key
        String hook = "http://127.0.0.1:" + RECEIVER.getAddress().getPort() + "/hook";
        Res search = call("POST", "/projects/" + pid + "/search", null, "X-Studio-Callback", hook, "Idempotency-Key", "search-1");
        assertThat(search.status()).isEqualTo(200);
        String searchId = search.body().get("id").asString();
        assertThat(search.header("Location")).isEqualTo("/api/v1/jobs/" + searchId);
        assertThat(search.body().get("callback").get("url").asString()).isEqualTo(hook);
        Received received = RECEIVED.poll(120, TimeUnit.SECONDS);
        assertThat(received).as("callback delivered").isNotNull();
        assertThat(received.path()).isEqualTo("/hook");
        assertThat(received.job()).isEqualTo(searchId);
        assertThat(received.event()).isEqualTo("job.finished");
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(SECRET.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
        assertThat(received.signature()).isEqualTo("sha256=" + HexFormat.of().formatHex(mac.doFinal(received.body())));
        JsonNode pushed = Json.MAPPER.readTree(new String(received.body(), StandardCharsets.UTF_8));
        assertThat(pushed.get("id").asString()).isEqualTo(searchId);
        assertThat(pushed.get("status").asString()).as(pushed.toString()).isEqualTo("done");
        assertThat(pushed.get("resource").get(0).get("id").asString()).isEqualTo("acme_ra_6");

        // the record shows the delivery; a finished job answers a wait at once with 200
        JsonNode record = null;
        for (int i = 0; i < 100; i++) {
            Res r = call("GET", "/jobs/" + searchId + "?wait=30&expand=resource", null);
            assertThat(r.status()).isEqualTo(200);
            record = r.body();
            if (record.get("callback").get("delivered").asBoolean()) break;
            Thread.sleep(50);
        }
        assertThat(record.get("callback").toString()).isEqualTo("{\"url\":\"" + hook + "\",\"delivered\":true,\"attempts\":1}");
        assertThat(record.get("resource").get(0).get("id").asString()).isEqualTo("acme_ra_6");

        // idempotent start: same key and body, same job; another body, 422; one callback only
        Res replay = call("POST", "/projects/" + pid + "/search", null, "X-Studio-Callback", hook, "Idempotency-Key", "search-1");
        assertThat(replay.status()).isEqualTo(200);
        assertThat(replay.body().get("id").asString()).isEqualTo(searchId);
        assertThat(replay.header("Location")).isEqualTo("/api/v1/jobs/" + searchId);
        Res replayMismatch = call("POST", "/projects/" + pid + "/search", Json.obj().put("other", 1), "Idempotency-Key", "search-1");
        assertThat(replayMismatch.status()).isEqualTo(422);
        assertThat(replayMismatch.body().get("detail").asString()).isEqualTo("Idempotency-Key reused with a different request");
        assertThat(RECEIVED.poll(500, TimeUnit.MILLISECONDS)).isNull();
        // the same key on another path is another key
        Res other = call("POST", "/projects/" + pid + "/search?wait=120", null, "Idempotency-Key", "search-1");
        assertThat(other.status()).isEqualTo(200);

        // -- a slow analyse: wait times out with 202, then cancel
        assertThat(call("POST", "/projects/" + pid + "/candidates/acme_ra_6/datasheet?filename=ra6.md", Files.readAllBytes(FlowSupport.DATASHEET)).status()).isEqualTo(200);
        assertThat(call("PUT", "/llm/roles", Json.obj().set("roles", Json.obj().put("extract", "slow"))).status()).isEqualTo(200);
        String analyse = "/projects/" + pid + "/candidates/acme_ra_6/analyse";
        Res slow = call("POST", analyse + "?wait=0", null);
        assertThat(slow.status()).isEqualTo(200);
        assertThat(slow.body().get("status").asString()).isEqualTo("running");
        String slowId = slow.body().get("id").asString();
        Res waiting = call("GET", "/jobs/" + slowId + "?wait=1", null);
        assertThat(waiting.status()).isEqualTo(202);
        assertThat(waiting.header("Location")).isEqualTo("/api/v1/jobs/" + slowId);
        assertThat(waiting.body().get("status").asString()).isEqualTo("running");
        Res joined = call("POST", analyse + "?wait=1", null);
        assertThat(joined.status()).isEqualTo(202);
        assertThat(joined.body().get("id").asString()).isEqualTo(slowId);
        assertThat(joined.header("Location")).isEqualTo("/api/v1/jobs/" + slowId);

        Res cancelled = call("DELETE", "/jobs/" + slowId, null);
        assertThat(cancelled.status()).isEqualTo(200);
        assertThat(cancelled.body().get("status").asString()).isEqualTo("cancelled");
        assertThat(cancelled.body().get("error").asString()).isEqualTo("Cancelled by the caller");
        assertThat(call("GET", "/jobs/" + slowId + "?wait=5&expand=resource", null).body().get("status").asString()).isEqualTo("cancelled");
        assertThat(call("DELETE", "/jobs/" + slowId, null).status()).isEqualTo(409);
        assertThat(call("DELETE", "/jobs/nope", null).status()).isEqualTo(404);
        assertThat(call("GET", "/jobs", null).body().toString()).doesNotContain(slowId);
        assertThat(call("GET", "/projects/" + pid + "/analysis/acme_ra_6", null).status()).as("nothing committed").isEqualTo(404);

        // the project lock is free: a design step is accepted (not 409) and runs to its end
        Res next = call("POST", "/projects/" + pid + "/design", Json.obj().set("answers", Json.obj()));
        assertThat(next.status()).isEqualTo(200);
        assertThat(call("GET", "/jobs/" + next.body().get("id").asString() + "?wait=120", null).status()).isEqualTo(200);

        // -- the analyse resource, with the quick profile again
        assertThat(call("PUT", "/llm/roles", Json.obj().set("roles", Json.obj().put("extract", "fx"))).status()).isEqualTo(200);
        Res analysed = call("POST", analyse + "?wait=120&expand=resource", null);
        assertThat(analysed.status()).isEqualTo(200);
        assertThat(analysed.body().get("status").asString()).as(analysed.body().toString()).isEqualTo("done");
        assertThat(analysed.body().get("resource")).isEqualTo(call("GET", "/projects/" + pid + "/analysis/acme_ra_6", null).body());
        assertThat(analysed.body().get("resource").get("parsed").get("joints")).isNotEmpty();
    }
}
