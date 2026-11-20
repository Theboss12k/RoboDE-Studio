# Handoff — Java + React migration (living document; newest state at the top)

**2026-11-20 (later) — Follow-ups (a), (c) and (d) done.** (a) The revise summary now ends with the code-checked outcome (`checked_outcome` in `pipeline/design.py`; golden allow-list entry added in `GoldenContractTest`). (c) The FastAPI stack has been uninstalled from `.venv`. (d) A real search and analyse ran through Java (UR3e and CR3A found; UR3e analysed, 5 values verified); see the last PROJECT_TIMELINE entry. Still open: nothing is committed, and the user decides how to split their logo/client work from the migration. The running jar is from before this session, which is fine because the Java code is unchanged; the next `npm run setup` or `./mvnw package` rebuilds it with the test change only.

**2026-11-20 16:45 IST — Migration COMPLETE and verified.** Step 8 is done: a real Opus revise from the viewed v1 at a paused moment gave v3 (parent 1) with the arm exactly 10 cm longer; see the last PROJECT_TIMELINE entry. Open follow-ups: (a) append the simulated outcome to the model-written revise summary; (b) nothing is committed; the user decides how to split their logo work from the migration; (c) the .venv still has fastapi/uvicorn installed (unused); (d) NOT RUN: the plan's real search + analyse run through Java. It was skipped at 90 % of the 5-hour usage window; the path is covered only by the scripted FullFlowTest. Run it next: approve a version, then Robot options → Find real robots → Analyse → Run analysis.


**For the next Claude agent:** read this, then the plan
(`~/.claude/plans/change-the-tech-stack-smooth-hoare.md`) and `docs/PROTOCOL.md` (the Java↔Python contract).
The user wants Sonnet/Haiku subagents to do the grunt work, with you reviewing. Never commit unless the user asks.
The working tree also holds the user's own uncommitted client and logo work; don't revert it.

## Status (2026-11-20, updated after the step 6 and 7 cut-over)
- Before the migration, the app (FastAPI + React) worked end to end: 124 pytest passed; real Claude run in
  `docs/evaluations/STUDIO_E2E_20261120.md`.
- Backup of the pre-migration tree (no node_modules/.venv):
  `/private/tmp/claude-501/-Users-parameshsriram--claude-spares-spare-4UnDID/a18c22ec-4eb5-40e6-9dde-2206f102679b/scratchpad/studio-backup-1543`
  (scratch; may be cleared on reboot).
- Written: `docs/PROTOCOL.md` (the contract).
- Done: step 1 goldens — 76 files + README in `server-java/src/test/resources/golden/` (generator
  `capture_goldens.py` there; it was run against the backup copy with scripted LLMs). Not captured: LLM profile
  test route, 413 body limit, static client fallback.
- Done: Lucide sweep (part of step 5). All icons come through `client/src/components/icons.ts` (semantic names;
  swap to lucide-react-native later). Typecheck and build pass; not yet looked at in a browser. Needs a timeline
  entry, to be written with the rest of step 5.
- Done: step 0. Pure pipelines (`pipeline/design.py` `specify/design/revise`, `pipeline/catalog.py`),
  `pipeline/adapter.py` (store wrappers FastAPI still uses), `server/studio/tasks/` (all 8 tasks per PROTOCOL.md),
  revise context with capabilities, all checks and the moment, `DesignVersion.parent_version`, `contracts/schemas/`
  (+ `scripts/export_schemas.py --check`). pytest: 140 passed, 4 skipped (fixture only).
- Done: step 5, client.
  - `packages/core` (`@robode/studio-core`, no DOM): generated types (`npm run contracts:types` /
    `contracts:check`, `scripts/contracts-types.mjs`), `createApi`, `linkFrames`, playback helpers.
  - Client calls `/api/v1`; Vite rewrites to `/api` when `STUDIO_BACKEND=fastapi`. The Robot_IDE
    `.claude/launch.json` `robode-studio` entry sets that env for now — remove it after the cut-over.
  - Design posts `{request, base_version, t}`; tabs show "based on vN".
  - Typecheck and build pass. Browser check against FastAPI (rewrite on): the project loads, viewing v1 shows
    "Change will be based on v1", and 55 Lucide SVGs render. No real revise was sent yet.
  - TODO: wire `contracts:check` into `npm test` (step 6).
- Done: steps 2–3, Java in `server-java/`.
  - Stack: Spring Boot 4.2.0 with Jackson 3 (`tools.jackson`), springdoc 3.2.0, JUnit 6, Mockito 5.24, Maven
    wrapper (Maven 3.10.0), `--release 25` on JDK 27. `./mvnw verify` gives 21 tests passing.
  - Packages under `dev.robode.studio`: config, common, store, llm, jobs, meta, api.
  - Test fake engine: `src/test/resources/fake_engine/tasks_main.py`.
  - Ran manually against the real Python tasks and the real runtime on port 8899: health, families, LLM settings,
    projects, versions (lazy parsed backfill), analysis, `frames.json`, and the export ZIP (11 entries, the same
    as FastAPI).
  - NOT yet exercised: design, revise, search, analyse, uploads, `llm_test`, and the profile/key routes over HTTP.
    There are no golden contract tests yet.
  - Deviations to resolve or accept:
    1. `llm_test` returns `{ok, answer, seconds}`, but the client Settings page expects the old
       `{text, text_seconds, images...}`. Make the Python task return the old shape.
    2. Validation errors return `{detail: string}` instead of FastAPI's array. The client only reads strings, so
       this is fine.
    3. Unknown profile on PUT or test returns 404, where FastAPI returned 500.
    4. Datasheet uploads are stored in `documents/<cid>.upload/` plus `documents/<cid>.json`; the text is
       extracted at analyse time.
    5. Reference URDF goes to `reference/urdf/<name>` plus `urdf.json`.
    6. `schemaVersion` is stripped from responses.
    7. Done jobs are capped at 500 in memory.
- Done: step 4.
  - `./mvnw verify` gives 23 tests passing, including:
    - `flow/FullFlowTest.java`: real Python tasks, scripted profiles from
      `src/test/resources/scripted/rules*.json` (regenerate with `make_fixtures.py`), revise from v1 at t=1 with the
      call log checked, branching to v3 from v1, approval 409s, search, analyse, the `datasheet_download` error,
      export, and no keys in usage;
    - `GoldenContractTest.java`: all 76 goldens replayed under `/api/v1`. Allow-listed differences: extra
      `parent_version`, `detail` returned as a string, 404 text for path traversal.
  - Fixes from this step: Python `llm_test` returns the old shape; the analyse task extracts text from an uploaded
    PDF/HTML; Java revise defaults `base_version` to the latest version; the scripted profile emits usage lines;
    `STUDIO_FAKE_DOWNLOAD` test hook (honoured only with `STUDIO_ALLOW_SCRIPTED`).
  - pytest: 140 passed, 4 skipped.
- Done: step 6 cut-over (2026-11-20). `scripts/studio.mjs`: `setup` makes the venv and builds the Java jar; `dev` and
  `api` run the jar (`STUDIO_PYTHON` set); `test` runs `./mvnw verify`, pytest and `contracts:check` in order and fails
  if any fails. FastAPI `api/`, `projects/` and `pipeline/adapter.py` deleted; `tests/test_api.py` deleted;
  `test_pipeline.py` and `test_revise_regressions.py` call `pipeline/design.py` directly; fastapi, uvicorn and their
  companions removed from `requirements.txt`; `STUDIO_BACKEND` removed from Robot_IDE `launch.json`. Results as run:
  Java 23 tests passed; pytest 131 passed, 4 skipped; contracts up to date; typecheck and build pass. Also fixed the
  Decision export link (`/api/v1`). The running dev server was not stopped; restart it to serve Java.
- Done: step 7 docs. AGENTS.md (architecture section), README.md, docs/STUDIO_TECHNICAL.md (architecture, API, client,
  testing), docs/CONTRACTS.md (API under `/api/v1`, units and frames), docs/PROTOCOL.md (route note). Timeline entry
  added.
- NEXT (not started): step 8 verification.
  - Restart `npm run dev` so the Java API serves the browser (do not kill a running process; ask the user first if one
    holds port 8800).
  - Real Claude revise in the browser: view v1, pause mid-carry, "make the arm 10 cm longer" → new version with parent 1
    and a longer arm. Old projects open; Lucide icons everywhere; the export download works from the Decision stage.
  - Record the result as a dated timeline entry. Real model calls are named as such.

## Steps (from the plan)
0. Python: pure pipelines + `studio.tasks` + scripted profile kind + revise base_version/moment + `parent_version`.
1. Goldens of today's FastAPI responses → `server-java/src/test/resources/golden/`.
2. Java spike (Spring Boot 4, `--release 25`, mvnw, Mockito test on JDK 27).
3. Java core: store, settings, keys, usage, runner, `/api/v1` routes, bind/token policy, springdoc.
4. Java tests: goldens, runner (watchdog, tree kill, error map, no keys in argv/env/logs), full flow with scripted Python.
5. Client: `packages/core` (types from `contracts/` JSON Schemas, API client, FK/playback), `/api/v1`,
   Design/Player `getMoment()` → revise `{request, base_version, t}`, Lucide sweep + `components/icons.ts`.
6. Cut-over: DONE (see Status).
7. Docs: DONE (see Status).
8. Verify: `npm test`, typecheck, build; browser pane with real Claude: view v1, pause mid-carry,
   "make the arm 10 cm longer" → new version based on v1; old projects open; Lucide icons everywhere.

## Commands
- Python tests: `cd ~/Downloads/RoboDE-Studio && .venv/bin/python -m pytest -c pyproject.toml`
- Java: `cd server-java && ./mvnw verify` (once it exists)
- Preview: `.claude/launch.json` in ~/Downloads/Robot_IDE has the `robode-studio` entry (`npm --prefix <studio> run dev`, port 5190).
