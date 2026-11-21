# RoboDE Studio: technical documentation

Studio turns a task description into a sized, simulated robot concept with requirements, then compares real robots
with it. This document explains:
- how the system is built and which parts are AI and which are code;
- every requirement, its formula as implemented, and how the robot is sized and checked;
- how real robots are found, downloaded, read and compared;
- where data is stored, what the API offers, and how it is tested;
- what it does not do.

For use, see [STUDIO.md](STUDIO.md). For setup, see [README.md](../README.md). Module contracts for contributors are in
[CONTRACTS.md](CONTRACTS.md); where this document and CONTRACTS.md differ, this document describes the code (see
section 15.1).

---

## 1. Architecture

```
 Browser (React + three.js, 127.0.0.1:5190) ── HTTP /api/v1 ──► Java server (Spring Boot, 127.0.0.1:8800)
                                                                  │  HTTP routes, jobs, project store, LLM settings,
                                                                  │  usage ledger: the only writer of runtime/
                                                                  │  one `python -m studio.tasks <task>` per job
                                                                  ▼
                                              Python tasks (server/studio/tasks/, cwd = repo root)
                                                                  │  request on stdin, JSON lines on stdout
       ┌───────────────┬───────────────────┬─────────────────────┼───────────────────┐
       ▼               ▼                   ▼                     ▼                   ▼
   agents/         physics/            families/ + sim/      catalog/            pipeline/design.py
   (AI roles)      (requirements)      (robots, simulator)   (search, download,  pipeline/catalog.py
       │                                                      extract, compare)
       ▼
   llm/ (one interface)
   claude_cli · gemini
```

| Process | Technology | Port | Started by |
|---|---|---|---|
| UI | React 19, Vite, three.js (react-three-fiber) | 5190 | `npm run dev` |
| API | Java 25 or later, Spring Boot 4.2, one jar built from `server-java/` with the Maven wrapper | 8800 | `npm run dev` or `npm run api` |
| Tasks | Python 3.12+ in `.venv`: pydantic 2, numpy, trimesh, pypdf, httpx | none | the Java server, one subprocess per job (at most four at once) |
| AI | Claude Code CLI (`claude -p`, one isolated process per call) or the Gemini REST API | none | a task, per call |

The Java server owns the HTTP routes under `/api/v1`, the in-memory job records, the project files under
`runtime/projects/`, the LLM profiles and keys under `runtime/llm/`, and the usage ledger. Python never writes
`runtime/` outside a job's output folder. The wire format between the two is in [PROTOCOL.md](PROTOCOL.md): each
task gets one JSON request on stdin, and answers with progress, usage and one result as JSON lines on stdout. API keys
go only on stdin, never in argv, the environment, logs or stdout. A task that is silent for 60 s is killed, and so are
its child processes (the Claude CLI is a grandchild); a task may run for 45 min at most.

`npm run dev` starts the jar and the Vite server, which proxies `/api/v1` to the Java server. `npm run api` serves the
built client from `client/dist` instead. `scripts/studio.mjs` loads `.env` and sets `STUDIO_PYTHON` for the tasks.
If one child process exits, it stops the others.

### 1.1 Who decides what

| Step | AI proposes | Code decides |
|---|---|---|
| Specification | Actions, objects, markers, physics inputs, questions | Schema, references between ids, dwell < cycle, physics inputs equal the main object, a path when driving |
| Requirements | None | All of them (section 3) |
| Scene | Boxes, objects, floor size | Markers unmoved, objects on their markers, support under raised markers, base clearance, mounting surface, floor size |
| Robot | Family, name, rationale, hint parameters | The family exists and is mobile when driving is needed; parameters parse. All sizes (section 5) |
| Simulation, checks | None | Everything (sections 6 to 8) |
| Revision | A patch of spec, scene and/or robot | Patch validators, base marker rule, re-derivation, re-sizing |
| Search | Candidates with sourced numbers and prices | Schema, URLs, quote and figure rules, units, screen |
| Datasheet values | Values with quotes | Quote found in the document and number in the quote |
| Analysis text | The `impact` sentence of each difference | Numbers and severity; the validator rejects any changed field |

A model's answer is a proposal until code has checked it. Each agent module has a system prompt, a pydantic output
schema, a validator and `async def run(adapter, ...)`. Agents call `llm.base.ask_json`, which parses the answer,
validates it, and on failure sends the error text back once for a repair. A second failure raises `LLMError`.

### 1.2 Agents and their outputs

| Role (module) | Input | Output |
|---|---|---|
| `spec` (`agents/spec.py`) | Brief, reference (text, URDF summary, images), answers so far, round | `{questions: [Question]}` or `{spec: TechnicalSpec}` |
| `scene` (`agents/scene.py`) | Spec (markers, objects, environment, locomotion), base pose | `SceneSpec` |
| `robot` (`agents/robot.py`) | Task summary, requirement targets, family descriptions | `RobotConcept` (family, name, rationale, hint params) |
| `revise` (`agents/revise.py`) | Current design (spec, scene, robot, failing checks), the request | `Patch` (targets, any of spec, scene, robot, summary) |
| `search` (`catalog/search.py`) | Task, requirement targets, today's date | `{candidates: [Candidate]}` |
| `extract` (`catalog/extract.py`) | Product name, document text | `{document_title, kinematics_note, values: [...]}` |
| `analysis` (`agents/analysis.py`) | The list of `Difference` | The same list with `impact` filled |

Spec limits: `MAX_QUESTIONS = 6`, `MAX_ROUNDS = 3`. In round 3 questions are refused and the spec must list its
assumptions. The prompt treats reference material as data, not instructions.

### 1.3 Frames and units

World frame: metres, right-handed, z up, origin on the floor. A family works in its own base frame whose origin is
the mounting point and whose x axis follows `BasePose.yaw`. Angles are radians in code and data, degrees in
parameters people read (`SerialArmParams`) and in text. Masses kg, forces N, torques N*m, energy Wh.

---

## 2. LLM layer

Connections are global adapters implementing `async def invoke(input: str, system_prompt: str) -> str`, plus
`invoke_multimodal(input, system_prompt, images)` when the provider reads images. Adapters know nothing about
robots, families, the physics engine or the simulator.

| Module | Job |
|---|---|
| `llm/base.py` | `ask_json`, `json_answer` (raw, fenced or first `{...}`), `portable_schema`, `Image`, `LLMError` |
| `llm/claude_cli.py` | Claude Code CLI adapter |
| `llm/gemini.py` | Gemini REST adapter (Interactions endpoint, pinned API revision) |
| `llm/registry.py` | Profiles, roles, `PROVIDERS` table, usage hook |
| `llm/keys.py` | API keys, kept apart from profiles |
| `llm/usage.py` | Token ledger |
| `llm/scripted.py` | Fixture adapter for tests: replays prepared answers |

**Claude CLI.** Each call is one `claude -p` process in an empty temporary folder with a reduced environment: the
caller's system prompt replaces Claude Code's own, tools are off (`--tools ''`), no MCP, slash commands, settings or
session history. A JSON schema, when given (`structured(schema)`), is enforced with `--json-schema`. The CLI is found
from the profile's path, `PATH`, or the desktop app's bundled copies.

**Web search.** `with_web()` returns a copy of the adapter allowed to search the web: for the CLI, only `WebSearch`
and `WebFetch` are enabled, still in the empty folder; for Gemini, Grounding with Google Search. Only the `search`
role uses it. The model fetches those pages itself. They do not pass through `safe_download` (section 11.3).

**Gemini.** Key from `runtime/llm/keys.json` (mode 0600) or `GEMINI_API_KEY` / `GOOGLE_API_KEY`. Retries on 429, 500
and 503 with back-off. Cost is estimated from a price table dated 10 November 2026; an unknown model gets no cost.
This repository has no test for the Gemini adapter and no recorded run against Google.

**Roles and profiles.** Roles: `spec, scene, robot, revise, search, extract, analysis`. Defaults: Claude Opus 5.5 for
spec, robot and revise; Claude Sonnet 5.5 for the others. A profile (`runtime/llm/profiles.json`) holds name, kind,
model, effort, timeout (10 to 900 s, default 240) and an optional CLI path. A profile assigned to a role cannot be
deleted. Adding a provider means one adapter module and one line in `PROVIDERS`.

**Usage ledger.** `runtime/llm/usage.jsonl`, one line per call: time, role, profile, model, project id, job kind,
input, cache write, cache read and output tokens, cost, seconds, images, turns, web calls. The project and job labels
come from a context variable set around each job, so adapters stay independent of projects. `cost_usd` is the
provider's estimate at list prices. `POST /api/v1/llm/usage/reset` renames the file to `usage-<unix time>.jsonl`.

---

## 3. Physics engine: requirements

`physics/requirements.py`, numpy only, no LLM. `derive(spec, base)` returns a list of `Requirement`. Every one has
an id, a comparator (`>=`, `<=` or `info`), a target and a `Calc` (formula text, named inputs with units, result,
unit, note). Targets are rounded to 3 significant digits (`_r`) and the check compares against the rounded target.

Constants: `SF` = `physics_inputs.safety_factor` (default 1.5), `g = 9.80665`, `approach` =
`approach_height_m` (default 0.10 m), `clearance` = `clearance_m` (default 0.05 m).

**Task points.** The marker of every `pick`, `place`, `move`, `press`, `inspect` and `push` action (the action's
`target`, or for a `pick` without target the object's `start_marker`). With none, every marker except `robot_base`.

**Base placement** (`place_base`):
- Fixed robot: xy = centre of the smallest circle enclosing the xy of the task points (brute force over pairs and
  triples), z = 0, yaw facing the first `pick` point (first task point if none). A marker with id `robot_base`
  overrides the mounting point, z included; the yaw still faces the first pick.
- Mobile (`locomotion.required`): the first path point, z = 0, yaw toward the second path point. With no path, the
  first task point and yaw 0. A `robot_base` marker is ignored for mobile robots.

**Requirements:**

| id | Cmp | Unit | Formula as implemented |
|---|---|---|---|
| `reach_max` | `>=` | m | `max_i dist(P_i, S_i) + clearance`. `P_i` runs over each task point and the same point raised by `approach`. `S` is the base mounting point; for mobile tasks, the nearest path point (per point). The note names the governing point. |
| `reach_min` | info | m | `min_i dist(P_i, S_i)` over the task points. |
| `z_max` | info | m | `max(z_i - z_base) + approach`. |
| `z_min` | info | m | `min(z_i - z_base)`. |
| `payload` | `>=` | kg | `(m_object + m_tool) * SF`. `m_tool` is `tool_mass_kg` if given, else `0.3 + 0.15 m_object`, capped at 3 kg when `m_object <= 10`. |
| `tcp_speed` | `>=` | m/s | See "Time budget" below. Only when some move is longer than 1e-9 m. |
| `tcp_accel` | `>=` | m/s^2 | See below. |
| `grip_force` | `>=` for parallel, vacuum, magnetic; info otherwise | N | parallel: `F = m (g + a) / (n mu) * SF`. vacuum, magnetic: `F = m (g + a) * SF`. hook, fork, none: `W = m (g + a)`. `a` is the `tcp_accel` target (`peak_acceleration_m_s2` or 1.0 when there are no moves). `n` = `contact_count`, `mu` = `friction_coefficient`. |
| `cycle_time` | `<=` | s | `T_cycle` as specified. |
| `accuracy` | `<=` | m | `repeatability = positional_accuracy_m / 2`. |

Only when `locomotion.required`:

| id | Cmp | Unit | Formula |
|---|---|---|---|
| `travel_distance` | info | m | Sum of path segment lengths; `travel_distance_m` when the path has fewer than two points. |
| `drive_speed` | `>=` | m/s | `v = d / (T (1 - f))`, `f = 1/3`, `T = (cycle - dwell) * 0.6` (`MOBILE_DRIVE_SHARE`). If `travel_speed_m_s` is given, `v` is that and `T = d / (v (1 - f))`. With no distance: the given speed or 0.5. |
| `drive_accel` | `>=` | m/s^2 | `a = v / (f T)`. With no distance: 0.5. |
| `wheel_torque` | `>=` | N*m | `tau = m_total (a + g sin(theta) + g Crr cos(theta)) r / n_wheels * SF`, with `n_wheels = 2`, `a` the `drive_accel` target, `r` = `wheel_radius_m` or 0.08 m, `Crr` = `rolling_resistance`. `m_total` = vehicle mass + object mass; vehicle mass is `vehicle_mass_kg` or `25 + 1.0 m_object` kg. |
| `slope` | `>=` | deg | `max(locomotion.slope_deg, physics_inputs.slope_deg)`. |
| `battery` | `>=` | Wh | `E = cycles * (m (g Crr cos(theta) + g sin(theta)) d + m v^2 / 2) / eta * (1 + hotel) / 3600`, `eta = 0.7`, `hotel = 0.15`. `cycles = cycles_per_hour * 8` (`cycles_per_hour` or `3600 / cycle_time`). One-way drive per cycle, no regeneration. |

**Time budget.** `dwell` is the sum of `duration_s` of `wait`, `inspect` and `press` actions only, capped at 50 %
of the cycle (`MAX_DWELL_SHARE`; the note says so when the cap applies). `budget = cycle - dwell`. For mobile tasks
with a drive distance, the arm gets `budget * (1 - 0.6)`; otherwise it gets `budget`. The moves are:
- the leg between each pair of consecutive task points (for mobile tasks, only legs whose points are nearest the same
  stop);
- for fixed robots with more than one point, the return from the last point to the first;
- the approach move `approach`, twice for every pick and place (down and up).

With `d_longest` the longest move and `sum_d` the total: `T_move = T_budget * d_longest / sum_d`, `t_a = T_move / 3`.
- Without `peak_acceleration_m_s2`: `v = d_longest / (T_move - t_a)` and `a = v / t_a`.
- With it: `a = max(peak, 4 d_longest / T_move^2)` and `v = (a T - sqrt(a^2 T^2 - 4 a d)) / 2`.

`motion.required_for(distance, time, accel_fraction)` is the first form.

**Motion helpers** (`physics/motion.py`): `trapezoid(distance, v_max, a_max)` returns total time, accel time, peak
speed and whether the profile is triangular (when `d < v^2 / a`). `sample(distance, v_max, a_max, dt)` gives
monotonic positions from 0 to exactly `distance`.

**Statics** (`physics/statics.py`): `tau = -sum_i J_i^T F_i` with `F_i = (0, 0, -m_i g)`: the torque (revolute) or
force (prismatic) each joint must supply to hold the link masses and the payload. `numeric_jacobian` uses central
differences (eps 1e-6).

---

## 4. Robot families (plug-ins)

`families/base.py` defines the `Family` protocol. `families/__init__.py` holds `REGISTRY`, `get(name)` and
`describe()`. A family is a class with:

| Member | Meaning |
|---|---|
| `name`, `title`, `description`, `Params`, `mobile` | Registry key, display text, pydantic parameter model, whether the base drives |
| `parse(params)` | Validate a dict into `Params` |
| `size_from_physics(requirements, spec, hint)` | Params that meet the requirements, starting from the agent's hint |
| `joints(p)` | `Joint` list: name, type, limits, max velocity, max effort, `actuated` (False for wheels animated by the base motion). The order of actuated joints is the order of `q`. |
| `fk(p, q)` | 4x4 tool-centre-point pose in the base frame |
| `chain_points(p, q)`, `chain_radii(p)` | Points base to TCP and one radius per segment: the capsules for collisions |
| `jacobian(p, q)`, `gravity_torques(p, q, payload)` | 3 x n positional Jacobian; static holding torques |
| `ik(p, target, seed)` | `IKResult` (q, ok, error, orientation error, note) |
| `workspace(p)`, `capabilities(p)` | Sample points; rated values (`Capabilities`) |
| `build_urdf(p, name)` | URDF XML; joint names equal `joints()` names |

Optional members the pipeline uses when present: `clamp_to_caps(params)`, `raise_speeds(params, factors, keep)`,
`SIZED_FIELDS`, `sizing_hint(params)`, `joint_accels(p)`, `base_params(p)`.

**Adding a family:**
1. Add a parameter model to `families/params.py` (extra keys ignored; lengths in m, angles in degrees).
2. Write `families/<name>.py` with a class implementing the protocol. Build the URDF with `families/urdf.py` (root
   link `base_link`, tool chain ending in a link named `tcp`). Put the plausible-limit constants in the module and
   implement `clamp_to_caps` so absurd requirements fail visibly.
3. Add one instance to `REGISTRY`.
4. The robot agent sees it through `describe()`. The catalog can model candidates of that family only if you also add
   a branch in `catalog/model.py` and a vocabulary in `catalog/extract.py`. Candidates of a family without such a
   branch are rejected at analysis (`ValueError: Candidate family ... cannot be modelled`).

**Families:**

| Family | Joints | IK |
|---|---|---|
| `serial_arm` | `j1` base yaw (z), `j2` shoulder pitch, `j3` elbow pitch, `j4` wrist pitch, then `j5` (4 to 6 DOF; dof 5: roll about the tool axis; dof 6: `j5` about x, `j6` tool roll). Zero pose: arm straight up, tool up. | Damped least squares |
| `gantry` | `x_axis`, `y_axis` (prismatic, centred on the base), `z_axis` (prismatic, down from the beam), optional `tool_yaw` (revolute) | Closed form |
| `mobile_base` | Wheels only (`wheel_left`, `wheel_right`, more for 4 or more drive wheels), not actuated in the arm sense | Reaches only its own deck |
| `mobile_manipulator` | Arm joints plus the base's wheels; arm mounted on the deck at `arm_mount_m` | The arm's IK, shifted by the mount |

Mobile bases have `Frame.base` set; fixed robots have `None`. The mobile base's TCP is the middle of the deck top.

### 4.1 Inverse kinematics

`families/ik.py`: damped least squares on any `fk(q)`. Step `-J^T (J J^T + lambda^2 I)^-1 r` with `lambda = 0.05`,
Jacobian by central differences, step norm limited to 0.4, joint limits by clamping, 120 iterations. The residual is
the position error, plus (tool-down mode, weight 0.3) the cross product of the tool z axis with straight down.
Several seeds are tried, in this order: the caller's first guess, the given seed, other guesses, zero pose, 6 random
poses within limits from a fixed-seed RNG (12345). Tool-down is tried first and dropped if it cannot be met. Accepted
when the position error is below 1 mm (and the tool-down error below 0.02 rad when used). It returns the best
candidate if none is accepted. `IKResult.ok` is the position test only.

The serial arm adds closed-form planar solutions for a tool-down wrist point (both elbow branches, both base yaws) as
guesses, which the iteration polishes. The arm solves joints 1 to 4; joints 5 and 6 stay at their seed.

The gantry: `q = (x, y, frame_height - z_offset - tool_length - z)` clipped to the strokes; `ok` when the clipped
position is within 1 mm of the target.

### 4.2 URDF and GLB

`families/urdf.py` writes links with box or cylinder visual, collision and inertial blocks (inertia of a box around
the first shape's extent), and joints with limits. The root is `base_link`; the tool chain ends in `tcp`. The result
re-parses with `ingest/urdf.parse_urdf`, which the API uses to attach a `parsed` description (links, joints,
geometry) to every version and analysis, so the client can draw and animate it. The client moves joints by name from
`Frame.joints`.

`families/export.py` builds a GLB of the zero pose with trimesh (boxes and cylinders from the URDF visuals, with
their colours). It also has an `stl()` helper that the API does not use. The export route writes the GLB only.

---

## 5. Sizing

`pipeline/design.size_and_simulate` does, in order:
1. `family.size_from_physics(requirements, spec, concept.params)`, starting from the agent's hint;
2. `clamp_to_caps`, wherever the value came from (hint, previous version, sizing);
3. explicit user numbers (from a revision) merged on top, unchanged, then `parse`;
4. build the URDF and simulate;
5. the speed-sizing loop (section 5.5);
6. append what the loop raised to the concept's rationale ("Speed sizing: ...").

### 5.1 Serial arm (`SerialArm.size_from_physics`)

Rated TCP speed and acceleration are capped at `MAX_TCP_SPEED_M_S = 4` and `MAX_TCP_ACCEL_M_S2 = 30`; acceleration
is `speed / ACCEL_TIME_S` (0.25 s), so the speed cap `min(4, 30 * 0.25)` makes the effective acceleration limit
16 m/s^2. Joint speeds are capped at `MAX_JOINT_SPEED_DEG_S = 360`.

Length rule. With `h` = base height = mid-height of the task, clamped to 0.1 to 1.5 m (heights relative to the base z);
`rho` = largest horizontal distance from the base to a waypoint plus clearance (or, on a vehicle, from the reach
requirement); `dz` = the largest vertical distance of the wrist point (task height + tool length + wrist length)
from `h`:
- `s12 = max( hypot(rho, dz) / 0.85, sqrt(reach^2 / 0.85^2 - h^2) - wrist - tool, 0.3 )`, at most 9.5 m;
- `upper_arm = 0.52 s12`, `forearm = 0.48 s12`; link radius `0.06 s12` clamped to 0.03 to 0.15 m.

Hint link lengths are never kept. The waypoints used are the real ones (grasp points and carry heights), taken from
`sim.task.waypoints`.

Other values:
- `payload_kg` = the `payload` target (so the rating includes the safety factor);
- upper arm and forearm masses scale with length and payload (`base_mass * (s12 * share / 0.4) * (1 + 0.05 payload)`)
  unless the hint gives them;
- `gripper_force_n = ceil(grip_force target)`;
- `repeatability_m = min(hint, 0.8 * accuracy target)`;
- `max_tcp_speed_m_s = max(hint or 1.0, 1.3 tcp_speed, 1.3 * 0.25 * tcp_accel)`, capped as above;
- joint speeds (if not in the hint): `w = max(180, deg(1.5 v / s12))` deg/s for joints 1 to 3, `1.3 w` for the rest;
- joint torque ratings (if not in the hint): `ceil(|gravity_torque| * SF)` (at least 1) with the arm stretched out
  horizontally (j2 at 90 degrees) and the rated payload, for each joint; j1's rating is `ceil(0.5 * j2's)`.

### 5.2 Gantry

Cap constants `MAX_AXIS_SPEED_M_S = 5`, `MAX_AXIS_ACCEL_M_S2 = 50`.
- `span` = largest |marker xy - base xy| over all spec markers; `margin = max(0.1, clearance)`;
- `x_stroke = max(2 span_x + 2 margin, 0.3)`, same for y;
- `top = max(z_max requirement, highest waypoint z) + margin`;
- `frame_height = top + z_offset + tool_length`; `z_stroke = max(top - max(lowest z, 0) + margin, 0.2)`;
- `v_xy = min(max(hint v_x, 1.3 tcp_speed), 5)`, `a_xy = min(max(hint a_x, 1.3 tcp_accel), 50)`; z axis at least
  `0.5 v_xy` and `0.7 a_xy`;
- axis force ratings, rounded up to 10 N: `Fx = (x + y + z moving mass + payload + tool) a_xy SF`,
  `Fy = (y + z moving mass + payload + tool) a_xy SF`, `Fz = (z moving mass + payload + tool) (g + a_z) SF`;
- payload, gripper force and repeatability as for the arm.

### 5.3 Mobile base

Caps `MAX_SPEED_M_S = 3`, `MAX_ACCEL_M_S2 = 3`.
- `length = max(hint, object x + 0.2)`, `width = max(hint, object y + 0.2)`; track `<= 0.9 width`;
- `max_speed = min(max(1.1 drive_speed, hint, 0.1), 3)`, `max_accel` likewise;
- `max_slope = max(1.1 slope, hint)`;
- `wheel_torque_nm = ceil(max(m (a + g sin + g Crr cos) r / n_wheels * SF, 1.1 wheel_torque target, hint))`
  with `m` = base mass + payload as sized and `n_wheels` = `drive_wheels` (so the rating can differ from the
  requirement, which assumes two wheels and the estimated vehicle mass);
- `battery_wh = max(ceil(1.1 battery target), hint)`.

### 5.4 Mobile manipulator

The arm is sized with `mount_z` = deck height + mount z, and the `reach_max` target is raised by the distance from
the base origin to the arm mount. The base is sized with the arm's mass added. The mobile manipulator takes no
speed sizing. Base tipping is not modelled.

### 5.5 Speed-sizing loop

If the `cycle_time` check fails because the run is too long (actual above the cycle), up to 3 times
(`MAX_SIZING_ITERATIONS`):
- `ratio = actual / cycle`;
- find each joint's peak speed as a share of its rating from the frames; the joints at 70 % or more of the busiest
  joint's share (`LIMITING_SHARE`) get factor `ratio * 1.05` (`SIZING_MARGIN`);
- `raise_speeds` raises those ratings (serial arm: capped at 360 deg/s; gantry: axis caps, force rating grows with
  the acceleration) and returns notes and the capped joints; explicit user numbers are never changed;
- rebuild the URDF and simulate again.

Only the serial arm and the gantry have `raise_speeds`. If the cycle still fails, the `cycle_time` check detail says
why (speed cap, explicit numbers or no rule for this family, or not met after sizing).

---

## 6. Task simulator

`sim/task.py`. Kinematic and static only.

**Waypoints** (`waypoints(spec, scene, base)`, kinds `approach, grasp, lift, carry, place, release, retreat, home,
drive, wait`). `zg` is the grasp or place height: marker z plus half the object height (centre of the object) for
parallel grip, plus the full height (top) for vacuum and magnetic; the marker z for `press` and `push`. `za = zg +
approach`.
- `pick`: approach (xy, `za`), grasp (`zg`, dwell 0.1 s), lift (`za`). The object is carried from here.
- `place`: carry (at `max(carried height, za)`), approach, place, release (dwell 0.1 s, object let go), retreat.
- `press`, `push`: approach, place (dwell `duration_s`), retreat.
- `move`: one carry waypoint at marker z + object height + approach, never the surface. The object is held only
  after a `pick`.
- `drive`: base moves to the path point nearest the target (searching forward from the current stop); the last stop
  with no target. A fixed robot skips it with a warning event.
- `home`: back to the start pose. `wait`, `inspect`: stand still for `duration_s`.

**Run.**
- The arm starts at the pose that reaches the first waypoint (IK from the zero pose); the base starts at the first path
  point (mobile) or the base pose.
- Each waypoint is solved by IK, seeded by the previous pose, in the base frame (world target rotated and shifted by
  the current base pose).
- Joint moves are synchronised in joint space. One trapezoid profile on the unit path is scaled so that the most
  limited joint stays inside its speed and acceleration. Acceleration is `speed / 0.25 s` unless the family has
  `joint_accels` (the gantry).
- Mobile bases drive the path polyline with one trapezoid profile from the rated speed and acceleration. The heading
  follows the segments, limited to 2 rad/s, with no extra time cost. Wheel angles are integrated from the motion.
- Static joint loads are evaluated at every waypoint pose with the payload then held; the worst (largest absolute)
  value per joint is kept. The path between waypoints is not examined.
- Sampling is every 0.04 s, decimated to at most 1500 frames. Each `Frame` has time, joint positions by URDF name,
  base pose (mobile), the object held, the TCP in the world frame and the phase label.

**Timing stretch.** If the fastest feasible run (`fastest`) is shorter than the cycle time, the run is repeated with
all motion stretched by `k = (cycle - dwelled) / moving` (speeds divided by `k`, accelerations by `k^2`), where
`dwelled` is the standing-still time (waits, grips, presses; not stretched) and `moving` is the rest. The run then
fills the cycle. The `cycle_time` check's detail reports the fastest feasible time. A longer run is kept and fails.

**Collisions** (`sim/collision.py`). Every second sample, each link segment is a capsule (the family's `chain_points`
and `chain_radii`) tested against every scene box with `collides = True`. The test samples the capsule axis every
2 cm and measures the distance to the oriented box (yaw only). A gap of 0 or less is a collision. An episode is
counted when a (box, segment) pair starts touching; at most 20 events are written. Exceptions:
- the box the object rests on, during grasp and release and for the move right after;
- a fixed robot mounted on a table (base z above 0.02 and within 2 cm of a box) may touch that box with its first
  segment.

Collisions are reported, not avoided. There is no planner: moves go straight in joint space.

**Checks.** `report.check(requirement, actual, calc, detail)` applies the comparator with a tiny tolerance;
`actual = None` gives `passed = None`. `SimResult.ok` is true when every check with a verdict passed. The "actual"
value of each requirement id:

| Requirement id | Robot value |
|---|---|
| `reach_max` | The family's rated maximum reach. The check also fails if any waypoint is unreachable by IK; the detail says "N of M waypoints reachable". |
| `reach_min`, `z_min`, `z_max` | Rated values (info, no verdict) |
| `payload`, `grip_force`, `accuracy` | Rated payload, gripper force and repeatability |
| `tcp_speed`, `tcp_accel` | Rated TCP speed and acceleration (gantry: norm of x and y axis values) |
| `cycle_time` | The simulated duration |
| `travel_distance` | Distance driven (mobile only) |
| `drive_speed`, `drive_accel`, `wheel_torque`, `slope`, `battery` | Rated values of the base |

For robots with no arm (`mobile_base`), the arm-related ids (`reach_*`, `z_*`, `grip_force`, `tcp_*`, `accuracy`)
have no value ("the load is carried on the deck") and pass nothing.

Extra checks:
- `joint_limits`: every waypoint pose inside the joint ranges (and no unreachable waypoint);
- `joint_torque_<joint>`: worst static load against the joint's rating (a failed one also adds a `limit` event);
- `joint_speed`: the peak simulated speed of the busiest joint against its rating (within 2 %);
- `collision_free`: no collision episodes;
- mobile with a path: `path_follow` (largest distance from the path polyline, at most 5 mm) and
  `drive_speed_limit` (peak simulated base speed against the rating).

---

## 7. Design flow and projects

`pipeline/design.specify` (job `design`): runs the spec agent until it returns a spec (answers accumulate in the
project; `round` counts), then `design`:
1. `place_base`, `derive`;
2. the scene agent and the robot agent run at the same time (`asyncio.gather`);
3. `size_and_simulate` in a worker thread;
4. a `DesignVersion` is written as `v<n>.json`.

A second `design` or `revise` job for the same project while one is running returns 409 (Java `JobService`).

The Python task `design` runs `pipeline/design.py` (`specify`, then `design`). Java assigns the version number,
`created` and `parent_version` and writes `v<n>.json`.

### 7.1 Revise flow

`pipeline/design.revise` (job `revise`):
1. The revise agent gets the current design (spec, scene, robot family, name, rationale and params, and the failing
   checks) and the request (2000 characters at most). It returns a patch: `targets` (any of spec, scene, robot) and
   the complete new object for each target.
2. Validators: the spec is checked as in section 1; the scene against the new spec and base; the robot concept as in
   section 1.2. A failure goes back to the model once.
3. If the spec changed: `place_base` and `derive` run again; the base counts as moved when it is more than 1 cm
   (`BASE_MOVE_M`) from the old one.
4. Scene: a patched scene is used. If the spec changed and markers or the base moved, the scene agent rebuilds the
   scene. The scene's `robot_base` is always set to the computed base.
5. Robot: if the patch has a robot, its params that differ from the old params (same family only) are the explicit
   user numbers.
6. Re-sizing: when the spec changed or the base moved, the hint passed to sizing drops the numbers code sized earlier
   (link lengths, strokes, speed, torque and force ratings, sized masses, payload, grip force, repeatability) and
   keeps the family, qualitative choices (dof, gripper, tool yaw, tool length, mount) and the explicit numbers. A
   patch with only a scene re-simulates without re-sizing.
7. A new version `v(n+1)` is written with the request and the summary. `v(n)` is kept.

**The `robot_base` marker rule.** The marker with id `robot_base` in the spec is the single source of truth for where
a fixed robot stands: its xy is the mounting point and its z is the height of the mounting surface (0 = floor).
Without it, code places the base. The revise prompt says so. If a patch moves `robot.base` by more than 1 cm but adds
no `robot_base` marker (and the robot is not mobile), `base_marker_from_robot` turns that position into the marker
(and adds it to a patched scene). When z is above 0.02, the scene validator requires a box whose top is at that z and
whose footprint contains the base xy, and exempts that box from the 0.3 m base-clearance rule.

### 7.2 Scene validation

Markers keep their positions (1 cm); every spec object is present, at the xy of its start marker and with its
bottom at the marker's z; every pick, place or start marker above 0.02 m needs a table, shelf, conveyor, pallet, box
or obstacle whose top is within 2 cm of the marker's z and whose footprint contains it; no colliding box within 0.3 m
of the base (0.1 m for a surface that holds a marker), except boxes whose bottom is 2 m or higher; floor size at least the span of the
layout plus 0.5 m.

### 7.3 Specification validation

Unique marker and object ids; sensible positions (within 1000 m); action targets and objects exist; `pick`, `place`,
`move`, `drive` have a target (or a pick has an object with a start marker); dwell times (wait, inspect, press) less
than the cycle time; `physics_inputs` object mass, size and grip equal the main object's; locomotion needs a path of
two or more points; `travel_distance_m` only with locomotion; the frame is stated; in the last round,
assumptions are listed.

---

## 8. Reference material and intake

- URDF or zip: `ingest/urdf.py`, ported from the RoboDE importer. No xacro, no external fetching, no DTD, entities or
  includes. ZIPs are read without extraction: at most 512 entries, 100 MB expanded, 25 MB per file, no unsafe paths,
  no symbolic links. Upload at most 32 MB; URDF at most 2 MB, 256 links, 256 joints. The summary goes to the spec
  agent.
- Photos: PNG, JPEG or WebP by file signature, 15 MB each, at most 6.
- Reference text: at most 60,000 characters; the spec agent sees the first 20,000.
- Documents (`ingest/docs.py`): PDF (pypdf), HTML, and text types (`.md .txt .rst .json .yaml .yml .py .h .hpp .c
  .cpp .csv .xml`); at most 40 MB; text capped at 400,000 characters. Scanned PDFs need OCR first.
  `contains_quote` compares text after lowercasing, removing back-ticks and collapsing white space.

---

## 9. Storage

Everything is files under `runtime/` (git-ignored; `STUDIO_RUNTIME` moves it). The Java server is the only writer, and
it writes through a temporary file and an atomic rename. Every JSON file it writes carries `"schemaVersion": 1`.

```
runtime/
  llm/
    profiles.json      saved model profiles and role assignments
    keys.json          API keys, mode 0600, never returned by the API
    usage.jsonl        one line per model call
  projects/<p_xxxxxxxxxx>/
    project.json       brief, stage, questions, answers, round, spec, approved_version,
                       selected_candidate, decision, created, updated
    versions/v<n>.json one DesignVersion. Written once; only the `approved` flag changes later.
    reference/         text.md, robot.urdf + urdf.json (summary), images/<name>
    candidates.json    the last search result
    documents/<cid>.txt  datasheet text (downloaded or uploaded), <cid>.json with its source
    analysis/<cid>.json  the Analysis
```

Project ids are `p_` plus 10 hex digits and are validated on every access; candidate ids, file names and uploads are
validated the same way. `stage` moves through `brief`, `questions`, `designed`, `approved`, `candidates`,
`analysed`, `decided`. The interface unlocks its stages from what exists (versions, an approved version, analyses),
not from `stage`.

`approve(n)` sets `approved` true on version `n` and false on all others. The API does not stop a revision after
approval (the interface does), and approving again moves the approval.

Jobs are kept in memory only: a restart loses running and finished job records (the projects are safe). Jobs cannot
be cancelled.

---

## 10. API

Spring Boot on `127.0.0.1:8800` (`STUDIO_HOST`, `STUDIO_PORT`). All routes are under `/api/v1`; the OpenAPI document
is at `GET /v3/api-docs`. Request bodies up to 64 MB (413 beyond that). The server refuses a non-loopback
`STUDIO_HOST` unless `STUDIO_TOKEN` is set, and then every `/api/v1` call needs `Authorization: Bearer <token>`.
Without a token there is no login. Errors have the body `{"detail": "<message>"}`: a value error is 422, a missing
item 404, a model failure 502, a conflict 409.

Long work is a job: the POST returns `{id, kind, robot_id (the project id), status, progress[], result, error,
error_code, started}`; the client polls `GET /api/v1/jobs/{id}` every 0.7 s. A datasheet that cannot be fetched gives
`error_code: datasheet_download`. Finished jobs are kept in memory, up to 500.

| Route | Purpose |
|---|---|
| `GET /api/v1/health`, `GET /api/v1/families` | Role assignments; family descriptions with parameter schemas |
| `GET /api/v1/jobs/{id}` | Job record |
| `GET /api/v1/llm`, `POST /api/v1/llm/profiles`, `PUT/DELETE /api/v1/llm/profiles/{id}`, `PUT /api/v1/llm/roles` | Profiles and roles |
| `PUT/DELETE /api/v1/llm/keys/{provider}` | Save or remove a key (only `gemini` uses one) |
| `POST /api/v1/llm/profiles/{id}/test` | Job: text call and, if supported, image call |
| `GET /api/v1/llm/usage?period=` today, 7d, 30d or all (also `/api/v1/usage`), `POST /api/v1/llm/usage/reset` | Usage |
| `GET/POST /api/v1/projects`, `GET/DELETE /api/v1/projects/{id}` | List, create (brief up to 8000 chars), open, delete |
| `PUT /api/v1/projects/{id}/reference`, `DELETE .../reference` | Reference text; clear the whole reference |
| `POST /api/v1/projects/{id}/reference/files?filename=` | Raw body: image, `.urdf` or `.zip` |
| `POST /api/v1/projects/{id}/design {answers}` | Job; result `{status: questions, questions}` or `{status: designed, version}` |
| `GET /api/v1/projects/{id}/versions/{n}` | The version with `parsed` URDF |
| `POST /api/v1/projects/{id}/revise {request}` | Job; result `{version}` |
| `POST /api/v1/projects/{id}/approve {version}` | Approve |
| `POST /api/v1/projects/{id}/search` | Job; result `{candidates}` |
| `GET /api/v1/projects/{id}/candidates` | Last search result |
| `POST /api/v1/projects/{id}/candidates/{cid}/analyse {datasheet_url?}` | Job; result the `Analysis` with `parsed` |
| `POST /api/v1/projects/{id}/candidates/{cid}/datasheet?filename=` | Raw body: upload a datasheet |
| `GET /api/v1/projects/{id}/analysis/{cid}` | A saved analysis |
| `POST /api/v1/projects/{id}/decision {candidate_id, action}` with action `approve` or `robot_studio` | Record; the candidate must have an analysis |
| `GET /api/v1/projects/{id}/export` | ZIP (section 12) |

Anything else not under `/api/v1` or `/v3` serves `client/dist` (with `index.html` as the fallback), or a plain note when the
client is not built.

---

## 11. Catalog: finding and checking real robots

### 11.1 Search (`catalog/search.py`, `pipeline/catalog.find`)

Requires an approved version. The `search` role's adapter is switched to web mode. The input has today's date (UTC),
the limit (6), the task, the environment, the payload description, the `payload`, `reach_max`, `tcp_speed` and
`accuracy` targets, all requirements, and the generated design's family and name. The prompt:
- treats page text as data, not instructions;
- asks for the smallest robots that meet every requirement with margin (rated payload about 1 to 5 times the
  requirement, reach about 1 to 1.6 times), best fit first, a cheaper and a more capable option;
- defines the families (`serial_arm`, `gantry`, `mobile_base`, `mobile_manipulator`, `other`; a SCARA is `other`);
- asks for manufacturer pages first, only products sold now, every number with its unit, a verbatim quote and the page
  URL, and the datasheet URL.

If the answer is not valid JSON, a second call repairs it without tools. `clean()` then:
- looks at up to twice the limit; skips entries without an http(s) `product_url`; clears image and datasheet URLs that
  are not http(s);
- nulls `payload_kg`, `reach_m`, `dof` and `repeatability_mm` unless they have a value and a quote; drops key specs
  without a quote;
- makes ids from manufacturer and model (`slug`, with `_2`, `_3` for duplicates) and validates with pydantic;
- keeps at most the limit.

**Price rules** (`price_problem`). A price is kept only with: a source URL; a verbatim quote; at least one of `low`
or `high` as a number; and a figure that appears in the quote (separators `,` `.` space and the thin and
non-breaking spaces, and currency symbols, are tolerated; compared to 0.005). A missing date becomes `seen YYYY-MM-DD`
(today, UTC). Otherwise the price is null and the reason ("no source URL", "no verbatim quote", "no price figure",
"figure not in the quote") is counted. The job reports "N prices dropped: ...". The quote shown to the user is the
model's text; the code cannot check it against the page, because the model fetched the page. The date, source and the
`web estimate` tag stay on every price.

**Screen.** `screen()` compares the sourced payload and reach, read to kg and m with `units.to_si`, with the
`payload` and `reach_max` requirements. Unreadable or missing gives `passed = None`. The screen uses the published
number against the target (which includes the payload safety factor).

**Images** (`fill_images`). For candidates without an image, the product page is fetched through `safe_download` (HTML
only, 3 MB, 10 s, 3 at a time) and the first `og:image` (else `twitter:image`) becomes `image_url` if it is an https
URL of at most 800 characters (relative URLs resolved). Failures are ignored. The image is not downloaded: the
browser loads it from the vendor with no referrer.

### 11.2 Analysis (`pipeline/catalog.analyse`)

1. **Document** (`datasheet_text`): an explicit URL given in the request is downloaded and used (an uploaded document
   is ignored then). Otherwise a stored document (uploaded earlier or fetched before) is used. Otherwise the
   candidate's `datasheet_url`, then, when there is none or it fails, the product page. The text is stored under
   `documents/<cid>.txt` with its source. If none works, the job fails with `Datasheet download failed: ...` and
   `error_code: datasheet_download`.
2. **Extraction** (`catalog/extract.py`). The text is cut to 60,000 characters: the first 3,000 plus the best
   passages for six topic queries. The model returns values with keys from a fixed vocabulary (`payload_kg`,
   `reach_m`, `dof`, `mass_kg`, `repeatability_mm`, `j1` to `j6` with `_min_deg`, `_max_deg`, `_speed_deg_s`, link lengths, gantry strokes,
   speed and acceleration, wheel radius, track, battery, slope, `ip_rating`), each with value, unit, a verbatim
   quote of at most 25 words and the page. Values are converted into the key's unit. Unknown keys and duplicates are
   dropped.
3. **Verification.** `verified = contains_quote(document, quote) and number_in_quote(value, unit, quote)`. The number
   must appear in the quote, as written or in another unit of the same dimension (within 1 %). Unverified values are
   kept and flagged.
4. **Candidate model** (`catalog/model.candidate_concept`). Only verified values are used.
   - Serial arm: `dof` (4 to 6; resets per-joint lists), payload, repeatability, base height, link lengths. If the
     reach is verified but link lengths are not, the generated segments are scaled so that
     `upper_arm + forearm + wrist` equals the verified reach. Mass scales the generated link masses. Joint ranges and
     speeds are used only when every joint has them; otherwise defaults fill the gaps.
   - Gantry: strokes, speeds and accelerations, payload, repeatability.
   - Mobile base and manipulator: wheel radius, track, battery, slope, speed, acceleration; mass and payload (base only).
   - Anything not verified starts from the generated robot (or from family defaults when the family differs) and is
     listed in `unverified` with a note. A missing payload or (for arms) reach is listed explicitly.
   - Every parameter gets a provenance (`verified`, `derived`, `carried` or `default`), kept as `Analysis.provenance`.
     A verified `max_speed_m_s` becomes the serial arm's TCP speed (gantry axis speeds, mobile base speed); the arm's
     TCP acceleration is derived from speed and stays `default`. The gripper and tool are `carried` from the generated
     design.
   - Family `other` raises `ValueError: Candidate family ... cannot be modelled`.
5. **Simulation.** The candidate is simulated on the approved version's scene and requirements with the generated
   robot's base. It is not re-sized. Afterwards `qualify_checks` withdraws the verdict (`passed=None`, "Not published
   by the manufacturer; <param> assumed <value>") from every check that depends on a `default` parameter (joint torque,
   joint speed, TCP acceleration, cycle time, ...); the estimate stays in `actual`. Gripper checks keep their verdict
   ("uses the generated design's gripper") and reach, joint-limit and collision checks keep theirs ("link lengths
   estimated from the verified reach"). `ok` counts only checks with a verdict.
6. **Comparison** (`catalog/compare.differences`): one `Difference` for every check of the candidate run (topic =
   check name; text from the check's actual value, verdict and requirement), plus checks only the generated run had
   ("not checked for the candidate"). When there is no cycle check, a "Task duration" row. Checks without a number
   read "passes"/"fails" plus their detail; unchecked candidate values read "not published (reason)". Requirements
   with comparator `info` are left out. Rows for rated payload and repeatability appear only when no payload or
   accuracy row covers them; torque-rating rows only when the rating is not a default.
7. **Severity** (code): no check or no verdict gives `minor`; a failed check gives `blocking` when the requirement id
   starts with `reach`, `payload`, `grip` or `collision`, else `major`; a pass within 10 % of the requirement gives
   `minor`; otherwise `ok`. Parameter rows: `ok` within 10 % (payload, repeatability) or when the candidate's torque
   rating is at least 90 % of the generated one, else `minor`.
8. **Explanation.** The analysis agent writes `impact` for each difference. Its validator requires the same number
   of rows in the same order and rejects any change to topic, generated, candidate or severity. Its prompt says
   "not published" means the manufacturer did not state the value (not a failure), never to call a check unavailable
   when it passed or failed, and to call assumed values estimates.

### 11.3 Safe download (`catalog/fetch.py`)

Used for every download that code starts: datasheets, product pages, image lookups.
- Only `https://`. Credentials in the URL are refused.
- The host is resolved, and **every** returned address must be public (`ipaddress` `is_global`, and not private,
  loopback, link-local, multicast, reserved, unspecified; IPv4-mapped IPv6 is unwrapped).
- **The connection is pinned to the validated address**: the request goes to that IP, with the `Host` header and the TLS
  SNI set to the real hostname. A second DNS answer cannot redirect the connection (DNS rebinding).
- Redirects are followed by hand, at most 5, and each target goes through the same checks. A redirect with no
  location is refused.
- Status must be 200. Only PDF, HTML, XHTML, plain text and Markdown content types. The size cap (25 MB by default) is
  enforced on the declared length and while streaming.
- File names are cleaned (`safe_name`) and given an extension from the content type.

This guard covers pages that code fetches. Web pages that the search model reads itself go through the model
provider's own web tools, not through this guard; those calls run in an empty working folder with no files or shell
(Claude CLI) or in Google's grounding (Gemini).

---

## 12. Export

`GET /api/v1/projects/{id}/export` returns `<id>-v<n>.zip` for the approved version, or the latest. Files:
`design.urdf`, `design.glb` (if the mesh could not be written, the README says so), `spec.json`, `requirements.json`,
`scene.json`, `sim_summary.json` (version, approved, ok, duration, robot, checks, events), `README.txt` (limits), and
for each analysis `candidates/<id>/analysis.json` (without the URDF and the frames), `manufacturer_values.json`,
`candidate.urdf`; and `decision.json` when a decision exists. Simulation frames are not exported.

---

## 13. Client

React 19, Vite, three.js through react-three-fiber and drei. It calls the API through `createApi` from
`packages/core` at `/api/v1`. `client/src/App.tsx` holds the seven stages and
Settings. Stage state comes from the project the API returns. `components/SceneView.tsx` draws the floor, boxes,
marker pins, objects and the robot from the parsed URDF; `Player.tsx` moves the joints from `Frame.joints`,
the base from `Frame.base` and the held object with `Frame.tcp`. `Requirements.tsx` shows the table and the `Calc` of
each row. Dictation uses the browser's `SpeechRecognition`. The types the client uses are generated from
`contracts/schemas/` into `packages/core/src/generated` (`npm run contracts:types`; `npm run contracts:check` fails when
they are stale). `packages/core` has no DOM code: types, the API client, forward kinematics and playback math.

---

## 14. Testing

`npm test` runs three steps in order and exits non-zero if any fails (`scripts/studio.mjs`):
1. `./mvnw verify` in `server-java/`: Java unit tests and the flow tests below;
2. `pytest` with `pyproject.toml` (`testpaths = tests`, `pythonpath = server`, async mode `auto`);
3. `npm run contracts:check`: the generated TypeScript types match `contracts/schemas/`.

Java (`server-java/src/test/java/dev/robode/studio/`):

| Test | What it covers |
|---|---|
| `store/ProjectStoreTest` | Project files: create, versions, approval; reading a project written by the former Python store; id and name rules |
| `llm/KeysAndUsageTest` | Key file permissions and that keys are never returned; the environment fallback; usage summary against a hand-made ledger |
| `config/BindPolicyTest` | Loopback only unless a token is set |
| `jobs/JobServiceTest`, `jobs/TaskRunnerTest` | Job lifecycle and the 409 guard; the runner against a fake Python engine: stream parsing, error mapping, hung process and child kill, and no API key in argv, environment or log |
| `api/ApiSmokeTest` | The bearer token is required when set, and a project round trip works over HTTP under `/api/v1` |
| `flow/FullFlowTest` | The whole flow through the real `python -m studio.tasks` subprocesses with scripted profiles: design and questions, uploads, revise from a version at a moment, branching, approval and its 409, search, analysis, datasheet download error, decision, export ZIP, usage |
| `flow/GoldenContractTest` | The responses recorded from the former FastAPI server (`server-java/src/test/resources/golden/`, 76 JSON files) replayed under `/api/v1`, with the listed differences allowed |

Python (`tests/`):

| File | What it covers |
|---|---|
| `test_physics.py` | Base placement, requirements, motion, grip, statics |
| `test_families.py` | Families: FK, IK, sizing, URDF re-parse, GLB |
| `test_sim.py` | Waypoints, simulation, stretch, collisions, checks |
| `test_agents.py` | Agent validators and repair turns, with scripted answers |
| `test_pipeline.py` | The design and revise functions in `pipeline/design.py` with scripted LLMs |
| `test_revise_regressions.py`, `test_real_regressions.py` | Saved real-model outputs (`tests/fixtures/real_*.json`) replayed as fixture input through the real code |
| `test_catalog.py` | Search cleaning, price rules, safe download (mocked DNS and transport), extraction, verification, candidate model, comparison |
| `test_tasks.py` | The task protocol: subprocess runs, stdout hygiene, error kinds, key handling, large results, usage lines, find and analyse handlers |
| `test_contracts.py` | The JSON schemas in `contracts/` are current |
| `test_web.py` | Safe download refusals; the real download is opt-in |
| `test_real.py` | One real Claude CLI call; skipped unless `STUDIO_CLAUDE=1` |

**Fixture tests versus real runs.** Fixture tests use `ScriptedLLM` (prepared answers) or scripted profiles, mocked
downloads and no network. They test code: the physics, the families, the simulator, the validators, the pipeline,
the task protocol and the Java server. They are not evaluations of any model. A saved real-model output replayed as a
fixture still tests code, not the model.

Real runs are opt-in:
- `STUDIO_CLAUDE=1`: calls the real Claude Code CLI (model from `STUDIO_CLAUDE_MODEL`, default
  `claude-sonnet-5-5`) for the spec agent on a bottle brief.
- `STUDIO_WEB=1`: the `web` marker is declared, but no test uses it and `conftest.py` does not look at the variable.
  There is no automated real-web test.

Real model runs and real web searches done by hand are recorded, with dates, in
[PROJECT_TIMELINE.md](PROJECT_TIMELINE.md). Only those records count as real-model evidence. Nothing here runs a
simulation in Isaac Sim, on a GPU or on a physical robot.

---

## 15. Limits, stated plainly

- **Kinematic and static-load checks only.** There is no dynamics simulation: no inertia coupling, no friction, no
  contact physics, no controller, no sensor or vibration model. The static load check holds a still pose at each
  waypoint; it does not examine the path between them.
- **Collisions are reported, not avoided.** Moves are direct in joint space. No motion planner. Capsule against box
  is coarse, sampled every second frame. A mobile manipulator's arm checks ignore the base body and base tipping.
- **Rated, not measured, values.** For most requirements the robot's value is its rated capability, computed from its
  parameters (sized by rule for the generated robot, taken from the datasheet for a candidate), not measured in the
  simulated run. Cycle time, joint speed and static load are the exceptions.
- **The generated robot is rule-sized.** Masses, torques and speeds follow the sizing rules in section 5, with caps
  at plausible family limits. It is a class of robot, not a design.
- **Prices are dated web estimates, not quotes.** The model reads the web; code can check that a quote contains its
  figure, not that the page says it.
- **Datasheet values** count only with a verified quote. One document per analysis. Candidates of family `other`
  cannot be analysed. Joint ranges and speeds are used only when all joints have them.
- **No hardware tests.** Nothing here was run on a physical robot. No Isaac Sim or GPU validation.
- **Robot studio is a placeholder.** The decision `robot_studio` is stored and nothing else happens.
- **Speed sizing** covers the serial arm and the gantry. Other families get none.
- **Jobs** are in memory and not cancellable. The API has no login.
- **The Gemini adapter** has no tests here and no recorded run against Google.

### 15.1 `CONTRACTS.md` and the code

`CONTRACTS.md` was reconciled with the code on 2026-11-20 (serial-arm acceleration cap of 16 m/s^2, export contents, LLM
test and usage routes, opt-in tests, mobile base placement, the `wait` waypoint kind, base-move rules, approval freeze).
No known differences remain; if one appears, this document describes the code.
