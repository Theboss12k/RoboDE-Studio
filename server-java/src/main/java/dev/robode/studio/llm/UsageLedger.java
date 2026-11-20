package dev.robode.studio.llm;

import dev.robode.studio.common.Json;
import dev.robode.studio.common.StudioException;
import dev.robode.studio.common.Time;
import dev.robode.studio.config.StudioProperties;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.function.Function;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * Token usage ledger: one JSON line per model call in runtime/llm/usage.jsonl. This class is the only appender.
 * Line: {at, role, profile, robot_id, task, model, input, cache_write, cache_read, output, cost_usd, seconds, images,
 * turns, structured, ok, web_calls}. {@link #summary} aggregates exactly as the Python ledger did.
 */
@Component
public class UsageLedger {
    private static final List<String> TOKENS = List.of("input", "cache_write", "cache_read", "output");
    private static final List<String> GROUPS = List.of("role", "model", "robot_id", "task", "day");

    private final Path path;
    private final Object lock = new Object();

    @org.springframework.beans.factory.annotation.Autowired
    public UsageLedger(StudioProperties properties) { this(properties.runtime()); }

    public UsageLedger(Path runtime) { this.path = runtime.resolve("llm").resolve("usage.jsonl"); }

    /** Appends one line for a model call reported by a task (`entry` is the adapter's usage entry). */
    public void record(String role, String profile, String robotId, String task, JsonNode entry) {
        ObjectNode line = Json.obj();
        line.put("at", Time.now());
        line.put("role", role);
        line.put("profile", profile);
        if (robotId == null) line.putNull("robot_id"); else line.put("robot_id", robotId);
        if (task == null) line.putNull("task"); else line.put("task", task);
        if (entry != null && entry.isObject()) {
            entry.properties().forEach(e -> {
                if (!e.getKey().equals("pages_opened") && !line.has(e.getKey())) line.set(e.getKey(), e.getValue());
            });
        }
        synchronized (lock) {
            try {
                Files.createDirectories(path.getParent());
                Files.writeString(path, Json.MAPPER.writeValueAsString(line) + "\n", StandardCharsets.UTF_8,
                        StandardOpenOption.CREATE, StandardOpenOption.APPEND);
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
        }
    }

    public List<ObjectNode> entries(String since) {
        List<ObjectNode> result = new ArrayList<>();
        if (!Files.exists(path)) return result;
        List<String> lines;
        try {
            lines = Files.readAllLines(path, StandardCharsets.UTF_8);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
        for (String text : lines) {
            if (text.isBlank()) continue;
            try {
                if (Json.parse(text) instanceof ObjectNode entry && entry.has("at")
                        && (since == null || entry.get("at").asString().compareTo(since) >= 0)) result.add(entry);
            } catch (RuntimeException ignored) {
                // a torn or foreign line is skipped, as before
            }
        }
        return result;
    }

    public ObjectNode summary(String period) { return summary(period, 40); }

    public ObjectNode summary(String period, int recent) {
        if (!List.of("today", "7d", "30d", "all").contains(period))
            throw StudioException.value("period is today, 7d, 30d or all");
        OffsetDateTime now = OffsetDateTime.now(ZoneOffset.UTC);
        OffsetDateTime since = switch (period) {
            case "today" -> now.truncatedTo(ChronoUnit.DAYS);
            case "7d" -> now.minusDays(7);
            case "30d" -> now.minusDays(30);
            default -> null;
        };
        return aggregate(period, entries(since == null ? null : Time.format(since)), recent);
    }

    static ObjectNode aggregate(String period, List<ObjectNode> entries, int recent) {
        ObjectNode totals = empty();
        Map<String, Map<String, ObjectNode>> groups = new java.util.LinkedHashMap<>();
        for (String g : GROUPS) groups.put(g, new TreeMap<>());
        for (ObjectNode e : entries) {
            add(totals, e);
            for (String g : GROUPS) {
                String key = g.equals("day") ? e.get("at").asString().substring(0, Math.min(10, e.get("at").asString().length()))
                        : orOther(e.get(g));
                add(groups.get(g).computeIfAbsent(key, k -> empty()), e);
            }
        }
        long inputTotal = totals.get("input").asLong() + totals.get("cache_write").asLong() + totals.get("cache_read").asLong();
        if (inputTotal > 0) totals.put("cache_hit_rate", round(totals.get("cache_read").asLong() / (double) inputTotal, 3));
        else totals.putNull("cache_hit_rate");
        ObjectNode out = Json.obj();
        out.put("period", period);
        out.set("totals", totals);
        for (String g : GROUPS) {
            ObjectNode group = Json.obj();
            groups.get(g).forEach(group::set);
            out.set("by_" + g, group);
        }
        ArrayNode recentRows = Json.arr();
        for (int i = entries.size() - 1; i >= Math.max(0, entries.size() - recent); i--) recentRows.add(entries.get(i));
        out.set("recent", recentRows);
        return out;
    }

    private static String orOther(JsonNode value) {
        if (value == null || value.isNull()) return "other";
        String text = value.asString();
        return text.isEmpty() || value.isBoolean() && !value.asBoolean() || value.isNumber() && value.asDouble() == 0 ? "other" : text;
    }

    private static ObjectNode empty() {
        ObjectNode b = Json.obj();
        b.put("calls", 0);
        for (String k : TOKENS) b.put(k, 0);
        b.put("cost_usd", 0.0);
        b.put("seconds", 0.0);
        b.put("images", 0);
        b.put("web_calls", 0);
        return b;
    }

    private static void add(ObjectNode bucket, ObjectNode e) {
        bucket.put("calls", bucket.get("calls").asLong() + 1);
        for (String k : TOKENS) bucket.put(k, bucket.get(k).asLong() + num(e, k));
        bucket.put("cost_usd", round(bucket.get("cost_usd").asDouble() + numD(e, "cost_usd"), 6));
        bucket.put("seconds", round(bucket.get("seconds").asDouble() + numD(e, "seconds"), 1));
        bucket.put("images", bucket.get("images").asLong() + num(e, "images"));
        bucket.put("web_calls", bucket.get("web_calls").asLong() + num(e, "web_calls"));
    }

    private static long num(ObjectNode e, String k) { return e.has(k) && e.get(k).isNumber() ? e.get(k).asLong() : 0; }

    private static double numD(ObjectNode e, String k) { return e.has(k) && e.get(k).isNumber() ? e.get(k).asDouble() : 0.0; }

    /** Python's round(x, digits): the exact binary value rounded half-even. */
    static double round(double value, int digits) {
        return new BigDecimal(value).setScale(digits, RoundingMode.HALF_EVEN).doubleValue();
    }

    /** Starts a new ledger; the old one is kept beside it with a timestamp. */
    public void reset() {
        synchronized (lock) {
            try {
                if (Files.exists(path))
                    Files.move(path, path.resolveSibling("usage-" + (System.currentTimeMillis() / 1000) + ".jsonl"));
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
        }
    }
}
