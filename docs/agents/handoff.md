# HANDOFF — RoboDE Studio AR feature (photo AR, phone link, site scan / video AR)

Audience: the next coding agent. Written 2026-11-21 when the 5-hour usage limit reached 94%. Read AGENTS.md first: it requires a docs/PROJECT_TIMELINE.md entry for every change, numbers only from code, and no claims about tests that were not run.

## Repo state
- Local: `C:\Users\<user>\Downloads\RoboDE`, created from a ZIP of GitHub `Theboss12k/RoboDE-Studio` main plus `git init` (no history). Branch `feature/ar-photo` is UNBORN: nothing is committed, and the ZIP baseline is staged. To align with GitHub, follow `docs/agents/merge-ar-photo.md`, section A.
- git is GitHub Desktop's: `$env:LOCALAPPDATA\GitHubDesktop\app-3.6.7\resources\app\git\cmd\git.exe`. Commit email (repo-local): <collaborator email, redacted>. Never commit or push without an explicit "yes" from the user.
- Toolchain: Python 3.12 `.venv` (torch 2.14.1+cu130, MoGe-2 ViT-L cached, open3d 0.20 with deps, av 19.0.2, opencv 5.1); Node 26; Temurin JDK 25; a CUDA GPU.
- Windows Application Control blocks some native DLLs (pycolmap was blocked). Don't add unknown compiled packages, and never change security settings.

## The feature (all in the Design stage; view switch next to "Simulated task": Modelled scene / My photo / My site)
1. **Photo AR ("My photo")**, contract `docs/AR_PHOTO.md`:
   - Upload a photo; calibrate from 4 corners of an A4/Letter/custom sheet (numpy homography; focal from the homography).
   - Optional "Estimate automatically" (MoGe-2 on GPU, approximate scale).
   - Place and turn the robot; playback over the photo; snapshot PNG.
   - Code: `server/studio/ar/`, Java AR routes and store, `packages/core/src/ar.ts`, `client/src/components/ar/`.
2. **Phone link:**
   - The desktop shows a QR code; the phone page takes a photo or records a site video.
   - A separate socket listener (`server-java/.../pairing/PairingListener.java`) on 0.0.0.0:8801 serves only `/p/{code}`, `/photo` and `/video`.
   - Links use a 256-bit code, expire after 10 min, and allow 20 uploads.
   - Limits: photos 15 MB, video 500 MB; 60 s per request, 15 min for video after a valid code; 32 open connections and 8 requests at once.
3. **Site scan / video AR ("My site")**, contract `docs/SITE_SCAN.md`:
   - **Pipeline:** video, then keyframes (PyAV), then our own SfM (OpenCV RootSIFT, PnP, SciPy BA, one shared camera = one focal length), then undistorted frames, then MoGe depth aligned to the sparse points, then the Open3D VoxelBlockGrid TSDF, then a 600k-triangle lit GLB.
   - **Scale and floor:** floor by RANSAC; scale from a sheet in a frame or from a measured distance.
   - **Tools:** place robot, add object, task point, measure, set scale, pick floor.
   - **Apply to design:** the `apply_scene` task makes a new version deterministically (no LLM) via `server/studio/pipeline/apply_scene.py`.
   - **Code:** `server/studio/scan/`, `client/src/components/scan/`, `packages/core/src/scan.ts`, and the Java scans routes with streaming upload (`common/StagedUpload.java`).
   - Scan geometry is NOT part of the pass/fail checks; only the objects users add are.
4. **LLM:** every role points at the profile "Claude Sonnet 5.5 (Claude Code CLI)" in the dev runtime. `server/studio/llm/claude_cli.py` was fixed for Windows (CLI discovery, reduced env, UTF-8). The CLI (desktop-bundled 2.1.326) is NOT signed in; the user must run `/login` (terminal tab "Claude CLI login"). No real Sonnet call has been made through Studio.

## Verified (all fixture LLM; synthetic data, no real phone footage)
- pytest: 234 passed, 6 skipped. Java `mvnw test`: 55 run, 1 known Windows-only error (`KeysAndUsageTest`, POSIX permissions), 7 skipped. e2e `npm run e2e` with `E2E_CHANNEL=chrome`: 17 of 17 (before the camera-view work). Typecheck clean. Core tests 12 of 12.
- Synthetic room scan: 90/90 frames, focal 0.01%, walls 0.2%, table 1.6%, about 2.5 min per reconstruction.

## DONE after this was written (camera view, e2e 19/19, timeline entry added); the section below is kept for reference
A Sonnet worker was building (client only):
- the "Through the camera" view: the keyframe as background, the scan mesh depth-only for occlusion, shadows, a walk scrubber with Play walk, and "Best view of the robot";
- 3D-view navigation: zoomToCursor, small minDistance, "Focus robot", double-click to set the orbit target, Reset view;
- a lit mesh, plus new e2e checks and screenshots `docs/screenshots/site/5-focus-robot.png` and `6-through-the-camera.png`.
If you pick this up:
- Check `git status` and those files.
- Run `npm run typecheck` and `npx playwright test -c e2e/playwright.config.ts e2e/tests/scan.spec.ts`, then the full `npm run e2e`.
- Add the timeline entry (not yet written for this step), then send the screenshots to the user and notify them. The user asked to be pinged when the screenshots are ready.

## Remaining / next
1. Finish and verify the in-flight camera-view work above, and log it in the timeline.
2. **Real data:** ask the user for a 30–60 s real phone video of a site; record accuracy as a real run in the timeline. Real accuracy has never been measured.
3. Claude CLI sign-in by the user, then the profile connection test, then a real design run in the browser.
4. Playwright's bundled Chromium download hangs on this machine; e2e uses the installed Chrome.
5. **Delivery 2 (planned):** scan mesh as collision geometry in the checks; Gaussian-splat photoreal layer.
6. **Polish:** a real-phone Wi-Fi test of the 500 MB phone upload; a CPU out-of-memory seen once in reconstruction (the TSDF initial capacity was cut to 20000; watch for it again).
7. **Before merging:** follow `docs/agents/merge-ar-photo.md` (align the ZIP baseline with origin, conflict rules, gates).

## How to run
- **Dev:** preview config `studio-dev` in `.claude/launch.json` (Java 8800 + Vite 5190).
- **Demo with test data:** `studio-e2e-data` (jar on 8810, runtime `e2e/.runtime`, project `p_1f3008ab62`, reconstructed scan).
- **Env:** `$env:PYTHONUTF8='1'`; `$env:STUDIO_PYTHON="$PWD\.venv\Scripts\python.exe"` for mvnw; JDK under `C:\Program Files\Eclipse Adoptium`.
- **Jar rebuild while the dev server runs:** build in a copy of server-java outside the repo, then copy the full jar over `server-java/target/studio-server-0.1.0.jar`. Never leave a thin jar.
