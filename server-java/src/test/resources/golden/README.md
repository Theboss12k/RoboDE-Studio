# Golden HTTP responses (RoboDE Studio API)

These files are recorded responses of the pre-migration FastAPI server. They are contract fixtures for the Java
rewrite: a test replays each request and compares the status and the response structure and stable values.

## How they were made

- Date: 2026-11-20.
- Source: a backup copy of the pre-migration FastAPI app (`server/studio`), not the live repo.
- The app runs in-process with FastAPI's `TestClient` against a temporary runtime folder. Nothing is written
  outside that folder and the golden folder.
- LLM answers are **scripted fixtures**, not model output. Each role (spec, scene, robot, revise, search,
  extract, analysis) replays a prepared answer from the backup's `tests/` fixtures (bottle task A to B, the
  RA-6 arm datasheet, the search and value-extraction answers). No model was called and there was no network
  access.
- The datasheet download is a fixture for `https://acme.example/...`. The one real download check is the SSRF
  rule: `http://127.0.0.1/...` is rejected by the real `safe_download` before any connection.
- The spec role has a 0.3 s delay so that the concurrent-design 409 can be observed.
- Generator: `capture_goldens.py` in the backup folder (run with the live repo's venv).

## File format

Each file is one request, numbered in the order it was sent:

```json
{ "method": "GET", "path": "/api/projects/<project_id>", "request_body": null, "status": 200, "response_body": {} }
```

- `frames_count` is added at the top level when the body carries simulation frames (`sim.frames`), for version
  and analysis responses. The full frames stay in `response_body`.
- A job start (`POST ...`) is saved as-is. Its `status` may be `running` or `done`, depending on timing. Its
  `*_final.json` file (`GET /api/jobs/<job_id>`) holds the final record, and that record is the one to assert on.
  Intermediate polls are not saved.
- The export file stores `response_body = {status, content_type, zip_entries}` instead of the ZIP bytes.
  `zip_entries` is sorted.
- Upload requests (raw bytes) show `request_body` as `"<raw body: N bytes>"`.

## Placeholders

| Placeholder | Replaces |
|---|---|
| `<project_id>` | every project id (`p_` + 10 hex) |
| `<job_id>` | every job id (12 hex), in the job record, the `id` field and the path |
| `<profile_id>` | the LLM profile id created by the test |
| `<timestamp>` | every ISO-8601 time (`created`, `updated`, `started`) |
| `<date>` | the capture day, wherever it appears |
| `<uuid>` | any UUID |
| `<duration>` | numeric values under keys ending in `seconds`, and `duration_s` / `elapsed_s` (sim and LLM timings) |
| `<runtime>` | absolute temp runtime folders |
| `<path>` | any other absolute machine path |
| `<api_key>` | the key sent to `PUT /api/llm/keys/gemini` (a fake test value; it is not written to these files) |

Stable values are kept: candidate id `acme_ra_6`, versions, requirements, URDF, checks, prices, error text and
error codes.

## Flow order (main app, `01`-`59`)

1. Health, families, LLM settings, usage (`period=7d`, alias `/api/usage`, bad period 422), profile add (bad
   provider 422) / edit / roles / key save and delete / profile delete / usage reset.
2. Reference material on a separate project: upload `.exe` (422), upload PNG, clear.
3. Main project: create, list, get, reference text, design step 1 (questions) with a second design request
   during it (409), design step 2 (`designed` v1), get v1, revise to v2, v1 unchanged, v2, v99 (404).
4. Approve v2, approve v2 again (no-op), approve v1 (409), revise after approval (409), get v2 (approved).
5. Search, candidates, analyse `acme_ra_6` from the vendor datasheet URL, analysis, unknown candidate (404),
   project after analysis.
6. Decision with bad action (422), decision approve, export ZIP.
7. Error cases: unknown project (404), bad body (422), `..%2F..%2Fx` (404), unknown job (404), unknown route
   under `/api` (404).
8. Delete the project, get it again (404), delete the reference project.

Second app (`60`-`75`), using the real `safe_download` for non-vendor URLs:

9. Search before approval (job error "Approve a design version first"), export before design (422).
10. Design step 1 and 2, approve v1, search.
11. Analyse with `http://127.0.0.1/secret.pdf` (job error `datasheet_download`), upload the datasheet
    (`candidates/acme_ra_6/datasheet`), analyse with the uploaded text (job done).

## Routes covered

`GET /api/health`, `GET /api/families`, `GET /api/llm`, `GET /api/llm/usage`, `GET /api/usage`,
`POST /api/llm/usage/reset`, `POST|PUT|DELETE /api/llm/profiles[/{id}]`, `PUT /api/llm/roles`,
`PUT|DELETE /api/llm/keys/{provider}`, `GET|POST /api/projects`, `GET|DELETE /api/projects/{pid}`,
`PUT|DELETE /api/projects/{pid}/reference`, `POST /api/projects/{pid}/reference/files`,
`POST /api/projects/{pid}/design`, `POST /api/projects/{pid}/revise`, `GET /api/projects/{pid}/versions/{n}`,
`POST /api/projects/{pid}/approve`, `POST /api/projects/{pid}/search`, `GET /api/projects/{pid}/candidates`,
`POST /api/projects/{pid}/candidates/{cid}/analyse`, `POST /api/projects/{pid}/candidates/{cid}/datasheet`,
`GET /api/projects/{pid}/analysis/{cid}`, `POST /api/projects/{pid}/decision`, `GET /api/projects/{pid}/export`,
`GET /api/jobs/{id}`, and unknown `/api/*` routes.

## Not captured

- `POST /api/llm/profiles/{id}/test`: it calls the adapter for real (the Claude CLI or Gemini), so it cannot be
  scripted offline.
- The 64 MB body limit (413), the static client catch-all (no built client in the backup run), and the
  unsupported-family candidate test.
- Per-poll job progress; only the final record of each job is saved.

## Edits after recording
- 2026-11-21: `01_health.json`, `03_llm_settings.json`, `10_llm_roles_set.json` and `13_llm_profile_delete.json` were edited by hand to add the `objects` role, the `claude_haiku` default profile and `roles_available`, because a new LLM role was added. Everything else is as recorded from the FastAPI app.

## google-gemini branch (2026-11-21)

Goldens 01, 03, 10 and 13 were edited by hand, not re-recorded: the default profiles and role defaults are Gemini
(`gemini_pro`, `gemini_flash`, `gemini_flash_lite`, `gemini_cli_pro`, `gemini_cli_flash`, `antigravity_flash_high`,
`antigravity_flash_medium`, `antigravity_flash_low`), `kinds` and `models` list `gemini_cli` and `antigravity_cli` (03 and 13), and the roles request in 10 points `analysis` at `gemini_flash`. The scripted LLM answers are unchanged.
