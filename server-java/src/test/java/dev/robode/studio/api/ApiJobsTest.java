package dev.robode.studio.api;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import dev.robode.studio.common.Json;
import dev.robode.studio.jobs.JobService;
import java.nio.file.Path;
import java.util.concurrent.CountDownLatch;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;

/** The job list routes: a running job shows up per project and in the global running list (blocking work, no Python). */
@SpringBootTest(properties = {"studio.warm-meta=false"})
@AutoConfigureMockMvc
class ApiJobsTest {
    static final Path RUNTIME = Path.of(System.getProperty("java.io.tmpdir"), "studio-jobs-" + System.nanoTime());

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry registry) { registry.add("studio.runtime", RUNTIME::toString); }

    @Autowired MockMvc mvc;
    @Autowired JobService jobs;

    @Test
    void runningJobAppearsInProjectAndGlobalLists() throws Exception {
        String body = mvc.perform(post("/api/v1/projects").contentType(MediaType.APPLICATION_JSON).content("{\"brief\":\"Move a box\"}"))
                .andExpect(status().isOk()).andReturn().getResponse().getContentAsString();
        String pid = Json.parse(body).get("id").asString();
        mvc.perform(get("/api/v1/projects/" + pid + "/jobs")).andExpect(status().isOk()).andExpect(jsonPath("$.length()").value(0));
        mvc.perform(get("/api/v1/projects/nope/jobs")).andExpect(status().isNotFound());

        CountDownLatch release = new CountDownLatch(1);
        String id = jobs.start("analyse", pid, "c1", job -> { release.await(); return Json.obj(); }).get("id").asString();
        try {
            mvc.perform(get("/api/v1/projects/" + pid + "/jobs")).andExpect(jsonPath("$[0].id").value(id))
                    .andExpect(jsonPath("$[0].status").value("running")).andExpect(jsonPath("$[0].subject").value("c1"))
                    .andExpect(jsonPath("$[0].finished").doesNotExist());
            mvc.perform(get("/api/v1/jobs").param("running", "true")).andExpect(jsonPath("$[?(@.id=='" + id + "')].kind").value("analyse"));
        } finally {
            release.countDown();
        }
    }
}
