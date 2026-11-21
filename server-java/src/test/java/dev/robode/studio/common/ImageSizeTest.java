package dev.robode.studio.common;

import static org.assertj.core.api.Assertions.assertThat;

import java.awt.image.BufferedImage;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import javax.imageio.ImageIO;
import org.junit.jupiter.api.Test;

class ImageSizeTest {
    static byte[] encoded(String format, int width, int height) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        ImageIO.write(new BufferedImage(width, height, BufferedImage.TYPE_INT_RGB), format, out);
        return out.toByteArray();
    }

    static byte[] le(long value, int bytes) {
        byte[] out = new byte[bytes];
        for (int i = 0; i < bytes; i++) out[i] = (byte) (value >> (8 * i));
        return out;
    }

    static byte[] cat(byte[]... parts) {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        for (byte[] p : parts) out.writeBytes(p);
        return out.toByteArray();
    }

    static byte[] ascii(String text) { return text.getBytes(StandardCharsets.US_ASCII); }

    @Test
    void pngHeaderGivesWidthAndHeight() throws IOException {
        ImageSize.Size size = ImageSize.read(encoded("png", 37, 11));
        assertThat(size).isEqualTo(new ImageSize.Size(37, 11, "png"));
    }

    @Test
    void jpegFrameHeaderGivesWidthAndHeight() throws IOException {
        ImageSize.Size size = ImageSize.read(encoded("jpg", 640, 480));
        assertThat(size).isEqualTo(new ImageSize.Size(640, 480, "jpg"));
    }

    @Test
    void webpLosslessVp8lHeader() {
        int width = 300;
        int height = 200;
        long bits = (width - 1) | ((long) (height - 1) << 14);
        byte[] vp8l = cat(ascii("VP8L"), le(5, 4), new byte[] {0x2F}, le(bits, 4));
        byte[] file = cat(ascii("RIFF"), le(vp8l.length + 4, 4), ascii("WEBP"), vp8l);
        assertThat(ImageSize.read(file)).isEqualTo(new ImageSize.Size(300, 200, "webp"));
    }

    @Test
    void webpExtendedVp8xHeader() {
        byte[] vp8x = cat(ascii("VP8X"), le(10, 4), new byte[4], le(799, 3), le(599, 3));
        byte[] file = cat(ascii("RIFF"), le(vp8x.length + 4, 4), ascii("WEBP"), vp8x);
        assertThat(ImageSize.read(file)).isEqualTo(new ImageSize.Size(800, 600, "webp"));
    }

    @Test
    void webpLossyVp8Header() {
        byte[] payload = cat(new byte[3], new byte[] {(byte) 0x9D, 0x01, 0x2A}, le(128, 2), le(96, 2));
        byte[] vp8 = cat(ascii("VP8 "), le(payload.length, 4), payload);
        byte[] file = cat(ascii("RIFF"), le(vp8.length + 4, 4), ascii("WEBP"), vp8);
        assertThat(ImageSize.read(file)).isEqualTo(new ImageSize.Size(128, 96, "webp"));
    }

    @Test
    void otherBytesAreNotAnImage() {
        assertThat(ImageSize.read("GIF89a0000".getBytes(StandardCharsets.US_ASCII))).isNull();
        assertThat(ImageSize.read(new byte[0])).isNull();
        assertThat(ImageSize.read(null)).isNull();
        assertThat(ImageSize.read(cat(new byte[] {(byte) 0x89}, ascii("PNG\r\n\u001a\n0000000000000000")))).isNull();
    }
}
