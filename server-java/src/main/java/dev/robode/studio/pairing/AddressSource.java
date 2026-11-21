package dev.robode.studio.pairing;

import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.net.SocketException;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Locale;

/** Where the phone-link addresses come from. Tests replace it with 127.0.0.1. */
@FunctionalInterface
public interface AddressSource {
    /** IPv4 addresses a phone on the same network could use, best first. */
    List<String> addresses();

    /** Wi-Fi first, then Ethernet; only up, non-loopback, non-virtual, non point-to-point, site-local IPv4. */
    static List<String> discover() {
        List<NetworkInterface> nics;
        try {
            var all = NetworkInterface.getNetworkInterfaces();
            nics = all == null ? List.of() : Collections.list(all);
        } catch (SocketException e) {
            return List.of();
        }
        record Found(int rank, String ip) {}
        List<Found> found = new ArrayList<>();
        for (NetworkInterface nic : nics) {
            try {
                if (!nic.isUp() || nic.isLoopback() || nic.isVirtual() || nic.isPointToPoint()) continue;
            } catch (SocketException e) {
                continue;
            }
            String label = (nic.getName() + " " + nic.getDisplayName()).toLowerCase(Locale.ROOT);
            if (skipped(label)) continue;
            int rank = label.contains("wi-fi") || label.contains("wifi") || label.contains("wlan") || label.contains("wireless") ? 0
                    : label.contains("ethernet") || label.contains("eth") || label.contains("en0") ? 1 : 2;
            for (InetAddress a : Collections.list(nic.getInetAddresses()))
                if (a instanceof Inet4Address && a.isSiteLocalAddress()) found.add(new Found(rank, a.getHostAddress()));
        }
        return found.stream().sorted((x, y) -> Integer.compare(x.rank(), y.rank())).map(Found::ip).distinct().toList();
    }

    private static boolean skipped(String label) {
        for (String bad : new String[] {"vethernet", "virtualbox", "vmware", "hyper-v", "wsl", "docker", "loopback"})
            if (label.contains(bad)) return true;
        return false;
    }
}
