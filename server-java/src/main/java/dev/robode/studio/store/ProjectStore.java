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
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.time.temporal.ChronoUnit;
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
    public static final String BUDGET_MESSAGE ="At most " + MAX_SPEC_IMAGES + " images in total go to the spec agent (reference photos count too)";
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
                    if (images(pid).size() + answerImages(pid).size() >= MAX_SPEC_IMAGES) throw StudioException.value(BUDGET_MESSAGE);
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
    public int specImageCount(String pid) { return images(pid).size() + answerImages(pid).size(); }

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

    // -- AR scenes ----------------------------------------------------------------------------------------------
    /** One photo of a site with its calibration; stored under ar/<sid>/ and never part of a design version. */
    public record ArPhoto(Path path, String contentType) {}

    public static final int MAX_AR_SCENES = 10;
    public static final String AR_LIMIT_MESSAGE = "At most " + MAX_AR_SCENES + " AR scenes per project";
    private static final Pattern SCENE_ID = Pattern.compile("^s_[0-9a-f]{10}$");
    private static final Map<String, String> AR_MIME = Map.of("png", "image/png", "jpg", "image/jpeg", "webp", "image/webp");

    private Path arDir(String pid) { return dir(pid).resolve("ar"); }

    private Path sceneDir(String pid, String sid) {
        if (sid == null || !SCENE_ID.matcher(sid).matches()) throw StudioException.notFound("AR scene not found");
        return arDir(pid).resolve(sid);
    }

    /** The scene's scene.json as stored (with schemaVersion), or 404. */
    private ObjectNode sceneFile(String pid, String sid) {
        ObjectNode scene = Json.readObject(sceneDir(pid, sid).resolve("scene.json"));
        if (scene == null) throw StudioException.notFound("AR scene not found");
        return scene;
    }

    private static ObjectNode sceneView(ObjectNode scene) { return Json.withoutSchemaVersion(scene); }

    private List<Path> sceneFolders(String pid) {
        Path folder = arDir(pid);
        if (!Files.isDirectory(folder)) return List.of();
        try (Stream<Path> files = Files.list(folder)) {
            return files.filter(p -> Files.isRegularFile(p.resolve("scene.json"))).toList();
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    public int arSceneCount(String pid) { return locked(pid, () -> sceneFolders(pid).size()); }

    /** Stores a validated photo as photo.&lt;ext&gt; (ext: png, jpg or webp) and a new scene.json; returns the scene. */
    public ObjectNode createArScene(String pid, byte[] bytes, String ext, Integer width, Integer height) {
        return locked(pid, () -> {
            if (sceneFolders(pid).size() >= MAX_AR_SCENES) throw StudioException.value(AR_LIMIT_MESSAGE);
            String sid;
            Path folder;
            do {
                byte[] raw = new byte[5];
                RANDOM.nextBytes(raw);
                sid = "s_" + HexFormat.of().formatHex(raw);
                folder = sceneDir(pid, sid);
            } while (Files.exists(folder));
            try {
                Files.createDirectories(folder);
                Files.write(folder.resolve("photo." + ext), bytes);
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
            ObjectNode scene = obj();
            scene.put("id", sid);
            scene.put("image", "photo." + ext);
            if (width == null) scene.putNull("width"); else scene.put("width", width.intValue());
            if (height == null) scene.putNull("height"); else scene.put("height", height.intValue());
            scene.put("created", OffsetDateTime.now(ZoneOffset.UTC).truncatedTo(ChronoUnit.MILLIS).toString());
            scene.putNull("reference");
            scene.putNull("calibration");
            scene.putNull("estimate");
            ObjectNode placement = obj();
            placement.put("x", 0.0);
            placement.put("y", 0.0);
            placement.put("yaw_deg", 0.0);
            scene.set("placement", placement);
            Json.write(folder.resolve("scene.json"), scene);
            return sceneView(scene);
        });
    }

    /** The scenes of a project, newest first. */
    public ArrayNode listArScenes(String pid) {
        return locked(pid, () -> {
            List<ObjectNode> scenes = new ArrayList<>();
            for (Path folder : sceneFolders(pid)) {
                ObjectNode scene = Json.readObject(folder.resolve("scene.json"));
                if (scene != null) scenes.add(sceneView(scene));
            }
            scenes.sort(Comparator.comparing((ObjectNode s) -> { String c = Json.text(s, "created"); return c == null ? "" : c; })
                    .thenComparing(s -> { String id = Json.text(s, "id"); return id == null ? "" : id; }).reversed());
            ArrayNode out = arr();
            scenes.forEach(out::add);
            return out;
        });
    }

    public ObjectNode getArScene(String pid, String sid) {
        return locked(pid, () -> sceneView(sceneFile(pid, sid)));
    }

    /** The photo file and its content type (404 when the scene or its photo is missing). */
    public ArPhoto arPhoto(String pid, String sid) {
        return locked(pid, () -> {
            ObjectNode scene = sceneFile(pid, sid);
            String image = Json.text(scene, "image");
            if (image == null) throw StudioException.notFound("Photo not found");
            String ext = image.substring(image.lastIndexOf('.') + 1).toLowerCase();
            Path file = Ids.inside(sceneDir(pid, sid), image);
            if (!AR_MIME.containsKey(ext) || !Files.isRegularFile(file)) throw StudioException.notFound("Photo not found");
            return new ArPhoto(file, AR_MIME.get(ext));
        });
    }

    /** Read-modify-write of scene.json under the project lock; returns the updated scene. */
    public ObjectNode updateArScene(String pid, String sid, Consumer<ObjectNode> change) {
        return locked(pid, () -> {
            ObjectNode scene = sceneFile(pid, sid);
            change.accept(scene);
            Json.write(sceneDir(pid, sid).resolve("scene.json"), scene);
            return sceneView(scene);
        });
    }

    /** Removes the scene's folder (photo and scene.json). */
    public void deleteArScene(String pid, String sid) {
        locked(pid, () -> {
            sceneFile(pid, sid);
            deleteTree(sceneDir(pid, sid));
        });
    }

    // -- site scans (video AR) ----------------------------------------------------------------------------------
    /** A stored site-scan video and its content type. */
    public record ScanFile(Path path, String contentType) {}

    public static final int MAX_SCANS = 5;
    public static final String SCAN_LIMIT_MESSAGE = "At most " + MAX_SCANS + " site scans per project";
    private static final Pattern SCAN_ID = Pattern.compile("^sc_[0-9a-f]{10}$");
    private static final Map<String, String> VIDEO_MIME = Map.of("mp4", "video/mp4", "mov", "video/quicktime", "webm", "video/webm");

    /** The lower-case extension (mp4, mov or webm) of a video file name, or null for any other name. */
    public static String videoExtension(String fileName) {
        int dot = fileName.lastIndexOf('.');
        if (dot < 0) return null;
        String ext = fileName.substring(dot + 1).toLowerCase(java.util.Locale.ROOT);
        return VIDEO_MIME.containsKey(ext) ? ext : null;
    }

    private Path scansDir(String pid) { return dir(pid).resolve("scans"); }

    private Path scanDir(String pid, String sid) {
        if (sid == null || !SCAN_ID.matcher(sid).matches()) throw StudioException.notFound("Site scan not found");
        return scansDir(pid).resolve(sid);
    }

    /** The scan's scan.json as stored (with schemaVersion), or 404. */
    private ObjectNode scanFile(String pid, String sid) {
        ObjectNode scan = Json.readObject(scanDir(pid, sid).resolve("scan.json"));
        if (scan == null) throw StudioException.notFound("Site scan not found");
        return scan;
    }

    private List<Path> scanFolders(String pid) {
        Path folder = scansDir(pid);
        if (!Files.isDirectory(folder)) return List.of();
        try (Stream<Path> files = Files.list(folder)) {
            return files.filter(p -> Files.isRegularFile(p.resolve("scan.json"))).toList();
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    public int scanCount(String pid) { return locked(pid, () -> scanFolders(pid).size()); }

    /**
     * Moves the validated video into a new scans/&lt;sid&gt;/ folder and writes scan.json with status "uploaded".
     * `probe` is the scan_probe result (ScanVideo without file); `bytes` is the stored size. Returns the scan.
     */
    public ObjectNode createScan(String pid, Path video, String ext, JsonNode probe, long bytes) {
        return locked(pid, () -> {
            if (scanFolders(pid).size() >= MAX_SCANS) throw StudioException.value(SCAN_LIMIT_MESSAGE);
            String sid;
            Path folder;
            do {
                byte[] raw = new byte[5];
                RANDOM.nextBytes(raw);
                sid = "sc_" + HexFormat.of().formatHex(raw);
                folder = scanDir(pid, sid);
            } while (Files.exists(folder));
            try {
                Files.createDirectories(folder.resolve("frames"));
                String fileName = "video." + ext;
                Json.move(video, folder.resolve(fileName));
                ObjectNode videoInfo = obj().put("file", fileName).put("bytes", bytes);
                probe.properties().forEach(e -> {
                    if (!e.getKey().equals("file") && !e.getKey().equals("bytes")) videoInfo.set(e.getKey(), e.getValue());
                });
                ObjectNode scan = obj();
                scan.put("id", sid);
                scan.put("created", OffsetDateTime.now(ZoneOffset.UTC).truncatedTo(ChronoUnit.MILLIS).toString());
                scan.set("video", videoInfo);
                scan.put("status", "uploaded");
                scan.putNull("error");
                scan.putNull("camera");
                scan.set("frames", arr());
                scan.putNull("floor");
                scan.putNull("alignment");
                scan.putNull("scale");
                scan.putNull("mesh");
                ObjectNode placement = obj();
                placement.put("x", 0.0);
                placement.put("y", 0.0);
                placement.put("yaw_deg", 0.0);
                scan.set("placement", placement);
                scan.putNull("stats");
                Json.write(folder.resolve("scan.json"), scan);
                return Json.withoutSchemaVersion(scan);
            } catch (IOException e) {
                deleteTree(folder);
                throw new UncheckedIOException(e);
            } catch (RuntimeException e) {
                deleteTree(folder);
                throw e;
            }
        });
    }

    /** The scans of a project, newest first. */
    public ArrayNode listScans(String pid) {
        return locked(pid, () -> {
            List<ObjectNode> scans = new ArrayList<>();
            for (Path folder : scanFolders(pid)) {
                ObjectNode scan = Json.readObject(folder.resolve("scan.json"));
                if (scan != null) scans.add(Json.withoutSchemaVersion(scan));
            }
            scans.sort(Comparator.comparing((ObjectNode s) -> { String c = Json.text(s, "created"); return c == null ? "" : c; })
                    .thenComparing(s -> { String id = Json.text(s, "id"); return id == null ? "" : id; }).reversed());
            ArrayNode out = arr();
            scans.forEach(out::add);
            return out;
        });
    }

    public ObjectNode getScan(String pid, String sid) {
        return locked(pid, () -> Json.withoutSchemaVersion(scanFile(pid, sid)));
    }

    /** Read-modify-write of scan.json under the project lock; returns the updated scan. */
    public ObjectNode updateScan(String pid, String sid, Consumer<ObjectNode> change) {
        return locked(pid, () -> {
            ObjectNode scan = scanFile(pid, sid);
            change.accept(scan);
            Json.write(scanDir(pid, sid).resolve("scan.json"), scan);
            return Json.withoutSchemaVersion(scan);
        });
    }

    /** Removes the scan's folder (video, scan.json, frames and mesh). */
    public void deleteScan(String pid, String sid) {
        locked(pid, () -> {
            scanFile(pid, sid);
            deleteTree(scanDir(pid, sid));
        });
    }

    /** The video file and its content type (404 when the scan or its video is missing). */
    public ScanFile scanVideo(String pid, String sid) {
        return locked(pid, () -> {
            ObjectNode scan = scanFile(pid, sid);
            String name = Json.text(scan.get("video"), "file");
            String ext = name == null ? null : videoExtension(name);
            if (ext == null) throw StudioException.notFound("Video not found");
            Path file = Ids.inside(scanDir(pid, sid), name);
            if (!Files.isRegularFile(file)) throw StudioException.notFound("Video not found");
            return new ScanFile(file, VIDEO_MIME.get(ext));
        });
    }

    /** frames/NNNN.jpg for a non-negative index (404 when there is no such frame). */
    public Path scanFrame(String pid, String sid, int index) {
        return locked(pid, () -> {
            scanFile(pid, sid);
            Path file = scanDir(pid, sid).resolve("frames").resolve(String.format(java.util.Locale.ROOT, "%04d.jpg", index));
            if (index < 0 || !Files.isRegularFile(file)) throw StudioException.notFound("Frame not found");
            return file;
        });
    }

    /** scan.glb (404 when the scan has no mesh yet). */
    public Path scanMesh(String pid, String sid) {
        return locked(pid, () -> {
            scanFile(pid, sid);
            Path file = scanDir(pid, sid).resolve("scan.glb");
            if (!Files.isRegularFile(file)) throw StudioException.notFound("Mesh not found");
            return file;
        });
    }

    /** Moves the frames folder and the mesh of a finished reconstruction into the scan folder, replacing older ones. */
    public void adoptScanOutputs(String pid, String sid, Path frames, Path mesh) {
        locked(pid, () -> {
            scanFile(pid, sid);
            Path folder = scanDir(pid, sid);
            try {
                deleteTree(folder.resolve("frames"));
                Files.move(frames, folder.resolve("frames"));
                Json.move(mesh, folder.resolve("scan.glb"));
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
        });
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
        if (m.get("answer_comments") instanceof ObjectNode c && !c.isEmpty()) view.set("answer_comments", c);
        if (m.get("answer_notes") instanceof ArrayNode n && !n.isEmpty()) view.set("answer_notes", n);
        ArrayNode answerImages = answerImages(pid);
        if (!answerImages.isEmpty()) view.set("answer_images", answerImages);
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
