"""Robot agent: picks a robot family and a rough starting concept. Code sizes it afterwards."""
from ..llm.base import ask_json
from ..schemas import RobotConcept
from .common import dumps, parse

SYSTEM = """You choose the kind of robot for a task and give a rough starting design. Code then sizes the robot from physics (reach, torques, gripper force, speeds), so your numbers are only hints; the family choice and the rationale are what matters.

Rules:
- "family" is one of the families offered. Mobile tasks (the robot must drive) need a mobile family; use mobile_manipulator when it must also pick or place, mobile_base when it only carries.
- Prefer the simplest family that can do the task: a gantry for straight-line pick and place over a fixed area, a serial arm for flexible reach, and so on.
- "params" is a partial object of that family's parameters (see params_schema); omit what you do not know. Lengths in metres, angles in degrees, masses in kg. Do not copy requirement numbers into rated values you cannot justify.
- For a gantry whose task rotates the tool (or a mounted object) more than half a turn, set tool_yaw true and tool_yaw_continuous true so the yaw joint can turn without limit.
- "name" is a short descriptive name (for example "6-axis bottle mover"). "rationale" says in two or three plain sentences why this family fits and what its limits are.
- Requirements are given as ids with targets and units only. Do not state whether the robot passes them."""

SCHEMA = RobotConcept.model_json_schema()


def validate_concept(concept: RobotConcept, spec, families_module=None, limits=None, fixed_only=False):
    """Raises ValueError (message for the LLM) unless the concept fits the spec, and, for a fleet member
    (`fixed_only`), is a fixed-base family allowed by the user's `limits` (FleetLimits)."""
    from .. import families as registry
    from ..fleet.assign import FIXED_BASE_FAMILIES
    families_module = families_module or registry
    if concept.family not in families_module.REGISTRY:
        raise ValueError(f'Unknown family "{concept.family}"; choose one of {sorted(families_module.REGISTRY)}')
    family = families_module.get(concept.family)
    if fixed_only or limits is not None:
        allowed = [f for f in (getattr(limits, 'families', None) or FIXED_BASE_FAMILIES) if f in FIXED_BASE_FAMILIES]
        allowed = allowed or list(FIXED_BASE_FAMILIES)
        if family.mobile or concept.family not in FIXED_BASE_FAMILIES:
            raise ValueError(f'"{concept.family}" drives: a fleet member stands on a fixed base; choose one of {allowed}')
        if concept.family not in allowed:
            raise ValueError(f'"{concept.family}" is not among the robot types the user allows; choose one of {allowed}')
    if spec.locomotion.required and not family.mobile:
        raise ValueError('The task needs the robot to drive: choose a mobile family (mobile_base or mobile_manipulator)')
    if not isinstance(concept.params, dict):
        raise ValueError('params must be an object')
    try:
        family.parse(concept.params)
    except ValueError as error:
        raise ValueError(f'params do not fit the {concept.family} parameters: {str(error)[:700]}') from error
    return concept


FLEET_RULES = """
This robot is one member of a fleet of fixed-base robots that share the task: it does only the steps listed in the task, objects pass between robots through handover stands, and it parks at its park positions. Choose only among the families offered. The user's limits (maximum reach, maximum payload, allowed types) are given; code checks them after sizing. When "feedback" is present, code sized and checked your previous choice and it failed: take the numbers in it into account (another family or other dimensions)."""


async def run(adapter, spec, requirements, families, limits=None, feedback=None, member=None):
    """The robot concept for `spec`. Fleet members (`member` = id) get the user's `limits` (FleetLimits), only
    fixed-base families and, on a re-choice, code's `feedback` (plain text with the failing checks and numbers)."""
    reqs = [{'id': r.id, 'name': r.name, 'must_be': r.comparator, 'target': r.target, 'unit': r.unit} for r in requirements]
    fleet = member is not None or limits is not None
    if fleet:
        from ..fleet.assign import FIXED_BASE_FAMILIES
        allowed = [f for f in (getattr(limits, 'families', None) or FIXED_BASE_FAMILIES) if f in FIXED_BASE_FAMILIES]
        allowed = allowed or list(FIXED_BASE_FAMILIES)
        families = [f for f in families if f.get('name') in allowed]
    prompt = ('Task (JSON):\n' + dumps({'title': spec.title, 'summary': spec.summary, 'environment': spec.environment,
                                       'objects': spec.objects, 'locomotion_required': spec.locomotion.required,
                                       'accuracy_m': spec.accuracy_m, 'speed_target': spec.speed_target}) +
              '\n\nRequirements (targets only):\n' + dumps(reqs) + '\n\nFamilies offered:\n' + dumps(families))
    system = SYSTEM
    if fleet:
        system = SYSTEM + FLEET_RULES
        lim = limits.model_dump(mode='json', exclude={'note', 'budget_usd', 'brands'}) if limits is not None else {}
        prompt += f'\n\nFleet member {member or "r1"}. User limits (JSON):\n' + dumps(lim)
        if feedback:
            prompt += '\n\nFeedback from code on the previous choice:\n' + feedback.strip()[:2000]
    return await ask_json(adapter, prompt, system, schema=SCHEMA,
                          validate=lambda d: validate_concept(parse(RobotConcept, d, 'The robot concept'), spec,
                                                              limits=limits, fixed_only=fleet))
