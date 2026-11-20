package dev.robode.studio.api;

import dev.robode.studio.common.Json;
import dev.robode.studio.common.StudioException;
import dev.robode.studio.config.StudioProperties;
import dev.robode.studio.jobs.Job;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.HexFormat;
import java.util.List;
import java.util.Locale;
import java.util.function.Supplier;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import tools.jackson.databind.node.ObjectNode;

/**
 * X-Studio-Callback: when a job ends, POSTs its expanded final record to the caller's URL (signed with HMAC-SHA256 when
 * STUDIO_CALLBACK_SECRET is set). One attempt right away, then one retry after each configured delay (1 s, 5 s, 25 s).
 * A delivery problem is logged and shown in the record's callback block; it never changes the job.
 */
@Component
class CallbackService {
    private static final Logger LOG = LoggerFactory.getLogger(CallbackService.class);
    private final List<String> hosts;
    private final String secret;
    private final List<Duration> delays;
    private final HttpClient client = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5))
            .followRedirects(HttpClient.Redirect.NEVER).build();

    CallbackService(StudioProperties props) {
        this.hosts = props.callbackHosts().stream().map(CallbackService::bare).toList();
        this.secret = props.callbackSecret();
        this.delays = props.callbackRetryDelays();
    }

    private static String bare(String host) {
        String h = host.strip().toLowerCase(Locale.ROOT);
        return h.startsWith("[") && h.endsWith("]") ? h.substring(1, h.length() - 1) : h;
    }

    /** The URL when it is http(s) with an allowed host; 422 otherwise. */
    String validate(String url) {
        URI uri;
        try {
            uri = URI.create(url.strip());
        } catch (IllegalArgumentException e) {
            throw StudioException.value("X-Studio-Callback is not a valid URL");
        }
        String scheme = uri.getScheme() == null ? "" : uri.getScheme().toLowerCase(Locale.ROOT);
        if (!(scheme.equals("http") || scheme.equals("https")) || uri.getHost() == null)
            throw StudioException.value("X-Studio-Callback must be an http or https URL");
        if (!hosts.contains(bare(uri.getHost())))
            throw StudioException.value("The callback host " + bare(uri.getHost()) + " is not allowed (see STUDIO_CALLBACK_HOSTS)");
        return url.strip();
    }

    /** Remembers the callback on the job and delivers when it ends; a job that already has one keeps the first. */
    void register(Job job, String url, Supplier<ObjectNode> finalRecord) {
        if (!job.callbackUrl(url)) return;
        Thread.ofVirtual().name("callback-" + job.id()).start(() -> {
            job.ended().join();
            try {
                deliver(job, url, finalRecord.get());
            } catch (RuntimeException e) {
                LOG.warn("Callback for job {} failed: {}", job.id(), e.toString());
            }
        });
    }

    private void deliver(Job job, String url, ObjectNode record) {
        byte[] body = Json.MAPPER.writeValueAsBytes(record);
        for (int attempt = 0; attempt <= delays.size(); attempt++) {
            if (attempt > 0) {
                try {
                    Thread.sleep(delays.get(attempt - 1));
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    return;
                }
            }
            boolean ok = post(job, url, body);
            job.callbackResult(ok, attempt + 1);
            if (ok) return;
        }
        LOG.warn("Callback for job {} was not delivered after {} attempts", job.id(), delays.size() + 1);
    }

    private boolean post(Job job, String url, byte[] body) {
        try {
            HttpRequest.Builder request = HttpRequest.newBuilder(URI.create(url)).timeout(Duration.ofSeconds(15))
                    .header("Content-Type", "application/json").header("X-Studio-Job", job.id())
                    .header("X-Studio-Event", "job.finished");
            if (secret != null) request.header("X-Studio-Signature", "sha256=" + sign(body));
            HttpResponse<Void> response = client.send(request.POST(HttpRequest.BodyPublishers.ofByteArray(body)).build(),
                    HttpResponse.BodyHandlers.discarding());
            return response.statusCode() / 100 == 2;
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return false;
        } catch (Exception e) {
            LOG.debug("Callback post failed: {}", e.toString());
            return false;
        }
    }

    private String sign(byte[] body) throws Exception {
        Mac mac = Mac.getInstance("HmacSHA256");
        mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA256"));
        return HexFormat.of().formatHex(mac.doFinal(body));
    }
}
