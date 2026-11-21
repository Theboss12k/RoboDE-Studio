"""Robot agent: picks a robot family and a rough starting concept. Code sizes it afterwards."""
from ..llm.base import ask_json
from ..schemas import RobotConcept
from .common import dumps, parse

SYSTEM = """You choose the kind of robot for a task and give a rough starting design. Code then sizes the robot from physics (reach, torques, gripper force, speeds), so your numbers are only hints; the family choice and the rationale are what matters.

Rules:
- "family" is one of the families offered. Mobile tasks (the robot must drive) need a mobile family; use mobile_manipulator when it must also pick or place, mobile_base when it only carries.
- Prefer the simplest family that can do the task: a gantry for straight-line pick and place over a fixed area, a serial arm for flexible reach, and so on.
- "params" is a partial object of that family's parameters (see params_schema); omit what you do not know. Lengths in metres, angles in degrees, masses in kg. Do not copy requirement numbers into rated values you cannot justify.
- "name" is a short descriptive name (for example "6-axis bottle mover"). "rationale" says in two or three plain sentences why this family fits and what its limits are.
- Requirements are given as ids with targets and units only. Do not state whether the robot passes them."""

SCHEMA = RobotConcept.model_json_schema()


def validate_concept(concept: RobotConcept, spec, families_module=None):
    from .. import families as registry
    families_module = families_module or registry
    if concept.family not in families_module.REGISTRY:
        raise ValueError(f'Unknown family "{concept.family}"; choose one of {sorted(families_module.REGISTRY)}')
    family = families_module.get(concept.family)
    if spec.locomotion.required and not family.mobile:
        raise ValueError('The task needs the robot to drive: choose a mobile family (mobile_base or mobile_manipulator)')
    if not isinstance(concept.params, dict):
        raise ValueError('params must be an object')
    try:
        family.parse(concept.params)
    except ValueError as error:
        raise ValueError(f'params do not fit the {concept.family} parameters: {str(error)[:700]}') from error
    return concept


async def run(adapter, spec, requirements, families):
    reqs = [{'id': r.id, 'name': r.name, 'must_be': r.comparator, 'target': r.target, 'unit': r.unit} for r in requirements]
    prompt = ('Task (JSON):\n' + dumps({'title': spec.title, 'summary': spec.summary, 'environment': spec.environment,
                                       'objects': spec.objects, 'locomotion_required': spec.locomotion.required,
                                       'accuracy_m': spec.accuracy_m, 'speed_target': spec.speed_target}) +
              '\n\nRequirements (targets only):\n' + dumps(reqs) + '\n\nFamilies offered:\n' + dumps(families))
    return await ask_json(adapter, prompt, SYSTEM, schema=SCHEMA,
                          validate=lambda d: validate_concept(parse(RobotConcept, d, 'The robot concept'), spec))
