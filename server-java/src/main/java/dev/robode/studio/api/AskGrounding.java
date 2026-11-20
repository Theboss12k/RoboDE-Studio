package dev.robode.studio.api;

import dev.robode.studio.common.Json;
import dev.robode.studio.common.StudioException;
import dev.robode.studio.store.ProjectStore;
import dev.robode.studio.store.StudioStore;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * The facts the Ask agent may answer from, read from storage for the ids the client names. Numbers never come from the
 * client. Simulation frames, URDF, scene and part geometry are never included; the whole thing is trimmed to about
 * {@link #MAX_BYTES} by dropping the least important parts first.
 */
@Component
class AskGrounding {
    static final int MAX_BYTES = 60 * 1024;
    private static final int MAX_VERSIONS = 40;
    private static final int MAX_NEAR_EVENTS = 20;
    private static final Set<String> NEVER = Set.of("frames", "images", "image", "image_url", "image_b64", "data_url", "urdf");

    /** What the client is looking at. Ids and the moment only. */
    record Context(String projectId, String stage, Integer version, Double t, String candidate, Integer build, String selection) {}

    private final ProjectStore store;
    private final StudioStore studio;

    AskGrounding(ProjectStore store, StudioStore studio) {
        this.store = store;
        this.studio = studio;
    }

    /** 404 when a referenced project, version, build or candidate does not exist. */
    void check(Context c) {
        if (c.projectId() == null) {
            if (c.version() != null || c.build() != null || c.candidate() != null)
                throw StudioException.value("context.project_id is required with version, build or candidate");
            return;
        }
        store.require(c.projectId());
        if (c.version() != null) store.version(c.projectId(), c.version());
        if (c.build() != null) studio.build(c.projectId(), c.build());
        if (c.candidate() != null) store.candidate(c.projectId(), c.candidate());
    }

    ObjectNode build(Context c) {
        ObjectNode g = Json.obj();
        if (c.projectId() == null) {
            g.putNull("project");
            g.put("note", "No project is open, so there are no stored facts to answer from.");
            return g;
        }
        String pid = c.projectId();
        g.set("project", project(pid));
        List<Integer> versions = store.versions(pid);
        Integer n = c.version() != null ? c.version() : (versions.isEmpty() ? null : versions.get(versions.size() - 1));
        if (n != null) g.set("version", version(pid, n, c.t()));
        if (c.build() != null) g.set("build", buildSection(pid, c.build()));
        if (c.candidate() != null) g.set("candidate", candidate(pid, c.candidate()));
        trim(g);
        return g;
    }

    // -- sections -----------------------------------------------------------------------------------------------
    private ObjectNode project(String pid) {
        ObjectNode m = store.meta(pid);
        ObjectNode out = Json.obj();
        out.put("id", pid);
        for (String k : List.of("title", "brief", "stage", "approved_version", "decision", "savings_inputs"))
            if (m.has(k)) out.set(k, clean(m.get(k)));
        List<Integer> all = store.versions(pid);
        ArrayNode versions = Json.arr();
        for (int n : all.subList(Math.max(0, all.size() - MAX_VERSIONS), all.size())) {
            ObjectNode v = store.version(pid, n);
            ObjectNode row = Json.obj();
            row.put("version", n);
            row.set("parent_version", v.hasNonNull("parent_version") ? v.get("parent_version") : Json.MAPPER.nullNode());
            row.put("summary", firstNonBlank(Json.text(v, "change_summary"), Json.text(v.path("spec"), "summary"), Json.text(v.path("spec"), "title")));
            if (v.hasNonNull("change_request")) row.put("change_request", Json.text(v, "change_request"));
            versions.add(row);
        }
        out.set("versions", versions);
        List<Integer> builds = store.builds(pid);
        if (!builds.isEmpty()) {
            ArrayNode numbers = Json.arr();
            builds.forEach(numbers::add);
            out.set("builds", numbers);
            JsonNode approvedBuild = m.path("studio").get("approved_build");
            if (approvedBuild != null && !approvedBuild.isNull()) out.set("approved_build", approvedBuild);
        }
        ArrayNode cands = Json.arr();
        for (JsonNode c : store.candidates(pid)) {
            if (cands.size() >= 12) break;
            cands.add(Json.obj().put("id", String.valueOf(Json.text(c, "id"))).put("manufacturer", String.valueOf(Json.text(c, "manufacturer")))
                    .put("model", String.valueOf(Json.text(c, "model"))));
        }
        if (!cands.isEmpty()) out.set("candidates", cands);
        return out;
    }

    private ObjectNode version(String pid, int n, Double t) {
        ObjectNode v = store.version(pid, n);
        ObjectNode out = Json.obj();
        out.put("version", n);
        for (String k : List.of("parent_version", "created", "change_request", "change_summary", "approved"))
            if (v.has(k)) out.set(k, v.get(k));
        if (v.has("spec")) out.set("spec", clean(v.get("spec")));
        if (v.has("requirements")) out.set("requirements", clean(v.get("requirements")));
        JsonNode sim = v.path("sim");
        out.set("checks", sim.has("checks") ? clean(sim.get("checks")) : Json.arr());
        out.set("events", sim.has("events") ? clean(sim.get("events")) : Json.arr());
        if (sim.has("duration_s")) out.set("duration_s", sim.get("duration_s"));
        if (sim.has("ok")) out.set("ok", sim.get("ok"));
        if (t != null) {
            ObjectNode moment = moment(sim, t);
            if (moment != null) out.set("moment", moment);
        }
        return out;
    }

    /** The stored frame nearest `t` (one frame, never the list) and the events within 0.5 s of `t`. */
    private static ObjectNode moment(JsonNode sim, double t) {
        JsonNode frames = sim.get("frames");
        if (frames == null || !frames.isArray()) return null;
        JsonNode best = null;
        double bestGap = Double.MAX_VALUE;
        for (JsonNode f : frames) {
            if (!f.isObject() || !f.path("t").isNumber()) continue;
            double gap = Math.abs(f.get("t").asDouble() - t);
            if (gap < bestGap) { bestGap = gap; best = f; }
        }
        if (best == null) return null;
        ObjectNode out = Json.obj();
        out.put("requested_t", t);
        for (String k : List.of("t", "phase", "joints", "tcp", "holding", "base")) if (best.has(k)) out.set(k, best.get(k));
        List<JsonNode> close = new ArrayList<>();
        if (sim.get("events") instanceof ArrayNode events)
            for (JsonNode e : events) if (e.path("t").isNumber() && Math.abs(e.get("t").asDouble() - t) <= 0.5) close.add(e);
        close.sort(java.util.Comparator.comparingDouble(e -> Math.abs(e.get("t").asDouble() - t)));
        ArrayNode near = Json.arr();
        close.stream().limit(MAX_NEAR_EVENTS).forEach(near::add);   // the nearest few, so a busy moment cannot fill the grounding
        out.set("events_near", near);
        return out;
    }

    private ObjectNode buildSection(String pid, int n) {
        ObjectNode b = studio.build(pid, n);
        ObjectNode out = Json.obj();
        for (String k : List.of("build", "design_version", "member", "parent_build", "created", "request", "summary", "family", "search_date", "approved"))
            if (b.has(k)) out.set(k, b.get(k));
        ArrayNode motors = Json.arr();
        if (b.get("motors") instanceof ArrayNode rows)
            for (JsonNode row : rows) {
                ObjectNode m = ((ObjectNode) clean(row));
                m.remove("alternatives");
                m.remove("url");
                motors.add(m);
            }
        out.set("motors", motors);
        ArrayNode electronics = Json.arr();
        if (b.get("electronics") instanceof ArrayNode rows)
            for (JsonNode row : rows) {
                ObjectNode e = ((ObjectNode) clean(row));
                e.remove("url");
                electronics.add(e);
            }
        out.set("electronics", electronics);
        if (b.has("checks")) out.set("checks", clean(b.get("checks")));
        if (b.has("sourcing_notes")) out.set("sourcing_notes", b.get("sourcing_notes"));
        return out;
    }

    private ObjectNode candidate(String pid, String cid) {
        ObjectNode out = ((ObjectNode) clean(store.candidate(pid, cid)));
        try {
            ObjectNode a = store.analysis(pid, cid);
            ObjectNode analysis = Json.obj();
            for (String k : List.of("manufacturer_spec", "differences", "unverified", "provenance", "created"))
                if (a.has(k)) analysis.set(k, clean(a.get(k)));
            JsonNode sim = a.path("sim");
            if (sim.has("checks")) analysis.set("checks", clean(sim.get("checks")));
            if (sim.has("ok")) analysis.set("sim_ok", sim.get("ok"));
            out.set("analysis", analysis);
        } catch (StudioException e) {
            // no analysis yet: the candidate alone is the grounding
        }
        return out;
    }

    // -- helpers ------------------------------------------------------------------------------------------------
    /** A deep copy without the keys that are bulky or not facts (frames, images, urdf) and without the schema marker. */
    static JsonNode clean(JsonNode node) {
        if (node instanceof ObjectNode o) {
            ObjectNode out = Json.obj();
            for (var e : o.properties()) if (!NEVER.contains(e.getKey()) && !e.getKey().equals("schemaVersion")) out.set(e.getKey(), clean(e.getValue()));
            return out;
        }
        if (node instanceof ArrayNode a) {
            ArrayNode out = Json.arr();
            a.forEach(x -> out.add(clean(x)));
            return out;
        }
        return node.deepCopy();
    }

    private static String firstNonBlank(String... values) {
        for (String v : values) if (v != null && !v.isBlank()) return v.length() > 300 ? v.substring(0, 300) : v;
        return "";
    }

    private static int size(JsonNode g) { return g.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8).length; }

    /** Drops the least important parts, one step at a time, until the grounding fits. */
    static void trim(ObjectNode g) {
        List<Runnable> steps = new ArrayList<>();
        steps.add(() -> capArray(g.path("version"), "events", 30));
        steps.add(() -> eachMotor(g, m -> { m.remove("rejected"); m.remove("calcs"); }));
        steps.add(() -> eachRow(g.path("build"), "electronics", e -> e.remove("rejected")));
        steps.add(() -> removeKey(g.path("build"), "sourcing_notes"));
        steps.add(() -> eachRow(g.path("version"), "checks", c -> c.remove("calc")));
        steps.add(() -> removeKey(g.path("candidate"), "analysis"));
        steps.add(() -> capArray(g.path("project"), "versions", 10));
        steps.add(() -> eachRow(g.path("version"), "requirements", r -> r.remove("calc")));
        steps.add(() -> removeKey(g.path("version"), "events"));
        steps.add(() -> removeKey(g.path("build"), "electronics"));
        steps.add(() -> removeKey(g.path("version"), "spec"));
        for (Runnable step : steps) {
            if (size(g) <= MAX_BYTES) return;
            step.run();
        }
    }

    private static void capArray(JsonNode parent, String key, int keep) {
        if (parent instanceof ObjectNode o && o.get(key) instanceof ArrayNode a)
            while (a.size() > keep) a.remove(key.equals("versions") ? 0 : a.size() - 1);   // old versions go first, later events go first
    }

    private static void removeKey(JsonNode parent, String key) {
        if (parent instanceof ObjectNode o) o.remove(key);
    }

    private static void eachRow(JsonNode parent, String key, java.util.function.Consumer<ObjectNode> change) {
        if (parent.get(key) instanceof ArrayNode a) for (JsonNode row : a) if (row instanceof ObjectNode o) change.accept(o);
    }

    private static void eachMotor(ObjectNode g, java.util.function.Consumer<ObjectNode> change) { eachRow(g.path("build"), "motors", change); }
}
