package dev.robode.studio.flow;

import static org.assertj.core.api.Assertions.assertThat;

import dev.robode.studio.common.Json;
import dev.robode.studio.flow.FlowSupport.Api;
import dev.robode.studio.flow.FlowSupport.Res;
import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import org.junit.jupiter.api.Assumptions;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * The robot studio flow through the Java API on the real Python tasks (CAD, twin, PDF) with the scripted fixture LLM
 * src/test/resources/scripted/rules_studio.json and saved shop pages (no network). Fixture answers, not model
 * results; the checks are kinematic and static-load calculations in code, not dynamics simulation or hardware tests.
 * Skipped when the Python .venv has no OCP.
 */
@SpringBootTest
@AutoConfigureMockMvc
class StudioFlowTest {
    static final Path RUNTIME = FlowSupport.newRuntime("studio-studio-flow");
    static final Path DOWNLOADS = RUNTIME.resolveSibling(RUNTIME.getFileName() + "-downloads.json");
    static final Path PYTHON = Path.of("../.venv/bin/python").toAbsolutePath().normalize();

    static {
        FlowSupport.writeScriptedProfiles(RUNTIME, "rules_studio.json");
        try {
            Path shops = FlowSupport.REPO.resolve("tests/fixtures/shops");
            ObjectNode map = Json.obj();
            map.put("https://robu.in/product/nema23-3nm", shops.resolve("robu_nema23_3nm.html").toString());
            map.put("https://www.omc-stepperonline.com/nema23-1-26nm", shops.resolve("stepperonline_23hs_1_26nm.html").toString());
            Files.writeString(DOWNLOADS, Json.MAPPER.writeValueAsString(map));
        } catch (java.io.IOException e) {
            throw new IllegalStateException(e);
        }
    }

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry registry) {
        FlowSupport.configure(registry, RUNTIME);
        registry.add("studio.fake-download", DOWNLOADS::toString);
    }

    @BeforeAll
    static void needsOcp() throws Exception {
        boolean ok = false;
        if (Files.isExecutable(PYTHON)) {
            Process p = new ProcessBuilder(PYTHON.toString(), "-c", "import OCP").redirectErrorStream(true).start();
            p.getInputStream().readAllBytes();
            ok = p.waitFor() == 0;
        }
        Assumptions.assumeTrue(ok, "the Python .venv has no OCP (cadquery-ocp)");
    }

    @Autowired MockMvc mvc;

    /** A valid small PNG: the PDF embeds the snapshots, so the placeholder bytes of FlowSupport.PNG would not decode. */
    private static byte[] realPng() throws java.io.IOException {
        var img = new java.awt.image.BufferedImage(8, 8, java.awt.image.BufferedImage.TYPE_INT_RGB);
        var out = new java.io.ByteArrayOutputStream();
        javax.imageio.ImageIO.write(img, "png", out);
        return out.toByteArray();
    }

    private static String text(Res r) { return new String(r.bytes(), StandardCharsets.UTF_8); }

    @Test
    void buildTwinSnapshotsApproveRebuild() throws Exception {
        Api api = new Api(mvc);
        assertThat(api.send("PUT", "/llm/keys/gemini", Json.obj().put("key", "AIza-FAKE-studio-flow-key-0001")).status()).isEqualTo(200);

        String pid = api.post("/projects", Json.obj().put("brief", "Move a 0.3 kg bottle from A to B")).body().get("id").asString();
        String project = "/projects/" + pid;
        JsonNode designed = api.job(project + "/design", Json.obj().set("answers", Json.obj()));
        assertThat(designed.get("status").asString()).as(designed.toString()).isEqualTo("done");
        if ("questions".equals(designed.get("result").get("status").asString())) {
            ObjectNode answers = Json.obj();
            for (JsonNode q : api.get(project).body().get("questions")) answers.put(q.get("id").asString(), 6);
            designed = api.job(project + "/design", Json.obj().set("answers", answers));
            assertThat(designed.get("status").asString()).as(designed.toString()).isEqualTo("done");
        }
        assertThat(designed.get("result").get("status").asString()).as(designed.toString()).isEqualTo("designed");
        assertThat(api.post(project + "/approve", Json.obj().put("version", 1)).status()).isEqualTo(200);

        String s = project + "/studio";
        JsonNode built = api.job(s + "/builds", null);
        assertThat(built.get("status").asString()).as(built.toString()).isEqualTo("done");
        assertThat(built.get("result").get("build").asInt()).isEqualTo(1);
        JsonNode b1 = api.get(s + "/builds/1").body();

        // motors: shop links and prices with the date seen
        JsonNode motors = b1.get("motors");
        assertThat(motors).isNotEmpty();
        boolean priced = false;
        for (JsonNode m : motors) {
            if (!"shop".equals(m.get("source").asString())) continue;
            assertThat(m.get("url").asString()).startsWith("https://");
            if (m.hasNonNull("price")) {
                assertThat(m.get("price").get("seen").asString()).isNotBlank();
                priced = true;
            }
        }
        assertThat(priced).as("at least one shop motor with a price seen").isTrue();

        // files listed and downloadable
        List<String> paths = new ArrayList<>();
        b1.get("files").forEach(f -> paths.add(f.get("path").asString()));
        assertThat(paths).contains("assembly.step");
        Res step = api.get(s + "/builds/1/files/assembly.step");
        assertThat(step.status()).isEqualTo(200);
        assertThat(text(step)).startsWith("ISO-10303-21");
        String stl = b1.get("files").findValuesAsString("path").stream().filter(p -> p.endsWith(".stl")).findFirst().orElseThrow();
        assertThat(api.get(s + "/builds/1/files/" + stl).bytes().length).isGreaterThan(0);

        // parsed URDF: a gripper joint and mesh links
        JsonNode parsed = api.get(s + "/builds/1").body().get("parsed");
        List<String> joints = new ArrayList<>();
        parsed.get("joints").forEach(j -> joints.add(j.get("name").asString()));
        assertThat(joints).contains("gripper");
        assertThat(parsed.get("mesh_uris")).isNotEmpty();

        // twin: a joint target, a cartesian target, a run above the limits
        String first = joints.stream().filter(j -> !j.equals("gripper")).findFirst().orElseThrow();
        ObjectNode q0 = Json.obj();
        joints.forEach(j -> q0.put(j, 0.0));
        ObjectNode jointMove = Json.obj();
        jointMove.set("q0", q0);
        jointMove.set("target", Json.obj().set("joints", Json.obj().put(first, 0.3)));
        Res moved = api.post(s + "/builds/1/twin/move", jointMove);
        assertThat(moved.status()).as(String.valueOf(moved.body())).isEqualTo(200);
        assertThat(moved.body().get("frames")).isNotEmpty();

        JsonNode marker = api.get(project + "/versions/1").body().path("scene").path("markers").path(0).path("position");
        ObjectNode cart = Json.obj();
        cart.set("q0", q0);
        cart.set("target", Json.obj().set("cartesian", marker.isArray() && marker.size() == 3 ? marker : Json.arr().add(0.5).add(0.3).add(0.75)));
        Res cm = api.post(s + "/builds/1/twin/move", cart);
        assertThat(cm.status()).as(String.valueOf(cm.body())).isEqualTo(200);
        assertThat(cm.body().get("frames")).isNotEmpty();

        Res run = api.post(s + "/builds/1/twin/run", Json.obj().put("speed_scale", 1.5));
        assertThat(run.status()).as(String.valueOf(run.body())).isEqualTo(200);
        List<String> hit = new ArrayList<>();
        run.body().get("limits").forEach(l -> hit.add(l.get("limit").asString()));
        assertThat(hit).contains("speed");

        // snapshots, approval, pdf, package
        assertThat(api.post(s + "/builds/1/snapshots?view=iso", realPng()).status()).isEqualTo(200);
        JsonNode approved = api.job(s + "/builds/1/approve", null);
        assertThat(approved.get("status").asString()).as(approved.toString()).isEqualTo("done");
        Res pdf = api.get(s + "/builds/1/pdf");
        assertThat(pdf.status()).isEqualTo(200);
        assertThat(text(pdf)).startsWith("%PDF");
        Res pkg = api.get(s + "/builds/1/package");
        assertThat(pkg.status()).isEqualTo(200);
        List<String> entries = new ArrayList<>();
        try (ZipInputStream zip = new ZipInputStream(new ByteArrayInputStream(pkg.bytes()))) {
            for (ZipEntry e; (e = zip.getNextEntry()) != null; ) entries.add(e.getName());
        }
        assertThat(entries).contains("bom.csv", "assembly.step");

        // rebuild from b1 with j1 pinned to an alternative from b1's j1 choice
        JsonNode j1 = null;
        for (JsonNode m : b1.get("motors")) if (m.get("joint").asString().equals("j1")) j1 = m;
        assertThat(j1).as("a j1 motor choice").isNotNull();
        String chosen = j1.hasNonNull("alternative_id") ? j1.get("alternative_id").asString() : "";
        String alt = null;
        for (JsonNode a : j1.get("alternatives")) {
            String id = a.get("id").asString();
            if (alt == null || chosen.equals(alt)) alt = id;
        }
        assertThat(alt).as("an alternative for j1").isNotNull();
        ObjectNode rebuild = Json.obj().put("base_build", 1).put("request", "use the other motor on j1");
        rebuild.set("pinned", Json.obj().put("j1", alt));
        JsonNode second = api.job(s + "/builds", rebuild);
        assertThat(second.get("status").asString()).as(second.toString()).isEqualTo("done");
        JsonNode b2 = api.get(s + "/builds/2").body();
        assertThat(b2.get("parent_build").asInt()).isEqualTo(1);
        JsonNode pinned = null;
        for (JsonNode m : b2.get("motors")) if (m.get("joint").asString().equals("j1")) pinned = m;
        assertThat(pinned.get("pinned").asBoolean()).isTrue();
    }
}
