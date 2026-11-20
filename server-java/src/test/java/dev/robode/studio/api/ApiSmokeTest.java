package dev.robode.studio.api;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.webmvc.test.autoconfigure.AutoConfigureMockMvc;
import org.springframework.http.MediaType;
import org.springframework.test.context.DynamicPropertyRegistry;
import org.springframework.test.context.DynamicPropertySource;
import org.springframework.test.web.servlet.MockMvc;

@SpringBootTest(properties = {"studio.warm-meta=false", "studio.token=t0ken"})
@AutoConfigureMockMvc
class ApiSmokeTest {
    static final Path RUNTIME = Path.of(System.getProperty("java.io.tmpdir"), "studio-smoke-" + System.nanoTime());

    @DynamicPropertySource
    static void props(DynamicPropertyRegistry registry) { registry.add("studio.runtime", RUNTIME::toString); }

    @Autowired MockMvc mvc;

    @Test
    void tokenIsRequiredAndProjectsRoundTrip() throws Exception {
        mvc.perform(get("/api/v1/health")).andExpect(status().isUnauthorized()).andExpect(jsonPath("$.detail").exists());
        mvc.perform(get("/api/v1/health").header("Authorization", "Bearer nope")).andExpect(status().isUnauthorized());
        mvc.perform(get("/api/v1/health").header("Authorization", "Bearer t0ken")).andExpect(status().isOk())
                .andExpect(jsonPath("$.ok").value(true)).andExpect(jsonPath("$.llm.spec").value("claude_opus"));

        String body = mvc.perform(post("/api/v1/projects").header("Authorization", "Bearer t0ken")
                        .contentType(MediaType.APPLICATION_JSON).content("{\"brief\":\"Move a box\"}"))
                .andExpect(status().isOk()).andExpect(jsonPath("$.stage").value("brief")).andReturn().getResponse().getContentAsString();
        String pid = tools.jackson.databind.json.JsonMapper.builder().build().readTree(body).get("id").asString();
        mvc.perform(get("/api/v1/projects").header("Authorization", "Bearer t0ken")).andExpect(jsonPath("$[0].id").value(pid));
        mvc.perform(get("/api/v1/projects/p_0000000000").header("Authorization", "Bearer t0ken")).andExpect(status().isNotFound())
                .andExpect(jsonPath("$.detail").value("Project not found"));
        mvc.perform(post("/api/v1/projects").header("Authorization", "Bearer t0ken").contentType(MediaType.APPLICATION_JSON)
                .content("{\"brief\":\"\"}")).andExpect(status().isUnprocessableEntity());
        mvc.perform(get("/api/v1/llm/usage").param("period", "bad").header("Authorization", "Bearer t0ken"))
                .andExpect(status().isUnprocessableEntity()).andExpect(jsonPath("$.detail").value("period is today, 7d, 30d or all"));
        mvc.perform(delete("/api/v1/projects/" + pid).header("Authorization", "Bearer t0ken")).andExpect(jsonPath("$.ok").value(true));
    }

    @Test
    void openApiDocumentIsServed() throws Exception {
        mvc.perform(get("/api/v1/openapi.json").header("Authorization", "Bearer t0ken")).andExpect(status().isOk())
                .andExpect(jsonPath("$.openapi").value("3.1.0")).andExpect(jsonPath("$.paths['/projects']").exists());
        mvc.perform(get("/api/v1/openapi.json")).andExpect(status().isUnauthorized());
    }
}
