package dev.robode.studio.config;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.regex.Pattern;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * Requests that announce more than 64 MB are refused before anything is read (chunked uploads are capped when read).
 * The one exception is the site-scan upload, POST /api/v1/projects/{pid}/scans, which streams to disk and enforces its
 * own cap (studio.scan-max-bytes, 500 MB) on both the declared length and the streamed count.
 */
@Component
@org.springframework.core.annotation.Order(20)
public class BodyLimitFilter extends OncePerRequestFilter {
    public static final long MAX_BODY = 64L * 1024 * 1024;
    private static final Pattern SCAN_UPLOAD = Pattern.compile("^/api/v1/projects/[^/]+/scans$");

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        if (request.getContentLengthLong() > MAX_BODY && !isScanUpload(request)) {
            response.setStatus(413);
            response.setContentType("application/json");
            response.getWriter().write("{\"detail\":\"The request body is too large (64 MB at most)\"}");
            return;
        }
        chain.doFilter(request, response);
    }

    /** Exactly POST /api/v1/projects/{pid}/scans, with no trailing segments. */
    static boolean isScanUpload(HttpServletRequest request) {
        return "POST".equals(request.getMethod()) && SCAN_UPLOAD.matcher(request.getRequestURI()).matches();
    }
}
