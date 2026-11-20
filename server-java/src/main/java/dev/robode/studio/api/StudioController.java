package dev.robode.studio.api;

import dev.robode.studio.common.Json;
import dev.robode.studio.common.StudioException;
import jakarta.servlet.http.HttpServletRequest;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Locale;
import java.util.Map;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

/** The robot studio routes under /api/v1/projects/{pid}/studio. */
@RestController
@RequestMapping("/api/v1/projects/{pid}/studio")
class StudioController {
    static final int MAX_SNAPSHOT = 15 * 1024 * 1024;
    private static final Map<String, String> TYPES = Map.of(
            "step", "model/step", "stp", "model/step", "stl", "model/stl", "urdf", "application/xml", "png", "image/png",
            "pdf", "application/pdf", "zip", "application/zip", "csv", "text/csv", "json", "application/json");

    private final StudioService studio;
    private final JobApi jobApi;

    StudioController(StudioService studio, JobApi jobApi) {
        this.studio = studio;
        this.jobApi = jobApi;
    }

    private static Integer optionalInt(JsonNode body, String field) {
        JsonNode v = body == null ? null : body.get(field);
        if (v == null || v.isNull()) return null;
        if (!v.isIntegralNumber() || !v.canConvertToInt() || v.asInt() < 1) throw StudioException.value(field + " must be a positive integer");
        return v.asInt();
    }

    @PostMapping("/builds")
    ResponseEntity<JsonNode> startBuild(@PathVariable String pid, @RequestBody(required = false) JsonNode body,
                                        @RequestParam(defaultValue = "0") int wait, @RequestParam(required = false) String expand,
                                        @RequestHeader(value = "X-Studio-Callback", required = false) String callback) {
        JobApi.Options options = jobApi.options(wait, expand, callback);
        if (body != null && !body.isNull() && !body.isObject()) throw StudioException.value("The body must be an object");
        Integer designVersion = optionalInt(body, "design_version");
        Integer baseBuild = optionalInt(body, "base_build");
        JsonNode request = body == null ? null : body.get("request");
        if (request != null && !request.isNull() && (!request.isString() || request.asString().length() > 2000))
            throw StudioException.value("request must be a string of at most 2000 characters");
        JsonNode pinned = body == null ? null : body.get("pinned");
        if (pinned != null && !pinned.isNull()) {
            if (!pinned.isObject()) throw StudioException.value("pinned must be an object of joint to alternative id");
            for (var e : pinned.properties()) {
                if (e.getKey().isBlank() || e.getKey().length() > 80) throw StudioException.value("pinned has an invalid joint name");
                if (!e.getValue().isString() || e.getValue().asString().isBlank() || e.getValue().asString().length() > 120)
                    throw StudioException.value("each pinned alternative must be a string of 1 to 120 characters");
            }
        }
        return jobApi.started(studio.startBuild(pid, designVersion, baseBuild,
                request == null || request.isNull() ? null : request.asString(), pinned), options);
    }

    @GetMapping("/builds")
    JsonNode builds(@PathVariable String pid) { return studio.builds(pid); }

    @GetMapping("/builds/{n}")
    JsonNode build(@PathVariable String pid, @PathVariable int n) { return studio.buildView(pid, n); }

    @GetMapping("/builds/{n}/files/{*path}")
    ResponseEntity<byte[]> file(@PathVariable String pid, @PathVariable int n, @PathVariable String path) {
        Path file = studio.file(pid, n, path);
        String name = file.getFileName().toString();
        int dot = name.lastIndexOf('.');
        String type = dot < 0 ? null : TYPES.get(name.substring(dot + 1).toLowerCase(Locale.ROOT));
        return ResponseEntity.ok().contentType(MediaType.parseMediaType(type == null ? "application/octet-stream" : type)).body(read(file));
    }

    @PostMapping("/builds/{n}/twin/move")
    JsonNode twinMove(@PathVariable String pid, @PathVariable int n, @RequestBody(required = false) JsonNode body) {
        if (body == null || !body.isObject()) throw StudioException.value("q0 and target are required");
        if (!body.path("q0").isObject()) throw StudioException.value("q0 must be an object of joint values");
        if (!body.path("target").isObject()) throw StudioException.value("target must be an object");
        JsonNode scale = body.get("speed_scale");
        if (scale != null && !scale.isNull() && (!scale.isNumber() || scale.asDouble() <= 0 || scale.asDouble() > 2))
            throw StudioException.value("speed_scale must be above 0 and at most 2");
        return studio.twinMove(pid, n, body);
    }

    @PostMapping("/builds/{n}/twin/run")
    JsonNode twinRun(@PathVariable String pid, @PathVariable int n, @RequestBody(required = false) JsonNode body) {
        if (body != null && !body.isNull() && !body.isObject()) throw StudioException.value("The body must be an object");
        JsonNode scale = body == null ? null : body.get("speed_scale");
        double value = 1.0;
        if (scale != null && !scale.isNull()) {
            if (!scale.isNumber() || scale.asDouble() <= 0 || scale.asDouble() > 2) throw StudioException.value("speed_scale must be above 0 and at most 2");
            value = scale.asDouble();
        }
        return studio.twinRun(pid, n, value);
    }

    @PostMapping("/builds/{n}/snapshots")
    JsonNode snapshot(@PathVariable String pid, @PathVariable int n, @RequestParam String view, HttpServletRequest request) {
        byte[] png;
        try {
            png = request.getInputStream().readNBytes(MAX_SNAPSHOT + 1);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
        if (png.length > MAX_SNAPSHOT) throw StudioException.tooLarge("The snapshot is too large (15 MB at most)");
        return studio.saveSnapshot(pid, n, view, png);
    }

    @PostMapping("/builds/{n}/approve")
    ResponseEntity<JsonNode> approve(@PathVariable String pid, @PathVariable int n, @RequestParam(defaultValue = "0") int wait,
                                     @RequestParam(required = false) String expand,
                                     @RequestHeader(value = "X-Studio-Callback", required = false) String callback) {
        JobApi.Options options = jobApi.options(wait, expand, callback);
        ObjectNode started = studio.approveBuild(pid, n);
        return jobApi.started(started, options);
    }

    @GetMapping("/builds/{n}/pdf")
    ResponseEntity<byte[]> pdf(@PathVariable String pid, @PathVariable int n) {
        return ResponseEntity.ok().contentType(MediaType.APPLICATION_PDF)
                .header(HttpHeaders.CONTENT_DISPOSITION, "inline; filename=\"manufacturer.pdf\"")
                .body(read(studio.approvedFile(pid, n, "manufacturer.pdf")));
    }

    @GetMapping("/builds/{n}/package")
    ResponseEntity<byte[]> pkg(@PathVariable String pid, @PathVariable int n) {
        return ResponseEntity.ok().contentType(MediaType.parseMediaType("application/zip"))
                .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename=\"package.zip\"")
                .body(read(studio.approvedFile(pid, n, "package.zip")));
    }

    private static byte[] read(Path file) {
        try {
            return Files.readAllBytes(file);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }
}
