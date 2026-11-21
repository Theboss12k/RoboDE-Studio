package dev.robode.studio.flow;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import dev.robode.studio.common.Json;
import dev.robode.studio.store.ProjectStore;
import java.awt.image.BufferedImage;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.file.Path;
import javax.imageio.ImageIO;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import tools.jackson.databind.node.ObjectNode;

/** Photo AR scenes through the API: upload (inspect_upload in Python), list, get, photo, placement, the limit, delete and ar_calibrate. */
@SpringBootTest
@AutoConfigureMockMvc
class ArScenesFlowTest {
    static final Path RUNTIME = FlowSupport.newRuntime("studio-ar");

    static {
        FlowSupport.writeScriptedProfiles(RUNTIME, "rules.json");
    }

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry registry) { FlowSupport.configure(registry, RUNTIME); }

    @Autowired MockMvc mvc;

    static byte[] png(int width, int height) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        ImageIO.write(new BufferedImage(width, height, BufferedImage.TYPE_INT_RGB), "png", out);
        return out.toByteArray();
    }

    @Test
    void sceneLifecycle() throws Exception {
        FlowSupport.Api api = new FlowSupport.Api(mvc);
        String pid = api.post("/projects", Json.obj().put("brief", "Pick a bottle from the table")).body().get("id").asString();
        String base = "/projects/" + pid + "/ar/scenes";
        byte[] photo = png(640, 480);

        // upload: 201 with the size read from the header; a non-image is refused
        var up = api.send("POST", base + "?filename=site.png", photo);
        assertThat(up.status()).as(String.valueOf(up.body())).isEqualTo(201);
        String sid = up.body().get("id").asString();
        assertThat(up.body().get("width").asInt()).isEqualTo(640);
        assertThat(up.body().get("height").asInt()).isEqualTo(480);
        assertThat(up.body().get("placement").get("yaw_deg").asDouble()).isEqualTo(0.0);
        assertThat(api.send("POST", base + "?filename=notes.txt", "hello".getBytes()).status()).isEqualTo(422);

        // list (newest first) and get
        assertThat(api.get(base).body().get(0).get("id").asString()).isEqualTo(sid);
        assertThat(api.get(base + "/" + sid).body().get("image").asString()).isEqualTo("photo.png");
        assertThat(api.get(base + "/s_0000000000").status()).isEqualTo(404);

        // photo bytes and content type
        assertThat(api.get(base + "/" + sid + "/photo").bytes()).isEqualTo(photo);
        mvc.perform(get("/api/v1" + base + "/" + sid + "/photo")).andExpect(status().isOk()).andExpect(content().contentType("image/png"));

        // placement: round trip; a value that is not a finite number is a 400
        var placed = api.send("PUT", base + "/" + sid + "/placement", Json.obj().put("x", 1.5).put("y", -0.25).put("yaw_deg", 90));
        assertThat(placed.status()).isEqualTo(200);
        assertThat(api.get(base + "/" + sid).body().get("placement").get("y").asDouble()).isEqualTo(-0.25);
        assertThat(api.send("PUT", base + "/" + sid + "/placement", Json.obj().put("x", "NaN")).status()).isEqualTo(400);

        // calibrate: ar_calibrate runs on the stored photo and writes reference and calibration
        ObjectNode reference = Json.obj();
        reference.set("corners_px", Json.parse("[[200,400],[480,400],[480,200],[200,200]]"));
        reference.set("size_m", Json.parse("[0.297,0.210]"));
        var calibrated = api.send("POST", base + "/" + sid + "/calibrate", Json.obj().set("reference", reference));
        assertThat(calibrated.status()).as(String.valueOf(calibrated.body())).isEqualTo(200);
        assertThat(calibrated.body().get("calibration").get("scale_source").asString()).isEqualTo("reference");
        assertThat(calibrated.body().get("reference").get("size_m").size()).isEqualTo(2);

        // the limit: 10 scenes, the 11th is refused
        for (int i = 2; i <= 10; i++) assertThat(api.send("POST", base + "?filename=site" + i + ".png", photo).status()).isEqualTo(201);
        assertThat(api.get(base).body().size()).isEqualTo(10);
        var eleventh = api.send("POST", base + "?filename=site11.png", photo);
        assertThat(eleventh.status()).isEqualTo(422);
        assertThat(eleventh.detail()).isEqualTo(ProjectStore.AR_LIMIT_MESSAGE);

        // delete: 204, then 404
        assertThat(api.send("DELETE", base + "/" + sid, null).status()).isEqualTo(204);
        assertThat(api.get(base + "/" + sid).status()).isEqualTo(404);
        assertThat(api.get(base).body().size()).isEqualTo(9);
    }
}
