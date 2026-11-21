package dev.robode.studio.pairing;

import dev.robode.studio.common.StudioException;
import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.FilterInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.Semaphore;
import java.util.concurrent.TimeUnit;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.springframework.http.HttpStatus;

/**
 * The only thing a phone can reach: a small HTTP/1.1 server on 0.0.0.0 with three routes, GET /p/{code},
 * POST /p/{code}/photo and POST /p/{code}/video. Anything else (including a wrong or expired code) is an empty 404.
 *
 * <p>It is written on plain sockets rather than on the JDK HttpServer because the JDK applies one request timeout to
 * every context, and a site video of up to 500 MB cannot finish in the 60 s that the photo route needs. Each connection
 * carries one request ({@code Connection: close}). A watchdog closes a connection that is still open at its deadline:
 * {@link #REQUEST_SECONDS} for the page and the photo, and {@link #VIDEO_SECONDS} once the video route is recognised.
 * Nothing here logs, and the code is never written anywhere.
 */
final class PairingListener {
    private static final Pattern PAGE = Pattern.compile("/p/([A-Za-z0-9_-]{43})");
    private static final Pattern PHOTO = Pattern.compile("/p/([A-Za-z0-9_-]{43})/photo");
    private static final Pattern VIDEO = Pattern.compile("/p/([A-Za-z0-9_-]{43})/video");
    private static final String CSP = "default-src 'none'; img-src blob: data:; media-src blob:; style-src 'unsafe-inline'; "
            + "script-src 'unsafe-inline'; connect-src 'self'; form-action 'none'";
    private static final String PLAIN = "text/plain; charset=utf-8";
    /** After an oversize body is refused, this much more is read and thrown away so the phone sees the answer. */
    private static final long DRAIN_LIMIT = 64L * 1024 * 1024;
    private static final int MAX_HEAD_BYTES = 16 * 1024;

    /** At most this many requests are handled at once; more get a 503. */
    static final int MAX_CONCURRENT = 8;
    /** Open connections, including those that have not sent a request yet; more are closed at once. */
    static final int MAX_OPEN = 32;
    /** Page and photo requests: 60 s (a 15 MB photo on slow Wi-Fi). The test configuration shortens it. */
    static final long REQUEST_SECONDS = Long.getLong("studio.pair-request-seconds", 60L);
    /**
     * The video route: 15 min. A 500 MB video in that time needs about 4.7 Mbit/s of sustained upload; a faster link
     * finishes sooner. Never shorter than the request limit.
     */
    static final long VIDEO_SECONDS = Math.max(REQUEST_SECONDS, 15L * 60);

    private final PairingService service;
    private final ServerSocket server;
    private final ExecutorService executor = Executors.newVirtualThreadPerTaskExecutor();
    private final ScheduledExecutorService watchdogs;
    private final Semaphore slots = new Semaphore(MAX_CONCURRENT);
    private final Semaphore open = new Semaphore(MAX_OPEN);

    PairingListener(PairingService service, int port) throws IOException {
        this.service = service;
        this.server = new ServerSocket();
        try {
            server.bind(new InetSocketAddress(InetAddress.getByName("0.0.0.0"), port), 16);
        } catch (IOException e) {
            server.close();
            throw e;
        }
        this.watchdogs = Executors.newSingleThreadScheduledExecutor(r -> {
            Thread t = new Thread(r, "pairing-watchdog");
            t.setDaemon(true);
            return t;
        });
        Thread.ofPlatform().name("pairing-accept").daemon(true).start(this::acceptLoop);
    }

    int port() { return server.getLocalPort(); }

    void stop() {
        watchdogs.shutdownNow();
        executor.shutdownNow();
        closeQuietly(server);
    }

    private void acceptLoop() {
        while (!server.isClosed()) {
            Socket socket;
            try {
                socket = server.accept();
            } catch (IOException e) {
                if (server.isClosed()) return;
                pause();
                continue;
            }
            if (!open.tryAcquire()) { closeQuietly(socket); continue; }
            try {
                executor.execute(() -> {
                    try {
                        serve(socket);
                    } finally {
                        open.release();
                    }
                });
            } catch (RejectedExecutionException e) {
                open.release();
                closeQuietly(socket);
            }
        }
    }

    private static void pause() {
        try {
            Thread.sleep(20);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
    }

    private void serve(Socket socket) {
        Deadline deadline = new Deadline(socket);
        boolean holding = false;
        try (socket) {
            OutputStream out = socket.getOutputStream();
            InputStream in = new BufferedInputStream(socket.getInputStream());
            try {
                Head head = readHead(in);
                if (head == null) return; // the phone closed before it sent anything
                // The cap is applied here, after the head is read, so only real requests hold a slot.
                if (!slots.tryAcquire()) {
                    send(out, 503, PLAIN, bytes("Busy, try again"), "Retry-After: 2\r\n");
                    return;
                }
                holding = true;
                route(head, in, out, deadline);
            } catch (Reject e) {
                send(out, e.status, PLAIN, bytes(e.getMessage()), "");
            } catch (StudioException e) {
                send(out, e.status(), PLAIN, bytes(e.getMessage() == null ? "Error" : e.getMessage()), "");
            } catch (RuntimeException e) {
                send(out, 500, PLAIN, bytes("Something went wrong"), "");
            }
        } catch (IOException e) {
            // the phone went away, or the watchdog closed the connection at its deadline
        } finally {
            deadline.cancel();
            if (holding) slots.release();
        }
    }

    private void route(Head head, InputStream in, OutputStream out, Deadline deadline) throws IOException {
        String path = head.path();
        boolean get = head.method().equals("GET");
        boolean post = head.method().equals("POST");
        Matcher page = PAGE.matcher(path);
        Matcher photo = PHOTO.matcher(path);
        Matcher video = VIDEO.matcher(path);
        if (get && page.matches()) {
            PairingService.Pairing p = service.find(page.group(1));
            if (p == null) { notFound(out); return; }
            send(out, 200, "text/html; charset=utf-8",
                    PhonePage.render(page.group(1), service.secondsLeft(p)).getBytes(StandardCharsets.UTF_8), "");
            return;
        }
        if (post && photo.matches()) {
            PairingService.Pairing p = service.find(photo.group(1));
            if (p == null) { notFound(out); return; } // unknown code: answer and close, never read its body
            expectContinue(head, out);
            InputStream body = bounded(in, head.length());
            byte[] bytes = readLimited(body, head.length());
            if (bytes == null) { text(out, 413, "The photo is too large (15 MB at most)"); return; }
            if (bytes.length == 0) { text(out, 400, "No photo was received"); return; }
            text(out, 200, service.accept(p, bytes));
            return;
        }
        if (post && video.matches()) {
            PairingService.Pairing p = service.find(video.group(1));
            if (p == null) { notFound(out); return; } // unknown code: answer and close, never read its body
            deadline.extend(VIDEO_SECONDS);
            expectContinue(head, out);
            InputStream body = bounded(in, head.length());
            String answer;
            try {
                answer = service.acceptVideo(p, body, head.length(), head.headers().get("content-type"));
            } catch (StudioException e) {
                if (e.status() == 413) drain(body); // the rest of an oversize video is discarded so the phone sees the answer
                throw e;
            }
            text(out, 200, answer);
            return;
        }
        notFound(out); // any other path: answer and close without reading a body
    }

    /** One request's line and headers; null when the phone closed before sending anything. */
    private static Head readHead(InputStream in) throws IOException {
        ByteArrayOutputStream buf = new ByteArrayOutputStream();
        int tail = 0;
        int b;
        while ((b = in.read()) >= 0) {
            buf.write(b);
            tail = (tail << 8) | b;
            if (tail == 0x0D0A0D0A) break; // CRLF CRLF ends the head
            if (buf.size() > MAX_HEAD_BYTES) throw new Reject(431, "The request headers are too large");
        }
        if (b < 0) {
            if (buf.size() == 0) return null;
            throw new Reject(400, "The request ended early");
        }
        String[] lines = buf.toString(StandardCharsets.ISO_8859_1).split("\r\n");
        String[] start = lines[0].split(" ");
        if (start.length != 3 || !start[2].startsWith("HTTP/1.")) throw new Reject(400, "Bad request line");
        Map<String, String> headers = new HashMap<>();
        for (int i = 1; i < lines.length; i++) {
            int colon = lines[i].indexOf(':');
            if (colon <= 0) throw new Reject(400, "Bad header line");
            headers.putIfAbsent(lines[i].substring(0, colon).strip().toLowerCase(Locale.ROOT), lines[i].substring(colon + 1).strip());
        }
        if (headers.containsKey("transfer-encoding")) throw new Reject(501, "Send the file with a Content-Length header");
        long length = -1;
        String declared = headers.get("content-length");
        if (declared != null) {
            try {
                length = Long.parseLong(declared);
            } catch (NumberFormatException e) {
                throw new Reject(400, "Invalid Content-Length");
            }
            if (length < 0) throw new Reject(400, "Invalid Content-Length");
        }
        String target = start[1];
        int query = target.indexOf('?');
        return new Head(start[0], query >= 0 ? target.substring(0, query) : target, headers, length);
    }

    /** At most 15 MB; null when the body is larger (the rest is read and discarded). */
    private static byte[] readLimited(InputStream body, long declared) throws IOException {
        if (declared > PairingService.MAX_BYTES) { drain(body); return null; }
        byte[] bytes = body.readNBytes(PairingService.MAX_BYTES + 1);
        if (bytes.length > PairingService.MAX_BYTES) { drain(body); return null; }
        return bytes;
    }

    /** The request body: exactly the declared length, or everything the phone sends when no length is declared. */
    private static InputStream bounded(InputStream source, long length) {
        if (length < 0) return source;
        return new FilterInputStream(source) {
            private long left = length;

            @Override
            public int read() throws IOException {
                if (left <= 0) return -1;
                int b = super.read();
                if (b >= 0) left--;
                return b;
            }

            @Override
            public int read(byte[] buffer, int off, int len) throws IOException {
                if (left <= 0) return -1;
                int n = super.read(buffer, off, (int) Math.min(len, left));
                if (n > 0) left -= n;
                return n;
            }
        };
    }

    private static void drain(Head head, InputStream in) {
        if (head.length() > 0) drain(bounded(in, head.length()));
    }

    private static void drain(InputStream body) {
        byte[] buf = new byte[64 * 1024];
        long total = 0;
        try {
            int n;
            while (total < DRAIN_LIMIT && (n = body.read(buf)) >= 0) total += n;
        } catch (IOException e) {
            // the phone went away
        }
    }

    private static void expectContinue(Head head, OutputStream out) throws IOException {
        if ("100-continue".equalsIgnoreCase(head.headers().get("expect"))) {
            out.write("HTTP/1.1 100 Continue\r\n\r\n".getBytes(StandardCharsets.ISO_8859_1));
            out.flush();
        }
    }

    private static void notFound(OutputStream out) throws IOException { send(out, 404, null, null, ""); }

    private static void text(OutputStream out, int status, String message) throws IOException {
        send(out, status, PLAIN, bytes(message == null ? "Error" : message), "");
    }

    private static byte[] bytes(String text) { return text.getBytes(StandardCharsets.UTF_8); }

    private static void send(OutputStream out, int status, String type, byte[] body, String extra) throws IOException {
        HttpStatus named = HttpStatus.resolve(status);
        StringBuilder head = new StringBuilder("HTTP/1.1 ").append(status).append(' ')
                .append(named == null ? "Error" : named.getReasonPhrase()).append("\r\n");
        head.append("Cache-Control: no-store\r\nReferrer-Policy: no-referrer\r\nX-Content-Type-Options: nosniff\r\n");
        head.append("Content-Security-Policy: ").append(CSP).append("\r\n").append(extra);
        int length = body == null ? 0 : body.length;
        if (length > 0) head.append("Content-Type: ").append(type).append("\r\n");
        head.append("Content-Length: ").append(length).append("\r\nConnection: close\r\n\r\n");
        out.write(head.toString().getBytes(StandardCharsets.ISO_8859_1));
        if (length > 0) out.write(body);
        out.flush();
    }

    private static void closeQuietly(Socket socket) {
        try {
            socket.close();
        } catch (IOException ignored) {
            // already closed
        }
    }

    private static void closeQuietly(ServerSocket socket) {
        try {
            socket.close();
        } catch (IOException ignored) {
            // already closed
        }
    }

    /** The request line and headers (names in lower case); length is -1 when no Content-Length was sent. */
    private record Head(String method, String path, Map<String, String> headers, long length) {}

    /** A request refused before routing: the status and the text the phone sees. */
    private static final class Reject extends RuntimeException {
        final int status;

        Reject(int status, String message) {
            super(message);
            this.status = status;
        }
    }

    /** Closes the connection when its time is up; {@link #extend} moves the deadline for a longer request. */
    private final class Deadline {
        private final Socket socket;
        private ScheduledFuture<?> pending;

        Deadline(Socket socket) {
            this.socket = socket;
            extend(REQUEST_SECONDS);
        }

        synchronized void extend(long seconds) {
            if (pending != null) pending.cancel(false);
            pending = watchdogs.schedule(() -> closeQuietly(socket), seconds, TimeUnit.SECONDS);
        }

        synchronized void cancel() {
            if (pending != null) pending.cancel(false);
        }
    }
}
