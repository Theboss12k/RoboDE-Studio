package dev.robode.studio.flow;

import static org.assertj.core.api.Assertions.assertThat;

import dev.robode.studio.common.Json;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import tools.jackson.databind.JsonNode;

/**
 * The fleet flow through the Java API with real Python subprocesses and the scripted fixture LLM
 * src/test/resources/scripted/rules_fleet.json (the long carton task). Fixture results, not model results; robot
 * sizes, runs and checks are real code (kinematics, static loads), not dynamics simulation or hardware.
 */
@SpringBootTest
@AutoConfigureMockMvc
class FleetFlowTest {
    static final Path RUNTIME = FlowSupport.newRuntime("studio-fleet-flow");
    static final String BRIEF = "Move 5 kg cartons from x = -2.5 m to x = +2.5 m every 10 s; our arms reach at most 1.3 m. "
            + "The cartons sit on 0.3 m high conveyors.";

    static {
        FlowSupport.writeScriptedProfiles(RUNTIME, "rules_fleet.json");
    }

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry registry) { FlowSupport.configure(registry, RUNTIME); }

    @Autowired MockMvc mvc;

    @Test
    void adviceAssignDesignReviseApproveSearchExport() throws Exception {
        FlowSupport.Api api = new FlowSupport.Api(mvc);
        assertThat(api.send("PUT", "/llm/keys/gemini", Json.obj().put("key", "AIza-FAKE-fleet-flow-key-0001")).status()).isEqualTo(200);

        // design (single robot) -> spec carries the fleet hint -> project meta suggests a fleet
        String pid = api.post("/projects", Json.obj().put("brief", BRIEF)).body().get("id").asString();
        String base = "/projects/" + pid;
        JsonNode designed = api.job(base + "/design", Json.obj().set("answers", Json.obj()));
        assertThat(designed.get("status").asString()).as(designed.toString()).isEqualTo("done");
        assertThat(designed.get("result").get("version").asInt()).isEqualTo(1);
        JsonNode fleet = api.get(base).body().get("fleet");
        assertThat(fleet.get("suggested").asBoolean()).isTrue();
        assertThat(fleet.get("source").asString()).isEqualTo("both");
        assertThat(fleet.get("reasons")).isNotEmpty();
        assertThat(api.get(base + "/versions/1").body().has("fleet")).isFalse();

        // the user enables it and sets limits
        var put = api.send("PUT", base + "/fleet", Json.obj().put("enabled", true).set("limits", Json.obj().put("max_robots", 3).put("max_reach_m", 1.3)));
        assertThat(put.status()).as(String.valueOf(put.body())).isEqualTo(200);
        assertThat(put.body().get("fleet").get("enabled").asBoolean()).isTrue();
        assertThat(put.body().get("fleet").get("limits").get("max_robots").asInt()).isEqualTo(3);

        // assign (synchronous, no LLM): assignment + preview
        var assign = api.post(base + "/fleet/assign", Json.obj());
        assertThat(assign.status()).as(String.valueOf(assign.body())).isEqualTo(200);
        assertThat(assign.body().get("feasible").asBoolean()).as(assign.body().toString()).isTrue();
        JsonNode preview = assign.body().get("preview");
        assertThat(preview.get("bases").size()).isGreaterThanOrEqualTo(2);
        assertThat(preview.get("handovers").size()).isGreaterThanOrEqualTo(1);
        assertThat(preview.get("parks")).isNotEmpty();
        assertThat(api.get(base).body().get("fleet").get("assignment").get("confirmed").asBoolean()).isFalse();

        // fleet_design job -> child version of the single-robot design
        var started = api.post(base + "/fleet/design", Json.obj().put("base_version", 1));
        assertThat(started.status()).isEqualTo(200);
        // A second fleet_design while one runs is 409 (covered with a held job in FleetApiTest); the scripted fixture has
        // no delay_s for this task, so it is not asserted here.
        JsonNode job = api.finish(started.body());
        assertThat(job.get("status").asString()).as(job.toString()).isEqualTo("done");
        int n = job.get("result").get("version").asInt();
        assertThat(n).isEqualTo(2);
        JsonNode v2 = api.get(base + "/versions/" + n).body();
        assertThat(v2.get("parent_version").asInt()).isEqualTo(1);
        assertThat(v2.get("fleet")).isNotNull();
        JsonNode parsed = v2.get("fleet_parsed");
        assertThat(parsed.propertyNames()).contains("r1", "r2", "r3");
        for (JsonNode m : v2.get("fleet").get("members")) assertThat(m.get("robot").get("family").asString()).isEqualTo("gantry");
        assertThat(v2.get("fleet").get("checks").toString()).doesNotContain("\"passed\":false");
        assertThat(api.get(base + "/versions/1").body().has("fleet_parsed")).isFalse();

        // revise on the fleet version: the fixture answer is a fleet-only patch (r2 gets a tool yaw axis)
        JsonNode rev = api.job(base + "/revise", Json.obj().put("request", "give r2 a tool yaw axis").put("base_version", n));
        assertThat(rev.get("status").asString()).as(rev.toString()).isEqualTo("done");
        int r = rev.get("result").get("version").asInt();
        assertThat(r).isEqualTo(3);
        JsonNode v3 = api.get(base + "/versions/" + r).body();
        assertThat(v3.get("parent_version").asInt()).isEqualTo(n);
        assertThat(v3.get("fleet_parsed").propertyNames()).contains("r1", "r2", "r3");
        JsonNode r2 = null;
        for (JsonNode m : v3.get("fleet").get("members")) if (m.get("id").asString().equals("r2")) r2 = m;
        assertThat(r2.get("robot").get("params").get("tool_yaw").asBoolean()).isTrue();

        // approve the fleet version, then search on a member
        assertThat(api.post(base + "/approve", Json.obj().put("version", r)).status()).isEqualTo(200);
        assertThat(api.post(base + "/fleet/design", Json.obj()).status()).isEqualTo(409);
        assertThat(api.send("PUT", base + "/fleet", Json.obj().put("member", "r2")).status()).isEqualTo(200);
        JsonNode search = api.job(base + "/search", null);
        assertThat(search.get("status").asString()).as(search.toString()).isEqualTo("done");
        assertThat(search.get("result").get("candidates")).isNotEmpty();

        // export has the fleet files
        var export = api.get(base + "/export");
        assertThat(export.status()).isEqualTo(200);
        List<String> names = new ArrayList<>(FullFlowTest.zipEntries(export.bytes()));
        assertThat(names).contains("fleet.json", "members/r1/design.urdf", "members/r2/design.urdf", "members/r3/design.urdf", "design.urdf");

        // disabling the fleet is allowed and keeps the stored version
        var off = api.send("PUT", base + "/fleet", Json.obj().put("enabled", false));
        assertThat(off.status()).isEqualTo(200);
        assertThat(off.body().get("fleet").get("enabled").asBoolean()).isFalse();
    }
}
