"""Spec agent: turns the brief (and reference) into questions, then into a TechnicalSpec."""
from pydantic import BaseModel

from ..llm.base import ask_json
from ..schemas import Question, TechnicalSpec
from .common import UNTRUSTED, dumps, finite, parse

MAX_QUESTIONS, MAX_ROUNDS = 6, 3

SYSTEM = f"""You are the requirements engineer of a robot design tool. You turn a person's plain description of a job into a technical specification that a physics engine and a simulator can use.

{UNTRUSTED}

Frame: world frame, metres, z up, origin on the floor. Put every marker (pick-up, drop-off, waypoints, stations) in that frame, with z at the surface the object rests on (table top, shelf, floor). State the frame in "frame".

Ask a question ONLY when the answer changes the design and cannot be read from the brief, the reference or the answers so far. Typical needs: payload mass and size, positions of A and B or the layout, speed or cycle time, accuracy, environment, whether the robot has to drive. Never ask something already answered; never ask about colours, brands or budget. Each question is short, with a unit and a sensible default where one exists, and a one-line "why". At most {MAX_QUESTIONS} questions per round and at most {MAX_ROUNDS} rounds.
If a reference robot is given (URDF summary, documents, photos), use its reach, payload and kind to fill in what you can and say so in "reference".
Answer with {{"questions": [...]}} while you still need answers, or {{"spec": {{...}}}} when you have enough. In the last round you must return a spec: fill gaps with reasonable assumptions and list each one in "assumptions".

Spec rules:
- Every action's "target" is a marker id; every action's "object" is an object id. Every object's start_marker is a marker id.
- physics_inputs must match the main object: object_mass_kg and object_size_m equal that object's mass_kg and size_m, grip equals its grip.
- cycle_time_s is the whole cycle of the task in seconds. If the person gave no speed, assume a reasonable one and list it as an assumption.
- If the robot has to drive between places, set locomotion.required true, give the path as world points, and set travel_distance_m.
- Marker ids are lowercase letters, digits and underscores."""


class SpecAnswer(BaseModel):
    questions: list[Question] | None = None
    spec: TechnicalSpec | None = None


SCHEMA = SpecAnswer.model_json_schema()


def validate_spec(spec: TechnicalSpec) -> TechnicalSpec:
    """Check the references and numbers inside a TechnicalSpec. Raises ValueError with a message for the LLM."""
    markers = {m.id for m in spec.markers}
    objects = {o.id: o for o in spec.objects}
    if len(markers) != len(spec.markers):
        raise ValueError('Marker ids must be unique')
    if len(objects) != len(spec.objects):
        raise ValueError('Object ids must be unique')
    for m in spec.markers:
        if not finite(m.position) or max(abs(c) for c in m.position) > 1000:
            raise ValueError(f'Marker {m.id} has a position that is not a sensible number of metres')
    for a in spec.actions:
        if a.target and a.target not in markers:
            raise ValueError(f'Action "{a.verb}" targets marker "{a.target}", which does not exist; markers are {sorted(markers)}')
        if a.object and a.object not in objects:
            raise ValueError(f'Action "{a.verb}" handles object "{a.object}", which does not exist; objects are {sorted(objects)}')
        if a.verb in ('pick', 'place', 'move', 'drive') and not (a.target or (a.verb == 'pick' and a.object in objects
                                                                              and objects[a.object].start_marker)):
            raise ValueError(f'Action "{a.verb}" needs a target marker')
    for o in spec.objects:
        if o.start_marker and o.start_marker not in markers:
            raise ValueError(f'Object "{o.id}" starts at marker "{o.start_marker}", which does not exist')
        if not finite(o.size_m) or min(o.size_m) <= 0:
            raise ValueError(f'Object "{o.id}" needs positive sizes in metres')
    handled = [a.object for a in spec.actions if a.object in objects and a.verb in ('pick', 'place', 'move')]
    main = objects[handled[0]] if handled else (spec.objects[0] if spec.objects else None)
    pi = spec.physics_inputs
    if main:
        if abs(pi.object_mass_kg - main.mass_kg) > 1e-6:
            raise ValueError(f'physics_inputs.object_mass_kg ({pi.object_mass_kg}) must equal the main object "{main.id}" mass_kg ({main.mass_kg})')
        if any(abs(a - b) > 1e-6 for a, b in zip(pi.object_size_m, main.size_m)):
            raise ValueError(f'physics_inputs.object_size_m must equal the main object "{main.id}" size_m {list(main.size_m)}')
        if pi.grip != main.grip:
            raise ValueError(f'physics_inputs.grip ({pi.grip}) must equal the main object grip ({main.grip})')
    if spec.locomotion.required:
        if len(spec.locomotion.path) < 2:
            raise ValueError('locomotion.required is true: give a path of at least two world points')
        if not all(finite(p) for p in spec.locomotion.path):
            raise ValueError('locomotion.path has a non-numeric point')
    elif pi.travel_distance_m > 0:
        raise ValueError('travel_distance_m is set but locomotion.required is false')
    if not spec.frame.strip():
        raise ValueError('State the frame of the marker coordinates in "frame"')
    return spec


def check(data, round_):
    answer = parse(SpecAnswer, data, 'The answer')
    if bool(answer.questions) == bool(answer.spec):
        raise ValueError('Answer with exactly one of "questions" or "spec"')
    if answer.questions:
        if round_ >= MAX_ROUNDS:
            raise ValueError(f'This is round {round_} of {MAX_ROUNDS}: no more questions. Return a spec and list your assumptions')
        if len(answer.questions) > MAX_QUESTIONS:
            raise ValueError(f'At most {MAX_QUESTIONS} questions per round')
        ids = [q.id for q in answer.questions]
        if len(set(ids)) != len(ids):
            raise ValueError('Question ids must be unique')
        return {'questions': answer.questions}
    validate_spec(answer.spec)
    if round_ >= MAX_ROUNDS and not answer.spec.assumptions:
        raise ValueError('In the last round, list every assumption you made in "assumptions"')
    return {'spec': answer.spec}


async def run(adapter, brief, reference, answers, round):
    """reference: {'text': str, 'urdf': dict | None (ingest.urdf.summary), 'images': [llm.base.Image]} or None."""
    reference = reference or {}
    payload = {'brief': brief, 'round': round, 'max_rounds': MAX_ROUNDS, 'answers_so_far': answers or {}}
    if reference.get('text'):
        payload['reference_text'] = reference['text'][:20000]
    if reference.get('urdf'):
        payload['reference_robot_urdf_summary'] = reference['urdf']
    images = reference.get('images') or None
    prompt = 'Job description and context (JSON):\n' + dumps(payload)
    if round >= MAX_ROUNDS:
        prompt += '\n\nThis is the last round: return a spec now.'
    if images:
        prompt += f'\n\nPhotos of the reference robot follow ({len(images)}).'
    return await ask_json(adapter, prompt, SYSTEM, schema=SCHEMA, images=images, validate=lambda d: check(d, round))
