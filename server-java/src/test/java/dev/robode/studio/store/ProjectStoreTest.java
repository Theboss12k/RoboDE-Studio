package dev.robode.studio.store;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import dev.robode.studio.common.Ids;
import dev.robode.studio.common.Json;
import dev.robode.studio.common.StudioException;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Comparator;
import java.util.List;
import java.util.stream.Stream;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

class ProjectStoreTest {
    @TempDir Path runtime;

    static void copy(Path from, Path to) throws IOException {
        try (Stream<Path> walk = Files.walk(from)) {
            for (Path p : walk.toList()) {
                Path target = to.resolve(from.relativize(p).toString());
                if (Files.isDirectory(p)) Files.createDirectories(target); else Files.copy(p, target);
            }
        }
    }

    @Test
    void roundTripCreateVersionsApprove() {
        ProjectStore store = new ProjectStore(runtime);
        ObjectNode created = store.create("Pick bottles\nfrom a table");
        String pid = created.get("id").asString();
        assertThat(pid).matches("p_[0-9a-f]{10}");
        assertThat(created.get("title").asString()).isEqualTo("Pick bottles");
        assertThat(created.get("stage").asString()).isEqualTo("brief");
        assertThat(Json.readObject(store.dir(pid).resolve("project.json")).get("schemaVersion").asInt()).isEqualTo(1);

        ObjectNode built = (ObjectNode) Json.parse("{\"version\":0,\"created\":\"x\",\"urdf\":\"<robot/>\",\"approved\":true,\"sim\":{\"duration_s\":4}}");
        int v1 = store.addVersion(pid, built, null, Json.parse("{\"links\":[]}"), null);
        int v2 = store.addVersion(pid, built, 1, null, null);
        assertThat(List.of(v1, v2)).containsExactly(1, 2);
        ObjectNode stored = store.version(pid, 2);
        assertThat(stored.get("parent_version").asInt()).isEqualTo(1);
        assertThat(stored.get("approved").asBoolean()).isFalse();
        assertThat(store.parsedSidecar(pid, 1).has("schemaVersion")).isFalse();
        assertThat(store.versions(pid)).containsExactly(1, 2); // the sidecar is not a version

        store.approve(pid, 2);
        assertThat(store.version(pid, 2).get("approved").asBoolean()).isTrue();
        assertThat(store.version(pid, 1).get("approved").asBoolean()).isFalse();
        assertThat(store.view(pid).get("approved_version").asInt()).isEqualTo(2);
        assertThat(store.list()).hasSize(1);
        store.delete(pid);
        assertThat(store.list()).isEmpty();
        assertThatThrownBy(() -> store.require(pid)).isInstanceOf(StudioException.class).hasMessage("Project not found");
    }

    @Test
    void readsAProjectCreatedByThePythonStore() throws IOException {
        Path real = Path.of(System.getProperty("user.home"), "Downloads/RoboDE-Studio/runtime/projects/p_4775b16f5c");
        if (!Files.isDirectory(real)) real = Path.of("..", "runtime", "projects", "p_4775b16f5c");
        org.junit.jupiter.api.Assumptions.assumeTrue(Files.isDirectory(real), "no Python-created project available");
        copy(real, runtime.resolve("projects").resolve("p_4775b16f5c"));
        ProjectStore store = new ProjectStore(runtime);

        ObjectNode view = store.view("p_4775b16f5c");
        assertThat(view.get("id").asString()).isEqualTo("p_4775b16f5c");
        assertThat(view.get("versions").size()).isGreaterThanOrEqualTo(2);
        assertThat(view.get("analyses").size()).isGreaterThanOrEqualTo(1);
        assertThat(view.get("candidates_count").asInt()).isGreaterThan(0);
        assertThat(store.version("p_4775b16f5c", 1).get("sim").get("frames").size()).isGreaterThan(0);
        String cid = view.get("analyses").get(0).asString();
        assertThat(store.analysis("p_4775b16f5c", cid).get("urdf").asString()).startsWith("<");
        assertThat(store.candidate("p_4775b16f5c", cid)).isNotNull();
        assertThat(store.documentPath("p_4775b16f5c", cid)).isPresent();
        assertThat(store.list().get(0).get("id").asString()).isEqualTo("p_4775b16f5c");

        // a write by Java adds schemaVersion and keeps the Python-written content
        JsonNode before = store.meta("p_4775b16f5c").get("brief");
        store.update("p_4775b16f5c", m -> m.put("stage", "analysed"));
        assertThat(store.meta("p_4775b16f5c").get("brief")).isEqualTo(before);
        assertThat(store.meta("p_4775b16f5c").get("schemaVersion").asInt()).isEqualTo(1);
    }

    @Test
    void sanitisationMatchesPython() {
        assertThat(Ids.safeName("../../evil name?.urdf")).isEqualTo("evil name_.urdf");
        assertThat(Ids.safeName("C:\\dir\\robot.zip")).isEqualTo("robot.zip");
        assertThatThrownBy(() -> Ids.safeName(".hidden")).hasMessage("Invalid file name");
        assertThatThrownBy(() -> Ids.safeName("..")).hasMessage("Invalid file name");
        assertThatThrownBy(() -> Ids.safeId("Bad-Id")).hasMessage("Invalid identifier");
        assertThat(Ids.safeId("fanuc_crx_5ia")).isEqualTo("fanuc_crx_5ia");
        assertThatThrownBy(() -> Ids.projectId("p_zz")).hasMessage("Project not found");
        assertThatThrownBy(() -> Ids.projectId("../x")).hasMessage("Project not found");
    }

    @Test
    void referenceTextAndLimits() {
        ProjectStore store = new ProjectStore(runtime);
        String pid = store.create("x").get("id").asString();
        assertThat(store.view(pid).get("reference").isNull()).isTrue();
        store.setReferenceText(pid, "  hello  ");
        assertThat(store.view(pid).get("reference").get("text").asString()).isEqualTo("hello");
        assertThatThrownBy(() -> store.setReferenceText(pid, "x".repeat(60001))).isInstanceOf(StudioException.class);
        store.setReferenceText(pid, "");
        assertThat(store.view(pid).get("reference").isNull()).isTrue();
        assertThatThrownBy(() -> store.create("  ")).hasMessage("Describe the job first");
    }

    @Test
    void answerImagesIndexBudgetAndView() throws Exception {
        ProjectStore store = new ProjectStore(runtime);
        String pid = store.create("x").get("id").asString();
        assertThat(store.view(pid).has("answer_images")).isFalse();
        assertThat(store.view(pid).has("answer_comments")).isFalse();
        byte[] png = {(byte) 0x89, 'P', 'N', 'G', 0, 0, 0, 0};
        for (int i = 0; i < 8; i++) {
            java.nio.file.Path src = java.nio.file.Files.createTempFile("img", ".png");
            java.nio.file.Files.write(src, png);
            store.addAnswerImage(pid, "a" + i + ".png", "_general", src);
        }
        java.nio.file.Path extra = java.nio.file.Files.createTempFile("img", ".png");
        assertThatThrownBy(() -> store.addAnswerImage(pid, "a9.png", "_general", extra))
                .hasMessage("At most 8 images in total go to the spec agent (reference photos count too)");
        assertThat(store.view(pid).get("answer_images").size()).isEqualTo(8);
        assertThat(store.answerImagesForTask(pid).get(0).get("question").asString()).isEqualTo("_general");
        store.removeAnswerImage(pid, "a0.png");
        assertThat(store.answerImages(pid).size()).isEqualTo(7);
    }
}
