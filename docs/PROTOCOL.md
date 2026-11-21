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
    "roles": {"spec": {"id": "gemini_pro", "kind": "gemini", "model": "gemini-3.1-pro-preview", "effort": "",
                       "timeout_s": 240, "cli_path": "", "api_key": null}, "...": "all 9 roles"},
    "profile": null
  },
  "context": {"project_id": "p_ab12cd34ef", "task": "design"},
  "out_dir": "/abs/path/runtime/tmp/jobs/<job_id>"
}
```
- `llm.roles`: every role (spec, scene, robot, revise, search, extract, analysis, objects, ask, checks) resolved by Java to its profile
  dict. `api_key` is set only for keyed providers (gemini): the saved key, else the `GEMINI_API_KEY` env fallback.
  Keys travel only on stdin: never in argv, env, logs or stdout.
- `llm.profile`: a single profile dict (only for `llm_test`).
- Profile kinds: `gemini` (keyed), `vertex_gemini` (Gemini on Vertex AI, billed to a Google Cloud project; no key: the engine gets an OAuth access
  token from the metadata server on Cloud Run or from `gcloud` locally; profile fields `location` (default `global`) and `project` (optional),
  `effort`, `timeout_s`; new installs on Cloud Run (`K_SERVICE`) or with `STUDIO_LLM_DEFAULT=vertex` default every role to `vertex_pro`/`vertex_flash`/`vertex_flash_lite`), `gemini_cli`, `antigravity_cli` (keyless CLIs signed in on the machine; text only, no
  `api_key`) and `claude_cli`. `antigravity_cli` models: `gemini-3.8-flash-high|medium|low`; `effort` becomes `--effort`;
  `cli_path` defaults to `agy` on PATH, then `~/.gemini/bin/agy`.
- Profile kind `scripted` (tests only): `{"kind": "scripted", "fixture": "/abs/rules.json"}`. It is accepted only
  when the Python process env has `STUDIO_ALLOW_SCRIPTED=1`. The fixture is `{"rules": [{"match": "text in system
  prompt or input", "answers": [<obj|str>, ...]}]}`. Answers are consumed in order, and the last one repeats. Every
  call is appended to `out_dir/llm_calls.jsonl` as `{role, match, system_prompt, input, images}` (`images` = number of images sent, 0 for text calls).
  A rule may carry `"delay_s"` (0..30 seconds): the fixture adapter sleeps that long before answering (to hold a job open in tests).

## Response (stdout, JSON lines; anything else on stdout must be ignored by Java)
At start, Python dups the real stdout fd for protocol lines and points `sys.stdout` at stderr, so a stray print
can't corrupt the stream. Line types:
- `{"type":"progress","text":"Running the task: 12 waypoints"}`
- `{"type":"usage","role":"spec","profile":"gemini_pro","entry":{...adapter usage entry, pages_opened removed...}}`
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
- Kill = the process plus all `ProcessHandle.descendants()` (the Claude CLI or Gemini CLI is a grandchild).
- Keep usage lines received before a kill.

## Tasks
All objects use the JSON shapes of `server/studio/schemas.py` (DesignVersion, Candidate, Analysis, ...). Java
assigns `version`, `parent_version`, `created` and `approved` when it commits a DesignVersion. Python fills
placeholders (`version: 0`).

| task | input | result |
|---|---|---|
| `meta` | `{}` | `{families: [...describe()], kinds, models: {kind: [..]}, roles, default_roles, default_profiles}` |
| `design` | `{brief, reference: {text, files: [{path, kind: image\|urdf}]} \| null, answers, round, comments?, notes?, answer_images?, brief_images?}` | `{status: "questions", questions, round}` or `{status: "designed", version: DesignVersion, parsed}` |
| `revise` | `{base: DesignVersion, request, t: number \| null}` | `{version: DesignVersion, parsed}` (`parent_version` = base.version) |
| `find` | `{version: DesignVersion, limit: 6}` | `{candidates: [Candidate], dropped: {reason: n}}` (images filled) |
| `analyse` | `{version: DesignVersion, candidate: Candidate, datasheet_url: str \| null, document_path: str \| null}` | `{analysis: Analysis, parsed, document: {file: "document.txt", url, title} \| null}`. A failed download is error code `datasheet_download`. |
| `fleet_assign` | `{version: DesignVersion, limits: FleetLimits, assignment: FleetAssignment \| null}` | `{assignment, preview: {bases, handovers, parks, workspace_samples}, feasible: bool, notes: [str]}`. No LLM, runs synchronously; an invalid user assignment is error kind `value`. |
| `fleet_design` | `{base: DesignVersion, limits: FleetLimits, assignment: FleetAssignment, primary?, trigger?, auto_repair?}` | `{version: DesignVersion (with fleet), parsed, fleet_parsed: {rid: parsed}}` (`parent_version` = base.version). LLM job. Optional inputs: `primary` (member id whose design fills the version's top-level robot, scene and files; default `r1`, else the first member, and an unknown id falls back the same way), `trigger` (list of strings recorded as `fleet.trigger`, why the fleet was planned; default the base fleet's trigger, else `["user"]`), `auto_repair` (bool; run the automatic collision repair on the new version; default true). |
| `studio_build` | `{version: DesignVersion, member: str \| null, base_build: StudioBuild \| null, request: str, pinned: {joint: alternative_id}, build: int}` | `{build: StudioBuild, parsed}` plus files in out_dir: `assembly.step`, `parts/<id>.step`, `parts/<id>.stl`, `urdf/robot.urdf`, `urdf/meshes/<link>.stl`. LLM job (search role with web for shops, robot role for the structure style). With `base_build` and only `pinned` changed, the shop search is not repeated: alternatives come from the base build. `build` is the number Java will store it as. Planned (Robot studio); see `docs/STUDIO_ROBOT_DESIGN.md`. |
| `studio_twin_move` | `{build: StudioBuild, version: DesignVersion, request: TwinMoveRequest}` | `TwinMoveResult`. No LLM, synchronous, deterministic. The twin rebuilds its kinematic chain from the design version, so `version` is required (as for `studio_run_task`). Planned. |
| `studio_run_task` | `{build: StudioBuild, version: DesignVersion, speed_scale: number}` | `TwinMoveResult` (the design's waypoints replayed under the build's limits). No LLM, synchronous. Planned. |
| `studio_pdf` | `{build: StudioBuild, version: DesignVersion, project: {id, title, brief}, build_dir: str, snapshots: [{view: iso\|front\|side\|top, path}]}` | `{pdf: "manufacturer.pdf", package: "package.zip", files: [BuildFile]}` written to out_dir, together with `bom.csv` (`files` lists the PDF, `bom.csv` and the package). `build_dir` is only read: its `assembly.step`, `parts/` and `urdf/` are staged with `bom.csv` in `out_dir/_stage` (removed afterwards) so that the package holds `bom.csv`. Job (no LLM). Planned. |
| `inspect_upload` | `{path, kind: urdf\|datasheet\|image}` | `{ok, summary \| text_chars \| mime}` (validates an upload; a bad file is a value error) |
| `export_assets` | `{urdf, want: ["parsed","glb","stl"]}` | `{parsed?}` plus files `design.glb` / `design.stl` in out_dir |
| `ask` | `{question, history: [{role, text}] (at most 6), context: {project_id?, stage?, version?, t?, candidate?, build?, selection?}, grounding: {project, version?, build?, candidate?}}` | `{answer, citations: [{kind, ref, label}], confirm: [{kind, label, request}], navigate: {stage, version?, build?, candidate?} \| null, proposals: [{kind, args}]}`. LLM job, role `ask`. It never performs an action: the tools only record `proposals` (at most 2, kinds `revise`, `design`, `search`, `analyse`, `studio_build`, `update_brief`, `set_baseline`, `cancel_job`) and answer `{"queued": true}`. Java builds `grounding` from stored data, validates the proposals after the result arrives and runs them through its own service methods; `proposals` is removed from the job result. |
| `llm_test` | `{}` (uses `llm.profile`) | `{ok, answer, seconds}` |

Fleet additions: `design` and `revise` results may carry `fleet_advice: {robots_needed, reasons: [str], source: "spec" \| "code" \| "both"}`
(only when one robot may not be enough); `revise` results may carry `fleet_parsed: {rid: parsed}` when the new version has a fleet.
`find` and `analyse` inputs may carry `member: str` (a fleet member id; the version's member is then analysed). A fleet
version is an ordinary DesignVersion for its primary member plus an optional `fleet` object (FleetPlan).

Clarifications (as implemented in `server/studio/tasks/`):
- `design.input.round` is the number of spec rounds already completed (project.json `round`). Python runs round+1
  and returns it as `round` in both result kinds.
- `design.input.answers` must already be merged (Java merges new answers into the project's answers).
- Optional `design.input.spec` skips the spec round, for retries.
- The `objects` role (default profile `gemini_flash_lite`) makes `scene.objects[].model` (primitives, with `name`) for each task object, concurrently with the scene and robot agents. It is never fatal: on failure the keys stay absent and no progress line is written; on success one line `Object models: <n> made`. `revise` reuses models of unchanged objects (same name, size, shape); fleet versions keep the base version's models.
- The `checks` role (default profile `gemini_pro`) plans task-specific checks after the simulation and before the collision repair, in `design` and in `revise` (a repair round re-evaluates the stored plan in code instead of asking again). Its plans are formulas over a code-built catalogue of named quantities; code evaluates them with units and appends them to `sim.checks` as `Check`s with `source: "planned"` (they count toward `sim.ok`; the client and the PDF label them "AI-planned, code-computed"). The accepted plans are stored in `version.check_plan` and the rejected ones in `version.check_plan_rejected` (`[{id, reason}]`); both keys are absent when empty, and `Check.source` is absent when `"sim"`. One progress line `Task-specific checks: <n> planned` on success; when the call itself fails (no adapter, an LLM error, a timeout) no checks, no line and no change. A revised design passes the previous plan to the agent as a hint; when the agent fails on a revise the previous plan is re-evaluated in code.
- Optional `design.input.brief_images` (`[{path, name}]`, absolute paths of files validated by `inspect_upload`) are photos
  of the task, objects or workcell. They go to the spec call labelled `brief:<name>`, before the reference and answer
  images, and count toward the same cap of 8 (brief first); dropped with a progress line for adapters without image support.
- Optional `design.input.comments` (`{question_id: text}`, cumulative), `notes` (`[{round, text}]`, one general note
  per round) and `answer_images` (`[{path, question: question id | "_general"}]`, files already validated by
  `inspect_upload`) are the user's additions to their answers. They go to the spec call only (`answer_comments`,
  `general_notes`, `answer_attachments` in its payload, images after the reference photos). Reference plus answer
  images are capped at 8 (extras dropped with a progress line); an adapter without image support drops all images
  with one progress line and still gets the comments. Absent keys change nothing.
- `analyse` takes an optional `document_source` text (default "uploaded document"). The result's `document` is
  non-null only when the task downloaded the document itself; with `document_path` it is null.
- `KeyError` maps to `not_found` only when its message says "not found"; otherwise `internal`.
- `ask` (the Ask bar): the `grounding` is the only source of facts (project, the chosen version without frames, the build without geometry, a candidate and its analysis, at most about 60 KB). The agent runs `llm/tools.py run_tools` with one recording tool per action kind. `approve`, `approve_build` and `decision` are not tools: they are `confirm` entries (`approve` `{version}`, `approve_build` `{build}`, `decision` `{action: self, build?}` or `{action: approve|robot_studio, candidate_id}`). Citations that do not exist in the grounding are dropped in Python (and again by Java). A third recorded action is refused by the tool (`limit: 2 actions per question`); Java refuses extras too.
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
- `GET /api/v1/openapi.json` (the bundled OpenAPI spec, `contracts/openapi.bundled.json`; the spec is `contracts/openapi.yaml`).

The server binds to 127.0.0.1. A non-loopback `STUDIO_HOST` requires `STUDIO_TOKEN`, and then every `/api/v1`
call needs `Authorization: Bearer <token>`.
