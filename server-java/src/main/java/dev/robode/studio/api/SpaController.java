package dev.robode.studio.api;

import dev.robode.studio.common.StudioException;
import dev.robode.studio.config.StudioProperties;
import java.nio.file.Files;
import java.nio.file.Path;
import org.springframework.core.io.FileSystemResource;
import org.springframework.core.io.Resource;
import org.springframework.http.MediaType;
import org.springframework.http.MediaTypeFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;

/** Serves client/dist with the single-page-app fallback; anything under api/ that is not a route is a 404. */
@RestController
class SpaController {
    private final Path dist;

    SpaController(StudioProperties properties) { this.dist = properties.clientDist().toAbsolutePath().normalize(); }

    @GetMapping({"/", "/{*path}"})
    ResponseEntity<?> client(@PathVariable(required = false) String path) {
        String p = path == null ? "" : path.replaceFirst("^/+", "");
        if (p.startsWith("api/")) throw StudioException.notFound("Not found");
        Path file = dist.resolve(p).normalize();
        if (!p.isEmpty() && file.startsWith(dist) && Files.isRegularFile(file)) return file(file);
        Path index = dist.resolve("index.html");
        if (Files.isRegularFile(index)) return file(index);
        return ResponseEntity.ok().contentType(MediaType.TEXT_PLAIN)
                .body("RoboDE Studio API is running. Start the client with: npm run dev");
    }

    private static ResponseEntity<Resource> file(Path file) {
        Resource resource = new FileSystemResource(file);
        return ResponseEntity.ok().contentType(MediaTypeFactory.getMediaType(resource).orElse(MediaType.APPLICATION_OCTET_STREAM))
                .body(resource);
    }
}
