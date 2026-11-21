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

## 2026-11-20 — Collision auto-repair, question comments and images, background jobs, fleet planner (built; results in the entry "Real-model verification of the five features")

- Plan: an automatic LLM revise loop for collisions, at most 3 attempts verified in code, keeping the best attempt with an attempt log; per-question comments and a note for each round; images attached to answers and sent to the spec agent; jobs that survive stage and project switches; a multi-robot fleet planner for the Design stage (fixed-base members, handover stations, 2–3 robots).
- Status (2026-11-20 ~19:55 IST): all five parts are coded. Fixture tests only: `./mvnw test` 36 tests, 0 failures (golden contract test unchanged); pytest 182 passed, 4 skipped; contracts check, client typecheck and build pass. Not yet done: a Java full-flow fleet test on the real Python tasks, any real-model run (the one attempt failed on the Claude CLI 5-hour usage limit, not on code), and any browser check beyond one: a job error stays visible after switching stages. Robot checks are kinematic and static-load calculations in code. Work handed over in `docs/HANDOFF.md`.

## 2026-11-20 — Callable API: OpenAPI 3.1 spec, results in responses, callbacks, cancel, idempotency

- Applications: Studio server (Java), contracts, `packages/core`, docs, examples. Design: [docs/API_DESIGN.md](API_DESIGN.md); caller guide: [docs/API.md](API.md).
- Every feature is callable by another application under `/api/v1`: projects, reference, question rounds with comments, notes and answer images, design, versions and assets, revise (base version, moment, auto repair), fleet, approve, search, analyse, datasheet, decision, export, jobs, LLM settings and usage.
- Spec: `contracts/openapi.yaml` (OpenAPI 3.1, 43 operations; domain types `$ref` the pydantic-generated JSON Schemas), bundled to `contracts/openapi.bundled.json` and served at `GET /api/v1/openapi.json`. springdoc removed. TS path types are generated into `packages/core`. `contracts:check` fails on lint errors or stale outputs.
- Results in responses: `?wait=0..600` and `expand=resource` on job-starting calls and `GET /jobs/{id}` (200 when done, 202 plus `Location` when still running; the resource is the version, candidate list, analysis or questions). Signed callbacks via `X-Studio-Callback` (host allowlist, HMAC-SHA256, retries). `DELETE /jobs/{id}` cancels a job (kills the process tree). `Idempotency-Key` makes starts safe to retry. Errors are `{detail, error_code?}`.
- Tests (fixture: scripted LLMs, real Python tasks, no network): `./mvnw test` 40 passed. They include `OpenApiContractTest` (route parity in both directions), a spec validator applied to every JSON response of the flow tests, and `ApiCallableTest` (wait, expand, callback signature, 202 then poll, cancel, idempotent replay, 422 cases). `GoldenContractTest` passes with no new allow-list entries. pytest 182 passed, 4 skipped; typecheck and build pass.
- `examples/api_flow.py` (standard library only) ran end to end against a separately built jar (port 8801, its own runtime, scripted profiles): create, a question round with a comment, design, revise from v1 at t = 3.0 s, approve, search, analyse (4 verified values, differences), decision, export ZIP. Exit 0. Fixture run, not a real model.
- Not covered: a real-model run through the API, and the 64 MB body limit. The user's dev server (8800) still runs the older jar; it gets these routes after `npm run setup` rebuilds the jar.

## 2026-11-20 — Animated 3D page artwork

- Studio client: added eight page-specific robotics illustrations with embedded GLB motion and matching WebP previews in `client/public/robotics-artifacts/`. Each page starts with a still and loads the 3D view through its play control. The Brief uses the pick-and-place cell in the hero.
- Asset source: `scripts/robotics-artifacts.mjs` regenerates the geometry, materials and embedded clips. The asset folder describes where each model is used.
- Verification: client typecheck and production build passed. Three.js loaded all eight GLBs and verified that each clip moves and loops. Chrome on an isolated fixture server checked all eight page placements, playback, pause, and the headers at 320 pixels. The running Java app served all 16 GLB and WebP files byte-for-byte. The previews illustrate motion for the UI.

## 2026-11-20 — Real-model verification of the five features (and of the callable API with a real model)

- How it was run:
  - Real Claude through the Claude Code CLI: Opus 5.5 for spec, robot and revise; Sonnet 5.5 for scene and search.
    About 26 model calls on the three verification projects (about 1.8 USD at list prices).
  - A separately built jar on port 8801 used a copy of the runtime, so the user's dev server stayed the only writer
    of the real data. Vite ran on 5191 in the browser pane, and some steps went through the `/api/v1` calls with
    `wait` and `expand`.
  - Checks are kinematic and static-load calculations only, not dynamics or hardware.
- (2) and (4), question comments and answer images: verified. On a vague brief ("Move boxes from our packing table
  onto a shelf") Opus asked 6 questions, returned in the response of `POST /design?wait&expand=resource`. In the UI
  a comment was added to "What are the boxes like?", a round note ("stay inside a 2 m x 2 m cell") was added, and a
  photo was attached to that question.
  - The spec call carried 1 image (usage ledger), and the next round produced a design.
  - The spec's assumptions cite all three: "Parallel side grip is used instead of suction, because tape ridges can
    break the vacuum seal and the photo shows a parallel gripper" and "inside the 2 m x 2 m cell".
- (3), jobs survive switches: verified. While a design ran, the stage was switched, the project was switched and the
  page was reloaded. The project list showed "(working)" and the Running list showed the job. The result arrived, and
  the project has exactly one design job (no duplicate).
- (1), collision auto-repair: NOT exercised with a real model; covered by the fixture tests only.
  - On the wall project (a 40 cm wall between A and B), every real design or revise came out collision-free (4
    tries). The model kept a 0.5 m approach height, or added an over-the-wall waypoint itself, even when told to set
    a 5 cm approach height. With no collisions, no repair log is produced, as designed.
  - Finding: the first design split the brief's continuous 1 m wall into two 18 cm pieces. The base is placed at the
    A–B midpoint, which lies inside the wall, and the scene validator's 0.3 m base clearance then forces the scene
    agent to cut the wall. A revise that mounted the base on a side table (x = 0.85 m) restored the continuous wall.
    Follow-up: place the base away from obstacles the brief names, instead of letting the scene change them.
- (5), fleet planner: verified with these limits.
  - Carton task: 5 kg cartons, conveyors at x = ±2.5 m, a 10 s cycle, "our arms reach at most 1.3 m". Code
    suggested a fleet ("One robot needs 10.2 s per cycle, more than the 10 s cycle time"). Opus had already put two
    hand-off tables into the spec.
  - With limits of 3 robots and 1.3 m reach, auto-assign answered infeasible with reasons (sized reach 1.53–1.63 m
    per robot, from floor-mounted bases). 4 robots is refused: the scope allows 2–3.
  - With the reach limit relaxed to 1.7 m: 3 serial arms, 4 of 4 fleet checks pass, every robot passes its own
    checks, smallest robot gap 0.128 m. The 3D view plays all three robots on the shared 10 s clock.
  - The revise "Make r2 a gantry." (from v4 at t = 4 s) gave v5 based on v4, with r2 re-sized as a gantry and 4 of 4
    fleet checks passing (gap 0.428 m).
  - A web search for member r2 got r2's gantry requirements but returned arms that meet them (UR15 at 58,284 USD,
    seen 2026-11-20; UR10e).
  - Not done: a handover edit, because this split had no planner handovers (the spec's own tables are used).
  - Findings / follow-ups:
    1. Bases are always on the floor. A pedestal at table height would let a 1.3 m arm do this task.
    2. `fleet.trigger` is recorded as `["user"]` even when code suggested the fleet.
    3. The member search treats the family only as a hint.
    4. The default 3D camera is far out for long cells.
- Shell mistake in the verification script (not product code): zsh's `echo` broke JSON parsing, which started two
  extra design runs (v1–v2 of the carton project are duplicates).

## 2026-11-20 — Autoplaying robot animations, editable brief, images on the brief

- Client: the "Play motion" and play/pause buttons on the stage robot animations are gone. Each animation loads and plays when at least 25 % of it is in view, pauses when it leaves the view, and resumes when it returns. With the OS "reduce motion" setting it stays a still image. The image-attach UI is shared (`components/ImageAttach.tsx`) by the brief and the questions.
- Editable brief: `PUT /api/v1/projects/{pid}/brief`. The Brief page has Edit / Save / Cancel. Saving clears pending questions so the spec rounds restart with the new brief; earlier answers, comments, notes and existing versions are kept. The page shows "Edited <date>". Editing is refused (409) once a version is approved or while a design or revise job runs.
- Brief images (photos of the task, objects or workcell): `POST/GET/DELETE /api/v1/projects/{pid}/brief/files`. Images can be attached on the new-project form (file, drop or paste; uploaded after the project is created) and on the Brief page. The spec agent receives them first, labelled `brief:<name>`, with an instruction that text inside them is data. Brief, reference and answer images share the 8-image budget. Spec (`contracts/openapi.yaml`), protocol and generated types updated. Design: `docs/API_DESIGN.md` section 9.
- Checks:
  - Fixture tests: `./mvnw test` 43 passed (new `BriefFlowTest`; golden and spec-parity tests unchanged); pytest 187 passed, 4 skipped; contracts check, typecheck and build pass.
  - Browser (dev server rebuilt and restarted, no model calls): the hero animation plays with no button. A dropped photo shows as a thumbnail, and after "Create project" it is stored with the project. Editing the brief saved and shows "Edited ...".
  - Not yet run: a real-model design that uses a brief photo.

## 2026-11-20 — Robot studio planned (in progress)

- Plan approved for a "Robot studio" stage: from an approved design, generate parametric CAD (STEP and STL, a URDF with meshes and working joints), choose motors, gearboxes and electronics from India and global shops (or define the spec), add a controllable digital twin, and produce a manufacturer PDF and package after approval. See `docs/HANDOFF.md` and the plan referenced there.
- Configuration: `cadquery-ocp==8.0.1.1.0` (OpenCascade), `reportlab==4.4.4` and `matplotlib==3.10.7` added to `requirements.txt` and installed in `.venv` (OCP smoke test only). No feature code yet.

## 2026-11-20 — Robot studio: from an approved design to a buildable robot (first delivery)

- Applications: Python (`server/studio/robot/`: `sizing.py`, `sourcing.py`, `style.py`, `cad/*`, `urdf_mesh.py`, `twin.py`, `report.py`; `pipeline/studio.py`; tasks `studio_build`, `studio_twin_move`, `studio_run_task`, `studio_pdf`), Java (`StudioController`, `StudioService`, `StudioStore`, job kinds `studio_build`/`studio_pdf`, 10 routes under `/api/v1/projects/{pid}/studio`, OpenAPI paths), core (`packages/core/src/twin.ts`, parity vectors `contracts/twin_vectors.json`, checked by `npm run contracts:check`), client (new "Robot studio" stage after Design, motor table with "Use this one", STL meshes in the 3D view, twin panel, change request, approve with snapshots, PDF and package downloads; Decision links to it). Design reference: `docs/STUDIO_ROBOT_DESIGN.md`.
- Behaviour: a build sizes every joint in code (static load over the task frames and a stretched pose, acceleration term, RMS continuous × 1.25, peak × 1.5), searches allow-listed shops once, keeps a motor only when its deciding values are quoted on the fetched page, picks the cheapest passing motor or writes a "defined" spec to procure to, adds rule-based electronics, lets the robot role propose a structure style that code checks (beam bending and deflection, print envelope, printed-part loads), generates concept-level CAD with OpenCascade (STEP assembly and parts, STL, a mesh URDF with a gripper joint), and derives twin limits from the chosen motors. A pin-only rebuild reuses the base build's alternatives without a new search.
- Fixture tests (scripted LLMs, saved shop pages; real CAD, kinematics and twin code), as run 2026-11-20: pytest 273 passed, 4 skipped; `./mvnw test` 47 tests, 0 failures, including `StudioApiTest` (fake engine) and `StudioFlowTest` (real Python tasks: design → approve → build → files → twin move/run → snapshots → approve → PDF/package → pinned rebuild); contracts check with twin parity (15 vector groups) up to date; client typecheck and build pass.
- Fixes found while testing: the CAD link-envelope check failed on every real-motor arm build because it did not allow for motors that stick out of thin design envelopes (now it allows the chosen motor's length and still catches a part placed 300 mm off); the twin panel stayed on "Asking the twin" in the dev build because React StrictMode left its mounted flag false (fixed).
- Real run (2026-11-20, browser pane on a copy of the runtime, project "Tabletop bottle transfer A to B", approved v2; search, extract and analysis roles: Claude Sonnet 5.5 via the Claude Code CLI with web search; robot role: Claude Opus 5.5): build b1 finished with 4 joints, 39 parts, 61 files, 21 of 21 checks passing, structure style machined aluminium (proposed by the model, kept by code). The shop search returned 5 stepper candidates, all from pololu.com (product pages 1478, 1474, 2267); none passed, because none states a rated speed on its page (the rule requires the deciding values to be verified), so all 4 motors are defined specs. No price was captured and no Indian shop listing came back. The twin ran the task (7.06 s, peak torque use 31 % at j3); at speed scale 2 the speed and acceleration limits bound and were shown. Approving uploaded four snapshots and produced a 13-page manufacturer PDF, `package.zip` and `bom.csv`.
- Limitations: concept-level envelope CAD (simplified profiles, no interference analysis, no FEA); sizing is kinematic, static and acceleration only (no dynamics simulation, not tested on hardware); prices are dated web estimates; gripper fingers are not symmetric about the tool point; the client uses the server for tool moves (no local IK chain from the URDF yet). Open question for the user: how to judge stepper speed when shops publish only holding torque.

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

## 2026-11-21 — Robot studio: conservative assumed stepper speed (user decision)

- Applications: Python `server/studio/robot/sizing.py` (`STEPPER_USABLE_RPM`, `stepper_frame`, `capability`, `evaluate`), `sourcing.py` (docstring), tests, `docs/STUDIO_ROBOT_DESIGN.md`.
- Behaviour: a stepper whose shop page states holding torque, current and voltage but no rated speed is now judged at a conservative usable speed at 24 V (NEMA 8–23: 300 rpm, NEMA 34 and unknown frames: 200 rpm), recorded as an `assumed` value with a note and named in the speed check ("speed assumed (conservative)"). Torque and current must still be verified on the page; BLDC, servo and DC motors still need a verified speed. This is a planning assumption, not a manufacturer value.
- Checked: pytest 277 passed, 4 skipped (fixture tests); `./mvnw test` 47, 0 failures. Re-running the selection offline on the real b1 candidates (Pololu 1478, 1474, 2267): the steppers are now evaluated instead of rejected and fail on torque and speed (for example j2 needs 98 N*m peak at 0.99 rad/s; the best candidate gives 55 N*m at 0.50 rad/s), so b1's motors stay defined specs, now for a measured reason. No new model call.

## 2026-11-21 — Rejected model answers are fed back up to 3 times

- Applications: Python `server/studio/llm/base.py` (`ask_json`, `JSON_REPAIRS = 3`), used by every agent (spec, scene, robot, revise, search, extract, analysis, style).
- Behaviour: when a model's answer is not valid JSON or a code check rejects it (for example the scene check "Marker "bottle_start" is at z = 0.75 with nothing under it: add a table or shelf ..."), the request is sent again with every earlier error listed, up to 3 repairs (4 calls in all) instead of 1. Only then does the job fail, with "... after 4 attempts: <last error>". Code still decides whether the answer is usable. A call can lower the count with `repairs=`.
- Checked: new `tests/test_ask_json.py` (repair until a passing answer, all earlier errors listed, give up after 4, per-call limit); `tests/test_fleet_pipeline.py` updated for the new call count; pytest 280 passed, 4 skipped; `./mvnw test` 47, 0 failures. Fixture tests only. The dev server picks this up without a restart because the Python tasks run from the source tree.

## 2026-11-21 — IN PROGRESS: realistic 3D models for task objects (client)

- Applications: client `client/src/components/objectModels.tsx` (new), `client/src/components/SceneView.tsx` (`ObjectMesh` uses it; Player, RobotView and the Robot studio twin draw objects through it).
- Behaviour: task objects whose id (or name, when passed) contains a known word are drawn as procedural three.js models instead of a plain box, cylinder or sphere. The models are mug, cup, bottle (lathe body, shoulder, neck, ribbed cap, label band; translucent plastic or green glass), can, apple (dimples, stem, leaf), orange, ball, egg, tote, dumbbell, book, pallet, screwdriver and box/carton (bevelled, tape strip). Each model fills the same bounding box as the primitive it replaces, so grasp points and the carried-object offset are unchanged. Unknown objects keep the old primitive. No downloaded assets and no new dependencies; noise uses a seeded hash.
- Checked: `npm run typecheck` and `npm run build` pass. The keyword matcher was checked in a throwaway script (8 cases). NOT yet checked: how the models look in the browser (the one attempt was too small to judge), the mug handle and tote cut-outs in particular.
- Limitation: `SceneObject` has no `name`, so matching uses the object id (e.g. `bottle`); passing the spec object's name would need a scene schema field.

## 2026-11-21 — Object models generated by a small model (objects agent on Claude Haiku)

- Applications: Studio server (Python agent and pipeline, Java LLM settings), client (scene rendering), contracts.
- New `objects` role (default profile `claude_haiku`, Claude Haiku 5.5 through the Claude Code CLI) and `agents/objects.py`. From the spec's task objects (name, size, mass, shape, grip) it builds a model of each object from up to 24 primitives (box, cylinder, sphere, cone, capsule, torus) with colours and materials.
- Code checks every model: one per object, every part inside the object's real box (2 % tolerance), at least 90 % coverage per axis (so grasp points and the carried-object offset stay right), and valid numbers and colours.
- It runs alongside the scene and robot agents in `design()`. Models are reused on revise when an object is unchanged, and fleets carry them over. A failure never breaks a design: the scene then uses the keyword models from `objectModels.tsx`, or the plain primitive. `SceneObject` gains optional `name` and `model`, left out of the JSON when empty, so the goldens' design and revise responses are unchanged.
- Four golden files (LLM settings responses) were edited by hand for the new role and profile; this is noted in the golden README.
- Checks:
  - Fixture tests: pytest 291 passed, 4 skipped (11 new); `./mvnw test` 49 passed; contracts check, typecheck and build pass.
  - Real model: one direct Haiku call for a bottle, a carton and a mug took about 15–19 s and passed validation. The bottle came back as body, shoulder cone, neck and cap; the carton as box and tape strip; the mug as body and torus handle.
  - Real design run (Opus, Sonnet and Haiku): "Object models: 1 made". In the browser the 4-part bottle stands upright at point A at the right size and travels with the gripper during the carry.
- The dev server was rebuilt and restarted with Robot studio and the objects agent (studio routes answer 200).
- The other agent's procedural bottle was too small to judge at the default camera. The generated model now takes precedence wherever the agent succeeds.

## 2026-11-21 — Decision between own design and catalog robot; time saved; preferred vendors; realistic motor sizing and sourcing

- **Decision stage.** The user chooses "Our own design" (the approved Robot studio build) or "Buy a robot" (an analysed candidate).
  - `POST /decision {action: "self", build?}` needs an approved build (409 otherwise) and records `{action: "self", build}`; the candidate path is unchanged.
  - The own-design card shows build facts, an estimated cost per currency (web estimates; "defined spec" items counted separately) and checks, with "Download manufacturer PDF" and "Download build package".
  - The Decision stage unlocks with an approved build or an analysed candidate.
- **Time saved.**
  - `TechnicalSpec.baseline` (manual time per item, operators): the spec agent asks for it with estimated defaults. It is absent unless the user answered or the agent produced it, and agent values are always marked as estimates.
  - `PUT /projects/{pid}/baseline` stores overrides and assumptions (hours per shift, days per week and year, supervision minutes per shift).
  - One savings function in Python (`physics/savings.py`) and TS (`packages/core/src/savings.ts`), checked by parity vectors in `contracts:check`.
  - Shown in a "Time saved" panel on the Design stage (labour hours freed per day, week and year; items per hour; throughput; the working), as a line on each Decision card, and as a manufacturer-PDF section.
- **Preferred vendors.**
  - A global list in Settings: `GET/PUT /api/v1/settings/vendors`, stored in `runtime/settings/vendors.json` (Java writes it).
  - Catalog search and motor/electronics sourcing search preferred vendors first and rank them higher, with a "Preferred vendor" badge; other vendors still appear.
- **Sizing (`robot/sizing.py`).**
  - Acceleration torque uses the simulated trajectory's per-frame acceleration (smoothed) at the same frame as the static load, instead of ω / 0.2 s. `ACCEL_RAMP_S` is no longer used for joints.
  - The stretched-pose hold (rated payload) is static only and carries the continuous factor 1.25. Task-frame demand carries the peak factor 1.5.
  - Gear ratio: the smallest standard ratio that fits both torque and speed. The row names the binding constraint when none fits.
  - Bottle-arm peaks (design v1), fixture calculation, before → after (without / with motor-mass feedback):

    | Joint | Before | After, without motor masses | After, with motor masses |
    |---|---|---|---|
    | j1 | 112 N·m | 3.5 N·m | 4.7 N·m |
    | j2 | 165 N·m | 84 N·m | 107 N·m |
    | j3 | 45 N·m | 29.5 N·m | 34 N·m |
    | j4 | 5.0 N·m | 3.7 N·m | 3.7 N·m |

    j2 is governed by the stretched hold of this old, oversized arm (1.6 m reach for a 0.95 m task).
- **Sourcing (`robot/sourcing.py`).**
  - Indian shops first, plus preferred vendors; at least 2 candidates per joint class.
  - A rejection reason per alternative: `no_price`, `page_blocked`, `quote_not_found`, `price_not_in_quote`, `missing_values: …`, `fails_margin: …`. The build also gets `sourcing_notes`.
  - Prices from shop pages that block the server's own check are kept as "unverified": shown, but never ranked or summed.
  - New electronics search: drivers and power supplies can be shop items.
  - Shown in the studio motor table and the PDF.
- **Checks (fixture tests only).** pytest 321 passed, 4 skipped; `./mvnw test` 52, 0 failures (goldens unchanged); `contracts:check` (types, OpenAPI, twin and savings parity), typecheck and build pass.
- **Real model runs.** Before this work, real build b1 of the bottle arm (Sonnet search with web, CAD) gave all defined specs and Pololu-only candidates with no prices; that motivated the sizing and sourcing fixes. Approving it produced a 15-page PDF (BOM, motor specs, file hashes, assumptions).
- **Not yet verified with real models** (paused at 89 % of the 5-hour usage window): a rebuild (b2) with the new sizing and sourcing, the baseline questions on a new brief, a preferred-vendor search, and the Decision page in the browser.

## 2026-11-21 (night) — Real verification of the Decision/time-saved/vendor/sourcing round; three fixes from it

Affected: Python engine (`llm/base.py`, `robot/sizing.py`, `robot/sourcing.py`), tests. No Java, client or contract change.

- **Browser checks (dev server 8800/5190, project p_4775b16f5c).**
  - Decision: the own-design and FANUC CRX-5iA choices are recorded and shown, and the PDF answers 200 (see the handoff).
  - Design stage "Time saved" panel, with the working open: v2 (simulated cycle 4.603 s) shows 20.61 h/day, 103 h/week and 5151 h/year, 782.1 against 300 items/h, +160.7 %. Every line was hand-checked against the formulas.
  - The Decision cards (78.75 h/week) use build b1, which is built on v1 (6 s cycle). The difference is the version, not an error.
- **Settings: preferred vendors** Robu (robu.in; robots, motors, electronics) and StepperOnline (omc-stepperonline.com; motors, electronics) were added in the UI. `GET /settings/vendors` returns both.
- **Real model inference, Claude Opus 5.5 (spec role), 2026-11-21.** Vague brief "VERIFY (Claude test): … small boxes of tea into a carton …" (p_9a798e817e).
  - The first run failed: the Claude CLI's strict `--json-schema` rejected the `x-optional` marker on `TechnicalSpec.baseline`, so every real spec call failed. **Fix:** `portable_schema` drops `x-` keywords and keeps field names (test added).
  - After the fix, six questions were asked, including `manual_cycle_s` (default 4 s) and `operators` (default 2), each with "the default is an estimate" in `why`.
- **Real build b2 of p_4775b16f5c (Claude Sonnet 5.5 search with web, CAD), 2026-11-21, search date 2026-11-20.** Not approved.
  - Peaks (design v1): j1 4.6, j2 101, j3 31.6, j4 3.7 N·m. j2 is still governed by the stretched hold of the oversized v1 arm.
  - j4 is a shop item: robokits NEMA17 7 kgcm (RMCS-1020), INR 801, price verified.
  - j1–j3 stayed defined specs. The rejection reasons were `missing_values: verified voltage` (Nema23 28 kgcm) and `fails_margin` (torque/speed ratio conflicts).
  - No candidates came from the preferred vendors. Sonnet proposed only robokits motors, although the vendor block was in the prompt.
  - No unverified prices.
- **Two engineering errors found in b2, fixed (fixture tests only; not yet re-run with a real model).**
  - A NEMA17 with a 2.5 V coil got its own "2.5 V" stepper driver and a 2.5 V power supply. Steppers run from a chopper bus: `electronics_for` now uses `STEPPER_BUS_V` (24 V) when the coil is rated lower, and notes the coil voltage.
  - A brushed DC driver (Pololu G2 24v21) was chosen for the stepper joints j1–j3, because the driver type was never checked. Driver needs now carry `driver_type` (stepper, dc or bldc), and the search prompt asks for it. A candidate's type counts only with a verified page quote containing the type's words, and code decides (`DRIVER_TYPE_WORDS`). A missing type is `missing_values: verified driver type`; a wrong type is `fails_margin: driver type dc cannot drive stepper motors`.
- **Checks.** pytest 324 passed, 4 skipped; `./mvnw test` 52, 0 failures; `contracts:check` passes. Typecheck and build were not re-run (no client change).
- **Not done.** Real build b3, to confirm the electronics fixes, was refused by the Claude CLI: the 5-hour usage limit was reached (resets 06:20 IST).

## 2026-11-21 — Ask bar, client panel (W6b)
- **Applications:** web client only (`client/src/components/AskBar.tsx`, App, Design, Studio, Decision, Settings, styles, icons).
- **Behavior.** A floating Ask button at the bottom right of every screen, and Cmd/Ctrl-K, open a non-modal panel (Escape closes; focus moves to the input and back to the button). The conversation lives in React state for the session only. Enter sends, Shift+Enter adds a line. The last 6 turns are sent as history, with a context of project id, stage, and the viewed version, build or candidate (ids only, no numbers from the screen). The player time is not sent yet.
- **Answers.** Markdown (paragraphs, lists, bold, code, line breaks) is rendered by a small local renderer, with no raw HTML. Under an answer: citation chips (version, build and candidate citations jump to that item), "Actions taken" lines with done or failed state, and confirm chips (approve, approve build, decision) that ask "Confirm / Cancel" before calling the existing route, then reload the project. A `navigate` result switches stage when that stage is open.
- **Selection.** Design, Robot studio and Decision publish what they show through `useAskSelection`; Design and Robot studio take a requested version or build through `useAskRequest`.
- **Settings.** The hard-coded role list gained `ask` and `checks` entries.
- **Checks.** `npm run typecheck` and `npm run build` pass. Not tried against the real server (`POST /api/v1/ask` is being built separately); no browser check yet. The panel uses local interfaces for the ask request and response, to be replaced by generated types when they exist.

## 2026-11-21 — Ask bar, server side (W6a)
- **Applications:** Java server (`AskController`, `AskService`, `AskGrounding`, `ProjectStore.appendActivity`, `LlmSettings`), Python engine (`agents/ask.py`, task `ask`, schemas, role `ask`), contracts, docs.
- **Behavior.** `POST /api/v1/ask` starts an `ask` job. Java builds the grounding from stored data for the ids in the request (project, version, moment, build, candidate; no frames, at most about 60 KB) and the Python `ask` task answers from it with citations. Python never performs an action (it cannot call back): its tools only record up to two proposals, and Java validates and runs them through the same service methods the REST routes use (revise, design, search, analyse, studio_build, update_brief, set_baseline, cancel_job). Anything else is refused ("not allowed"), a third action is refused, approvals and decisions come back as confirm chips and are never executed. Each executed or refused action is appended to the project's `meta.activity` (last 200). Citations that are not in the grounding are dropped.
- **Roles.** New role `ask` (default profile `claude_sonnet`), added to the Python registry, the Java `LlmSettings`, their tests and the golden files that list roles. The client's role list was already extended by W6b.
- **Contracts.** `/ask` path, `AskRequest` and `AskResponse` schemas, job kind `ask`; generated types and the bundle were regenerated.
- **Checks (fixture tests only, no model call).** Scripted-LLM Python tests (two actions recorded and a third rejected, unknown citations dropped, confirm chips passed through, malformed confirm sent back to the model, the `ask` task through the subprocess entry point) and Java `AskApiTest` against the fake engine (revise proposal executed with a real job and an activity entry, `delete_project` refused, cap at 2, approve becomes a chip, 404 and 422 cases, grounding without frames and within 60 KB, moment taken from the stored frame). pytest 340 passed, 4 skipped; `./mvnw -q test` 59 tests, 0 failures; `contracts:check` passes.
- **Limitations.** No real model was asked anything, so answer quality, citation behavior and prompt-injection resistance of the real `ask` role are not evaluated. The client was not run against this server in this step.

## 2026-11-21 — Generic motions: rotate, attached loads, at-most limits, timed moves (W4)

- Applications: Studio server (schemas, spec agent, robot agent prompt, gantry family, physics requirements, task simulator), generated contracts and types. Part A of `docs/CHECKS_AND_ASK_DESIGN.md`.
- Behaviour: `TaskAction` gains verb `rotate` (`turns`, `rpm`, `ramp_s`, `axis`) played on `tool_yaw` (or the named joint) with a trapezoid profile, not scaled by the cycle stretch; `duration_s` of `move`/`rotate` is an optional upper time (a faster move is stretched to it, a slower one fails `move_time_<n>`). `TaskObject.attached` holds an object from t=0 so its mass is in every load (the spin-mill lift load now includes the 5,000 kg cabin). `PhysicsInputs.max_accel_m_s2` / `max_jerk_m_s3` add requirements `max_accel` (peak of the TCP acceleration and the rim acceleration of a rotated object, from code) and `max_jerk` (no verdict: trapezoid profiles do not bound jerk). Gantry param `tool_yaw_continuous` gives a continuous yaw joint; limited joints fail `rotate_range_<joint>` when the swept range does not fit; joints moved by a rotate are excluded from `joint_limits` and are not unwound by later IK or home. New fields are x-optional and dropped from JSON when default, so stored versions and existing outputs are unchanged. The spec agent prompt and validator document and check the new fields (rpm > 0, turns != 0, no target; dwells plus move/rotate times must fit the cycle); the robot agent prompt mentions `tool_yaw_continuous`.
- Checked (fixture tests only, scripted/numeric; no real model inference, no hardware, no dynamics simulation): new `tests/test_motions.py` (spin-mill fixture: 5 turns swept, 30 s lift, cabin in the z load, `max_accel` fails with the rim acceleration, continuous vs limited range, too-short timed move, missing joint, serialization, URDF); full Python suite and `./mvnw -q test` pass; `contracts:check` and `typecheck` pass. The golden `02_families.json` gained the new gantry param in its `params_schema`.
- Limitations: the real spin-mill project was not re-run; the `tcp_speed` requirement still uses the whole cycle minus dwell (rotate and timed-move time is not subtracted); the studio twin still models `tool_yaw` as limited.


## 2026-11-21 — Task-specific checks agent (W5)

- Applications: Studio server (`physics/quantities.py`, `physics/formulas.py`, `agents/checks.py`, `pipeline/checks.py`, `pipeline/design.py`, `robot/sizing.py`, `robot/report.py`, `physics/requirements.py`, schemas, LLM registry), Java server (role list), web client (`Requirements.tsx`), generated contracts and types. Part B of `docs/CHECKS_AND_ASK_DESIGN.md`.
- Behavior: a new role `checks` (default profile `claude_opus`) plans up to 12 task-specific checks after the simulation, in `design` and `revise`, before the collision repair. The model proposes formulas over a code-built catalogue of named quantities with units (object masses, sizes and inertias, rotate turns/rpm/ramp/omega/alpha, joint ratings and strokes, simulated peak speeds and accelerations, static loads, `g`, `pi`) plus constants it declares with a unit and a justification. Code parses each formula with `ast` (whitelist only; no eval, exec or compile of the text), evaluates it with pint units, and requires matching dimensions, finite values, known names and at most 6 constants per check. A plan that fails goes back to the model once; what still fails is dropped and listed in `version.check_plan_rejected`. Accepted plans become `Check`s with `source: "planned"` and a `Calc`, count toward `sim.ok`, and are stored in `version.check_plan` so repair rounds (and a revise whose agent call fails) re-evaluate them in code without a model call. A revise passes the previous plan to the agent as a hint. A failed agent call adds nothing and writes no progress line; success writes `Task-specific checks: <n> planned`. The client and the robot-studio PDF label these checks "AI-planned, code-computed".
- Robot studio sizing: for each rotate action that turns a held or attached object on a joint, the inertial torque `I_obj_z * alpha_rotate` (from the stored spec, in code, not from a plan) is added to that joint's peak demand with a `Calc`. On the spin-mill fixture the 5,000 kg, 30 m cylinder at 4 rpm in a 10 s ramp needs about 23.6 kN*m on the yaw joint (the fixture's yaw is rated 5 N*m, so the planned check fails there).
- Requirements: the `tcp_speed`/`tcp_accel` time budget now also excludes the time of rotate actions and of timed moves (only when such actions exist; at least 5 % of the cycle is kept).
- Role plumbing: `checks` added to the Python registry, Java `LlmSettings` (default `claude_opus`), their tests and the four golden files that list roles. `pint==0.24.4` is pinned in `requirements.txt` (pyproject.toml has no dependency list).
- Tests: scripted-LLM fixtures with real formula, simulator and sizing code (`tests/test_checks.py`): the whitelist rejects attribute access, indexing, lambdas, unknown and dunder names, dimension mismatches are rejected, kN/N conversion, `pi` and `sqrt`; box, cylinder and sphere inertia; one bad plan repaired and one still bad dropped into `check_plan_rejected`; a planned yaw spin-up check on the spin-mill fixture changes `sim.ok`; the studio yaw peak includes the same torque. `tests/conftest.py` now blocks real providers in every test (a role without a scripted adapter fails like an unreachable model). `npm run contracts:check`, `typecheck` and `build` pass; Java `./mvnw -q test` passes (59 tests, 0 failures); Python 388 passed, 4 skipped (the real-model and web tests).
- Limitations: these are fixture tests of the verification code, not model evaluations: no real model proposed a plan, so the quality of plans from the real `checks` role, and the prompt's resistance to injected text, are not evaluated. The spin-mill project was not re-run. The checks are kinematic and static-load calculations in code (plus the inertial torque), not dynamics simulation and not hardware tests. The client change was type-checked and built but not viewed in a browser. A full Python test run made before the conftest guard may have started the Claude CLI for the `checks` role in tests that lacked a scripted adapter.

## 2026-11-21 — google-gemini branch: Gemini for every role, Gemini CLI adapter, Cloud Run

- Applications: Studio server (Python `llm/`), Java API (`LlmSettings`, `BearerTokenFilter`, `TaskRunner`), Studio client (Settings, `api.ts`), deployment files. Branch `google-gemini`, built for the Startup Buildathon India 2026 (Google AI and Google Cloud).
- Defaults: every role now starts on a Gemini profile through the Gemini API: `gemini_pro` (`gemini-3.1-pro-preview`; spec, revise, robot, analysis, checks), `gemini_flash` (`gemini-3.8-flash`; scene, search, extract, ask), `gemini_flash_lite` (`gemini-3.5-flash-lite`; objects). `gemini_cli_pro` and `gemini_cli_flash` (kind `gemini_cli`) are also saved for new installs. The Claude profiles are no longer defaults; `claude_cli.py` and its kind are unchanged. A `profiles.json` saved earlier keeps its role choices; only roles it lacks get the new defaults. New-profile defaults are kind `gemini`, model `gemini-3.8-flash`. Model ids come from the price table in `gemini.py`.
- Gemini CLI adapter (`server/studio/llm/gemini_cli.py`, kind `gemini_cli`; fields model, effort (ignored: the CLI has no flag), cli_path, timeout_s; no key): runs `gemini -m <model> -o json --approval-mode default --allowed-mcp-server-names __none__ -e none` with the prompt on stdin, in a fresh empty temporary folder, and parses `{response, stats, error}`. Tools: a settings file there (also given as the system settings file, which outranks the user's own) lists no real tool in `tools.core`, excludes every built-in tool name, and turns off MCP, skills, hooks, agents and context files; `with_web()` leaves only `google_web_search` and `web_fetch`. The system prompt is a temp file named by `GEMINI_SYSTEM_MD`. The child gets a short allow-list of environment variables, so `GEMINI_API_KEY` and `GOOGLE_API_KEY` are not passed and the CLI uses its own Google sign-in. On timeout the whole process group is killed. Usage is read from `stats.models.*.tokens` and priced with the table in `gemini.py` (no cost for an unknown model). Images and `structured()` are not offered; schemas go in the prompt. Source for the flags and the JSON shape: the Gemini CLI documentation and source on GitHub (checked 21 November 2026).
- Java and API: `gemini_cli` is a valid profile kind (no key in the envelope); the Python engine no longer inherits `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `STUDIO_TOKEN` or `STUDIO_CALLBACK_SECRET` from the Java process (keys stay on stdin only). `BearerTokenFilter` also accepts the token in a `studio_token` cookie (never together with a wrong Authorization header), and the client reads `#token=` once, keeps it in local storage and sets that cookie, so image, mesh and download links work behind a token. Without a token nothing changes.
- Client: Settings offers "Google Gemini (API key)" and "Gemini CLI (Google sign-in)" (CLI path optional); Claude is no longer offered. Model suggestions are Gemini ids.
- Cloud Run: `Dockerfile` (client build, Maven jar with tests skipped, JRE plus Python venv; listens on `$PORT`), `.dockerignore`, `deploy/cloudrun/README.md` (APIs, Artifact Registry, Cloud Build, Secret Manager for the Gemini key and the studio token, 3600 s timeout, CPU always allocated, min and max one instance, ephemeral `runtime/` or an untested Cloud Storage volume).
- Goldens: 01, 03, 10 and 13 edited by hand (new default profiles and roles, `gemini_cli` in kinds and models, `analysis` set to `gemini_flash` in 10); README note added. Not re-recorded.
- Verification (fixture tests only): `tests/test_gemini_cli.py` uses a fake `gemini` executable and checks argv, environment (no key), working folder, settings (no tool for plain calls, web tools only with `with_web()`), usage parsing, error mapping and that a timeout kills the child's child process. No real Gemini API call, no real Gemini CLI run, no model inference and no web search were made. Results (21 November 2026): pytest 403 passed, 4 skipped (the skipped are the real-Claude and real-network tests); Java `./mvnw -q test` 61 tests, 0 failures (surefire reports); `npm run contracts:check`, `npm run typecheck` and `npm run build` passed.
- Limitations: the CLI flags, the stdin-only prompt (no `-p`), the `-e none` and `--allowed-mcp-server-names` handling and the `stats` field names follow the documentation but were not run against a real installed `gemini`; a CLI version that rejects a flag will fail the call with its message. The Docker image was not built, nothing was deployed to Cloud Run and the Cloud Storage volume was not tried. Gemini model ids and prices are those in `gemini.py` (10 November 2026), not re-checked.

## 2026-11-21 — Antigravity CLI adapter (agy)

- Applications: Studio server (Python `llm/antigravity_cli.py`, registry, `meta` task), Java API (`LlmSettings`), Studio client (Settings), docs. Branch `google-gemini`. Wish: Antigravity instead of Claude Code.
- Adapter: profile kind `antigravity_cli` (fields model, effort, cli_path, timeout_s; no key; models `gemini-3.8-flash-high|medium|low`, default medium). Invocation: `agy --agent studio --model <id> --input-format stream-json --output-format stream-json --settings '<json>' --disable-slash-commands [--effort e] [--json-schema s]`, one NDJSON `user` message on stdin, the final `result` event parsed; `cli_path` defaults to `agy` on PATH, then `~/.gemini/bin/agy`. Input over 8 MiB is refused. The child gets an allow-list environment (no `*_API_KEY`, `STUDIO_TOKEN`, `STUDIO_CALLBACK_SECRET`) and runs in a fresh empty temporary folder; on timeout or cancellation the whole process group is killed. No images (`invoke_multimodal` absent). `structured(schema)` uses `--json-schema`. Usage: input = `input_tokens` minus `cache_read_tokens`, cache_read, output (thinking included, also kept as `thinking`), `cost_usd` None (Antigravity quota, not a price).
- Lock-down: the system prompt is the body of `.agents/agents/studio/agent.md` in the temp folder; its `tools:` list is the only tool set offered to the model (`view_file` as placeholder, plus `finish` when a schema is given; `search_web` and `read_url_content` for `with_web()`). `--settings` denies `command`, `read_file`, `write_file`, `mcp`, `execute_url`, `download_url` and `read_url` (web mode allows `read_url(*)` only), sets `toolPermission: strict` and the terminal sandbox. `--dangerously-skip-permissions` and `--mode` are never used.
- Registered everywhere: Python registry and `meta`, Java kinds and model validation, saved profiles `antigravity_flash_high|medium|low` (role defaults stay on the Gemini API profiles), client Settings option "Google Antigravity CLI (signed-in, no key)", goldens 03 and 13 edited by hand, README and PROTOCOL.md.
- Real calls (agy 1.5.13, `gemini-3.8-flash-low`, 21 November 2026, about 8 short calls on the user's Antigravity quota): (1) with the deny rules, a request to run `ls` and read a file: the file read was refused by the deny rule, no command ran; (2) with the default tool set, `search_web` ran despite the `read_url(*)` deny rule, so permissions alone do not turn it off; (3) with the agent `tools:` list holding only `view_file`, the model did not offer or call `search_web`; (4) web agent (`search_web`, `read_url_content`, allow `read_url(*)`): two searches ran and the page heading of a fetched page was reported (fetch call itself not seen as a separate tool step); (5) `--json-schema` in stream mode without `finish` in the tools looped for several turns and gave no `structured_output`; with `finish` listed it returned `structured_output` in one turn; (6) a wrong model id gives a `status: ERROR` result with the model list. These were probes of the CLI, not model evaluations.
- Verification (fixture tests only): `tests/test_antigravity_cli.py` uses a fake `agy` executable and checks argv, environment (no keys), empty working folder, agent file and tool lists, deny rules, web settings, schema passing, usage parsing, error mapping, the size cap, the CLI path fallback and that a timeout kills the child's child process. The Java and Python suites pass with the new profiles. No role was run end to end on an Antigravity profile.
- Limitations: the exact deny of every tool relies on the agent `tools:` list (documented for subagents; behaviour for the primary agent was observed, not documented) plus the permission rules; a future `agy` may differ. `--effort` with a model that already encodes its level was not probed. The `Url` parameter name used to list opened pages is a guess, so `pages_opened` may stay empty. Antigravity quota use and latency (about 3 to 30 s per call) were not measured over a full design run.

## 2026-11-21 — Vertex AI Gemini adapter

- Applications: Studio server (`server/studio/llm/vertex.py`, `registry.py`, `tasks/handlers.py`), Java API (`LlmSettings`), Studio client (Settings), `deploy/cloudrun/README.md`, `docs/PROTOCOL.md`, `contracts/openapi.yaml`.
- Added provider kind `vertex_gemini`: Gemini on Vertex AI `generateContent` (global endpoint `aiplatform.googleapis.com`, or `{location}-aiplatform.googleapis.com`), billed to a Google Cloud project. No API key: the access token comes from the metadata server on Cloud Run/GCE, or from `gcloud auth application-default print-access-token` (fallback `gcloud auth print-access-token`) locally; it is cached until shortly before expiry and never logged, stored, passed in argv or put in usage or error text. The project is the profile's, else `GOOGLE_CLOUD_PROJECT`, else the metadata server, else `gcloud config get-value project`. Same behaviours as the Gemini API adapter: images as `inlineData`, `structured(schema)` (`responseMimeType` + `responseJsonSchema`, prompt fallback on 400), `with_web()` (`googleSearch` grounding, queries and sources in usage), effort as `thinkingLevel`, usage from `usageMetadata` with the cost estimate from the existing price table. Errors for 401, 403 (enable the API, grant `roles/aiplatform.user`), 404, 429 and no project or no gcloud sign-in are explained in the message.
- Profiles `vertex_pro`, `vertex_flash`, `vertex_flash_lite` are added to the defaults (existing `profiles.json` files keep their roles). A new install with `K_SERVICE` set (Cloud Run) or `STUDIO_LLM_DEFAULT=vertex` defaults all roles to them; otherwise the Gemini API defaults are unchanged. Profiles gain optional `location` and `project` (Python, Java, OpenAPI, client). Settings has "Gemini on Vertex AI (Google Cloud project, no key)".
- Deploy guide: Vertex is the recommended Cloud Run route (enable `aiplatform.googleapis.com`, grant the service account `roles/aiplatform.user`); the `gemini-api-key` secret is optional.
- Checked (fixture tests only, no real Vertex call, no real Gemini inference, no Cloud Run run): `tests/test_vertex.py` (fake HTTP layer and fake gcloud: URLs, headers, bodies, usage, error mapping, token cache and refresh, metadata path, no token in logs); full Python suite, Java suite (goldens `03_llm_settings` and `13_llm_profile_delete` updated for the three new profiles, the new kind and models), `contracts:check`, client typecheck and build. Limitation: request and response shapes follow Google's REST reference but have not been confirmed against the live service.

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

@@MERGE_ENTRY@@

## 2026-11-21 — Merge of origin/AR_review into google-gemini (finalised)
- Affected: all applications. The Photo AR and Site scan work (ar_calibrate, ar_estimate, scan tasks, phone pairing, AR and
  site views in Design) is merged with the Gemini providers, objects/ask/checks roles, studio, fleet, savings and vendors.
- Resolution notes: the pom keeps zxing and not springdoc (the API spec is the bundled contract); the job kind enum in
  `contracts/openapi.yaml` gained ar_estimate, scan_reconstruct and apply_scene; two Java tests were adapted to the
  18-argument `StudioProperties`; the spin-mill worked example was removed from the spec and checks prompts (generic wording).
  Generated files (schemas, types, OpenAPI bundle, path types, lockfile) were regenerated, not hand-merged.
- Checks (fixture tests, no real model, no GPU): pytest 517 passed, 8 skipped, 12 failed (tests/test_scan.py needs the
  optional av, OpenCV and SciPy, which are not installed in the shared venv); Java 88 run, 2 failures (the same missing
  `av` for a scan upload and a phone video); contracts:check, typecheck, build and the core tests (15) pass.

## 2026-11-21 — Merged the AR review branch into the Google edition (final branch)

- `origin/AR_review` was merged into `google-gemini` (cb72f69), keeping both sides. Fixture checks: pytest 529 passed, 8 skipped; Java 88, 0 failures; contracts:check, typecheck and build pass. A browser check on the branch server (8801/5191) showed all stages, the Ask bar and the AR "My site" view, with no console errors.
- The spin-mill worked example was removed from the spec and checks agent prompts, so it cannot leak into other projects' specs or PDFs. The mill remains only as a test fixture.
- Vertex AI: the ADC sign-in works, but the real call was refused (403) until billing is enabled on the project. No real Vertex inference yet.
