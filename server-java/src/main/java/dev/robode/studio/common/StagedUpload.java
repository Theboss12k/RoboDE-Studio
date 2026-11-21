package dev.robode.studio.common;

import dev.robode.studio.store.ProjectStore;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;

/** Streams an upload to a file in a new staging folder: the body is never held in memory. */
public final class StagedUpload {
    private StagedUpload() {}

    /**
     * Copies {@code in} to {@code folder/name}. A declared length (Content-Length, or -1 when unknown) over the cap is
     * refused before anything is read, and the streamed byte count is checked against the cap as well. Either way the
     * partial folder is deleted and a 413 is thrown with {@code tooLarge}. A body shorter than its declared length is a 400.
     */
    public static Path stage(InputStream in, long declared, long cap, Path folder, String name, String tooLarge) {
        if (declared > cap) throw StudioException.tooLarge(tooLarge);
        Path file = folder.resolve(name);
        try {
            Files.createDirectories(folder);
            long total = 0;
            try (OutputStream out = Files.newOutputStream(file, StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING,
                    StandardOpenOption.WRITE)) {
                byte[] buffer = new byte[64 * 1024];
                int n;
                while ((n = in.read(buffer)) >= 0) {
                    total += n;
                    if (total > cap) throw StudioException.tooLarge(tooLarge);
                    out.write(buffer, 0, n);
                }
            }
            if (declared >= 0 && total != declared) throw new StudioException(400, "The upload ended before its declared length", null);
            return file;
        } catch (IOException e) {
            ProjectStore.deleteTree(folder);
            throw new UncheckedIOException(e);
        } catch (RuntimeException e) {
            ProjectStore.deleteTree(folder);
            throw e;
        }
    }
}
