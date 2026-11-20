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
    /** Reference photos plus answer images that go to the spec agent (the Claude CLI's cap). */
    public static final int MAX_SPEC_IMAGES = 8;
    public static final String GENERAL = "_general";
    public static final String BUDGET_MESSAGE = "At most " + MAX_SPEC_IMAGES + " images in total go to the spec agent (reference photos count too)";
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

    /** Newest entries of `meta.activity` kept (it is a log of what the Ask bar did, not exposed in the project view). */
    public static final int MAX_ACTIVITY = 200;

    /** Appends {at, source, kind, ok, job_id?, summary} to `meta.activity` (the last MAX_ACTIVITY entries are kept). */
    public void appendActivity(String pid, String source, String kind, boolean ok, String jobId, String summary) {
        update(pid, meta -> {
            ArrayNode log = meta.get("activity") instanceof ArrayNode a ? a : arr();
            ObjectNode entry = obj();
            entry.put("at", Time.now());
            entry.put("source", source);
            entry.put("kind", kind);
            entry.put("ok", ok);
            if (jobId != null) entry.put("job_id", jobId);
            entry.put("summary", summary == null ? "" : summary);
            log.add(entry);
            while (log.size() > MAX_ACTIVITY) log.remove(0);
            meta.set("activity", log);
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

    public boolean hasReferenceImage(String pid, String name) { return Files.exists(ref(pid).resolve("images").resolve(name)); }

    public boolean hasAnswerImage(String pid, String name) {
        for (JsonNode e : answerImages(pid)) if (name.equals(Json.text(e, "name"))) return true;
        return false;
    }

    public int imageCount(String pid) { return images(pid).size(); }

    /** Stores a validated image upload. */
    public void addReferenceImage(String pid, String name, Path source) {
        locked(pid, () -> {
            try {
                Path folder = ref(pid).resolve("images");
                Files.createDirectories(folder);
                if (!Files.exists(folder.resolve(name))) {
                    if (images(pid).size() >= MAX_IMAGES) throw StudioException.value("At most " + MAX_IMAGES + " reference images");
                    if (specImageCount(pid) >= MAX_SPEC_IMAGES) throw StudioException.value(BUDGET_MESSAGE);
                }
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

    // -- answer images ------------------------------------------------------------------------------------------
    private Path answerDir(String pid) { return dir(pid).resolve("answers").resolve("images"); }

    private Path answerIndex(String pid) { return dir(pid).resolve("answers").resolve("images.json"); }

    /** The answer-image index: [{name, question, round, mime}], oldest first. */
    public ArrayNode answerImages(String pid) {
        JsonNode data = Json.read(answerIndex(pid));
        ArrayNode out = arr();
        if (data instanceof ArrayNode array)
            for (JsonNode e : array) if (Files.isRegularFile(answerDir(pid).resolve(Json.text(e, "name")))) out.add(e);
        return out;
    }

    /** Total images that would go to the spec agent: reference photos plus answer images. */
    public int specImageCount(String pid) { return images(pid).size() + answerImages(pid).size() + briefImages(pid).size(); }

    static String sniffMime(Path file) {
        try (var in = Files.newInputStream(file)) {
            byte[] h = in.readNBytes(12);
            if (h.length >= 4 && h[0] == (byte) 0x89 && h[1] == 'P' && h[2] == 'N' && h[3] == 'G') return "image/png";
            if (h.length >= 3 && h[0] == (byte) 0xFF && h[1] == (byte) 0xD8) return "image/jpeg";
            if (h.length >= 3 && h[0] == 'G' && h[1] == 'I' && h[2] == 'F') return "image/gif";
            if (h.length >= 12 && h[0] == 'R' && h[1] == 'I' && h[8] == 'W' && h[9] == 'E') return "image/webp";
            if (h.length >= 2 && h[0] == 'B' && h[1] == 'M') return "image/bmp";
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
        return "application/octet-stream";
    }

    /** Stores a validated image for a question (or "_general"); the budget is shared with the reference photos. */
    public void addAnswerImage(String pid, String name, String question, Path source) {
        locked(pid, () -> {
            try {
                ArrayNode index = answerImages(pid);
                boolean replacing = false;
                for (JsonNode e : index) if (name.equals(Json.text(e, "name"))) replacing = true;
                if (!replacing && specImageCount(pid) >= MAX_SPEC_IMAGES) throw StudioException.value(BUDGET_MESSAGE);
                Files.createDirectories(answerDir(pid));
                Path target = answerDir(pid).resolve(name);
                Json.move(source, target);
                ArrayNode next = arr();
                for (JsonNode e : index) if (!name.equals(Json.text(e, "name"))) next.add(e);
                next.add(obj().put("name", name).put("question", question).put("round", meta(pid).path("round").asInt(0))
                        .put("mime", sniffMime(target)));
                Json.write(answerIndex(pid), next);
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
        });
    }

    public void removeAnswerImage(String pid, String name) {
        locked(pid, () -> {
            ArrayNode index = answerImages(pid);
            boolean found = false;
            ArrayNode next = arr();
            for (JsonNode e : index) { if (name.equals(Json.text(e, "name"))) found = true; else next.add(e); }
            if (!found) throw StudioException.notFound("Image not found");
            try {
                Files.deleteIfExists(answerDir(pid).resolve(name));
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
            Json.write(answerIndex(pid), next);
        });
    }

    /** The index entry and file of one answer image (404 when unknown). */
    public JsonNode answerImage(String pid, String name) {
        for (JsonNode e : answerImages(pid)) if (name.equals(Json.text(e, "name"))) return e;
        throw StudioException.notFound("Image not found");
    }

    public Path answerImagePath(String pid, String name) { return Ids.inside(answerDir(pid), name); }

    /** The images as the design task takes them: [{path, question}]. */
    public ArrayNode answerImagesForTask(String pid) {
        ArrayNode out = arr();
        for (JsonNode e : answerImages(pid))
            out.add(obj().put("path", answerDir(pid).resolve(Json.text(e, "name")).toString()).put("question", Json.text(e, "question")));
        return out;
    }

    // -- brief ----------------------------------------------------------------------------------------------------
    public static final String BRIEF_FROZEN = "The design is approved; the brief is frozen";

    private void requireNotApproved(String pid) {
        JsonNode frozen = meta(pid).get("approved_version");
        if (frozen != null && !frozen.isNull() && frozen.asInt() != 0) throw StudioException.conflict(BRIEF_FROZEN);
    }

    /** Replaces the brief; pending questions are cleared and the round restarts. Existing versions stay. */
    public void setBrief(String pid, String brief) {
        locked(pid, () -> {
            requireNotApproved(pid);
            update(pid, m -> {
                m.put("brief", brief);
                m.set("questions", arr());
                m.put("round", 0);
                m.put("brief_updated", Time.now());
                if ("questions".equals(Json.text(m, "stage"))) m.put("stage", "brief");
            });
        });
    }

    private Path briefDir(String pid) { return dir(pid).resolve("brief").resolve("images"); }

    private Path briefIndex(String pid) { return dir(pid).resolve("brief").resolve("images.json"); }

    /** The brief-image index: [{name, mime}], oldest first. */
    public ArrayNode briefImages(String pid) {
        JsonNode data = Json.read(briefIndex(pid));
        ArrayNode out = arr();
        if (data instanceof ArrayNode array)
            for (JsonNode e : array) if (Files.isRegularFile(briefDir(pid).resolve(Json.text(e, "name")))) out.add(e);
        return out;
    }

    public boolean hasBriefImage(String pid, String name) {
        for (JsonNode e : briefImages(pid)) if (name.equals(Json.text(e, "name"))) return true;
        return false;
    }

    public void addBriefImage(String pid, String name, Path source) {
        locked(pid, () -> {
            try {
                requireNotApproved(pid);
                ArrayNode index = briefImages(pid);
                if (!hasBriefImage(pid, name) && specImageCount(pid) >= MAX_SPEC_IMAGES) throw StudioException.value(BUDGET_MESSAGE);
                Files.createDirectories(briefDir(pid));
                Path target = briefDir(pid).resolve(name);
                Json.move(source, target);
                ArrayNode next = arr();
                for (JsonNode e : index) if (!name.equals(Json.text(e, "name"))) next.add(e);
                next.add(obj().put("name", name).put("mime", sniffMime(target)));
                Json.write(briefIndex(pid), next);
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
        });
    }

    public void removeBriefImage(String pid, String name) {
        locked(pid, () -> {
            requireNotApproved(pid);
            boolean found = false;
            ArrayNode next = arr();
            for (JsonNode e : briefImages(pid)) { if (name.equals(Json.text(e, "name"))) found = true; else next.add(e); }
            if (!found) throw StudioException.notFound("Image not found");
            try {
                Files.deleteIfExists(briefDir(pid).resolve(name));
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
            Json.write(briefIndex(pid), next);
        });
    }

    public JsonNode briefImage(String pid, String name) {
        for (JsonNode e : briefImages(pid)) if (name.equals(Json.text(e, "name"))) return e;
        throw StudioException.notFound("Image not found");
    }

    public Path briefImagePath(String pid, String name) { return Ids.inside(briefDir(pid), name); }

    /** The images as the design task takes them: [{path, name}]. */
    public ArrayNode briefImagesForTask(String pid) {
        ArrayNode out = arr();
        for (JsonNode e : briefImages(pid))
            out.add(obj().put("path", briefDir(pid).resolve(Json.text(e, "name")).toString()).put("name", Json.text(e, "name")));
        return out;
    }

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

    /** The {rid: parsed} sidecar of a fleet version, or null when it has not been written yet. */
    public JsonNode fleetParsedSidecar(String pid, int n) {
        ObjectNode data = Json.readObject(versionPath(pid, n, ".fleet.parsed.json"));
        return data == null ? null : Json.withoutSchemaVersion(data);
    }

    public void saveFleetParsed(String pid, int n, JsonNode parsed) {
        Json.write(versionPath(pid, n, ".fleet.parsed.json"), parsed);
    }

    /** Sets meta.fleet from a task's fleet_advice (suggested, reasons, source); a new entry is also enabled. */
    public void applyFleetAdvice(String pid, JsonNode advice) {
        if (advice == null || !advice.isObject()) return;
        update(pid, m -> {
            ObjectNode fleet = m.get("fleet") instanceof ObjectNode f ? f : fleetDefaults();
            boolean fresh = !(m.get("fleet") instanceof ObjectNode);
            fleet.put("suggested", true);
            if (fresh) fleet.put("enabled", true);
            fleet.set("reasons", advice.get("reasons") instanceof ArrayNode r ? r : arr());
            fleet.put("source", Json.text(advice, "source") == null ? "spec" : Json.text(advice, "source"));
            m.set("fleet", fleet);
        });
    }

    /** A fresh meta.fleet entry. */
    public static ObjectNode fleetDefaults() {
        ObjectNode f = obj();
        f.put("suggested", false);
        f.put("enabled", false);
        f.set("reasons", arr());
        f.put("source", "user");
        f.putNull("limits");
        f.putNull("assignment");
        f.putNull("member");
        return f;
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

    /** Records the user's own design as the decision. build null = the approved studio build; 409 when none/not approved, 404 when unknown. */
    public void setSelfDecision(String pid, Integer build) {
        locked(pid, () -> {
            JsonNode approvedNode = meta(pid).path("studio").get("approved_build");
            Integer approved = approvedNode == null || approvedNode.isNull() ? null : approvedNode.asInt();
            int n;
            if (build == null) {
                if (approved == null) throw StudioException.conflict("Approve a build in Robot studio first");
                n = approved;
            } else {
                if (!builds(pid).contains(build)) throw StudioException.notFound("Build not found");
                ObjectNode stored = Json.readObject(buildDir(pid, build).resolve("build.json"));
                if (stored == null || !stored.path("approved").asBoolean(false)) throw StudioException.conflict("Approve a build in Robot studio first");
                n = build;
            }
            update(pid, meta -> {
                meta.set("decision", obj().put("action", "self").put("build", n));
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
        if (m.get("fleet") instanceof ObjectNode f) view.set("fleet", f);
        if (m.get("savings_inputs") instanceof ObjectNode si) view.set("savings_inputs", si);
        if (m.get("answer_comments") instanceof ObjectNode c && !c.isEmpty()) view.set("answer_comments", c);
        if (m.get("answer_notes") instanceof ArrayNode n && !n.isEmpty()) view.set("answer_notes", n);
        ArrayNode answerImages = answerImages(pid);
        if (!answerImages.isEmpty()) view.set("answer_images", answerImages);
        if (m.hasNonNull("brief_updated")) view.set("brief_updated", m.get("brief_updated"));
        ArrayNode briefImages = briefImages(pid);
        if (!briefImages.isEmpty()) view.set("brief_images", briefImages);
        List<Integer> builds = builds(pid);
        if (!builds.isEmpty()) {
            ObjectNode studio = obj();
            ArrayNode numbers = arr();
            builds.forEach(numbers::add);
            studio.set("builds", numbers);
            JsonNode approvedBuild = m.path("studio").get("approved_build");
            if (approvedBuild == null || approvedBuild.isNull()) studio.putNull("approved_build"); else studio.set("approved_build", approvedBuild);
            view.set("studio", studio);
        }
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

    // -- robot studio builds (runtime/projects/<id>/studio/b<n>/, written by StudioStore) --------------------------
    private static final Pattern BUILD_DIR = Pattern.compile("^b(\\d+)$");

    public Path studioDir(String pid) { return dir(pid).resolve("studio"); }

    public Path buildDir(String pid, int n) { return studioDir(pid).resolve("b" + n); }

    /** Build numbers (ascending) that have a build.json. */
    public List<Integer> builds(String pid) {
        Path studio = studioDir(pid);
        if (!Files.isDirectory(studio)) return List.of();
        try (Stream<Path> list = Files.list(studio)) {
            return list.map(p -> BUILD_DIR.matcher(p.getFileName().toString())).filter(java.util.regex.Matcher::matches)
                    .filter(mt -> Files.isRegularFile(studio.resolve(mt.group(0)).resolve("build.json")))
                    .map(mt -> Integer.parseInt(mt.group(1))).sorted().toList();
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
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
