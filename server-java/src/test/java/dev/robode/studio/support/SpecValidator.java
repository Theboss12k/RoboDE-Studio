package dev.robode.studio.support;

import com.networknt.schema.Error;
import com.networknt.schema.Schema;
import com.networknt.schema.SchemaRegistry;
import com.networknt.schema.SpecificationVersion;
import dev.robode.studio.common.Json;
import java.io.IOException;
import java.io.InputStream;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.regex.Pattern;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * Validates a JSON response of the API against the bundled OpenAPI spec (openapi.bundled.json on the classpath, copied
 * from contracts/ at build time): the operation for (method, path) must exist, the status must be declared for it, and a
 * JSON body must match the response's application/json schema (JSON Schema 2020-12 through networknt). Reusable by any test:
 * {@code SpecValidator.check("GET", "/projects/p_1?x=1", 200, "application/json", bytes)}. Paths may carry the /api/v1 prefix
 * and a query string.
 */
public final class SpecValidator {
    private record Route(String method, String template, Pattern pattern, int literals, JsonNode operation) {}

    private static final SpecValidator INSTANCE = new SpecValidator();

    private final JsonNode spec;
    private final List<Route> routes = new ArrayList<>();
    private final SchemaRegistry registry = SchemaRegistry.withDefaultDialect(SpecificationVersion.DRAFT_2020_12);
    private final Map<String, Schema> schemas = new ConcurrentHashMap<>();

    private SpecValidator() {
        try (InputStream in = SpecValidator.class.getResourceAsStream("/openapi.bundled.json")) {
            if (in == null) throw new IllegalStateException("openapi.bundled.json is not on the classpath; run npm run contracts:openapi");
            spec = Json.MAPPER.readTree(new String(in.readAllBytes(), StandardCharsets.UTF_8));
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
        for (var path : spec.get("paths").properties()) {
            StringBuilder sb = new StringBuilder();
            int literals = 0;
            for (String part : path.getKey().split("/")) {
                if (part.isEmpty()) continue;
                if (part.startsWith("{")) sb.append("/[^/]+");
                else { sb.append("/").append(Pattern.quote(part)); literals++; }
            }
            for (var op : path.getValue().properties())
                if (java.util.Set.of("get", "put", "post", "delete", "patch").contains(op.getKey()))
                    routes.add(new Route(op.getKey().toUpperCase(), path.getKey(), Pattern.compile(sb.toString()), literals, op.getValue()));
        }
        routes.sort(Comparator.comparingInt((Route r) -> r.literals()).reversed());
    }

    public static SpecValidator instance() { return INSTANCE; }

    /** Convenience for tests: validates and throws AssertionError listing every difference. */
    public static void check(String method, String uri, int status, String contentType, byte[] body) {
        List<String> problems = INSTANCE.validate(method, uri, status, contentType, body);
        if (!problems.isEmpty())
            throw new AssertionError("Response does not match contracts/openapi.yaml: " + method + " " + uri + " -> " + status + "\n  "
                    + String.join("\n  ", problems));
    }

    /** True when the spec has an operation for the method and path (unknown routes, such as a 404 probe, have none). */
    public boolean hasOperation(String method, String uri) {
        String path = uri.contains("?") ? uri.substring(0, uri.indexOf('?')) : uri;
        if (path.startsWith("/api/v1")) path = path.substring("/api/v1".length());
        for (Route r : routes) if (r.method().equals(method.toUpperCase()) && r.pattern().matcher(path).matches()) return true;
        return false;
    }

    /** The problems found (empty when the response matches the spec). */
    public List<String> validate(String method, String uri, int status, String contentType, byte[] body) {
        String path = uri.contains("?") ? uri.substring(0, uri.indexOf('?')) : uri;
        if (path.startsWith("/api/v1")) path = path.substring("/api/v1".length());
        while (path.length() > 1 && path.endsWith("/")) path = path.substring(0, path.length() - 1);
        Route route = null;
        for (Route r : routes) if (r.method().equals(method.toUpperCase()) && r.pattern().matcher(path).matches()) { route = r; break; }
        if (route == null) return List.of("no operation " + method + " " + path + " in the spec");
        JsonNode response = route.operation().path("responses").get(String.valueOf(status));
        if (response == null) response = route.operation().path("responses").get("default");
        if (response == null)
            return List.of("status " + status + " is not declared for " + route.method() + " " + route.template() + " (declared: "
                    + keys(route.operation().path("responses")) + ")");
        response = resolve(response);
        if (contentType == null || !contentType.contains("json") || body == null || body.length == 0) return List.of();
        JsonNode schemaNode = response.path("content").path("application/json").get("schema");
        if (schemaNode == null)
            return List.of("a JSON body is not declared for status " + status + " of " + route.method() + " " + route.template());
        JsonNode instance;
        try {
            instance = Json.MAPPER.readTree(new String(body, StandardCharsets.UTF_8));
        } catch (RuntimeException e) {
            return List.of("the body is not valid JSON: " + e.getMessage());
        }
        Schema schema = schemas.computeIfAbsent(route.method() + " " + route.template() + " " + status, k -> compile(schemaNode));
        List<String> out = new ArrayList<>();
        for (Error e : schema.validate(instance)) out.add(e.getInstanceLocation() + ": " + e.getMessage());
        return out;
    }

    private JsonNode resolve(JsonNode node) {
        JsonNode ref = node.get("$ref");
        if (ref == null) return node;
        JsonNode target = spec.at(ref.asString().substring(1));
        if (target.isMissingNode()) throw new IllegalStateException("unresolved " + ref);
        return target;
    }

    private Schema compile(JsonNode schemaNode) {
        ObjectNode wrapper = Json.obj();
        wrapper.put("$schema", "https://json-schema.org/draft/2020-12/schema");
        wrapper.setAll((ObjectNode) schemaNode.deepCopy());
        wrapper.set("components", spec.get("components"));
        return registry.getSchema(wrapper);
    }

    private static String keys(JsonNode node) {
        List<String> keys = new ArrayList<>();
        node.propertyNames().forEach(keys::add);
        return String.join(", ", keys);
    }

    /** Every (METHOD, path template) the spec declares, for the route parity test. */
    public static List<String> operations() {
        List<String> out = new ArrayList<>();
        for (Route r : INSTANCE.routes) out.add(r.method() + " " + r.template());
        out.sort(String::compareTo);
        return out;
    }
}
