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
    /** Cloud Run (K_SERVICE) or STUDIO_LLM_DEFAULT=vertex: a new install's roles use the Vertex AI profiles. */
    private final boolean vertexDefault;

    @org.springframework.beans.factory.annotation.Autowired
    public LlmSettings(StudioProperties props, MetaService meta, Keys keys) { this(props, meta, keys, System.getenv()); }

    public LlmSettings(StudioProperties props, MetaService meta, Keys keys, java.util.Map<String, String> env) {
        this.vertexDefault = env.get("K_SERVICE") != null && !env.get("K_SERVICE").isBlank()
                || "vertex".equalsIgnoreCase(env.getOrDefault("STUDIO_LLM_DEFAULT", "").trim());
        this.path = props.runtime().resolve("llm").resolve("profiles.json");
        this.meta = meta;
        this.keys = keys;
    }

    public Keys keys() { return keys; }

    private ObjectNode defaultRoles() {
        ObjectNode r = Json.obj();
        if (vertexDefault) {
            r.put("spec", "vertex_pro").put("scene", "vertex_flash").put("robot", "vertex_pro").put("revise", "vertex_pro")
                    .put("search", "vertex_flash").put("extract", "vertex_flash").put("analysis", "vertex_pro").put("objects", "vertex_flash_lite").put("ask", "vertex_flash").put("checks", "vertex_pro");
            return r;
        }
        r.put("spec", "gemini_pro").put("scene", "gemini_flash").put("robot", "gemini_pro").put("revise", "gemini_pro")
                .put("search", "gemini_flash").put("extract", "gemini_flash").put("analysis", "gemini_pro").put("objects", "gemini_flash_lite").put("ask", "gemini_flash").put("checks", "gemini_pro");
        return r;
    }

    private static ObjectNode defaultProfile(String id, String name, String kind, String model) {
        ObjectNode p = Json.obj().put("id", id).put("name", name).put("kind", kind).put("model", model)
                .put("effort", "").put("timeout_s", 240).put("cli_path", "");
        if ("vertex_gemini".equals(kind)) p.put("location", "global").put("project", "");
        return p;
    }

    /** {id, name, kind, model}: the Google edition's defaults (Gemini API for every role; Gemini CLI profiles for local sign-in). */
    private static final List<String[]> DEFAULT_PROFILES = List.of(
            new String[] {"gemini_pro", "Gemini 3.1 Pro (Gemini API)", "gemini", "gemini-3.1-pro-preview"},
            new String[] {"gemini_flash", "Gemini 3.8 Flash (Gemini API)", "gemini", "gemini-3.8-flash"},
            new String[] {"gemini_flash_lite", "Gemini 3.5 Flash-Lite (Gemini API)", "gemini", "gemini-3.5-flash-lite"},
            new String[] {"vertex_pro", "Gemini 3.1 Pro (Vertex AI, Google Cloud project)", "vertex_gemini", "gemini-3.1-pro-preview"},
            new String[] {"vertex_flash", "Gemini 3.8 Flash (Vertex AI, Google Cloud project)", "vertex_gemini", "gemini-3.8-flash"},
            new String[] {"vertex_flash_lite", "Gemini 3.5 Flash-Lite (Vertex AI, Google Cloud project)", "vertex_gemini", "gemini-3.5-flash-lite"},
            new String[] {"gemini_cli_pro", "Gemini 3.1 Pro (Gemini CLI, Google sign-in)", "gemini_cli", "gemini-3.1-pro-preview"},
            new String[] {"gemini_cli_flash", "Gemini 3.8 Flash (Gemini CLI, Google sign-in)", "gemini_cli", "gemini-3.8-flash"},
            new String[] {"antigravity_flash_high", "Gemini 3.8 Flash High (Antigravity CLI)", "antigravity_cli", "gemini-3.8-flash-high"},
            new String[] {"antigravity_flash_medium", "Gemini 3.8 Flash Medium (Antigravity CLI)", "antigravity_cli", "gemini-3.8-flash-medium"},
            new String[] {"antigravity_flash_low", "Gemini 3.8 Flash Low (Antigravity CLI)", "antigravity_cli", "gemini-3.8-flash-low"});

    /** profiles + roles (profiles.json, or the defaults when it does not exist); later-added roles get their default. */
    public ObjectNode data() {
        synchronized (lock) {
            ObjectNode data = Json.readObject(path);
            if (data == null) {
                data = Json.obj();
                ArrayNode profiles = Json.arr();
                DEFAULT_PROFILES.forEach(p -> profiles.add(defaultProfile(p[0], p[1], p[2], p[3])));
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
                if (!have && used) saved.add(defaultProfile(p[0], p[1], p[2], p[3]));
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
        if (k == null || !k.isArray()) return List.of("antigravity_cli", "claude_cli", "gemini", "gemini_cli", "vertex_gemini");
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
        if (List.of("gemini", "gemini_cli", "antigravity_cli", "vertex_gemini").contains(profile.get("kind").asString()) && !profile.get("model").asString().startsWith("gemini"))
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

    /** The pydantic Profile model: name 1..120, kind ^[a-z_]+$, model <=120, effort, timeout_s 10..900, cli_path <=1000, location and project (Vertex AI; optional). */
    static ObjectNode validate(JsonNode body) {
        if (body == null || !body.isObject()) throw StudioException.value("The profile must be a JSON object");
        String name = text(body, "name", null);
        if (name == null || name.isEmpty() || name.length() > 120) throw StudioException.value("name must be 1 to 120 characters");
        String kind = text(body, "kind", "gemini");
        if (!kind.matches("^[a-z_]+$")) throw StudioException.value("kind must be lowercase letters and underscores");
        String model = text(body, "model", "gemini-3.8-flash");
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
        String location = text(body, "location", "");
        if (location.length() > 40 || !location.matches("^[a-z0-9-]*$")) throw StudioException.value("location must be a Google Cloud location such as global or us-central1");
        String project = text(body, "project", "");
        if (project.length() > 100 || !project.matches("^[a-z0-9:.\\-]*$")) throw StudioException.value("project must be a Google Cloud project id");
        ObjectNode out = Json.obj().put("name", name).put("kind", kind).put("model", model).put("effort", effort)
                .put("timeout_s", timeout).put("cli_path", cli);
        if ("vertex_gemini".equals(kind)) out.put("location", location.isEmpty() ? "global" : location).put("project", project);
        return out;
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
