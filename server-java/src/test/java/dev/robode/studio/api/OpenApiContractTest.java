package dev.robode.studio.api;

import static org.assertj.core.api.Assertions.assertThat;

import dev.robode.studio.support.SpecValidator;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.TreeSet;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.web.servlet.mvc.method.annotation.RequestMappingHandlerMapping;

/** contracts/openapi.yaml (bundled) is the authority: every /api/v1 route has an operation and every operation has a route. */
@SpringBootTest(properties = "studio.warm-meta=false")
class OpenApiContractTest {
    static final Path RUNTIME = Path.of(System.getProperty("java.io.tmpdir"), "studio-openapi-" + System.nanoTime());

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry registry) { registry.add("studio.runtime", RUNTIME::toString); }

    @Autowired RequestMappingHandlerMapping mapping;

    /** "GET /projects/{}" style keys: parameter names do not matter, and Spring's {*path} equals {path}. */
    private static String key(String method, String path) {
        return method + " " + path.replaceAll("\\{\\*?[^}/]+}", "{}");
    }

    @Test
    void everyRouteIsInTheSpecAndEveryOperationHasARoute() {
        TreeSet<String> routes = new TreeSet<>();
        mapping.getHandlerMethods().forEach((info, handler) -> {
            if (info.getPathPatternsCondition() == null) return;
            for (String pattern : info.getPathPatternsCondition().getPatternValues()) {
                if (!pattern.startsWith("/api/v1")) continue; // the SPA fallback is not an API route
                String path = pattern.substring("/api/v1".length());
                var methods = info.getMethodsCondition().getMethods();
                if (methods.isEmpty()) routes.add(key("ANY", path));
                else methods.forEach(m -> routes.add(key(m.name(), path)));
            }
        });
        TreeSet<String> spec = new TreeSet<>();
        for (String op : SpecValidator.operations()) {
            int space = op.indexOf(' ');
            spec.add(key(op.substring(0, space), op.substring(space + 1)));
        }
        List<String> problems = new ArrayList<>();
        for (String r : routes) if (!spec.contains(r)) problems.add("route without a spec operation: " + r);
        for (String s : spec) if (!routes.contains(s)) problems.add("spec operation without a route: " + s);
        assertThat(problems).as("differences between the Spring routes (%d) and contracts/openapi.yaml (%d operations)", routes.size(), spec.size())
                .isEmpty();
    }

    @Test
    void operationIdsAreUnique() {
        // the bundle is parsed by SpecValidator; a duplicate would also fail the lint, this keeps it visible in the Java build
        assertThat(SpecValidator.operations()).doesNotHaveDuplicates();
    }
}
