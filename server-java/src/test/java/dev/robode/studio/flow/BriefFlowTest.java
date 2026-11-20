package dev.robode.studio.flow;

import static org.assertj.core.api.Assertions.assertThat;

import dev.robode.studio.common.Json;
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

/** Editable brief and brief images through the API (real Python tasks, scripted fixture LLMs). */
@SpringBootTest
@AutoConfigureMockMvc
class BriefFlowTest {
    static final Path RUNTIME = FlowSupport.newRuntime("studio-brief");

    static {
        FlowSupport.writeScriptedProfiles(RUNTIME, "rules.json");
    }

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry registry) { FlowSupport.configure(registry, RUNTIME); }

    @Autowired MockMvc mvc;

    private static final String FILES = "/brief/files";

    private String create(FlowSupport.Api api, String brief) {
        return api.post("/projects", Json.obj().put("brief", brief)).body().get("id").asString();
    }

    private static String calls(JsonNode job) throws Exception {
        return String.join("\n", Files.readAllLines(RUNTIME.resolve("tmp/jobs/" + job.get("id").asString() + "/llm_calls.jsonl")));
    }

    @Test
    void editBriefClearsQuestionsAndValidates() {
        FlowSupport.Api api = new FlowSupport.Api(mvc);
        String pid = create(api, "Move a 0.3 kg bottle from A to B");
        String base = "/projects/" + pid;
        assertThat(api.job(base + "/design", Json.obj().set("answers", Json.obj())).get("result").get("status").asString()).isEqualTo("questions");
        JsonNode view = api.get(base).body();
        assertThat(view.get("questions").size()).isPositive();
        assertThat(view.has("brief_updated") || view.has("brief_images")).isFalse();

        assertThat(api.send("PUT", base + "/brief", Json.obj().put("brief", "short")).status()).isEqualTo(422);
        assertThat(api.send("PUT", base + "/brief", Json.obj().put("brief", "   too short ")).status()).isEqualTo(422);
        assertThat(api.send("PUT", base + "/brief", Json.obj().put("brief", "x".repeat(20001))).status()).isEqualTo(422);
        assertThat(api.send("PUT", base + "/brief", Json.obj()).status()).isEqualTo(422);
        assertThat(api.send("PUT", "/projects/nope1234/brief", Json.obj().put("brief", "Move the bottle to the shelf")).status()).isEqualTo(404);

        var edited = api.send("PUT", base + "/brief", Json.obj().put("brief", "  Move a 0.5 kg bottle from A to the shelf  "));
        assertThat(edited.status()).as(String.valueOf(edited.body())).isEqualTo(200);
        assertThat(edited.body().get("brief").asString()).isEqualTo("Move a 0.5 kg bottle from A to the shelf");
        assertThat(edited.body().get("questions").size()).isZero();
        assertThat(edited.body().get("brief_updated").asString()).isNotBlank();
        // the next design call starts the rounds again
        assertThat(api.job(base + "/design", Json.obj().set("answers", Json.obj())).get("result").get("status").asString()).isEqualTo("questions");
    }

    @Test
    void briefImagesReachTheSpecCallAndShareTheBudget() throws Exception {
        FlowSupport.Api api = new FlowSupport.Api(mvc);
        String pid = create(api, "Move a 0.3 kg bottle from A to B");
        String base = "/projects/" + pid;

        var up = api.send("POST", base + FILES + "?filename=cell.png", FlowSupport.PNG);
        assertThat(up.status()).as(String.valueOf(up.body())).isEqualTo(200);
        assertThat(up.body().get(0).get("name").asString()).isEqualTo("cell.png");
        assertThat(up.body().get(0).get("mime").asString()).isEqualTo("image/png");
        JsonNode view = api.get(base).body();
        assertThat(view.get("brief_images").get(0).get("name").asString()).isEqualTo("cell.png");
        var bytes = api.get(base + FILES + "/cell.png");
        assertThat(bytes.status()).isEqualTo(200);
        assertThat(bytes.bytes()).isEqualTo(FlowSupport.PNG);
        assertThat(Files.exists(RUNTIME.resolve("projects/" + pid + "/brief/images/cell.png"))).isTrue();
        assertThat(api.send("POST", base + FILES + "?filename=notes.txt", "hello".getBytes()).status()).isEqualTo(422);
        assertThat(api.send("POST", base + FILES + "?filename=fake.png", "not an image".getBytes()).status()).isEqualTo(422);
        assertThat(api.send("GET", base + FILES + "/missing.png", null).status()).isEqualTo(404);
        assertThat(api.send("GET", base + FILES + "/..%2Fproject.json", null).status()).isNotEqualTo(200);

        // the design call carries the image
        JsonNode designed = api.job(base + "/design", Json.obj().set("answers", Json.obj()));
        assertThat(designed.get("status").asString()).as(designed.toString()).isEqualTo("done");
        assertThat(calls(designed)).contains("\"images\": 1").contains("attached to the brief");

        // shared budget: 1 brief + 5 reference + 2 answer = 8, the ninth is refused on every route
        for (int i = 0; i < 5; i++)
            assertThat(api.send("POST", base + "/reference/files?filename=r" + i + ".png", FlowSupport.PNG).status()).isEqualTo(200);
        for (int i = 0; i < 2; i++)
            assertThat(api.send("POST", base + "/answers/files?filename=a" + i + ".png&question=_general", FlowSupport.PNG).status()).isEqualTo(200);
        assertThat(api.send("POST", base + FILES + "?filename=extra.png", FlowSupport.PNG).status()).isEqualTo(422);
        assertThat(api.send("POST", base + "/reference/files?filename=r5.png", FlowSupport.PNG).status()).isEqualTo(422);
        assertThat(api.send("POST", base + "/answers/files?filename=a2.png&question=_general", FlowSupport.PNG).status()).isEqualTo(422);
        // replacing an existing name is not a new image
        assertThat(api.send("POST", base + FILES + "?filename=cell.png", FlowSupport.PNG).status()).isEqualTo(200);

        // delete
        assertThat(api.send("DELETE", base + FILES + "/cell.png", null).body().size()).isZero();
        assertThat(api.get(base).body().has("brief_images")).isFalse();
        assertThat(Files.exists(RUNTIME.resolve("projects/" + pid + "/brief/images/cell.png"))).isFalse();
        assertThat(api.send("DELETE", base + FILES + "/cell.png", null).status()).isEqualTo(404);
    }

    @Test
    void frozenAfterApproval() {
        FlowSupport.Api api = new FlowSupport.Api(mvc);
        String pid = create(api, "Move a 0.3 kg bottle from A to B");
        String base = "/projects/" + pid;
        api.job(base + "/design", Json.obj().set("answers", Json.obj()));
        JsonNode done = api.job(base + "/design", Json.obj().set("answers", Json.obj().put("cycle", 6)));
        assertThat(done.get("result").get("status").asString()).isEqualTo("designed");
        assertThat(api.send("POST", base + FILES + "?filename=cell.png", FlowSupport.PNG).status()).isEqualTo(200);
        assertThat(api.post(base + "/approve", Json.obj().put("version", 1)).status()).isEqualTo(200);
        var put = api.send("PUT", base + "/brief", Json.obj().put("brief", "Move a heavier bottle from A to B"));
        assertThat(put.status()).isEqualTo(409);
        assertThat(put.detail()).isEqualTo("The design is approved; the brief is frozen");
        assertThat(api.send("POST", base + FILES + "?filename=b.png", FlowSupport.PNG).status()).isEqualTo(409);
        assertThat(api.send("DELETE", base + FILES + "/cell.png", null).status()).isEqualTo(409);
    }
}
