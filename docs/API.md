# RoboDE Studio API

This guide is for developers who call RoboDE Studio from another app. The full spec is `contracts/openapi.yaml`.
The served copy is `contracts/openapi.bundled.json`. Read the spec for exact fields and schemas. This guide shows how the
pieces fit together.

## What you can call

Base URL: `http://127.0.0.1:8800/api/v1`

The OpenAPI document is served at `GET /api/v1/openapi.json`. To make an SDK in another language, generate it from
`contracts/openapi.bundled.json`.

| Area | Routes |
|---|---|
| Meta | `GET /health`, `GET /families`, `GET /openapi.json` |
| Projects | `GET /projects`, `POST /projects`, `GET /projects/{pid}`, `DELETE /projects/{pid}` |
| Reference | `PUT /projects/{pid}/reference`, `POST /projects/{pid}/reference/files?filename=`, `DELETE /projects/{pid}/reference` |
| Questions and design | `POST /projects/{pid}/design`, `POST /projects/{pid}/answers/files?filename=&question=`, `GET` and `DELETE /projects/{pid}/answers/files/{name}` |
| Versions | `GET /projects/{pid}/versions/{n}`, `GET /projects/{pid}/versions/{n}/assets/{design.urdf\|design.glb\|frames.json}`, `POST /projects/{pid}/approve` |
| Revise | `POST /projects/{pid}/revise` |
| Fleet | `PUT /projects/{pid}/fleet`, `POST /projects/{pid}/fleet/assign`, `POST /projects/{pid}/fleet/design` |
| Catalog | `POST /projects/{pid}/search`, `GET /projects/{pid}/candidates`, `POST /projects/{pid}/candidates/{cid}/analyse`, `POST /projects/{pid}/candidates/{cid}/datasheet?filename=`, `GET /projects/{pid}/analysis/{cid}` |
| Decision and export | `POST /projects/{pid}/decision`, `GET /projects/{pid}/export` |
| Jobs | `GET /jobs`, `GET /jobs/{id}`, `DELETE /jobs/{id}`, `GET /projects/{pid}/jobs` |
| LLM settings and usage | `GET /llm`, `POST /llm/profiles`, `PUT` and `DELETE /llm/profiles/{id}`, `POST /llm/profiles/{id}/test`, `PUT /llm/roles`, `PUT` and `DELETE /llm/keys/{provider}`, `GET /llm/usage?period=`, `GET /usage`, `POST /llm/usage/reset` |

### Versioning

`/api/v1` changes only in an additive way. New routes, new optional request fields and new response fields can appear.
No route, field or enum value is removed, and no meaning changes. Ignore response fields you do not know. Breaking
changes go to `/api/v2`.

## Auth

- Callers on the loopback address (`127.0.0.1`) need no credentials by default.
- If the server sets `STUDIO_TOKEN`, every call needs the header `Authorization: Bearer <token>`.
- A server bound to any other address must have `STUDIO_TOKEN`. The server will not start without it.
- A missing or wrong token gets `401`.

```bash
curl -s "$BASE/health" -H "Authorization: Bearer $STUDIO_TOKEN"
```

## Getting results

Long work runs as a job. A design, revise, fleet design, search, analysis or LLM test is a job. There are three ways to
get the result.

### 1. Synchronous: `wait` and `expand`

Add `wait=<seconds>` to the start call, or to `GET /jobs/{id}`. Add `expand=resource` to get the result as a full
resource.

- If the job ends in time, you get `200` with the final job record.
- If it is still running, you get `202` with the running record and a `Location: /api/v1/jobs/{id}` header.

`wait` takes whole seconds from 0 to 600. The default is 0, which returns at once. A value outside that range gets `422`.
`expand` accepts only `resource`. Any other value gets `422`.

```bash
curl -s -X POST "$BASE/projects/$PID/design?wait=600&expand=resource" \
  -H "Content-Type: application/json" -d '{"answers": {}}'
```

With `expand=resource`, the `resource` field holds:

| Job | `resource` |
|---|---|
| design that produced a version, revise, fleet design | the version view (same as `GET /versions/{n}`) |
| design that asks questions | `{questions, round}` |
| search | the candidates (a JSON list, not `{candidates: [...]}`) |
| analysis | the analysis view (same as `GET /analysis/{cid}`) |

### 2. Polling

Start the job without `wait`. You get `200` with a record whose `status` is `running`. Then poll:

```bash
curl -s "$BASE/jobs/$JOB?wait=60&expand=resource"
```

Stop when `status` is `done`, `error` or `cancelled`. A job that fails still answers with HTTP `200`. Read its `error`
and `error_code` fields.

### 3. Callbacks

Send the header `X-Studio-Callback: <url>` on any job-starting POST. When the job ends, Studio POSTs the expanded final
record to that URL.

```bash
curl -s -X POST "$BASE/projects/$PID/search" \
  -H "X-Studio-Callback: http://localhost:9000/studio-hook"
```

Rules for callbacks:

- Only `http` and `https` URLs are allowed.
- The host must be in `STUDIO_CALLBACK_HOSTS` (comma list). The default is `localhost,127.0.0.1,::1`. Any other host
  gets `422`, and nothing starts.
- Each request has these headers:
  - `X-Studio-Job: <job id>`
  - `X-Studio-Event: job.finished`
  - `X-Studio-Signature: sha256=<hex>`, only when `STUDIO_CALLBACK_SECRET` is set. The value is an HMAC-SHA256 of the
    raw body with that secret.
- The first attempt goes out at once. If it fails (a network error or a non-2xx answer), Studio retries after 1 s, 5 s
  and 25 s. That is at most 4 attempts in all.
- A failed delivery never changes the job.
- The job record shows `callback: {url, delivered, attempts}` only when you set a callback.
- A joined search or analysis keeps only the first callback URL.

Verify the signature with the raw body bytes. Do not parse and re-serialize the JSON first.

```python
import hashlib
import hmac


def callback_is_genuine(secret: bytes, raw_body: bytes, signature_header: str) -> bool:
    expected = "sha256=" + hmac.new(secret, raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header or "")
```

### Cancel

```bash
curl -s -X DELETE "$BASE/jobs/$JOB"
```

- `200`: the job is now `cancelled`, with `error: "Cancelled by the caller"` and `error_code: "cancelled"`.
- `409`: the job has already finished.
- `404`: the job is unknown.

The call waits up to 10 s for the task's process tree to stop. Model usage streamed before the cancel is kept. Nothing from
a cancelled job is saved to the project, and the project lock is released. If a task finishes at the same moment as the
cancel, it may already have saved its result. The record still says `cancelled`.

### Idempotency-Key

Send `Idempotency-Key: <1 to 200 characters>` on `POST /projects` and on every job-starting POST.

- The same key with the same body replays the first answer. You get the same project or the same job id.
- A replayed `202` stays `202`.
- The same key with a different body gets `422`: `Idempotency-Key reused with a different request`.
- Keys are kept in memory for 24 hours, per key, method and path. A server restart forgets them.
- Responses with status 500 or higher are not stored.
- An empty key, or one over 200 characters, gets `422`.

## Walkthrough with curl

Set the base URL once. Add the token header if the server needs one.

```bash
export BASE=http://127.0.0.1:8800/api/v1
```

The answers below are examples. Your ids and values will differ.

### 1. Create a project

```bash
curl -s -X POST "$BASE/projects" -H "Content-Type: application/json" \
  -H "Idempotency-Key: bottle-demo-1" \
  -d '{"brief": "Move a 0.3 kg bottle from table A to table B"}'
```

Keep the `id` (for example `p_ab12cd34ef`). Set it as `PID`.

### 2. Add a reference

Text:

```bash
curl -s -X PUT "$BASE/projects/$PID/reference" -H "Content-Type: application/json" \
  -d '{"text": "A bench-top task. Both tables are 0.75 m high."}'
```

A photo (PNG, JPEG or WebP, at most 6 per project). URL-encode the file name if it has spaces:

```bash
curl -s -X POST "$BASE/projects/$PID/reference/files?filename=bench.png" \
  -H "Content-Type: application/octet-stream" --data-binary @bench.png
```

A URDF or ZIP with a URDF. It replaces the previous one:

```bash
curl -s -X POST "$BASE/projects/$PID/reference/files?filename=cell.urdf" \
  -H "Content-Type: application/octet-stream" --data-binary @cell.urdf
```

Remove the whole reference (text, photos and URDF):

```bash
curl -s -X DELETE "$BASE/projects/$PID/reference"
```

### 3. Design: the first round of questions

```bash
curl -s -X POST "$BASE/projects/$PID/design?wait=600&expand=resource" \
  -H "Content-Type: application/json" -d '{"answers": {}}'
```

The answer has `result.status: "questions"` and a list of questions. Each question has an `id`, a `text`, a `kind`
(`choice`, `number`, `text` or `boolean`), an optional `unit`, and a `default`. This is a trimmed example:

```json
{"status": "done", "result": {"status": "questions", "round": 1,
  "questions": [{"id": "cycle", "text": "Cycle time?", "kind": "number", "unit": "s", "default": 6}]},
  "resource": {"round": 1, "questions": [...]}}
```

The AI asks at most six questions per round, and at most three rounds.

### 4. Answer

Send the answers by question id. You can also send one comment per question, and one general note for the round.

```bash
curl -s -X POST "$BASE/projects/$PID/design?wait=600&expand=resource" \
  -H "Content-Type: application/json" \
  -d '{"answers": {"cycle": 6},
       "comments": {"cycle": "Two cycles a minute is the goal."},
       "note": "The bottle is plastic and half full."}'
```

Rules for answers:

- Answers are merged into the project's answers. Send only the new ones, or send them all.
- A comment is a string of up to 1000 characters. The key must match a question id (lower case letters, digits and
  underscore, at most 41 characters). An empty string removes a comment.
- The note is a string of up to 4000 characters. Send one note per round.

To attach an image to a question, upload it before the next design call. Use the question id, or `_general` for the
round's note:

```bash
curl -s -X POST "$BASE/projects/$PID/answers/files?filename=bottle.jpg&question=cycle" \
  -H "Content-Type: application/octet-stream" --data-binary @bottle.jpg
```

The answer is the list of answer images for the project. Each item has `name`, `question`, `round` and `mime`.

```bash
curl -s "$BASE/projects/$PID/answers/files/bottle.jpg" -o bottle-copy.jpg   # download
curl -s -X DELETE "$BASE/projects/$PID/answers/files/bottle.jpg"             # remove
```

Reference photos and answer images share one budget of 8 images per design round.

### 5. Designed: the first version

When the AI has enough answers, the design job answers `result.status: "designed"` and `result.version`. With
`expand=resource`, the `resource` is the version. You can also read it later:

```bash
curl -s "$BASE/projects/$PID/versions/1"
```

A version holds:

- `spec`: the technical spec (task, actions, objects, markers, frame).
- `requirements`: each requirement with its limit and its calculation (`calc`).
- `scene`: the boxes, objects and markers of the scene.
- `robot`: `family`, `name`, `rationale`, `params` (with units in the family's schema) and `base`.
- `urdf`: the robot description as XML.
- `sim`: `frames`, `events`, `checks`, `duration_s` and `ok`.
- `repair`: the log of the automatic collision repair, or `null`.
- `parsed`: the parsed URDF (links, joints and their limits). Only in the version view.
- `parent_version`, `change_request`, `change_summary`, `approved` and `fleet` (when a fleet exists).

### 6. Download the assets

```bash
curl -s "$BASE/projects/$PID/versions/1/assets/design.urdf" -o design.urdf
curl -s "$BASE/projects/$PID/versions/1/assets/design.glb" -o design.glb
curl -s "$BASE/projects/$PID/versions/1/assets/frames.json"
```

`design.glb` is a zero-pose mesh in glTF binary, y up. `frames.json` is the simulated frames as a JSON list.

### 7. Revise from a version at a moment

Pick the version to change (`base_version`, default the latest). Pick a moment of its simulation with `t`, in seconds
from 0 up to the version's `sim.duration_s`. `t` can be `null`. An out-of-range `t` gets `422`.

```bash
curl -s -X POST "$BASE/projects/$PID/revise?wait=600&expand=resource" \
  -H "Content-Type: application/json" \
  -d '{"request": "Make the arm 5 cm longer", "base_version": 1, "t": 3.5, "auto_repair": true}'
```

The new version has `parent_version` set to the base. `auto_repair` runs the automatic collision repair. It defaults to
`true`. Revise is blocked with `409` once a version is approved.

### 8. Fleet (optional)

Turn the fleet on and set its limits. `null` clears a limit. Send at least one field.

```bash
curl -s -X PUT "$BASE/projects/$PID/fleet" -H "Content-Type: application/json" \
  -d '{"enabled": true, "limits": {"max_robots": 3, "max_reach_m": 1.3}}'
```

Other limits: `max_payload_kg`, `budget_usd`, `families`, `brands` and `note`. `max_robots` is 2 or 3.

Preview an assignment. This call runs at once and needs no job. Without an `assignment`, the code proposes one. The
answer holds the assignment, a preview (bases, handovers, parks, workspace samples), `feasible` and notes.

```bash
curl -s -X POST "$BASE/projects/$PID/fleet/assign" -H "Content-Type: application/json" -d '{}'
```

Plan the fleet as a new version. This needs a stored assignment. It returns `409` when none exists or when the design is
approved.

```bash
curl -s -X POST "$BASE/projects/$PID/fleet/design?wait=600&expand=resource" \
  -H "Content-Type: application/json" -d '{"base_version": 1}'
```

Choose the member that robot search uses:

```bash
curl -s -X PUT "$BASE/projects/$PID/fleet" -H "Content-Type: application/json" -d '{"member": "r2"}'
```

### 9. Approve

Approval freezes the design.

```bash
curl -s -X POST "$BASE/projects/$PID/approve" -H "Content-Type: application/json" -d '{"version": 2}'
```

After approval:

- revise and fleet design answer `409`;
- approving a different version answers `409`;
- approving the same version again does nothing and answers `200`.

### 10. Search for real robots

Search needs an approved design.

```bash
curl -s -X POST "$BASE/projects/$PID/search?wait=600&expand=resource"
curl -s "$BASE/projects/$PID/candidates"
```

Each candidate has `id`, `manufacturer`, `model`, `family`, `product_url`, `image_url`, `datasheet_url`, `price`,
`payload_kg`, `reach_m`, `dof`, `repeatability_mm`, `key_specs`, `confidence` and `screen`. `price` is a low and high
estimate with a source and a date, or `null`.

If a search of the project is already running, you get that running job back.

### 11. Analyse a candidate

The datasheet comes from, in this order:

1. `datasheet_url` in the request body (https only);
2. a datasheet you uploaded for this candidate;
3. the candidate's own datasheet link;
4. the candidate's product page.

```bash
curl -s -X POST "$BASE/projects/$PID/candidates/acme_ra_6/analyse?wait=600&expand=resource" \
  -H "Content-Type: application/json" -d '{"datasheet_url": "https://example.com/ra6-datasheet.pdf"}'
```

Upload a datasheet yourself when the download fails. Send the raw bytes of a PDF, HTML or text file. The next analysis
of that candidate uses it when there is no `datasheet_url`.

```bash
curl -s -X POST "$BASE/projects/$PID/candidates/acme_ra_6/datasheet?filename=ra6.pdf" \
  -H "Content-Type: application/octet-stream" --data-binary @ra6.pdf
```

The analysis holds `manufacturer_spec` (values with their quotes and page), `robot` (the candidate as a robot concept),
`urdf`, `sim` (the same task simulated on the candidate), `differences` (each with `topic`, `generated`, `candidate`,
`impact` and `severity`), `unverified` and `provenance`. Read it at any time:

```bash
curl -s "$BASE/projects/$PID/analysis/acme_ra_6"
```

A candidate in a family the designer does not know, such as `other`, fails at once with a plain message. No download
or model call happens. A failed download ends the job with `error_code: datasheet_download`.

### 12. Record the decision

```bash
curl -s -X POST "$BASE/projects/$PID/decision" -H "Content-Type: application/json" \
  -d '{"candidate_id": "acme_ra_6", "action": "approve"}'
```

`action` is `approve` or `robot_studio`. Any other value gets `422`.

### 13. Export

```bash
curl -s "$BASE/projects/$PID/export" -o export.zip
```

The export holds the approved version, or the latest one when nothing is approved. It contains:

- `design.urdf`, `design.glb`, `spec.json`, `requirements.json`, `scene.json`, `sim_summary.json` and `README.txt`;
- `decision.json`, once a decision exists;
- for each analysed candidate: `candidates/<id>/analysis.json`, `manufacturer_values.json` and `candidate.urdf`;
- for a fleet: `fleet.json` and `members/<robot id>/design.urdf`.

The README in the ZIP lists the limits of the results. Read it before you show results to people.

### 14. Jobs

```bash
curl -s "$BASE/projects/$PID/jobs"   # running first, then finished (newest first), at most 20
curl -s "$BASE/jobs"                # running jobs of all projects
curl -s "$BASE/jobs/$JOB"            # one job
```

Jobs are kept in memory, with the last finished ones. A job id is unknown after a server restart.

### 15. Language models and usage

Connections are global, not per project. API keys are never returned.

```bash
curl -s "$BASE/llm"                          # profiles, role assignment, key status
curl -s -X PUT "$BASE/llm/roles" -H "Content-Type: application/json" \
  -d '{"roles": {"spec": "claude_opus", "extract": "claude_sonnet"}}'
curl -s -X PUT "$BASE/llm/keys/gemini" -H "Content-Type: application/json" \
  -d '{"key": "<your-key>"}'
curl -s "$BASE/llm/usage?period=7d"          # period: today, 7d, 30d or all
curl -s -X POST "$BASE/llm/profiles/claude_opus/test?wait=120&expand=resource"
```

A profile that a role uses cannot be deleted (`422`). Roles that are not in the body keep their profile.

## Errors

Every error body is `{"detail": "...", "error_code": "..."}`. `error_code` appears only when there is one.

| Status | Meaning |
|---|---|
| 401 | The token is missing or wrong. |
| 404 | Unknown project, version, candidate, job, route or image. |
| 409 | State conflict. The project is busy with a design, revise or fleet job. The design is approved and frozen. A job has already finished, so it cannot be cancelled. |
| 413 | The upload is larger than 64 MB. |
| 422 | Invalid input. Examples: `wait` outside 0 to 600, an unknown `expand`, a `t` outside the version's duration, an `Idempotency-Key` reused with a different body, a callback host not allowed. |
| 502 | The language model failed. |
| 500 | Something went wrong on the server. Retry later. Report it if it happens again. |

A job that fails keeps HTTP `200`. Its record has `status: "error"`, with `error` and `error_code`:

| `error_code` | Meaning |
|---|---|
| `datasheet_download` | Studio could not download the datasheet. Upload it, or give a `datasheet_url`. |
| `cancelled` | The job was cancelled with `DELETE /jobs/{id}`. |

Other codes can appear. Treat an unknown code as a plain failure, and show its `error` text.

## Limits

- Brief: 1 to 8000 characters.
- Reference text: at most 60000 characters.
- Comments: at most 1000 characters each. The note: at most 4000 characters.
- Revise request: 1 to 2000 characters.
- Uploads and the request body: at most 64 MB. A request that sends an `Idempotency-Key` is checked for up to 4 MB of body. A larger body gets `413`.
- Reference photos: at most 6 per project.
- Images per design round: 8, counting reference photos and answer images.
- `wait`: 0 to 600 seconds. A job can run for up to 45 minutes.
- One design, revise or fleet design job per project at a time.
- A search or an analysis of the same candidate that is already running returns that running job.
- The server runs at most 4 task processes at once.

## What the results mean

- The checks are kinematic and static. They cover reach, joint limits, static joint torques, speed, cycle time and coarse
  collisions. A check with `passed: null` is not decided. For a real robot, the manufacturer did not publish the value.
- There is no dynamics, no contact simulation and no controller.
- Nothing has been tested on hardware.
- Requirement numbers come from Studio's physics code and the robot family. The AI chose the task layout and the family.
  Check the assumptions in `spec.json`.
- A manufacturer value is `verified` only when its quote is found in the datasheet text. The other values are listed as
  `unverified`.
- Prices of real robots are web estimates. Each one has a source and a date. They change, so check them before you use
  them.

## Example script

`examples/api_flow.py` runs the whole flow with the standard library only. It answers each question with its default,
revises at a moment, approves, searches, analyses, records a decision and downloads the export.

```bash
python3 examples/api_flow.py --base http://127.0.0.1:8800/api/v1
python3 examples/api_flow.py --base http://127.0.0.1:8800/api/v1 --no-catalog
```

It reads the token from `--token` or from `$STUDIO_TOKEN`.
