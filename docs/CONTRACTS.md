# RoboDE Studio — module contracts (for contributors)

Data types: `server/studio/schemas.py`. Family parameter models: `server/studio/families/params.py`.
Family protocol: `server/studio/families/base.py`. Statics helper: `server/studio/physics/statics.py`.
Python 3.12+, numpy, pydantic 2 (task subprocesses). Java 25+ (`server-java/`): HTTP, jobs, store, settings and usage;
the Java <-> Python wire format is in [PROTOCOL.md](PROTOCOL.md). Tests: `npm test` runs the Java tests
(`./mvnw verify`), pytest (`tests/`; scripted LLMs, no network by default) and the contracts check. Opt-in tests:
`STUDIO_CLAUDE=1` runs `test_real.py` against the real Claude CLI; `STUDIO_WEB=1` is declared for real web tests but no
test reads it yet.
Imports inside the package are relative (`from ..schemas import ...`); tests import `studio.*`.

## Flow
brief (+ optional reference) -> `spec` agent asks questions (<= 6 per round, <= 3 rounds) -> `TechnicalSpec`
-> `physics.place_base(spec)` -> `physics.derive(spec, base)` -> `list[Requirement]`
-> `asyncio.gather(scene agent, robot agent, objects agent)` -> `SceneSpec`, `RobotConcept(family, hint params)`
-> `family.size_from_physics(requirements, spec, hint)` -> params -> `build_urdf`
-> `sim.simulate(...)` -> `SimResult` (frames + checks) -> `DesignVersion` v1.
`revise` agent: change request -> patch of spec/scene/robot -> re-run affected steps -> v(n+1). v(n) is kept.
Approve -> `catalog.search` (web) -> `Candidate[]` + code screen -> select -> download datasheet -> `extract`
(quotes verified) -> `catalog.model.candidate_concept` -> simulate same task -> `catalog.compare.differences` ->
`analysis` agent writes impact text -> decision.

## physics/ (numpy only, no LLM)
Every requirement carries a `Calc` (formula text, named inputs with units, result, unit).
- `requirements.place_base(spec) -> BasePose`: fixed robots: xy = centre of the smallest enclosing circle of the
  task markers' xy, z = 0 (floor); yaw facing the first pick marker. A marker with id `robot_base` overrides the
  mounting point (its position, z included; e.g. an arm on a table); the yaw still faces the first pick. Mobile (`spec.locomotion.required`): first
  path point (with no path: the first task point), yaw toward the second path point.
- `requirements.derive(spec, base) -> list[Requirement]` ids (stable, the client and tests use them):
  `reach_max` (>=, m): max Euclidean distance from the base mounting point to each task point (marker, and the
  same point raised by approach_height_m), + clearance_m;
  for mobile tasks reach is per stop (distance from the stopped base to the point), `reach_min` (info),
  `z_max`/`z_min` (info, m), `payload` (>=, kg: (object + tool) * SF; tool estimated as 0.3 + 0.15*object mass
  when None, capped at 3 kg for <= 10 kg objects), `grip_force` (>=, N: m (g + a) / (n mu) * SF; vacuum/magnetic:
  m (g + a) * SF; none/fork/hook: info), `tcp_speed` (>=, m/s) and `tcp_accel` (>=, m/s^2) from a trapezoid move
  over the longest single move in the cycle in the time budget (cycle_time_s split over the moves by distance,
  dwell excluded: only `wait`/`inspect`/`press` durations are dwell, capped at 50 % of the cycle (the Calc note says
  so when the cap applies); accel phase = 1/3 of each move unless peak_acceleration_m_s2 given), `cycle_time` (<=, s),
  `accuracy` (<=, m, repeatability needed = positional_accuracy_m / 2), and when locomotion is required:
  `travel_distance` (info, m), `drive_speed` (>=, m/s), `drive_accel` (>=), `wheel_torque` (>=, N*m per drive
  wheel: (m_total (a + g sin(theta) + g Crr cos(theta)) * r / n_wheels) * SF), `slope` (>=, deg), `battery` (>=,
  Wh per shift: traction energy / efficiency 0.7 + 15 % hotel load, times cycles).
- `motion.trapezoid(distance, v_max, a_max) -> dict(t_total, t_accel, v_peak, triangular)` and
  `motion.required_for(distance, time, accel_fraction=1/3) -> dict(v_peak, a)`; `motion.sample(distance, v_max,
  a_max, dt) -> np.ndarray` of positions (0..distance) used by the simulator.
- `grip.force(...)`, `locomotion.*` helpers; `statics.py` as given.

## families/ (numpy)
`REGISTRY: dict[str, Family]` in `families/__init__.py` with `serial_arm`, `gantry`, `mobile_base`,
`mobile_manipulator`; `get(name)`; `describe() -> [{name, title, description, mobile, params_schema}]`.
- IK: `families/ik.py` damped least squares (lambda ~0.05), joint-limit clamping, several seeds (current, zero,
  random within limits, fixed seed RNG), tolerance 1 mm, orientation tool-down constraint for arms (dof >= 4).
  Analytic shortcuts allowed (gantry is closed form).
- URDF: `families/urdf.py` writer helpers (links with box/cylinder visual + collision + inertial, joints with limits,
  materials with colours). Output must re-parse with `studio.ingest.urdf.parse_urdf` and joint names must match
  `joints()`. `families/export.py`: `meshes(urdf_xml) -> bytes` GLB of the zero pose via trimesh (and STL).
- `size_from_physics`: serial arm: upper_arm + forearm + wrist + tool chosen so reach_max <= 0.85 * max reach,
  base_height near the task height; torques = gravity_torques at the worst waypoint/stretched pose * SF rounded
  up; gripper_force_n >= grip_force; payload_kg >= payload. Gantry: strokes cover the waypoints' xy span + margins,
  frame height above the tallest point + approach. Mobile base: speed/accel/torque/battery from requirements.
  Rated speeds and accelerations are capped at plausible family limits (serial arm TCP 4 m/s; its acceleration is speed / 0.25 s, so the effective cap is 16 m/s^2 (`ACCEL_TIME_S` in
  `families/serial_arm.py`), gantry axis
  5 m/s and 50 m/s^2, mobile base 3 m/s and 3 m/s^2), so an absurd requirement fails its check visibly.

## sim/
- `sim.task.waypoints(spec, scene, base) -> list[Waypoint]` (dataclass: name, world xyz, kind
  'approach'|'grasp'|'lift'|'carry'|'place'|'release'|'retreat'|'home'|'drive'|'wait', object id, phase label).
  pick at marker M: approach (M + approach_height up), grasp (M + object half height), lift (approach). place
  similarly; carry between at max(approach heights). A `move` goes to carry height above its marker (marker z + object
  height + approach height), never to the surface; it holds the object only after a pick. A press/push dwells
  `duration_s` at the surface. Grasp point = object top centre for vacuum, centre for parallel.
- `sim.task.simulate(spec, requirements, scene, concept, params=None, dt=0.04) -> SimResult`: IK per waypoint
  (seeded by the previous), joint-space interpolation with per-joint trapezoid limits (speed/accel from joints),
  mobile bases drive the path with `motion.sample`, wheel joints animated. Frames: `Frame(t, joints by URDF name,
  base, holding, phase)`; at most ~1500 frames (decimate). Checks (`Check` per requirement id + extras):
  reach per waypoint (`reach_<waypoint>` collapsed into `reach_max` with detail), joint limits, `joint_torque_<j>`
  (static gravity torque incl. payload at worst waypoint vs joint max_effort), joint speed, cycle time, payload,
  grip force, accuracy (repeatability), coarse collisions (capsules of `chain_points` vs scene boxes with
  `collides`, skipping the box the object rests on during grasp/release within 2 cm), locomotion checks.
  When the fastest feasible run is shorter than `cycle_time_s`, the motion is stretched uniformly (joint speeds / k,
  accelerations / k^2, dwell unchanged) so the run fills the cycle; the `cycle_time` check detail reports the fastest
  feasible time. A longer run is kept and the check fails.
  `ok` = all checks with passed is not None are True.
- `sim.collision`: capsule-vs-oriented-box distance (numpy).
- `sim.report.check(requirement, actual, calc=None, detail='') -> Check` implements the comparator.

## catalog/
- `catalog.fetch.safe_download(url, max_bytes=25_000_000, timeout_s=30) -> Downloaded(bytes, content_type,
  final_url, filename)`: https only, hostname resolved and every address rejected if private, loopback,
  link-local, multicast, reserved or unspecified (IPv4 and IPv6), redirects followed manually (<= 5) and each
  re-checked, size cap enforced while streaming, only PDF/HTML/text content types. `UnsafeURL(ValueError)`.
- `catalog.search.search(adapter, spec, requirements, concept, limit=6, today=None, dropped=None) -> list[Candidate]`:
  adapter.with_web(); untrusted-page rules; the prompt gets today's date (UTC default), the requirement targets
  (payload, reach_max, tcp_speed, accuracy, environment), the sizing rule (smallest robots that meet every
  requirement with margin: payload ~1-5x, reach ~1-1.6x; best fit first; a cheaper and a more capable option) and the
  family definitions (SCARA = `other`). A price is kept only with source URL + verbatim quote + a low/high figure that
  appears in the quote (thousands separators and currency symbols tolerated); a missing date becomes
  `seen YYYY-MM-DD` (today). Otherwise the price is null and the reason is counted in `dropped` (a Counter; the job
  reports "N prices dropped: ..."). Validated with pydantic; ids slugged and unique.
  `catalog.search.screen(candidate, requirements) -> list[Check]` (payload, reach; unparseable -> None).
- `catalog.search.fill_images(candidates, download=safe_download)`: for candidates without `image_url`, fetch the product
  page (HTML, <= 3 MB, 10 s, 3 at a time) and take og:image / twitter:image, resolved to an absolute https URL.
  Linked, never stored; failures are ignored. `pipeline.catalog.find` runs it after the search.
- `pipeline.catalog.analyse` document order: uploaded document, explicit URL, the candidate's `datasheet_url`, then
  (no datasheet link, or the candidate's link failed) the candidate's product page via `safe_download` +
  `ingest.docs.extract_text`. The progress line says which was used and `ManufacturerSpec.document_url` records it.
  Only when none works: `Datasheet download failed: ...`.
- `catalog.extract.extract(adapter, candidate, text, url) -> ManufacturerSpec`: the model returns values with
  verbatim quotes; code sets `verified` only if `ingest.docs.contains_quote(text, quote)` and the number (in the
  stated unit, converted via `units`) appears in the quote. Unverified values are kept, flagged.
- `catalog.model.candidate_concept(mspec, generated: RobotConcept, spec) -> (RobotConcept, unverified: list[str],
  provenance: dict[str, str])`: maps verified values (payload_kg, reach_m, dof, link lengths, joint ranges/speeds,
  repeatability, mass, strokes, speed and acceleration) onto the family params (a verified `max_speed_m_s` sets the
  serial arm's `max_tcp_speed_m_s`, the gantry axis speeds, the mobile base's speed; a serial arm derives TCP
  acceleration from speed, so `max_tcp_accel_m_s2` stays `default`). `provenance` has an entry for every param (mobile
  manipulator: `base.`/`arm.` prefixes) as `'<kind>: <how>'` with kind `verified` (a verified value, with its key),
  `derived` (computed from verified values, e.g. link lengths scaled to the verified reach), `carried` (from the
  generated design: gripper, tool) or `default` (family default or sized, not published). Stored as
  `Analysis.provenance`; `unverified` stays the readable list.
- `catalog.model.qualify_checks(checks, provenance) -> list[Check]`, applied to the candidate's simulation before
  comparing: a check whose robot value depends on a `default` param (joint_torque_* -> max_torque_nm, joint_speed ->
  speeds, tcp_speed/tcp_accel -> TCP speed / acceleration, cycle_time -> joint speeds and accelerations, payload,
  grip_force, accuracy ...) gets `passed=None` and detail `Not published by the manufacturer; <param> assumed <value>`
  (the estimate stays in `actual`; cycle_time's detail gives the estimated time). `carried` gripper checks keep
  pass/fail with "uses the generated design's gripper"; reach, joint limits and collisions that depend on `derived`
  link lengths keep pass/fail with "link lengths estimated from the verified reach". `SimResult.ok` is recomputed
  from the checks with a verdict.
- `catalog.compare.differences(generated: DesignVersion, candidate_sim: SimResult, candidate: RobotConcept,
  provenance=None) -> list[Difference]` (severity from checks: failed requirement -> blocking/major, within 10 % margin
  -> minor). A check without a number renders as `passes`/`fails` + its detail; for the candidate a check with
  `passed=None` renders as `not published (<reason>)`. Requirements with comparator `info` are skipped; the rated
  payload and repeatability rows appear only when no payload/accuracy check row covers them; the torque-rating rows
  are skipped when `max_torque_nm` is `default`.

## agents/ (one module per role: SYSTEM prompt, output schema, validator, `async def run(adapter, ...)`)
`spec.run(adapter, brief, reference, answers, round) -> {'questions': [Question]} | {'spec': TechnicalSpec}`;
`scene.run(adapter, spec, base) -> SceneSpec`; `robot.run(adapter, spec, requirements, families) -> RobotConcept`;
`revise.run(adapter, version, request) -> Patch{targets, spec?, scene?, robot?, summary}`. Base moves: the spec's
`robot_base` marker is the only source of the base position. If a patch moves `robot.base` (> 1 cm) without that marker
(fixed robots only), `base_marker_from_robot` converts it into the marker (spec target added, marker also added to a
patched scene). The scene validator requires, when the base z is above the floor, a box whose top is at that z and
whose footprint contains the base x, y (the mounting surface), and keeps 0.3 m around the base free of other colliding
boxes (0.1 m for tables holding a marker);
`search` / `extract` live in catalog; `analysis.run(adapter, differences, spec) -> list[Difference]` (impact text
only; numbers unchanged — validator rejects changed generated/candidate strings).
All use `llm.base.ask_json` with pydantic validation and one repair turn.

`agents/objects.py`: `run(adapter, objects, summary) -> {object id: ObjectModel}` (role `objects`, Haiku; needs only the spec). `SceneObject` has
optional `name` and `model` (`ObjectModel`: 1..24 `ObjectModelPart` primitives box/cylinder/sphere/cone/capsule/torus in the object's own
frame, origin at the centre of its box, z up); both keys are absent from JSON when empty. Code validates: one model per object, every part
inside the box (2 % tolerance), the parts cover at least 90 % of the box per axis. Failure is never fatal (no model, no progress line); on
success `design` emits `Object models: <n> made`. `revise` reuses the models of objects whose name, size and shape did not change; fleet
versions keep the base version's models. The client draws `model`, else the keyword models (`objectModels.tsx`), else the primitive.

## API (Java, 127.0.0.1:8800, `/api/v1`; long work = job: POST returns `Job`, poll `GET /api/v1/jobs/{id}`)
Full wire protocol between the Java server and the Python tasks: [PROTOCOL.md](PROTOCOL.md). The OpenAPI document is
at `GET /api/v1/openapi.json` (source `contracts/openapi.yaml`, bundled `contracts/openapi.bundled.json`). Errors are `{detail: string}`: 422 value, 404 not found, 409 conflict, 502 model failure, 413
body too large. With a non-loopback `STUDIO_HOST`, `STUDIO_TOKEN` is required and every call needs a bearer token.
- `GET /api/v1/health`, `GET /api/v1/families`, LLM settings: `GET /api/v1/llm`, `PUT /api/v1/llm/profiles/{id}`,
  `POST /api/v1/llm/profiles`, `DELETE /api/v1/llm/profiles/{id}`, `PUT /api/v1/llm/roles`, `PUT/DELETE /api/v1/llm/keys/{kind}`,
  `POST /api/v1/llm/profiles/{id}/test` (one test call), `GET /api/v1/llm/usage?period=` (also `GET /api/v1/usage`) and
  `POST /api/v1/llm/usage/reset`.
- `GET /api/v1/projects`, `POST /api/v1/projects {brief}`, `GET /api/v1/projects/{id}`, `DELETE /api/v1/projects/{id}`.
- `PUT /api/v1/projects/{id}/reference {text}`, `POST /api/v1/projects/{id}/reference/files?filename=` (raw body: image or
  .urdf/.zip), `DELETE /api/v1/projects/{id}/reference`.
- `POST /api/v1/projects/{id}/design {answers: {}}` -> job result `{status: 'questions', questions}` or
  `{status: 'designed', version: n}`.
- `GET /api/v1/projects/{id}/versions/{n}` -> `{...DesignVersion, parsed: parse_urdf(urdf)}`;
  `GET /api/v1/projects/{id}/versions/{n}/assets/{design.urdf|design.glb|frames.json}`.
- `POST /api/v1/projects/{id}/revise {request, base_version, t}` -> job `{version: n}`. `base_version` defaults to the
  latest version; `t` (seconds, 0 to the version's duration, or null) is the moment on screen, and an out-of-range `t`
  is 422. The new version's `parent_version` is `base_version`.
  `POST /api/v1/projects/{id}/approve {version}`. Approval freezes the design on the server:
  after it, `revise` and `approve` of a different version answer 409; approving the same version again is a no-op.
- `POST /api/v1/projects/{id}/search` -> job `{candidates}`; `GET /api/v1/projects/{id}/candidates`.
- `POST /api/v1/projects/{id}/candidates/{cid}/analyse {datasheet_url?}` -> job `Analysis` (a candidate whose family is
  not in `GET /api/v1/families`, e.g. `other`, fails at once with a plain message, before any download or model call);
  `POST /api/v1/projects/{id}/candidates/{cid}/datasheet?filename=` (raw body upload when download fails);
  `GET /api/v1/projects/{id}/analysis/{cid}` -> `{...Analysis, parsed}`.
- `POST /api/v1/projects/{id}/decision {candidate_id, action: 'approve'|'robot_studio'}`;
  `GET /api/v1/projects/{id}/export` -> ZIP: `design.urdf`, `design.glb`, `spec.json`,
  `requirements.json`, `scene.json`, `sim_summary.json`, `README.txt`, `decision.json` (when decided) and per analysed
  candidate `candidates/<id>/analysis.json` (without URDF and frames), `manufacturer_values.json` and `candidate.urdf`.

## Units and frames
- Metres, kilograms, seconds, newtons, newton-metres, watt-hours; angles in radians in code and data, degrees in
  parameters people read and in text.
- World frame: right-handed, z up, origin on the floor. Mobile tasks use the same frame.
- A family works in its base frame: origin at the mounting point, x axis along `BasePose.yaw`.
- The three.js client and GLB assets are y up. The conversion is a rotation of -90 degrees about X
  (`Z_UP` in `packages/core`): `(x, y, z)_world -> (x, z, -y)_three`. GLB assets from the API are already y up.
- `packages/core` (`@robode/studio-core`) is DOM-free: generated types, `createApi`, `linkFrames`, playback helpers.
  See `packages/core/README.md`.
