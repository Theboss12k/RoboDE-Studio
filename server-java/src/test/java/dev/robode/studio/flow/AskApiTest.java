package dev.robode.studio.flow;

import static org.assertj.core.api.Assertions.assertThat;

import dev.robode.studio.common.Json;
import dev.robode.studio.flow.FlowSupport.Api;
import dev.robode.studio.flow.FlowSupport.Res;
import dev.robode.studio.store.ProjectStore;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/** POST /ask against the fake Python engine: the canned `ask` task proposes actions, Java validates and executes them (fixture answers, not a model evaluation). */
@SpringBootTest
@AutoConfigureMockMvc
class AskApiTest {
    static final Path TEMP;
    static final Path RUNTIME;
    static final Path REPO;

    static {
        try {
            TEMP = Files.createTempDirectory("studio-ask");
            RUNTIME = TEMP.resolve("runtime");
            REPO = TEMP.resolve("repo");
            Path tasks = REPO.resolve("server/studio/tasks");
            Files.createDirectories(tasks);
            Files.writeString(REPO.resolve("server/studio/__init__.py"), "");
            Files.writeString(tasks.resolve("__init__.py"), "");
            Files.copy(Path.of("src/test/resources/fake_engine/tasks_main.py"), tasks.resolve("__main__.py"));
        } catch (IOException e) {
            throw new IllegalStateException(e);
        }
    }

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry registry) {
        registry.add("studio.repo-root", REPO::toString);
        registry.add("studio.runtime", RUNTIME::toString);
        registry.add("studio.python", StudioApiTest::python);
        registry.add("studio.warm-meta", () -> "false");
    }

    @Autowired MockMvc mvc;
    @Autowired ProjectStore store;

    private Api api() { return new Api(mvc); }

    private String designed(Api api) {
        String pid = api.post("/projects", Json.obj().put("brief", "Move a box from A to B")).body().get("id").asString();
        JsonNode job = api.job("/projects/" + pid + "/design", Json.obj().set("answers", Json.obj()));
        assertThat(job.get("status").asString()).as(job.toString()).isEqualTo("done");
        return pid;
    }

    private static ObjectNode question(String text, String pid, Integer version) {
        ObjectNode ctx = Json.obj();
        if (pid != null) ctx.put("project_id", pid);
        if (version != null) ctx.put("version", version);
        ctx.put("stage", "design");
        ObjectNode body = Json.obj().put("question", text);
        body.set("context", ctx);
        return body;
    }

    /** POST /ask?wait=60 and returns the finished job record's result. */
    private JsonNode ask(Api api, ObjectNode body) {
        Res r = api.post("/ask?wait=60", body);
        assertThat(r.status()).as(String.valueOf(r.body())).isEqualTo(200);
        assertThat(r.body().get("kind").asString()).isEqualTo("ask");
        assertThat(r.body().get("status").asString()).as(r.body().toString()).isEqualTo("done");
        return r.body().get("result");
    }

    private JsonNode activity(String pid) {
        return Json.readObject(RUNTIME.resolve("projects").resolve(pid).resolve("project.json")).get("activity");
    }

    @Test
    void invalidAndMissingReferences() {
        Api api = api();
        String pid = designed(api);
        assertThat(api.post("/ask", Json.obj().put("question", "")).status()).isEqualTo(422);
        assertThat(api.post("/ask", Json.obj()).status()).isEqualTo(422);
        assertThat(api.post("/ask", null).status()).isEqualTo(422);
        assertThat(api.post("/ask", Json.obj().put("question", "x".repeat(2001))).status()).isEqualTo(422);
        assertThat(api.post("/ask", question("hi", pid, 0)).status()).isEqualTo(422);
        ObjectNode badRole = question("hi", pid, null);
        badRole.set("history", Json.arr().add(Json.obj().put("role", "system").put("text", "x")));
        assertThat(api.post("/ask", badRole).status()).isEqualTo(422);
        ObjectNode needsProject = Json.obj().put("question", "hi");
        needsProject.set("context", Json.obj().put("version", 1));
        assertThat(api.post("/ask", needsProject).status()).isEqualTo(422);
        assertThat(api.post("/ask?wait=601", question("hi", pid, null)).status()).isEqualTo(422);

        assertThat(api.post("/ask", question("hi", "p_0000000000", null)).status()).isEqualTo(404);
        assertThat(api.post("/ask", question("hi", pid, 7)).status()).isEqualTo(404);
        ObjectNode build = question("hi", pid, null);
        ((ObjectNode) build.get("context")).put("build", 3);
        assertThat(api.post("/ask", build).status()).isEqualTo(404);
        ObjectNode cand = question("hi", pid, null);
        ((ObjectNode) cand.get("context")).put("candidate", "nobody");
        assertThat(api.post("/ask", cand).status()).isEqualTo(404);
    }

    @Test
    void executesAReviseProposalAndLogsIt() {
        Api api = api();
        String pid = designed(api);
        JsonNode result = ask(api, question("please REVISE it", pid, 1));
        assertThat(result.get("answer").asString()).isEqualTo("Canned answer.");
        assertThat(result.get("citations")).hasSize(1);   // the "weird" kind is dropped
        ArrayNode taken = (ArrayNode) result.get("actions_taken");
        assertThat(taken).hasSize(1);
        JsonNode action = taken.get(0);
        assertThat(action.get("kind").asString()).isEqualTo("revise");
        assertThat(action.get("ok").asBoolean()).as(action.toString()).isTrue();
        String jobId = action.get("job_id").asString();
        JsonNode started = api.get("/jobs/" + jobId).body();           // the job exists, in the project's job list too
        assertThat(started.get("kind").asString()).isEqualTo("revise");
        assertThat(started.get("robot_id").asString()).isEqualTo(pid);
        JsonNode done = api.finish(started);
        assertThat(done.get("status").asString()).as(done.toString()).isEqualTo("done");
        assertThat(api.get("/projects/" + pid).body().get("versions")).hasSize(2);

        JsonNode log = activity(pid);
        assertThat(log).hasSize(1);
        assertThat(log.get(0).get("source").asString()).isEqualTo("ask");
        assertThat(log.get(0).get("kind").asString()).isEqualTo("revise");
        assertThat(log.get(0).get("ok").asBoolean()).isTrue();
        assertThat(log.get(0).get("job_id").asString()).isEqualTo(jobId);
        assertThat(log.get(0).get("summary").asString()).contains("20 cm");
        assertThat(api.get("/projects/" + pid).body().has("activity")).isFalse();
    }

    @Test
    void refusesWhatIsNotOnTheList() {
        Api api = api();
        String pid = designed(api);
        JsonNode result = ask(api, question("DELETE the project", pid, null));
        JsonNode action = result.get("actions_taken").get(0);
        assertThat(action.get("kind").asString()).isEqualTo("delete_project");
        assertThat(action.get("ok").asBoolean()).isFalse();
        assertThat(action.get("error").asString()).isEqualTo("not allowed");
        assertThat(api.get("/projects/" + pid).status()).isEqualTo(200);    // nothing was deleted
        JsonNode log = activity(pid);
        assertThat(log.get(0).get("ok").asBoolean()).isFalse();
        assertThat(log.get(0).get("kind").asString()).isEqualTo("delete_project");
    }

    @Test
    void capsAtTwoActions() {
        Api api = api();
        String pid = designed(api);
        JsonNode taken = ask(api, question("THREE things", pid, null)).get("actions_taken");
        assertThat(taken).hasSize(3);
        assertThat(taken.get(0).get("ok").asBoolean()).as(taken.toString()).isTrue();
        assertThat(taken.get(1).get("ok").asBoolean()).as(taken.toString()).isTrue();
        assertThat(taken.get(2).get("ok").asBoolean()).isFalse();
        assertThat(taken.get(2).get("error").asString()).isEqualTo("limit: 2 actions per question");
        assertThat(api.get("/projects/" + pid).body().get("brief").asString()).isEqualTo("A brand new brief for the job");
        assertThat(api.get("/projects/" + pid).body().has("savings_inputs")).isFalse();   // the third (set_baseline) did not run
        assertThat(activity(pid)).hasSize(3);
    }

    @Test
    void actionsAreValidatedLikeTheRoutes() {
        Api api = api();
        String pid = designed(api);
        JsonNode taken = ask(api, question("BAD ones", pid, null)).get("actions_taken");
        assertThat(taken.get(0).get("ok").asBoolean()).isFalse();
        assertThat(taken.get(0).get("error").asString()).contains("operators");
        assertThat(taken.get(1).get("ok").asBoolean()).isFalse();
        assertThat(taken.get(1).get("error").asString()).contains("Candidate not found");
        // without a project nothing can be done
        JsonNode none = ask(api, question("REVISE it", null, null)).get("actions_taken").get(0);
        assertThat(none.get("ok").asBoolean()).isFalse();
        assertThat(none.get("error").asString()).contains("needs a project");
    }

    @Test
    void approvalsBecomeConfirmChipsAndAreNeverExecuted() {
        Api api = api();
        String pid = designed(api);
        JsonNode result = ask(api, question("APPROVE it", pid, null));
        JsonNode confirm = result.get("confirm");
        assertThat(confirm).hasSize(2);   // v9 does not exist and "sell" is not a decision action: both dropped
        assertThat(confirm.get(0).get("kind").asString()).isEqualTo("approve");
        assertThat(confirm.get(0).get("request").toString()).isEqualTo("{\"version\":1}");
        assertThat(confirm.get(1).get("request").toString()).isEqualTo("{\"action\":\"self\"}");
        assertThat(result.get("navigate").toString()).isEqualTo("{\"stage\":\"decision\",\"version\":1}");
        assertThat(result.get("actions_taken")).isEmpty();
        JsonNode project = api.get("/projects/" + pid).body();
        assertThat(project.get("approved_version").isNull()).isTrue();
        assertThat(project.get("decision").isNull()).isTrue();
    }

    @Test
    void groundingComesFromStorageWithoutFrames() throws Exception {
        Api api = api();
        String pid = designed(api);
        // a stored version with real frames and a huge event list
        ObjectNode v = store.version(pid, 1);
        ObjectNode sim = (ObjectNode) v.get("sim");
        ArrayNode frames = Json.arr();
        for (int i = 0; i <= 40; i++)
            frames.add(Json.obj().put("t", i * 0.1).put("phase", "step " + i).set("joints", Json.obj().put("j1", i * 0.01)));
        sim.set("frames", frames);
        ArrayNode events = Json.arr();
        for (int i = 0; i < 3000; i++) events.add(Json.obj().put("t", i * 0.001).put("kind", "info").put("text", "event number " + i + " " + "x".repeat(60)));
        sim.set("events", events);
        Json.write(store.versionPath(pid, 1, ".json"), v);

        ObjectNode body = question("GROUNDING please", pid, 1);
        ((ObjectNode) body.get("context")).put("t", 1.52);
        ArrayNode history = Json.arr();
        for (int i = 0; i < 8; i++) history.add(Json.obj().put("role", i % 2 == 0 ? "user" : "assistant").put("text", "turn " + i));
        body.set("history", history);
        JsonNode echoed = Json.parse(ask(api, body).get("answer").asString());
        assertThat(echoed.get("has_frames").asBoolean()).isFalse();
        assertThat(echoed.get("bytes").asInt()).isLessThanOrEqualTo(60 * 1024);
        assertThat(echoed.get("keys").toString()).isEqualTo("[\"project\",\"version\"]");
        assertThat(echoed.get("version_keys").toString()).contains("moment", "checks", "spec");
        assertThat(echoed.get("project_keys").toString()).contains("brief", "versions");
        assertThat(echoed.get("moment").get("t").asDouble()).isEqualTo(1.5);   // the stored frame nearest to 1.52
        assertThat(echoed.get("moment").get("phase").asString()).isEqualTo("step 15");
        assertThat(echoed.get("history").asInt()).isEqualTo(6);                // older turns are dropped
        assertThat(echoed.get("context").get("t").asDouble()).isEqualTo(1.52);
    }
}
