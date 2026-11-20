package dev.robode.studio.llm;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import dev.robode.studio.common.Json;
import dev.robode.studio.common.StudioException;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import tools.jackson.databind.JsonNode;

class KeysAndUsageTest {
    @TempDir Path runtime;

    @Test
    void keyFileIsOwnerOnlyAndKeysAreNeverReturned() throws IOException {
        Keys keys = new Keys(runtime, Map.of());
        keys.set("gemini", " AIza-secret-key ");
        assertThat(PosixFilePermissions.toString(Files.getPosixFilePermissions(keys.path()))).isEqualTo("rw-------");
        assertThat(keys.get("gemini")).isEqualTo("AIza-secret-key");
        String status = Json.pretty(keys.status());
        assertThat(status).doesNotContain("AIza-secret-key");
        assertThat(keys.status().get("gemini").get("saved").asBoolean()).isTrue();
        keys.set("gemini", "another-key");
        assertThat(PosixFilePermissions.toString(Files.getPosixFilePermissions(keys.path()))).isEqualTo("rw-------");
        try (var left = Files.list(keys.path().getParent())) {
            assertThat(left.map(p -> p.getFileName().toString())).containsExactly("keys.json"); // no temp files left
        }
        keys.delete("gemini");
        assertThat(keys.status().get("gemini").get("ready").asBoolean()).isFalse();
        assertThatThrownBy(() -> keys.set("claude_cli", "k")).hasMessage("claude_cli does not use an API key");
        assertThatThrownBy(() -> keys.set("gemini", "has space")).hasMessage("That does not look like an API key");
    }

    @Test
    void environmentIsTheFallback() {
        Keys keys = new Keys(runtime, Map.of("GEMINI_API_KEY", "from-env"));
        assertThat(keys.get("gemini")).isEqualTo("from-env");
        assertThat(keys.status().get("gemini").get("environment").asString()).isEqualTo("GEMINI_API_KEY");
        keys.set("gemini", "s3cret-value");
        assertThat(keys.get("gemini")).isEqualTo("s3cret-value");
        assertThat(Json.pretty(keys.status())).doesNotContain("from-env").doesNotContain("s3cret-value");
    }

    @Test
    void summaryEqualsAHandMadeLedger() throws IOException {
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        String recent = dev.robode.studio.common.Time.format(now.minusHours(1));
        String old = dev.robode.studio.common.Time.format(now.minusDays(10));
        Path file = runtime.resolve("llm").resolve("usage.jsonl");
        Files.createDirectories(file.getParent());
        Files.writeString(file, String.join("\n", List.of(
                "{\"at\":\"" + recent + "\",\"role\":\"spec\",\"profile\":\"p\",\"robot_id\":\"p_1\",\"task\":\"design\",\"model\":\"opus\",\"input\":2,\"cache_write\":100,\"cache_read\":300,\"output\":10,\"cost_usd\":0.1,\"seconds\":1.25,\"images\":1,\"web_calls\":2}",
                "not json",
                "{\"at\":\"" + recent + "\",\"role\":\"scene\",\"profile\":\"p\",\"robot_id\":null,\"task\":\"design\",\"model\":\"sonnet\",\"input\":8,\"cache_write\":0,\"cache_read\":100,\"output\":5,\"cost_usd\":0.2,\"seconds\":2.5}",
                "{\"at\":\"" + old + "\",\"role\":\"spec\",\"profile\":\"p\",\"robot_id\":\"p_1\",\"task\":\"design\",\"model\":\"opus\",\"input\":1,\"output\":1,\"cost_usd\":1.0}")) + "\n");
        UsageLedger ledger = new UsageLedger(runtime);

        JsonNode week = ledger.summary("7d");
        JsonNode t = week.get("totals");
        assertThat(t.get("calls").asInt()).isEqualTo(2);
        assertThat(t.get("input").asInt()).isEqualTo(10);
        assertThat(t.get("cache_write").asInt()).isEqualTo(100);
        assertThat(t.get("cache_read").asInt()).isEqualTo(400);
        assertThat(t.get("output").asInt()).isEqualTo(15);
        assertThat(t.get("cost_usd").asDouble()).isEqualTo(0.3);
        assertThat(t.get("seconds").asDouble()).isEqualTo(3.7); // Python rounds at every addition: round(round(1.25, 1) + 2.5, 1)
        assertThat(t.get("images").asInt()).isEqualTo(1);
        assertThat(t.get("web_calls").asInt()).isEqualTo(2);
        assertThat(t.get("cache_hit_rate").asDouble()).isEqualTo(0.784); // 400 / 510
        assertThat(week.get("by_role").propertyNames()).containsExactly("scene", "spec");
        assertThat(week.get("by_model").get("opus").get("calls").asInt()).isEqualTo(1);
        assertThat(week.get("by_robot_id").propertyNames()).containsExactly("other", "p_1");
        assertThat(week.get("by_task").get("design").get("calls").asInt()).isEqualTo(2);
        assertThat(week.get("recent").size()).isEqualTo(2);
        assertThat(week.get("recent").get(0).get("role").asString()).isEqualTo("scene"); // newest first
        assertThat(ledger.summary("all").get("totals").get("calls").asInt()).isEqualTo(3);
        assertThat(ledger.summary("all").get("by_day").size()).isEqualTo(2);
        assertThatThrownBy(() -> ledger.summary("1y")).isInstanceOf(StudioException.class)
                .hasMessage("period is today, 7d, 30d or all");
    }

    @Test
    void emptyLedgerAndAppendFormat() {
        UsageLedger ledger = new UsageLedger(runtime);
        assertThat(ledger.summary("all").get("totals").get("cache_hit_rate").isNull()).isTrue();
        ledger.record("spec", "claude_opus", "p_1", "design", Json.parse("{\"model\":\"m\",\"input\":1,\"pages_opened\":[\"x\"]}"));
        JsonNode line = ledger.entries(null).get(0);
        assertThat(line.propertyNames()).containsExactly("at", "role", "profile", "robot_id", "task", "model", "input");
        ledger.reset();
        assertThat(ledger.summary("all").get("totals").get("calls").asInt()).isZero();
    }
}
