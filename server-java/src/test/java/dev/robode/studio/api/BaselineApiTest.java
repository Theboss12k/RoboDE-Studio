package dev.robode.studio.api;

import static org.assertj.core.api.Assertions.assertThat;

import dev.robode.studio.common.Json;
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

/** PUT /baseline against the fake Python engine in src/test/resources/fake_engine (fixture answers, not a model evaluation). */
@SpringBootTest
@AutoConfigureMockMvc
class BaselineApiTest {
    static final Path TEMP;
    static final Path RUNTIME;
    static final Path REPO;

    static {
        try {
            TEMP = Files.createTempDirectory("studio-baseline");
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


    private record Res(int status, JsonNode body) {}

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
        if (response.getStatus() < 500) dev.robode.studio.support.SpecValidator.check(method, "/api/v1" + path, response.getStatus(), response.getContentType(), bytes);
        return new Res(response.getStatus(), bytes.length > 0 ? Json.MAPPER.readTree(new String(bytes, StandardCharsets.UTF_8)) : null);
    }

    @Test
    void validatesStoresAndShowsTheInputsOnlyOnceSet() throws Exception {
        String pid = call("POST", "/projects", Json.obj().put("brief", "Move a box")).body().get("id").asString();
        String url = "/projects/" + pid + "/baseline";
        assertThat(call("GET", "/projects/" + pid, null).body().has("savings_inputs")).isFalse();

        assertThat(call("PUT", url, Json.obj()).status()).isEqualTo(422);
        assertThat(call("PUT", url, Json.obj().put("manual_cycle_s", 0)).status()).isEqualTo(422);
        assertThat(call("PUT", url, Json.obj().put("manual_cycle_s", "fast")).status()).isEqualTo(422);
        assertThat(call("PUT", url, Json.obj().put("operators", 0)).status()).isEqualTo(422);
        assertThat(call("PUT", url, Json.obj().put("operators", 1.5)).status()).isEqualTo(422);
        assertThat(call("PUT", url, Json.obj().put("days_per_week", 8)).status()).isEqualTo(422);
        assertThat(call("PUT", url, Json.obj().put("hours_per_shift", 25)).status()).isEqualTo(422);
        assertThat(call("PUT", url, Json.obj().put("supervision_min_per_shift", -1)).status()).isEqualTo(422);
        assertThat(call("PUT", "/projects/p_missing/baseline", Json.obj().put("operators", 2)).status()).isEqualTo(404);
        assertThat(call("GET", "/projects/" + pid, null).body().has("savings_inputs")).isFalse();

        JsonNode ok = call("PUT", url, Json.obj().put("manual_cycle_s", 12.5).put("operators", 2)).body().get("savings_inputs");
        assertThat(ok.get("manual_cycle_s").asDouble()).isEqualTo(12.5);
        assertThat(ok.get("operators").asInt()).isEqualTo(2);
        assertThat(ok.get("hours_per_shift").asDouble()).isEqualTo(8);
        assertThat(ok.get("days_per_week").asDouble()).isEqualTo(5);
        assertThat(ok.get("days_per_year").asDouble()).isEqualTo(250);
        assertThat(ok.get("supervision_min_per_shift").asDouble()).isEqualTo(15);

        // a later subset keeps the earlier values; null removes the baseline answers and restores a default
        JsonNode more = call("PUT", url, Json.obj().put("days_per_week", 6).put("supervision_min_per_shift", 30)).body().get("savings_inputs");
        assertThat(more.get("manual_cycle_s").asDouble()).isEqualTo(12.5);
        assertThat(more.get("days_per_week").asDouble()).isEqualTo(6);
        assertThat(more.get("supervision_min_per_shift").asDouble()).isEqualTo(30);
        JsonNode cleared = call("PUT", url, Json.obj().putNull("manual_cycle_s").putNull("days_per_week")).body().get("savings_inputs");
        assertThat(cleared.has("manual_cycle_s")).isFalse();
        assertThat(cleared.get("days_per_week").asDouble()).isEqualTo(5);
        assertThat(call("GET", "/projects/" + pid, null).body().get("savings_inputs").get("operators").asInt()).isEqualTo(2);
    }
}
