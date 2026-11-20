package dev.robode.studio.meta;

import dev.robode.studio.common.Json;
import dev.robode.studio.config.StudioProperties;
import dev.robode.studio.jobs.TaskResult;
import dev.robode.studio.jobs.TaskRunner;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;
import tools.jackson.databind.JsonNode;

/** Families, provider kinds and models come from the Python task `meta`, called once and cached. */
@Component
public class MetaService {
    private static final Logger LOG = LoggerFactory.getLogger(MetaService.class);
    private final TaskRunner runner;
    private final StudioProperties props;
    private volatile JsonNode cached;

    public MetaService(TaskRunner runner, StudioProperties props) {
        this.runner = runner;
        this.props = props;
    }

    @EventListener(ApplicationReadyEvent.class)
    void warm() {
        if (!props.warmMeta()) return;
        Thread.ofVirtual().start(() -> {
            try {
                get();
            } catch (RuntimeException e) {
                LOG.warn("The Python engine's meta task failed at startup (retried on first use): {}", e.getMessage());
            }
        });
    }

    public JsonNode get() {
        JsonNode value = cached;
        if (value != null) return value;
        synchronized (this) {
            if (cached == null) {
                try (TaskResult result = runner.run("meta-" + UUID.randomUUID().toString().substring(0, 8), "meta", Json.obj(),
                        Json.obj().put("profile", (String) null), null, "meta", null)) {
                    cached = result.data();
                }
            }
            return cached;
        }
    }

    public JsonNode families() { return get().path("families"); }
}
