package dev.robode.studio.store;

import static dev.robode.studio.common.Json.arr;
import static dev.robode.studio.common.Json.obj;

import dev.robode.studio.common.Ids;
import dev.robode.studio.common.Json;
import dev.robode.studio.common.StudioException;
import dev.robode.studio.common.Time;
import dev.robode.studio.config.StudioProperties;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.locks.ReentrantLock;
import java.util.function.Consumer;
import java.util.function.Supplier;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Stream;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * The only writer of runtime/projects/**. Same layout and file names as the former Python store:
 * <pre>
 * runtime/projects/&lt;id&gt;/project.json, versions/v&lt;n&gt;.json (+ v&lt;n&gt;.parsed.json, v&lt;n&gt;.glb), reference/{text.md, images/, urdf/},
 * candidates.json, documents/&lt;cid&gt;.txt (+ .json), analysis/&lt;cid&gt;.json (+ &lt;cid&gt;.parsed.json)
 * </pre>
 * Domain JSON stays an opaque tree; only the envelope fields (id, version, parent_version, created, approved,
 * decision, schemaVersion) are known here. Each project has its own lock.
 */
@Component
public class ProjectStore {
    public static final int MAX_TEXT = 60_000;
    public static final int MAX_IMAGES = 6;
    private static final Pattern VERSION_FILE = Pattern.compile("^v(\\d+)\\.json$");
    private static final SecureRandom RANDOM = new SecureRandom();

    private final Path root;
    private final Map<String, ReentrantLock> locks = new ConcurrentHashMap<>();

    @org.springframework.beans.factory.annotation.Autowired
    public ProjectStore(StudioProperties properties) { this(properties.runtime()); }

    public ProjectStore(Path runtime) { this.root = runtime.resolve("projects"); }

    public Path root() { return root; }

    // -- locking ------------------------------------------------------------------------------------------------
    public <T> T locked(String pid, Supplier<T> work) {
        ReentrantLock lock = locks.computeIfAbsent(pid, k -> new ReentrantLock());
        lock.lock();
        try {
            return work.get();
        } finally {
            lock.unlock();
        }
    }

    public void locked(String pid, Runnable work) {
        locked(pid, () -> { work.run(); return null; });
    }

    // -- projects -----------------------------------------------------------------------------------------------
    public Path dir(String pid) { return root.resolve(Ids.projectId(pid)); }

    /** Validates the id and that the project exists (404 otherwise); returns its id. */
    public String require(String pid) {
        if (!Files.exists(dir(pid).resolve("project.json"))) throw StudioException.notFound("Project not found");
        return pid;
    }

    public ObjectNode create(String brief) {
        brief = brief == null ? "" : brief.strip();
        if (brief.isEmpty()) throw StudioException.value("Describe the job first");
        byte[] bytes = new byte[5];
        RANDOM.nextBytes(bytes);
        String pid = "p_" + HexFormat.of().formatHex(bytes);
        String firstLine = brief.split("\n", 2)[0];
        ObjectNode meta = obj();
        meta.put("id", pid);
        meta.put("title", firstLine.substring(0, Math.min(80, firstLine.length())));
        meta.put("brief", brief.substring(0, Math.min(8000, brief.length())));
        meta.put("stage", "brief");
        meta.set("questions", arr());
        meta.set("answers", obj());
        meta.put("round", 0);
        meta.putNull("spec");
        meta.putNull("approved_version");
        meta.putNull("selected_candidate");
        meta.putNull("decision");
        meta.put("created", Time.now());
        meta.put("updated", Time.now());
        Json.write(root.resolve(pid).resolve("project.json"), meta);
        return view(pid);
    }

    public ArrayNode list() {
        List<ObjectNode> out = new ArrayList<>();
        if (Files.isDirectory(root)) {
            try (Stream<Path> dirs = Files.list(root)) {
                for (Path dir : dirs.filter(p -> p.getFileName().toString().startsWith("p_")).sorted().toList()) {
                    ObjectNode meta = Json.readObject(dir.resolve("project.json"));
                    if (meta == null) continue;
                    ObjectNode row = obj();
                    for (String k : List.of("id", "title", "stage", "created", "updated"))
                        row.set(k, meta.has(k) ? meta.get(k) : Json.MAPPER.nullNode());
                    out.add(row);
                }
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
        }
        out.sort(Comparator.comparing((ObjectNode m) -> { String u = Json.text(m, "updated"); return u == null ? "" : u; }).reversed());
        ArrayNode array = arr();
        out.forEach(array::add);
        return array;
    }

    public void delete(String pid) {
        require(pid);
        locked(pid, () -> deleteTree(dir(pid)));
        locks.remove(pid);
    }

    // -- metadata -----------------------------------------------------------------------------------------------
    public ObjectNode meta(String pid) {
        ObjectNode meta = Json.readObject(dir(pid).resolve("project.json"));
        if (meta == null) throw StudioException.notFound("Project not found");
        return meta;
    }

    /** Applies the change to project.json under the project lock and stamps `updated`. */
    public ObjectNode update(String pid, Consumer<ObjectNode> change) {
        return locked(pid, () -> {
            ObjectNode meta = meta(pid);
            change.accept(meta);
            meta.put("updated", Time.now());
            Json.write(dir(pid).resolve("project.json"), meta);
            return meta;
        });
    }

    // -- reference ----------------------------------------------------------------------------------------------
    private Path ref(String pid) { return dir(pid).resolve("reference"); }

    private Optional<Path> urdfFile(String pid) {
        Path legacy = ref(pid).resolve("robot.urdf");
        if (Files.isRegularFile(legacy)) return Optional.of(legacy);
        Path folder = ref(pid).resolve("urdf");
        if (Files.isDirectory(folder)) {
            try (Stream<Path> files = Files.list(folder)) {
                return files.filter(Files::isRegularFile).sorted().findFirst();
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
        }
        return Optional.empty();
    }

    private List<Path> images(String pid) {
        Path folder = ref(pid).resolve("images");
        if (!Files.isDirectory(folder)) return List.of();
        try (Stream<Path> files = Files.list(folder)) {
            return files.filter(Files::isRegularFile).sorted().toList();
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    public JsonNode referenceView(String pid) {
        Path textFile = ref(pid).resolve("text.md");
        String text = Files.exists(textFile) ? readString(textFile) : "";
        ArrayNode files = arr();
        urdfFile(pid).ifPresent(file -> {
            ObjectNode entry = obj();
            JsonNode info = Json.read(ref(pid).resolve("urdf.json"));
            String name = info == null ? null : Json.text(info, "filename");
            entry.put("name", name != null ? name : "robot.urdf");
            entry.put("kind", "urdf");
            files.add(entry);
        });
        for (Path image : images(pid)) {
            ObjectNode entry = obj();
            entry.put("name", image.getFileName().toString());
            entry.put("kind", "image");
            files.add(entry);
        }
        if (text.isEmpty() && files.isEmpty()) return Json.MAPPER.nullNode();
        ObjectNode view = obj();
        view.put("text", text);
        view.set("files", files);
        return view;
    }

    /** The reference as the Python tasks take it: {text, files: [{path, kind}]}, or null when there is none. */
    public JsonNode referenceForTask(String pid) {
        JsonNode view = referenceView(pid);
        if (view.isNull()) return view;
        ObjectNode out = obj();
        out.put("text", Json.text(view, "text"));
        ArrayNode files = arr();
        urdfFile(pid).ifPresent(p -> files.add(obj().put("path", p.toString()).put("kind", "urdf")));
        for (Path image : images(pid)) files.add(obj().put("path", image.toString()).put("kind", "image"));
        out.set("files", files);
        return out;
    }

    public void setReferenceText(String pid, String text) {
        text = text == null ? "" : text.strip();
        if (text.length() > MAX_TEXT) throw StudioException.value("Reference text must be at most " + MAX_TEXT + " characters");
        String finalText = text;
        locked(pid, () -> {
            try {
                Files.createDirectories(ref(pid));
                Path file = ref(pid).resolve("text.md");
                if (finalText.isEmpty()) Files.deleteIfExists(file);
                else Json.writeText(file, finalText);
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
        });
    }

    public int imageCount(String pid) { return images(pid).size(); }

    /** Stores a validated image upload. */
    public void addReferenceImage(String pid, String name, Path source) {
        locked(pid, () -> {
            try {
                Path folder = ref(pid).resolve("images");
                Files.createDirectories(folder);
                if (images(pid).size() >= MAX_IMAGES && !Files.exists(folder.resolve(name)))
                    throw StudioException.value("At most " + MAX_IMAGES + " reference images");
                Json.move(source, folder.resolve(name));
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
        });
    }

    /** Stores a validated URDF (or zip) upload, replacing the previous one; `summary` is the task's description. */
    public void setReferenceUrdf(String pid, String name, Path source, JsonNode summary) {
        locked(pid, () -> {
            try {
                Files.deleteIfExists(ref(pid).resolve("robot.urdf"));
                deleteTree(ref(pid).resolve("urdf"));
                Files.createDirectories(ref(pid).resolve("urdf"));
                Json.move(source, ref(pid).resolve("urdf").resolve(name));
                ObjectNode info = obj();
                info.put("filename", name);
                info.set("summary", summary == null ? Json.MAPPER.nullNode() : summary);
                Json.write(ref(pid).resolve("urdf.json"), info);
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
        });
    }

    public void clearReference(String pid) { locked(pid, () -> deleteTree(ref(pid))); }

    // -- versions -----------------------------------------------------------------------------------------------
    private Path versionsDir(String pid) { return dir(pid).resolve("versions"); }

    public List<Integer> versions(String pid) {
        Path folder = versionsDir(pid);
        if (!Files.isDirectory(folder)) return List.of();
        try (Stream<Path> files = Files.list(folder)) {
            return files.map(p -> VERSION_FILE.matcher(p.getFileName().toString())).filter(Matcher::matches)
                    .map(m -> Integer.parseInt(m.group(1))).sorted().toList();
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    public ObjectNode version(String pid, int n) {
        ObjectNode data = Json.readObject(versionsDir(pid).resolve("v" + n + ".json"));
        if (data == null) throw StudioException.notFound("Version " + n + " not found");
        return data;
    }

    public ObjectNode latest(String pid) {
        List<Integer> all = versions(pid);
        return all.isEmpty() ? null : version(pid, all.get(all.size() - 1));
    }

    public ObjectNode approved(String pid) {
        JsonNode n = meta(pid).get("approved_version");
        return n == null || n.isNull() || n.asInt() == 0 ? null : version(pid, n.asInt());
    }

    public Path versionPath(String pid, int n, String suffix) { return versionsDir(pid).resolve("v" + n + suffix); }

    public JsonNode parsedSidecar(String pid, int n) {
        ObjectNode data = Json.readObject(versionPath(pid, n, ".parsed.json"));
        return data == null ? null : Json.withoutSchemaVersion(data);
    }

    public void saveParsed(String pid, int n, JsonNode parsed) {
        Json.write(versionPath(pid, n, ".parsed.json"), parsed);
    }

    /**
     * Commits a DesignVersion built by a task: assigns the next number, parent_version and created, and sets
     * approved=false. `guard` runs under the project lock first (re-checks approval). Versions are never
     * overwritten. Returns the new number.
     */
    public int addVersion(String pid, JsonNode built, Integer parent, JsonNode parsed, Consumer<ObjectNode> guard) {
        return locked(pid, () -> {
            ObjectNode meta = meta(pid);
            if (guard != null) guard.accept(meta);
            List<Integer> all = versions(pid);
            int n = (all.isEmpty() ? 0 : all.get(all.size() - 1)) + 1;
            Path path = versionPath(pid, n, ".json");
            if (Files.exists(path)) throw StudioException.value("Version already exists");
            ObjectNode out = obj();
            out.put("schemaVersion", Json.SCHEMA_VERSION);
            out.put("version", n);
            if (parent == null) out.putNull("parent_version"); else out.put("parent_version", parent);
            out.put("created", Time.now());
            ((ObjectNode) built).properties().forEach(e -> {
                if (!List.of("schemaVersion", "version", "parent_version", "created", "approved").contains(e.getKey()))
                    out.set(e.getKey(), e.getValue());
            });
            out.put("approved", false);
            Json.write(path, out);
            if (parsed != null && !parsed.isNull()) saveParsed(pid, n, parsed);
            update(pid, m -> m.put("stage", "designed"));
            return n;
        });
    }

    /** Marks version n approved and every other version not approved (the only field that changes after creation). */
    public void approve(String pid, int n) {
        locked(pid, () -> {
            version(pid, n);
            for (int m : versions(pid)) {
                ObjectNode data = version(pid, m);
                boolean now = m == n;
                if (!data.has("approved") || data.get("approved").asBoolean() != now) {
                    data.put("approved", now);
                    Json.write(versionPath(pid, m, ".json"), data);
                }
            }
            update(pid, meta -> { meta.put("approved_version", n); meta.put("stage", "approved"); });
        });
    }

    // -- candidates, documents, analyses ------------------------------------------------------------------------
    public ArrayNode candidates(String pid) {
        JsonNode data = Json.read(dir(pid).resolve("candidates.json"));
        return data instanceof ArrayNode array ? array : arr();
    }

    public void saveCandidates(String pid, JsonNode candidates) {
        locked(pid, () -> {
            Json.write(dir(pid).resolve("candidates.json"), candidates);
            update(pid, meta -> meta.put("stage", "candidates"));
        });
    }

    public ObjectNode candidate(String pid, String cid) {
        Ids.safeId(cid);
        for (JsonNode c : candidates(pid)) if (cid.equals(Json.text(c, "id"))) return (ObjectNode) c;
        throw StudioException.notFound("Candidate not found");
    }

    public Path documentText(String pid, String cid) { return dir(pid).resolve("documents").resolve(Ids.safeId(cid) + ".txt"); }

    public Path documentUploadDir(String pid, String cid) { return dir(pid).resolve("documents").resolve(Ids.safeId(cid) + ".upload"); }

    public String documentSource(String pid, String cid) {
        JsonNode info = Json.read(documentInfo(pid, cid));
        String source = info == null ? null : Json.text(info, "source");
        return source == null ? "" : source;
    }

    private Path documentInfo(String pid, String cid) { return dir(pid).resolve("documents").resolve(Ids.safeId(cid) + ".json"); }

    /** Saves a document text (downloaded by a task) and forgets any earlier upload for the candidate. */
    public void saveDocument(String pid, String cid, String text, String source) {
        locked(pid, () -> {
            deleteTree(documentUploadDir(pid, cid));
            Json.writeText(documentText(pid, cid), text);
            Json.write(documentInfo(pid, cid), obj().put("source", source));
        });
    }

    /** Saves an uploaded datasheet as received; the analyse task extracts its text. */
    public void saveUploadedDocument(String pid, String cid, String name, Path source) {
        locked(pid, () -> {
            try {
                deleteTree(documentUploadDir(pid, cid));
                Files.createDirectories(documentUploadDir(pid, cid));
                Json.move(source, documentUploadDir(pid, cid).resolve(name));
                Files.deleteIfExists(documentText(pid, cid));
                Json.write(documentInfo(pid, cid), obj().put("source", "uploaded document " + name).put("upload", name));
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
        });
    }

    /** The stored document for a candidate (upload first, else saved text), or empty. */
    public Optional<Path> documentPath(String pid, String cid) {
        JsonNode info = Json.read(documentInfo(pid, cid));
        String upload = info == null ? null : Json.text(info, "upload");
        if (upload != null && Files.isRegularFile(documentUploadDir(pid, cid).resolve(upload)))
            return Optional.of(documentUploadDir(pid, cid).resolve(upload));
        Path text = documentText(pid, cid);
        return Files.isRegularFile(text) ? Optional.of(text) : Optional.empty();
    }

    private Path analysisPath(String pid, String cid, String suffix) {
        return dir(pid).resolve("analysis").resolve(Ids.safeId(cid) + suffix);
    }

    public void saveAnalysis(String pid, String cid, JsonNode analysis, JsonNode parsed) {
        locked(pid, () -> {
            Json.write(analysisPath(pid, cid, ".json"), analysis);
            if (parsed != null && !parsed.isNull()) Json.write(analysisPath(pid, cid, ".parsed.json"), parsed);
            update(pid, meta -> { meta.put("selected_candidate", cid); meta.put("stage", "analysed"); });
        });
    }

    public ObjectNode analysis(String pid, String cid) {
        ObjectNode data = Json.readObject(analysisPath(pid, cid, ".json"));
        if (data == null) throw StudioException.notFound("No analysis for this candidate yet");
        return data;
    }

    public JsonNode analysisParsed(String pid, String cid) {
        ObjectNode data = Json.readObject(analysisPath(pid, cid, ".parsed.json"));
        return data == null ? null : Json.withoutSchemaVersion(data);
    }

    public void saveAnalysisParsed(String pid, String cid, JsonNode parsed) {
        Json.write(analysisPath(pid, cid, ".parsed.json"), parsed);
    }

    public List<String> analyses(String pid) {
        Path folder = dir(pid).resolve("analysis");
        if (!Files.isDirectory(folder)) return List.of();
        try (Stream<Path> files = Files.list(folder)) {
            return files.map(p -> p.getFileName().toString())
                    .filter(n -> n.endsWith(".json") && !n.endsWith(".parsed.json"))
                    .map(n -> n.substring(0, n.length() - 5)).sorted().toList();
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    public void setDecision(String pid, String candidateId, String action) {
        locked(pid, () -> {
            if (!analyses(pid).contains(candidateId)) throw StudioException.value("Analyse this candidate before deciding");
            update(pid, meta -> {
                meta.set("decision", obj().put("candidate_id", candidateId).put("action", action));
                meta.put("stage", "decided");
            });
        });
    }

    // -- the object the API returns -----------------------------------------------------------------------------
    public ObjectNode view(String pid) {
        ObjectNode m = meta(pid);
        ObjectNode view = obj();
        view.put("id", pid);
        for (String k : List.of("title", "brief")) view.set(k, orNull(m, k));
        view.set("reference", referenceView(pid));
        view.set("stage", orNull(m, "stage"));
        view.set("questions", m.has("questions") ? m.get("questions") : arr());
        view.set("answers", m.has("answers") ? m.get("answers") : obj());
        ArrayNode versions = arr();
        versions(pid).forEach(versions::add);
        view.set("versions", versions);
        view.set("approved_version", orNull(m, "approved_version"));
        view.put("candidates_count", candidates(pid).size());
        view.set("selected_candidate", orNull(m, "selected_candidate"));
        ArrayNode analyses = arr();
        analyses(pid).forEach(analyses::add);
        view.set("analyses", analyses);
        view.set("decision", orNull(m, "decision"));
        view.set("created", orNull(m, "created"));
        view.set("updated", orNull(m, "updated"));
        return view;
    }

    private static JsonNode orNull(ObjectNode node, String key) {
        return node.has(key) ? node.get(key) : Json.MAPPER.nullNode();
    }

    // -- helpers ------------------------------------------------------------------------------------------------
    static String readString(Path path) {
        try {
            return Files.readString(path, StandardCharsets.UTF_8);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    public static void deleteTree(Path path) {
        if (!Files.exists(path)) return;
        try (Stream<Path> walk = Files.walk(path)) {
            for (Path p : walk.sorted(Comparator.reverseOrder()).toList()) Files.deleteIfExists(p);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }
}
