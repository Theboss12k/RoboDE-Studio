"""Brief -> spec -> requirements -> scene + robot (concurrently) -> sized robot -> URDF -> simulation -> DesignVersion.

Numbers that decide pass/fail come from code: requirements from physics/, sizes from the family, checks from sim/.
Synchronous numeric work runs in a worker thread so the server stays responsive.
"""
import asyncio

from .. import families
from ..agents import revise as revise_agent
from ..agents import robot as robot_agent
from ..agents import scene as scene_agent
from ..agents import spec as spec_agent
from ..llm.base import supports_images
from ..llm.usage import set_context
from ..physics import derive, place_base
from ..schemas import DesignVersion
from ..sim.task import simulate
from ..util import now, slug


def merge(base, over):
    """Recursive dict merge: values of `over` win."""
    out = dict(base)
    for k, v in over.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def size_and_simulate(spec, requirements, scene, concept, base, explicit=None):
    """Size the robot from physics starting at concept.params, apply explicit user numbers on top, build the URDF
    and run the task. Returns (concept, urdf, sim)."""
    family = families.get(concept.family)
    params = family.size_from_physics(requirements, spec, concept.params).model_dump()
    if explicit:
        params = family.parse(merge(params, explicit)).model_dump()
    sized = concept.model_copy(update={'params': params, 'base': base})
    urdf = family.build_urdf(family.parse(params), slug(sized.name))
    sim = simulate(spec, requirements, scene, sized)
    return sized, urdf, sim


async def specify(llms, project, answers, progress):
    """One step of POST /design: ask questions, or (spec complete) design. Returns the job result."""
    meta = project.meta
    merged = {**meta['answers'], **(answers or {})}
    if project.spec is None:
        round_ = meta['round'] + 1
        progress('Reading your brief' if round_ == 1 else f'Updating the specification (round {round_})')
        set_context(task='spec')
        adapter = llms.for_role('spec')
        reference = project.reference_for_agent()
        if reference and reference['images'] and not supports_images(adapter):
            progress('The spec LLM cannot read images; the photos are not used')
            reference['images'] = []
        result = await spec_agent.run(adapter, project.brief, reference, merged, round_)
        if 'questions' in result:
            questions = [q.model_dump(mode='json') for q in result['questions']]
            project.update(questions=questions, answers=merged, round=round_, stage='questions')
            return {'status': 'questions', 'questions': questions}
        project.update(spec=result['spec'].model_dump(mode='json'), answers=merged, round=round_, questions=[],
                       title=result['spec'].title[:80])
    version = await design(llms, project, progress)
    return {'status': 'designed', 'version': version.version}


async def design(llms, project, progress):
    spec = project.spec
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
    scene, concept = await asyncio.gather(scene_task(), robot_task())
    progress(f'Sizing the {concept.family.replace("_", " ")} from the requirements')
    concept, urdf, sim = await asyncio.to_thread(size_and_simulate, spec, requirements, scene, concept, base)
    progress(f'Task simulated: {sum(c.passed is True for c in sim.checks)} of {len(sim.checks)} checks pass')
    return project.add_version(lambda n: DesignVersion(version=n, created=now(), spec=spec, requirements=requirements, scene=scene,
                                                       robot=concept, urdf=urdf, sim=sim))


def markers_moved(old, new):
    a = {m.id: m.position for m in old.markers}
    b = {m.id: m.position for m in new.markers}
    return a.keys() != b.keys() or any(max(abs(x - y) for x, y in zip(a[k], b[k])) > 1e-6 for k in a)


async def revise(llms, project, request, progress):
    old = project.latest()
    if old is None:
        raise ValueError('There is no design to change yet')
    request = (request or '').strip()
    if not request:
        raise ValueError('Say what to change')
    progress('Working out what the change touches')
    set_context(task='revise')
    patch = await revise_agent.run(llms.for_role('revise'), old, request)
    spec, scene, concept = old.spec, old.scene, old.robot
    base, requirements = old.robot.base, old.requirements
    if patch.spec:
        progress('Re-deriving the requirements')
        spec = patch.spec
        base = place_base(spec)
        requirements = derive(spec, base)
    if patch.scene:
        scene = patch.scene
    elif patch.spec and markers_moved(old.spec, spec):
        progress('Markers moved: rebuilding the scene')
        set_context(task='scene')
        scene = await scene_agent.run(llms.for_role('scene'), spec, base)
    scene = scene.model_copy(update={'robot_base': base})
    explicit = None
    if patch.robot:
        if patch.robot.family == old.robot.family:
            explicit = {k: v for k, v in patch.robot.params.items() if old.robot.params.get(k) != v}
        concept = patch.robot.model_copy(update={'base': base})
    resize = patch.robot is not None or patch.spec is not None
    progress('Sizing the robot and running the task' if resize else 'Running the task')
    if resize:
        concept, urdf, sim = await asyncio.to_thread(size_and_simulate, spec, requirements, scene, concept, base, explicit)
    else:
        urdf = old.urdf
        sim = await asyncio.to_thread(simulate, spec, requirements, scene, concept)
    progress(f'Task simulated: {sum(c.passed is True for c in sim.checks)} of {len(sim.checks)} checks pass')
    return project.add_version(lambda n: DesignVersion(version=n, created=now(), change_request=request[:2000], change_summary=patch.summary,
                                                       spec=spec, requirements=requirements, scene=scene, robot=concept, urdf=urdf, sim=sim))
