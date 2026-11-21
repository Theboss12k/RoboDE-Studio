# RoboDE Studio

Start from the job, not from a robot. You describe the task in plain words and answer a few questions. Studio then
generates a rough robot, shows it doing the task in 3D, and lists the requirements the robot must meet, each with
its pass or fail and its working. You change the design in plain words; every change is a new version. When a
version is right you approve it.

After that Studio searches the web for real robots that could do the job, with price estimates that carry a date and
a source. You pick one and Studio analyses it against the manufacturer's own datasheet: the published numbers replace
the generated ones, the same task is simulated again, and the differences are listed. Then you decide.

Studio started from RoboDE Testbed (LLM adapters, URDF and document
intake) and stands alone. It shares no code with the Testbed or the IDE at run time.

## How it works

1. **Brief.** Describe the job: what moves, how heavy it is, where it goes, how often. Typing or dictation.
2. **Reference (optional).** Photos, a URDF or zip, or a description of a robot you already have in mind.
3. **Questions.** The AI turns the brief into a technical specification. It asks only what it cannot read from your
   words: at most six questions a round, at most three rounds. Anything still open becomes a listed assumption.
4. **Design.** Code derives the requirements (reach, payload, grip force, speed, cycle time and so on, each with its
   formula). The AI lays out the scene and picks a robot type; code sizes the robot from the requirements, builds its
   URDF and simulates the task. You see the 3D playback and a table of requirements with pass or fail. You can ask
   for changes in words and compare versions. You approve one version.
5. **Robot options.** The AI searches the web for real, currently sold robots. Each has links, quoted published
   numbers, an image linked from the vendor, and a dated web price estimate when one was found.
6. **Analysis.** For one robot, Studio reads its datasheet (or product page), keeps only values whose quotes are found
   in the document, builds a model from them, simulates the same task and lists the differences.
7. **Decision.** Approve a robot, or mark it for Robot studio (a placeholder today). Download the design package.

The numbers that decide pass or fail come from code. The AI proposes the task layout, the robot type and the
wording; code checks and calculates.

Documentation:
- [docs/STUDIO.md](docs/STUDIO.md): user guide.
- [docs/STUDIO_TECHNICAL.md](docs/STUDIO_TECHNICAL.md): architecture, formulas, storage, API, limits.
- [docs/CONTRACTS.md](docs/CONTRACTS.md): module contracts for contributors.
- [docs/PROJECT_TIMELINE.md](docs/PROJECT_TIMELINE.md): dated record of changes and how each was checked.

## Run it

Requirements:
- Node 22 or later;
- JDK 25 or later (the Maven wrapper `server-java/mvnw` is included; no Maven install needed);
- Python 3.12 or later (runs the AI and numeric tasks);
- a Gemini API key (this branch's default), or the Gemini CLI signed in with your Google account; the Claude Code CLI adapter is still in the code but is not a default on this branch.

```bash
npm install
npm run setup    # creates .venv, installs requirements.txt, builds the Java server (server-java/target)
npm run dev      # Java API on 127.0.0.1:8800, UI on http://127.0.0.1:5190
npm test         # Java tests, pytest, contracts check (fixture tests; no network, no model calls)
```

| Command | What it does |
|---|---|
| `npm run api` | Java API only. It serves the built client from `client/dist` (run `npm run build` first). |
| `npm run build` | Type-check and build the client |
| `npm run typecheck` | Type-check the client and the core package |
| `npm run contracts:types` | Regenerate the TypeScript types from `contracts/schemas/` |
| `npm test -- tests/test_sim.py` | Extra arguments go to pytest. Every step runs; the exit code is non-zero if any failed |
| `cd server-java && ./mvnw verify` | Java tests alone |
| `STUDIO_CLAUDE=1 npm test` | Adds the real Claude CLI test (one real model call) |
| `STUDIO_WEB=1 npm test` | Reserved for real web tests. A `web` marker is declared in `pyproject.toml`, but no test uses it yet. |

Settings through environment variables (or a `.env` file in the repository root, which the launcher loads):

| Variable | Meaning |
|---|---|
| `STUDIO_PYTHON` | Python interpreter the Java server runs the tasks with, and the tests use (default `.venv/bin/python`) |
| `STUDIO_BASE_PYTHON` | Python used by `npm run setup` to create the venv |
| `STUDIO_RUNTIME` | Data folder (default `runtime/`) |
| `STUDIO_HOST`, `STUDIO_PORT` | API address (default `127.0.0.1`, `8800`) |
| `STUDIO_TOKEN` | Bearer token for `/api/v1`. Required when `STUDIO_HOST` is not a loopback address |
| `STUDIO_API` | Where the client dev server proxies `/api/v1` (default `http://127.0.0.1:8800`) |
| `GEMINI_API_KEY` or `GOOGLE_API_KEY` | Gemini key, used when none is saved in Settings |

The fixture tests use scripted model answers and mocked downloads. They need no network and no signed-in CLI.

Architecture in one line: the Java server owns HTTP, jobs, the project store, LLM settings and the usage ledger;
each AI or numeric job is one `python -m studio.tasks <task>` process, following [docs/PROTOCOL.md](docs/PROTOCOL.md).

## Layout

```
server-java/        Java server (Spring Boot): /api/v1 routes, jobs, project store, LLM settings, usage ledger
  src/main/java/dev/robode/studio/   api, jobs (runs the Python tasks), store, llm, config, meta
  src/test/                          unit tests, FullFlowTest (real Python tasks), GoldenContractTest (76 recorded responses)
server/studio/      Python: AI and numeric work, run one task per process
  tasks/            entry points: python -m studio.tasks <task>; reads the request on stdin
  schemas.py        shared data types (spec, scene, requirements, checks, versions, candidates)
  agents/           one module per AI role: prompt, output schema, validator, run()
                    spec, scene, robot, revise, analysis
  physics/          requirements from the spec: base placement, reach, payload, grip, speed, locomotion, statics
  families/         robot types as plug-ins: serial_arm, gantry, mobile_base, mobile_manipulator; IK, URDF, GLB
  sim/              task simulator, collision test, check reports
  pipeline/         design.py (brief to version, revise, sizing loop), catalog.py (search, analyse): pure functions
  catalog/          web search result cleaning, safe download, datasheet extraction, candidate model, comparison
  ingest/           URDF, document and photo intake
  llm/              provider-independent adapters: invoke(input, system_prompt) -> str
                    claude_cli.py, gemini.py, keys.py, registry.py (profiles and roles), usage.py, scripted.py (tests)
packages/core/      @robode/studio-core: DOM-free types, API client, kinematics and playback
contracts/          JSON Schemas of the shared types (checked by npm run contracts:check)
client/             React + Vite + three.js UI
tests/              pytest with fixtures
scripts/studio.mjs  the launcher behind the npm scripts
runtime/            projects, LLM profiles, usage log, API keys (git-ignored)
docs/               guides, contracts, protocol, PROJECT_TIMELINE.md
```

## LLM roles

Each role has its own saved profile. Change them under **Settings**.

| Role | Default | Job |
|---|---|---|
| spec | Gemini 3.1 Pro (API) | Brief to technical specification; asks the questions |
| scene | Gemini 3.8 Flash (API) | Tables, shelves, obstacles, markers, objects |
| robot | Gemini 3.1 Pro (API) | Robot type and a rough starting concept |
| revise | Gemini 3.1 Pro (API) | Turns a change request into a change to the spec, scene or robot |
| search | Gemini 3.8 Flash (API) | Web search (Grounding with Google Search) for real robots and prices |
| extract | Gemini 3.8 Flash (API) | Reads a datasheet and quotes the values it finds |
| analysis | Gemini 3.1 Pro (API) | Plain-words impact of each difference |
| objects | Gemini 3.5 Flash-Lite (API) | Simple 3D models of the task objects |
| checks | Gemini 3.1 Pro (API) | Plans the task-specific checks; code evaluates them |
| ask | Gemini 3.8 Flash (API) | Answers the Ask bar's questions |

Three providers are built: Google Gemini (an API key saved in Settings, never shown again, kept in
`runtime/llm/keys.json`), the Gemini CLI (your own Google sign-in, no key) and the Claude Code CLI (not a default here). A provider is one module with
`invoke(input, system_prompt)` and one entry in `llm/registry.py`. The Gemini adapter has no tests in this
repository and no recorded run against Google.

## Google edition (this branch)

The `google-gemini` branch runs entirely on Google for the Startup Buildathon India 2026: Gemini models for every role
and a Cloud Run deployment. Nothing on this branch has been run against the real Gemini API, the real Gemini CLI or
Cloud Run; the tests use fake adapters and a fake `gemini` executable.

- **Gemini API (default).** Every role starts on a saved Gemini profile: `gemini_pro` (spec, revise, robot, analysis,
  checks), `gemini_flash` (scene, search, extract, ask) and `gemini_flash_lite` (objects). Put a key from
  <https://aistudio.google.com/apikey> in **Settings** or set `GEMINI_API_KEY` before starting. Search uses Grounding
  with Google Search.
- **Gemini CLI (local, no key).** `npm install -g @google/gemini-cli`, run `gemini` once and choose "Sign in with
  Google", then add a "Gemini CLI (Google sign-in)" model under Settings (profiles `gemini_cli_pro` and
  `gemini_cli_flash` are saved for new installs; assign them to roles to use them). Each call runs in an empty temporary
  folder with every CLI tool turned off (only Google Search and web fetch for the search role), and the child process
  gets no `GEMINI_API_KEY` or `GOOGLE_API_KEY`, so the CLI's own login is used. Text only: roles that read images need a Gemini API model.
  Details are in `server/studio/llm/gemini_cli.py`.
- **Google Antigravity CLI (local, no key).** Install Google Antigravity, run `agy` once and sign in (the installer puts
  it in `~/.gemini/bin`, which need not be on `PATH`; the studio looks there). Add a "Google Antigravity CLI
  (signed-in, no key)" model under Settings with `gemini-3.8-flash-high`, `-medium` or `-low` (profiles
  `antigravity_flash_high`, `antigravity_flash_medium` and `antigravity_flash_low` are saved for new installs; assign them
  to roles to use them). Each call is one headless `agy` run in an empty temporary folder: the system prompt is a
  custom agent file, the model is offered no tool (only web search and fetch for the search role), every file, shell and
  MCP permission is denied, the prompt goes on stdin, structured answers use `--json-schema`, and the child gets no API
  keys. Calls use your Antigravity quota (no price is shown). Text only. Details are in `server/studio/llm/antigravity_cli.py`.
- **Cloud Run.** `Dockerfile` and `deploy/cloudrun/README.md` (the exact `gcloud` steps, secrets, sizing and storage).
  The browser opens the deployment once with `/#token=<STUDIO_TOKEN>`.

## Limits

- The checks are kinematic and static-load calculations: reach, joint limits, static joint torques, speed, cycle
  time and coarse collisions. There is no dynamics simulation, no contact physics and no controller.
- Collisions are reported, not avoided. There is no motion planner.
- Prices are web estimates with a date and a source. They are not quotes.
- A real robot's values come from one datasheet or product page. Values without a verified quote are shown and
  excluded from pass or fail. Candidates of family `other` (SCARA, delta and similar) can be listed but not analysed.
- Nothing has been tested on a physical robot. There is no Isaac Sim or GPU validation.
- Robot studio is a placeholder: choosing it records the decision and nothing more.
- The API has no login unless `STUDIO_TOKEN` is set. The server binds to `127.0.0.1` and refuses any other address
  without a token.
- Fixture tests are not model evaluations. The recorded real-model and web-search runs are in
  [docs/PROJECT_TIMELINE.md](docs/PROJECT_TIMELINE.md).
