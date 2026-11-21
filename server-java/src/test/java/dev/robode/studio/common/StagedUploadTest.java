package dev.robode.studio.common;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;

class StagedUploadTest {
    private static final String TOO_LARGE = "too large";

    private static Path folder() throws IOException {
        return Files.createTempDirectory("staged").resolve("u-test");
    }

    @Test
    void streamsTheBodyToTheFile() throws IOException {
        Path dir = folder();
        byte[] body = new byte[200_000];
        for (int i = 0; i < body.length; i++) body[i] = (byte) i;
        Path file = StagedUpload.stage(new ByteArrayInputStream(body), body.length, 1_000_000, dir, "video.mp4", TOO_LARGE);
        assertThat(file).isEqualTo(dir.resolve("video.mp4"));
        assertThat(Files.readAllBytes(file)).isEqualTo(body);
    }

    @Test
    void aDeclaredLengthOverTheCapIsRefusedBeforeReadingAndLeavesNothing() throws IOException {
        Path dir = folder();
        ByteArrayInputStream body = new ByteArrayInputStream(new byte[2000]);
        assertThatThrownBy(() -> StagedUpload.stage(body, 2000, 1000, dir, "video.mp4", TOO_LARGE))
                .isInstanceOfSatisfying(StudioException.class, e -> assertThat(e.status()).isEqualTo(413));
        assertThat(body.available()).isEqualTo(2000);
        assertThat(Files.exists(dir)).isFalse();
    }

    @Test
    void aStreamedBodyOverTheCapIsRefusedAndThePartialFileIsDeleted() throws IOException {
        Path dir = folder();
        assertThatThrownBy(() -> StagedUpload.stage(new ByteArrayInputStream(new byte[2000]), -1, 1000, dir, "video.mp4", TOO_LARGE))
                .isInstanceOfSatisfying(StudioException.class, e -> {
                    assertThat(e.status()).isEqualTo(413);
                    assertThat(e.getMessage()).isEqualTo(TOO_LARGE);
                });
        assertThat(Files.exists(dir)).isFalse();
    }

    @Test
    void aShortBodyIsA400() throws IOException {
        Path dir = folder();
        assertThatThrownBy(() -> StagedUpload.stage(new ByteArrayInputStream(new byte[10]), 20, 1000, dir, "video.mp4", TOO_LARGE))
                .isInstanceOfSatisfying(StudioException.class, e -> assertThat(e.status()).isEqualTo(400));
        assertThat(Files.exists(dir)).isFalse();
    }
}
