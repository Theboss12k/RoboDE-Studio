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

/** With STUDIO_TOKEN set, every /api/v1 call needs "Authorization: Bearer <token>" (CORS preflights excepted). */
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
        response.setStatus(401);
        response.setHeader("WWW-Authenticate", "Bearer");
        response.setContentType("application/json");
        response.getWriter().write("{\"detail\":\"A valid bearer token is required\"}");
    }
}
