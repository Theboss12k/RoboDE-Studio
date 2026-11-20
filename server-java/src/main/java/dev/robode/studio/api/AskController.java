package dev.robode.studio.api;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import tools.jackson.databind.JsonNode;

/** POST /api/v1/ask: the Ask bar. A job (kind "ask") whose result is the AskResponse. */
@RestController
@RequestMapping("/api/v1")
class AskController {
    private final AskService ask;
    private final JobApi jobApi;

    AskController(AskService ask, JobApi jobApi) {
        this.ask = ask;
        this.jobApi = jobApi;
    }

    @PostMapping("/ask")
    ResponseEntity<JsonNode> ask(@RequestBody(required = false) JsonNode body, @RequestParam(defaultValue = "0") int wait,
                                 @RequestParam(required = false) String expand,
                                 @RequestHeader(value = "X-Studio-Callback", required = false) String callback) {
        JobApi.Options options = jobApi.options(wait, expand, callback);
        return jobApi.started(ask.start(body), options);
    }
}
