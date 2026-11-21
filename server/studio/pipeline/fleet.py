"""Multi-robot (fleet) designs: assignment preview, the fleet design, fleet revisions and member views.

Pure like pipeline/design.py: plain inputs and `llms` (any object with `for_role(role)`) in, results out, no store.
Pass/fail numbers come from code: the planner (fleet/), the families' sizing and the simulator. The robot agent only
proposes a family and starting params per member; code sizes, simulates and checks them, and sends the failing
checks back with their numbers (at most MAX_ROBOT_RECHOICE times).

A fleet version's top-level spec, requirements, robot, urdf and sim are the PRIMARY member's complete sub-task (so
search, analysis and export keep working on one robot); `fleet` holds the full task, the plan and every member.
"""
import asyncio
import math
from dataclasses import dataclass, field

from .. import families
from ..agents import revise as revise_agent
from ..agents import robot as robot_agent
from ..agents import scene as scene_agent
from . import object_models
from ..agents.spec import validate_spec
from ..fleet import patch as fleet_patch
from ..fleet.assign import auto_assign, validate_assignment
from ..fleet.checks import fleet_checks, grasp_point, handover_reachable, member_limit_checks
from ..fleet.fit import allowed_families, sized_reach
from ..fleet.layout import plan_layout
from ..fleet.schedule import fleet_period, offsets, resolve_by_delay, robot_clearance
from ..fleet.subspec import handover_marker_id, member_spec, station_boxes
from ..ingest import urdf as urdf_intake
from ..llm.usage import set_context
from ..physics import derive, place_base
from ..schemas import (DesignVersion, FleetAssignment, FleetLimits, FleetMember, FleetPlan, Marker, RobotConcept,
                       SceneSpec)
from ..sim import report
from ..util import now

MAX_ROBOT_RECHOICE = 2          # re-choice rounds for members that fail their limit, reach or handover checks
MAX_WORKSPACE_SAMPLES = 300     # per member in the assignment preview
STATION_PREFIX = 'station_'      # ids of the stands code adds (fleet.subspec.station_boxes)
STATION_NAME = 'Handover station'


def task_of(version: DesignVersion):
    """The full task: the fleet's task on a fleet version, else the version's spec."""
    return version.fleet.task if version.fleet is not None else version.spec


def limits_of(value) -> FleetLimits:
    if isinstance(value, FleetLimits):
        return value
    return FleetLimits.model_validate(value or {})


def assignment_of(value) -> FleetAssignment | None:
    if value is None or isinstance(value, FleetAssignment):
        return value
    return FleetAssignment.model_validate(value)


def _r3(xyz):
    return [round(float(c), 4) for c in xyz]


def _world(base, pts):
    c, s = math.cos(base.yaw), math.sin(base.yaw)
    x0, y0, z0 = base.position
    return [[round(x0 + c * p[0] - s * p[1], 3), round(y0 + s * p[0] + c * p[1], 3), round(z0 + p[2], 3)] for p in pts]


def _with_positions(a: FleetAssignment, layout) -> FleetAssignment:
    """The assignment with every handover's planned position (user-fixed ones unchanged)."""
    return a.model_copy(update={'handovers': [h.model_copy(update={'position': tuple(layout.handovers[h.id])})
                                              if h.id in layout.handovers else h for h in a.handovers]})


# -- assignment preview (fleet_assign, no LLM) -------------------------------------------------------------------------

def assign(version: DesignVersion, limits, assignment=None) -> dict:
    """The automatic (assignment None) or the user's validated assignment, a preview of the layout (bases, handover
    stations, park poses, workspace samples of a nominal robot per member, world frame), `feasible` and notes from a
    quick sizing check. A user assignment that does not fit the task raises ValueError."""
    task = task_of(version)
    limits = limits_of(limits)
    user = assignment_of(assignment)
    a = validate_assignment(task, user if user is not None else auto_assign(task, limits), limits)
    layout = plan_layout(task, a, limits)
    notes = [f'{"Your" if user is not None else "Automatic"} split: {len(a.members)} robots, '
             f'{len(a.handovers)} handover(s).']
    feasible = True
    bases, parks, samples = {}, {}, {}
    for m in a.members:
        spec = member_spec(task, a, layout, m.id)
        reqs = derive(spec, place_base(spec))
        fams = allowed_families(limits, m.family)
        reach = {f: sized_reach(spec, f) for f in fams}
        best = min(reach, key=reach.get) if reach else 'serial_arm'
        limit = limits.max_reach_m
        text = ', '.join(f'{f.replace("_", " ")} {r:.2f} m' for f, r in reach.items())
        if limit is not None:
            ok = reach.get(best, math.inf) <= limit + 1e-9
            feasible &= ok
            notes.append(f'{m.id}: sized reach {text}; your limit {limit:.2f} m'
                         + ('' if ok else ' (no allowed type fits)'))
        else:
            notes.append(f'{m.id}: sized reach {text}')
        pay = next((r.target for r in reqs if r.id == 'payload'), None)
        if limits.max_payload_kg is not None and pay is not None and pay > limits.max_payload_kg + 1e-9:
            feasible = False
            notes.append(f'{m.id}: needs a payload of {pay:.1f} kg (with tool and safety factor); your limit is '
                         f'{limits.max_payload_kg:g} kg')
        fam = families.get(best)
        p = fam.size_from_physics(reqs, spec, {})
        ws = fam.workspace(p, n=MAX_WORKSPACE_SAMPLES) if best == 'serial_arm' else fam.workspace(p)
        b = layout.bases[m.id]
        samples[m.id] = _world(b, ws['samples'][:MAX_WORKSPACE_SAMPLES])
        bases[m.id] = {'position': _r3(b.position), 'yaw': round(float(b.yaw), 4)}
        start, end = layout.parks[m.id]
        parks[m.id] = {'start': _r3(start), 'end': _r3(end)}
    if not layout.converged:
        notes.append(layout.log[-1])
    notes += _mount_notes(version.scene, layout)
    notes += [line for line in layout.log if line.startswith(('Bases of', 'The base of'))]
    preview = {'bases': bases, 'handovers': {k: _r3(v) for k, v in sorted(layout.handovers.items())},
               'parks': parks, 'workspace_samples': samples}
    return {'assignment': _with_positions(a, layout).model_dump(mode='json'), 'preview': preview,
            'feasible': bool(feasible), 'notes': notes}


def _mount_notes(scene, layout) -> list[str]:
    """Notes for bases above the floor that stand on no surface of the base version's scene (the fleet's own scene is
    built around the planned bases, so this is a warning, not a failure)."""
    out = []
    for mid, b in sorted(layout.bases.items()):
        x, y, z = b.position
        if z <= 0.02:
            continue
        if not any(abs(bx.center[2] + bx.size[2] / 2 - z) <= 0.02 and scene_agent.rect_distance(bx, x, y) == 0
                   for bx in scene.boxes if bx.collides and not bx.id.startswith(STATION_PREFIX)):
            out.append(f'{mid}: its base at ({x:.2f}, {y:.2f}, {z:.2f}) is on no surface of the current scene; the '
                       'fleet scene will add a mount there')
    return out


# -- planning a fleet ---------------------------------------------------------------------------------------------------

@dataclass
class _Member:
    id: str
    name: str
    spec: object
    reqs: list
    base: object
    prefer: str = ''                      # the user's family for this member ('' = any allowed)
    concept: RobotConcept | None = None   # sized
    urdf: str = ''
    sim: object = None
    limit_checks: list = field(default_factory=list)
    reasons: list = field(default_factory=list)


def _scene_core(scene: SceneSpec) -> SceneSpec:
    """A shared scene without the stands and markers code adds for handover stations."""
    return scene.model_copy(update={'boxes': [b for b in scene.boxes if not b.id.startswith(STATION_PREFIX)],
                                    'markers': [m for m in scene.markers if not m.name.startswith(STATION_NAME)]})


def _full_scene(core: SceneSpec, task, a, layout, primary) -> SceneSpec:
    stations = station_boxes(layout, boxes=core.boxes)
    marks = [Marker(id=handover_marker_id(task, h.id), name=f'{STATION_NAME} {h.id}', position=layout.handovers[h.id],
                    note=f'{h.object} from {h.from_member} to {h.to_member}') for h in a.handovers]
    ids = {m.id for m in core.markers}
    return core.model_copy(update={'boxes': [*core.boxes, *stations],
                                   'markers': [*core.markers, *[m for m in marks if m.id not in ids]],
                                   'robot_base': layout.bases[primary]})


def _scene_fits(core, task, bases, primary, stations):
    try:
        scene_agent.validate_scene(core, task, bases[primary], [b for k, b in bases.items() if k != primary])
        scene_agent._check_stations(core, stations)
        return True
    except ValueError:
        return False


def _reasons(m: _Member, task, a, layout, limits):
    """Plain reasons (with code's numbers) why a member's robot needs another choice; [] = fine."""
    out = []
    by = {c.requirement_id: c for c in m.limit_checks}
    c = by.get('limit_family')
    if c is not None and c.passed is False:
        out.append(f'limit_family fails: {c.detail}')
    c = by.get('limit_reach')
    if c is not None and c.passed is False:
        out.append(f'limit_reach fails: code sized this {m.concept.family.replace("_", " ")} to a reach of '
                   f'{c.actual:.2f} m; the user allows at most {c.required:.2f} m')
    c = next((c for c in m.sim.checks if c.requirement_id == 'reach_max'), None)
    if c is not None and c.passed is False:
        out.append(f'reach_max fails: {c.detail}')
    appr = task.physics_inputs.approach_height_m
    for h in a.handovers:
        if m.id in (h.from_member, h.to_member):
            ok, why = handover_reachable(m.concept, m.base, grasp_point(task, h.object, layout.handovers[h.id]), appr)
            if not ok:
                out.append(f'handover {h.id} not reachable: {why}')
    return out


def _feedback(m: _Member, limits):
    reach = {f: sized_reach(m.spec, f) for f in allowed_families(limits, m.prefer)}
    text = (f'Your previous choice for {m.id}: {m.concept.family} ("{m.concept.name}"). Code sized and checked it:\n- '
            + '\n- '.join(m.reasons))
    text += ('\nCode-sized reach for this member\'s share of the task, by robot type: '
             + ', '.join(f'{f} {r:.2f} m' for f, r in reach.items()))
    if limits.max_reach_m is not None:
        text += f'; the user allows at most {limits.max_reach_m:.2f} m.'
    return text


async def _choose(llms, m: _Member, limits, feedback=None):
    set_context(task='robot')
    own = limits.model_copy(update={'families': [m.prefer]}) if m.prefer else limits
    return await robot_agent.run(llms.for_role('robot'), m.spec, m.reqs, families.describe(), limits=own,
                                 feedback=feedback, member=m.id)


async def _size(m: _Member, concept, scene, task, a, layout, limits, explicit, progress):
    from .design import size_and_simulate       # design imports this module for revise dispatch
    progress(f'Sizing {m.id}: {concept.family.replace("_", " ")} for its share of the task')
    sized, urdf, sim = await asyncio.to_thread(size_and_simulate, m.spec, m.reqs, scene, concept, m.base, explicit)
    m.concept, m.urdf, m.sim = sized, urdf, sim
    m.limit_checks = member_limit_checks(sized, m.reqs, limits)
    m.reasons = _reasons(m, task, a, layout, limits)


def fleet_sentence(plan: FleetPlan) -> str:
    """One code sentence on the plan: robots, handovers and the fleet checks."""
    n, k = len(plan.members), len(plan.assignment.handovers)
    checks = plan.checks
    passed = sum(c.passed is True for c in checks)
    own = sum(report.all_ok([*m.sim.checks, *m.limit_checks]) for m in plan.members)
    text = (f'Multi-robot layout: {n} robots, {k} handover{"s" if k != 1 else ""}; {passed} of {len(checks)} fleet '
            f'checks pass and {own} of {n} robots pass their own checks')
    failed = [c.requirement_id for c in checks if c.passed is False]
    return text + (f' (failing: {", ".join(failed)})' if failed else '') + '.'


async def _plan(llms, task, limits, a, progress, *, scene=None, robots=None, explicit=None, locked=(), replan=True,
                primary=None, log=None):
    """(FleetPlan, scene, primary FleetMember) for a validated assignment. `robots` {mid: RobotConcept} skips the
    robot agent for those members (revise); `explicit` {mid: params} are the user's numbers; `locked` members are
    never re-chosen; `scene` is a shared scene to reuse when it still fits (else the scene agent builds one)."""
    robots, explicit, log = dict(robots or {}), dict(explicit or {}), list(log or [])
    ids = [m.id for m in a.members]
    primary = primary if primary in ids else ('r1' if 'r1' in ids else ids[0])
    layout = plan_layout(task, a, limits)
    log += layout.log
    progress(f'Laying out the cell: {len(ids)} robots, {len(a.handovers)} handover(s), '
             f'{"settled" if layout.converged else "not settled"} after {layout.iterations} iteration(s)')
    members = {}
    for m in a.members:
        spec = validate_spec(member_spec(task, a, layout, m.id))
        base = place_base(spec)
        members[m.id] = _Member(m.id, m.name, spec, derive(spec, base), base, m.family)
    bases = {i: members[i].base for i in ids}
    core = _scene_core(scene) if scene is not None else None
    reuse = core is not None and _scene_fits(core, task, bases, primary, layout.handovers)
    if core is not None and not reuse:
        progress('The robots or stations moved: rebuilding the shared scene')

    async def scene_task():
        if reuse:
            return core
        set_context(task='scene')
        built = await scene_agent.run(llms.for_role('scene'), task, bases[primary],
                                      [b for i, b in bases.items() if i != primary], layout.handovers)
        return _scene_core(built)
    asking = [i for i in ids if i not in robots]
    progress('Building the shared scene and choosing the robots' if asking and not reuse else
             'Building the shared scene' if not reuse else f'Choosing robots for {", ".join(asking)}' if asking
             else 'Keeping the scene and the robots')
    results = await asyncio.gather(scene_task(), *[_choose(llms, members[i], limits) for i in asking])
    core, chosen = results[0], dict(zip(asking, results[1:]))
    full = _full_scene(core, task, a, layout, primary)
    for i in ids:
        concept = robots.get(i) or chosen[i]
        await _size(members[i], concept, full, task, a, layout, limits, explicit.get(i), progress)

    for rnd in range(1, MAX_ROBOT_RECHOICE + 1):
        failing = [i for i in ids if members[i].reasons and i not in locked]
        if not failing:
            break
        progress(f'Choosing again for {", ".join(failing)} (round {rnd} of {MAX_ROBOT_RECHOICE}): '
                 f'{members[failing[0]].reasons[0]}')
        log.append(f'Re-choice round {rnd}: ' + '; '.join(f'{i}: {members[i].reasons[0]}' for i in failing))
        answers = await asyncio.gather(*[_choose(llms, members[i], limits, _feedback(members[i], limits))
                                         for i in failing])
        for i, concept in zip(failing, answers):
            old = members[i]
            trial = _Member(old.id, old.name, old.spec, old.reqs, old.base, old.prefer)
            await _size(trial, concept, full, task, a, layout, limits, explicit.get(i), progress)
            if len(trial.reasons) <= len(old.reasons):
                members[i] = trial
            log.append(f'{i}: {concept.family} chosen again; ' +
                       ('now passes its limit, reach and handover checks' if not trial.reasons else
                        f'still failing: {trial.reasons[0]}') + ('' if members[i] is trial else '; the earlier choice is kept'))

    still = [i for i in ids if members[i].reasons]
    if still and replan and a.source == 'auto' and not a.confirmed and len(ids) < limits.max_robots:
        k = len(ids) + 1
        try:
            a2 = validate_assignment(task, auto_assign(task, limits, min_robots=k), limits)
        except ValueError as error:
            log.append(f'Re-planning with {k} robots was not possible: {error}')
        else:
            if len(a2.members) > len(ids):
                progress(f'{", ".join(still)} still fail{"s" if len(still) == 1 else ""}: re-planning with {k} robots')
                log.append(f'Re-planned with {len(a2.members)} robots because {", ".join(still)} still failed '
                           f'({members[still[0]].reasons[0]})')
                return await _plan(llms, task, limits, a2, progress, scene=core, replan=False, primary=primary, log=log)
    if still:
        log.append('Still failing after the re-choices (reported as checks): ' + ', '.join(still))

    fm = [FleetMember(id=i, name=members[i].name, spec=members[i].spec, requirements=members[i].reqs,
                      robot=members[i].concept, urdf=members[i].urdf, sim=members[i].sim,
                      limit_checks=members[i].limit_checks) for i in ids]
    progress('Scheduling the robots on a shared clock and checking robot-robot clearance')
    off = offsets(fm, a)
    off, delay_log = await asyncio.to_thread(resolve_by_delay, fm, off, task.physics_inputs.cycle_time_s, 0.2, progress)
    log += delay_log
    clearance = await asyncio.to_thread(robot_clearance, fm, off)
    fm = [m.model_copy(update={'t_offset_s': round(float(off[m.id]), 6)}) for m in fm]
    checks = fleet_checks(task, a, layout, fm, clearance, limits)
    episodes, events, _gap = clearance
    plan = FleetPlan(task=task, limits=limits, assignment=_with_positions(a, layout), members=fm, primary=primary,
                     checks=checks, events=events, duration_s=max(m.t_offset_s + m.sim.duration_s for m in fm),
                     period_s=fleet_period(fm),
                     ok=report.all_ok(checks) and all(report.all_ok([*m.sim.checks, *m.limit_checks]) for m in fm),
                     planner_log=[line[:400] for line in log][-80:])
    progress(fleet_sentence(plan))
    return plan, full, next(m for m in fm if m.id == primary)


def _version(plan, scene, prim, parent, change_request='', summary=''):
    return DesignVersion(version=0, parent_version=parent, created=now(), change_request=change_request[:2000],
                         change_summary=summary, spec=prim.spec, requirements=prim.requirements, scene=scene,
                         robot=prim.robot, urdf=prim.urdf, sim=prim.sim, fleet=plan)


async def design_fleet(llms, base: DesignVersion, limits, assignment, progress, primary=None, trigger=None,
                       auto_repair=True) -> DesignVersion:
    """A fleet DesignVersion (placeholder number 0, parent = base.version) for the base version's task. A missing
    assignment is planned automatically; a user assignment is validated (ValueError) and never changed silently."""
    from .design import repair_collisions
    task = task_of(base)
    limits = limits_of(limits)
    user = assignment_of(assignment)
    a = validate_assignment(task, user if user is not None else auto_assign(task, limits), limits)
    progress(f'Planning {len(a.members)} robots for "{task.title}"')
    plan, scene, prim = await _plan(llms, task, limits, a, progress,
                                    scene=base.scene if base.fleet is not None else None, primary=primary)
    plan = plan.model_copy(update={'trigger': list(trigger) if trigger else
                                   (list(base.fleet.trigger) if base.fleet is not None else ['user'])})
    scene = object_models.carry(scene, base.scene)                 # the base version's object models
    version = _version(plan, scene, prim, base.version, summary=fleet_sentence(plan))
    return await repair_collisions(llms, version, progress) if auto_repair else version


# -- revising a fleet version -------------------------------------------------------------------------------------------

async def revise_fleet(llms, old: DesignVersion, request: str, t, progress) -> DesignVersion:
    """One revise round on a fleet version (no collision repair): the revise agent proposes a spec (the full task),
    scene and/or fleet patch; code applies it to the planner's inputs as user-fixed values, re-plans, re-sizes,
    re-simulates and re-checks."""
    from .design import sizing_hint
    f = old.fleet
    progress('Working out what the change touches')
    set_context(task='revise')
    patch = await revise_agent.run(llms.for_role('revise'), old, request, t)
    task = patch.spec or f.task
    current = {m.id: m.robot for m in f.members}
    limits, a, changed = fleet_patch.apply(task, f.limits, f.assignment, current, patch.fleet or fleet_patch.FleetPatch(),
                                           task_changed=patch.spec is not None)
    robots, explicit = {}, {}
    for m in a.members:
        prev = current.get(m.id)
        if m.id in changed:
            family, params = changed[m.id]
            params = dict(params or {})
            if prev is not None and prev.family == family:
                explicit[m.id] = {k: v for k, v in params.items() if prev.params.get(k) != v} or None
                robots[m.id] = prev.model_copy(update={'params': sizing_hint(family, prev.params, explicit[m.id])})
            else:
                robots[m.id] = RobotConcept(family=family, name=f'{family.replace("_", " ").capitalize()} {m.id}',
                                            rationale=f'Chosen by the user\'s change request for {m.id}.', params=params)
        elif prev is not None:
            robots[m.id] = prev.model_copy(update={'params': sizing_hint(prev.family, prev.params)})
    progress('Re-planning the robots' + (f' ({", ".join(sorted(changed))} changed)' if changed else ''))
    plan, scene, prim = await _plan(llms, task, limits, a, progress, scene=patch.scene or old.scene, robots=robots,
                                    explicit=explicit, locked=set(changed), replan=False, primary=f.primary,
                                    log=[f'Revised from version {old.version}: {patch.summary}'])
    plan = plan.model_copy(update={'trigger': list(f.trigger)})
    scene = object_models.carry(scene, old.scene)
    return _version(plan, scene, prim, old.version, change_request=request,
                    summary=f'{patch.summary} {fleet_sentence(plan)}')


# -- member views ----------------------------------------------------------------------------------------------------

def member_view(version: DesignVersion, member_id: str | None) -> DesignVersion:
    """The version as one member sees it: that member's spec, requirements, robot, URDF and run in the top-level
    fields (the scene's robot base is the member's). No member, or the primary on a fleet version, changes nothing."""
    if not member_id:
        return version
    if version.fleet is None:
        if member_id == 'r1':
            return version
        raise ValueError(f'This design has one robot; there is no robot {member_id}')
    m = next((m for m in version.fleet.members if m.id == member_id), None)
    if m is None:
        raise KeyError(f'Robot {member_id} not found in this design (robots: '
                       f'{", ".join(x.id for x in version.fleet.members)})')
    return version.model_copy(update={'spec': m.spec, 'requirements': m.requirements, 'robot': m.robot, 'urdf': m.urdf,
                                      'sim': m.sim, 'scene': version.scene.model_copy(update={'robot_base': m.robot.base})})


def fleet_parsed(version: DesignVersion) -> dict:
    """{member id: parsed URDF} of a fleet version ({} for one robot)."""
    if version.fleet is None:
        return {}
    return {m.id: urdf_intake.parse_urdf(m.urdf.encode()) for m in version.fleet.members}


