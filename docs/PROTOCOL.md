# Java ↔ Python task protocol (contract)

Java (Spring Boot, `server-java/`) owns HTTP, jobs, the project store, LLM settings and the usage ledger. Python
(`server/studio/`) does the AI and numeric work, one subprocess per job:

    <python> -m studio.tasks <task>        (cwd = repo root, PYTHONPATH=server)

Python never writes `runtime/` except inside the job's `out_dir`. Java is the only writer of `runtime/projects/**`
and `runtime/llm/**`.

## Request (stdin, one JSON object, then stdin is closed)
```json
{
  "protocol": 1,
  "task": "design",
  "input": { ...task-specific, see below... },
  "llm": {
    "roles": {"spec": {"id": "claude_opus", "kind": "claude_cli", "model": "claude-opus-5-5", "effort": "",
                       "timeout_s": 240, "cli_path": "", "api_key": null}, "...": "all 7 roles"},
    "profile": null
  },
  "context": {"project_id": "p_ab12cd34ef", "task": "design"},
  "out_dir": "/abs/path/runtime/tmp/jobs/<job_id>"
}
```
- `llm.roles`: every role (spec, scene, robot, revise, search, extract, analysis) resolved by Java to its profile
  dict. `api_key` is set only for keyed providers (gemini): the saved key, else the `GEMINI_API_KEY` env fallback.
  Keys travel only on stdin: never in argv, env, logs or stdout.
- `llm.profile`: a single profile dict (only for `llm_test`).
- Profile kind `scripted` (tests only): `{"kind": "scripted", "fixture": "/abs/rules.json"}`. It is accepted only
  when the Python process env has `STUDIO_ALLOW_SCRIPTED=1`. The fixture is `{"rules": [{"match": "text in system
  prompt or input", "answers": [<obj|str>, ...]}]}`. Answers are consumed in order, and the last one repeats. Every
  call is appended to `out_dir/llm_calls.jsonl` as `{role, match, system_prompt, input, images}` (`images` = number of images sent, 0 for text calls).
  A rule may carry `"delay_s"` (0..30 seconds): the fixture adapter sleeps that long before answering (to hold a job open in tests).

## Response (stdout, JSON lines; anything else on stdout must be ignored by Java)
At start, Python dups the real stdout fd for protocol lines and points `sys.stdout` at stderr, so a stray print
can't corrupt the stream. Line types:
- `{"type":"progress","text":"Running the task: 12 waypoints"}`
- `{"type":"usage","role":"spec","profile":"claude_opus","entry":{...adapter usage entry, pages_opened removed...}}`
  Java appends `{at, role, profile, robot_id: context.project_id, task: context.task, ...entry}` to
  `runtime/llm/usage.jsonl`, the same line format as before.
- `{"type":"heartbeat"}` every 15 s while running.
- `{"type":"result","data":{...}}` or `{"type":"result","file":"result.json"}` (relative to `out_dir`; used when
  the data is above ~256 KB). Exactly one, then exit 0.
- `{"type":"error","kind":"value|not_found|llm|internal","code":"datasheet_download|...|null","message":"..."}`
  then exit 1. Java maps value→422, not_found→404, llm→502, internal→500. The body is `{"detail": message}`, and
  the job record carries `error_code`.

stderr is free text. Java writes it to `runtime/logs/jobs/<job_id>.log` and never echoes the request.

## Java runner rules
- Drain stdout and stderr on separate threads.
- Idle watchdog: no line for 60 s means kill. Ceiling 45 min.
- At most 4 Python processes at once (semaphore).
- Kill = the process plus all `ProcessHandle.descendants()` (the Claude CLI is a grandchild).
- Keep usage lines received before a kill.

## Tasks
All objects use the JSON shapes of `server/studio/schemas.py` (DesignVersion, Candidate, Analysis, ...). Java
assigns `version`, `parent_version`, `created` and `approved` when it commits a DesignVersion. Python fills
placeholders (`version: 0`).

| task | input | result |
|---|---|---|
| `meta` | `{}` | `{families: [...describe()], kinds, models: {kind: [..]}, roles, default_roles, default_profiles}` |
| `design` | `{brief, reference: {text, files: [{path, kind: image\|urdf}]} \| null, answers, round, comments?, notes?, answer_images?}` | `{status: "questions", questions, round}` or `{status: "designed", version: DesignVersion, parsed}` |
| `revise` | `{base: DesignVersion, request, t: number \| null}` | `{version: DesignVersion, parsed}` (`parent_version` = base.version) |
| `find` | `{version: DesignVersion, limit: 6}` | `{candidates: [Candidate], dropped: {reason: n}}` (images filled) |
| `analyse` | `{version: DesignVersion, candidate: Candidate, datasheet_url: str \| null, document_path: str \| null}` | `{analysis: Analysis, parsed, document: {file: "document.txt", url, title} \| null}`. A failed download is error code `datasheet_download`. |
| `inspect_upload` | `{path, kind: urdf\|datasheet\|image}` | `{ok, summary \| text_chars \| mime}` (validates an upload; a bad file is a value error) |
| `export_assets` | `{urdf, want: ["parsed","glb","stl"]}` | `{parsed?}` plus files `design.glb` / `design.stl` in out_dir |
| `llm_test` | `{}` (uses `llm.profile`) | `{ok, answer, seconds}` |

Clarifications (as implemented in `server/studio/tasks/`):
- `design.input.round` is the number of spec rounds already completed (project.json `round`). Python runs round+1
  and returns it as `round` in both result kinds.
- `design.input.answers` must already be merged (Java merges new answers into the project's answers).
- Optional `design.input.spec` skips the spec round, for retries.
- Optional `design.input.comments` (`{question_id: text}`, cumulative), `notes` (`[{round, text}]`, one general note
  per round) and `answer_images` (`[{path, question: question id | "_general"}]`, files already validated by
  `inspect_upload`) are the user's additions to their answers. They go to the spec call only (`answer_comments`,
  `general_notes`, `answer_attachments` in its payload, images after the reference photos). Reference plus answer
  images are capped at 8 (extras dropped with a progress line); an adapter without image support drops all images
  with one progress line and still gets the comments. Absent keys change nothing.
- `analyse` takes an optional `document_source` text (default "uploaded document"). The result's `document` is
  non-null only when the task downloaded the document itself; with `document_path` it is null.
- `KeyError` maps to `not_found` only when its message says "not found"; otherwise `internal`.
- Scripted fixture answers are shared by all roles using the same fixture file within one process.
- `llm_test` may add `images` and `image_seconds`; `export_assets` adds `files: [...]`.
- `STUDIO_HEARTBEAT_S` overrides the 15 s heartbeat (tests). Error and progress text is redacted of API keys.

`revise` context rule: Python builds the model's view from `input.base`, never from numbers the client sent. The
view holds spec, scene, robot family and params with units, capabilities (`family.capabilities`), requirement
targets, every check, and when `t` is given, the frame nearest `t` (t, phase, joints, tcp, holding, base) plus
events within ±0.5 s.

## Store layout (unchanged, Java writes)
`runtime/projects/<id>/project.json`, `versions/v<n>.json` (+ sidecar `v<n>.parsed.json`, `v<n>.glb` cache),
`reference/{text.md, images/, urdf/...}`, `candidates.json`, `documents/<cid>.txt`, `analysis/<cid>.json`
(+ `.parsed.json`), decision inside `project.json`. Every JSON file Java writes gets `"schemaVersion": 1` at the
top level (Python ignores unknown keys).

## REST
The routes are under `/api/v1` with the same bodies and responses as the former FastAPI app (removed at the
cut-over), plus:
- `POST /projects/{id}/revise {request, base_version, t}`, which answers 422 for an unknown base or a `t` outside
  0..duration;
- `GET /projects/{id}/versions/{n}/assets/{design.urdf|design.glb|frames.json}`;
- `GET /v3/api-docs` (springdoc OpenAPI).

The server binds to 127.0.0.1. A non-loopback `STUDIO_HOST` requires `STUDIO_TOKEN`, and then every `/api/v1`
call needs `Authorization: Bearer <token>`.
