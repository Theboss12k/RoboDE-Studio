package dev.robode.studio.config;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/**
 * With STUDIO_TOKEN set, every /api/v1 call needs "Authorization: Bearer <token>" (CORS preflights excepted). The
 * same token in the `studio_token` cookie is accepted too, so the browser app's plain image, mesh and download links
 * (which cannot carry a header) work behind a token, for example on Cloud Run.
 */
@Component
@org.springframework.core.annotation.Order(10)
public class BearerTokenFilter extends OncePerRequestFilter {
    private final byte[] token;

    public BearerTokenFilter(StudioProperties properties) {
        this.token = properties.token() == null ? null : properties.token().getBytes(StandardCharsets.UTF_8);
    }

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        String path = request.getRequestURI();
        return token == null || !(path.equals("/api/v1") || path.startsWith("/api/v1/")) || "OPTIONS".equals(request.getMethod());
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String header = request.getHeader("Authorization");
        if (header != null && header.regionMatches(true, 0, "Bearer ", 0, 7)
                && MessageDigest.isEqual(header.substring(7).strip().getBytes(StandardCharsets.UTF_8), token)) {
            chain.doFilter(request, response);
            return;
        }
        // Cookie only for reads (plain links are GETs); writes always need the header, so a forged cross-site
        // request cannot change anything even where SameSite=Strict is not enforced.
        boolean read = "GET".equals(request.getMethod()) || "HEAD".equals(request.getMethod());
        if (header == null && read && request.getCookies() != null) {
            for (jakarta.servlet.http.Cookie cookie : request.getCookies()) {
                if ("studio_token".equals(cookie.getName()) && cookie.getValue() != null
                        && MessageDigest.isEqual(cookie.getValue().getBytes(StandardCharsets.UTF_8), token)) {
                    chain.doFilter(request, response);
                    return;
                }
            }
        }
        response.setStatus(401);
        response.setHeader("WWW-Authenticate", "Bearer");
        response.setContentType("application/json");
        response.getWriter().write("{\"detail\":\"A valid bearer token is required\"}");
    }
}
