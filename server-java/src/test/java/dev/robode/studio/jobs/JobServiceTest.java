package dev.robode.studio.jobs;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

import dev.robode.studio.common.Json;
import dev.robode.studio.common.StudioException;
import dev.robode.studio.llm.UsageLedger;
import java.util.concurrent.CountDownLatch;
import org.junit.jupiter.api.Test;
import tools.jackson.databind.JsonNode;

class JobServiceTest {
    UsageLedger ledger = mock(UsageLedger.class);
    JobService jobs = new JobService(ledger);

    JsonNode await(String id) throws InterruptedException {
        for (int i = 0; i < 100; i++) {
            JsonNode job = jobs.get(id);
            if (!job.get("status").asString().equals("running")) return job;
            Thread.sleep(50);
        }
        throw new AssertionError("job did not finish");
    }

    @Test
    void secondDesignStepOnTheSameProjectIs409AndErrorsKeepTheirCode() throws Exception {
        CountDownLatch release = new CountDownLatch(1);
        JsonNode first = jobs.start("design", "p_1", job -> { release.await(); return Json.obj().put("ok", true); });
        assertThat(first.get("status").asString()).isEqualTo("running");
        assertThat(first.propertyNames()).containsExactly("id", "kind", "robot_id", "status", "progress", "result", "error", "error_code", "started");
        assertThatThrownBy(() -> jobs.start("revise", "p_1", job -> null)).isInstanceOfSatisfying(StudioException.class,
                e -> assertThat(e.status()).isEqualTo(409));
        jobs.start("design", "p_2", job -> null); // another project is fine
        jobs.start("search", "p_1", job -> null); // other kinds are fine
        release.countDown();
        assertThat(await(first.get("id").asString()).get("result").get("ok").asBoolean()).isTrue();

        JsonNode failed = await(jobs.start("search", "p_1", job -> { throw TaskRunner.failure("llm", "x_code", "model down"); }).get("id").asString());
        assertThat(failed.get("status").asString()).isEqualTo("error");
        assertThat(failed.get("error").asString()).isEqualTo("model down");
        assertThat(failed.get("error_code").asString()).isEqualTo("x_code");
        JsonNode crashed = await(jobs.start("search", "p_1", job -> { throw new IllegalStateException("oops"); }).get("id").asString());
        assertThat(crashed.get("error").asString()).isEqualTo("IllegalStateException: oops");
        assertThatThrownBy(() -> jobs.get("nope")).hasMessage("Job not found");
    }

    @Test
    void usageLinesGoToTheLedgerLabelledWithProjectAndKind() throws Exception {
        JsonNode record = jobs.start("revise", "p_9", job -> {
            job.listener().progress("working");
            job.listener().usage(Json.parse("{\"type\":\"usage\",\"role\":\"revise\",\"profile\":\"claude_opus\",\"entry\":{\"input\":1}}"));
            return Json.obj();
        });
        JsonNode done = await(record.get("id").asString());
        assertThat(done.get("progress").get(0).asString()).isEqualTo("working");
        verify(ledger).record(eq("revise"), eq("claude_opus"), eq("p_9"), eq("revise"), any());
    }

    @Test
    void summaryAddsSubjectAndFinishedWhileJsonStaysTheSame() throws Exception {
        CountDownLatch release = new CountDownLatch(1);
        JsonNode started = jobs.start("analyse", "p_1", "c7", job -> { release.await(); return Json.obj(); });
        String id = started.get("id").asString();
        assertThat(started.has("subject")).isFalse();
        assertThat(started.has("finished")).isFalse();
        JsonNode running = jobs.list("p_1").get(0);
        assertThat(running.get("subject").asString()).isEqualTo("c7");
        assertThat(running.get("finished").isNull()).isTrue();
        release.countDown();
        await(id);
        JsonNode done = jobs.list("p_1").get(0);
        assertThat(done.get("finished").asString()).isNotEmpty();
        assertThat(jobs.get(id).propertyNames()).containsExactly("id", "kind", "robot_id", "status", "progress", "result", "error", "error_code", "started");
        assertThat(jobs.start("search", "p_1", job -> null).get("kind").asString()).isEqualTo("search");
    }

    @Test
    void listPutsRunningFirstThenNewestFinishedAndCapsAtTwenty() throws Exception {
        for (int i = 0; i < 25; i++) await(jobs.start("search", "p_1", job -> null).get("id").asString());
        CountDownLatch release = new CountDownLatch(1);
        String running = jobs.start("design", "p_1", job -> { release.await(); return null; }).get("id").asString();
        jobs.start("search", "p_2", job -> null);
        var list = jobs.list("p_1");
        assertThat(list).hasSize(20);
        assertThat(list.get(0).get("id").asString()).isEqualTo(running);
        assertThat(list.stream().allMatch(j -> j.get("robot_id").asString().equals("p_1"))).isTrue();
        for (int i = 2; i < list.size(); i++)
            assertThat(list.get(i - 1).get("finished").asString()).isGreaterThanOrEqualTo(list.get(i).get("finished").asString());
        assertThat(jobs.running()).extracting(j -> j.get("id").asString()).contains(running);
        release.countDown();
    }

    @Test
    void searchAndAnalyseJoinARunningJobAndFleetDesignIsExclusive() throws Exception {
        CountDownLatch release = new CountDownLatch(1);
        JsonNode search = jobs.start("search", "p_1", job -> { release.await(); return Json.obj(); });
        assertThat(jobs.start("search", "p_1", job -> null).get("id")).isEqualTo(search.get("id"));
        assertThat(jobs.start("search", "p_2", job -> null).get("id")).isNotEqualTo(search.get("id"));
        JsonNode a = jobs.start("analyse", "p_1", "c1", job -> { release.await(); return Json.obj(); });
        assertThat(jobs.start("analyse", "p_1", "c1", job -> null).get("id")).isEqualTo(a.get("id"));
        assertThat(jobs.start("analyse", "p_1", "c2", job -> null).get("id")).isNotEqualTo(a.get("id"));
        JsonNode design = jobs.start("design", "p_3", job -> { release.await(); return Json.obj(); });
        assertThat(design.get("status").asString()).isEqualTo("running");
        assertThatThrownBy(() -> jobs.start("fleet_design", "p_3", job -> null)).isInstanceOfSatisfying(StudioException.class,
                e -> assertThat(e.status()).isEqualTo(409));
        assertThatThrownBy(() -> jobs.start("design", "p_3", job -> null)).isInstanceOfSatisfying(StudioException.class,
                e -> assertThat(e.status()).isEqualTo(409));
        release.countDown();
        await(search.get("id").asString());
        // finished jobs no longer join
        assertThat(jobs.start("search", "p_1", job -> null).get("id")).isNotEqualTo(search.get("id"));
    }
}
