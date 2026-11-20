package dev.robode.studio.llm;

import dev.robode.studio.common.Json;
import dev.robode.studio.common.StudioException;
import dev.robode.studio.config.StudioProperties;
import dev.robode.studio.meta.MetaService;
import java.nio.file.Path;
import java.util.List;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/** Saved LLM profiles (runtime/llm/profiles.json) and which profile each role uses; resolves roles for task envelopes. */
@Component
public class LlmSettings {
    private static final Logger LOG = LoggerFactory.getLogger(LlmSettings.class);
    public static final List<String> ROLES = List.of("spec", "scene", "robot", "revise", "search", "extract", "analysis", "objects", "ask", "checks");
    private static final List<String> EFFORTS = List.of("", "low", "medium", "high", "xhigh", "max");

    private final Path path;
    private final MetaService meta;
    private final Keys keys;
    private final Object lock = new Object();

    public LlmSettings(StudioProperties props, MetaService meta, Keys keys) {
        this.path = props.runtime().resolve("llm").resolve("profiles.json");
        this.meta = meta;
        this.keys = keys;
    }

    public Keys keys() { return keys; }

    private static ObjectNode defaultRoles() {
        ObjectNode r = Json.obj();
        r.put("spec", "claude_opus").put("scene", "claude_sonnet").put("robot", "claude_opus").put("revise", "claude_opus")
                .put("search", "claude_sonnet").put("extract", "claude_sonnet").put("analysis", "claude_sonnet").put("objects", "claude_haiku").put("ask", "claude_sonnet").put("checks", "claude_opus");
        return r;
    }

    private static ObjectNode defaultProfile(String id, String name, String model) {
        return Json.obj().put("id", id).put("name", name).put("kind", "claude_cli").put("model", model)
                .put("effort", "").put("timeout_s", 240).put("cli_path", "");
    }

    private static final List<String[]> DEFAULT_PROFILES = List.of(
            new String[] {"claude_opus", "Claude Opus 5.5 (Claude Code CLI)", "claude-opus-5-5"},
            new String[] {"claude_sonnet", "Claude Sonnet 5.5 (Claude Code CLI)", "claude-sonnet-5-5"},
            new String[] {"claude_haiku", "Claude Haiku 5.5 (Claude Code CLI)", "claude-haiku-5-5"});

    /** profiles + roles (profiles.json, or the defaults when it does not exist); later-added roles get their default. */
    public ObjectNode data() {
        synchronized (lock) {
            ObjectNode data = Json.readObject(path);
            if (data == null) {
                data = Json.obj();
                ArrayNode profiles = Json.arr();
                DEFAULT_PROFILES.forEach(p -> profiles.add(defaultProfile(p[0], p[1], p[2])));
                data.set("profiles", profiles);
                data.set("roles", defaultRoles());
            } else {
                data = Json.withoutSchemaVersion(data);
            }
            ObjectNode roles = defaultRoles();
            if (data.get("roles") instanceof ObjectNode saved) saved.properties().forEach(e -> roles.set(e.getKey(), e.getValue()));
            data.set("roles", roles);
            if (!(data.get("profiles") instanceof ArrayNode)) data.set("profiles", Json.arr());
            // a default profile that a role points to (a role added later) is added when the saved file lacks it
            ArrayNode saved = (ArrayNode) data.get("profiles");
            for (String[] p : DEFAULT_PROFILES) {
                boolean have = saved.valueStream().anyMatch(n -> p[0].equals(Json.text(n, "id")));
                boolean used = roles.valueStream().anyMatch(v -> p[0].equals(v.asString()));
                if (!have && used) saved.add(defaultProfile(p[0], p[1], p[2]));
            }
            return data;
        }
    }

    private void save(ObjectNode data) { Json.write(path, data); }

    public JsonNode roles() { return data().get("roles"); }

    private JsonNode metaOrEmpty() {
        try {
            return meta.get();
        } catch (RuntimeException e) {
            LOG.warn("Provider list unavailable, using built-in kinds: {}", e.getMessage());
            return Json.obj();
        }
    }

    private List<String> kinds() {
        JsonNode k = metaOrEmpty().get("kinds");
        if (k == null || !k.isArray()) return List.of("claude_cli", "gemini");
        return k.valueStream().map(JsonNode::asString).sorted().toList();
    }

    public ObjectNode describe() {
        ObjectNode out = data();
        JsonNode m = metaOrEmpty();
        ArrayNode kinds = Json.arr();
        kinds().forEach(kinds::add);
        out.set("kinds", kinds);
        ArrayNode roles = Json.arr();
        ROLES.forEach(roles::add);
        out.set("roles_available", roles);
        out.set("keys", keys.status());
        JsonNode models = m.get("models");
        out.set("models", models != null && models.isObject() ? models : Json.obj());
        return out;
    }

    public ObjectNode upsert(JsonNode body, String profileId) {
        ObjectNode profile = validate(body);
        if (!kinds().contains(profile.get("kind").asString()))
            throw StudioException.value("Unknown provider kind " + profile.get("kind").asString());
        if (profile.get("kind").asString().equals("gemini") && !profile.get("model").asString().startsWith("gemini"))
            throw StudioException.value("A Gemini profile needs a Gemini model name, such as gemini-3.8-flash");
        synchronized (lock) {
            ObjectNode data = data();
            String id = profileId != null ? profileId : UUID.randomUUID().toString().replace("-", "").substring(0, 10);
            ObjectNode entry = Json.obj();
            entry.put("id", id);
            profile.properties().forEach(e -> entry.set(e.getKey(), e.getValue()));
            ArrayNode profiles = Json.arr();
            data.get("profiles").forEach(p -> { if (!id.equals(Json.text(p, "id"))) profiles.add(p); });
            profiles.add(entry);
            data.set("profiles", profiles);
            save(data);
            return entry;
        }
    }

    /** The pydantic Profile model: name 1..120, kind ^[a-z_]+$, model <=120, effort, timeout_s 10..900, cli_path <=1000. */
    static ObjectNode validate(JsonNode body) {
        if (body == null || !body.isObject()) throw StudioException.value("The profile must be a JSON object");
        String name = text(body, "name", null);
        if (name == null || name.isEmpty() || name.length() > 120) throw StudioException.value("name must be 1 to 120 characters");
        String kind = text(body, "kind", "claude_cli");
        if (!kind.matches("^[a-z_]+$")) throw StudioException.value("kind must be lowercase letters and underscores");
        String model = text(body, "model", "claude-sonnet-5-5");
        if (model.length() > 120) throw StudioException.value("model must be at most 120 characters");
        String effort = text(body, "effort", "");
        if (!EFFORTS.contains(effort)) throw StudioException.value("effort must be one of: low, medium, high, xhigh, max, or empty");
        int timeout = 240;
        if (body.hasNonNull("timeout_s")) {
            if (!body.get("timeout_s").canConvertToInt()) throw StudioException.value("timeout_s must be an integer from 10 to 900");
            timeout = body.get("timeout_s").asInt();
        }
        if (timeout < 10 || timeout > 900) throw StudioException.value("timeout_s must be an integer from 10 to 900");
        String cli = text(body, "cli_path", "");
        if (cli.length() > 1000) throw StudioException.value("cli_path must be at most 1000 characters");
        return Json.obj().put("name", name).put("kind", kind).put("model", model).put("effort", effort)
                .put("timeout_s", timeout).put("cli_path", cli);
    }

    private static String text(JsonNode body, String field, String fallback) {
        JsonNode v = body.get(field);
        if (v == null || v.isNull()) return fallback;
        if (!v.isString()) throw StudioException.value(field + " must be a string");
        return v.asString();
    }

    public void delete(String profileId) {
        synchronized (lock) {
            ObjectNode data = data();
            if (data.get("roles").valueStream().anyMatch(v -> profileId.equals(v.asString())))
                throw StudioException.value("This profile is assigned to a role; assign another profile first");
            ArrayNode profiles = Json.arr();
            data.get("profiles").forEach(p -> { if (!profileId.equals(Json.text(p, "id"))) profiles.add(p); });
            data.set("profiles", profiles);
            save(data);
        }
    }

    public JsonNode setRoles(JsonNode roles) {
        if (roles == null || !roles.isObject()) throw StudioException.value("roles must be an object of role to profile id");
        synchronized (lock) {
            ObjectNode data = data();
            List<String> ids = data.get("profiles").valueStream().map(p -> Json.text(p, "id")).toList();
            ObjectNode current = (ObjectNode) data.get("roles");
            for (var e : roles.properties()) {
                if (!e.getValue().isString()) throw StudioException.value("roles must map to profile ids");
                if (!ROLES.contains(e.getKey()) || !ids.contains(e.getValue().asString()))
                    throw StudioException.value("Unknown role or profile: " + e.getKey() + " -> " + e.getValue().asString());
                current.put(e.getKey(), e.getValue().asString());
            }
            save(data);
            return current;
        }
    }

    public ObjectNode profile(String profileId) {
        for (JsonNode p : data().get("profiles")) if (profileId.equals(Json.text(p, "id"))) return (ObjectNode) p.deepCopy();
        throw StudioException.notFound("LLM profile " + profileId + " not found");
    }

    /** The profile dict for the envelope: api_key is the saved/environment key for keyed kinds, null otherwise. */
    public ObjectNode resolved(String profileId) {
        ObjectNode p = profile(profileId);
        if (Keys.KEYED.contains(Json.text(p, "kind"))) p.put("api_key", keys.get(Json.text(p, "kind")));
        else p.putNull("api_key");
        return p;
    }

    /** {"roles": {all roles resolved}, "profile": null} */
    public ObjectNode resolveRoles() {
        ObjectNode data = data();
        ObjectNode roles = Json.obj();
        for (String role : ROLES) {
            String id = Json.text(data.get("roles"), role);
            try {
                roles.set(role, resolved(id));
            } catch (StudioException e) {
                throw StudioException.value("Role " + role + " uses LLM profile " + id + ", which does not exist");
            }
        }
        return envelope(roles, null);
    }

    public ObjectNode forProfile(String profileId) { return envelope(Json.obj(), resolved(profileId)); }

    /** An envelope llm block for tasks that need no model. */
    public static ObjectNode none() { return envelope(Json.obj(), null); }

    private static ObjectNode envelope(ObjectNode roles, ObjectNode profile) {
        ObjectNode llm = Json.obj();
        llm.set("roles", roles);
        if (profile == null) llm.putNull("profile"); else llm.set("profile", profile);
        return llm;
    }
}
