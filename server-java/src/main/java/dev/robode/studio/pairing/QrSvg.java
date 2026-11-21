package dev.robode.studio.pairing;

import com.google.zxing.BarcodeFormat;
import com.google.zxing.EncodeHintType;
import com.google.zxing.WriterException;
import com.google.zxing.common.BitMatrix;
import com.google.zxing.qrcode.QRCodeWriter;
import com.google.zxing.qrcode.decoder.ErrorCorrectionLevel;
import java.util.Map;

/** A QR code as SVG text: one rect per horizontal run of dark modules, with a quiet zone of 4 modules. */
final class QrSvg {
    private static final int QUIET = 4;

    private QrSvg() {}

    static String of(String text) {
        BitMatrix m;
        try {
            m = new QRCodeWriter().encode(text, BarcodeFormat.QR_CODE, 0, 0,
                    Map.of(EncodeHintType.MARGIN, 0, EncodeHintType.ERROR_CORRECTION, ErrorCorrectionLevel.M));
        } catch (WriterException e) {
            throw new IllegalStateException("The QR code could not be made", e);
        }
        int size = m.getWidth() + 2 * QUIET;
        StringBuilder sb = new StringBuilder(4096);
        sb.append("<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 ").append(size).append(' ').append(size)
                .append("\" shape-rendering=\"crispEdges\"><rect width=\"").append(size).append("\" height=\"").append(size)
                .append("\" fill=\"#fff\"/><g fill=\"#000\">");
        for (int y = 0; y < m.getHeight(); y++) {
            int x = 0;
            while (x < m.getWidth()) {
                if (!m.get(x, y)) { x++; continue; }
                int start = x;
                while (x < m.getWidth() && m.get(x, y)) x++;
                sb.append("<rect x=\"").append(start + QUIET).append("\" y=\"").append(y + QUIET).append("\" width=\"")
                        .append(x - start).append("\" height=\"1\"/>");
            }
        }
        return sb.append("</g></svg>").toString();
    }
}
