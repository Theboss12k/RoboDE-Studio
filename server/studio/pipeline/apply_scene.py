"""Apply the user's scene edits (moved base, moved markers, added boxes) as a new design version, computed in code.

No LLM is used, except by the optional collision repair when the caller passes `llms` and sets `auto_repair`.
Pure like the rest of pipeline/: plain inputs in, a DesignVersion out, no project or store access.
Frames and units: world frame, metres (docs/SITE_SCAN.md, apply_scene).
"""
import asyncio
import math

from ..agents import scene as scene_agent
from ..agents import spec as spec_agent
from ..agents.revise import BASE_MOVE_M
from ..physics import derive, place_base
from ..schemas import DesignVersion, Marker, SceneSpec
from ..util import now
from .design import checked_outcome, collision_episodes, repair_collisions, size_and_simulate, sizing_hint

BASE_MARKER = 'robot_base'
MOVED_M = 1e-6          # a marker counts as moved beyond this distance
PREFIX = 'Edited in the site scan'


class SceneInvalid(ValueError):
    """The edited scene or spec does not validate. `code` is the protocol error code."""
    code = 'scene_invalid'


def _position(item):
    try:
        pos = tuple(float(c) for c in item['position'])
    except (KeyError, TypeError, ValueError) as error:
        raise SceneInvalid(f'Marker "{item.get("id") if isinstance(item, dict) else item}" needs a position [x, y, z]') from error
    if len(pos) != 3 or not all(math.isfinite(c) for c in pos):
        raise SceneInvalid(f'Marker "{item["id"]}" needs three finite numbers [x, y, z]')
    return pos


def apply_markers(spec, markers):
    """The spec with the given marker positions. `robot_base` is added when the spec has none (the user placed the robot).
    Unknown ids raise SceneInvalid."""
    new = {m.id: m for m in spec.markers}
    for item in markers or []:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str):
            raise SceneInvalid('Every marker needs an id and a position')
        mid, pos = item['id'], _position(item)
        if mid in new:
            new[mid] = new[mid].model_copy(update={'position': pos})
        elif mid == BASE_MARKER:
            if spec.locomotion.required:
                raise SceneInvalid('This robot drives along a path; its base is the first path point, not a robot_base marker')
            new[mid] = Marker(id=BASE_MARKER, name='Robot base', position=pos, note='Mounting point of the robot base')
        else:
            raise SceneInvalid(f'Unknown marker "{mid}"; markers are {sorted(new)}')
    return spec.model_copy(update={'markers': list(new.values())})


def place_objects(spec, scene):
    """Scene objects moved to their start marker: x, y of the marker, bottom on the marker's z (as the scene agent builds
    them: centre z = marker z + size z / 2). Objects without a start marker stay where they are."""
    start = {o.id: o.start_marker for o in spec.objects if o.start_marker}
    objects = []
    for o in scene.objects:
        mid = start.get(o.id)
        if mid:
            x, y, z = spec.marker(mid).position
            o = o.model_copy(update={'center': (x, y, z + o.size[2] / 2)})
        objects.append(o)
    return objects


def _moved(a, b, tol):
    return max(abs(x - y) for x, y in zip(a, b)) > tol


async def apply_scene(base: DesignVersion, scene: SceneSpec, markers: list, auto_repair: bool, note: str,
                      progress=None, llms=None) -> DesignVersion:
    """A new DesignVersion (placeholder number 0, parent_version = base.version) from the user's edited scene boxes and the
    given marker positions. Nothing the user changed is decided by a model: validation, base placement, requirements, sizing
    and the checks are code. `llms` is used only for the collision repair, and only when `auto_repair` is set."""
    progress = progress or (lambda _text: None)
    note = (note or '').strip().rstrip('.')
    old = base
    progress('Applying the scene edits')
    spec = apply_markers(old.spec, markers)
    place = place_base(spec)
    scene = scene.model_copy(update={'markers': [m.model_copy() for m in spec.markers], 'robot_base': place})
    scene = scene.model_copy(update={'objects': place_objects(spec, scene)})
    try:
        spec_agent.validate_spec(spec)
        scene_agent.validate_scene(scene, spec, place)
    except ValueError as error:
        raise SceneInvalid(str(error)) from error
    progress('Re-deriving the requirements')
    requirements = derive(spec, place)
    base_moved = _moved(place.position, old.robot.base.position, BASE_MOVE_M)
    old_markers = {m.id: m.position for m in old.spec.markers}
    markers_moved = base_moved or any(m.id not in old_markers or _moved(m.position, old_markers[m.id], MOVED_M)
                                      for m in spec.markers)
    concept = old.robot.model_copy(update={'base': place})
    if markers_moved:           # the task points changed: re-size instead of keeping the old sizes, as revise does
        concept = concept.model_copy(update={'params': sizing_hint(concept.family, concept.params)})
    progress('Sizing the robot and running the task')
    concept, urdf, sim = await asyncio.to_thread(size_and_simulate, spec, requirements, scene, concept, place)
    progress(f'Task simulated: {sum(c.passed is True for c in sim.checks)} of {len(sim.checks)} checks pass')
    head = f'{PREFIX}: {note}' if note else PREFIX
    version = DesignVersion(version=0, parent_version=old.version, created=now(), change_request=f'{head}'[:2000],
                            change_summary=f'{head}. {checked_outcome(sim)}', spec=spec, requirements=requirements,
                            scene=scene, robot=concept, urdf=urdf, sim=sim)
    if auto_repair and collision_episodes(version):
        if llms is None:
            version = version.model_copy(update={
                'change_summary': f'{version.change_summary} Automatic collision repair was skipped: no model is available.'})
        else:
            version = await repair_collisions(llms, version, progress)
    return version
