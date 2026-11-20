package dev.robode.studio.store;

import dev.robode.studio.common.Ids;
import dev.robode.studio.common.Json;
import dev.robode.studio.common.StudioException;
import dev.robode.studio.common.Time;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.stream.Stream;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/** The robot studio's builds under runtime/projects/&lt;id&gt;/studio/b&lt;n&gt;/. Java is the only writer. */
@Component
public class StudioStore {
    public static final List<String> VIEWS = List.of("iso", "front", "side", "top");
    private final ProjectStore store;

    public StudioStore(ProjectStore store) { this.store = store; }

    public List<Integer> builds(String pid) { return store.builds(pid); }

    public Path buildDir(String pid, int n) { return store.buildDir(pid, n); }

    /** The stored StudioBuild (without the schema marker); 404 when unknown. */
    public ObjectNode build(String pid, int n) {
        store.require(pid);
        ObjectNode build = n < 1 ? null : Json.readObject(buildDir(pid, n).resolve("build.json"));
        if (build == null) throw StudioException.notFound("Build not found");
        return Json.withoutSchemaVersion(build);
    }

    public JsonNode parsed(String pid, int n) { return Json.read(buildDir(pid, n).resolve("parsed.json")); }

    public void saveParsed(String pid, int n, JsonNode parsed) { Json.write(buildDir(pid, n).resolve("parsed.json"), parsed); }

    /** The next build number; call it inside the project lock when it is used to commit. */
    public int nextBuild(String pid) {
        List<Integer> all = builds(pid);
        return (all.isEmpty() ? 0 : all.get(all.size() - 1)) + 1;
    }

    /**
     * Commits a finished studio_build: moves every file of the task's out_dir into b&lt;n&gt;/, writes build.json and
     * parsed.json. Java sets the number, the design version, the parent and the creation time; a build is born
     * unapproved. Returns n.
     */
    public int commit(String pid, int n, int designVersion, Integer parent, JsonNode built, JsonNode parsed, Path outDir) {
        return store.locked(pid, () -> {
            Path dir = buildDir(pid, n);
            if (Files.exists(dir)) throw StudioException.conflict("Build " + n + " already exists");
            ObjectNode out = ((ObjectNode) built).deepCopy();
            out.put("build", n);
            out.put("design_version", designVersion);
            if (parent == null) out.putNull("parent_build"); else out.put("parent_build", parent);
            if (!out.hasNonNull("created")) out.put("created", Time.now());
            out.put("approved", false);
            out.putNull("approved_at");
            try {
                Files.createDirectories(dir);
                moveTree(outDir, dir);
                if (parsed != null && !parsed.isNull()) Json.write(dir.resolve("parsed.json"), parsed);
                Json.write(dir.resolve("build.json"), out); // last: a build exists only once build.json does
            } catch (IOException e) {
                ProjectStore.deleteTree(dir);
                throw new UncheckedIOException(e);
            } catch (RuntimeException e) {
                ProjectStore.deleteTree(dir);
                throw e;
            }
            return n;
        });
    }

    private static void moveTree(Path from, Path to) throws IOException {
        if (!Files.isDirectory(from)) return;
        List<Path> files;
        try (Stream<Path> walk = Files.walk(from)) { files = walk.filter(Files::isRegularFile).toList(); }
        for (Path file : files) {
            Path target = to.resolve(from.relativize(file).toString()).normalize();
            if (!target.startsWith(to.normalize())) continue;
            Files.createDirectories(target.getParent());
            Json.move(file, target);
        }
    }

    /** A file inside a build's folder: a safe relative path (no "..", no absolute or backslash form); 404 when absent. */
    public Path file(String pid, int n, String relative) {
        build(pid, n);
        String rel = relative == null ? "" : relative;
        while (rel.startsWith("/")) rel = rel.substring(1);
        if (rel.isEmpty() || rel.indexOf('\\') >= 0 || rel.indexOf('\0') >= 0)
            throw StudioException.value("Invalid path");
        for (String part : rel.split("/", -1))
            if (part.isEmpty() || part.equals(".") || part.equals("..")) throw StudioException.value("Invalid path");
        Path path = Ids.inside(buildDir(pid, n), rel);
        if (!Files.isRegularFile(path)) throw StudioException.notFound("File not found");
        return path;
    }

    /** The snapshot views that exist, in canonical order. */
    public ArrayNode views(String pid, int n) {
        ArrayNode out = Json.arr();
        for (String view : VIEWS) if (Files.isRegularFile(buildDir(pid, n).resolve("snapshots").resolve(view + ".png"))) out.add(view);
        return out;
    }

    public void saveSnapshot(String pid, int n, String view, byte[] png) {
        Path target = buildDir(pid, n).resolve("snapshots").resolve(view + ".png");
        try {
            Files.createDirectories(target.getParent());
            Path temp = Files.createTempFile(target.getParent(), ".tmp-", "");
            try {
                Files.write(temp, png);
                Json.move(temp, target);
            } finally {
                Files.deleteIfExists(temp);
            }
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    /**
     * Commits a finished studio_pdf under the project lock: copies the named files from the task's out_dir, merges the
     * reported BuildFile entries, marks the build approved and records it in meta.studio.approved_build.
     */
    public void approve(String pid, int n, Path outDir, List<String> names, JsonNode files) {
        store.locked(pid, () -> {
            ObjectNode build = ((ObjectNode) Json.readObject(buildDir(pid, n).resolve("build.json")));
            if (build == null) throw StudioException.notFound("Build not found");
            if (build.path("approved").asBoolean(false)) throw StudioException.conflict("Build " + n + " is already approved");
            try {
                for (String name : names) {
                    Path made = outDir.resolve(name);
                    if (Files.isRegularFile(made)) Json.move(made, buildDir(pid, n).resolve(name));
                }
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
            if (files instanceof ArrayNode reported) {
                ArrayNode merged = build.get("files") instanceof ArrayNode old ? old : Json.arr();
                ArrayNode next = Json.arr();
                for (JsonNode f : merged) if (!hasPath(reported, Json.text(f, "path"))) next.add(f);
                reported.forEach(next::add);
                build.set("files", next);
            }
            String now = Time.now();
            build.put("approved", true);
            build.put("approved_at", now);
            Json.write(buildDir(pid, n).resolve("build.json"), build);
            store.update(pid, m -> {
                ObjectNode studio = m.get("studio") instanceof ObjectNode s ? s : Json.obj();
                studio.put("approved_build", n);
                m.set("studio", studio);
            });
        });
    }

    private static boolean hasPath(ArrayNode files, String path) {
        for (JsonNode f : files) if (path != null && path.equals(Json.text(f, "path"))) return true;
        return false;
    }
}
