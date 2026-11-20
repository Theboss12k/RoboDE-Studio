# RoboDE Studio — module contracts (for contributors)

Data types: `server/studio/schemas.py`. Family parameter models: `server/studio/families/params.py`.
Family protocol: `server/studio/families/base.py`. Statics helper: `server/studio/physics/statics.py`.
Python 3.12+, numpy, pydantic 2. Tests: `npm test` (pytest, `tests/`, no network, scripted LLMs only).
Imports inside the package are relative (`from ..schemas import ...`); tests import `studio.*`.

## Flow
brief (+ optional reference) -> `spec` agent asks questions (<= 6 per round, <= 3 rounds) -> `TechnicalSpec`
-> `physics.place_base(spec)` -> `physics.derive(spec, base)` -> `list[Requirement]`
-> `asyncio.gather(scene agent, robot agent)` -> `SceneSpec`, `RobotConcept(family, hint params)`
-> `family.size_from_physics(requirements, spec, hint)` -> params -> `build_urdf`
-> `sim.simulate(...)` -> `SimResult` (frames + checks) -> `DesignVersion` v1.
`revise` agent: change request -> patch of spec/scene/robot -> re-run affected steps -> v(n+1). v(n) is kept.
Approve -> `catalog.search` (web) -> `Candidate[]` + code screen -> select -> download datasheet -> `extract`
(quotes verified) -> `catalog.model.candidate_concept` -> simulate same task -> `catalog.compare.differences` ->
`analysis` agent writes impact text -> decision.

## physics/ (numpy only, no LLM)
Every requirement carries a `Calc` (formula text, named inputs with units, result, unit).
- `requirements.place_base(spec) -> BasePose`: fixed robots: xy = centre of the smallest enclosing circle of the
  task markers' xy, z = 0 (floor); yaw facing the first pick marker. Mobile (`spec.locomotion.required`): first
  path point (or first marker), yaw toward the second path point.
- `requirements.derive(spec, base) -> list[Requirement]` ids (stable, the client and tests use them):
  `reach_max` (>=, m): max Euclidean distance from the base mounting point to each task point (marker, and the
  same point raised by approach_height_m), + clearance_m;
  for mobile tasks reach is per stop (distance from the stopped base to the point), `reach_min` (info),
  `z_max`/`z_min` (info, m), `payload` (>=, kg: (object + tool) * SF; tool estimated as 0.3 + 0.15*object mass
  when None, capped at 3 kg for <= 10 kg objects), `grip_force` (>=, N: m (g + a) / (n mu) * SF; vacuum/magnetic:
  m (g + a) * SF; none/fork/hook: info), `tcp_speed` (>=, m/s) and `tcp_accel` (>=, m/s^2) from a trapezoid move
  over the longest single move in the cycle in the time budget (cycle_time_s split over the moves by distance,
  dwell excluded, accel phase = 1/3 of each move unless peak_acceleration_m_s2 given), `cycle_time` (<=, s),
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

## sim/
- `sim.task.waypoints(spec, scene, base) -> list[Waypoint]` (dataclass: name, world xyz, kind
  'approach'|'grasp'|'lift'|'carry'|'place'|'release'|'retreat'|'home'|'drive', object id, phase label).
  pick at marker M: approach (M + approach_height up), grasp (M + object half height), lift (approach). place
  similarly; carry between at max(approach heights). Grasp point = object top centre for vacuum, centre for parallel.
- `sim.task.simulate(spec, requirements, scene, concept, params=None, dt=0.04) -> SimResult`: IK per waypoint
  (seeded by the previous), joint-space interpolation with per-joint trapezoid limits (speed/accel from joints),
  mobile bases drive the path with `motion.sample`, wheel joints animated. Frames: `Frame(t, joints by URDF name,
  base, holding, phase)`; at most ~1500 frames (decimate). Checks (`Check` per requirement id + extras):
  reach per waypoint (`reach_<waypoint>` collapsed into `reach_max` with detail), joint limits, `joint_torque_<j>`
  (static gravity torque incl. payload at worst waypoint vs joint max_effort), joint speed, cycle time, payload,
  grip force, accuracy (repeatability), coarse collisions (capsules of `chain_points` vs scene boxes with
  `collides`, skipping the box the object rests on during grasp/release within 2 cm), locomotion checks.
  `ok` = all checks with passed is not None are True.
- `sim.collision`: capsule-vs-oriented-box distance (numpy).
- `sim.report.check(requirement, actual, calc=None, detail='') -> Check` implements the comparator.

## catalog/
- `catalog.fetch.safe_download(url, max_bytes=25_000_000, timeout_s=30) -> Downloaded(bytes, content_type,
  final_url, filename)`: https only, hostname resolved and every address rejected if private, loopback,
  link-local, multicast, reserved or unspecified (IPv4 and IPv6), redirects followed manually (<= 5) and each
  re-checked, size cap enforced while streaming, only PDF/HTML/text content types. `UnsafeURL(ValueError)`.
- `catalog.search.search(adapter, spec, requirements, concept, limit=6) -> list[Candidate]`: adapter.with_web();
  untrusted-page rules; a price only with source URL + verbatim quote + date; validated with pydantic; ids slugged
  and unique. `catalog.search.screen(candidate, requirements) -> list[Check]` (payload, reach; unparseable -> None).
- `catalog.extract.extract(adapter, candidate, text, url) -> ManufacturerSpec`: the model returns values with
  verbatim quotes; code sets `verified` only if `ingest.docs.contains_quote(text, quote)` and the number (in the
  stated unit, converted via `units`) appears in the quote. Unverified values are kept, flagged.
- `catalog.model.candidate_concept(mspec, generated: RobotConcept, spec) -> (RobotConcept, unverified: list[str])`:
  maps verified values (payload_kg, reach_m, dof, link lengths, joint ranges/speeds, repeatability, mass, strokes,
  speeds) onto the family params; missing values are scaled from the generated robot by reach ratio and listed.
- `catalog.compare.differences(generated: DesignVersion, candidate_sim: SimResult, candidate: RobotConcept) ->
  list[Difference]` (severity from checks: failed requirement -> blocking/major, within 10 % margin -> minor).

## agents/ (one module per role: SYSTEM prompt, output schema, validator, `async def run(adapter, ...)`)
`spec.run(adapter, brief, reference, answers, round) -> {'questions': [Question]} | {'spec': TechnicalSpec}`;
`scene.run(adapter, spec, base) -> SceneSpec`; `robot.run(adapter, spec, requirements, families) -> RobotConcept`;
`revise.run(adapter, version, request) -> Patch{targets, spec?, scene?, robot?, summary}`;
`search` / `extract` live in catalog; `analysis.run(adapter, differences, spec) -> list[Difference]` (impact text
only; numbers unchanged — validator rejects changed generated/candidate strings).
All use `llm.base.ask_json` with pydantic validation and one repair turn.

## API (FastAPI, 127.0.0.1:8800; long work = job: POST returns `Job`, poll `GET /api/jobs/{id}`)
- `GET /api/health`, `GET /api/families`, LLM settings: `GET /api/llm`, `PUT /api/llm/profiles/{id}`,
  `POST /api/llm/profiles`, `DELETE /api/llm/profiles/{id}`, `PUT /api/llm/roles`, `PUT/DELETE /api/llm/keys/{kind}`,
  `GET /api/usage`.
- `GET /api/projects`, `POST /api/projects {brief}`, `GET /api/projects/{id}`, `DELETE /api/projects/{id}`.
- `PUT /api/projects/{id}/reference {text}`, `POST /api/projects/{id}/reference/files?filename=` (raw body: image or
  .urdf/.zip), `DELETE /api/projects/{id}/reference`.
- `POST /api/projects/{id}/design {answers: {}}` -> job result `{status: 'questions', questions}` or
  `{status: 'designed', version: n}`.
- `GET /api/projects/{id}/versions/{n}` -> `{...DesignVersion, parsed: parse_urdf(urdf)}`.
- `POST /api/projects/{id}/revise {request}` -> job `{version: n}`. `POST /api/projects/{id}/approve {version}`.
- `POST /api/projects/{id}/search` -> job `{candidates}`; `GET /api/projects/{id}/candidates`.
- `POST /api/projects/{id}/candidates/{cid}/analyse {datasheet_url?}` -> job `Analysis`;
  `POST /api/projects/{id}/candidates/{cid}/datasheet?filename=` (raw body upload when download fails);
  `GET /api/projects/{id}/analysis/{cid}` -> `{...Analysis, parsed}`.
- `POST /api/projects/{id}/decision {candidate_id, action: 'approve'|'robot_studio'}`;
  `GET /api/projects/{id}/export` -> ZIP (design URDF + GLB, spec.json, requirements.json, sim summary, analysis).
