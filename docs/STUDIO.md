# RoboDE Studio: user guide

Studio helps you go from a job to a choice of real robot. You describe the job. Studio generates a rough robot,
shows it doing the job, and checks it against requirements it works out from your description. Then it finds real
robots and compares one with its manufacturer's own numbers.

For setup, see [README.md](../README.md). For how it works inside, see [STUDIO_TECHNICAL.md](STUDIO_TECHNICAL.md).

## What to expect

- The generated robot is a rough concept made of boxes and cylinders. It is not a product.
- Every pass or fail comes from a calculation you can open and read.
- The AI chooses the layout of the scene and the type of robot. It does not decide pass or fail.
- Checks are kinematic and static-load only. Nothing was tested on a real robot.
- Real robot prices are web estimates with a date and a source. They are not quotes.

## The screen

The left rail has:
- the project picker and **New project**;
- the seven stages, in order;
- **Settings**, and **Delete project** (it asks first);
- a note that Studio runs on this computer, and today's count of model calls.

A stage is locked until the one before it has produced what it needs. A tick means the stage is done. When you
open a saved project, Studio takes you to the furthest stage it has reached.

Long work (designing, revising, searching, analysing) runs in the background. The page shows the current step and a
list of all steps so far.

## The seven stages

### 1. Brief

Describe the job in your own words: what moves, how heavy it is, where it goes and how often. Type it, or press the
microphone to dictate. The microphone uses your browser's speech recognition; whether that works, and where the
audio is processed, depends on your browser.

Four example tasks fill the box for you. At least 10 characters are needed. **Create project** saves the brief and
takes you to Reference. Afterwards the Brief page only shows what you wrote.

### 2. Reference (optional)

Add something that shows what you have in mind:
- photos (PNG, JPEG or WebP, up to 6, 15 MB each), by dropping, choosing or pasting them;
- a `.urdf` or a `.zip` that holds one (the URDF is read for its joints and sizes; xacro must be expanded first);
- a written description, saved with **Save description**.

The AI uses the reference to fill in reach, payload and kind of robot, and says so in the specification. If the
model chosen for the spec role cannot read images, the photos are skipped and Studio tells you. Press **Skip**
or **Continue**.

### 3. Questions

Press **Design it**. The AI first writes a technical specification. If something that changes the design cannot be
read from your brief, it asks, at most six questions a round and at most three rounds. Each question has a reason
and often a default. Leave a default as it is and it counts as your answer. In the last round the AI must produce
a specification and list every assumption it made.

When the specification is complete, Studio carries straight on: requirements, scene, robot, simulation. This takes a
minute or more.

### 4. Design

This is the main page.

- **Simulated task.** A 3D view of the scene and the robot. Press play, drag the time bar, set the speed (0.5x, 1x,
  2x) and loop. Drag to turn the view. Red pins are markers (pick-up and drop-off points). The current phase is
  shown under the view, for example "pick bottle at A". Collisions and unreachable points are marked on the time bar
  and listed under the view. The tag says **All checks pass** or **Some checks fail**.
- **Requirements.** The table described in the next section.
- **Specification.** The AI's summary of the job, the actions in order, the objects, the environment, the accuracy,
  and **Assumptions made by the AI**. Read the assumptions: they are the places where your brief did not say.
- **Robot concept.** The robot type, the AI's reason for it, and the sized parameters.
- **Suggest a change.** Say what to change in plain words, for example "make the table 20 cm higher", "use a
  gantry instead of an arm", "4 seconds per cycle", "mount the robot on the table". Press **Apply change**.

Each change makes a new version (v2, v3 ...) and keeps the old ones. The tabs at the top switch between versions. A
version that was changed from an earlier one shows your request and a one-line summary of what changed.

If your change affects the task (positions, payload, speed, cycle time), Studio re-sizes the robot from the new
requirements instead of keeping the old sizes. If you give robot numbers yourself (a link length, a payload, a
speed), those numbers are kept as you gave them, and the checks report it if they do not meet the requirements.

#### Approving

**Approve this version** marks one version as the design to take forward. The interface then freezes the design
page: no more changes. Open the stage again later and the approved version is still the one used. The search and
the analysis use the approved version.

### 5. Robot options

Press **Find real robots**. The AI searches the web, so this takes a few minutes. Each result is a card:

- the manufacturer and model, the robot type, and a confidence level (high when the manufacturer's own page states
  the numbers);
- a **price**: a figure or range with its currency, the tag **web estimate**, the date and a link to the source.
  The date is the one on the page, or `seen` and the day Studio looked if the page shows none. A note says what is
  included (arm only, with controller). Many industrial robots have no public price; the card then says
  **No published price**;
- published numbers (payload, reach, axes, repeatability) with a **source** link. Hover the link to see the quote;
- a **quick screen**: pass, fail or unknown for payload and reach, from the published numbers only;
- a **Product page** link and an **Analyse** button;
- a picture. Pictures are linked from the vendor's site. Studio does not store them. If the vendor blocks the
  link or removes the file, a placeholder shows.

A price appears only when the AI gave a source, a verbatim quote and a figure that really appears in that quote.
Otherwise the price is left out, and the progress list says how many were left out and why.

The search asks for the smallest robots that meet every requirement with margin, best fit first, plus a cheaper
and a more capable option. The search can still miss good robots or return a poor fit. Treat it as a start.

**Search again** replaces the list.

### 6. Analysis

Choose **Analyse** on a card. Studio:

1. gets a document: a datasheet you uploaded earlier, or the datasheet link from the search, or, when there is
   none or it fails, the product page. The progress line says which one it used. You may type a datasheet URL
   yourself to use instead;
2. has the AI read it and quote every value it finds (payload, reach, number of axes, joint ranges and speeds, link
   lengths, mass, repeatability and so on);
3. **checks each quote in code**. A value is **verified** only if its quote is found in the document text and the
   quote contains the number;
4. builds a model of that robot from the verified values only. Anything missing is filled from the generated robot,
   scaled, and listed under **Estimated, not from the manufacturer**. Every model value is labelled **verified**,
   **derived** (computed from verified values, such as link lengths scaled to the verified reach), **carried** (from
   the generated design, such as the gripper) or **default** (not published);
5. simulates the same task on the same scene with that model. A check that depends on a value the manufacturer
   did not publish (joint torque, joint speed, acceleration, cycle time) gets no pass or fail and reads **not
   published**, with the assumed value shown as an estimate;
6. lists the differences from the generated robot, each with a severity, and has the AI explain in plain words what
   each one means for your task. The AI cannot change the numbers.

If the document cannot be downloaded (many sites refuse, or the file is not a PDF, HTML or text), an
**Upload datasheet** button appears. Upload the PDF or a text file and the analysis runs again. Scanned PDFs without
text cannot be read.

What you see:
- **The same task with this robot**: the 3D playback and the requirement table for this robot;
- **Differences**: topic, the generated robot's value, this robot's value, impact, and severity:
  - `blocking`: a failed reach, payload, grip or collision check;
  - `major`: another failed check;
  - `minor`: passes, but within 10 % of the requirement, or could not be checked;
  - `ok`: passes with margin;
- **Manufacturer values**: each value with its quote and the page when known, and its status. **Unverified values are
  shown but excluded from pass or fail.**
- **Model built from the datasheet**: the parameters used.

You can analyse several robots. Each gets a tab. Candidates of type `other` (SCARA, delta and similar) cannot be
modelled, so the analysis stops with an error.

### 7. Decision

Pick one of the analysed robots, then either:
- **Approve this robot**, or
- **Open in Robot studio**.

Both record your choice in the project. Robot studio is not built yet: it will take the chosen robot through
control set-up and testing. Prices and specifications are web estimates, so confirm them with the manufacturer
before buying.

**Download design package** gives a ZIP:

| File | Content |
|---|---|
| `design.urdf`, `design.glb` | The generated robot at its zero pose |
| `spec.json`, `scene.json` | The specification and the scene |
| `requirements.json` | Requirements with their calculations |
| `sim_summary.json` | The robot, every check and the simulation events |
| `candidates/<id>/analysis.json` | The analysis (without the frames and the URDF) |
| `candidates/<id>/manufacturer_values.json` | Values and quotes |
| `candidates/<id>/candidate.urdf` | The model built from the datasheet |
| `decision.json` | Your decision, if one was made |
| `README.txt` | The limits, in short |

The package holds the approved version, or the latest one when none is approved.

## Reading the requirement table

Columns:
- **Requirement**: the name. Click it to open the working.
- **Required**: the target, with `>=` (the robot must reach at least this), `<=` (at most this), or `info` (context,
  no test).
- **Robot**: the robot's value.
- **Result**: **PASS**, **FAIL**, or a dash when the value could not be checked.

Most rows compare a rated value of the robot (for example its payload rating or its reach) with the requirement.
Cycle time, peak joint speed and static joint load come from the simulated task. A dash means no robot value exists
for that row: for example a mobile base without an arm has no reach, and the load rides on its deck.

Open a row and you see up to two blocks:
- **How the requirement was derived**: the formula, each input with its unit, the result, and a note. For example
  `payload = (m_object + m_tool) * SF` with the object mass, the tool mass and the safety factor.
- **How the robot's value was obtained**: for rows such as static joint load, the formula and what it used.
  A line of detail may follow, for example "5 of 6 waypoints reachable: unreachable pick_bottle_a_grasp".

Rows that you did not ask for but that matter also appear: joint limits, static load per joint, peak joint speed, no
collisions with the scene, and, for driving robots, path following and drive speed.

Things to know when reading:
- The payload and grip-force targets already include a safety factor (1.5 unless the specification says
  otherwise). Reach, speed and the other targets do not.
- The speed and acceleration targets come from the cycle time. A shorter cycle time means a faster, stronger robot.
- If the run is faster than your cycle time, the playback is slowed so that it takes the full cycle. The
  cycle-time row says the fastest time the robot could have done it in.
- A failed reach row names the waypoints that cannot be reached.

## Settings

Open **Settings** in the left rail.

- **Usage.** Model calls and tokens for today, 7 days, 30 days or all time, by role, by job, by project and by
  model, with an estimated cost. The cost is the provider's figure at list prices. On a subscription it is an
  equivalent figure, not a charge. **Start a new count** keeps the old log in a timestamped file and starts again.
- **Who does what.** One saved model for each of the seven roles: spec, scene, robot, revise, search, extract,
  analysis. Each can use a different model. A role that uses a Gemini profile warns you when no key is set.
- **Saved models.** The list, with **Test** (one short text call and, if supported, one image call) and **Remove**.
  A model that is in use by a role cannot be removed.
- **Add a model.** Choose a provider:
  - **Claude (Claude Code CLI)**: uses the Claude Code sign-in on this computer. No key. Optional effort level and
    CLI path.
  - **Google Gemini (API key)**: paste your key from Google AI Studio and save it. It is stored on this computer in
    the runtime folder, readable only by your user account, and never shown again. The key can also come from the
    `GEMINI_API_KEY` or `GOOGLE_API_KEY` environment variable. Google bills the key, not Studio. Web search with
    Gemini uses Grounding with Google Search, which Google prices separately.

Suggested use: a stronger model for spec, robot and revise (the defaults are Claude Opus), a faster one for the
rest. The search role needs a model that can use web search.

## Limits

- **Kinematic and static-load checks only.** Studio checks whether the arm can reach each point, whether each joint
  can hold the load in a still pose, whether speed and time fit. It does not simulate dynamics, contact, grip
  slip, vibration, controllers or sensors.
- **Collisions are reported, not avoided.** The robot takes direct joint-space moves. If a move would hit the table,
  Studio tells you; it does not plan around it. Collision shapes are coarse (capsules for the links, boxes for the
  scene).
- **The generated robot is a concept.** Its link lengths, masses and ratings are chosen by rules to meet the
  requirements. It shows what class of robot you need.
- **The AI makes mistakes.** It can choose a poor layout, miss an obstacle or assume the wrong thing. Read the
  assumptions and the scene. Code checks the shape of its answers and a number of consistency rules, not whether the
  scene suits your real site.
- **Prices are web estimates** with a date and a source. They change and are not offers.
- **The analysis is only as good as one document.** A robot's real behaviour can differ from what one datasheet
  says. Missing values are estimates from the generated robot.
- **Not tested on hardware.** There is no Isaac Sim or GPU validation either.
- **Robot studio does not exist yet.** The button records your choice only.
- **Your data stays local.** Projects are files in the runtime folder. The model providers you choose, and the web
  pages the search reads, are the only outside parties. The API has no login: keep it on `127.0.0.1`.
