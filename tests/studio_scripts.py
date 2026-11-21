"""Scripted (fixture) LLM answers for the bottle task. These are not model results."""
import asyncio
import time

from studio.llm.scripted import ScriptedLLM
from studio.physics import place_base
from fixtures_specs import bottle_spec

BASE = place_base(bottle_spec())


def spec_dict(**changes):
    spec = bottle_spec().model_dump(mode='json')
    spec.update(changes)
    return spec


def scene_dict(spec=None):
    spec = spec or bottle_spec()
    a, b = spec.marker('a').position, spec.marker('b').position
    tables = [{'id': f'table_{m.id}', 'kind': 'table', 'center': [m.position[0], m.position[1], m.position[2] / 2],
               'size': [0.3, 0.3, m.position[2]]} for m in spec.markers]
    o = spec.objects[0]
    return {'floor_size_m': [3.0, 3.0], 'boxes': tables, 'markers': [m.model_dump(mode='json') for m in spec.markers],
            'objects': [{'id': o.id, 'shape': o.shape, 'center': [a[0], a[1], a[2] + o.size_m[2] / 2], 'size': list(o.size_m)}]}


ROBOT = {'family': 'serial_arm', 'name': 'Bottle mover arm', 'rationale': 'A 6-axis arm reaches both points.',
         'params': {'dof': 6}}


class SlowLLM(ScriptedLLM):
    """Sleeps inside the call and records (start, end) so tests can see whether calls overlapped."""
    def __init__(self, rules, name, stamps, delay):
        super().__init__(rules)
        self.name, self.stamps, self.delay = name, stamps, delay

    async def invoke(self, input, system_prompt):
        start = time.monotonic()
        await asyncio.sleep(self.delay)
        out = self._answer(input, system_prompt, [])
        self.stamps[self.name] = (start, time.monotonic())
        return out


def llms(spec=None, scene=None, robot=None, delay=0.0, stamps=None):
    """Overrides for LLMRegistry: one ScriptedLLM per role. `stamps` gets {'scene': (start, end), 'robot': ...}."""
    stamps = {} if stamps is None else stamps
    return {'spec': ScriptedLLM([('requirements engineer', spec if spec is not None else {'spec': spec_dict()})]),
            'scene': SlowLLM([('3D scene', scene or scene_dict())], 'scene', stamps, delay),
            'robot': SlowLLM([('kind of robot', robot or ROBOT)], 'robot', stamps, delay)}
