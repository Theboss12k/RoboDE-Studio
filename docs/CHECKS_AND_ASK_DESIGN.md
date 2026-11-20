# Task-specific checks agent, generic motions, and the Ask bar (design, approved 2026-11-21)

User decisions:
- **Checks agent:** runs on every design and revise.
- **Its checks count toward pass/fail**, labelled "AI-planned, code-computed".
- **The Ask bar can act directly,** within the guardrails below.
- **Spin-mill motions are included:** rotate, attached loads and "at most" limits.

Acceptance test: the spin-mill project (`p_cf07285d3b`).

## A. Generic motions in the spec and simulator (do first)
- **`TaskAction.verb`** gains `rotate`, with new optional fields (x-optional):
  - `turns: float` (signed, may be fractional);
  - `rpm: float > 0`;
  - `ramp_s: float >= 0`;
  - `axis: "tool_yaw" | "<joint name>" = "tool_yaw"`.

  The simulator plays it on that joint with a trapezoid profile: peak ω = rpm·2π/60 and ramp time `ramp_s`, so
  α = ω / ramp_s. The number of turns is the joint travel; continuous joints ignore the limits. Frames carry it as
  usual.
- **Attached loads:** `TaskObject.attached: bool = False` (x-optional). When true, the object is held from t = 0 and
  its mass counts in every load (the gravity torques in `sim/task.py` use `run.holding`, so `holding` starts as the
  object). It fixes the spin-mill bug: the lift axis's load left out the 5,000 kg cabin.
- **"At most" limits:**
  - `PhysicsInputs` gains `max_accel_m_s2: float | None` and `max_jerk_m_s3: float | None` (comfort or fragile limits).
  - `derive()` adds `max_accel` (comparator `<=`). The simulator reports the peak simulated TCP acceleration (and
    for rotate, the rim acceleration) against it.
  - `peak_acceleration_m_s2` keeps its meaning (the needed capability, `>=`).
- **Duration limits per move:** `TaskAction.duration_s` for `move` and `rotate` may set an upper time for that
  move (dwell is still wait/inspect/press only). The motion fits inside it; a check reports when it can't.
- **The spec agent prompt** documents rotate, attached, the max limits and the move durations (the spin mill: lift
  in 30 s, rotate 5 turns at about 4 rpm with a 10 s ramp).

## B. Checks agent (`agents/checks.py`, role `checks`, default `claude_opus`)
- **Input:** the spec, the robot concept (family and params with units), the requirements, the existing checks,
  and a **variable catalogue** of named quantities with units that code builds (`physics/quantities.py`):
  - spec values: object masses, sizes and inertias (computed for box, cylinder and sphere), cycle time, the
    rotate actions' turns, rpm and ramp;
  - robot params and capabilities: joint ratings, strokes, masses, payload;
  - simulation maxima: peak joint speed and acceleration per joint, peak TCP speed and acceleration, duration,
    the worst-case static load per joint;
  - scene and fleet values where present;
  - constants (g, π).

  Every entry is `{name, value, unit, source}`.
- **Output:** `CheckPlan[]`, at most 12, each `{id, name, why, expression, unit, comparator: ">=" | "<=", limit
  (expression), unit_limit, constants: [{name, value, unit, justification}]}`.
  - Expressions use only catalogue names, the declared constants, `+ - * / ** ( )`, numbers and the functions
    `sqrt abs min max sin cos tan pi`.
  - Example: `peak torque to spin the cabin = 0.5 * m_cabin * (d_cabin/2)**2 * rpm_rotate * 2*pi/60 / ramp_rotate`
    `<= rating_tool_yaw`.
- **Code verifies (`physics/formulas.py`):**
  - It parses with Python `ast`, allowing only the whitelist above, and never uses `eval`.
  - It resolves names and evaluates with **pint units** (new pinned dependency).
  - Every check needs dimensional consistency between the expression and `unit`, and between `limit` and
    `unit_limit`.
  - It also needs finite values, all names known, and duplicate ids removed. Constants must have units and a
    justification, and are capped at 6 per check.
  - Rejected checks go back to the model once (repair). If still bad, they are dropped and listed in
    `version.check_plan_rejected` with their reason.
- **Result:** each accepted plan becomes a `Check` (source `planned`, x-optional) with a `Calc` (the formula
  text, the inputs with values and units, the result) and counts toward `sim.ok`. The UI and the PDF show the
  badge "AI-planned, code-computed".
- **When it runs:** in `design()` and `revise()` after the simulation, before repair. A failure of the agent itself
  adds no checks and no progress line, and the version is unchanged (goldens). On success the progress line is
  "Task-specific checks: N planned".
- **Robot studio** sizing reads the planned checks' inputs (rotate inertia and α) to size the yaw joint's torque.

## C. Ask bar (floating, every screen; role `ask`, default `claude_sonnet`)
- **Client:** a floating button and Cmd/Ctrl-K that opens a panel with an input, the conversation (this session
  only) and action chips.
  - It sends `{question, history (last 6 turns), context: {project_id?, stage, version?, t?, candidate?, build?,
    selection?}}`. The ids come from the current screen; numbers are never trusted from the client.
- **Server:** `POST /api/v1/ask` runs a synchronous job (with `wait` and `expand`, like the others) and the Python
  task `ask`.
  - It builds a grounded context from stored data for the referenced project, version, moment, build and
    candidate: summaries, checks with working, requirements, the timeline of versions, and the build's
    motors/BOM.
  - It answers with `run_tools` over a **fixed action tool list** that calls the same Java routes (through a
    callback the Java side executes, never Python HTTP):
    - `revise(request, base_version, t)`
    - `design(answers, comments)`
    - `search()`
    - `analyse(candidate)`
    - `studio_build(request?, pinned?)`
    - `update_brief(text)`
    - `set_baseline(...)`
    - `cancel_job(id)`
    - `navigate(stage, …)`, which is client-only
  - **Never available:** delete project, keys or settings, profile changes.
  - `approve`, `approve_build` and `decision` are returned as **confirm chips**: the user clicks to perform them.
- **Response:** `{answer (markdown), citations: [{kind, ref, label}], actions_taken: [{kind, job_id?, summary}],
  confirm: [{kind, label, request}]}`. Jobs it started appear in the Running list and can be cancelled.
- **Safety:**
  - At most 2 actions per question; each action is logged in the project's activity (`meta.activity`).
  - The model is told that page and stored text are data.
  - Costs are visible through the usage ledger (role `ask`).

## Build order (after the current workers W1–W3 finish, since they share the spec, schema and studio files)
1. **W4:** part A (schemas, spec prompt, sim rotate, attached, max limits, move durations), with tests and the
   spin-mill fixture.
2. **W5:** part B (quantities, formulas with pint, the agent, integration, UI badge and PDF), after W4.
3. **W6:** part C (Java `/ask`, the Python `ask` task with tools, the client floating panel). It can run in
   parallel with W5, because it only reads checks.
4. **Verification:** re-run the spin mill for real (Opus). Expect:
   - the rotate motion simulated;
   - the cabin mass in the lift load;
   - a planned yaw-torque check from the rotational inertia;
   - a `max_accel` comfort check.

   Then ask the Ask bar questions on 3 screens and let it make one change request.
