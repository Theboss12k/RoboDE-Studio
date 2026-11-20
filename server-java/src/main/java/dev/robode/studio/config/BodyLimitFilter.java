package dev.robode.studio.config;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

/** Requests that announce more than 64 MB are refused before anything is read (chunked uploads are capped when read). */
@Component
public class BodyLimitFilter extends OncePerRequestFilter {
    public static final long MAX_BODY = 64L * 1024 * 1024;

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        if (request.getContentLengthLong() > MAX_BODY) {
            response.setStatus(413);
            response.setContentType("application/json");
            response.getWriter().write("{\"detail\":\"The request body is too large (64 MB at most)\"}");
            return;
        }
        chain.doFilter(request, response);
    }
}
