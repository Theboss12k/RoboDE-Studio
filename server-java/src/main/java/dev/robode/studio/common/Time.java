package dev.robode.studio.common;

import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;

/** Timestamps in the format Python's datetime.isoformat(timespec='seconds') gave: 2026-11-20T09:06:05+00:00. */
public final class Time {
    private static final DateTimeFormatter FORMAT = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ssxxx");

    private Time() {}

    public static String now() { return format(OffsetDateTime.now(ZoneOffset.UTC)); }

    public static String format(OffsetDateTime time) { return FORMAT.format(time.withNano(0)); }
}
