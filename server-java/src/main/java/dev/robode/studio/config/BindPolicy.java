package dev.robode.studio.config;

import java.net.InetAddress;
import java.net.UnknownHostException;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;

/** The server must not listen beyond the loopback interface unless a bearer token protects /api/v1. */
@Component
public class BindPolicy {
    public BindPolicy(Environment environment, StudioProperties properties) {
        check(environment.getProperty("server.address", properties.host()), properties.token());
    }

    public static boolean isLoopback(String host) {
        if (host == null || host.isBlank()) return false;
        String h = host.strip();
        if (h.startsWith("[") && h.endsWith("]")) h = h.substring(1, h.length() - 1);
        if (h.equalsIgnoreCase("localhost")) return true;
        if (!h.matches("[0-9a-fA-F:.]+")) return false; // never resolve a name: DNS must not decide this
        try {
            return InetAddress.getByName(h).isLoopbackAddress();
        } catch (UnknownHostException e) {
            return false;
        }
    }

    public static void check(String host, String token) {
        if (!isLoopback(host) && (token == null || token.isBlank()))
            throw new IllegalStateException("Refusing to listen on " + host + " without STUDIO_TOKEN: "
                    + "a non-loopback STUDIO_HOST requires a bearer token");
    }
}
