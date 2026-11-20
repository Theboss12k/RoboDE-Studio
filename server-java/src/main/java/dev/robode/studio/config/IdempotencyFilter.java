package dev.robode.studio.config;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ReadListener;
import jakarta.servlet.ServletException;
import jakarta.servlet.ServletInputStream;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletRequestWrapper;
import jakarta.servlet.http.HttpServletResponse;
import java.io.BufferedReader;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.Charset;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.Duration;
import java.util.Arrays;
import java.util.HexFormat;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.regex.Pattern;
import org.springframework.core.annotation.Order;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import org.springframework.web.util.ContentCachingResponseWrapper;

/**
 * Idempotency-Key (1 to 200 characters) on POST /api/v1/projects and the job-starting POSTs. Keys live in memory for 24 h,
 * keyed by (key, method, path). The same key with the same body replays the first response (status, body, Location,
 * content type); with another body it is a 422. Server errors (5xx) are not remembered, so a retry runs again.
 */
@Component
@Order(30)
public class IdempotencyFilter extends OncePerRequestFilter {
    static final Duration TTL = Duration.ofHours(24);
    private static final int MAX_BODY = 4 * 1024 * 1024;
    private static final Pattern STARTS = Pattern.compile(
            "^/api/v1/(projects|projects/[^/]+/(design|revise|fleet/design|search|studio/builds|studio/builds/[0-9]+/approve)|projects/[^/]+/candidates/[^/]+/analyse|llm/profiles/[^/]+/test)$");

    private record Stored(int status, String contentType, String location, byte[] body) {}

    private static final class Entry {
        final String bodyHash;
        final long created = System.nanoTime();
        final CompletableFuture<Stored> outcome = new CompletableFuture<>();

        Entry(String bodyHash) { this.bodyHash = bodyHash; }

        boolean expired() { return System.nanoTime() - created > TTL.toNanos(); }
    }

    private final Map<String, Entry> entries = new ConcurrentHashMap<>();

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        return !"POST".equals(request.getMethod()) || request.getHeader("Idempotency-Key") == null
                || !STARTS.matcher(request.getRequestURI()).matches();
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String key = request.getHeader("Idempotency-Key");
        if (key.isEmpty() || key.length() > 200) {
            fail(response, 422, "Idempotency-Key must be 1 to 200 characters");
            return;
        }
        byte[] body = request.getInputStream().readNBytes(MAX_BODY + 1);
        if (body.length > MAX_BODY) {
            fail(response, 413, "The request body is too large");
            return;
        }
        String hash = sha256(body);
        String mapKey = key + "\n" + request.getMethod() + "\n" + request.getRequestURI();
        purgeExpired();
        while (true) {
            Entry mine = new Entry(hash);
            Entry existing = entries.putIfAbsent(mapKey, mine);
            if (existing != null && existing.expired()) {
                entries.remove(mapKey, existing);
                continue;
            }
            if (existing == null) {
                run(request, response, chain, body, mapKey, mine);
                return;
            }
            if (!existing.bodyHash.equals(hash)) {
                fail(response, 422, "Idempotency-Key reused with a different request");
                return;
            }
            Stored stored = existing.outcome.join(); // a concurrent first request is still running
            if (stored == null) continue; // it failed with a server error: this one runs afresh
            replay(response, stored);
            return;
        }
    }

    private void run(HttpServletRequest request, HttpServletResponse response, FilterChain chain, byte[] body, String mapKey, Entry mine)
            throws IOException, ServletException {
        ContentCachingResponseWrapper wrapped = new ContentCachingResponseWrapper(response);
        try {
            chain.doFilter(new CachedBody(request, body), wrapped);
        } catch (IOException | ServletException | RuntimeException e) {
            entries.remove(mapKey, mine);
            mine.outcome.complete(null);
            throw e;
        }
        int status = wrapped.getStatus();
        if (status >= 500) {
            entries.remove(mapKey, mine);
            mine.outcome.complete(null);
        } else {
            mine.outcome.complete(new Stored(status, wrapped.getContentType(), wrapped.getHeader("Location"), wrapped.getContentAsByteArray()));
        }
        wrapped.copyBodyToResponse();
    }

    private static void replay(HttpServletResponse response, Stored stored) throws IOException {
        response.setStatus(stored.status());
        if (stored.contentType() != null) response.setContentType(stored.contentType());
        if (stored.location() != null) response.setHeader("Location", stored.location());
        response.setContentLength(stored.body().length);
        response.getOutputStream().write(stored.body());
    }

    private void purgeExpired() { entries.values().removeIf(Entry::expired); }

    private static void fail(HttpServletResponse response, int status, String detail) throws IOException {
        response.setStatus(status);
        response.setContentType("application/json");
        response.getWriter().write("{\"detail\":\"" + detail + "\"}");
    }

    private static String sha256(byte[] body) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(body));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    /** The request with its body already read (so the controller can read it again). */
    private static final class CachedBody extends HttpServletRequestWrapper {
        private final byte[] body;

        CachedBody(HttpServletRequest request, byte[] body) {
            super(request);
            this.body = Arrays.copyOf(body, body.length);
        }

        @Override
        public ServletInputStream getInputStream() {
            ByteArrayInputStream in = new ByteArrayInputStream(body);
            return new ServletInputStream() {
                @Override public int read() { return in.read(); }
                @Override public int read(byte[] b, int off, int len) { return in.read(b, off, len); }
                @Override public boolean isFinished() { return in.available() == 0; }
                @Override public boolean isReady() { return true; }
                @Override public void setReadListener(ReadListener listener) { throw new UnsupportedOperationException(); }
            };
        }

        @Override
        public BufferedReader getReader() {
            String enc = getCharacterEncoding();
            return new BufferedReader(new InputStreamReader(getInputStream(), enc == null ? StandardCharsets.UTF_8 : Charset.forName(enc)));
        }

        @Override
        public int getContentLength() { return body.length; }

        @Override
        public long getContentLengthLong() { return body.length; }
    }
}
