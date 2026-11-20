package dev.robode.studio.config;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.List;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Settings bound from the environment: STUDIO_RUNTIME, STUDIO_PYTHON, STUDIO_REPO_ROOT, STUDIO_HOST, STUDIO_PORT,
 * STUDIO_TOKEN, STUDIO_ALLOW_SCRIPTED (tests only), STUDIO_CORS_ORIGINS (comma separated).
 */
@ConfigurationProperties("studio")
public record StudioProperties(Path repoRoot, Path runtime, Path python, String host, Integer port, String token,
                               Boolean allowScripted, List<String> corsOrigins, Duration idleTimeout,
                               Duration ceiling, Integer maxProcesses, Boolean warmMeta, Boolean keepJobDirs, Path fakeDownload) {
    public StudioProperties {
        repoRoot = (repoRoot != null ? repoRoot : detectRoot()).toAbsolutePath().normalize();
        runtime = (runtime != null ? runtime : repoRoot.resolve("runtime")).toAbsolutePath().normalize();
        python = python != null ? python : repoRoot.resolve(".venv/bin/python");
        host = host == null || host.isBlank() ? "127.0.0.1" : host.strip();
        port = port != null ? port : 8800;
        token = token == null || token.isBlank() ? null : token;
        allowScripted = allowScripted != null && allowScripted;
        corsOrigins = corsOrigins == null ? List.of() : corsOrigins.stream().map(String::strip).filter(s -> !s.isEmpty()).toList();
        idleTimeout = idleTimeout != null ? idleTimeout : Duration.ofSeconds(60);
        ceiling = ceiling != null ? ceiling : Duration.ofMinutes(45);
        maxProcesses = maxProcesses != null ? maxProcesses : 4;
        warmMeta = warmMeta == null || warmMeta;
        keepJobDirs = keepJobDirs != null && keepJobDirs;
    }

    public Path server() { return repoRoot.resolve("server"); }

    public Path clientDist() { return repoRoot.resolve("client").resolve("dist"); }

    /** The repository root: the nearest folder above the working directory that holds server/studio. */
    static Path detectRoot() {
        Path dir = Path.of("").toAbsolutePath();
        for (Path p = dir; p != null; p = p.getParent()) {
            if (Files.isDirectory(p.resolve("server").resolve("studio"))) return p;
        }
        return dir;
    }
}
