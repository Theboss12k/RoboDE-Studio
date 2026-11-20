package dev.robode.studio.common;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import tools.jackson.core.util.DefaultIndenter;
import tools.jackson.core.util.DefaultPrettyPrinter;
import tools.jackson.core.util.Separators;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectWriter;
import tools.jackson.databind.json.JsonMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/** Domain JSON is kept as opaque Jackson trees. This holds the shared mapper and the crash-safe file helpers. */
public final class Json {
    public static final JsonMapper MAPPER = JsonMapper.builder().build();
    public static final int SCHEMA_VERSION = 1;
    private static final ObjectWriter PRETTY = MAPPER.writer().with(new DefaultPrettyPrinter(
            Separators.createDefaultInstance().withObjectNameValueSpacing(Separators.Spacing.AFTER))
            .withObjectIndenter(new DefaultIndenter("  ", "\n")).withArrayIndenter(new DefaultIndenter("  ", "\n")));

    private Json() {}

    public static ObjectNode obj() { return MAPPER.createObjectNode(); }

    public static ArrayNode arr() { return MAPPER.createArrayNode(); }

    public static JsonNode parse(String text) { return MAPPER.readTree(text); }

    /** Indent-2 JSON like Python's json.dumps(indent=2). */
    public static String pretty(JsonNode node) { return PRETTY.writeValueAsString(node); }

    /** The file's JSON, or null when it does not exist. */
    public static JsonNode read(Path path) {
        try {
            return MAPPER.readTree(Files.readString(path, StandardCharsets.UTF_8));
        } catch (NoSuchFileException e) {
            return null;
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    public static ObjectNode readObject(Path path) {
        return read(path) instanceof ObjectNode node ? node : null;
    }

    /** The same object with schemaVersion first (replacing any previous value). Arrays and others pass through. */
    public static JsonNode withSchemaVersion(JsonNode node) {
        if (!(node instanceof ObjectNode object)) return node;
        ObjectNode out = obj();
        out.put("schemaVersion", SCHEMA_VERSION);
        object.properties().forEach(e -> {
            if (!e.getKey().equals("schemaVersion")) out.set(e.getKey(), e.getValue());
        });
        return out;
    }

    /** A copy without the storage-only schemaVersion field, for API responses. */
    public static ObjectNode withoutSchemaVersion(JsonNode node) {
        ObjectNode copy = ((ObjectNode) node).deepCopy();
        copy.remove("schemaVersion");
        return copy;
    }

    /** Writes the file through a temporary file in the same folder and an atomic move; objects get schemaVersion 1. */
    public static void write(Path path, JsonNode node) {
        writeText(path, pretty(withSchemaVersion(node)));
    }

    public static void writeText(Path path, String text) {
        try {
            Path dir = path.toAbsolutePath().getParent();
            Files.createDirectories(dir);
            Path temp = Files.createTempFile(dir, ".tmp-", "");
            try {
                Files.writeString(temp, text, StandardCharsets.UTF_8);
                move(temp, path);
            } finally {
                Files.deleteIfExists(temp);
            }
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    public static void move(Path from, Path to) throws IOException {
        try {
            Files.move(from, to, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
        } catch (java.nio.file.AtomicMoveNotSupportedException e) {
            Files.move(from, to, StandardCopyOption.REPLACE_EXISTING);
        }
    }

    public static String text(JsonNode node, String field) {
        JsonNode value = node == null ? null : node.get(field);
        return value == null || value.isNull() ? null : value.asString();
    }
}
