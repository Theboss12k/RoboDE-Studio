package dev.robode.studio.jobs;

import dev.robode.studio.store.ProjectStore;
import java.nio.file.Path;
import tools.jackson.databind.JsonNode;

/** A finished task: its result data and the out_dir holding any files it wrote. Close it to delete the out_dir. */
public final class TaskResult implements AutoCloseable {
    private final JsonNode data;
    private final Path outDir;
    private final boolean keep;

    TaskResult(JsonNode data, Path outDir, boolean keep) {
        this.data = data;
        this.outDir = outDir;
        this.keep = keep;
    }

    public JsonNode data() { return data; }

    public Path outDir() { return outDir; }

    public Path file(String name) { return outDir.resolve(name); }

    @Override
    public void close() {
        if (!keep) ProjectStore.deleteTree(outDir);
    }
}
