package dev.robode.studio.api;

import dev.robode.studio.common.StudioException;
import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import org.springframework.core.io.ClassPathResource;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

/** Serves the bundled OpenAPI spec (contracts/openapi.bundled.json, copied into the jar at build time). */
@RestController
class OpenApiController {
    private static final String RESOURCE = "openapi.bundled.json";
    private volatile byte[] spec;

    @GetMapping("/api/v1/openapi.json")
    ResponseEntity<byte[]> openApi() {
        return ResponseEntity.ok().contentType(MediaType.APPLICATION_JSON).body(spec());
    }

    private byte[] spec() {
        byte[] cached = spec;
        if (cached != null) return cached;
        ClassPathResource resource = new ClassPathResource(RESOURCE);
        if (!resource.exists()) throw StudioException.internal("The API spec is not part of this build", "no_spec");
        try (InputStream in = resource.getInputStream()) {
            return spec = in.readAllBytes();
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }
}
