# Robot studio: design reference

| | |
|---|---|
| Status | Planned; being implemented |
| Date | 2026-11-20 |
| Applies to | `server/studio/` (Python), `server-java/` (API, jobs, store), `client/` (Studio stage), `packages/core/` (twin maths), `contracts/` (OpenAPI) |
| Sources | The approved Robot studio plan and the module interfaces note (both kept outside the repository), the "Robot studio" section of `server/studio/schemas.py`, and the `studio_*` rows of `docs/PROTOCOL.md` |

This is the design reference. It states the rules the code must follow. It records no results.

---

## 1. Purpose and status

Robot studio takes an approved design and makes it buildable. It sizes the joints from the design's task, finds
motors and electronics in shops, generates parametric CAD, runs a digital twin under the chosen motors' limits, and,
after approval, produces a manufacturer PDF and a build package.

### 1.1 What the user gets

| Step | Stage | What happens |
|---|---|---|
| 1 | Build | "Build this robot" starts job `studio_build`: size the joints, search the shops, verify motor specs, choose the structure style, generate CAD, assemble the URDF, check. Result: build **b1**, linked to the approved design version. |
| 2 | Motors and electronics | One row per joint, plus driver, controller and power supply. Each row shows need against choice: product, shop link, price ("seen <date>"), gear ratio, verified or assumed values, margins. A row can read "Defined spec (not found in shops)". "Use this one" on an alternative makes a new build with that motor pinned. That rebuild repeats only the CAD step. |
| 3 | CAD | 3D view of the part meshes, part list (name, material, process, mass, file), downloads: STEP assembly, STEP and STL per part, URDF package. |
| 4 | Digital twin | Joint sliders and jog, "move tool to x, y, z" with IK, gripper, "Run the task" with the chosen motors' limits, live readouts with a "limited by" note when a limit binds. |
| 5 | Change request | "Change the build" makes b(n+1) from b(n), as design versions do. |
| 6 | Approve | Uploads four snapshots (iso, front, side, top), freezes the build, produces the manufacturer PDF and the build package ZIP. |

Families in v1: `serial_arm`, `gantry`, `mobile_base`. `mobile_manipulator` raises `ValueError` ("not in v1"). A
fleet version builds the chosen member only.

Units are SI: torque in N*m (revolute) or N (prismatic), speed in rad/s or m/s, on the joint side unless a field says
motor side.

### 1.2 Planned checks (not run)

`./mvnw test`, pytest, `contracts:check`, typecheck and build; golden tests unchanged; route parity; CAD tests skip
cleanly without OCP; real runs on an approved arm project and an approved gantry project. Results go to
`docs/PROJECT_TIMELINE.md` when they are run.

---

## 2. Data model

Source: the "Robot studio" section of `server/studio/schemas.py`. `Check`, `Calc`, `Frame`, `Vec3` and `DesignVersion`
are defined earlier in that file.

### 2.1 StudioBuild

| Field | Meaning |
|---|---|
| `build` | Build number within the project (b1, b2, ...) |
| `design_version` | Approved design version it was built from |
| `member` | Fleet member built; null for a single robot |
| `parent_build` | Build this one changed from; null for b1 |
| `created`, `request`, `summary` | Creation time; change request (max 2000 chars, empty for b1); summary |
| `family` | `serial_arm`, `gantry` or `mobile_base` |
| `search_date` | When the shops were searched; prices are as seen then |
| `sizing` | list of JointSizing |
| `motors`, `electronics` | list of MotorChoice; list of ElectronicsItem |
| `style` | StructureStyle |
| `parts` | list of BuildPart (bill of materials) |
| `urdf` | Studio URDF: mesh geometry and a `gripper` joint |
| `twin_limits` | TwinLimits |
| `checks` | CAD validity, frames against the joint chain, motor axes, sizing margins |
| `files` | list of BuildFile |
| `approved`, `approved_at` | Frozen after approval; approval time |

### 2.2 JointSizing

Computed in code from the design. Every figure has a `Calc`.

| Field | Meaning | Unit |
|---|---|---|
| `joint`, `kind` | Joint name; `revolute`, `prismatic` or `wheel` | |
| `static_torque` | Worst static load | N*m or N |
| `accel_torque` | (I + m r^2) times alpha | N*m or N |
| `continuous` | RMS over the task times 1.25 | N*m or N |
| `peak` | Worst instant times 1.5 | N*m or N |
| `speed` | Peak joint speed | rad/s or m/s |
| `calcs` | Traceable working for each figure | |

### 2.3 Drive

`ratio` is motor turns per joint turn. For a leadscrew or belt-driven linear axis it is motor radians per metre of
travel.

| Field | Meaning | Unit |
|---|---|---|
| `type` | `direct`, `planetary`, `harmonic`, `belt`, `leadscrew`, `rack`, `wheel_gearbox` | |
| `ratio` | Gear ratio N, greater than 0 | see above |
| `efficiency` | Transmission efficiency eta, greater than 0 and at most 1 | |
| `lead_m` | Leadscrew lead, or belt travel per motor turn | m |
| `name` | Gearbox product or standard description | |

### 2.4 MotorAlternative and MotorChoice

A MotorAlternative is a candidate found by search. A MotorChoice is the motor picked for a joint.

| Field | Meaning |
|---|---|
| `id` (alternative) | Stable within a build; pins it in a rebuild. Pattern `^[a-z][a-z0-9_]{0,62}$` |
| `name`, `shop`, `url` | Product, shop, product page |
| `price` | ShopPrice or null |
| `motor_type` | `stepper`, `bldc`, `servo`, `dc_gear` or `other` |
| `values` | list of SpecValue (torque, speed, voltage, current, mass, frame, shaft, and so on) |
| `drive` | Drive: the gearing code pairs with it |
| `passes`, `reason` | Code result and why (alternative) |
| `joint`, `source` | Joint; `shop`, or `defined` (no suitable product: procure to this spec) |
| `alternative_id` | The chosen alternative; null for a defined spec (choice) |
| `required` | Joint side: `continuous`, `peak`, `speed` |
| `provided` | Joint side, through the drive |
| `margins` | provided / required, and `inertia_ratio` as a value |
| `checks` | list of Check |
| `alternatives` | All alternatives found for the joint, kept for "Use this one" (choice) |
| `pinned` | True when the user chose it |

### 2.5 ElectronicsItem and StructureStyle

| Field | Meaning |
|---|---|
| `id`, `role` | Id; `driver`, `controller`, `power_supply`, `battery` or `other` |
| `name`, `source`, `qty` | Name; `shop` or `defined`; quantity, at least 1 |
| `for_joints`, `shop`, `url`, `price`, `values` | Joints served; as for motors |
| `checks` | For example, driver current at least 1.2 times motor current |
| `style` | `extrusion_printed` or `machined_aluminium` |
| `proposed` | The style agent's proposal |
| `reasons` | The agent's reasons (a proposal) |
| `forced` | True when code overrode the proposal because a check failed |
| `checks` | Bending, deflection, print envelope, printed-part loads |

### 2.6 BuildPart and BuildFile

| Field | Meaning |
|---|---|
| `BuildPart.id`, `name`, `qty` | Id (matches file names); name; quantity, at least 1 |
| `BuildPart.material`, `process`, `source` | Material; `printed`, `machined`, `cut`, `purchased` or `standard`; `custom`, `standard` or `purchased` |
| `BuildPart.mass_kg` | Mass (kg) |
| `BuildPart.link`, `joint` | URDF link (the part is built in its frame); joint for motors, gearboxes, bearings |
| `BuildPart.spec`, `url`, `files` | Spec for standard parts, e.g. "6805-2RS, 25 x 37 x 7 mm"; product link; BuildFile list |
| `BuildFile.path` | Relative to the build folder, e.g. `parts/link_2.step` |
| `BuildFile.kind` | `step`, `stl`, `urdf`, `pdf`, `zip`, `csv`, `png` or `json` |
| `BuildFile.sha256`, `bytes` | SHA-256 hash; size in bytes |

### 2.7 Sourced values

**ShopPrice.** A price as a shop listed it on a date. An estimate for planning, never a quote.

| Field | Meaning |
|---|---|
| `amount`, `currency` | Listed price (or null); currency, default `INR` |
| `seen` | Date the page was read (ISO date) |
| `shop`, `source_url` | Shop; URL, which must start with `http://` or `https://` |
| `quote` | Verbatim price text from the page |

**SpecValue.** One value of a motor or part.

| Field | Meaning |
|---|---|
| `key`, `value`, `text`, `unit` | Key such as `rated_torque_nm`, `rated_speed_rpm`, `current_a`, `frame`; number; non-numeric text (e.g. "NEMA 23"); unit |
| `status` | `verified`, `assumed` or `defined` (below) |
| `quote`, `source_url`, `note` | Verbatim quote; source page; free note |

- `verified`: quoted on the shop or datasheet page, and the quote contains the number (checked in code).
- `assumed`: from a standard table or rule, for example a NEMA frame size.
- `defined`: specified for procurement because nothing suitable was found.

### 2.8 Twin models

**TwinLimits**: `joints` (list of TwinJointLimit); `gripper` (TwinJointLimit or null, the `gripper` prismatic joint, m);
`dt_s` (default 0.02 s); `accel_ramp_s` (default 0.2 s).

**TwinJointLimit**: `joint`, `kind`; `lower`, `upper` (rad or m); `max_speed` (min of URDF limit and motor rated speed
divided by ratio, joint side); `max_accel`; `max_torque` (motor peak through the drive, joint side).

**TwinTarget**: `joints` (dict, rad or m); `cartesian` (Vec3, tool centre point, world frame, tool down); `gripper` (m).

**TwinMoveRequest**: `q0` (current joint positions, gripper included when present); `target` (TwinTarget);
`speed_scale` (greater than 0, at most 2.0, default 1.0; above 1 the limits bind).

**TwinMoveResult**: `frames` (at `dt_s`, at most 1500); `reached`; `duration_s`; `limits` (list of TwinLimitHit);
`torque_use` (peak required over available torque per joint; 1.0 = at limit); `ik_error_m` (cartesian targets only).

**TwinLimitHit**: `t` (s); `joint`; `limit` (`position`, `speed`, `acceleration`, `torque` or `ik`); `detail` (max 300 chars).

---

## 3. Motor sizing

Code only (`server/studio/robot/sizing.py`). No model call.

### 3.1 Constants

| Name | Value |
|---|---|
| `SF_CONTINUOUS` | 1.25 |
| `SF_PEAK` | 1.5 |
| `ACCEL_RAMP_S` | 0.2 s |
| `SPEED_USE_MAX` | 0.8 (motor speed at most 80 % of rated) |
| `INERTIA_RATIO_MAX` | stepper 5; servo 10; bldc 10; dc_gear 10; other 5 |

### 3.2 Loads per joint

| Figure | Rule |
|---|---|
| Static torque | Worst `gravity_torques` over the task's sim frames and a stretched pose, with payload |
| Acceleration torque | (I_link + m r^2) times alpha; alpha from the design's peak joint speed over `ACCEL_RAMP_S` (alpha = peak speed / 0.2 s) |
| Continuous | RMS over the sim frames, times 1.25 |
| Peak | Worst instant, times 1.5 |
| Speed | The design's peak joint speed |

`extra_masses` (motor and gearbox masses per link) feed back on each iteration (3.4).

### 3.3 Gearing

| Quantity | Rule |
|---|---|
| Motor torque | tau_m = tau / (N * eta) |
| Motor speed | omega_m = omega * N, at most 0.8 times rated speed |
| Inertia ratio | At most 5 for steppers, 10 for servos (and bldc, dc_gear), 5 for other. Taken as load over motor rotor inertia, both at the motor shaft (the usual convention; the sources do not define it). |

Standard ratios: planetary 5, 10, 20, 50; harmonic 50, 100; belt 3:1; leadscrew lead 2, 5, 8 or 10 mm. Rack and wheel
gearbox ratios come from the design. Gearbox efficiencies are set in code.

### 3.4 Mass feedback

Motor and gearbox masses are added to link masses and sizing runs again. `iterate_sizing` runs up to 3 rounds (the plan
says 2 to 3) and stops when the chosen motors stop changing.

### 3.5 Per-family rules

| Family | Rule |
|---|---|
| `serial_arm` | J1 to J3: NEMA 23 or 34 steppers, or BLDC, with planetary or harmonic gearing. Wrist: NEMA 17 or small servos. |
| `gantry` | X and Y: NEMA 23 on belts. Z: NEMA 23 on a leadscrew, with a Z holding check. |
| `mobile_base` | Wheel torque and speed from the existing locomotion formulas. Geared DC or BLDC. Battery check. |

### 3.6 Electronics rules

| Item | Rule |
|---|---|
| Driver | Current at least 1.2 times motor current; one per motor |
| Controller | Axis count and interfaces match the motors |
| Power supply | Power W at least 1.3 times the sum of V * I_rms over the motors |
| Battery | Required for `mobile_base` |

These are `defined` items by default. A shop search for electronics is optional (`sourcing.search_electronics`).

### 3.7 Evaluating a candidate

`evaluate(alt, sizing, drive, motor_kind)` returns `(passes, margins, checks, reason)`. It uses only `verified` or
`assumed` values; a missing critical value fails with a reason. Margins: continuous, peak, speed (at most 0.8 of rated),
inertia ratio.

Stepper speed. Shop pages for steppers usually state holding torque but no rated speed. For `motor_kind == 'stepper'`
only, when no verified rated (or derated no-load) speed exists, `evaluate` uses a conservative usable speed from
`STEPPER_USABLE_RPM` for the motor's frame (verified frame text, else the frame value, else the NEMA_TABLE frame matching
the flange, else unknown). The value is added to the motor's `values` as `rated_speed_rpm`, status `assumed`, note
"conservative stepper usable speed at 24 V; the page states no rated speed". The speed check detail and the `reason`
say "speed assumed (conservative)". It is a planning assumption for a hybrid stepper on a chopper driver at 24 V where
about 70 % of the holding torque is still available, not a manufacturer value.

| NEMA frame | 8 | 11 | 14 | 17 | 23 | 34 | unknown |
|---|---|---|---|---|---|---|---|
| Usable speed (rpm) | 300 | 300 | 300 | 300 | 300 | 200 | 200 |

Torque and current (and voltage) must still be verified; servos, BLDC and DC motors still need a verified speed.

---

## 4. Sourcing rules

An LLM step with web access (role `search`), followed by code checks. It follows `catalog/search.py`. Page text is data.

### 4.1 Shop allowlist

A URL matches when its host ends with a listed domain. Other URLs are dropped.

| Domain | Shop |
|---|---|
| `robu.in` | Robu.in |
| `robokits.co.in` | Robokits India |
| `evelta.com` | Evelta |
| `amazon.in` | Amazon.in |
| `omc-stepperonline.com` | StepperOnline |
| `pololu.com` | Pololu |
| `servocity.com` | ServoCity |
| `aliexpress.com` | AliExpress |

### 4.2 Search and code checks

- One search call per joint class; joints with similar needs are grouped. Input per need: `{joint, kind, motor_side,
  family, hint}`, plus the search date.
- Each value needed for sizing (torque, current, voltage, rpm, frame, length, shaft, mass, gear ratio) needs a verbatim
  quote, and each price needs a link.
- Code checks the quote: it must appear in the fetched page text and contain the number (`extract.verify` pattern).
- Code checks the price with `price_problem`.
- Missing NEMA frame dimensions are filled from `NEMA_TABLE` and marked `assumed`. The table holds documented values
  (flange size in mm, bolt circle, pilot, shaft diameter, typical length and mass by stack).
- A motor is accepted only when its sizing values are `verified` and its margins pass. The one exception is a stepper's
  speed: when the page states none, the conservative `STEPPER_USABLE_RPM` value is used and marked `assumed` (section 3.7). Frame dimensions needed for CAD
  may be `assumed`.
- A model's answer is a proposal until code has checked it.

### 4.3 Prices

A price is a list price, from a named shop, seen on a date, with its source link. It is shown as "seen <date>" and is
never shown as a quote. Prices are not re-checked after approval.

### 4.4 Selection

`select(sizing, options, family, pinned)` evaluates every alternative with every drive option, in code, per joint:

1. **Pinned.** A pinned alternative is used even if it fails. Its failing checks stay visible.
2. **Cheapest passing.** Otherwise, the cheapest passing alternative.
3. **Defined spec.** Otherwise, `defined_motor`: required torque, speed, voltage, frame from the rules, shaft and gear
   ratio, all `defined`. The PDF says "procure to this spec".

---

## 5. Structure style

Either `extrusion_printed` (aluminium extrusion with 3D-printed parts) or `machined_aluminium`.

1. **Proposal.** The style agent (role `robot`) returns `{style, reasons}`. A proposal only.
2. **Code checks** (`style_checks(version, style, motors)`, per link):

| Check | Rule |
|---|---|
| Bending stress | Beam formula, per material |
| Tip deflection | Beam formula, per material |
| Print envelope | About 220 mm |
| Printed-part loads | Load limit for printed parts |

Materials: PLA and PETG for printed parts; 6061 for extrusion and plate. The numeric limits are in code.

3. **Decision.** Any failing check forces `machined_aluminium` and sets `forced` to true. The proposal stays in `proposed`.

---

## 6. CAD

Parametric OpenCascade through `cadquery-ocp` (pinned in `requirements.txt`, imported lazily). `cad.available()` reports
whether OCP is present.

### 6.1 Kernel and parts

`kernel.py`: box, cylinder, tube, extrusion profile (2020, 2040 T-slot), cut, fuse, bolt pattern, `place(shape, 4x4)`,
`valid` (BRepCheck_Analyzer and volume above 0), `volume`, `bbox`, `write_step` (XCAF names), `write_stl(shape, path,
linear_tol=0.0005, angular_tol=0.3)` (metres).

`parts.py` builders return `CadPart(name, shape, material, process, mass_kg, link, joint, source, spec)`:
`link_tube`, `extrusion`, `motor_mount` (NEMA or flange pattern from the motor), `bracket_l`, `shaft`, `bearing_seat`,
`base_plate`, `gripper_parallel` (fingers on the prismatic `gripper` joint), `suction_cup`, `gantry_carriage`.
Standard parts (bearings by shaft, bolts by pattern, belts and pulleys) come from rule tables with generic specs.

### 6.2 Frames

Every part is built in its URDF link frame: joint axis at the link origin, +z along the link (`families/urdf.py`). Meshes
therefore move with the joints by construction. `robot/urdf_mesh.py` reuses the family joint layout, replaces primitives
with `meshes/<link>.stl`, and adds the `gripper` joint.

### 6.3 Files and checks

| Path | Content |
|---|---|
| `assembly.step` | Assembly with part names (XCAF `STEPCAFControl_Writer`) |
| `parts/<id>.step`, `parts/<id>.stl` | One per part; STL via `BRepMesh` with fixed tolerances, metres |
| `urdf/robot.urdf`, `urdf/meshes/<link>.stl` | Studio URDF; one merged mesh per link |

Checks: every shape is valid; each link's CAD bounding box matches the joint chain at q = 0 (tolerance in code); each
motor shaft axis lies on its joint axis.

Output is deterministic: no randomness, rounded inputs. A progress line is written at least every 10 s.

The client's `RobotGeometry` loads the URDF mesh STLs instead of the placeholder shapes.

Planned tests (not run): read each STEP back with the OCP pattern in `RoboDE-Testbed/server/testbed/ingest/cad.py`;
check part counts, volumes and bounding boxes against the joint chain; check that the URDF re-parses with mesh links.

---

## 7. Digital twin

### 7.1 Shared rules

| Rule | Python | TypeScript |
|---|---|---|
| DLS inverse kinematics, tool down, joint clamp | Port of `families/ik.py` | `ikSolve` in `packages/core/src/twin.ts` |
| Step with limits | `clamp_step`: joint limits; speed at most min(URDF, motor rpm / N); acceleration ramps; torque-use estimate | `clampStep` |
| Torque use | `gravity_torques` plus inertia, against `max_torque` | `torqueUse` |
| Timing | `dt_s` = 0.02 s; ramp 0.2 s | same |

Python: `server/studio/robot/twin.py` (`twin_limits`, `move`, `run_task`, `clamp_step`).

### 7.2 Behaviour

- `move` solves a cartesian target with IK, then steps towards it with `clamp_step`. Each limit that binds is reported
  as a `TwinLimitHit`, with the first instants.
- `speed_scale` above 1 asks for more than the limits allow. The limits bind and are reported.
- `run_task` replays the design's waypoints (`sim/task.waypoints`) under the build's limits.

### 7.3 Parity and UI

- `contracts/twin_vectors.json` is generated by a Python script. A TypeScript test checks `ikSolve`, `clampStep` and
  `torqueUse` against it within a tolerance (set in code). It runs in `npm test` or `contracts:check`.
- `client/src/stages/Studio.tsx` and `components/twin/*`: joint sliders and jog at a fixed 20 ms step with ramps and a rate
  limit; tool XYZ and jog (IK); gripper; "Run the task" (`/twin/run`); Reset; readouts with "limited by" chips; the robot
  with its meshes in `SceneView` with the design's scene.

`/twin/move` and `/twin/run` are stateless and deterministic, so other applications can drive the twin and receive the
frames. There is no hardware bridge in v1.

---

## 8. Manufacturer PDF and package

`server/studio/robot/report.py`: reportlab, and matplotlib with the Agg backend. Produced by job `studio_pdf` after
approval. No model call.

| # | Section | Contents |
|---|---|---|
| 1 | Cover | Project, build, date, approved by the user |
| 2 | Task and requirements | The task and the requirements met |
| 3 | Renders | The four snapshots; if absent, matplotlib orthographic views from the URDF link frames, with overall dimensions |
| 4 | Bill of materials | Item, name, qty, material, process, mass, file |
| 5 | Motors, gearboxes, drivers, controller, power supply | Model, shop link, price "seen <date>", key specs marked verified or assumed, required against provided, margin |
| 6 | Defined-spec items | Each marked "procure to this spec" |
| 7 | Standard parts | Bearings, bolts, belts, pulleys, with specs |
| 8 | File list | Every file with its SHA-256 |
| 9 | Assumptions and limits | Kinematic, static and acceleration sizing only; no FEA; no dynamics; not tested on hardware; prices are dated web estimates |

`write_bom_csv` writes `bom.csv`. `write_package` writes `package.zip`: `manufacturer.pdf`, `assembly.step`, `parts/`,
`urdf/` and `bom.csv`. Planned test (not run): pypdf reads the PDF back and checks part names, links and prices.

---

## 9. HTTP API, tasks and storage

### 9.1 Routes

Base path `/api/v1/projects/{pid}/studio`, served by Java `StudioService`.

| Method | Path | Body | Response | Errors |
|---|---|---|---|---|
| POST | `/builds` | `{design_version?, base_build?, request?, pinned?: {joint: alternative_id}}` | Job `studio_build` with wait, expand, callback and idempotency. Result `{build: n}`. | 409 unless the design version is approved (default: the approved one); 409 while another `studio_build` runs for the project |
| GET | `/builds` | | `[{build, design_version, member, parent_build, created, summary, approved}]` | Not stated |
| GET | `/builds/{n}` | | `StudioBuild` plus `parsed` (studio URDF, mesh paths relative to the build) | Not stated |
| GET | `/builds/{n}/files/{path}` | | File in the build folder; STEP as `model/step`, STL as `model/stl` | Unsafe paths rejected (status not stated) |
| POST | `/builds/{n}/twin/move` | `TwinMoveRequest` | `TwinMoveResult` (synchronous) | Not stated |
| POST | `/builds/{n}/twin/run` | `{speed_scale?}` | `TwinMoveResult` (synchronous) | Not stated |
| POST | `/builds/{n}/snapshots?view=iso\|front\|side\|top` | Raw PNG | `{views: [...]}` | 409 when approved |
| POST | `/builds/{n}/approve` | | Job `studio_pdf`. Result `{build: n}`. | 409 if already approved |
| GET | `/builds/{n}/pdf` | | `manufacturer.pdf` | 404 until approved |
| GET | `/builds/{n}/package` | | `package.zip` | 404 until approved |

The project view gains `studio: {builds: [n], approved_build: n | null}`, only when a build exists. The decision action
`robot_studio` is unchanged; the Decision page links to the new stage.

### 9.2 Tasks

Each runs as `python -m studio.tasks <task>`: request on stdin, JSON lines on stdout (`docs/PROTOCOL.md`).

| Task | Input | Output | Type |
|---|---|---|---|
| `studio_build` | `{version, member, base_build, request, pinned, build}` | `{build, parsed}`; files in out_dir (`assembly.step`, `parts/`, `urdf/`) | Job; LLM (`search` role with web, `robot` role for style) |
| `studio_twin_move` | `{build, request: TwinMoveRequest}` | `TwinMoveResult` | Synchronous; no LLM; deterministic |
| `studio_run_task` | `{build, version, speed_scale}` | `TwinMoveResult` | Synchronous; no LLM |
| `studio_pdf` | `{build, version, project: {id, title, brief}, build_dir, snapshots: [{view, path}]}` | `{pdf, package, files: [BuildFile]}` in out_dir | Job; no LLM |

With `base_build` and only `pinned` changed, the shop search is not repeated; alternatives come from the base build.

### 9.3 Storage

`runtime/projects/<id>/studio/b<n>/`: `build.json`, `parsed.json`, `assembly.step`, `parts/`, `urdf/robot.urdf`,
`urdf/meshes/`, `snapshots/<view>.png`, `manufacturer.pdf`, `package.zip`, `bom.csv`.

Java writes these files. Python writes only inside the job's `out_dir`.

---

## 10. What the numbers are and are not

**They are:**
- kinematic results: frames, inverse kinematics, the twin's joint, speed and torque limits;
- static loads from `gravity_torques`, with payload, over the task and a stretched pose;
- an acceleration estimate, (I + m r^2) times alpha over a 0.2 s ramp;
- safety factors (1.25 continuous, 1.5 peak) and gearing arithmetic;
- beam checks for links, with textbook beam formulas;
- electronics ratios (1.2 times current, 1.3 times power);
- motor margins against catalog values that are quoted and verified in code.

**They are not:**
- FEA. The only structural check is the beam formulas.
- Dynamics or contact simulation. The twin is a kinematic and limit model.
- Hardware tests. Nothing has been run on a physical robot.
- Quotes. Prices are dated web estimates, not re-checked after approval.
- Proof. A manufacturer value counts only with a verbatim quote verified in code. A model's answer is a proposal until
  code checks it. A "defined spec" item has no verified product behind it, and the PDF says so.

**Gaps in the sources** (to settle during implementation or review):

1. **Alternative ids.** The pattern `^[a-z][a-z0-9_]{0,62}$` does not match the planned `<shop>_<slug>` form when the shop
   is a domain such as `omc-stepperonline.com`. The normalisation is not stated.
2. **Verified or assumed.** The plan says critical values must be verified; the interfaces note allows verified or
   assumed. This document uses: sizing values verified; CAD frame dimensions may be assumed.
3. **Currency.** `ShopPrice.currency` defaults to `INR`. How "cheapest" compares different currencies is not stated.
4. **Numeric limits in code.** Bending and deflection limits, printed-part load limit, CAD bounding-box tolerance,
   parity tolerance, gearbox efficiencies.
5. **Error codes.** Some GET routes and unsafe file paths have no status in the sources.
6. **Check location.** The `schemas.py` comment cites `robot/checks`; the plan puts checks in `sizing.py` and `style.py`.

---

## 11. Not in v1

- No hardware bridge and no generated driver. No dynamics or contact simulation.
- No FEA beyond beam checks. No fillets or threads. No wiring harness.
- No ordering. No live re-pricing after approval.
- No mobile-manipulator builds. No whole-fleet builds (a fleet builds one member).
