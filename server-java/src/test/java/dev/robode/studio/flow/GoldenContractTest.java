package dev.robode.studio.flow;

import static org.assertj.core.api.Assertions.assertThat;

import dev.robode.studio.common.Json;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.TreeSet;
import java.util.regex.Pattern;
import java.util.stream.Stream;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/**
 * Replays every recorded FastAPI request (src/test/resources/golden, see its README) against the Java app under /api/v1,
 * with the real Python tasks and scripted fixture LLMs, and compares the status, the response structure (field names and
 * types, recursively) and the stable values. Intentional differences are in ALLOWED, each with its reason.
 */
@SpringBootTest
@AutoConfigureMockMvc
class GoldenContractTest {
    static final Path RUNTIME = FlowSupport.newRuntime("studio-golden");
    static final Path GOLDEN = FlowSupport.RESOURCES.resolve("golden");

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry registry) { FlowSupport.configure(registry, RUNTIME); }

    @Autowired MockMvc mvc;

    /** An accepted difference: golden file number pattern, JSON pointer pattern, why. */
    record Allowed(String files, String pointer, String reason) {
        boolean covers(String file, String pointer) { return file.matches(files) && pointer.matches(this.pointer); }
    }

    static final List<Allowed> ALLOWED = List.of(
            new Allowed("29|33|34|40", "/parent_version",
                    "DesignVersion gained parent_version (migration plan); the pre-migration goldens lack it"),
            new Allowed("34|40", "/change_summary",
                    "Revise now appends the outcome of the checks code ran after the change to the model's summary (2026-11-20)"),
            new Allowed("54", "/detail", "Encoded slashes: FastAPI matched no route (\"Not found\"), Spring routes to the project and answers \"Project not found\"; both 404"),
            new Allowed("53|49", "/detail",
                    "Request validation: FastAPI returned pydantic's list of error objects, the Java API returns one detail string"));

    static final Pattern PLACEHOLDER = Pattern.compile("<(project_id|job_id|profile_id|timestamp|date|uuid|duration|runtime|path|api_key)>");

    final List<String> failures = new ArrayList<>();
    final List<String> used = new ArrayList<>();
    final String[] projects = new String[4];
    String profileId;
    String lastJobId;

    @Test
    void everyGoldenResponseMatches() throws IOException {
        FlowSupport.Api api = new FlowSupport.Api(mvc);
        List<Path> files;
        try (Stream<Path> s = Files.list(GOLDEN)) {
            files = s.filter(p -> p.getFileName().toString().matches("\\d\\d_.*\\.json")).sorted().toList();
        }
        assertThat(files).hasSize(76);
        for (Path file : files) {
            String name = file.getFileName().toString();
            String number = name.substring(0, 2);
            if (number.equals("15")) FlowSupport.writeScriptedProfiles(RUNTIME, "rules_v2.json"); // from here the LLM roles are fixtures
            if (number.equals("64")) FlowSupport.writeScriptedProfiles(RUNTIME, "rules.json"); // this part approves v1, not the revised v2
            JsonNode golden = Json.read(file);
            String method = golden.get("method").asString();
            String path = golden.get("path").asString().replaceFirst("^/api", "");
            path = path.replace("<project_id>", projects[projectIndex(Integer.parseInt(number))] == null ? "" : projects[projectIndex(Integer.parseInt(number))]);
            path = path.replace("<profile_id>", String.valueOf(profileId));
            if (path.contains("<job_id>")) path = path.replace("<job_id>", String.valueOf(lastJobId));
            Object body = requestBody(number, golden.get("request_body"));
            FlowSupport.Res res = api.send(method, path, body);
            JsonNode actual = res.body();
            boolean exportFile = path.endsWith("/export") && res.status() == 200;
            if (name.endsWith("_final.json")) {
                JsonNode job = api.finish(actual);
                actual = job;
            }
            if (exportFile) actual = exportView(res);
            // bookkeeping for later requests
            if (method.equals("POST") && path.equals("/projects") && res.status() == 200) projects[projectIndex(Integer.parseInt(number))] = actual.get("id").asString();
            if (number.equals("07")) profileId = actual.get("id").asString();
            if (actual != null && actual.has("kind") && actual.has("status") && actual.has("progress") && !name.endsWith("_final.json")) lastJobId = actual.get("id").asString();

            if (res.status() != golden.get("status").asInt()) {
                failures.add(name + ": status " + res.status() + " != " + golden.get("status").asInt() + " " + res.body());
                continue;
            }
            JsonNode expected = golden.get("response_body");
            if (!name.endsWith("_final.json") && expected != null && expected.has("kind") && expected.has("progress") && actual != null) {
                expected = stripJobRunning(expected); // a job start may already be done in either run
                actual = stripJobRunning(actual);
            }
            if (number.equals("20")) { expected = byTitle(expected); actual = byTitle(actual); }
            compare(name, "", expected, actual);
        }
        System.out.println("Golden contract: " + files.size() + " files replayed; allowed differences used: " + new TreeSet<>(used));
        assertThat(failures).as("contract differences").isEmpty();
    }

    static int projectIndex(int n) {
        return n <= 18 || n == 59 ? 0 : n <= 58 ? 1 : n <= 63 ? 2 : 3;
    }

    Object requestBody(String number, JsonNode body) {
        if (body == null || body.isNull()) return null;
        if (body.isString() && body.asString().startsWith("<raw body")) {
            return switch (number) {
                case "16" -> "MZ".getBytes(StandardCharsets.ISO_8859_1);
                case "17" -> FlowSupport.PNG;
                default -> { try { yield Files.readAllBytes(FlowSupport.DATASHEET); } catch (IOException e) { throw new IllegalStateException(e); } }
            };
        }
        return Json.parse(body.toString().replace("<api_key>", "AIza-golden-fake-key"));
    }

    static JsonNode stripJobRunning(JsonNode job) {
        ObjectNode copy = ((ObjectNode) job).deepCopy();
        copy.remove("status");
        copy.remove("progress");
        copy.remove("result");
        return copy;
    }

    static JsonNode byTitle(JsonNode array) {
        List<JsonNode> items = new ArrayList<>();
        array.forEach(items::add);
        items.sort(Comparator.comparing(n -> n.path("title").asString()));
        ArrayNode out = Json.arr();
        items.forEach(out::add);
        return out;
    }

    static JsonNode exportView(FlowSupport.Res res) throws IOException {
        List<String> names = new ArrayList<>();
        try (ZipInputStream in = new ZipInputStream(new ByteArrayInputStream(res.bytes()))) {
            for (ZipEntry e; (e = in.getNextEntry()) != null; ) names.add(e.getName());
        }
        names.sort(null);
        ObjectNode out = Json.obj();
        out.put("status", res.status());
        out.put("content_type", "application/zip");
        ArrayNode entries = Json.arr();
        names.forEach(entries::add);
        out.set("zip_entries", entries);
        return out;
    }

    // -- comparison ---------------------------------------------------------------------------------------------
    void compare(String file, String pointer, JsonNode expected, JsonNode actual) {
        String number = file.substring(0, 2);
        if (expected == null || actual == null) {
            if (expected != actual) report(number, file, pointer, "missing body on one side");
            return;
        }
        if (expected.isObject() && actual.isObject()) {
            for (var e : expected.properties()) {
                if (!actual.has(e.getKey())) report(number, file, pointer + "/" + e.getKey(), "missing in Java response");
                else compare(file, pointer + "/" + e.getKey(), e.getValue(), actual.get(e.getKey()));
            }
            for (String key : actual.propertyNames())
                if (!expected.has(key)) report(number, file, pointer + "/" + key, "extra in Java response");
        } else if (expected.isArray() && actual.isArray()) {
            if (expected.size() != actual.size()) { report(number, file, pointer, "array size " + expected.size() + " vs " + actual.size()); return; }
            for (int i = 0; i < expected.size(); i++) compare(file, pointer + "/" + i, expected.get(i), actual.get(i));
        } else if (expected.isNumber() && actual.isNumber()) {
            double a = expected.asDouble(), b = actual.asDouble();
            if (Math.abs(a - b) > 1e-9 * Math.max(1, Math.abs(a))) report(number, file, pointer, "number " + a + " vs " + b);
        } else if (expected.isString() && actual.isString()) {
            String text = expected.asString();
            if (PLACEHOLDER.matcher(text).find()) {
                String regex = Pattern.quote(text).replaceAll("<(project_id|job_id|profile_id|timestamp|date|uuid|duration|runtime|path|api_key)>", "\\\\E.+\\\\Q");
                if (!actual.asString().matches("(?s)" + regex) && !actual.asString().isEmpty())
                    report(number, file, pointer, "placeholder " + text + " vs " + abbreviate(actual.asString()));
            } else if (!text.equals(actual.asString())) {
                report(number, file, pointer, "\"" + abbreviate(text) + "\" vs \"" + abbreviate(actual.asString()) + "\"");
            }
        } else if (expected.isNull() && actual.isNull() || expected.isBoolean() && actual.isBoolean() && expected.asBoolean() == actual.asBoolean()) {
            return;
        } else if (expected.getNodeType() != actual.getNodeType()) {
            // numbers in a placeholder position (durations): golden has the string, Java the number
            if (expected.isString() && PLACEHOLDER.matcher(expected.asString()).find() && actual.isNumber()) return;
            report(number, file, pointer, "type " + expected.getNodeType() + " vs " + actual.getNodeType());
        } else {
            report(number, file, pointer, expected + " vs " + actual);
        }
    }

    void report(String number, String file, String pointer, String what) {
        for (Allowed a : ALLOWED) {
            if (a.covers(number, pointer.isEmpty() ? "/" : pointer)) { used.add(a.files() + " " + a.pointer() + " (" + a.reason() + ")"); return; }
        }
        failures.add(file + " " + (pointer.isEmpty() ? "/" : pointer) + ": " + what);
    }

    static String abbreviate(String s) { return s.length() > 120 ? s.substring(0, 120) + "..." : s; }
}
