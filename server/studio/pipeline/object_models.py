"""Object models for a design: make them (non-fatal), attach them to the scene, reuse them across revisions.

A model is decoration: when the objects agent fails for any reason the scene keeps its plain objects and no progress
line is written, so a design never fails or changes because of it."""
from ..agents import objects as objects_agent
from ..llm.usage import set_context


def same_object(a, b):
    """True when two task objects would get the same model (name, size and shape unchanged)."""
    return a.name == b.name and tuple(a.size_m) == tuple(b.size_m) and a.shape == b.shape


async def make_models(llms, spec, objects=None):
    """{object id: ObjectModel} for `objects` (default: all of the spec's); {} on any failure or when none are asked."""
    objects = list(spec.objects if objects is None else objects)
    if not objects:
        return {}
    try:
        set_context(task='objects')
        return await objects_agent.run(llms.for_role('objects'), objects, spec.summary or spec.title)
    except Exception:       # decoration only: an LLM error, a rejected answer or a missing role never fails the design
        return {}


def attach(scene, spec, models):
    """The scene with each model on the object of the same id, whose name is set from the spec's object."""
    if not models:
        return scene
    names = {o.id: o.name for o in spec.objects}
    objs = [o.model_copy(update={'name': names.get(o.id, o.name), 'model': models[o.id]}) if o.id in models else o
            for o in scene.objects]
    return scene.model_copy(update={'objects': objs})


def carry(scene, source):
    """The scene with the name and model of the same-id objects of `source` (an earlier scene) where it has none."""
    have = {o.id: o for o in source.objects if o.model is not None}
    objs = [o.model_copy(update={'name': have[o.id].name, 'model': have[o.id].model}) if o.model is None and o.id in have else o
            for o in scene.objects]
    return scene.model_copy(update={'objects': objs})


def strip(scene):
    """The scene without names and models (what the scene and revise agents see and answer with)."""
    return scene.model_copy(update={'objects': [o.model_copy(update={'name': '', 'model': None}) for o in scene.objects]})


async def models_for(llms, spec, scene, old=None, progress=None):
    """The scene with models attached. With `old` (the version this one is revised from) the models of objects whose
    (name, size, shape) did not change are reused and the agent only runs for the new or changed ones."""
    reused = {}
    if old is not None:
        before = {o.id: o for o in old.spec.objects}
        held = {o.id: o.model for o in old.scene.objects if o.model is not None}
        reused = {o.id: held[o.id] for o in spec.objects if o.id in held and o.id in before and same_object(before[o.id], o)}
    todo = [o for o in spec.objects if o.id not in reused]
    made = await make_models(llms, spec, todo) if todo else {}
    if made and progress is not None:
        progress(f'Object models: {len(made)} made')
    return attach(strip(scene), spec, {**reused, **made})
