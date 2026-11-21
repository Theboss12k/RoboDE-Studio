package dev.robode.studio.pairing;

import dev.robode.studio.api.ProjectService;
import dev.robode.studio.common.Json;
import dev.robode.studio.common.StudioException;
import dev.robode.studio.config.StudioProperties;
import jakarta.annotation.PreDestroy;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.stereotype.Service;
import tools.jackson.databind.node.ObjectNode;

/**
 * "Link a phone": short-lived pairing codes that let a phone on the same network add photos to one project.
 * The phone talks only to {@link PairingListener} (three routes, own port); the Spring app stays on loopback.
 * The code is never logged.
 */
@Service
public class PairingService {
    public static final Duration TTL = Duration.ofMinutes(10);
    public static final int MAX_UPLOADS = 20;
    public static final int MAX_BYTES = 15 * 1024 * 1024;
    private static final DateTimeFormatter STAMP = DateTimeFormatter.ofPattern("yyyyMMdd'T'HHmmss").withZone(ZoneOffset.UTC);

    /** One active pairing. `uploads` is guarded by the service lock. */
    public static final class Pairing {
        private final String pid;
        private final String code;
        private final byte[] codeBytes;
        private final Instant expires;
        private final List<String> urls;
        private int uploads;

        private Pairing(String pid, String code, Instant expires, List<String> urls) {
            this.pid = pid;
            this.code = code;
            this.codeBytes = code.getBytes(StandardCharsets.UTF_8);
            this.expires = expires;
            this.urls = urls;
        }

        public String pid() { return pid; }
        public Instant expires() { return expires; }
        public List<String> urls() { return urls; }
    }

    private final ProjectService projects;
    private final StudioProperties properties;
    private final Clock clock;
    private final AddressSource addresses;
    private final SecureRandom random = new SecureRandom();
    private final Map<String, Pairing> byProject = new HashMap<>();
    private PairingListener listener;
    private ScheduledExecutorService scheduler;
    private ScheduledFuture<?> sweeper;

    public PairingService(ProjectService projects, StudioProperties properties, ObjectProvider<Clock> clock,
                          ObjectProvider<AddressSource> addresses) {
        this.projects = projects;
        this.properties = properties;
        this.clock = clock.getIfAvailable(Clock::systemUTC);
        this.addresses = addresses.getIfAvailable(() -> AddressSource::discover);
    }

    // -- desktop side ---------------------------------------------------------------------------------------------
    /** Starts (or replaces) the pairing of a project; the caller has checked that the project exists. */
    public synchronized ObjectNode start(String pid) {
        List<String> ips = addresses.addresses();
        if (ips.isEmpty()) throw StudioException.conflict("No Wi-Fi or network connection found for a phone to reach");
        byProject.remove(pid); // the listener stays up: a new pairing is about to take its place
        prune();
        if (listener == null) {
            try {
                listener = new PairingListener(this, properties.pairPort());
            } catch (IOException e) {
                throw StudioException.conflict("The phone link port " + properties.pairPort()
                        + " is not available. Set STUDIO_PAIR_PORT to a free port.");
            }
        }
        byte[] raw = new byte[32];
        random.nextBytes(raw);
        String code = Base64.getUrlEncoder().withoutPadding().encodeToString(raw);
        int port = listener.port();
        List<String> urls = new ArrayList<>();
        for (String ip : ips) urls.add("http://" + ip + ":" + port + "/p/" + code);
        Pairing p = new Pairing(pid, code, clock.instant().plus(TTL), List.copyOf(urls));
        byProject.put(pid, p);
        if (sweeper == null) {
            if (scheduler == null) scheduler = Executors.newSingleThreadScheduledExecutor(r -> {
                Thread t = new Thread(r, "pairing-sweep");
                t.setDaemon(true);
                return t;
            });
            sweeper = scheduler.scheduleWithFixedDelay(this::sweep, 30, 30, TimeUnit.SECONDS);
        }
        return view(p, true);
    }

    /** The active pairing of a project, or null. */
    public synchronized ObjectNode status(String pid) {
        prune();
        Pairing p = byProject.get(pid);
        return p == null ? null : view(p, false);
    }

    public synchronized void end(String pid) {
        byProject.remove(pid); // the scheduled sweep stops the listener once nothing is left
    }

    /** The SVG for one of the pairing's own addresses; anything else is a 400. */
    public synchronized String qr(String pid, String url) {
        prune();
        Pairing p = byProject.get(pid);
        if (p == null) throw StudioException.notFound("No phone link is active");
        if (url == null || !p.urls.contains(url)) throw new StudioException(400, "That address is not one of this link's addresses", null);
        return QrSvg.of(url);
    }

    private ObjectNode view(Pairing p, boolean withQr) {
        ObjectNode out = Json.obj().put("url", p.urls.get(0));
        var arr = out.putArray("urls");
        p.urls.forEach(arr::add);
        if (withQr) out.put("qr_svg", QrSvg.of(p.urls.get(0)));
        return out.put("expires_at", p.expires.toString()).put("uploads", p.uploads).put("max_uploads", MAX_UPLOADS);
    }

    // -- phone side (called by the listener) ----------------------------------------------------------------------
    /** The live pairing for a code, compared in constant time over all pairings; null for anything unknown or expired. */
    synchronized Pairing find(String code) {
        byte[] given = code.getBytes(StandardCharsets.UTF_8);
        Pairing hit = null;
        for (Pairing p : byProject.values()) if (MessageDigest.isEqual(p.codeBytes, given)) hit = p;
        return hit != null && clock.instant().isBefore(hit.expires) ? hit : null;
    }

    synchronized long secondsLeft(Pairing p) {
        return Math.max(0, Duration.between(clock.instant(), p.expires).getSeconds());
    }

    /** Stores a photo through the normal AR upload (so inspect_upload and the scene limit apply). */
    String accept(Pairing p, byte[] bytes) {
        synchronized (this) {
            if (byProject.get(p.pid) != p || !clock.instant().isBefore(p.expires))
                throw StudioException.notFound("This link has expired");
            if (p.uploads >= MAX_UPLOADS) throw new StudioException(429, "This link has reached its limit of " + MAX_UPLOADS + " photos", null);
            p.uploads++; // reserve the slot so parallel uploads cannot pass the limit
        }
        try {
            projects.uploadArScene(p.pid, "phone-" + STAMP.format(clock.instant()) + ".jpg", bytes);
        } catch (RuntimeException e) {
            synchronized (this) { p.uploads--; }
            throw e;
        }
        return "Sent";
    }

    /**
     * A phone video becomes a site scan through ProjectService (streamed to a staging folder, probed, then stored). It counts
     * as one upload of the link, like a photo. `declared` is the Content-Length, or -1 when unknown.
     */
    String acceptVideo(Pairing p, InputStream body, long declared, String contentType) {
        String name = videoName(contentType, clock.instant());
        synchronized (this) {
            if (byProject.get(p.pid) != p || !clock.instant().isBefore(p.expires))
                throw StudioException.notFound("This link has expired");
            if (p.uploads >= MAX_UPLOADS) throw new StudioException(429, "This link has reached its limit of " + MAX_UPLOADS + " uploads", null);
            p.uploads++;
        }
        try {
            projects.uploadScan(p.pid, name, projects.stageScan(p.pid, name, body, declared));
        } catch (RuntimeException e) {
            synchronized (this) { p.uploads--; }
            throw e;
        }
        return "Sent";
    }

    /** The stored name of a phone video, from its Content-Type (MP4, MOV or WebM); any other type is a 422. */
    static String videoName(String contentType, Instant now) {
        String type = contentType == null ? "" : contentType.split(";")[0].strip().toLowerCase(Locale.ROOT);
        String ext = switch (type) {
            case "video/mp4" -> "mp4";
            case "video/quicktime" -> "mov";
            case "video/webm" -> "webm";
            default -> null;
        };
        if (ext == null) throw new StudioException(422, "The video must be an MP4, MOV or WebM file", null);
        return "phone-" + STAMP.format(now) + "." + ext;
    }

    // -- lifecycle ------------------------------------------------------------------------------------------------
    /** Drops expired pairings and stops the listener when none is left. Runs every 30 s while a pairing exists. */
    public synchronized void sweep() { sweepLocked(); }

    private void prune() {
        Instant now = clock.instant();
        byProject.values().removeIf(p -> !now.isBefore(p.expires));
    }

    private void sweepLocked() {
        prune();
        if (!byProject.isEmpty()) return;
        if (listener != null) {
            listener.stop();
            listener = null;
        }
        if (sweeper != null) {
            sweeper.cancel(false);
            sweeper = null;
        }
    }

    /** The port of the running listener, or -1 when none (tests). */
    public synchronized int listenerPort() { return listener == null ? -1 : listener.port(); }

    @PreDestroy
    synchronized void shutdown() {
        byProject.clear();
        sweepLocked();
        if (scheduler != null) scheduler.shutdownNow();
    }
}
