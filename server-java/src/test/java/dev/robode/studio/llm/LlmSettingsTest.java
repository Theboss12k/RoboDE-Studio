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

    LlmSettings settings() { return settings(Map.of()); }

    LlmSettings settings(Map<String, String> env) {
        StudioProperties props = new StudioProperties(runtime, runtime, null, null, null, null, null, null, null, null, null, null, null,
                null, null, null, null);
        return new LlmSettings(props, null, new Keys(runtime, Map.of()), env);
    }

    @Test
    void googleEditionDefaultsUseGeminiForEveryRole() {
        assertThat(LlmSettings.ROLES).containsExactly("spec", "scene", "robot", "revise", "search", "extract", "analysis", "objects", "ask", "checks");
        ObjectNode data = settings().data();
        JsonNode roles = data.get("roles");
        for (String r : new String[] {"spec", "revise", "checks", "analysis", "robot"}) assertThat(roles.get(r).asString()).isEqualTo("gemini_pro");
        for (String r : new String[] {"scene", "search", "extract", "ask"}) assertThat(roles.get(r).asString()).isEqualTo("gemini_flash");
        assertThat(roles.get("objects").asString()).isEqualTo("gemini_flash_lite");
        assertThat(data.get("profiles")).hasSize(11);
        JsonNode lite = null;
        for (JsonNode p : data.get("profiles")) {
            assertThat(Json.text(p, "kind")).matches("gemini|gemini_cli|antigravity_cli|vertex_gemini");
            if ("gemini_flash_lite".equals(Json.text(p, "id"))) lite = p;
        }
        assertThat(lite).isNotNull();
        assertThat(Json.text(lite, "kind")).isEqualTo("gemini");
        assertThat(Json.text(lite, "model")).isEqualTo("gemini-3.5-flash-lite");
        assertThat(data.toString()).doesNotContain("claude").doesNotContain("api_key");
        assertThat(Json.text(settings().profile("gemini_cli_pro"), "kind")).isEqualTo("gemini_cli");
        assertThat(Json.text(settings().profile("gemini_cli_flash"), "kind")).isEqualTo("gemini_cli");
        for (String id : new String[] {"antigravity_flash_high", "antigravity_flash_medium", "antigravity_flash_low"})
            assertThat(Json.text(settings().profile(id), "kind")).isEqualTo("antigravity_cli");
        assertThat(settings().resolved("antigravity_flash_low").get("api_key").isNull()).isTrue();   // keyless kind
    }

    @Test
    void vertexProfilesExistAndAreTheRoleDefaultsOnCloudRunOrWhenAsked() {
        for (Map<String, String> env : java.util.List.of(Map.of("K_SERVICE", "robode-studio"), Map.of("STUDIO_LLM_DEFAULT", "vertex"))) {
            JsonNode roles = settings(env).data().get("roles");
            for (String r : new String[] {"spec", "revise", "checks", "analysis", "robot"}) assertThat(roles.get(r).asString()).isEqualTo("vertex_pro");
            for (String r : new String[] {"scene", "search", "extract", "ask"}) assertThat(roles.get(r).asString()).isEqualTo("vertex_flash");
            assertThat(roles.get("objects").asString()).isEqualTo("vertex_flash_lite");
        }
        ObjectNode flash = settings().profile("vertex_flash");   // the profile exists even when the roles do not use it
        assertThat(Json.text(flash, "kind")).isEqualTo("vertex_gemini");
        assertThat(Json.text(flash, "location")).isEqualTo("global");
        assertThat(settings().resolved("vertex_pro").get("api_key").isNull()).isTrue();   // no key, ever
        ObjectNode p = LlmSettings.validate(Json.parse("{\"name\": \"V\", \"kind\": \"vertex_gemini\", \"model\": \"gemini-3.8-flash\", \"location\": \"us-central1\", \"project\": \"my-proj-1\"}"));
        assertThat(Json.text(p, "location")).isEqualTo("us-central1");
        assertThat(Json.text(p, "project")).isEqualTo("my-proj-1");
        assertThat(p.has("api_key")).isFalse();
    }

    @Test
    void aGeminiCliProfileIsValidWithoutAKey() {
        ObjectNode p = LlmSettings.validate(Json.parse("{\"name\": \"CLI\", \"kind\": \"gemini_cli\", \"model\": \"gemini-3.8-flash\", \"cli_path\": \"/usr/local/bin/gemini\"}"));
        assertThat(Json.text(p, "kind")).isEqualTo("gemini_cli");
        assertThat(Json.text(p, "cli_path")).isEqualTo("/usr/local/bin/gemini");
        assertThat(p.has("api_key")).isFalse();
        assertThat(settings().resolved("gemini_cli_flash").get("api_key").isNull()).isTrue();   // keyless kind: no key in the envelope
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
        assertThat(s.roles().get("objects").asString()).isEqualTo("gemini_flash_lite");
        assertThat(s.profile("gemini_flash_lite").get("model").asString()).isEqualTo("gemini-3.5-flash-lite");
        assertThat(s.roles().get("spec").asString()).isEqualTo("claude_opus");   // a saved choice is kept
        // every role resolves, so a task envelope can be built
        assertThat(s.resolveRoles().get("roles").propertyNames()).containsExactlyInAnyOrderElementsOf(LlmSettings.ROLES);
        // a user who points the role at another profile keeps that choice
        s.setRoles(Json.parse("{\"objects\": \"claude_sonnet\"}"));
        assertThat(s.roles().get("objects").asString()).isEqualTo("claude_sonnet");
    }
}
