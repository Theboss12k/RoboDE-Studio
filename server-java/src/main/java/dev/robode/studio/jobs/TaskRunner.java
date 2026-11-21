package dev.robode.studio.jobs;

import dev.robode.studio.common.Ids;
import dev.robode.studio.common.Json;
import dev.robode.studio.common.StudioException;
import dev.robode.studio.config.StudioProperties;
import dev.robode.studio.store.ProjectStore;
import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.Duration;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Semaphore;
import java.util.concurrent.TimeUnit;
import java.util.stream.Stream;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * Runs one Python task per call as {@code <python> -m studio.tasks <task>} following docs/PROTOCOL.md: the envelope
 * goes to stdin (then stdin is closed), stdout carries JSON lines, stderr is free text for runtime/logs/jobs/&lt;id&gt;.log.
 * At most {@code maxProcesses} run at once. A process that is silent for the idle limit, or runs past the ceiling, is
 * killed together with all of its descendants (the Claude CLI is a grandchild). API keys travel only in the envelope.
 */
@Component
public class TaskRunner {
    private static final Logger LOG = LoggerFactory.getLogger(TaskRunner.class);

    /** Receives the stream while the task runs. Usage lines arrive as they are printed, so they survive a kill. */
    public interface Listener {
        Listener NONE = new Listener() {};

        default void progress(String text) {}

        /** A {"type":"usage","role","profile","entry"} message. */
        default void usage(JsonNode message) {}
    }

    private final StudioProperties props;
    private final Semaphore slots;
    private final Set<Process> live = ConcurrentHashMap.newKeySet();
    private final Thread shutdownHook = new Thread(this::killAll, "task-runner-shutdown");

    public TaskRunner(StudioProperties props) {
        this.props = props;
        this.slots = new Semaphore(props.maxProcesses(), true);
        try {
            Runtime.getRuntime().addShutdownHook(shutdownHook);
        } catch (IllegalStateException shuttingDown) {
            // already shutting down
        }
    }

    public Path jobsDir() { return props.runtime().resolve("tmp").resolve("jobs"); }

    public Path logFile(String jobId) { return props.runtime().resolve("logs").resolve("jobs").resolve(jobId + ".log"); }

    /** Removes out_dirs left behind by a previous server run (nothing can be running at startup). */
    @PostConstruct
    public void cleanStale() {
        Path dir = jobsDir();
        if (!Files.isDirectory(dir)) return;
        try (Stream<Path> children = Files.list(dir)) {
            children.forEach(ProjectStore::deleteTree);
        } catch (IOException | RuntimeException e) {
            LOG.warn("Could not clean stale job folders: {}", e.toString());
        }
    }

    @PreDestroy
    public void shutdown() {
        killAll();
        try {
            Runtime.getRuntime().removeShutdownHook(shutdownHook);
        } catch (IllegalStateException ignored) {
            // JVM is already going down
        }
    }

    /** Task failure with the protocol's kind mapped to an HTTP status: value 422, not_found 404, llm 502, other 500. */
    public static StudioException failure(String kind, String code, String message) {
        return switch (kind == null ? "internal" : kind) {
            case "value" -> new StudioException(422, message, code);
            case "not_found" -> new StudioException(404, message, code);
            case "llm" -> new StudioException(502, message, code);
            default -> new StudioException(500, message, code);
        };
    }

    public TaskResult run(String jobId, String task, JsonNode input, JsonNode llm, String projectId, String contextTask,
                          Listener listener) {
        Path outDir = jobsDir().resolve(jobId);
        try {
            slots.acquire();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw StudioException.internal("Interrupted while waiting for a free task slot", null);
        }
        try {
            Files.createDirectories(outDir);
            return execute(jobId, task, input, llm, projectId, contextTask, listener == null ? Listener.NONE : listener, outDir);
        } catch (IOException e) {
            ProjectStore.deleteTree(outDir);
            throw StudioException.internal("Could not run the task: " + e.getMessage(), null);
        } catch (RuntimeException e) {
            if (!props.keepJobDirs()) ProjectStore.deleteTree(outDir);
            throw e;
        } finally {
            slots.release();
        }
    }

    private TaskResult execute(String jobId, String task, JsonNode input, JsonNode llm, String projectId, String contextTask,
                               Listener listener, Path outDir) throws IOException {
        ObjectNode envelope = Json.obj();
        envelope.put("protocol", 1);
        envelope.put("task", task);
        envelope.set("input", input == null ? Json.obj() : input);
        envelope.set("llm", llm == null ? Json.obj().put("profile", (String) null) : llm);
        ObjectNode context = Json.obj();
        if (projectId == null) context.putNull("project_id"); else context.put("project_id", projectId);
        context.put("task", contextTask == null ? task : contextTask);
        envelope.set("context", context);
        envelope.put("out_dir", outDir.toString());
        byte[] request = Json.MAPPER.writeValueAsBytes(envelope);
        List<String> secrets = secrets(llm);

        ProcessBuilder builder = new ProcessBuilder(props.python().toString(), "-m", "studio.tasks", task);
        builder.directory(props.repoRoot().toFile());
        Map<String, String> env = builder.environment();
        env.put("PYTHONPATH", props.server().toString());
        env.put("PYTHONUNBUFFERED", "1");
        // keys travel only on stdin: the engine does not inherit this process's key or token variables (Cloud Run mounts them as env)
        for (String secret : List.of("GEMINI_API_KEY", "GOOGLE_API_KEY", "STUDIO_TOKEN", "STUDIO_CALLBACK_SECRET")) env.remove(secret);
        if (props.allowScripted()) env.put("STUDIO_ALLOW_SCRIPTED", "1"); else env.remove("STUDIO_ALLOW_SCRIPTED");
        // tests only: serve this file for https://acme.example/ downloads so flows run offline (needs allowScripted)
        if (props.allowScripted() && props.fakeDownload() != null) env.put("STUDIO_FAKE_DOWNLOAD", props.fakeDownload().toString());
        else env.remove("STUDIO_FAKE_DOWNLOAD");
        Path log = logFile(jobId);
        Files.createDirectories(log.getParent());

        Process process;
        try {
            process = builder.start();
        } catch (IOException e) {
            throw StudioException.internal("Could not start the Python engine (" + props.python() + "): " + e.getMessage(),
                    "python_unavailable");
        }
        live.add(process);
        Outcome outcome = new Outcome();
        Set<ProcessHandle> known = ConcurrentHashMap.newKeySet();
        Thread writer = Thread.ofVirtual().name("task-stdin-" + jobId).start(() -> {
            try (var out = process.getOutputStream()) {
                out.write(request);
            } catch (IOException ignored) {
                // the process may exit before reading everything; its exit status tells the story
            }
        });
        Thread stdout = Thread.ofVirtual().name("task-stdout-" + jobId).start(() -> readStdout(process, outcome, listener, log, secrets));
        Thread stderr = Thread.ofVirtual().name("task-stderr-" + jobId).start(() -> readStderr(process, log, secrets));

        String killed = null;
        try {
            long start = System.nanoTime();
            long lastScan = 0;
            while (!process.waitFor(100, TimeUnit.MILLISECONDS)) {
                long now = System.nanoTime();
                if (now - lastScan > TimeUnit.SECONDS.toNanos(1)) {
                    process.descendants().forEach(known::add);
                    lastScan = now;
                }
                if (now - outcome.lastLine() > props.idleTimeout().toNanos()) { killed = "idle"; break; }
                if (now - start > props.ceiling().toNanos()) { killed = "ceiling"; break; }
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            killed = "interrupted";
        }
        if (killed != null) {
            appendLog(log, "[runner] killing the task: " + killed);
            killTree(process, known);
            try {
                process.waitFor(5, TimeUnit.SECONDS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }
        join(stdout, 5_000);
        join(stderr, 5_000);
        // a grandchild that outlived the task and still holds the pipes must go too
        killLeftovers(known);
        join(stdout, 2_000);
        join(stderr, 2_000);
        live.remove(process);
        writer.interrupt();
        int exit = process.isAlive() ? -1 : process.exitValue();

        if (killed != null) {
            String why = killed.equals("idle") ? "The task stopped responding (no output for " + props.idleTimeout().toSeconds() + " s) and was stopped"
                    : killed.equals("ceiling") ? "The task ran longer than " + props.ceiling().toMinutes() + " minutes and was stopped"
                    : "The task was cancelled";
            throw StudioException.internal(why, "timeout");
        }
        Outcome.Error error = outcome.error();
        if (error != null) throw failure(error.kind, error.code, error.message);
        JsonNode result = outcome.result();
        if (result == null && outcome.resultFile() != null) result = readResultFile(outDir, outcome.resultFile());
        if (exit == 0 && result != null) return new TaskResult(result, outDir, props.keepJobDirs());
        if (exit == 0) throw StudioException.internal("The task ended without a result", "no_result");
        throw StudioException.internal("The task failed (exit code " + exit + "); see runtime/logs/jobs/" + jobId + ".log", "task_failed");
    }

    private JsonNode readResultFile(Path outDir, String file) {
        try {
            JsonNode node = Json.read(Ids.inside(outDir, file));
            if (node == null) throw StudioException.internal("The task's result file is missing", "no_result");
            return node;
        } catch (RuntimeException e) {
            if (e instanceof StudioException s) throw s;
            throw StudioException.internal("The task's result file is unreadable", "no_result");
        }
    }

    private void readStdout(Process process, Outcome outcome, Listener listener, Path log, List<String> secrets) {
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(process.getInputStream(), StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) {
                outcome.touch();
                JsonNode message = parseLine(line);
                if (message == null) {
                    appendLog(log, "[stdout] " + redact(line, secrets));
                    continue;
                }
                switch (Json.text(message, "type") == null ? "" : Json.text(message, "type")) {
                    case "progress" -> { if (message.has("text")) listener.progress(message.get("text").asString()); }
                    case "usage" -> listener.usage(message);
                    case "result" -> outcome.setResult(message);
                    case "error" -> outcome.setError(Json.text(message, "kind"), Json.text(message, "code"),
                            Json.text(message, "message") == null ? "The task failed" : Json.text(message, "message"));
                    default -> { /* heartbeat and unknown types only keep the task alive */ }
                }
            }
        } catch (IOException | RuntimeException e) {
            LOG.debug("stdout reader ended: {}", e.toString());
        }
    }

    private void readStderr(Process process, Path log, List<String> secrets) {
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(process.getErrorStream(), StandardCharsets.UTF_8))) {
            String line;
            while ((line = reader.readLine()) != null) appendLog(log, redact(line, secrets));
        } catch (IOException | RuntimeException e) {
            LOG.debug("stderr reader ended: {}", e.toString());
        }
    }

    private static JsonNode parseLine(String line) {
        String text = line.strip();
        if (!text.startsWith("{")) return null;
        try {
            JsonNode node = Json.parse(text);
            return node.isObject() && node.has("type") ? node : null;
        } catch (RuntimeException e) {
            return null;
        }
    }

    private static synchronized void appendLog(Path log, String line) {
        try {
            Files.writeString(log, line + "\n", StandardCharsets.UTF_8, StandardOpenOption.CREATE, StandardOpenOption.APPEND);
        } catch (IOException ignored) {
            // logging must never break a job
        }
    }

    private static List<String> secrets(JsonNode llm) {
        List<String> out = new ArrayList<>();
        if (llm == null) return out;
        collectKeys(llm, out);
        return out;
    }

    private static void collectKeys(JsonNode node, List<String> out) {
        if (node.isObject()) {
            node.properties().forEach(e -> {
                if (e.getKey().equals("api_key") && e.getValue().isString() && e.getValue().asString().length() >= 4)
                    out.add(e.getValue().asString());
                else collectKeys(e.getValue(), out);
            });
        }
    }

    static String redact(String line, List<String> secrets) {
        for (String secret : secrets) line = line.replace(secret, "[redacted]");
        return line;
    }

    private static void join(Thread thread, long millis) {
        try {
            thread.join(millis);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    /** Kills the process and every descendant (collected before the parent dies, so none is orphaned unseen). */
    static void killTree(Process process, Set<ProcessHandle> known) {
        Set<ProcessHandle> all = new HashSet<>(known);
        process.descendants().forEach(all::add);
        all.forEach(ProcessHandle::destroyForcibly);
        process.destroyForcibly();
    }

    private static void killLeftovers(Set<ProcessHandle> known) {
        known.stream().filter(ProcessHandle::isAlive).forEach(ProcessHandle::destroyForcibly);
    }

    private void killAll() {
        for (Process process : live) killTree(process, Set.of());
    }

    /** What the stdout reader saw. */
    private static final class Outcome {
        record Error(String kind, String code, String message) {}

        private volatile long lastLine = System.nanoTime();
        private JsonNode result;
        private String resultFile;
        private Error error;

        long lastLine() { return lastLine; }

        void touch() { lastLine = System.nanoTime(); }

        synchronized void setResult(JsonNode message) {
            if (message.has("data")) result = message.get("data");
            else if (message.has("file")) resultFile = message.get("file").asString();
        }

        synchronized void setError(String kind, String code, String message) {
            if (error == null) error = new Error(kind, code, message);
        }

        synchronized JsonNode result() { return result; }

        synchronized String resultFile() { return resultFile; }

        synchronized Error error() { return error; }
    }

    static Duration seconds(long s) { return Duration.ofSeconds(s); }
}
