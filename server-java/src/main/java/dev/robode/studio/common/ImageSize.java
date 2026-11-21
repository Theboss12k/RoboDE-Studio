package dev.robode.studio.common;

/**
 * Width, height and format of a PNG, JPEG or WebP image, read from its header only (nothing is decoded).
 * Used at upload time; the Python ar_calibrate result replaces the size later.
 */
public final class ImageSize {
    /** format is the stored extension: png, jpg or webp. */
    public record Size(int width, int height, String format) {}

    private static final byte[] PNG_SIGNATURE = {(byte) 0x89, 'P', 'N', 'G', '\r', '\n', 0x1A, '\n'};

    private ImageSize() {}

    /** The size from the header, or null when the bytes are not a PNG, JPEG or WebP image with a readable header. */
    public static Size read(byte[] b) {
        if (b == null) return null;
        if (startsWith(b, PNG_SIGNATURE)) return png(b);
        if (b.length >= 2 && (b[0] & 0xFF) == 0xFF && (b[1] & 0xFF) == 0xD8) return jpeg(b);
        if (b.length >= 12 && ascii(b, 0, "RIFF") && ascii(b, 8, "WEBP")) return webp(b);
        return null;
    }

    /** IHDR is the first chunk: width and height are big-endian 32-bit values at offsets 16 and 20. */
    private static Size png(byte[] b) {
        if (b.length < 24 || !ascii(b, 12, "IHDR")) return null;
        long w = u32be(b, 16);
        long h = u32be(b, 20);
        return valid(w, h) ? new Size((int) w, (int) h, "png") : null;
    }

    /** Walks the marker segments to the first SOF0 to SOF15 (except DHT, JPG and DAC), which holds height then width. */
    private static Size jpeg(byte[] b) {
        int p = 2;
        while (p + 4 <= b.length) {
            if ((b[p] & 0xFF) != 0xFF) return null;
            int marker = b[p + 1] & 0xFF;
            if (marker == 0xFF) { // fill byte
                p++;
                continue;
            }
            if (marker == 0xD8 || marker == 0x01 || (marker >= 0xD0 && marker <= 0xD7)) { // standalone markers
                p += 2;
                continue;
            }
            if (marker == 0xD9 || marker == 0xDA) return null; // end of image or start of scan: no frame header seen
            if (marker >= 0xC0 && marker <= 0xCF && marker != 0xC4 && marker != 0xC8 && marker != 0xCC) {
                if (p + 9 > b.length) return null;
                long h = u16be(b, p + 5);
                long w = u16be(b, p + 7);
                return valid(w, h) ? new Size((int) w, (int) h, "jpg") : null;
            }
            int length = u16be(b, p + 2);
            if (length < 2) return null;
            p += 2 + length;
        }
        return null;
    }

    /** VP8X (canvas), VP8L (lossless) or VP8 (lossy) as the first chunk after WEBP. */
    private static Size webp(byte[] b) {
        if (ascii(b, 12, "VP8X") && b.length >= 30) {
            long w = u24le(b, 24) + 1;
            long h = u24le(b, 27) + 1;
            return valid(w, h) ? new Size((int) w, (int) h, "webp") : null;
        }
        if (ascii(b, 12, "VP8L") && b.length >= 25 && (b[20] & 0xFF) == 0x2F) {
            long bits = u32le(b, 21);
            long w = (bits & 0x3FFF) + 1;
            long h = ((bits >> 14) & 0x3FFF) + 1;
            return valid(w, h) ? new Size((int) w, (int) h, "webp") : null;
        }
        if (ascii(b, 12, "VP8 ") && b.length >= 30 && (b[23] & 0xFF) == 0x9D && (b[24] & 0xFF) == 0x01 && (b[25] & 0xFF) == 0x2A) {
            long w = u16le(b, 26) & 0x3FFF;
            long h = u16le(b, 28) & 0x3FFF;
            return valid(w, h) ? new Size((int) w, (int) h, "webp") : null;
        }
        return null;
    }

    private static boolean valid(long w, long h) { return w > 0 && h > 0 && w <= Integer.MAX_VALUE && h <= Integer.MAX_VALUE; }

    private static boolean startsWith(byte[] b, byte[] prefix) {
        if (b.length < prefix.length) return false;
        for (int i = 0; i < prefix.length; i++) if (b[i] != prefix[i]) return false;
        return true;
    }

    private static boolean ascii(byte[] b, int at, String text) {
        if (b.length < at + text.length()) return false;
        for (int i = 0; i < text.length(); i++) if (b[at + i] != (byte) text.charAt(i)) return false;
        return true;
    }

    private static long u32be(byte[] b, int at) {
        return ((long) (b[at] & 0xFF) << 24) | ((b[at + 1] & 0xFF) << 16) | ((b[at + 2] & 0xFF) << 8) | (b[at + 3] & 0xFF);
    }

    private static long u32le(byte[] b, int at) {
        return (b[at] & 0xFF) | ((b[at + 1] & 0xFF) << 8) | ((b[at + 2] & 0xFF) << 16) | ((long) (b[at + 3] & 0xFF) << 24);
    }

    private static int u16be(byte[] b, int at) { return ((b[at] & 0xFF) << 8) | (b[at + 1] & 0xFF); }

    private static int u16le(byte[] b, int at) { return (b[at] & 0xFF) | ((b[at + 1] & 0xFF) << 8); }

    private static long u24le(byte[] b, int at) { return (b[at] & 0xFF) | ((b[at + 1] & 0xFF) << 8) | ((b[at + 2] & 0xFF) << 16); }
}
