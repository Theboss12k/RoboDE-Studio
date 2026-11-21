package dev.robode.studio.jobs;

import dev.robode.studio.common.Json;
import dev.robode.studio.common.Time;
import dev.robode.studio.llm.UsageLedger;
import java.util.ArrayList;
import java.util.List;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

/** A background job's record: {id, kind, robot_id, status, progress, result, error, error_code, started}. */
public final class Job {
    private final String id;
    private final String kind;
    private final String robotId;
    private final String started = Time.now();
    private final String subject;
    private final List<String> progress = new ArrayList<>();
    private final UsageLedger ledger;
    private String status = "running";
    private JsonNode result;
    private String error;
    private String errorCode;
    private String finished;

    Job(String id, String kind, String robotId, String subject, UsageLedger ledger) {
        this.id = id;
        this.subject = subject;
        this.kind = kind;
        this.robotId = robotId;
        this.ledger = ledger;
    }

    public String id() { return id; }

    public String kind() { return kind; }

    public String robotId() { return robotId; }

    /** What the job works on: a candidate id for analyse, "v&lt;n&gt;" for revise, otherwise null. */
    public String subject() { return subject; }

    synchronized String finishedAt() { return finished; }

    public synchronized boolean running() { return status.equals("running"); }

    public synchronized void progress(String text) { progress.add(text); }

    synchronized void done(JsonNode value) { result = value; status = "done"; finished = Time.now(); }

    synchronized void failed(String message, String code) { error = message; errorCode = code; status = "error"; finished = Time.now(); }

    /** The task listener: progress lines go to the record, usage lines to the ledger as they arrive. */
    public TaskRunner.Listener listener() {
        return new TaskRunner.Listener() {
            @Override public void progress(String text) { Job.this.progress(text); }

            @Override public void usage(JsonNode message) {
                ledger.record(Json.text(message, "role"), Json.text(message, "profile"), robotId, kind, message.get("entry"));
            }
        };
    }

    public synchronized ObjectNode json() {
        ObjectNode out = Json.obj();
        out.put("id", id);
        out.put("kind", kind);
        if (robotId == null) out.putNull("robot_id"); else out.put("robot_id", robotId);
        out.put("status", status);
        ArrayNode lines = Json.arr();
        progress.forEach(lines::add);
        out.set("progress", lines);
        out.set("result", result == null ? Json.MAPPER.nullNode() : result);
        if (error == null) out.putNull("error"); else out.put("error", error);
        if (errorCode == null) out.putNull("error_code"); else out.put("error_code", errorCode);
        out.put("started", started);
        return out;
    }

    /** json() plus {subject, finished}; what the job list routes return. */
    public synchronized ObjectNode summary() {
        ObjectNode out = json();
        if (subject == null) out.putNull("subject"); else out.put("subject", subject);
        if (finished == null) out.putNull("finished"); else out.put("finished", finished);
        return out;
    }
}
