package dev.robode.studio.llm;

import dev.robode.studio.common.Json;
import dev.robode.studio.common.StudioException;
import dev.robode.studio.config.StudioProperties;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.List;
import java.util.Map;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * API keys for providers that need one (Gemini), kept in runtime/llm/keys.json apart from the profiles. The file is
 * created with mode 0600 at temp-file creation and moved into place atomically. Keys are never returned: only
 * {@link #status()} leaves this class, and {@link #get} serves the job runner. An environment variable is the
 * fallback when no key is saved.
 */
@Component
public class Keys {
    private static final Map<String, List<String>> ENVIRONMENT = Map.of("gemini", List.of("GEMINI_API_KEY", "GOOGLE_API_KEY"));
    /** Providers whose profiles get an api_key in the task envelope. */
    public static final List<String> KEYED = List.of("gemini");

    private final Path path;
    private final Map<String, String> env;

    @org.springframework.beans.factory.annotation.Autowired
    public Keys(StudioProperties properties) { this(properties.runtime(), System.getenv()); }

    public Keys(Path runtime, Map<String, String> env) {
        this.path = runtime.resolve("llm").resolve("keys.json");
        this.env = env;
    }

    public Path path() { return path; }

    private synchronized ObjectNode read() {
        try {
            return Json.readObject(path) instanceof ObjectNode node ? node : Json.obj();
        } catch (RuntimeException e) {
            return Json.obj();
        }
    }

    private void write(ObjectNode data) {
        try {
            Path dir = path.getParent();
            Files.createDirectories(dir);
            Path temp;
            try {
                temp = Files.createTempFile(dir, "keys-", ".tmp",
                        PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")));
            } catch (UnsupportedOperationException notPosix) {
                temp = Files.createTempFile(dir, "keys-", ".tmp");
            }
            try {
                Files.writeString(temp, Json.MAPPER.writeValueAsString(Json.withSchemaVersion(data)), StandardCharsets.UTF_8);
                Json.move(temp, path);
            } finally {
                Files.deleteIfExists(temp);
            }
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    /** The key to use: the saved one, else the environment's, else "". */
    public synchronized String get(String provider) {
        String saved = Json.text(read(), provider);
        if (saved != null && !saved.isEmpty()) return saved;
        for (String name : ENVIRONMENT.getOrDefault(provider, List.of())) {
            String value = env.get(name);
            if (value != null && !value.isEmpty()) return value;
        }
        return "";
    }

    public synchronized void set(String provider, String key) {
        key = key == null ? "" : key.strip();
        if (!ENVIRONMENT.containsKey(provider)) throw StudioException.value(provider + " does not use an API key");
        if (key.isEmpty() || key.length() > 400 || key.chars().anyMatch(Character::isWhitespace))
            throw StudioException.value("That does not look like an API key");
        ObjectNode data = read();
        data.put(provider, key);
        write(data);
    }

    public synchronized void delete(String provider) {
        ObjectNode data = read();
        if (data.remove(provider) != null) write(data);
    }

    /** Whether each provider has a key, and from where; never the key itself. */
    public synchronized ObjectNode status() {
        ObjectNode saved = read();
        ObjectNode result = Json.obj();
        for (var entry : ENVIRONMENT.entrySet()) {
            String envName = entry.getValue().stream().filter(n -> env.get(n) != null && !env.get(n).isEmpty()).findFirst().orElse(null);
            String savedKey = Json.text(saved, entry.getKey());
            boolean isSaved = savedKey != null && !savedKey.isEmpty();
            ObjectNode item = Json.obj();
            item.put("saved", isSaved);
            if (envName == null) item.putNull("environment"); else item.put("environment", envName);
            item.put("ready", isSaved || envName != null);
            result.set(entry.getKey(), item);
        }
        return result;
    }
}
