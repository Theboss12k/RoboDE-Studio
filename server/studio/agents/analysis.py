"""Analysis agent: explains what each difference between the generated design and a real robot means for the task."""
from pydantic import BaseModel

from ..llm.base import ask_json
from ..schemas import Difference
from .common import dumps, parse

SYSTEM = """You explain, in plain words for a non-specialist, what each difference between a generated robot design and a real product means for the task.

Rules:
- Return every difference in the same order. Copy topic, generated, candidate and severity unchanged, character for character; write only "impact".
- "impact" is one or two short sentences: what the difference means for the task (for example "reach is 8 cm short of the far marker, so the bottle at B cannot be placed"). Do not repeat the numbers more than needed, do not invent numbers, and do not say the product is good or bad overall.
- Severity was set by code from simulated checks; do not argue with it. A severity of ok needs only a short note.
- "not published" means the manufacturer did not state that value, so it could not be judged; it does not mean the robot failed. Say "the manufacturer does not publish this" rather than "fails".
- Never say a check was unavailable or missing when the text shows "passes" or "fails": those are results. Only "not published" and "not checked" mean no verdict.
- A value described as assumed or estimated (for example a torque rating or link lengths) is an estimate, not a manufacturer figure; call it an estimate.
- The values come from simulation of kinematics and statics and from manufacturer datasheets; do not claim hardware tests.
Answer with {"differences": [...]}."""


class Answer(BaseModel):
    differences: list[Difference]


SCHEMA = Answer.model_json_schema()


def check(data, originals):
    answer = parse(Answer, data, 'The answer').differences
    if len(answer) != len(originals):
        raise ValueError(f'Return exactly {len(originals)} differences, in the original order (got {len(answer)})')
    out = []
    for i, (new, old) in enumerate(zip(answer, originals), 1):
        for field in ('topic', 'generated', 'candidate', 'severity'):
            if getattr(new, field) != getattr(old, field):
                raise ValueError(f'Difference {i}: "{field}" was changed; copy it unchanged and write only "impact"')
        out.append(old.model_copy(update={'impact': new.impact.strip()[:600]}))
    return out


async def run(adapter, differences, spec):
    prompt = ('Task: ' + spec.title + ' - ' + spec.summary + '\n\nDifferences (JSON):\n' + dumps(differences))
    return await ask_json(adapter, prompt, SYSTEM, schema=SCHEMA, validate=lambda d: check(d, differences))
