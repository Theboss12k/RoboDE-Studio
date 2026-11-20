package dev.robode.studio.llm;

import static org.assertj.core.api.Assertions.assertThat;

import dev.robode.studio.common.Json;
import dev.robode.studio.config.StudioProperties;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

/** Roles and default profiles; the Python side (llm/registry.py) has the same lists. */
class LlmSettingsTest {
    @TempDir Path runtime;

    LlmSettings settings() {
        StudioProperties props = new StudioProperties(runtime, runtime, null, null, null, null, null, null, null, null, null, null, null,
                null, null, null, null);
        return new LlmSettings(props, null, new Keys(runtime, Map.of()));
    }

    @Test
    void objectsRoleDefaultsToHaiku() {
        assertThat(LlmSettings.ROLES).containsExactly("spec", "scene", "robot", "revise", "search", "extract", "analysis", "objects");
        ObjectNode data = settings().data();
        assertThat(data.get("roles").get("objects").asString()).isEqualTo("claude_haiku");
        JsonNode haiku = null;
        for (JsonNode p : data.get("profiles")) if ("claude_haiku".equals(Json.text(p, "id"))) haiku = p;
        assertThat(haiku).isNotNull();
        assertThat(Json.text(haiku, "name")).isEqualTo("Claude Haiku 5.5 (Claude Code CLI)");
        assertThat(Json.text(haiku, "model")).isEqualTo("claude-haiku-5-5");
        assertThat(data.get("profiles")).hasSize(3);
    }

    @Test
    void anOlderProfilesFileGetsTheNewRoleAndProfile() throws IOException {
        Files.createDirectories(runtime.resolve("llm"));
        Files.writeString(runtime.resolve("llm/profiles.json"), """
                {"schemaVersion": 1, "profiles": [
                  {"id": "claude_opus", "name": "Opus", "kind": "claude_cli", "model": "claude-opus-5-5", "effort": "", "timeout_s": 240, "cli_path": ""},
                  {"id": "claude_sonnet", "name": "Sonnet", "kind": "claude_cli", "model": "claude-sonnet-5-5", "effort": "", "timeout_s": 240, "cli_path": ""}],
                 "roles": {"spec": "claude_opus", "scene": "claude_sonnet", "robot": "claude_opus", "revise": "claude_opus",
                           "search": "claude_sonnet", "extract": "claude_sonnet", "analysis": "claude_sonnet"}}
                """);
        LlmSettings s = settings();
        assertThat(s.roles().get("objects").asString()).isEqualTo("claude_haiku");
        assertThat(s.profile("claude_haiku").get("model").asString()).isEqualTo("claude-haiku-5-5");
        // every role resolves, so a task envelope can be built
        assertThat(s.resolveRoles().get("roles").propertyNames()).containsExactlyInAnyOrderElementsOf(LlmSettings.ROLES);
        // a user who points the role at another profile keeps that choice
        s.setRoles(Json.parse("{\"objects\": \"claude_sonnet\"}"));
        assertThat(s.roles().get("objects").asString()).isEqualTo("claude_sonnet");
    }
}
