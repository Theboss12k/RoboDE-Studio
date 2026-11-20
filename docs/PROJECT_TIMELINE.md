# RoboDE Studio project timeline

Dated record of changes to this repository. Newest entries are added at the bottom.

## 2026-11-20 — Project started (in progress)

- Scaffold: new repository copied from RoboDE-Testbed (LLM adapters and registry with Studio roles, URDF/document/image intake, unit parsing, client helpers, launcher on ports 8800/5190). Nothing in the Testbed or the IDE changed.

## 2026-11-20 — Agents, design pipeline, project store and HTTP API (in progress)

- Added `agents/` (spec, scene, robot, revise, analysis: prompt, pydantic output schema, validator, one repair turn), `pipeline/` (design, revise, catalog find and analyse), `projects/` (file store, versions never overwritten except the `approved` flag) and `api/` (FastAPI jobs, LLM settings routes as in the Testbed, export ZIP).
- Behaviour: scene and robot agents run concurrently; sizes, requirements, checks and the candidate comparison come from code. A datasheet that cannot be downloaded fails the job with `error_code: datasheet_download` and the user uploads it instead.
- Scene validator lets a surface that holds a marker come within 0.1 m of the base (other boxes keep 0.3 m free).
- Checked: `tests/test_agents.py`, `test_pipeline.py`, `test_api.py` with scripted (fixture) LLMs and the real family and simulator code; no network, no real model inference, no hardware. `tests/test_real.py` (real Claude CLI, `STUDIO_CLAUDE=1`) was not run.

## 2026-11-20 — Robot families, IK, URDF generation and task simulator (in progress)

- Applications: Studio server (`server/studio/families/`, `server/studio/sim/`).
- Added the four v1 families (serial arm, gantry, mobile base, mobile manipulator) with forward kinematics, damped-least-squares IK (joint limits, fixed-seed multi-start, tool-down constraint), static gravity torques via `physics.statics`, physics-driven sizing, URDF generation (root `base_link`, tool chain ends in `tcp`) and GLB/STL export of the zero pose.
- Added the task simulator: waypoints from the spec, per-waypoint IK, synchronised joint-space motion with per-joint trapezoid limits, base driving along the locomotion path, frames with world-frame TCP, and checks (reach, joint limits, static joint loads, joint speed, cycle time, payload, grip, accuracy, collisions against scene boxes, locomotion).
- Verification: `tests/test_families.py` and `tests/test_sim.py` run fixture specs through the numeric simulator only. No model inference, no Isaac/GPU validation and no physical robot tests. Limitations: collisions are reported, not avoided (no motion planner); capsule collision is coarse; mobile manipulator ignores base tipping and the base body in arm collision checks.

## 2026-11-20 — Studio workspace and brief redesign

- Application: Studio client. Updated workspace navigation, stage states, and responsive layouts. Redesigned brief entry with example tasks, clearer input guidance, and a visible path to the reference step for saved briefs.

- Improved the simulation viewport's starting camera and kept marker labels in the scene overlay to avoid a browser error when navigating away. Saved model details remain available in a horizontally scrollable table on phones.
- Verification: the production client build passed, along with 91 local Python tests. Chrome completed the workflow from a new brief through decision with scripted model fixtures, including scene playback and saved project switching. Checked the desktop light and dark themes and phone layouts down to 320 pixels. The checks used local fixture data without live model inference, web search, or hardware.
