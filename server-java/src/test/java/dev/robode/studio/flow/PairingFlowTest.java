package dev.robode.studio.flow;

import static org.assertj.core.api.Assertions.assertThat;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

import dev.robode.studio.common.Json;
import dev.robode.studio.pairing.AddressSource;
import dev.robode.studio.pairing.PairingService;
import dev.robode.studio.store.ProjectStore;
import java.awt.image.BufferedImage;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneId;
import java.util.List;
import javax.imageio.ImageIO;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.context.TestConfiguration;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Primary;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;

/** "Link a phone": the separate pairing listener (three routes, own port) and the desktop pairing routes. */
@SpringBootTest
@AutoConfigureMockMvc
class PairingFlowTest {
    static final Path RUNTIME = FlowSupport.newRuntime("studio-pair");

    static {
        FlowSupport.writeScriptedProfiles(RUNTIME, "rules.json");
    }

    static final class TestClock extends Clock {
        private volatile Instant now = Instant.now();

        void advance(Duration d) { now = now.plus(d); }

        @Override public ZoneId getZone() { return ZoneId.of("UTC"); }
        @Override public Clock withZone(ZoneId zone) { return this; }
        @Override public Instant instant() { return now; }
    }

    static final TestClock CLOCK = new TestClock();
    static volatile List<String> addresses = List.of("127.0.0.1");

    @TestConfiguration
    static class Config {
        @Bean @Primary Clock pairingTestClock() { return CLOCK; }

        @Bean @Primary AddressSource pairingTestAddresses() { return () -> addresses; }
    }

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry registry) {
        FlowSupport.configure(registry, RUNTIME);
        registry.add("studio.pair-port", () -> "0");
        registry.add("studio.scan-max-bytes", () -> String.valueOf(1024 * 1024)); // the phone video cap, small for the test
    }

    @Autowired MockMvc mvc;
    @Autowired ProjectStore store;
    @Autowired PairingService pairingService;

    final HttpClient http = HttpClient.newHttpClient();

    static byte[] png(int width, int height) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        ImageIO.write(new BufferedImage(width, height, BufferedImage.TYPE_INT_RGB), "png", out);
        return out.toByteArray();
    }

    HttpResponse<byte[]> call(String method, String url, byte[] body) throws Exception {
        HttpRequest.Builder b = HttpRequest.newBuilder(URI.create(url)).timeout(Duration.ofSeconds(60));
        b.method(method, body == null ? HttpRequest.BodyPublishers.noBody() : HttpRequest.BodyPublishers.ofByteArray(body));
        return http.send(b.build(), HttpResponse.BodyHandlers.ofByteArray());
    }

    @Test
    void phoneLinkLifecycle() throws Exception {
        FlowSupport.Api api = new FlowSupport.Api(mvc);
        String pid = api.post("/projects", Json.obj().put("brief", "Pick a bottle from the table")).body().get("id").asString();
        String base = "/projects/" + pid + "/ar/pairing";

        assertThat(api.get(base).status()).isEqualTo(404);

        // start: url, urls, qr_svg, limits
        var start = api.post(base, null);
        assertThat(start.status()).as(String.valueOf(start.body())).isEqualTo(200);
        String url = start.body().get("url").asString();
        assertThat(url).matches("http://127\\.0\\.0\\.1:\\d+/p/[A-Za-z0-9_-]{43}");
        assertThat(start.body().get("urls").get(0).asString()).isEqualTo(url);
        assertThat(start.body().get("qr_svg").asString()).startsWith("<svg").contains("<rect");
        assertThat(start.body().get("uploads").asInt()).isEqualTo(0);
        assertThat(start.body().get("max_uploads").asInt()).isEqualTo(20);
        assertThat(Instant.parse(start.body().get("expires_at").asString())).isAfter(CLOCK.instant().plus(Duration.ofMinutes(9)));

        var status = api.get(base);
        assertThat(status.status()).isEqualTo(200);
        assertThat(status.body().has("qr_svg")).isFalse();

        // qr for a listed address, 400 for anything else
        var qr = api.get(base + "/qr?url=" + java.net.URLEncoder.encode(url, "UTF-8"));
        assertThat(qr.status()).isEqualTo(200);
        assertThat(new String(qr.bytes())).startsWith("<svg");
        assertThat(api.get(base + "/qr?url=" + java.net.URLEncoder.encode("http://10.0.0.9:1/p/x", "UTF-8")).status()).isEqualTo(400);

        URI u = URI.create(url);
        String origin = "http://127.0.0.1:" + u.getPort();
        String path = u.getPath();
        String code = path.substring("/p/".length());

        // the page and its headers
        var page = call("GET", url, null);
        assertThat(page.statusCode()).isEqualTo(200);
        assertThat(page.headers().firstValue("Cache-Control")).hasValue("no-store");
        assertThat(page.headers().firstValue("Referrer-Policy")).hasValue("no-referrer");
        assertThat(page.headers().firstValue("X-Content-Type-Options")).hasValue("nosniff");
        assertThat(page.headers().firstValue("Content-Security-Policy")).hasValue("default-src 'none'; img-src blob: data:; media-src blob:; "
                + "style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; form-action 'none'");
        assertThat(page.headers().firstValue("Access-Control-Allow-Origin")).isEmpty();
        String html = new String(page.body());
        assertThat(html).contains("Take a photo", "Choose from gallery", "Record a site video", "Link expires in 10 min",
                "Lay a sheet of A4 or Letter paper flat on the floor, in view.");

        // nothing else is reachable: wrong code, root, the API, other methods
        String wrong = "A".repeat(43);
        assertThat(call("GET", origin + "/p/" + wrong, null).statusCode()).isEqualTo(404);
        assertThat(call("POST", origin + "/p/" + wrong + "/photo", png(8, 8)).statusCode()).isEqualTo(404);
        assertThat(call("GET", origin + "/", null).statusCode()).isEqualTo(404);
        assertThat(call("GET", origin + "/api/v1/projects", null).statusCode()).isEqualTo(404);
        assertThat(call("GET", origin + "/p/" + code + "/photo", null).statusCode()).isEqualTo(404);
        assertThat(call("POST", url, png(8, 8)).statusCode()).isEqualTo(404);
        var missing = call("GET", origin + "/p/" + wrong, null);
        assertThat(missing.body()).isEmpty();
        assertThat(missing.headers().firstValue("Cache-Control")).hasValue("no-store");

        // an upload becomes an AR scene and counts
        var up = call("POST", url + "/photo", png(640, 480));
        assertThat(up.statusCode()).as(new String(up.body())).isEqualTo(200);
        assertThat(store.listArScenes(pid)).hasSize(1);
        assertThat(api.get(base).body().get("uploads").asInt()).isEqualTo(1);

        // not an image: the existing validation answers, and the count does not move
        var bad = call("POST", url + "/photo", "hello".getBytes());
        assertThat(bad.statusCode()).isEqualTo(422);
        assertThat(new String(bad.body())).isNotBlank();
        assertThat(api.get(base).body().get("uploads").asInt()).isEqualTo(1);
        assertThat(store.listArScenes(pid)).hasSize(1);

        // more than 15 MB
        var big = call("POST", url + "/photo", new byte[15 * 1024 * 1024 + 1]);
        assertThat(big.statusCode()).isEqualTo(413);
        assertThat(store.listArScenes(pid)).hasSize(1);

        // a new pairing replaces the old code
        var second = api.post(base, null);
        assertThat(second.body().get("url").asString()).isNotEqualTo(url);
        assertThat(second.body().get("uploads").asInt()).isEqualTo(0);
        assertThat(call("GET", url, null).statusCode()).isEqualTo(404);
        String url2 = second.body().get("url").asString();
        assertThat(call("GET", url2, null).statusCode()).isEqualTo(200);

        // DELETE ends it
        assertThat(api.send("DELETE", base, null).status()).isEqualTo(204);
        assertThat(api.get(base).status()).isEqualTo(404);
        assertThat(call("GET", url2, null).statusCode()).isEqualTo(404);

        // an expired pairing stops answering
        String url3 = api.post(base, null).body().get("url").asString();
        assertThat(call("GET", url3, null).statusCode()).isEqualTo(200);
        CLOCK.advance(Duration.ofMinutes(11));
        assertThat(call("GET", url3, null).statusCode()).isEqualTo(404);
        assertThat(call("POST", url3 + "/photo", png(8, 8)).statusCode()).isEqualTo(404);
        assertThat(api.get(base).status()).isEqualTo(404);

        // the scheduled sweep (run by hand here) stops the listener once no pairing is active
        assertThat(pairingService.listenerPort()).isGreaterThan(0);
        pairingService.sweep();
        assertThat(pairingService.listenerPort()).isEqualTo(-1);
    }

    /** Starts a pairing and returns {host, port, code, project id}. */
    String[] startPairing() {
        FlowSupport.Api api = new FlowSupport.Api(mvc);
        String pid = api.post("/projects", Json.obj().put("brief", "Pick a bottle from the table")).body().get("id").asString();
        String url = api.post("/projects/" + pid + "/ar/pairing", null).body().get("url").asString();
        URI u = URI.create(url);
        return new String[] {"127.0.0.1", String.valueOf(u.getPort()), u.getPath().substring("/p/".length()), pid};
    }

    /** POSTs a raw body with a Content-Type (the phone's video upload). */
    HttpResponse<byte[]> upload(String url, byte[] body, String contentType) throws Exception {
        HttpRequest request = HttpRequest.newBuilder(URI.create(url)).timeout(Duration.ofSeconds(60))
                .header("Content-Type", contentType).POST(HttpRequest.BodyPublishers.ofByteArray(body)).build();
        return http.send(request, HttpResponse.BodyHandlers.ofByteArray());
    }

    /** A phone video of the wrong kind, a wrong code and a video over the cap: none is stored. */
    @Test
    void phoneVideoRefusals() throws Exception {
        String[] p = startPairing();
        String origin = "http://127.0.0.1:" + p[1];
        FlowSupport.Api api = new FlowSupport.Api(mvc);
        String scans = "/projects/" + p[3] + "/scans";

        assertThat(upload(origin + "/p/" + "A".repeat(43) + "/video", new byte[] {1, 2, 3}, "video/mp4").statusCode()).isEqualTo(404);
        assertThat(upload(origin + "/p/" + p[2] + "/video", new byte[] {1, 2, 3}, "text/plain").statusCode()).isEqualTo(422);
        var big = upload(origin + "/p/" + p[2] + "/video", new byte[1024 * 1024 + 1], "video/mp4");
        assertThat(big.statusCode()).isEqualTo(413);
        assertThat(new String(big.body())).contains("The video is too large");
        assertThat(api.get(scans).body().size()).isZero();
        assertThat(api.get("/projects/" + p[3] + "/ar/pairing").body().get("uploads").asInt()).isZero();
    }

    /** A phone video becomes a site scan and counts as one upload of the link (needs the scan_probe task). */
    @Test
    void phoneVideoStoresAScan() throws Exception {
        assumeTrue(FlowSupport.hasTask("scan_probe"), "scan_probe is not registered in server/studio/tasks/handlers.py yet");
        String[] p = startPairing();
        var sent = upload("http://127.0.0.1:" + p[1] + "/p/" + p[2] + "/video", Files.readAllBytes(FlowSupport.RESOURCES.resolve("scan/tiny.mp4")),
                "video/mp4");
        assertThat(sent.statusCode()).as(new String(sent.body())).isEqualTo(200);
        FlowSupport.Api api = new FlowSupport.Api(mvc);
        var scans = api.get("/projects/" + p[3] + "/scans").body();
        assertThat(scans.size()).isEqualTo(1);
        assertThat(scans.get(0).get("status").asString()).isEqualTo("uploaded");
        assertThat(scans.get(0).get("video").get("duration_s").asDouble()).isGreaterThan(0);
        assertThat(api.get("/projects/" + p[3] + "/ar/pairing").body().get("uploads").asInt()).isEqualTo(1);
    }

    /**
     * A device that sends half a request and goes quiet must not hold the connection open. The listener closes it when
     * the request time (studio.pair-request-seconds: 60 s by default, 4 s in the surefire configuration) has passed.
     */
    @Test
    void stalledHalfRequestIsClosedInBoundedTime() throws Exception {
        assertThat(System.getProperty("studio.pair-request-seconds")).isEqualTo("4");
        String[] p = startPairing();
        try (java.net.Socket s = new java.net.Socket(p[0], Integer.parseInt(p[1]))) {
            s.setSoTimeout(9000);
            s.getOutputStream().write(("POST /p/" + p[2] + "/photo HTTP/1.1\r\nHost: x\r\nContent-Length: 100\r\n").getBytes());
            s.getOutputStream().flush();
            long start = System.nanoTime();
            int outcome;
            try {
                outcome = s.getInputStream().read();
            } catch (java.net.SocketTimeoutException e) {
                outcome = -2; // still open after 9 s
            } catch (java.io.IOException e) {
                outcome = -1; // reset by the server
            }
            long seconds = Duration.ofNanos(System.nanoTime() - start).toSeconds();
            assertThat(outcome).as("the server closed the stalled connection").isEqualTo(-1);
            assertThat(seconds).isLessThan(9);
        }
    }

    /** At most 8 requests are handled at once; the ninth is answered 503 "Busy, try again", and service resumes after. */
    @Test
    void ninthConcurrentRequestIsBusy() throws Exception {
        String[] p = startPairing();
        int port = Integer.parseInt(p[1]);
        java.util.List<java.net.Socket> held = new java.util.ArrayList<>();
        try {
            for (int i = 0; i < 8; i++) {
                java.net.Socket s = new java.net.Socket(p[0], port);
                held.add(s);
                // Complete headers announce a body that never arrives: the handler waits for it and holds its slot.
                s.getOutputStream().write(("POST /p/" + p[2] + "/photo HTTP/1.1\r\nHost: x\r\nContent-Length: 100\r\n\r\n").getBytes());
                s.getOutputStream().flush();
            }
            HttpResponse<byte[]> busy = null;
            long end = System.nanoTime() + Duration.ofSeconds(3).toNanos();
            while (System.nanoTime() < end) { // the handlers start asynchronously: wait until all 8 hold a slot
                busy = call("GET", "http://127.0.0.1:" + port + "/p/" + p[2], null);
                if (busy.statusCode() == 503) break;
                Thread.sleep(50);
            }
            assertThat(busy.statusCode()).isEqualTo(503);
            assertThat(new String(busy.body())).isEqualTo("Busy, try again");
        } finally {
            for (java.net.Socket s : held) s.close();
        }
        long end = System.nanoTime() + Duration.ofSeconds(8).toNanos();
        int status = 0;
        while (System.nanoTime() < end) {
            status = call("GET", "http://127.0.0.1:" + port + "/p/" + p[2], null).statusCode();
            if (status == 200) break;
            Thread.sleep(100);
        }
        assertThat(status).as("slots are freed when the stalled clients leave").isEqualTo(200);
    }

    @Test
    void noNetworkIsA409() {
        FlowSupport.Api api = new FlowSupport.Api(mvc);
        String pid = api.post("/projects", Json.obj().put("brief", "Pick a bottle from the table")).body().get("id").asString();
        addresses = List.of();
        try {
            var r = api.post("/projects/" + pid + "/ar/pairing", null);
            assertThat(r.status()).isEqualTo(409);
            assertThat(r.detail()).isEqualTo("No Wi-Fi or network connection found for a phone to reach");
        } finally {
            addresses = List.of("127.0.0.1");
        }
    }
}
