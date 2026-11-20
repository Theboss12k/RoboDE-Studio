package dev.robode.studio.api;

import dev.robode.studio.common.StudioException;
import java.util.LinkedHashMap;
import java.util.Map;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.HttpRequestMethodNotSupportedException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;
import org.springframework.web.servlet.resource.NoResourceFoundException;

/** Every error body is {"detail": message, "error_code"?: code}, as the client expects. */
@RestControllerAdvice
class ApiErrors {
    private static final Logger LOG = LoggerFactory.getLogger(ApiErrors.class);

    @ExceptionHandler(StudioException.class)
    ResponseEntity<Map<String, String>> studio(StudioException e) { return body(e.status(), e.getMessage(), e.code()); }

    @ExceptionHandler(org.springframework.web.multipart.MaxUploadSizeExceededException.class)
    ResponseEntity<Map<String, String>> tooLarge(Exception e) { return body(413, "The request body is too large (64 MB at most)"); }

    @ExceptionHandler({HttpMessageNotReadableException.class, MissingServletRequestParameterException.class,
            MethodArgumentTypeMismatchException.class})
    ResponseEntity<Map<String, String>> invalid(Exception e) {
        String message = e instanceof MissingServletRequestParameterException m ? "Missing parameter: " + m.getParameterName()
                : e instanceof MethodArgumentTypeMismatchException t ? "Invalid value for " + t.getName()
                : "The request body is not valid JSON";
        return body(422, message);
    }

    @ExceptionHandler(NoResourceFoundException.class)
    ResponseEntity<Map<String, String>> missing(NoResourceFoundException e) { return body(404, "Not found"); }

    @ExceptionHandler(HttpRequestMethodNotSupportedException.class)
    ResponseEntity<Map<String, String>> method(HttpRequestMethodNotSupportedException e) { return body(405, "Method not allowed"); }

    @ExceptionHandler(Exception.class)
    ResponseEntity<Map<String, String>> other(Exception e) {
        LOG.error("Unhandled error", e);
        return body(500, e.getClass().getSimpleName() + ": " + e.getMessage());
    }

    private static ResponseEntity<Map<String, String>> body(int status, String detail) { return body(status, detail, null); }

    /** {detail, error_code?}: the code only when the failure has one. */
    private static ResponseEntity<Map<String, String>> body(int status, String detail, String code) {
        Map<String, String> out = new LinkedHashMap<>();
        out.put("detail", detail == null ? "Error" : detail);
        if (code != null) out.put("error_code", code);
        return ResponseEntity.status(status).body(out);
    }
}
