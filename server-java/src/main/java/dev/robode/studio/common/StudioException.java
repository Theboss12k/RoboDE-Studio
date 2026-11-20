package dev.robode.studio.common;

/** An error that maps to an HTTP status and the client's {"detail": message} body. */
public class StudioException extends RuntimeException {
    private final int status;
    private final String code;

    public StudioException(int status, String message, String code) {
        super(message);
        this.status = status;
        this.code = code;
    }

    public int status() { return status; }

    /** Machine-readable cause recorded as the job's error_code (may be null). */
    public String code() { return code; }

    /** 422: the input is not acceptable (Python's ValueError). */
    public static StudioException value(String message) { return new StudioException(422, message, null); }

    /** 404: unknown project, version, candidate, job (Python's FileNotFoundError). */
    public static StudioException notFound(String message) { return new StudioException(404, message, null); }

    /** 409: state conflict (approved design is frozen, project busy). */
    public static StudioException conflict(String message) { return new StudioException(409, message, null); }

    /** 413: request too large. */
    public static StudioException tooLarge(String message) { return new StudioException(413, message, null); }

    /** 502: a model call failed. */
    public static StudioException llm(String message, String code) { return new StudioException(502, message, code); }

    /** 500: anything else. */
    public static StudioException internal(String message, String code) { return new StudioException(500, message, code); }
}
