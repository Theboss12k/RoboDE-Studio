# RoboDE Studio project timeline

Dated record of changes to this repository. Newest entries are added at the bottom.

## 2026-11-20 — Project started

- Scaffold: new repository copied from RoboDE-Testbed (LLM adapters and registry with Studio roles, URDF/document/image intake, unit parsing, client helpers, launcher on ports 8800/5190). Nothing in the Testbed or the IDE changed.

## 2026-11-20 — Agents, design pipeline, project store and HTTP API

- Added `agents/` (spec, scene, robot, revise, analysis: prompt, pydantic output schema, validator, one repair turn), `pipeline/` (design, revise, catalog find and analyse), `projects/` (file store, versions never overwritten except the `approved` flag) and `api/` (FastAPI jobs, LLM settings routes as in the Testbed, export ZIP).
- Behaviour: scene and robot agents run concurrently; sizes, requirements, checks and the candidate comparison come from code. A datasheet that cannot be downloaded fails the job with `error_code: datasheet_download` and the user uploads it instead.
- Scene validator lets a surface that holds a marker come within 0.1 m of the base (other boxes keep 0.3 m free).
- Checked: `tests/test_agents.py`, `test_pipeline.py`, `test_api.py` with scripted (fixture) LLMs and the real family and simulator code; no network, no real model inference, no hardware. `tests/test_real.py` (real Claude CLI, `STUDIO_CLAUDE=1`) was not run.

## 2026-11-20 — Robot families, IK, URDF generation and task simulator

- Applications: Studio server (`server/studio/families/`, `server/studio/sim/`).
- Added the four v1 families (serial arm, gantry, mobile base, mobile manipulator) with forward kinematics, damped-least-squares IK (joint limits, fixed-seed multi-start, tool-down constraint), static gravity torques via `physics.statics`, physics-driven sizing, URDF generation (root `base_link`, tool chain ends in `tcp`) and GLB/STL export of the zero pose.
- Added the task simulator: waypoints from the spec, per-waypoint IK, synchronised joint-space motion with per-joint trapezoid limits, base driving along the locomotion path, frames with world-frame TCP, and checks (reach, joint limits, static joint loads, joint speed, cycle time, payload, grip, accuracy, collisions against scene boxes, locomotion).
- Verification: `tests/test_families.py` and `tests/test_sim.py` run fixture specs through the numeric simulator only. No model inference, no Isaac/GPU validation and no physical robot tests. Limitations: collisions are reported, not avoided (no motion planner); capsule collision is coarse; mobile manipulator ignores base tipping and the base body in arm collision checks.

## 2026-11-20 — Studio workspace and brief redesign

- Application: Studio client. Updated workspace navigation, stage states, and responsive layouts. Redesigned brief entry with example tasks, clearer input guidance, and a visible path to the reference step for saved briefs.

- Improved the simulation viewport's starting camera and kept marker labels in the scene overlay to avoid a browser error when navigating away. Saved model details remain available in a horizontally scrollable table on phones.
- Verification: the production client build passed, along with 91 local Python tests. Chrome completed the workflow from a new brief through decision with scripted model fixtures, including scene playback and saved project switching. Checked the desktop light and dark themes and phone layouts down to 320 pixels. The checks used local fixture data without live model inference, web search, or hardware.

## 2026-11-20 — Fixes from the first real-model run

- Applications: Studio server (`physics/`, `sim/`, `families/`, `agents/`), `docs/CONTRACTS.md`. The client is unchanged.
- Cause: the first real Claude run of the bottle task (6 s cycle) gave every step a `duration_s`; all of it counted as dwell, so the requirements asked for 5.3 m/s and 47 m/s^2, the arm was rated 15.4 m/s, the run lasted 1.6 s instead of 6 s, the tool collided with the table at start and during the carry, and the scene agent invented a floating shelf under a hover marker.
- Requirements: only `wait`/`inspect`/`press` durations are dwell, and dwell is capped at 50 % of the cycle (the Calc note says so when it applies). The spec prompt says `duration_s` is only a dwell, and the spec validator rejects a dwell sum at or above the cycle time. A marker `robot_base` is now the mounting point of a fixed robot (z included; documented in the prompt and CONTRACTS.md).
- Simulator: a `move` goes to carry height above its marker (never the surface) and holds the object only after the pick; a press dwells at the surface. When the fastest feasible run is shorter than the cycle time, the motion is stretched uniformly (speeds / k, accelerations / k^2) so the run fills the cycle, and the `cycle_time` check reports the fastest feasible time; a longer run is kept and fails. A fixed robot mounted on a table may touch that table with its base segment.
- Sizing: rated values are capped at plausible family limits (serial arm TCP 4 m/s, 30 m/s^2; gantry axis 5 m/s, 50 m/s^2; mobile base 3 m/s, 3 m/s^2) so an absurd requirement fails visibly.
- Scene: only pick/place targets and object start markers need a supporting surface (prompt and validator); the surface the base is mounted on is exempt from the base clearance rule.
- Verification: `tests/test_real_regressions.py` uses the saved real-model output (`tests/fixtures/real_bottle_v1.json`) as fixture input; no new model calls. With it tcp_speed is 0.28 m/s, tcp_accel 3.0 m/s^2, the simulated run is 6.0 s with no collisions and correct holding. Numeric simulation only: no Isaac/GPU validation, no hardware, and the real Claude CLI test (`tests/test_real.py`) was not run.

## 2026-11-20 — Fixes from the real-model revise run (move the base, faster cycle)

- Applications: Studio server (`agents/revise.py`, `agents/scene.py`, `pipeline/design.py`, `families/serial_arm.py`, `families/gantry.py`) and Studio client (`Requirements.tsx`).
- Cause: for "mount the robot on the table top ... 4 seconds" the real revise agent wrote the new base into `robot.base` only. The pipeline recomputes the base from the spec (marker `robot_base` is the single source of truth) and discarded it, so the robot stayed on the floor, the scene kept a gap between the tables and the arm (base column shrunk to 0.16 m) crossed both tables (6 collisions). The 4 s cycle failed (fastest feasible 4.6 s) because joint speed ratings stayed at the defaults.
- Base: the revise prompt now explains the `robot_base` marker (z = mounting surface) and asks for a scene change when the mount needs a surface. A patch whose `robot.base` is set and more than 1 cm from the current base, with no `robot_base` marker in the spec, is converted into that marker (also added to a patched scene). When the base moved the requirements are re-derived and the scene agent re-runs with the new base unless the patch has a scene. The scene prompt and validator require, when the base z > 0.02, a box whose top is at the base z and whose footprint contains the base x, y; the exemption of that surface from the base clearance rule is unchanged.
- Sizing: `serial_arm.size_from_physics` now takes task heights relative to the base z (it used world heights for a raised base). `size_and_simulate` runs a speed-sizing loop: if the cycle-time check fails because the run is too long, the joints using at least 70 % of the busiest joint's speed are raised by the shortfall ratio x 1.05 (serial arm joints capped at `MAX_JOINT_SPEED_DEG_S` = 360 deg/s, gantry axes at its existing axis caps; gantry forces grow with the acceleration), the URDF is rebuilt and the task re-simulated, at most 3 times. Explicit user numbers (`explicit`) are never overridden. What was raised is appended to the concept rationale ("Speed sizing: ..."); if the cycle still fails, the cycle-time check detail says what limited it (speed cap). Static torques do not depend on speed, so they are unchanged for the serial arm.
- Client: for `info` requirements the Robot column shows a dash when the check has no actual value, and always for "Closest task point" (its 0 m was a family constant, not a measurement).
- Verification: `tests/test_revise_regressions.py` replays the saved real revise output (`tests/fixtures/real_bottle_revise.json`) through a scripted revise LLM and the real pipeline, families and numeric simulator: v2 base at (0.5, 0, 0.75), `robot_base` marker present, mounting table in the scene, no collisions, 4 s cycle passes (j1 raised 180 -> 196 deg/s); a patch with a direct marker; a rebuilt scene; the cap and explicit-number cases. All 108 Python tests pass, `npm run typecheck` passes. No real model inference was run, no Isaac/GPU validation, no hardware. Limitation: the loop covers the serial arm and gantry; other families get no speed sizing.

## 2026-11-20 — Robot search: prices, sizing, images and datasheet fallback

- Applications: Studio server (`catalog/search.py`, `pipeline/catalog.py`, `api/app.py`), `docs/CONTRACTS.md`. The client is unchanged.
- Cause: a real web-search run (Claude with web tools, bottle pick-and-place) returned 4 candidates with no prices (every price was nulled because shop pages show no publication date), all oversized (10-12.5 kg arms for a 0.3 kg bottle), 3 of 4 without an image and 3 of 4 without a datasheet link (analysis would stop).
- Prices: the search prompt gets today's date; a price with source, quote and figure but no date gets `seen <UTC date>`. Code still nulls a price without source or quote and now also one whose low/high figure does not appear in its quote (separators and currency symbols tolerated). The prompt asks for a price per candidate from the manufacturer's shop or authorised resellers, with what is included in `note`, and null when none is public. The job reports how many prices were dropped and why.
- Sizing: the prompt passes the requirement targets and asks for the smallest robots meeting every requirement with margin (payload ~1-5x, reach ~1-1.6x), best fit first, a cheaper and a more capable option, a mix of families only when the task allows, with the family definitions (SCARA is `other`). It also asks for the manufacturer datasheet URL.
- Images: `catalog.search.fill_images` takes og:image / twitter:image from the product page (safe download, 3 MB, HTML only; linked, not stored; failures ignored), run by `pipeline.catalog.find`.
- Analysis input: without a datasheet link (or when the candidate's link fails) `analyse` uses the product page text; the progress line and `ManufacturerSpec.document_url` name it. `Datasheet download failed:` only when nothing works. An explicit user URL is not replaced.
- Verification: new fixture tests in `tests/test_catalog.py` (scripted LLM, mocked downloads, no network): dated-less price, number-not-in-quote, separators, og:image with relative URL, no og:image, failure, product-page fallback; full suite 114 passed, 1 skipped. No real model inference or live web search was run for these changes, so the effect on real prompts (sizing, price coverage) is not yet measured.

## 2026-11-20 — Sizing: caps on every source, and re-sizing when a revision changes the task

- Applications: Studio server (`pipeline/design.py`, `families/serial_arm.py`, `gantry.py`, `mobile_base.py`, `mobile_manipulator.py`). The client is unchanged.
- Cause: a real revise run (robot moved onto the table, 6 s -> 4 s) gave a v2 with joint speed ratings of 1401 and 1821 deg/s and an arm reaching 0.93 m for a 0.37 m requirement. The previous version's params (made by older code) were passed as the sizing hint, and the caps only applied to values that sizing raised.
- Caps: every family has `clamp_to_caps`; `size_and_simulate` clamps the sized params (joint speeds, TCP speed/acceleration, axis speed/acceleration, drive speed/acceleration) wherever the value came from, then puts explicit user numbers on top unchanged. An explicit number above a cap stays and the checks report it (the `joint_speed` check shows the user's rating). The families have no force, torque or gripper-force caps, so none are clamped.
- Re-sizing: when a revision changes the spec or moves the base, the hint passed to sizing (`pipeline.design.sizing_hint`) drops the previously sized numbers (link lengths, strokes, speed/torque/force ratings, sized masses, payload, grip force, repeatability) and keeps the family, the qualitative choices (dof, gripper, tool yaw, tool length, mount) and explicit user numbers.
- Arm length: `serial_arm.size_from_physics` already ignored hint link lengths (it always sizes from the reach rule), so the 0.93 m arm came from the rule itself, which counted the 3-D distance as horizontal reach and then added the wrist-point height again. It now uses the horizontal distance and the height once each (rule in the docstring; a hint longer than the rule is shrunk to it). On the saved bottle revise the link reach (upper arm + forearm) is 0.59 m against 0.43 m (reach requirement / 0.85), 1.37x, down from 1.13 m; the arm still has to reach the carry heights with the tool pointing down, which is why the requirement's 0.366 m (distance to the table-level points) is not the whole need.
- Verification (scripted tests, no model calls; kinematic and static-load numeric simulation only): `tests/test_revise_regressions.py` adds hint speeds of 1400 deg/s clamped, an explicit 1400 deg/s kept with the check reporting it, caps of the other families, `sizing_hint` contents, and the saved real revise re-sized to 1.37x. Existing tests unchanged; full suite 119 passed, 1 skipped. No real inference, no Isaac/GPU, no hardware.

## 2026-11-20 — RoboDE Studio logo

- Application: Studio client. Replaced the generic header icon with an articulated arm mark, added a matching browser favicon, and included a standalone SVG wordmark.
- Verification: the production build passed. Chrome confirmed the header mark, favicon and standalone SVG load from the running app and checked the header at desktop and phone widths.

## 2026-11-20 — Integrated RoboDE Studio wordmark

- Studio client: combined the product name and custom lettering in one chamfered SVG for the header. The second O carries a rotary joint detail, and the favicon uses the same DE lettering.
- Verification: the production build passed. Chrome loaded the full wordmark and favicon from the running app and checked the header at desktop and phone widths.

## 2026-11-20 — Clean-up: server-side approval, unsupported families, web tests, documentation

- Applications: Studio server (`api/app.py`, `pipeline/catalog.py`), Studio client (`stages/Options.tsx`), tests, documentation.
- Approval is now enforced by the server: once a version is approved, `POST /revise` and `POST /approve` for another version answer 409 with a plain message; approving the same version again is a no-op. `Design.tsx` already shows server errors from both calls, so it is unchanged.
- Candidates whose family is `other` (SCARA, delta, ...) or not in the family registry fail `analyse` at once, before any download or model call, with a message saying Studio can model serial arms, gantries, mobile bases and mobile manipulators and that the listed specs and quick screen still apply. The Options page shows that note instead of the Analyse button (unless an analysis already exists).
- `STUDIO_WEB=1` now gates tests marked `web` (skipped otherwise, like `claude`). Added `tests/test_web.py`: a real HTTPS download of a public page (iana.org) through the pinned-address path, and refusal of `https://localhost/` and `http://example.com/`. This is a real network call, not a model run. It passed once on 2026-11-20 (3 passed); no change to `catalog/fetch.py` was needed.
- Documentation: `docs/CONTRACTS.md` reconciled with the code (16 m/s^2 effective arm acceleration cap, export contents, LLM test/usage routes, test markers, mobile base placement, `wait` waypoint, base-move and mounting-surface rules, approval freeze); the mismatch list in `docs/STUDIO_TECHNICAL.md` section 15.1 was removed. `README.md`, `docs/STUDIO.md` and `docs/STUDIO_TECHNICAL.md` were added today by another engineer. `requirements.txt` comments no longer describe Testbed features.
- Verification: new scripted tests in `tests/test_api.py` (approval freeze; unsupported family fails before download and extraction; no model calls). Full `npm test` run: 121 passed, 4 skipped (real Claude and web tests); `npm run typecheck` clean. The Options.tsx change was type-checked only, not exercised in a browser.

## 2026-11-20 — Industrial logo and dual-lens sample

- Studio client: redrew the header logo and favicon as industrial equipment plates. Added a standalone SVG sample with two asymmetrical optical sensors inside the final O.
- Verification: the production build passed. Both SVGs rendered in Chrome, and the running app served the header logo, matching favicon, and alternate sample. Checked the header at desktop and phone widths.

## 2026-11-20 — Real candidates are judged only on manufacturer values

- Applications: Studio server (`catalog/model.py`, `catalog/compare.py`, `pipeline/catalog.py`, `agents/analysis.py`, `schemas.py`), Studio client (`stages/Analysis.tsx`, `components/Requirements.tsx`), documentation.
- Cause: the saved real run for the FANUC CRX-5iA (`tests/fixtures/real_fanuc_analysis.json`; product page read by real Claude) passed joint torques, joint speed, TCP acceleration and cycle time on family defaults and sized values the manufacturer never published, and ignored the verified 2.0 m/s speed. The comparison also printed "not available" for checks that had a verdict but no number, so the analysis text claimed the collision check was missing.
- Behaviour: `candidate_concept` returns a provenance for every parameter (`verified`, `derived`, `carried`, `default`), saved as `Analysis.provenance`. Verified speed and acceleration values are applied to the family (serial arm TCP speed, gantry axes, mobile base). After the candidate simulation, checks that depend on `default` parameters get no verdict ("Not published by the manufacturer; <param> assumed <value>", estimate kept in `actual`); gripper checks say they use the generated design's gripper; reach, joint-limit and collision checks say link lengths are estimated from the verified reach; `ok` counts only checks with a verdict. The comparison renders number-less checks as passes/fails with their detail and unpublished values as "not published", skips `info` requirements and no longer duplicates the payload and repeatability rows. The analysis agent prompt explains these terms. The client shows the provenance list and "not published" chips; the unsupported-family message now reads "is listed as family 'other'".
- Verification: scripted tests only (no model calls, no new real inference): the FANUC fixture's values give TCP speed 2.0 verified and default torques, unpublished checks are `None`, collision stays a pass and renders as "passes", info and duplicate rows are absent; full Python suite and client typecheck pass. The saved real run itself was not re-run with the new code, so its stored analysis predates these rules. Checks remain kinematic and static-load calculations, not dynamics or hardware tests.

## 2026-11-20 — First end-to-end evaluation with real models and the live web

- Applications: whole Studio (server, client, docs). Report: [docs/evaluations/STUDIO_E2E_20261120.md](evaluations/STUDIO_E2E_20261120.md).
- The full flow was run with real Claude (Opus 5.5 for spec, robot and revise; Sonnet 5.5 for scene, search, extract and analysis) through the browser pane and the API. The run went from brief and questions to design v1, a change request to v2, approval, live web search, and analysis of the FANUC CRX-5iA from its product page (7 of 7 values verified), through to the decision and the export. Usage: 18 model calls, about 317k tokens, about 1.30 USD, 26 web calls.
- Ten defects found by these runs were fixed the same day; see the entries above and the report. The real outputs are saved as fixtures (`tests/fixtures/real_*.json`) for regression tests.
- Client: a held object now keeps its grasp offset in the 3D view, so a bottle gripped at its centre no longer sinks into the table on release. Price dates read "seen <date>" or "dated <date>" instead of "seen seen".
- Verification: `npm test` 124 passed, 4 skipped (real Claude and web tests; the web tests passed once with `STUDIO_WEB=1`); `npm run typecheck` and `npm run build` pass. Checks are kinematic and static-load calculations only; no dynamics, Isaac, Gazebo or hardware.

## 2026-11-20 — Client migration step 5: shared core package, /api/v1, feedback with the viewed iteration

- Applications: Studio client, new workspace `packages/core` (`@robode/studio-core`), root `package.json`, `scripts/contracts-types.mjs`. No server code changed.
- `packages/core` holds DOM-free code (three.js is a peer dependency, math only): TS types generated from `contracts/schemas/*.json` (`npm run contracts:types`; `npm run contracts:check` fails when the committed file is stale), hand-written envelopes (`Project`, `Job`, `Parsed`, `ReviseRequest`), `createApi({ baseUrl, fetch, token? })` with `runJob` polling, and the link forward kinematics and playback helpers (`linkFrames`, `robotExtent`, `frameAt`, `baseAt`, `objectCenters`) moved out of `RobotView`, `SceneView` and `Player`. Frames and the z-up to y-up conversion are documented in `packages/core/README.md`. It is consumed as TypeScript source through the npm workspace, with no build step.
- API base: the client calls `/api/v1`. `client/vite.config.ts` proxies `/api/v1` to the API target unchanged (the Java server); with `STUDIO_BACKEND=fastapi` it rewrites `/api/v1` to `/api` so the Python server keeps working until it is replaced.
- Feedback: the Design stage posts `{request, base_version, t}` with the viewed version and the player's current time (read through a `getMoment()` ref, not pushed up at frame rate). When the viewed version is not the latest the box says "Change will be based on v{n}", revised versions' tabs show "based on v{parent}", and the view switches to the new version after the job.
- Checked: `npm run typecheck`, `npm run build` and `npm run contracts:check` pass. Not run: browser test of the Design flow, real model inference, the Java server, native Isaac or hardware.

## 2026-11-20 — Java server (`server-java/`): Spring Boot spike and core, not yet the live backend

- Applications: new `server-java/` (Spring Boot 4.2.0, Spring MVC on virtual threads, Maven wrapper, compiled with `--release 25`, running on JDK 27). The FastAPI server and the client are unchanged; the Java server is not wired into `npm run dev` yet.
- Behaviour: implements the Java side of `docs/PROTOCOL.md`. It is the sole writer of `runtime/projects/**` and `runtime/llm/**` (same layout, id and file-name rules, `schemaVersion` 1 on written JSON objects, sidecars `v<n>.parsed.json`), keeps profiles, roles, keys (`keys.json` created 0600) and the usage ledger, runs one Python task per job (stdin envelope, JSON-line stdout, idle watchdog 60 s, 45 min ceiling, 4 at once, process-tree kill), and serves the routes of the old API under `/api/v1` plus revise with `base_version`/`t`, version assets, and `/v3/api-docs`. A non-loopback `STUDIO_HOST` without `STUDIO_TOKEN` is refused; with a token every `/api/v1` call needs a bearer header.
- Verification: `./mvnw verify` passes (21 tests: store round trip including a project written by the Python store, key file permissions, usage summary against a hand-made ledger, bind policy, job service, and the task runner against a fake Python engine covering stream parsing, error mapping, hung script and child killed by the watchdog, and the API key absent from argv, env and log). Fixture and real-process tests only; no real model inference. A manual run of the jar against the real Python tasks served health, families, LLM settings, project and version views, analysis, and the export ZIP for a saved project. Contract tests against the golden files, the design/revise/search/analyse flows through scripted Python tasks, and the client switch-over are not done yet.

## 2026-11-20 — Java server: full-flow and golden contract tests against real Python tasks

- Applications: `server-java/` (tests and small fixes), `server/studio/tasks/` (three small fixes). The Java server is still not the live backend.
- Tests: `FullFlowTest` runs design (questions, answers), v1, reference text/URDF/image uploads, revise from v1 at a moment, a second revise from v1 while v2 exists (v3, parent 1), approval and the frozen-design 409, search, analyse with an uploaded datasheet, a blocked datasheet URL (`datasheet_download`), decision, export ZIP and the usage ledger, with real `python -m studio.tasks` subprocesses and scripted fixture LLMs (`src/test/resources/scripted/`, regenerated by `make_fixtures.py`). `GoldenContractTest` replays all 76 recorded FastAPI requests against `/api/v1` and compares status, field names and types, and stable values; the only accepted differences are `parent_version` on versions, pydantic's validation error list (now a string), and the message for encoded slashes. These are fixture runs: no real model inference, no network, simulated robots only.
- Fixes: the `analyse` task read an uploaded datasheet as UTF-8 text, so a PDF upload would have failed (now extracts text); the `llm_test` task returns the old Settings response shape (`text`, `text_seconds`, `images`) beside `ok`/`answer`/`seconds`; scripted LLM calls now emit a small fixture usage line so the ledger can be tested; revise accepts a missing `base_version` (latest) with the Python messages; tests can serve one offline datasheet for `https://acme.example/` (`STUDIO_FAKE_DOWNLOAD`, only with `STUDIO_ALLOW_SCRIPTED=1`).
- Verification: `./mvnw verify` 23 tests pass; pytest 140 passed, 4 skipped; client typecheck passes.

## 2026-11-20 — Cut-over: the Java server replaces the FastAPI server

- Applications: Studio server (`server-java/` serves `/api/v1`; Python runs as `python -m studio.tasks <task>` subprocesses), Studio launcher (`scripts/studio.mjs`), Studio client (export link, Vite proxy), packages and docs. Robot_IDE launch configuration: the `robode-studio` entry no longer sets `STUDIO_BACKEND=fastapi`.
- Behaviour: `npm run dev` starts the Java jar and the Vite server; `npm run api` starts the Java server only; `npm run setup` also builds the jar with `./mvnw package`. `npm test` runs `./mvnw verify`, then pytest, then `npm run contracts:check`, one after another. Every step runs, and the exit code is non-zero if any failed.
- Removed: `server/studio/api/` (FastAPI app and launcher), `server/studio/projects/` (file store, now written by Java), `server/studio/pipeline/adapter.py` (store wrappers), `tests/test_api.py` (HTTP tests now in the Java `FullFlowTest` and `GoldenContractTest`). Dependencies removed from `requirements.txt`: fastapi, starlette, uvicorn, websockets, httptools, uvloop, watchfiles. No remaining code imported them. `pydantic` and `httpx` stay.
- Changed tests: `tests/test_pipeline.py` and `tests/test_revise_regressions.py` now call `pipeline/design.py` directly with scripted LLMs. The `test_tasks.py` find and analyse test no longer imports the deleted API test module; its scripted helpers moved into that file.
- Fixed: the Decision stage's export link pointed at `/api/projects/...`, which the Java server does not serve. It now uses `/api/v1/projects/...`. The Vite proxy no longer has a FastAPI rewrite.
- Checked, as run on 2026-11-20 with `npm test` (run output not kept in the repository):
  - Java: `[INFO] Tests run: 23, Failures: 0, Errors: 0, Skipped: 0` and `BUILD SUCCESS`.
  - Python: `131 passed, 4 skipped in 20.34s` (the 4 skipped are the opt-in real-Claude and web tests).
  - Contracts: `contracts types are up to date`. The summary line says `all passed`.
  - `npm run typecheck` and `npm run build`: both exit 0.
- Not checked: no browser test of the Java-backed app and no real model call since the cut-over (the step 8 check). The Java tests run scripted profiles through real Python tasks; the fixture tests make no model call. The Gemini adapter is still untested against Google.
- Limitation for the user: a dev server started before this change may still be the old FastAPI process on port 8800. It was not stopped or restarted. Run `npm run dev` again to serve the Java API. The `.venv` still has the removed packages installed; they are unused.


## 2026-11-20 — Java + React migration verified with a real model

- Applications: Studio server (Java Spring Boot in `server-java/`, Python tasks in `server/studio/tasks/`), client (`/api/v1`, `packages/core`, Lucide icons).
- Real model run (Claude Opus 5.5 through the Claude Code CLI, via the Java job runner and the Python `revise` task): in the browser pane, the existing pre-migration project was opened, v1 was viewed while v2 existed, playback was paused mid-carry (t = 0.71 s, phase "Carry the bottle ... above B"), and "Make the arm 10 cm longer." was sent. Result: v3 with `parent_version` 1, v2 unchanged. The arm grew by exactly 0.10 m from v1's values (upper arm 0.491 → 0.541 m, forearm 0.454 → 0.504 m). The model's summary referred to the collision at the paused moment (forearm against table_b while carrying to B). Code re-ran the checks: 12 of 17 pass, and the collisions inherited from v1 (made by pre-fix code) remain and are reported.
- Pre-migration projects open unchanged through Java. The old `/api` routes are gone (404); the client uses `/api/v1`.
- Fixture tests at the cut-over: `npm test` gave Java 23 passed, pytest 131 passed with 4 skipped, and the contracts check up to date. Kinematic and static-load checks only; no hardware.
- Known follow-up: the revise summary is written by the model before the simulation runs, so it can say "needs a re-check" even though the checks did run. Appending the simulated outcome to the summary in code would fix that.

## 2026-11-20 — Revise summary states the code-checked outcome; real search and analyse through Java

- Applications: Studio Python pipeline (`server/studio/pipeline/design.py`), Java tests (`server-java/.../flow/GoldenContractTest.java`), Python test `tests/test_pipeline.py`, the local `.venv`.
- Behaviour: the model writes the revise summary before the checks run, so it could say a change "needs a re-check" although code had run the checks. `revise()` now appends one sentence built in code from the new version's checks (function `checked_outcome(sim)`), for example "Checked in code after the change: 16 of 19 pass; 3 could not be checked." It names up to three failing checks ("failing: TCP speed, TCP acceleration") and counts the rest. The model's text is kept unchanged in front.
- Tests changed: `GoldenContractTest` has a new allowed difference for goldens 34 and 40, pointer `/change_summary`, reason "revise appends the code-checked outcome" (the pre-change goldens have only the model text). `test_pipeline` now checks that the summary starts with the model text and contains "N of M pass".
- `.venv`: uninstalled fastapi, starlette, uvicorn, httptools, uvloop, watchfiles and websockets (removed from `requirements.txt` at the cut-over). `pip check` reports no broken requirements.
- Real model run and real web search (2026-11-20, through the Java job runner and the Python `search` and `analyse` tasks; the search, extract and analysis roles are Claude Sonnet 5.5 via the Claude Code CLI): in the browser pane on project "Tabletop bottle transfer A to B", v2 (14 of 17 checks pass, none failing) was approved, then Robot options, Find real robots, then Analyse, Run analysis on the first result.
  - Search returned 2 candidates: Universal Robots UR3e (no published price; payload 3 kg, reach 0.54 m, repeatability ±0.03 mm, TCP speed 3 m/s) and Dobot CR3A (22120 USD web estimate seen 2026-11-20 from a RobotShop US reseller listing, confidence low; payload 3 kg, reach 0.62 m). The quick screen passed both on payload (≥ 1.2 kg) and reach (≥ 0.366 m, the v2 requirement).
  - Analyse UR3e: no separate datasheet was found, so the product page https://www.universal-robots.com/products/ur3e-540/ was used as the document. 5 manufacturer values were verified by quote in that page: payload 3 kg, reach 540 mm, weight 11.2 kg, repeatability ±0.03 mm, max TCP speed 3 m/s. The same task re-run in code with the UR3e values: reach, payload, TCP speed, grip force, repeatability, joint limits and collisions pass; TCP acceleration, cycle time, static joint loads and peak joint speed are "not published" (assumed values, no verdict). Link lengths are not published and were scaled so the flange reach equals 0.54 m; the reported 0.679 m maximum reach includes the tool. The arm is modelled with the family's 4 joints; the UR3e has 6 (the page text does not state the joint count). These are kinematic and static-load calculations in code, not dynamics simulation and not hardware tests.
- Checked after the change (2026-11-20): `./mvnw test` 23 tests, 0 failures (run as `test`, not `verify`, so the jar used by the running dev server was not rebuilt); pytest 131 passed, 4 skipped; `npm run contracts:check` up to date; `scripts/export_schemas.py --check` exit 0; `npm run typecheck` and `npm run build` pass.
- Not checked: `./mvnw verify` and `npm test` as one command (the running server's jar was not repackaged); the running Java server was not restarted (not needed, the Python tasks run from source). No revise was sent after the summary change with a real model, only scripted fixtures. Dobot CR3A was not analysed. The Gemini adapter remains untested against Google.

## 2026-11-20 — IN PROGRESS: collision auto-repair, question comments and images, background jobs, fleet planner

- Plan: an automatic LLM revise loop for collisions, at most 3 attempts verified in code, keeping the best attempt with an attempt log; per-question comments and a note for each round; images attached to answers and sent to the spec agent; jobs that survive stage and project switches; a multi-robot fleet planner for the Design stage (fixed-base members, handover stations, 2–3 robots).
- Status: started. This entry is updated as each part lands; results are recorded in separate dated entries.

## 2026-11-20 — Photo AR ("View in my space"), first delivery

- Applications: Python tasks (`server/studio/ar/`, `ar_calibrate`, `ar_estimate`), schemas and contracts (`ArScene` and related models), Java routes and store (`/api/v1/projects/{pid}/ar/scenes`, `runtime/projects/<pid>/ar/`), `packages/core` (`ar.ts`), client Design stage ("View in my space").
- Plan: the user uploads a site photo; code finds the camera from a known-size floor rectangle (homography, numpy) or, optionally, a depth-model estimate (torch + MoGe, an optional install). The robot, task objects and playback render over the photo at true scale. Contract: `docs/AR_PHOTO.md`.
- This is a visual overlay only: no checks against the real room, and pass/fail numbers still come from the existing kinematic checks.
- Calibration: the focal length comes from the reference homography, using whichever of the two constraints is well-conditioned, so a sheet square or at 45° to the camera still solves. Otherwise it falls back to the estimate, else a default 60° horizontal field of view, with a warning.
- Windows support (not AR): `server/studio/llm/claude_cli.py` imported the Unix-only `pwd` module, which stopped every task import on Windows. It now falls back to `~` when `pwd` is absent; behaviour on macOS and Linux is unchanged.
- Checked so far (2026-11-20, Windows 11, Python 3.12.10, Node 26.13.1; fixture tests only, no real model):
  - pytest with `PYTHONUTF8=1`: 181 passed, 4 skipped, including 29 new AR tests. The synthetic camera recovers its pose within 1 mm and 0.1° and its focal length within 1%. Without UTF-8 mode, `test_catalog.py::test_extract_verifies_quotes` fails on Windows; this is pre-existing and not related to AR.
  - `scripts/export_schemas.py --check` and `npm run contracts:check`: up to date.
  - `npm run typecheck` (core and client): passes.
  - Core `node --test`: 4 of 4 pass; the GL projection matches the OpenCV pinhole within 0.5 px.
- Java fix (not AR): `ProjectService` used `ProjectStore.BUDGET_MESSAGE`, which was package-private, so `main` as downloaded did not compile. It is now `public`.
- Java (`mvnw test`, JDK 25.0.5, with `STUDIO_PYTHON=.venv\Scripts\python.exe` and `PYTHONUTF8=1`): 36 run, 1 error, 7 skipped. New tests: `ArScenesFlowTest` (upload, list, photo, `ar_calibrate` through Python, placement, the 10-scene limit, delete) and `ImageSizeTest`. The one error is `KeysAndUsageTest.keyFileIsOwnerOnlyAndKeysAreNeverReturned`, which sets POSIX file permissions and is unsupported on Windows; it is pre-existing and not related to AR.
- Browser check (built-in browser pane, scripted fixture model, no real model):
  - Test photo: a synthetic 1600×1200 floor photo with an A4 sheet, rendered from a known camera (f = 1200 px, 1.3 m high, 40° pitch, 15° yaw).
  - Calibration from the sheet corners gave f = 1201 px, a camera height of 1.3002 m and a residual of 0.05 px, through the API and through the UI calibrator.
  - With the design placed so the robot base sits on the sheet centre, the base drew on the sheet at the canvas centre, as the maths requires.
  - Playback ran over the photo with shadows, and there were no console errors.
  - "Estimate automatically" showed the error that the optional backend is missing.
- Not checked: uploading a photo through the file picker (the browser pane cannot pick a local file, so the upload used the same API route), dragging to move the design, the PNG snapshot, a real phone photo, and `ar_estimate` with a real depth model (torch and MoGe are not installed; that code path is untested).
- The local repo was created from a ZIP of `main` (no git history); see `docs/agents/merge-ar-photo.md`.

## 2026-11-20 — Photo AR: GPU depth estimate, phone link and end-to-end test

- Applications:
  - Python: `.venv` and `requirements-ar.txt`.
  - Java: the `pairing` package; `StudioProperties.pairPort`; zxing 3.5.3 in `pom.xml`; the `/api/v1/projects/{pid}/ar/pairing` routes.
  - Client: `PhoneLink.tsx` and `ArPanel.tsx`.
  - `packages/core`: the pairing API functions.
  - Playwright end-to-end test: `e2e/`, `npm run e2e`.
- Automatic estimate: installed into `.venv`. It follows the recipe now in `requirements-ar.txt`; MoGe's other declared dependencies (gradio, FlexGEMM/Triton, newer trimesh) are not installed.
  - PyTorch 2.14.1+cu130 and torchvision 0.29.1.
  - MoGe 3.0.0 (MIT), using the MoGe-2 ViT-L weights (1.32 GB, MIT) in the Hugging Face cache.
  - OpenCV headless, SciPy and Pillow.
- Real depth-model run (MoGe-2 ViT-L on an RTX 4050, 2026-11-20) on the synthetic A4 photo: about 12–17 s per estimate, including model load. It estimated a focal length of 1147–1171 px (true 1200) and a camera height of about 2.1 m (true 1.3 m). The scale is approximate, as the UI says; a flat rendered checkerboard is a poor input for a depth model, and real photos were not tried.
- Phone link:
  - The desktop shows a QR code. A phone on the same Wi-Fi opens a page that takes a photo with its camera app, or picks one from its gallery, and the photo arrives as an AR scene.
  - The phone talks to a separate JDK HttpServer on 0.0.0.0:8801 (`STUDIO_PAIR_PORT`), started only while a link is active. It serves exactly `GET /p/{code}` and `POST /p/{code}/photo`; everything else returns 404.
  - The code is 256-bit, expires after 10 minutes, belongs to one project and allows at most 20 uploads. Uploads are capped at 15 MB.
  - The main API stays on loopback.
  - Traffic on the Wi-Fi is plain HTTP.
  - There is no per-connection timeout yet.
- Checked (2026-11-20, Windows 11; fixture LLM only, no real language model):
  - Playwright end-to-end: 8 of 8 pass in 44.9 s, run on installed Chrome (`E2E_CHANNEL=chrome`) because the Playwright Chromium download stalled. The suite uses three synthetic sample photos with known cameras (`e2e/fixtures/make_photos.py`):
    - Upload through the real file input, drag the 4 handles and calibrate. Every photo calibrated to its true camera: A4 PNG at f 1200 px and 1.30 m, Letter square to the camera at 1500 px and 1.10 m (`focal_source` "reference"), and the EXIF-rotated phone JPEG stored as 1500×2000 at 1600 px and 1.45 m.
    - AR view: the aspect ratio matches the photo, there are no console errors, and dragging the floor and changing the turn update the placement.
    - The snapshot PNG matches the photo size.
    - The real MoGe estimate shows the "Approximate scale" badge.
    - Phone link in an emulated Pixel 7: sent through the LAN address; "Photos received: 1" and a new portrait scene appeared; after "Stop linking" the link returns 404.
    - Error cases: a collinear reference and a non-image file.
  - pytest: 180 passed, 5 skipped. The `ar_estimator_unavailable` test now skips because torch is installed.
  - Java (`mvnw test`): 38 run, 1 error, 7 skipped, as reported by the worker that added `PairingFlowTest`. The one error is the known Windows-only `KeysAndUsageTest`.
- Not checked: a real phone on real Wi-Fi (the Windows firewall prompt was not seen), real site photos, the 20-upload limit, link expiry in the UI, deleting a scene, and the "Show scene boxes" toggle.

## 2026-11-20 — Claude Code CLI adapter on Windows; every role set to Sonnet 5.5

- Applications: Python LLM adapter (`server/studio/llm/claude_cli.py`); the local LLM settings in `runtime/llm/` (not in git).
- Windows support: `find_cli` now also searches the Windows install locations, newest version first:
  - `%USERPROFILE%\.local\bin\claude.exe` and `%USERPROFILE%\.claude\local\claude.exe`;
  - the Claude desktop app's bundled CLI, under `%LOCALAPPDATA%\Claude-Data\*\claude-code\<version>\*\claude.exe` or `%APPDATA%\Claude\claude-code\...`.
- Environment: `cli_environment()` builds the CLI's reduced environment.
  - On Windows it keeps only what a Windows program needs to start and find its login: SYSTEMROOT, the profile folders, TEMP/TMP and a PATH of system folders. Nothing from the project is passed (no PYTHONPATH, tokens or keys).
  - macOS and Linux are unchanged.
- Encoding: the system-prompt file is written as UTF-8.
- Local settings (this machine): a profile "Claude Sonnet 5.5 (Claude Code CLI)" (`claude_cli`, model `claude-sonnet-5-5`, CLI path auto-detected) is assigned to all seven roles: spec, scene, robot, revise, search, extract and analysis.
- Found: the desktop app's bundled CLI is 2.1.326. On its own it answers "Not logged in", so the user must sign in once (`claude /login`) before Studio can use it.
- Checked: pytest 180 passed, 5 skipped after the change. CLI discovery finds the bundled 2.1.326 executable.
- Not checked: no real Sonnet call has been made through Studio yet (the CLI was not signed in). The connection test and a real design run remain to do.

## 2026-11-20 — Photo AR follow-ups: phone-listener limits, CLI adapter tests, more end-to-end coverage

- Applications: Java `pairing/PairingListener.java` and `pom.xml` (test-only surefire property); client `ArPanel.tsx`; Python tests `tests/test_claude_cli.py`; `e2e/tests/ar.spec.ts`.
- Phone listener limits:
  - A request may take at most 60 s, a response 30 s, and an idle connection 30 s (the JDK `sun.net.httpserver.*` properties, set only when the operator has not set them).
  - At most 8 requests are handled at once; more get 503 "Busy, try again".
  - New tests in `PairingFlowTest`: a stalled half-request is closed, and the 9th concurrent request gets 503.
- Calibrator: the residual line no longer appears twice. The panel's "Calibration residual" line shows only when the calibrator is closed.
- `tests/test_claude_cli.py` (new; no network, no real CLI):
  - CLI discovery: the newest version wins, plus the Windows layouts, a configured path, a missing path and none found.
  - The reduced environment never carries STUDIO_TOKEN, ANTHROPIC_API_KEY, GEMINI_API_KEY or PYTHONPATH.
  - A stub CLI run checks the stream-json input and the result, image and error handling.
- End-to-end, new tests 9–11:
  - "Show scene boxes" changes the rendered pixels.
  - Deleting a scene through the UI removes it from the list and the API, and its photo URL returns 404.
  - The 11th upload shows "At most 10 AR scenes per project".
- Checked (2026-11-20, Windows 11; fixture LLM only):
  - pytest: 190 passed, 6 skipped.
  - Playwright `npm run e2e` with `E2E_CHANNEL=chrome`: 11 of 11 pass in 54.7 s.
  - Java: 40 run, 1 error (the known Windows-only `KeysAndUsageTest`), 7 skipped.
  - `npm run typecheck`: passes.
  - Core tests: 4 of 4 pass.
- Not checked: Playwright's bundled Chromium. Its download hung twice on this machine, so the suite ran on the installed Google Chrome.

## 2026-11-21 — Fix: "Link a phone" showed a stray address error in development

- Applications: client `client/src/components/ar/PhoneLink.tsx`.
- Bug, found while taking UI screenshots on the dev server: the panel showed "That address is not one of this link's addresses" under a working QR code.
  - React's double mount in development started two links at once. The second replaced the first while the first's QR code was being fetched, so that fetch was refused.
  - The error was never cleared after the next QR code loaded.
- Fix: starting a link is now single-flight (a concurrent start reuses the one in progress), and a QR code that loads clears the error.
- Checked: `npm run typecheck` passes. A re-captured dev-server screenshot shows one link and no error. Playwright `npm run e2e` (Chrome) passes. The production build has no double mount, so the e2e suite could not have caught this.
- Screenshots of the current UI: `docs/screenshots/ar/` (Design stage AR view, AR panel, calibrator, Link a phone, phone page). The link codes they show were stopped right after capture.

## 2026-11-21 — Site scan (video AR): rebuild the real site in 3D and design inside it

- Applications: Python (`server/studio/scan/`; tasks `scan_probe`, `scan_reconstruct`, `scan_scale`, `scan_floor`, `apply_scene`); schemas and contracts (`SiteScan` and related models); Java (streaming video upload, `/api/v1/projects/{pid}/scans`, `/apply-scene`, the phone video route); client (Design stage "My site" view and its tools). Contract: `docs/SITE_SCAN.md`.
- Plan:
  - A walk-around video from any phone becomes a coloured 3D mesh, at true scale once a sheet or a measured distance is given.
  - The pipeline: our own structure-from-motion (OpenCV + SciPy) with one shared camera, so the clip has one focal length; then MoGe-2 depth aligned to the sparse points; then TSDF fusion (Open3D).
  - Users place the robot, add objects, move task points onto real surfaces and measure, then apply the edits as a new design version computed in code (no LLM).
- This is a visual 3D reconstruction, not a survey. Scan geometry is not part of the pass/fail checks in this delivery; added objects are, as modelled scene boxes.
- Environment: pycolmap was tried and rejected, because Windows Application Control blocked its native DLL. Installed into `.venv`: av 19.0.2 and open3d 0.20.0 with its dependencies (`requirements-scan.txt`).
- What was built:
  - **Python:** `server/studio/scan/`, covering video probe and keyframes (PyAV) and our own SfM.
    - The SfM uses RootSIFT matching across frame offsets 1–12, essential-matrix initialisation with a field-of-view sweep, PnP registration and SciPy bundle adjustment with one shared (f, k1) camera.
    - MoGe-2 depth is aligned to the sparse points, then fused with Open3D's tensor `VoxelBlockGrid` TSDF. The legacy `ScalableTSDFVolume` in the 0.20 wheel integrated nothing.
    - Floor and scale come from a sheet in a frame or a measured distance.
  - **Deterministic `apply_scene`:** no LLM unless auto-repair is asked for. It moves markers and objects, validates, re-sizes and re-simulates.
  - **Java:** streaming video upload (500 MB, exempt from the 64 MB body filter on that route only), scan storage and routes with HTTP Range, and `/apply-scene`.
  - **Phone link:** "Record a site video", sent to a new socket-based phone listener. Video gets a 15 min window only after a valid code; it answers wrong codes without reading the body, and has at most 32 open connections and 8 requests at a time.
  - **Client:** a three-way view switch next to "Simulated task" (Modelled scene / My photo / My site), and the My site panel with its tools (place robot, add object, task point, measure, set scale, pick floor) and Apply to design.
- Checked (2026-11-21, Windows 11, RTX 4050; synthetic test video, fixture LLM, no real phone footage):
  - The synthetic room video comes from `tests/scan_synth.py`: about 5 × 4 m, ray-cast at f = 700 px, 30 s.
    - 90 of 90 frames registered, reprojection 0.40 px.
    - Focal 699.9 px against 700, floor normal 0.05° off.
    - After the sheet scale (residual 0.0005), walls 5.011 m and 4.007 m (0.2% error), table top 0.738 m against 0.75 (1.6%).
    - Mesh 300k triangles. Reconstruction about 2–4 min.
  - The synthetic video is ideal footage (no motion blur, rolling shutter, lens distortion or bad light), so real phone clips will be less accurate. That has not been measured.
  - pytest: 228 passed, 6 skipped (3 min 35 s, including the GPU reconstruction).
  - Java (`mvnw test`): 55 run, 1 error (the known Windows-only `KeysAndUsageTest`), 7 skipped.
  - Playwright `npm run e2e` (Chrome): 17 of 17 passed in 4.1 min.
    - The scan suite uploads through the UI and reconstructs on the GPU, then sets the scale from the sheet in a frame.
    - It measures 4.990 m between walls (true 5 m) and places the robot.
    - It adds a table and puts drop point B on it (z = 0.750 m), then applies to the design. That gives v2 with `parent_version` 1 and "Edited in the site scan".
    - It also sends a video from an emulated Pixel 7, and checks all three views with no console errors.
  - `npm run typecheck` passes, and the core tests pass 12 of 12.
- Known issue: during the worker's runs, one reconstruction failed once with an out-of-memory error in the Python job and passed on retry with nothing changed. It did not recur in the final run. The machine has 16 GB RAM, and the cause has not been investigated.
- Not checked: real phone footage, a 180-frame clip at 1600 px, and a 500 MB phone upload over real Wi-Fi.
- Screenshots: `docs/screenshots/site/`.

## 2026-11-21 — Site scan: undistorted keyframes, finer lit mesh

- Applications: Python `server/studio/scan/` (dense.py, scale.py), the `ScanCamera.undistorted` field (default false; contracts regenerated), `docs/SITE_SCAN.md`.
- Behaviour:
  - Registered keyframes are rewritten undistorted with the recovered k1 and the same K, so they are pure pinhole images. `camera.undistorted` is true, and `scan_scale` no longer applies k1 to corners clicked on them.
  - The mesh is finer: voxel = extent/384 (falling back to /320 when out of memory), capped at 600k triangles.
  - The mesh is cleaned up: Taubin smoothing, removal of degenerate, duplicate and small floating parts, and vertex normals in the GLB.
  - Vertex colours come from every depth-tested view, weighted by sharpness and viewing angle, with the blurriest 20% of frames skipped and a 2-pass bilateral filter.
  - The TSDF's initial block capacity is cut from 100000 to 20000 (it grows on demand), after a CPU allocation failure with about 5 GB RAM free. This is a likely cause of the earlier one-off out-of-memory failure.
- Checked (2026-11-21, synthetic clip; not real footage): 90/90 registered, focal 0.01%, floor 0.05°, walls 0.21% and 0.17%, table 1.58%. The mesh has 599,999 triangles with normals, and reconstruction took 142–153 s (was 131 s). pytest: 234 passed, 6 skipped.

## 2026-11-21 — Site scan: "Through the camera" view, Focus robot, lit 3D view

- Applications: client `client/src/components/scan/` (SiteLayer, SiteWorkspace), `SceneView.tsx`, `styles.css`; core `packages/core/src/scan.ts`; `e2e/` (E2E_PORT and E2E_RUNTIME overrides).
- Behaviour:
  - "Through the camera" shows an undistorted keyframe as the background, with the camera from the shared intrinsics and the frame pose. The scan mesh is drawn depth-only, so real walls hide the robot, and it catches the robot's shadow. A walk scrubber, Play walk (6 fps) and "Best view of the robot" move along the recording.
  - The 3D view zooms to the cursor, with a scale-aware minDistance and damping. "Focus robot" and "Reset view" buttons; double-click sets the orbit target. The mesh is lit with hemisphere and directional light, with shadows and fog.
- Checked (2026-11-21, synthetic clip, fixture LLM): e2e 19 of 19 (Chrome). The robot fills 60.5% of the canvas height after Focus robot (15.7% before). Camera-view reprojection is 2.56 px mean and 2.85 px worst (limit 4 px) on one frame pair. Typecheck is clean and core tests pass 15 of 15. Not checked: real phone footage.