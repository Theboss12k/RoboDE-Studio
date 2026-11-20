"""Brief -> spec -> requirements -> scene + robot (concurrently) -> sized robot -> URDF -> simulation -> DesignVersion.

Numbers that decide pass/fail come from code: requirements from physics/, sizes from the family, checks from sim/.
These functions are pure: plain inputs in, results out, no project or store access (the Java server stores results;
tasks/ runs these as the subprocess side). `llms` is any object with `for_role(role)`.
Synchronous numeric work runs in a worker thread so the server stays responsive.
"""
import asyncio
import hashlib
import json
import math
import re

from .. import families
from ..agents import revise as revise_agent
from ..agents.revise import BASE_MOVE_M
from ..agents import robot as robot_agent
from ..agents import scene as scene_agent
from . import object_models
from ..agents import spec as spec_agent
from ..llm.base import LLMError, supports_images
from ..llm.usage import set_context
from ..fleet.assign import auto_assign
from ..fleet.feasibility import single_robot_shortfall
from ..physics import derive, place_base
from ..schemas import DesignVersion, FleetLimits, RepairAttempt, RepairLog, RejectedPlan
from . import checks as checks_pipeline
from ..sim.report import episodes_from
from ..sim.task import simulate
from ..util import now, slug


def merge(base, over):
    """Recursive dict merge: values of `over` win."""
    out = dict(base)
    for k, v in over.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


MAX_SIZING_ITERATIONS = 3       # speed-sizing passes after the first simulation
SIZING_MARGIN = 1.05            # raise ratings a little beyond the exact ratio so the next run passes
LIMITING_SHARE = 0.7            # joints using at least this share of the busiest joint's speed rating are raised
SIZING_NOTE = ' Speed sizing:'


def cycle_shortfall(sim, spec):
    """Fastest feasible time / cycle time when the cycle_time check fails because the run is too long, else None."""
    c = next((c for c in sim.checks if c.requirement_id == 'cycle_time'), None)
    cycle = spec.physics_inputs.cycle_time_s
    if c is None or c.passed is not False or c.actual is None or c.actual <= cycle:
        return None
    return c.actual / cycle


def joint_speed_use(sim, family, params):
    """Peak speed of every actuated joint in the simulated run as a share of its rating (from the frames)."""
    ratings = {j.name: j.max_velocity for j in family.joints(family.parse(params)) if j.actuated}
    peak = dict.fromkeys(ratings, 0.0)
    for a, b in zip(sim.frames, sim.frames[1:]):
        dt = b.t - a.t
        if dt <= 1e-9:
            continue
        for n in ratings:
            if n in a.joints and n in b.joints:
                peak[n] = max(peak[n], abs(b.joints[n] - a.joints[n]) / dt)
    return {n: peak[n] / ratings[n] for n in ratings if ratings[n] > 0}


def sizing_hint(family, params, explicit=None):
    """The robot's params without the numbers code sized for the previous task (link lengths, strokes, ratings,
    masses, forces), keeping the family's qualitative choices (dof, gripper type, tool yaw, tool length, ...) and
    the numbers the user asked for explicitly. Used when a revision changed the requirements."""
    fam = families.get(family) if isinstance(family, str) else family
    if hasattr(fam, 'sizing_hint'):
        hint = fam.sizing_hint(params)
    else:
        hint = {k: v for k, v in (params or {}).items() if k not in getattr(fam, 'SIZED_FIELDS', ())}
    return merge(hint, explicit) if explicit else hint


def size_and_simulate(spec, requirements, scene, concept, base, explicit=None):
    """Size the robot from physics starting at concept.params, apply explicit user numbers on top, build the URDF
    and run the task. When the run is too long for the cycle time, raise the speed ratings of the limiting joints
    (up to the family's plausible limits, at most MAX_SIZING_ITERATIONS times, never over explicit user numbers),
    rebuild and simulate again; what was raised is appended to the concept's rationale. Returns (concept, urdf, sim)."""
    family = families.get(concept.family)
    params = family.size_from_physics(requirements, spec, concept.params).model_dump()
    # sized values are clamped to the family's plausible caps wherever they came from; explicit numbers go on top, as given
    params = family.clamp_to_caps(params) if hasattr(family, 'clamp_to_caps') else params   # caps apply to every source
    if explicit:
        params = family.parse(merge(params, explicit)).model_dump()
    sized = concept.model_copy(update={'params': params, 'base': base})
    urdf = family.build_urdf(family.parse(params), slug(sized.name))
    sim = simulate(spec, requirements, scene, sized)
    notes, capped = [], set()
    raise_speeds = getattr(family, 'raise_speeds', None)
    cycle = spec.physics_inputs.cycle_time_s
    for _ in range(MAX_SIZING_ITERATIONS):
        ratio = cycle_shortfall(sim, spec)
        if ratio is None or raise_speeds is None:
            break
        use = joint_speed_use(sim, family, params)
        top = max(use.values(), default=0.0)
        factors = {n: ratio * SIZING_MARGIN for n, u in use.items() if top > 0 and u >= LIMITING_SHARE * top}
        params2, done, hit = raise_speeds(params, factors, frozenset(explicit or ()))
        capped |= set(hit)
        if not done:
            break
        notes += done
        params = params2
        sized = sized.model_copy(update={'params': params})
        urdf = family.build_urdf(family.parse(params), slug(sized.name))
        sim = simulate(spec, requirements, scene, sized)
    if notes:
        text = f'{SIZING_NOTE} to meet the {cycle:g} s cycle the joint speed ratings were raised ({"; ".join(notes)}).'
        rationale = sized.rationale.split(SIZING_NOTE)[0].rstrip()
        sized = sized.model_copy(update={'rationale': rationale[:max(0, 2000 - len(text) - 1)] + text})
    if cycle_shortfall(sim, spec) is not None:
        why = (f'limited by the speed cap of {", ".join(sorted(capped))}' if capped else
               'speed ratings could not be raised further (explicit user numbers or no sizing rule for this robot)'
               if (raise_speeds is None or explicit) else 'not met after speed sizing')
        for c in sim.checks:
            if c.requirement_id == 'cycle_time':
                c.detail = f'{c.detail}; {why}' if c.detail else why
    return sized, urdf, sim


MAX_SPEC_IMAGES = 8      # the Claude CLI cap; brief images first, then reference photos, then answer images


async def specify(llms, brief, reference, answers, round_, progress, extras=None):
    """One spec round, from plain inputs: `reference` is the agent's view ({'text', 'urdf', 'images': [Image]} or
    None), `answers` all answers so far, `round_` the number of this round (1 for the first). `extras` is optional
    {'comments': {qid: text}, 'notes': [{round, text}], 'answer_images': [Image], 'brief_images': [Image]} from the user's answers.
    Returns {'status': 'questions', 'questions': [dict], 'round'} or {'status': 'spec', 'spec': TechnicalSpec, 'round'}."""
    progress('Reading your brief' if round_ == 1 else f'Updating the specification (round {round_})')
    set_context(task='spec')
    adapter = llms.for_role('spec')
    extras = dict(extras) if extras else None
    ref_images = list(reference['images']) if reference and reference['images'] else []
    answer_images = list(extras.get('answer_images') or []) if extras else []
    brief_images = list(extras.get('brief_images') or []) if extras else []
    total = len(brief_images) + len(ref_images) + len(answer_images)
    if total and not supports_images(adapter):
        progress('The spec LLM cannot read images; the photos are not used')
        brief_images, ref_images, answer_images = [], [], []
    elif total > MAX_SPEC_IMAGES:
        progress(f'Only {MAX_SPEC_IMAGES} images can be sent to the spec LLM; {total - MAX_SPEC_IMAGES} not used')
        brief_images = brief_images[:MAX_SPEC_IMAGES]
        ref_images = ref_images[:MAX_SPEC_IMAGES - len(brief_images)]
        answer_images = answer_images[:MAX_SPEC_IMAGES - len(brief_images) - len(ref_images)]
    if reference and reference['images']:
        reference = {**reference, 'images': ref_images}
    if extras and 'answer_images' in extras:
        extras['answer_images'] = answer_images
    if extras and 'brief_images' in extras:
        extras['brief_images'] = brief_images
    result = await spec_agent.run(adapter, brief, reference, answers, round_, extras)
    if 'questions' in result:
        return {'status': 'questions', 'questions': [q.model_dump(mode='json') for q in result['questions']],
                'round': round_}
    out = {'status': 'spec', 'spec': result['spec'], 'round': round_}
    if result.get('fleet') is not None:          # the spec agent's proposal of several robots (FleetHint)
        out['fleet'] = result['fleet']
    return out


async def design(llms, spec, progress, auto_repair=True):
    """A complete DesignVersion (placeholder number 0; the caller numbers and stores it) for a finished spec."""
    progress('Working out the requirements')
    base = place_base(spec)
    requirements = derive(spec, base)
    progress('Building the scene and the robot concept')

    async def scene_task():
        set_context(task='scene')
        return await scene_agent.run(llms.for_role('scene'), spec, base)

    async def robot_task():
        set_context(task='robot')
        return await robot_agent.run(llms.for_role('robot'), spec, requirements, families.describe())
    async def objects_task():
        return await object_models.make_models(llms, spec)       # never fails: {} when no model could be made
    scene, concept, models = await asyncio.gather(scene_task(), robot_task(), objects_task())
    if models:
        progress(f'Object models: {len(models)} made')
    scene = object_models.attach(scene, spec, models)
    progress(f'Sizing the {concept.family.replace("_", " ")} from the requirements')
    concept, urdf, sim = await asyncio.to_thread(size_and_simulate, spec, requirements, scene, concept, base)
    progress(f'Task simulated: {sum(c.passed is True for c in sim.checks)} of {len(sim.checks)} checks pass')
    sim, plan, rejected = await _planned_checks(llms, spec, requirements, scene, concept, sim, progress)
    version = DesignVersion(version=0, created=now(), spec=spec, requirements=requirements, scene=scene, robot=concept,
                            urdf=urdf, sim=sim, check_plan=plan, check_plan_rejected=rejected)
    return await repair_collisions(llms, version, progress) if auto_repair else version


async def _planned_checks(llms, spec, requirements, scene, concept, sim, progress, previous=None):
    """(sim, check_plan, check_plan_rejected) after the checks agent planned task-specific checks and code evaluated them
    (they count toward sim.ok). When the agent cannot be used nothing changes: with `previous` (the plan of the version
    this one is revised from) that plan is re-evaluated in code against the new simulation instead."""
    done = await checks_pipeline.plan_and_apply(llms, spec, requirements, scene, concept, sim, progress, previous)
    if done is not None:
        sim, plan, rejected = done
        return sim, plan, [RejectedPlan(**r) for r in rejected]
    if previous:
        return _reevaluated(spec, requirements, scene, concept, sim, previous)
    return sim, [], []


def _reevaluated(spec, requirements, scene, concept, sim, plan):
    """(sim, check_plan, check_plan_rejected) with the stored plan evaluated in code against `sim`; no model call."""
    sim, accepted, rejected = checks_pipeline.reevaluate(spec, requirements, scene, concept, sim, plan)
    return sim, accepted, [RejectedPlan(**r) for r in rejected]


FLEET_ROBOT_ESTIMATE_MAX = 3


def fleet_advice(version, hint=None, progress=None):
    """{robots_needed, reasons, source} when one robot looks not enough for the version's task, else None (and no
    progress line). Sources: the spec agent's FleetHint (a proposal) and/or code (single_robot_shortfall of the sized
    and simulated robot). Mobile tasks and fleet versions get no advice: fleets are fixed-base robots."""
    if version.fleet is not None or version.spec.locomotion.required:
        return None
    reasons, source = [], []
    if hint is not None and hint.robots_needed > 1:
        reasons.append(f'Spec agent (a proposal): {hint.reason}')
        source.append('spec')
    code = single_robot_shortfall(version.spec, version.requirements, version.robot, version.sim)
    if code:
        reasons += [f'Code: {r}' for r in code]
        source.append('code')
    if not reasons:
        return None
    needed = hint.robots_needed if 'spec' in source else 2
    if code:
        try:
            needed = max(needed, len(auto_assign(version.spec, FleetLimits(max_robots=FLEET_ROBOT_ESTIMATE_MAX)).members))
        except ValueError:
            pass
    if progress is not None:
        more = f' (and {len(reasons) - 1} more)' if len(reasons) > 1 else ''
        progress(f'One robot may not be enough: {reasons[0]}{more}')
    return {'robots_needed': int(needed), 'reasons': reasons, 'source': 'both' if len(source) == 2 else source[0]}


def markers_moved(old, new):
    a = {m.id: m.position for m in old.markers}
    b = {m.id: m.position for m in new.markers}
    return a.keys() != b.keys() or any(max(abs(x - y) for x, y in zip(a[k], b[k])) > 1e-6 for k in a)


def check_moment(base, t):
    """ValueError unless `t` (None = no moment) lies within the base version's simulation."""
    if t is None:
        return None
    duration = run_length(base)
    if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) or not 0 <= t <= duration:
        raise ValueError(f'The moment t must be between 0 and {duration:g} s (the length of version {base.version})')
    return float(t)


def run_length(v):
    """Length of the version's run on screen: the shared fleet clock for several robots, else the robot's run."""
    return v.fleet.duration_s if getattr(v, 'fleet', None) is not None else v.sim.duration_s


CHECKED_PREFIX = 'Checked in code after the change:'


def checked_outcome(sim):
    """One sentence on the checks code ran after the change (the model's summary is written before they run)."""
    failed = [c.name for c in sim.checks if c.passed is False]
    unchecked = sum(c.passed is None for c in sim.checks)
    text = f'{CHECKED_PREFIX} {sum(c.passed is True for c in sim.checks)} of {len(sim.checks)} pass'
    if failed:
        shown = ', '.join(failed[:3]) + (f' and {len(failed) - 3} more' if len(failed) > 3 else '')
        text += f'; failing: {shown}'
    if unchecked:
        text += f'; {unchecked} could not be checked'
    return text + '.'


async def revise(llms, base_version, request, t, progress, auto_repair=True):
    """A new DesignVersion (placeholder number 0, parent_version = base_version.version) from the version the user is
    looking at and, optionally, the moment `t` (s) on screen. Nothing is read from or written to a store.
    When the result has collisions, code asks the revise agent for a fix (up to MAX_REPAIR_ATTEMPTS) unless
    `auto_repair` is False."""
    version = await _revise_core(llms, base_version, request, t, progress)
    return await repair_collisions(llms, version, progress) if auto_repair else version


async def _revise_core(llms, base_version, request, t, progress, plan_checks=True):
    """One revise round without the collision repair. With `plan_checks` the checks agent plans again (the previous plan
    goes to it as a hint); without (a repair round) the stored plan is re-evaluated in code, no model call."""
    old = base_version
    request = (request or '').strip()
    if not request:
        raise ValueError('Say what to change')
    t = check_moment(old, t)
    if old.fleet is not None:                     # several robots: the fleet pipeline re-plans them
        from . import fleet as fleet_pipeline
        return await fleet_pipeline.revise_fleet(llms, old, request, t, progress)
    progress('Working out what the change touches')
    set_context(task='revise')
    patch = await revise_agent.run(llms.for_role('revise'), old, request, t)
    spec, scene, concept = old.spec, old.scene, old.robot
    base, requirements = old.robot.base, old.requirements
    base_moved = False
    if patch.spec:
        progress('Re-deriving the requirements')
        spec = patch.spec
        base = place_base(spec)
        requirements = derive(spec, base)
        base_moved = max(abs(a - b) for a, b in zip(base.position, old.robot.base.position)) > BASE_MOVE_M
    if patch.scene:
        scene = patch.scene
    elif patch.spec and (markers_moved(old.spec, spec) or base_moved):
        progress('Markers or the robot base moved: rebuilding the scene')
        set_context(task='scene')
        scene = await scene_agent.run(llms.for_role('scene'), spec, base)
    scene = scene.model_copy(update={'robot_base': base})
    scene = await object_models.models_for(llms, spec, scene, old, progress)
    explicit = None
    if patch.robot:
        if patch.robot.family == old.robot.family:
            explicit = {k: v for k, v in patch.robot.params.items() if old.robot.params.get(k) != v}
        concept = patch.robot.model_copy(update={'base': base})
    resize = patch.robot is not None or patch.spec is not None
    if resize and (patch.spec is not None or base_moved):   # the task changed: re-size instead of keeping old sizes
        concept = concept.model_copy(update={'params': sizing_hint(concept.family, concept.params, explicit)})
    progress('Sizing the robot and running the task' if resize else 'Running the task')
    if resize:
        concept, urdf, sim = await asyncio.to_thread(size_and_simulate, spec, requirements, scene, concept, base, explicit)
    else:
        urdf = old.urdf
        sim = await asyncio.to_thread(simulate, spec, requirements, scene, concept)
    progress(f'Task simulated: {sum(c.passed is True for c in sim.checks)} of {len(sim.checks)} checks pass')
    if plan_checks:
        sim, plan, rejected = await _planned_checks(llms, spec, requirements, scene, concept, sim, progress, old.check_plan)
    elif old.check_plan:
        sim, plan, rejected = _reevaluated(spec, requirements, scene, concept, sim, old.check_plan)
    else:
        plan, rejected = [], []
    return DesignVersion(version=0, parent_version=old.version, created=now(), change_request=request[:2000],
                         change_summary=f'{patch.summary} {checked_outcome(sim)}', spec=spec, requirements=requirements, scene=scene, robot=concept,
                         urdf=urdf, sim=sim, check_plan=plan, check_plan_rejected=rejected)


async def _revise_repair(llms, base_version, request, t, progress):
    """A repair round: like _revise_core, but the stored check plan is re-evaluated in code instead of asking again."""
    return await _revise_core(llms, base_version, request, t, progress, plan_checks=False)


# -- collision auto-repair --------------------------------------------------------------------------------------------

MAX_REPAIR_ATTEMPTS = 3
REQUEST_BUDGET = 1900           # the revise agent reads at most 2000 characters of a request
MAX_REPAIR_EVENTS = 8


def collision_events(v):
    """The collision events of the version's run, in time order. A fleet version: every member's scene collisions
    on the shared clock (prefixed with the member id) plus the robot-robot events."""
    if getattr(v, 'fleet', None) is None:
        return sorted([e for e in v.sim.events if e.kind == 'collision'], key=lambda e: e.t)
    events = [e.model_copy(update={'t': e.t + m.t_offset_s, 'text': f'{m.id}: {e.text}'})
              for m in v.fleet.members for e in m.sim.events if e.kind == 'collision']
    events += [e for e in v.fleet.events if e.kind == 'collision']
    return sorted(events, key=lambda e: e.t)


_CONTACTS = re.compile(r'^\s*(\d+) contact episode')


def collision_episodes(v):
    """Collision episodes in the version: the `collision_free` check's count; a fleet version adds up its members'
    counts and the robot-robot episodes (the fleet_robot_clearance check, else the robot-robot events)."""
    if getattr(v, 'fleet', None) is None:
        return episodes_from(next((c for c in v.sim.checks if c.requirement_id == 'collision_free'), None))
    total = sum(episodes_from(next((c for c in m.sim.checks if c.requirement_id == 'collision_free'), None))
                for m in v.fleet.members)
    rr = next((c for c in v.fleet.checks if c.requirement_id == 'fleet_robot_clearance'), None)
    hit = _CONTACTS.match(rr.detail or '') if rr is not None else None
    return total + (int(hit.group(1)) if hit else sum(e.kind == 'collision' for e in v.fleet.events))


def _all_checks(v):
    if getattr(v, 'fleet', None) is None:
        return v.sim.checks
    return [*v.fleet.checks, *[c for m in v.fleet.members for c in (*m.sim.checks, *m.limit_checks)]]


def checks_passed(v):
    return sum(c.passed is True for c in _all_checks(v))


def checks_total(v):
    return len(_all_checks(v))


def repair_request(v, attempts):
    """(text, t): the request code sends to the revise agent after a collision check, and the first collision time
    (clamped into the version's run) for the revise moment. `attempts` are the RepairAttempts so far."""
    n, events = collision_episodes(v), collision_events(v)
    i = len(attempts) + 1
    head = f'Automatic repair (attempt {i} of {MAX_REPAIR_ATTEMPTS}): the run of this version has {n} collision episode(s).'
    levers = ('Allowed changes: move the "robot_base" marker (the robot stands elsewhere); change the robot family or its '
              'dimensions; raise physics_inputs.approach_height_m; move or resize scene boxes that hold no task marker. '
              'Forbidden: moving any other marker, and changing objects, actions or the cycle time.')
    if getattr(v, 'fleet', None) is not None:
        levers = ('Allowed changes (target "fleet" or "scene"): move a robot\'s base (fleet members[].base); change a '
                  'robot\'s family or dimensions; move a handover station; move or resize scene boxes that hold no task '
                  'marker. Forbidden: changing the task (spec: markers, objects, actions, cycle time).')
    tail = []
    if attempts:
        tail.append('Earlier attempts (the model\'s own summaries; do something different):')
        tail += [f'- attempt {a.attempt}: {a.summary or "no summary"} -> {a.collisions if a.collisions is not None else "unusable"}'
                 f' collision(s) left ({a.outcome})' for a in attempts]
    shown = events[:MAX_REPAIR_EVENTS]
    while True:
        lines = [head]
        if shown:
            lines.append('Collisions:')
            lines += [f'- t={e.t:.2f}s: {e.text}' for e in shown]
        text = '\n'.join([*lines, levers, *tail])
        if len(text) <= REQUEST_BUDGET or not shown:
            break
        shown = shown[:-1]
    first = events[0].t if events else None
    t = None if first is None else min(max(float(first), 0.0), float(run_length(v)))
    return text[:REQUEST_BUDGET], t


def _without_levers(spec):
    d = spec.model_dump(mode='json')
    d['markers'] = [m for m in d['markers'] if m['id'] != 'robot_base']
    d['physics_inputs'].pop('approach_height_m', None)
    d.pop('assumptions', None)           # free text the model may extend while explaining its change
    return d


def task_unchanged(old_spec, new_spec):
    """None when only the `robot_base` marker, the approach height (and assumption notes) differ, else a reason."""
    a, b = _without_levers(old_spec), _without_levers(new_spec)
    if a == b:
        return None
    if a['markers'] != b['markers']:
        ids = {m['id'] for m in a['markers']} ^ {m['id'] for m in b['markers']}
        moved = sorted(ids | {x['id'] for x in a['markers'] for y in b['markers'] if x['id'] == y['id'] and x != y})
        return f'a task marker changed ({", ".join(moved)})'
    key = next(k for k in a if a[k] != b[k])
    if key == 'physics_inputs':
        key = 'physics_inputs.' + next(k for k in a[key] if a[key][k] != b[key].get(k))
    return f'the task changed ({key})'


def design_fingerprint(v):
    """sha256 of the design: spec, scene, robot family and params (and the fleet's assignment and members' robots)."""
    body = {'spec': v.spec.model_dump(mode='json'), 'scene': v.scene.model_dump(mode='json'),
            'robot': {'family': v.robot.family, 'params': v.robot.params, 'base': v.robot.base.model_dump(mode='json')}}
    if getattr(v, 'fleet', None):
        body['fleet'] = {'assignment': v.fleet.assignment.model_dump(mode='json'),
                         'members': [{'id': m.id, 'family': m.robot.family, 'params': m.robot.params,
                                      'base': m.robot.base.model_dump(mode='json')} for m in v.fleet.members]}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def task_of(v):
    """The task a repair must keep: the full task of a fleet version, else the spec."""
    return v.fleet.task if getattr(v, 'fleet', None) is not None else v.spec


def _quiet(_line):
    """Progress sink for the inner revise rounds of a repair (their lines would hide the repair's own)."""


def _final_summary(original, kept, sentence):
    """The original's summary with its checked-outcome sentence describing the kept version, plus the repair sentence."""
    old = original.change_summary or ''
    cut = old.rfind(CHECKED_PREFIX)
    base = old[:cut].rstrip() if cut >= 0 else old.strip()
    parts = [base] if base else []
    if cut >= 0:
        parts.append(checked_outcome(kept.sim))
    parts.append(sentence)
    return ' '.join(parts)


async def repair_collisions(llms, version, progress, revise_fn=None):
    """Return `version` unchanged when it has no collisions. Otherwise ask the revise agent up to MAX_REPAIR_ATTEMPTS
    times to move the robot (never the task), re-simulate in code and keep the best result: fewest collisions, then
    most checks passed, then the earliest. The result carries a RepairLog; the user's own step (version number,
    parent, request, time) is kept. Nothing the model says is trusted until code has re-simulated it."""
    before = collision_episodes(version)
    if before == 0:
        return version
    revise_fn = revise_fn or _revise_repair
    best, best_score, best_attempt = version, (before, -checks_passed(version), 0), 0
    seen, attempts = {design_fingerprint(version)}, []
    stop = 'max attempts'
    for i in range(1, MAX_REPAIR_ATTEMPTS + 1):
        text, t = repair_request(best, attempts)
        progress(f'Collisions found ({collision_episodes(best)}): automatic fix, attempt {i} of {MAX_REPAIR_ATTEMPTS}')
        att = {'attempt': i, 'request': text, 't': t}
        inner = _quiet if getattr(best, 'fleet', None) is None else (lambda line, i=i: progress(f'Attempt {i}: {line}'))
        try:
            cand = await revise_fn(llms, best, text, t, inner)   # a fleet round is long: its lines keep the job alive
        except (ValueError, LLMError) as error:
            attempts.append(RepairAttempt(**att, outcome='invalid', note=str(error)[:600]))
            progress(f'Attempt {i}: the proposed change was not usable')
            continue
        att['summary'] = cand.change_summary.split(CHECKED_PREFIX)[0].strip()[:600]
        why = task_unchanged(task_of(version), task_of(cand))
        if why:
            attempts.append(RepairAttempt(**att, outcome='changed_task', note=why[:600]))
            progress(f'Attempt {i}: discarded, {why}')
            continue
        print_ = design_fingerprint(cand)
        if print_ in seen:
            attempts.append(RepairAttempt(**att, outcome='no_change', note='The design is identical to an earlier one.'))
            stop = 'model changed nothing'
            progress(f'Attempt {i}: the model changed nothing')
            break
        seen.add(print_)
        c, p = collision_episodes(cand), checks_passed(cand)
        att.update(collisions=c, checks_passed=p, checks_total=checks_total(cand))
        progress(f'Attempt {i}: {c} collision(s) left, {p} of {checks_total(cand)} checks pass')
        score = (c, -p, i)
        better = score < best_score
        if better:
            best, best_score, best_attempt = cand, score, i
        attempts.append(RepairAttempt(**att, outcome='worse'))
        if c == 0:
            stop = 'collision free'
            break
    attempts = [a.model_copy(update={'outcome': 'kept'}) if a.attempt == best_attempt else a for a in attempts]
    log = RepairLog(collisions_before=before, attempts=attempts, kept=best_attempt, stop_reason=stop)
    left = collision_episodes(best)
    sentence = (f'Automatic collision repair: kept attempt {best_attempt} of {len(attempts)} ({left} collision(s) left).'
                if best_attempt else 'Automatic collision repair: no attempt removed the collisions; the original design is kept.')
    return best.model_copy(update={
        'version': version.version, 'parent_version': version.parent_version, 'change_request': version.change_request,
        'created': version.created, 'repair': log, 'change_summary': _final_summary(version, best, sentence)})
