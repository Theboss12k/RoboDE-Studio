package dev.robode.studio.flow;

import static org.assertj.core.api.Assertions.assertThat;

import dev.robode.studio.common.Json;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import tools.jackson.databind.JsonNode;

/** Question comments, the per-round note and answer images through the API (real Python tasks, scripted fixture LLMs). */
@SpringBootTest
@AutoConfigureMockMvc
class AnswersFlowTest {
    static final Path RUNTIME = FlowSupport.newRuntime("studio-answers");

    static {
        FlowSupport.writeScriptedProfiles(RUNTIME, "rules.json");
    }

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry registry) { FlowSupport.configure(registry, RUNTIME); }

    @Autowired MockMvc mvc;

    private static final String FILES = "/answers/files";

    @Test
    void commentsNoteAndImages() throws Exception {
        FlowSupport.Api api = new FlowSupport.Api(mvc);
        String pid = api.post("/projects", Json.obj().put("brief", "Move a 0.3 kg bottle from A to B")).body().get("id").asString();
        String base = "/projects/" + pid;
        assertThat(api.job(base + "/design", Json.obj().set("answers", Json.obj())).get("result").get("status").asString()).isEqualTo("questions");
        JsonNode view = api.get(base).body();
        assertThat(view.has("answer_comments") || view.has("answer_notes") || view.has("answer_images")).isFalse();

        // upload: listed in the view, served back with its content type
        var up = api.send("POST", base + FILES + "?filename=sketch.png&question=cycle", FlowSupport.PNG);
        assertThat(up.status()).as(String.valueOf(up.body())).isEqualTo(200);
        assertThat(up.body().get(0).get("name").asString()).isEqualTo("sketch.png");
        view = api.get(base).body();
        assertThat(view.get("answer_images").get(0).get("question").asString()).isEqualTo("cycle");
        assertThat(view.get("answer_images").get(0).get("mime").asString()).isEqualTo("image/png");
        var bytes = api.get(base + FILES + "/sketch.png");
        assertThat(bytes.status()).isEqualTo(200);
        assertThat(bytes.bytes()).isEqualTo(FlowSupport.PNG);
        assertThat(Files.exists(RUNTIME.resolve("projects/" + pid + "/answers/images/sketch.png"))).isTrue();

        // refusals
        assertThat(api.send("POST", base + FILES + "?filename=notes.txt&question=cycle", "hello".getBytes()).status()).isEqualTo(422);
        assertThat(api.send("POST", base + FILES + "?filename=fake.png&question=cycle", "not an image".getBytes()).status()).isEqualTo(422);
        var unknown = api.send("POST", base + FILES + "?filename=b.png&question=nope", FlowSupport.PNG);
        assertThat(unknown.status()).isEqualTo(422);
        assertThat(api.send("POST", base + FILES + "?filename=g.png&question=_general", FlowSupport.PNG).status()).isEqualTo(200);
        assertThat(api.send("GET", base + FILES + "/missing.png", null).status()).isEqualTo(404);
        assertThat(api.send("GET", base + FILES + "/..%2Fproject.json", null).status()).isNotEqualTo(200);

        // validation of the design body
        assertThat(api.post(base + "/design", Json.obj().set("comments", Json.arr())).status()).isEqualTo(422);
        assertThat(api.post(base + "/design", Json.obj().set("comments", Json.obj().put("Bad-Key", "x"))).status()).isEqualTo(422);
        assertThat(api.post(base + "/design", Json.obj().set("comments", Json.obj().put("cycle", "x".repeat(1001)))).status()).isEqualTo(422);
        assertThat(api.post(base + "/design", Json.obj().put("note", "x".repeat(4001))).status()).isEqualTo(422);
        assertThat(api.post(base + "/design", Json.obj().put("auto_repair", "yes")).status()).isEqualTo(422);

        // design with a comment and a note: stored, and the spec call sees them
        var body = Json.obj();
        body.set("answers", Json.obj().put("cycle", 6));
        body.set("comments", Json.obj().put("cycle", "SENTINEL-COMMENT-ZX81"));
        body.put("note", "SENTINEL-NOTE-QK42");
        JsonNode designed = api.job(base + "/design", body);
        assertThat(designed.get("status").asString()).as(designed.toString()).isEqualTo("done");
        view = api.get(base).body();
        assertThat(view.get("answer_comments").get("cycle").asString()).isEqualTo("SENTINEL-COMMENT-ZX81");
        assertThat(view.get("answer_notes").get(0).get("text").asString()).isEqualTo("SENTINEL-NOTE-QK42");
        assertThat(view.get("answer_notes").get(0).get("round").asInt()).isEqualTo(1);
        List<String> calls = Files.readAllLines(RUNTIME.resolve("tmp/jobs/" + designed.get("id").asString() + "/llm_calls.jsonl"));
        String all = String.join("\n", calls);
        assertThat(all).contains("SENTINEL-COMMENT-ZX81").contains("SENTINEL-NOTE-QK42");

        // delete removes the file and the entry; the view drops the key again
        assertThat(api.send("DELETE", base + FILES + "/sketch.png", null).body().size()).isEqualTo(1);
        assertThat(api.send("DELETE", base + FILES + "/g.png", null).body().size()).isZero();
        assertThat(Files.exists(RUNTIME.resolve("projects/" + pid + "/answers/images/sketch.png"))).isFalse();
        assertThat(api.get(base).body().has("answer_images")).isFalse();
        assertThat(api.send("DELETE", base + FILES + "/sketch.png", null).status()).isEqualTo(404);
    }

    @Test
    void sharedImageBudget() {
        FlowSupport.Api api = new FlowSupport.Api(mvc);
        String pid = api.post("/projects", Json.obj().put("brief", "Budget check")).body().get("id").asString();
        String base = "/projects/" + pid;
        for (int i = 0; i < 6; i++)
            assertThat(api.send("POST", base + "/reference/files?filename=r" + i + ".png", FlowSupport.PNG).status()).isEqualTo(200);
        for (int i = 0; i < 2; i++)
            assertThat(api.send("POST", base + FILES + "?filename=a" + i + ".png&question=_general", FlowSupport.PNG).status()).isEqualTo(200);
        var ninth = api.send("POST", base + FILES + "?filename=a2.png&question=_general", FlowSupport.PNG);
        assertThat(ninth.status()).isEqualTo(422);
        assertThat(ninth.detail()).isEqualTo("At most 8 images in total go to the spec agent (reference photos count too)");
        // clearing the reference keeps the answer images
        assertThat(api.send("DELETE", base + "/reference", null).status()).isEqualTo(200);
        assertThat(api.get(base).body().get("answer_images").size()).isEqualTo(2);
        for (int i = 0; i < 6; i++)
            assertThat(api.send("POST", base + "/reference/files?filename=s" + i + ".png", FlowSupport.PNG).status()).isEqualTo(200);
        var over = api.send("POST", base + "/reference/files?filename=s6.png", FlowSupport.PNG);
        assertThat(over.status()).isEqualTo(422);
    }
}
