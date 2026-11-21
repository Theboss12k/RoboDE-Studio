package dev.robode.studio.flow;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assumptions.assumeTrue;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;

import dev.robode.studio.common.Json;
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
import tools.jackson.databind.node.ObjectNode;

/**
 * Site scans (video AR) through the API. The scan folders are made by the store from the tiny fixture video (scan/tiny.mp4),
 * so the limits, list, video, frames, placement and delete do not need the Python probe. Only the HTTP upload, which runs
 * scan_probe, is skipped while that task is not registered. Reconstruct only runs on a clip too short to reconstruct, so
 * the failure path is what is tested; a real reconstruction needs the heavy vision backend and is not run here. Scale
 * (distance method) and floor are pure calculations and run on a stand-in reconstructed scan.
 */
@SpringBootTest
@AutoConfigureMockMvc
class ScanFlowTest {
    static final Path RUNTIME = FlowSupport.newRuntime("studio-scan");
    static final Path TINY = FlowSupport.RESOURCES.resolve("scan/tiny.mp4");
    static final long MB = 1024L * 1024;

    static {
        FlowSupport.writeScriptedProfiles(RUNTIME, "rules.json");
    }

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry registry) {
        FlowSupport.configure(registry, RUNTIME);
        registry.add("studio.scan-max-bytes", () -> String.valueOf(MB));
    }

    @Autowired MockMvc mvc;
    @Autowired ProjectStore store;

    private FlowSupport.Api api() { return new FlowSupport.Api(mvc); }


    private String project() {
        return api().post("/projects", Json.obj().put("brief", "Walk the site with a phone")).body().get("id").asString();
    }

    /** A scan folder made the way uploadScan makes it, with a hand-written probe result instead of the Python probe. */
    private String storedScan(String pid) throws IOException {
        Path copy = Files.createTempDirectory("scan-video").resolve("walk.mp4");
        Files.copy(TINY, copy);
        ObjectNode probe = Json.obj().put("duration_s", 1.0).put("width", 64).put("height", 64).put("fps", 10.0).put("rotation_deg", 0);
        return store.createScan(pid, copy, "mp4", probe, Files.size(TINY)).get("id").asString();
    }

    @Test
    void uploadLimitsAreEnforcedAndTheBodyFilterStillCoversOtherRoutes() throws Exception {
        String pid = project();
        String base = "/projects/" + pid + "/scans";
        // a declared length over the cap is refused by the route itself (the body filter exempts this POST); the mock
        // derives Content-Length from the body, so the body really is 65 MB
        var declared = mvc.perform(post("/api/v1" + base + "?filename=walk.mp4").content(new byte[(int) (65 * MB)]))
                .andReturn().getResponse();
        assertThat(declared.getStatus()).as(declared.getContentAsString()).isEqualTo(413);
        assertThat(new String(declared.getContentAsByteArray())).contains("The video is too large (1 MB at most)");
        // a body over the cap
        assertThat(api().send("POST", base + "?filename=walk.mp4", new byte[(int) MB + 1]).status()).isEqualTo(413);
        // not a video file
        assertThat(api().send("POST", base + "?filename=notes.txt", Files.readAllBytes(TINY)).status()).isEqualTo(422);
        // every other route keeps the 64 MB limit: a declared 65 MB reference upload
        var reference = mvc.perform(post("/api/v1/projects/" + pid + "/reference/files?filename=a.pdf")
                .content(new byte[(int) (65 * MB)])).andReturn().getResponse();
        assertThat(reference.getStatus()).isEqualTo(413);
        assertThat(new String(reference.getContentAsByteArray())).contains("64 MB");
        assertThat(api().get(base).body().size()).isZero();
    }

    @Test
    void sixthScanIsRefusedBeforeTheProbeRuns() throws IOException {
        String pid = project();
        for (int i = 0; i < 5; i++) storedScan(pid);
        var r = api().send("POST", "/projects/" + pid + "/scans?filename=walk.mp4", Files.readAllBytes(TINY));
        assertThat(r.status()).isEqualTo(422);
        assertThat(r.detail()).isEqualTo("At most 5 site scans per project");
        assertThat(api().get("/projects/" + pid + "/scans").body().size()).isEqualTo(5);
    }

    @Test
    void listGetVideoFramesAndMesh() throws Exception {
        String pid = project();
        String base = "/projects/" + pid + "/scans";
        String first = storedScan(pid);
        Thread.sleep(5); // distinct creation times, so the order is meaningful
        String second = storedScan(pid);

        var list = api().get(base).body();
        assertThat(list.size()).isEqualTo(2);
        assertThat(list.get(0).get("id").asString()).isEqualTo(second); // newest first

        var scan = api().get(base + "/" + second);
        assertThat(scan.status()).isEqualTo(200);
        assertThat(scan.body().get("status").asString()).isEqualTo("uploaded");
        assertThat(scan.body().get("video").get("file").asString()).isEqualTo("video.mp4");
        assertThat(scan.body().get("video").get("duration_s").asDouble()).isEqualTo(1.0);
        assertThat(scan.body().get("placement").get("x").asDouble()).isEqualTo(0.0);
        assertThat(scan.body().get("frames").size()).isZero();
        assertThat(scan.body().has("schemaVersion")).isFalse();
        assertThat(api().get(base + "/sc_0000000000").status()).isEqualTo(404);
        assertThat(api().get(base + "/..%2Fx").status()).isEqualTo(404);

        // the video in full, then a Range request: 206 with exactly the 100 bytes asked for
        String video = base + "/" + second + "/video";
        assertThat(api().get(video).bytes()).hasSize((int) Files.size(TINY));
        var range = mvc.perform(get("/api/v1" + video).header("Range", "bytes=0-99")).andReturn().getResponse();
        assertThat(range.getStatus()).isEqualTo(206);
        assertThat(range.getContentAsByteArray()).hasSize(100);
        assertThat(range.getContentType()).isEqualTo("video/mp4");

        // mesh and frames are 404 until the reconstruction has written them; then they stream with their types
        assertThat(api().get(base + "/" + second + "/mesh").status()).isEqualTo(404);
        assertThat(api().get(base + "/" + second + "/frames/0").status()).isEqualTo(404);
        assertThat(api().get(base + "/" + second + "/frames/abc").status()).isEqualTo(404);
        assertThat(api().get(base + "/" + second + "/frames/-1").status()).isEqualTo(404);
        Path folder = store.dir(pid).resolve("scans").resolve(second);
        Files.write(folder.resolve("frames/0000.jpg"), new byte[] {(byte) 0xFF, (byte) 0xD8, 1, 2});
        Files.write(folder.resolve("scan.glb"), "glTF".getBytes());
        var frame = mvc.perform(get("/api/v1" + base + "/" + second + "/frames/0")).andReturn().getResponse();
        assertThat(frame.getStatus()).isEqualTo(200);
        assertThat(frame.getContentType()).isEqualTo("image/jpeg");
        var mesh = mvc.perform(get("/api/v1" + base + "/" + second + "/mesh")).andReturn().getResponse();
        assertThat(mesh.getContentType()).isEqualTo("model/gltf-binary");
        assertThat(mesh.getContentAsString()).isEqualTo("glTF");

        // the other scan is unaffected
        assertThat(api().get(base + "/" + first).status()).isEqualTo(200);
    }

    @Test
    void placementRoundTripThenDelete() throws Exception {
        String pid = project();
        String base = "/projects/" + pid + "/scans";
        String sid = storedScan(pid);

        var placed = api().send("PUT", base + "/" + sid + "/placement", Json.obj().put("x", 1.5).put("y", -0.25).put("yaw_deg", 90));
        assertThat(placed.status()).isEqualTo(200);
        assertThat(placed.body().get("placement").get("yaw_deg").asDouble()).isEqualTo(90.0);
        assertThat(api().get(base + "/" + sid).body().get("placement").get("y").asDouble()).isEqualTo(-0.25);
        assertThat(api().send("PUT", base + "/" + sid + "/placement", Json.obj().put("x", "NaN")).status()).isEqualTo(400);
        assertThat(api().send("PUT", base + "/sc_0000000000/placement", Json.obj()).status()).isEqualTo(404);

        assertThat(api().send("DELETE", base + "/" + sid, null).status()).isEqualTo(204);
        assertThat(api().get(base + "/" + sid).status()).isEqualTo(404);
        assertThat(api().get(base).body().size()).isZero();
    }

    @Test
    void scaleAndFloorNeedAReconstructedScan() throws IOException {
        String pid = project();
        String sid = storedScan(pid);
        String base = "/projects/" + pid + "/scans/" + sid;
        var scale = api().send("POST", base + "/scale", Json.obj().put("method", "distance").put("distance_m", 2.0));
        assertThat(scale.status()).isEqualTo(409);
        assertThat(scale.detail()).isEqualTo("Reconstruct this site scan first");
        var floor = api().send("PUT", base + "/floor", Json.obj().set("points", Json.parse("[[0,0,0],[1,0,0],[0,1,0]]")));
        assertThat(floor.status()).isEqualTo(409);
    }

    @Test
    void applySceneIsRefusedOnAnApprovedDesignLikeRevise() {
        String pid = project();
        store.update(pid, m -> m.put("approved_version", 1));
        var refused = api().send("POST", "/projects/" + pid + "/apply-scene", Json.obj().set("scene", Json.obj()));
        assertThat(refused.status()).isEqualTo(409);
        assertThat(refused.detail()).isEqualTo("This design is approved (version 1) and frozen; changes are disabled");
        assertThat(api().send("POST", "/projects/" + pid + "/apply-scene", Json.obj()).status()).isEqualTo(422);
    }

    /** Marks a stored scan as reconstructed with one registered camera (identity pose); the reconstruction itself is not run. */
    private void pretendReconstructed(String pid, String sid) {
        store.updateScan(pid, sid, s -> {
            s.put("status", "reconstructed");
            s.set("frames", Json.parse("[{\"index\":0,\"t_s\":0.0,\"image\":\"frames/0000.jpg\","
                    + "\"world_from_camera\":[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]}]"));
        });
    }

    @Test
    void scaleFromADistanceAndTheFloorFromPoints() throws IOException {
        assumeTrue(FlowSupport.hasTask("scan_scale") && FlowSupport.hasTask("scan_floor"),
                "scan_scale and scan_floor are not registered in server/studio/tasks/handlers.py yet");
        String pid = project();
        String sid = storedScan(pid);
        pretendReconstructed(pid, sid);
        String base = "/projects/" + pid + "/scans/" + sid;

        // two points 2 m apart in R, measured as 1 m: the factor is 0.5 (true over measured)
        var scale = api().send("POST", base + "/scale", Json.obj().put("method", "distance").put("distance_m", 1.0)
                .set("p1", Json.parse("[0,0,0]")).set("p2", Json.parse("[0,0,2]")));
        assertThat(scale.status()).as(String.valueOf(scale.body())).isEqualTo(200);
        assertThat(scale.body().get("scale").get("factor").asDouble()).isCloseTo(0.5, org.assertj.core.api.Assertions.within(1e-9));
        assertThat(api().get(base).body().get("scale").get("method").asString()).isEqualTo("distance");

        // four clicked points on the plane y = 0: the floor and a 4x4 alignment come back and are stored
        var floor = api().send("PUT", base + "/floor",
                Json.obj().set("points", Json.parse("[[0,0,0],[1,0,0],[0,0,1],[1,0,1]]")));
        assertThat(floor.status()).as(String.valueOf(floor.body())).isEqualTo(200);
        assertThat(floor.body().get("floor").get("normal").size()).isEqualTo(3);
        assertThat(floor.body().get("alignment").size()).isEqualTo(4);
        assertThat(api().get(base).body().get("floor").get("normal").size()).isEqualTo(3);
    }

    @Test
    void reconstructFailsCleanlyOnAClipTooShortToReconstruct() throws IOException {
        assumeTrue(FlowSupport.hasTask("scan_reconstruct"), "scan_reconstruct is not registered in server/studio/tasks/handlers.py yet");
        String pid = project();
        String sid = storedScan(pid);
        String base = "/projects/" + pid + "/scans/" + sid;

        var started = api().post(base + "/reconstruct", null);
        assertThat(started.status()).as(String.valueOf(started.body())).isEqualTo(200);
        var job = api().finish(started.body());
        assertThat(job.get("kind").asString()).isEqualTo("scan_reconstruct");
        assertThat(job.get("status").asString()).isEqualTo("error");
        // the scan records the failure with the job's own text
        var scan = api().get(base).body();
        assertThat(scan.get("status").asString()).isEqualTo("failed");
        assertThat(scan.get("error").asString()).isNotBlank().isEqualTo(job.get("error").asString());
    }

    @Test
    void uploadOverHttpStoresAScan() throws IOException {
        assumeTrue(FlowSupport.hasTask("scan_probe"), "scan_probe is not registered in server/studio/tasks/handlers.py yet");
        String pid = project();
        var r = api().send("POST", "/projects/" + pid + "/scans?filename=walk.mp4", Files.readAllBytes(TINY));
        assertThat(r.status()).as(String.valueOf(r.body())).isEqualTo(201);
        assertThat(r.body().get("status").asString()).isEqualTo("uploaded");
        assertThat(r.body().get("video").get("duration_s").asDouble()).isGreaterThan(0);
        assertThat(r.body().get("video").get("bytes").asLong()).isEqualTo(Files.size(TINY));
    }
}
