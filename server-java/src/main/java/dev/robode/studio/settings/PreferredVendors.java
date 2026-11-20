package dev.robode.studio.settings;

import dev.robode.studio.common.Json;
import dev.robode.studio.common.StudioException;
import dev.robode.studio.config.StudioProperties;
import java.nio.file.Path;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/** The user's preferred vendors (runtime/settings/vendors.json); Java is the only writer. Python gets the list in task inputs. */
@Component
public class PreferredVendors {
    private static final Logger LOG = LoggerFactory.getLogger(PreferredVendors.class);
    public static final List<String> CATEGORIES = List.of("robots", "motors", "electronics");
    public static final int MAX = 50;
    private static final Pattern LABEL = Pattern.compile("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$");

    private final Path path;
    private final Object lock = new Object();

    public PreferredVendors(StudioProperties props) {
        this.path = props.runtime().resolve("settings").resolve("vendors.json");
    }

    /** {"vendors": [...]} (empty when nothing was saved or the file is unreadable). */
    public ObjectNode get() {
        synchronized (lock) {
            ObjectNode out = Json.obj();
            ArrayNode vendors = Json.arr();
            try {
                ObjectNode data = Json.readObject(path);
                if (data != null && data.get("vendors") instanceof ArrayNode saved) saved.forEach(vendors::add);
            } catch (RuntimeException e) {
                LOG.warn("Preferred vendors file unreadable, using an empty list: {}", e.getMessage());
            }
            out.set("vendors", vendors);
            return out;
        }
    }

    /** The list for task inputs. */
    public ArrayNode list() { return (ArrayNode) get().get("vendors"); }

    public ObjectNode replace(JsonNode body) {
        ObjectNode clean = validate(body);
        synchronized (lock) {
            Json.write(path, clean);
        }
        return clean;
    }

    static ObjectNode validate(JsonNode body) {
        if (body == null || !body.isObject() || !(body.get("vendors") instanceof ArrayNode vendors))
            throw StudioException.value("The body must be an object with a vendors list");
        if (vendors.size() > MAX) throw StudioException.value("At most " + MAX + " preferred vendors");
        ArrayNode out = Json.arr();
        Set<String> seen = new HashSet<>();
        int i = 0;
        for (JsonNode v : vendors) {
            i++;
            String where = "Vendor " + i + ": ";
            if (!v.isObject()) throw StudioException.value(where + "must be an object");
            String name = text(v, "name", null, where);
            if (name == null || name.isBlank() || name.strip().length() > 120)
                throw StudioException.value(where + "name must be 1 to 120 characters");
            String domain = text(v, "domain", null, where);
            domain = domain == null ? "" : domain.strip().toLowerCase();
            if (!validDomain(domain))
                throw StudioException.value(where + "domain must be a bare host such as robu.in (no scheme, path or port)");
            if (!seen.add(domain)) throw StudioException.value(where + "domain " + domain + " is listed twice");
            JsonNode cats = v.get("categories");
            if (cats == null || !cats.isArray() || cats.isEmpty())
                throw StudioException.value(where + "categories needs at least one of robots, motors, electronics");
            Set<String> chosen = new HashSet<>();
            for (JsonNode c : cats) {
                if (!c.isString() || !CATEGORIES.contains(c.asString()))
                    throw StudioException.value(where + "categories may only be robots, motors or electronics");
                chosen.add(c.asString());
            }
            String note = text(v, "note", "", where).strip();
            if (note.length() > 300) throw StudioException.value(where + "note must be at most 300 characters");
            ArrayNode ordered = Json.arr();
            CATEGORIES.stream().filter(chosen::contains).forEach(ordered::add);
            out.add(Json.obj().put("name", name.strip()).put("domain", domain).<ObjectNode>set("categories", ordered).put("note", note));
        }
        return Json.obj().set("vendors", out);
    }

    static boolean validDomain(String host) {
        if (host.isEmpty() || host.length() > 253) return false;
        String[] labels = host.split("\\.", -1);
        if (labels.length < 2) return false;
        for (String l : labels) if (!LABEL.matcher(l).matches()) return false;
        return !labels[labels.length - 1].chars().allMatch(Character::isDigit);
    }

    private static String text(JsonNode v, String field, String fallback, String where) {
        JsonNode x = v.get(field);
        if (x == null || x.isNull()) return fallback;
        if (!x.isString()) throw StudioException.value(where + field + " must be a string");
        return x.asString();
    }
}
