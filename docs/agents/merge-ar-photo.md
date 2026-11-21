# AGENT TASK: merge `feature/ar-photo` into `main` (RoboDE Studio)

Audience: an autonomous coding agent. Follow the steps in order. If a STOP condition fires, stop and report; do not work around it.

## Fixed facts
- REPO: https://github.com/Theboss12k/RoboDE-Studio (private). LOCAL: C:\Users\<user>\Downloads\RoboDE
- The LOCAL repo was created from a ZIP of `main` plus `git init` (no history). Branch `feature/ar-photo` may be UNBORN (no commits), with the ZIP contents staged as the baseline. Section A aligns it with GitHub before anything else.
- SHELL: Windows PowerShell 5.1. There is no `&&`; chain commands with `; if ($?) { ... }`.
- GIT: resolve it first:
  `$git = (Get-Command git -ErrorAction SilentlyContinue).Source; if (-not $git) { $git = "$env:LOCALAPPDATA\GitHubDesktop\app-3.6.7\resources\app\git\cmd\git.exe" }`
  Use `& $git ...` for every git command.
- `gh` is not installed. For a PR, give the user `https://github.com/Theboss12k/RoboDE-Studio/compare/main...feature/ar-photo?expand=1`.
- Commit identity (repo-local): user.email = <collaborator email, redacted>
- Contract for the feature: docs/AR_PHOTO.md. Repo rules: AGENTS.md (it requires a docs/PROJECT_TIMELINE.md entry).
- Regenerate contracts: `.venv\Scripts\python scripts/export_schemas.py`, then `npm run contracts:types`. Check them with `npm run contracts:check`.
- All checks: `node scripts/studio.mjs test` (mvnw verify + pytest + contracts:check). Also run `npm run typecheck` and `npm --workspace packages/core test`.
- On Windows: set `$env:PYTHONUTF8="1"` (otherwise `test_catalog.py::test_extract_verifies_quotes` fails on encoding) and `$env:STUDIO_PYTHON="$PWD\.venv\Scripts\python.exe"` before `mvnw` (the Java default is `.venv/bin/python`). `KeysAndUsageTest.keyFileIsOwnerOnlyAndKeysAreNeverReturned` errors on Windows (POSIX permissions); this is pre-existing, so treat it as a known exception, not a merge failure.
- Baseline on Windows at the AR delivery (2026-11-20): pytest 181 passed, 4 skipped without torch (180 passed, 5 skipped with torch installed); Java 38 run, 1 error (the one above), 7 skipped; typecheck passes; core tests 4 of 4 pass.

## Hard rules
- Never push to `main`, never force-push, and never run `git reset --hard` on a pushed branch.
- Never commit, push or merge without an explicit "yes" from the user in chat during this session.
- Never commit: runtime/**, uploaded photos, model weights (*.pt, *.pth, *.safetensors, *.onnx), .env files, runtime/llm/keys.json, RoboDE-Studio-main.zip.
- Never hand-edit generated files (contracts/schemas/**, packages/core/src/generated/**). Regenerate them.
- Never weaken, skip or delete a test to get a green run.
- Never ask for or type a GitHub password or token. Auth is the user's (GitHub Desktop or Git Credential Manager).

## A. Align the ZIP baseline with GitHub (only if `& $git rev-parse --verify HEAD` fails = unborn branch)
1. `& $git fetch origin`. On an auth error, STOP and ask the user to sign in.
2. `& $git reset --soft origin/main`. HEAD now points at main, while the index and working tree stay as they are.
3. `& $git diff --cached --stat origin/main`. Every listed path must be AR work (see "AR paths" below) or the timeline. If anything else differs, the ZIP was not the current main: STOP and report the paths.
4. Ask the user for a "yes", then commit the AR work on `feature/ar-photo` with the message "Photo AR: View in my space".

AR paths: server/studio/ar/**, server/studio/llm/claude_cli.py (Windows `pwd` fallback), server/studio/schemas.py, server/studio/tasks/handlers.py, scripts/export_schemas.py, requirements*.txt, tests/test_ar_*.py, contracts/**, server-java/** (AR store, service, controller, ImageSize and their tests), packages/core/** (ar.ts, api.ts, index.ts, generated, test/, package.json), client/src/** (components/ar/**, SceneView.tsx, Player.tsx, stages/Design.tsx, jobs.tsx, CSS), docs/AR_PHOTO.md, docs/agents/**, docs/PROJECT_TIMELINE.md, server-java/pom.xml (zxing), server-java/**/pairing/**, client/src/components/ar/PhoneLink.tsx, client/src/components/icons.ts, e2e/** (except e2e/.runtime, e2e/.photos, e2e/report), package.json and package-lock.json (@playwright/test devDependency), .gitignore.

Site scan (video AR) paths, also AR work: docs/SITE_SCAN.md, requirements-scan.txt, server/studio/scan/**, server/studio/pipeline/apply_scene.py, tests/test_scan.py, tests/scan_synth.py, tests/test_apply_scene.py, tests/test_claude_cli.py, server-java/**/common/StagedUpload.java, server-java/**/flow/ScanFlowTest.java, server-java/src/test/resources/scan/**, packages/core/src/scan.ts, packages/core/test/scan.test.ts, client/src/components/scan/**, e2e/tests/scan.spec.ts, e2e/fixtures/make_scan.py. Never commit tests/.cache/ or e2e/.photos/ (generated videos).
Extra conflict rule: server-java/**/pairing/PairingListener.java is now a socket server (not JDK HttpServer); on conflict keep this version and re-apply the other side's intent by hand, then rerun PairingFlowTest.

Gate addition: `npm run e2e` (Playwright; on Windows set `$env:E2E_CHANNEL="chrome"` if the Playwright Chromium is not installed). Baseline: 11 of 11 pass (pytest 190 passed / 6 skipped with torch; Java 40 run, 1 known Windows error, 7 skipped). It needs .venv with torch and MoGe for test 6 (see requirements-ar.txt), and it needs ports 8810 and 8811 free.

## B. Preconditions for the merge (any failure means STOP and report which)
1. `& $git status --porcelain` is empty.
2. `& $git config user.email` equals <collaborator email, redacted> (repo-local).
3. `& $git fetch origin` succeeds.
4. The branch has at least one commit beyond origin/main: `& $git rev-list --count origin/main..HEAD` > 0.

## C. Procedure
1. Record the merge-base: `& $git merge-base HEAD origin/main`
2. `& $git merge --no-ff origin/main`. If it says "Already up to date", skip to step 5.
3. Resolve each conflict by the CONFLICT RULES, then `& $git add <file>`. If a file matches no rule, STOP and report its path and both sides.
4. Regenerate contracts and TS types, and regenerate any conflicted lockfile with npm.
5. Run the GATES. Fix a failure only when the merge caused it; otherwise STOP and report the output.
6. Commit the merge, appending the line "Regenerated contracts and TS types" to the message.
7. Ask the user: "feature/ar-photo is up to date with main and green. Push and open PR?" Wait for "yes".
8. After the yes: `& $git push -u origin feature/ar-photo`, then give the compare URL. The user merges on GitHub with "Create a merge commit" (no squash).

## Conflict rules
| Path pattern | Rule |
|---|---|
| contracts/schemas/**, packages/core/src/generated/** | Take either side, then regenerate. The result must equal the generator output. |
| scripts/export_schemas.py (MODELS list) | Union of names; keep the five Ar* models. |
| server/studio/schemas.py | Keep both sides' models and fields. Ar* models stay separate; never add AR fields to DesignVersion. |
| server/studio/tasks/handlers.py (HANDLERS) | Union of entries; keep `ar_calibrate` and `ar_estimate`. |
| server-java/** controller, service, store | Union. AR routes stay under /api/v1/projects/{pid}/ar/scenes. Do not change other route paths. |
| client/src/components/SceneView.tsx, Player.tsx | Keep both. With no `ar` prop, behaviour must equal main's. Other tracks (fleet playback, background jobs) and the AR view must both work. |
| client/src/stages/Design.tsx | Keep both sides' UI; the "View in my space" toggle sits next to the player. |
| client/src/jobs.tsx | Union of job kinds; keep `ar_estimate`. |
| packages/core/src (non-generated) | Union of exports. |
| requirements.txt | Union. torch and MoGe stay ONLY in requirements-ar.txt. |
| package-lock.json | Regenerate with `npm install`; never hand-merge. |
| docs/PROJECT_TIMELINE.md | Keep both sides' entries in date order; never rewrite past entries. |
| tests/** | Union. |
| Anything else | STOP and report. |

## Gates (all must pass)
- `node scripts/studio.mjs test`: Java with no failures; pytest with no failures and a passed count ≥ origin/main's count + the AR tests (the `ar_estimate` model test may skip without torch); contracts up to date.
- `npm run typecheck` passes.
- `npm --workspace packages/core test` passes (the GL projection matches the OpenCV pinhole within 0.5 px).
- `& $git diff --name-only <merge-base> HEAD` contains nothing from the "never commit" list.

## Rollback (only on user request)
- Before pushing: `& $git merge --abort` (mid-merge), or `& $git reset --merge ORIG_HEAD` (merged, not pushed).
- After the PR is merged: on a new branch from main, run `& $git revert -m 1 <merge-commit-sha>`, push it and open a PR. Never rewrite main.

## Report back
- Status: aligned | merged-to-branch | pushed | STOPPED (reason)
- Commit SHAs, conflicted files and the rule applied to each
- Gate results with counts
- The user's next action (sign in, approve the push, merge the PR)
