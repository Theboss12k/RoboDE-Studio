# RoboDE Studio repository instructions

## Change documentation

The user requires a maintained change document. Update `docs/PROJECT_TIMELINE.md`
for every functional change, bug fix, configuration change or test-driven behavior
change made in this repository. Use dated entries, identify affected applications,
describe the resulting behavior, and link detailed evaluation reports when present.
Update an in-progress entry while working; record completed checks and limitations
before delivering. Preserve historical entries instead of rewriting past results.

Never record credentials, private access codes, internal prompts or user data.
LLM keys live in `runtime/llm/keys.json`, which is git-ignored; never copy them into
code, docs, tests or the timeline.

Record results as what they were:
- fixture tests (scripted model answers) are not model evaluations;
- real model inference is named as such, with the model and date;
- real web search is named as such, with the query date and sources;
- robot checks are kinematic and static-load calculations in code, not dynamics
  simulation and not hardware tests; do not describe them as simulated or tested on
  a physical robot;
- prices are web estimates with a date and source, never quotes.

Do not claim unrun tests.

## LLM architecture

LLM connections are global, modular adapters implementing
`async def invoke(input: str, system_prompt: str) -> str` (and `invoke_multimodal`
where images are needed). Provider integrations must remain independent of robot
families, the physics engine and the simulator.

Each role (spec, scene, robot, revise, search, extract, analysis) selects its own
saved LLM profile.

## Claims

- Numbers that decide pass or fail (reach, load, clearance, carry height) come from the
  physics engine and kinematics code, never from an LLM.
- A manufacturer value counts only with a verbatim quote that is verified in the
  downloaded document.
- A model's answer is a proposal until code has checked it.
