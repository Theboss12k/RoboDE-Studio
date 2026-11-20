package dev.robode.studio.common;

import java.nio.file.Path;
import java.util.regex.Pattern;

/** Identifier and file name rules, ported exactly from server/studio/util.py and projects/store.py. */
public final class Ids {
    private static final Pattern ID = Pattern.compile("^[a-z][a-z0-9_]{0,62}$");
    private static final Pattern PROJECT_ID = Pattern.compile("^p_[0-9a-f]{10}$");
    private static final Pattern UNSAFE = Pattern.compile("[^A-Za-z0-9._ -]+");

    private Ids() {}

    /** A candidate or analysis id; anything else is a 422 "Invalid identifier". */
    public static String safeId(String value) {
        if (value == null || !ID.matcher(value).matches()) throw StudioException.value("Invalid identifier");
        return value;
    }

    /** A project id; anything else is a 404 so ids cannot probe the file system. */
    public static String projectId(String value) {
        if (value == null || !PROJECT_ID.matcher(value).matches()) throw StudioException.notFound("Project not found");
        return value;
    }

    /** A plain file name (no folders) that is safe to store. */
    public static String safeName(String filename) {
        String text = String.valueOf(filename).replace('\\', '/');
        int slash = text.lastIndexOf('/');
        String name = (slash >= 0 ? text.substring(slash + 1) : text).strip();
        name = UNSAFE.matcher(name).replaceAll("_");
        if (name.length() > 160) name = name.substring(0, 160);
        if (name.isEmpty() || name.equals(".") || name.equals("..") || name.startsWith("."))
            throw StudioException.value("Invalid file name");
        return name;
    }

    /** Resolves {@code child} below {@code root}, refusing anything that escapes it. */
    public static Path inside(Path root, String child) {
        Path resolved = root.resolve(child).normalize();
        if (!resolved.startsWith(root.normalize())) throw StudioException.value("Invalid path");
        return resolved;
    }
}
