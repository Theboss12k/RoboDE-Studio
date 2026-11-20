"""Valid TechnicalSpec fixtures shared by tests: bottle A->B, pallet, mobile delivery."""
from studio.schemas import (Locomotion, Marker, PhysicsInputs, TaskAction, TaskObject, TechnicalSpec)


def bottle_spec() -> TechnicalSpec:
    """0.3 kg bottle (70 mm dia x 220 mm), A=(0.5, 0.3, 0.75) to B=(0.5, -0.3, 0.75) on a 0.75 m table, 6 s cycle,
    peak acceleration 2 m/s^2."""
    return TechnicalSpec(
        title='Bottle A to B', summary='Move a bottle between two points on a table.',
        actions=[TaskAction(verb='pick', object='bottle', target='a', description='pick bottle at A'),
                 TaskAction(verb='place', object='bottle', target='b', description='place bottle at B')],
        objects=[TaskObject(id='bottle', name='Bottle', mass_kg=0.3, size_m=(0.07, 0.07, 0.22), shape='cylinder',
                            grip='parallel', start_marker='a')],
        markers=[Marker(id='a', name='A', position=(0.5, 0.3, 0.75)),
                 Marker(id='b', name='B', position=(0.5, -0.3, 0.75))],
        physics_inputs=PhysicsInputs(object_mass_kg=0.3, object_size_m=(0.07, 0.07, 0.22), grip='parallel',
                                     friction_coefficient=0.4, contact_count=2, cycle_time_s=6.0,
                                     safety_factor=1.5, clearance_m=0.05, approach_height_m=0.10,
                                     peak_acceleration_m_s2=2.0))


def pallet_spec() -> TechnicalSpec:
    """20 kg box moved across a 1.2 m span (gantry-like), vacuum grip, 10 s cycle."""
    return TechnicalSpec(
        title='Box to pallet', summary='Move a 20 kg box 1.2 m along a line.',
        actions=[TaskAction(verb='pick', object='box', target='in', description='pick box at input'),
                 TaskAction(verb='place', object='box', target='out', description='place box at pallet')],
        objects=[TaskObject(id='box', name='Box', mass_kg=20.0, size_m=(0.4, 0.3, 0.3), grip='vacuum',
                            start_marker='in')],
        markers=[Marker(id='in', name='Input', position=(-0.6, 0.0, 0.5)),
                 Marker(id='out', name='Pallet', position=(0.6, 0.0, 0.5))],
        physics_inputs=PhysicsInputs(object_mass_kg=20.0, object_size_m=(0.4, 0.3, 0.3), grip='vacuum',
                                     cycle_time_s=10.0, positional_accuracy_m=0.01, approach_height_m=0.15))


def mobile_delivery_spec() -> TechnicalSpec:
    """5 kg box carried along a 12 m path with a 3 degree ramp, 60 s cycle."""
    path = [(0.0, 0.0, 0.0), (6.0, 0.0, 0.0), (12.0, 0.0, 0.0)]
    return TechnicalSpec(
        title='Mobile delivery', summary='Carry a 5 kg box 12 m across the floor, including a ramp.',
        actions=[TaskAction(verb='pick', object='box', target='a', description='pick box at A'),
                 TaskAction(verb='drive', target='b', description='drive to B'),
                 TaskAction(verb='place', object='box', target='b', description='place box at B')],
        objects=[TaskObject(id='box', name='Box', mass_kg=5.0, size_m=(0.3, 0.2, 0.2), grip='parallel',
                            start_marker='a')],
        markers=[Marker(id='a', name='A', position=(0.5, 0.0, 0.8)),
                 Marker(id='b', name='B', position=(11.5, 0.0, 0.8))],
        locomotion=Locomotion(required=True, path=path, slope_deg=3.0),
        physics_inputs=PhysicsInputs(object_mass_kg=5.0, object_size_m=(0.3, 0.2, 0.2), grip='parallel',
                                     cycle_time_s=60.0, travel_distance_m=12.0, slope_deg=3.0))
