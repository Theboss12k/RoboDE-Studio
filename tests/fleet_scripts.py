"""Scripted (fixture) LLM answers for the long carton task that needs several robots. These are not model results.

The task: 5 kg cartons from A (x = -2.5 m) to B (x = +2.5 m) on 0.3 m conveyors every 10 s; the user's arms reach at
most 1.3 m (limits form: up to 3 robots). The same answers feed tests/test_fleet_pipeline.py and the Java fixture
server-java/src/test/resources/scripted/rules_fleet.json (written by make_fleet_fixtures.py there).
"""
from test_catalog import answer as search_answer

from studio.schemas import FleetLimits, Marker, PhysicsInputs, TaskAction, TaskObject, TechnicalSpec

BRIEF = ('Move 5 kg cartons from x = -2.5 m to x = +2.5 m every 10 s; our arms reach at most 1.3 m. '
         'The cartons sit on 0.3 m high conveyors.')
LIMITS = FleetLimits(max_robots=3, max_reach_m=1.3)
HINT = {'robots_needed': 3, 'reason': 'A and B are 5 m apart and the arms reach at most 1.3 m, so one arm cannot '
                                      'serve both ends.'}


def carton_spec() -> TechnicalSpec:
    return TechnicalSpec(
        title='Carton transfer', summary='Move 5 kg cartons 5 m along a line between two conveyors every 10 s.',
        actions=[TaskAction(verb='pick', object='carton', target='a', description='pick carton at A'),
                 TaskAction(verb='place', object='carton', target='b', description='place carton at B')],
        objects=[TaskObject(id='carton', name='Carton', mass_kg=5.0, size_m=(0.3, 0.3, 0.25), grip='parallel',
                            start_marker='a')],
        markers=[Marker(id='a', name='Conveyor A', position=(-2.5, 0.0, 0.3)),
                 Marker(id='b', name='Conveyor B', position=(2.5, 0.0, 0.3))],
        physics_inputs=PhysicsInputs(object_mass_kg=5.0, object_size_m=(0.3, 0.3, 0.25), grip='parallel',
                                     cycle_time_s=10.0),
        assumptions=['The arms reach at most 1.3 m (the user said so).'])


def spec_answer():
    return {'spec': carton_spec().model_dump(mode='json'), 'fleet': HINT}


def scene_answer():
    """Conveyors under A and B; it fits the single robot and the three fleet bases (and keeps the stations free)."""
    spec = carton_spec()
    boxes = [{'id': f'conveyor_{m.id}', 'kind': 'conveyor', 'center': [m.position[0], 0.0, m.position[2] / 2],
              'size': [0.4, 0.4, m.position[2]]} for m in spec.markers]
    o = spec.objects[0]
    a = spec.marker('a').position
    return {'floor_size_m': [6.5, 3.0], 'boxes': boxes, 'markers': [m.model_dump(mode='json') for m in spec.markers],
            'objects': [{'id': o.id, 'shape': 'box', 'center': [a[0], a[1], a[2] + o.size_m[2] / 2],
                         'size': list(o.size_m)}]}


SINGLE_ARM = {'family': 'serial_arm', 'name': 'Long-reach carton arm', 'rationale': 'One arm in the middle.',
              'params': {'dof': 6}}
MEMBER_ARM = {'family': 'serial_arm', 'name': 'Carton arm', 'rationale': 'A 6-axis arm for its share of the line.',
              'params': {'dof': 6}}
MEMBER_GANTRY = {'family': 'gantry', 'name': 'Carton gantry', 'rationale': 'A short gantry over its share of the line.',
                 'params': {}}

# a fleet revise: r2 gets a tool yaw axis and wider beams (explicit numbers code keeps when it re-sizes)
REVISE = {'targets': ['fleet'], 'fleet': {'members': [{'id': 'r2', 'family': 'gantry',
                                                       'params': {'tool_yaw': True, 'beam_size_m': 0.1}}]},
          'summary': 'r2 gets a tool yaw axis and wider beams.'}


def rules():
    """Fixture rules {match, answers} in order (first match wins; answers are consumed, the last repeats)."""
    return [
        {'match': 'connection test', 'answers': ['ready']},
        {'match': 'Fleet member r1.', 'answers': [MEMBER_ARM, MEMBER_GANTRY]},     # r1: an arm (over the limit), then a gantry
        {'match': 'Fleet member r2.', 'answers': [MEMBER_GANTRY]},
        {'match': 'Fleet member r3.', 'answers': [MEMBER_GANTRY]},
        {'match': 'kind of robot', 'answers': [SINGLE_ARM]},                         # the single-robot design (v1)
        {'match': '3D scene', 'answers': [scene_answer()]},
        {'match': 'change an existing', 'answers': [REVISE]},
        {'match': 'find real robots', 'answers': [search_answer()]},
        {'match': 'requirements engineer', 'answers': [spec_answer()]},
    ]
