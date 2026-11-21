package dev.robode.studio.jobs;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import dev.robode.studio.common.Json;
import dev.robode.studio.common.StudioException;
import dev.robode.studio.config.StudioProperties;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

/** Runs a real Python process (a tiny fake engine in src/test/resources/fake_engine) through the real runner. */
class TaskRunnerTest {
    static final String KEY = "sk-test-SECRET-123456";
    @TempDir Path temp;
    Path repo;
    Path runtime;
    TaskRunner runner;

    @BeforeEach
    void setUp() throws IOException {
        repo = temp.resolve("repo");
        runtime = temp.resolve("runtime");
        Path tasks = repo.resolve("server/studio/tasks");
        Files.createDirectories(tasks);
        Files.writeString(repo.resolve("server/studio/__init__.py"), "");
        Files.writeString(tasks.resolve("__init__.py"), "");
        Files.copy(Path.of("src/test/resources/fake_engine/tasks_main.py"), tasks.resolve("__main__.py"));
        runner = runner(Duration.ofSeconds(60));
    }

    TaskRunner runner(Duration idle) {
        return new TaskRunner(new StudioProperties(repo, runtime, Path.of(python()), "127.0.0.1", 0, null, true, null,
                idle, Duration.ofMinutes(5), 4, false, false, null, null));
    }

    static String python() {
        for (String candidate : new String[] {"../.venv/bin/python", "/usr/bin/python3", "/opt/homebrew/bin/python3", "/usr/local/bin/python3"})
            if (Files.isExecutable(Path.of(candidate))) return Path.of(candidate).toAbsolutePath().toString();
        org.junit.jupiter.api.Assumptions.abort("no python3 available");
        return null;
    }

    static ObjectNode llm() {
        ObjectNode spec = Json.obj().put("id", "g").put("kind", "gemini").put("api_key", KEY);
        ObjectNode roles = Json.obj();
        roles.set("spec", spec);
        ObjectNode llm = Json.obj();
        llm.set("roles", roles);
        llm.putNull("profile");
        return llm;
    }

    static class Capture implements TaskRunner.Listener {
        final List<String> progress = new ArrayList<>();
        final List<JsonNode> usage = new ArrayList<>();
        @Override public void progress(String text) { progress.add(text); }
        @Override public void usage(JsonNode message) { usage.add(message); }
    }

    @Test
    void streamsProgressUsageAndResultAndIgnoresNonJsonLines() throws IOException {
        Capture capture = new Capture();
        try (TaskResult r = runner.run("job1", "ok", Json.obj().put("a", 1), llm(), "p_ab12cd34ef", "design", capture)) {
            assertThat(r.data().get("echo").get("a").asInt()).isEqualTo(1);
            assertThat(r.data().get("context").get("project_id").asString()).isEqualTo("p_ab12cd34ef");
            assertThat(r.data().get("context").get("task").asString()).isEqualTo("design");
            assertThat(r.data().get("task").asString()).isEqualTo("ok");
        }
        assertThat(capture.progress).containsExactly("Running the task: 12 waypoints");
        assertThat(capture.usage).hasSize(1);
        assertThat(capture.usage.get(0).get("role").asString()).isEqualTo("spec");
        assertThat(Files.exists(runner.jobsDir().resolve("job1"))).isFalse(); // out_dir cleaned on close
        assertThat(Files.readString(logPath("job1"))).contains("a stray non-JSON line");
    }

    Path logPath(String id) { return runner.logFile(id); }

    @Test
    void largeResultsComeFromAFileInOutDir() {
        try (TaskResult r = runner.run("job2", "file", Json.obj(), llm(), null, null, null)) {
            assertThat(r.data().get("big").asBoolean()).isTrue();
        }
    }

    @Test
    void errorKindsMapToStatusesAndCodes() {
        for (var expected : List.of(List.of("value", 422), List.of("not_found", 404), List.of("llm", 502), List.of("internal", 500))) {
            ObjectNode input = Json.obj().put("kind", (String) expected.get(0)).put("code", "datasheet_download");
            assertThatThrownBy(() -> runner.run("e-" + expected.get(0), "error", input, llm(), null, null, null))
                    .isInstanceOfSatisfying(StudioException.class, e -> {
                        assertThat(e.status()).isEqualTo((int) (Integer) expected.get(1));
                        assertThat(e.code()).isEqualTo("datasheet_download");
                        assertThat(e.getMessage()).isEqualTo("boom " + expected.get(0));
                    });
        }
        assertThatThrownBy(() -> runner.run("crash", "crash", Json.obj(), llm(), null, null, null))
                .isInstanceOfSatisfying(StudioException.class, e -> {
                    assertThat(e.status()).isEqualTo(500);
                    assertThat(e.getMessage()).contains("exit code 3");
                });
    }

    @Test
    void usageSentBeforeAKillIsKeptAndTheWatchdogKillsTheHungScriptAndItsChild() throws Exception {
        TaskRunner quick = runner(Duration.ofMillis(1500));
        Path pidfile = temp.resolve("pids.txt");
        Capture capture = new Capture();
        long started = System.nanoTime();
        assertThatThrownBy(() -> quick.run("hang", "hang", Json.obj().put("pidfile", pidfile.toString()), llm(), null, null, capture))
                .isInstanceOfSatisfying(StudioException.class, e -> {
                    assertThat(e.status()).isEqualTo(500);
                    assertThat(e.code()).isEqualTo("timeout");
                    assertThat(e.getMessage()).contains("stopped responding");
                });
        assertThat(Duration.ofNanos(System.nanoTime() - started)).isLessThan(Duration.ofSeconds(20));
        assertThat(capture.usage).hasSize(1);
        String[] pids = Files.readString(pidfile).trim().split(" ");
        for (String pid : pids) {
            long id = Long.parseLong(pid);
            boolean alive = true;
            for (int i = 0; i < 50 && alive; i++) {
                alive = ProcessHandle.of(id).map(ProcessHandle::isAlive).orElse(false);
                if (alive) Thread.sleep(100);
            }
            assertThat(alive).as("process " + pid + " must be dead").isFalse();
        }
    }

    @Test
    void theApiKeyNeverReachesArgvEnvOrTheLog() throws Exception {
        Path dump = temp.resolve("dump.json");
        try (TaskResult r = runner.run("leakjob", "leak", Json.obj().put("dump", dump.toString()), llm(), null, null, null)) {
            assertThat(r.data()).isNotNull();
        }
        String seen = Files.readString(dump);
        assertThat(seen).doesNotContain(KEY);
        JsonNode info = Json.parse(seen);
        assertThat(info.get("argv").toString()).contains("__main__.py"); // sanity: the dump is real
        assertThat(info.get("env").get("PYTHONPATH").asString()).isEqualTo(repo.resolve("server").toString());
        assertThat(info.get("env").get("STUDIO_ALLOW_SCRIPTED").asString()).isEqualTo("1");
        assertThat(info.get("cwd").asString()).isEqualTo(repo.toRealPath().toString());
        String log = Files.readString(logPath("leakjob"));
        assertThat(log).doesNotContain(KEY).contains("calling with [redacted]");
    }

    @Test
    void staleJobFoldersAreRemovedAtStartup() throws IOException {
        Path stale = runner.jobsDir().resolve("old");
        Files.createDirectories(stale);
        Files.writeString(stale.resolve("x"), "x");
        runner.cleanStale();
        assertThat(Files.exists(stale)).isFalse();
    }
}
