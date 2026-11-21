"""The pure fleet planner (server/studio/fleet): geometry, assignment, layout, member specs, schedule and checks.
Pure code: no LLM, no store. Robot checks here are kinematic and static-load calculations, not dynamics."""
import math

import numpy as np
import pytest

from fixtures_specs import bottle_spec
from studio import families
from studio.agents.spec import validate_spec
from studio.fleet import assign, checks, feasibility, geometry, layout, schedule, subspec
from studio.physics import derive, place_base
from studio.pipeline.design import size_and_simulate
from studio.sim import simulate
from studio.schemas import (BasePose, FleetAssignment, FleetHandover, FleetLimits, FleetMember, FleetMemberAssignment, Frame,
                            Marker, PhysicsInputs, RobotConcept, SceneSpec, SimResult, TaskAction, TaskObject,
                            TechnicalSpec)


def line_spec(x0=-1.6, x1=1.6, cycle=10.0) -> TechnicalSpec:
    """A 2 kg carton picked at A and placed at B on 0.75 m tables, |AB| = x1 - x0."""
    return TechnicalSpec(
        title='Long transfer', summary='Move a carton along a line.',
        actions=[TaskAction(verb='pick', object='carton', target='a', description='pick carton at A'),
                 TaskAction(verb='place', object='carton', target='b', description='place carton at B')],
        objects=[TaskObject(id='carton', name='Carton', mass_kg=2.0, size_m=(0.2, 0.2, 0.2), start_marker='a')],
        markers=[Marker(id='a', name='A', position=(x0, 0.0, 0.75)), Marker(id='b', name='B', position=(x1, 0.0, 0.75))],
        physics_inputs=PhysicsInputs(object_mass_kg=2.0, object_size_m=(0.2, 0.2, 0.2), cycle_time_s=cycle))


LIMITS = FleetLimits(max_reach_m=1.2)


def plan(spec=None, limits=LIMITS, a=None):
    spec = spec or line_spec()
    a = assign.validate_assignment(spec, a or assign.auto_assign(spec, limits), limits)
    return spec, a, layout.plan_layout(spec, a, limits)


def sized(spec, family='serial_arm'):
    base = place_base(spec)
    reqs = derive(spec, base)
    p = families.get(family).size_from_physics(reqs, spec, {})
    return RobotConcept(family=family, name=f'{family} {spec.title}'[:120], params=p.model_dump(), base=base), reqs


def simulate_members(spec, a, lay):
    out = []
    for m in a.members:
        s = subspec.member_spec(spec, a, lay, m.id)
        base = place_base(s)
        reqs = derive(s, base)
        scene = SceneSpec(boxes=subspec.station_boxes(lay), robot_base=base)
        concept, urdf, sim = size_and_simulate(s, reqs, scene, RobotConcept(family='serial_arm', name=f'arm {m.id}'), base)
        out.append(FleetMember(id=m.id, name=m.name, spec=s, requirements=reqs, robot=concept, urdf=urdf, sim=sim))
    return out


# --- geometry ----------------------------------------------------------------------------------------------------------

def test_capsule_capsule_distance_cases():
    d = geometry.capsule_capsule_distance
    assert d((0, 0, 0), (1, 0, 0), 0.1, (0, 1, 0), (1, 1, 0), 0.1) == pytest.approx(0.8)          # parallel
    assert d((0, 0, 0), (1, 0, 0), 0.1, (0, 1, 0), (1, 1, 0), 0.95) < 0                            # parallel, overlapping
    assert d((-1, 0, 0), (1, 0, 0), 0.05, (0, -1, 0.3), (0, 1, 0.3), 0.05) == pytest.approx(0.2)  # crossing
    assert d((0, 0, 0), (1, 0, 0), 0.0, (2, 0, 0), (3, 0, 0), 0.0) == pytest.approx(1.0)           # end to end
    assert d((0, 0, 0), (1, 0, 0), 0.1, (0, 0, 0), (1, 0, 0), 0.1) == pytest.approx(-0.2)         # coincident
    assert geometry.segment_distance((0, 0, 0), (0, 0, 0), (1, 1, 0), (1, 1, 0)) == pytest.approx(math.sqrt(2))
    assert geometry.segment_distance((0, 0, 0), (0, 0, 0), (-1, 1, 0), (1, 1, 0)) == pytest.approx(1.0)


# --- assignment --------------------------------------------------------------------------------------------------------

def test_auto_assign_long_line_gives_two_robots_and_one_handover():
    a = assign.validate_assignment(line_spec(), assign.auto_assign(line_spec(), LIMITS), LIMITS)
    assert [m.id for m in a.members] == ['r1', 'r2'] and [m.actions for m in a.members] == [[0], [1]]
    assert len(a.handovers) == 1
    h = a.handovers[0]
    assert (h.object, h.from_member, h.to_member) == ('carton', 'r1', 'r2')
    assert a.members[0].name == 'Robot 1 (pick at A)' and a.source == 'auto'


def test_auto_assign_adds_a_relay_when_two_reaches_are_too_short():
    spec = line_spec(-2.5, 2.5)
    lim = FleetLimits(max_reach_m=1.0, max_robots=3)
    a = assign.validate_assignment(spec, assign.auto_assign(spec, lim), lim)
    assert [m.actions for m in a.members] == [[0], [], [1]] and len(a.handovers) == 2
    lay = layout.plan_layout(spec, a, lim)
    assert lay.converged and lay.handovers['h1'][0] < lay.handovers['h2'][0]
    for m in a.members:
        validate_spec(subspec.member_spec(spec, a, lay, m.id))


def test_bottle_needs_one_robot_but_still_splits_validly():
    spec = bottle_spec()
    base = place_base(spec)
    reqs = derive(spec, base)
    concept, _, sim = size_and_simulate(spec, reqs, SceneSpec(robot_base=base),
                                        RobotConcept(family='serial_arm', name='arm'), base)
    assert feasibility.single_robot_shortfall(spec, reqs, concept, sim) == []
    a = assign.validate_assignment(spec, assign.auto_assign(spec, FleetLimits()), FleetLimits())
    assert len(a.members) == 2 and len(a.handovers) == 1


def test_shortfall_flags_a_long_task_for_one_robot():
    spec = line_spec(-2.5, 2.5)
    base = place_base(spec)
    reqs = derive(spec, base)
    concept, _, sim = size_and_simulate(spec, reqs, SceneSpec(robot_base=base),
                                        RobotConcept(family='serial_arm', name='arm'), base)
    reasons = feasibility.single_robot_shortfall(spec, reqs, concept, sim)
    assert any('practical 2.5 m' in r for r in reasons)


def test_validate_assignment_rejects_bad_edits():
    spec = line_spec()
    spec = spec.model_copy(update={'actions': spec.actions + [TaskAction(verb='home', description='go home')]})
    ok = FleetAssignment(members=[FleetMemberAssignment(id='r1', actions=[0]), FleetMemberAssignment(id='r2', actions=[1, 2])],
                         handovers=[FleetHandover(id='h1', object='carton', from_member='r1', to_member='r2')])
    assign.validate_assignment(spec, ok, LIMITS)
    missing = ok.model_copy(update={'members': [FleetMemberAssignment(id='r1', actions=[0]),
                                                FleetMemberAssignment(id='r2', actions=[1])]})
    with pytest.raises(ValueError, match='Action 3 .* not assigned'):
        assign.validate_assignment(spec, missing, LIMITS)
    dup = ok.model_copy(update={'members': [FleetMemberAssignment(id='r1', actions=[0, 1]),
                                            FleetMemberAssignment(id='r2', actions=[1, 2])]})
    with pytest.raises(ValueError, match='assigned to both r1 and r2'):
        assign.validate_assignment(spec, dup, LIMITS)
    with pytest.raises(ValueError, match='without holding it'):
        assign.validate_assignment(spec, ok.model_copy(update={'handovers': []}), LIMITS)
    unknown = [FleetHandover(id='h1', object='carton', from_member='r1', to_member='r3')]
    with pytest.raises(ValueError, match='r3'):
        assign.validate_assignment(spec, ok.model_copy(update={'handovers': unknown}), LIMITS)
    backwards = [FleetHandover(id='h1', object='carton', from_member='r2', to_member='r1')]
    with pytest.raises(ValueError):
        assign.validate_assignment(spec, ok.model_copy(update={'handovers': backwards}), LIMITS)
    three = ok.model_copy(update={'members': ok.members[:1] + [FleetMemberAssignment(id='r2', actions=[1]),
                                                               FleetMemberAssignment(id='r3', actions=[2])]})
    with pytest.raises(ValueError, match='limit is 2'):
        assign.validate_assignment(spec, three, LIMITS)


# --- layout and member specs -------------------------------------------------------------------------------------------

def test_layout_converges_with_spacing_and_reachable_handover():
    spec, a, lay = plan()
    assert lay.converged and lay.iterations < 20 and len(lay.log) >= lay.iterations
    b1, b2 = (np.array(lay.bases[i].position[:2]) for i in ('r1', 'r2'))
    assert np.linalg.norm(b1 - b2) >= layout.MIN_BASE_SPACING_M - 1e-6
    h = lay.handovers['h1']
    for i in ('r1', 'r2'):
        s = validate_spec(subspec.member_spec(spec, a, lay, i))
        assert place_base(s) == lay.bases[i]          # the sub-spec reproduces the layout base
        concept, _ = sized(s)
        ok, why = checks.handover_reachable(concept, lay.bases[i], checks.grasp_point(spec, 'carton', h),
                                            spec.physics_inputs.approach_height_m)
        assert ok, why
        for p in (spec.marker('a').position, spec.marker('b').position, h):
            assert np.linalg.norm(np.array(p[:2]) - np.array(lay.bases[i].position[:2])) >= layout.POINT_CLEARANCE_M - 1e-6


def test_bottle_layout_keeps_clearances():
    spec, a, lay = plan(bottle_spec(), FleetLimits())
    b1, b2 = (np.array(lay.bases[i].position[:2]) for i in ('r1', 'r2'))
    assert lay.converged and np.linalg.norm(b1 - b2) >= 0.6 - 1e-6
    for b in (b1, b2):
        for m in spec.markers:
            assert np.linalg.norm(np.array(m.position[:2]) - b) >= 0.3 - 1e-6


def test_user_fixed_handover_and_base_are_kept():
    spec = line_spec()
    a = assign.auto_assign(spec, LIMITS)
    a = a.model_copy(update={
        'members': [a.members[0].model_copy(update={'base': (-0.9, 0.2, 0.0), 'start': (-1.3, 0.5, 0.75)}), a.members[1]],
        'handovers': [a.handovers[0].model_copy(update={'position': (0.2, 0.1, 0.75), 'fixed': True})],
        'source': 'user'})
    spec, a, lay = plan(spec, LIMITS, a)
    assert lay.handovers['h1'] == (0.2, 0.1, 0.75)
    assert lay.bases['r1'].position == (-0.9, 0.2, 0.0)
    assert lay.parks['r1'][0] == (-1.3, 0.5, 0.75)
    s = validate_spec(subspec.member_spec(spec, a, lay, 'r1'))
    assert s.marker('h1').position == (0.2, 0.1, 0.75) and s.marker('park_r1_start').position == (-1.3, 0.5, 0.75)


def test_member_specs_pass_validate_spec():
    spec, a, lay = plan()
    giver = validate_spec(subspec.member_spec(spec, a, lay, 'r1'))
    receiver = validate_spec(subspec.member_spec(spec, a, lay, 'r2'))
    assert [x.verb for x in giver.actions] == ['move', 'pick', 'place', 'move']
    assert giver.actions[2].target == 'h1' and giver.actions[2].description == subspec.handover_place_label('carton', 'h1')
    assert [x.verb for x in receiver.actions] == ['move', 'pick', 'place', 'move'] and receiver.actions[1].target == 'h1'
    assert receiver.objects[0].start_marker == 'h1' and giver.objects[0].start_marker == 'a'
    assert {m.id for m in receiver.markers} == {'robot_base', 'h1', 'b', 'park_r2_start', 'park_r2_end'}
    assert receiver.physics_inputs.cycle_time_s == spec.physics_inputs.cycle_time_s
    boxes = subspec.station_boxes(lay)
    assert len(boxes) == 1 and boxes[0].collides and boxes[0].center[2] + boxes[0].size[2] / 2 == pytest.approx(0.75)


# --- schedule ----------------------------------------------------------------------------------------------------------

def test_schedule_offset_follows_the_givers_handover_phase():
    spec, a, lay = plan()
    members = simulate_members(spec, a, lay)
    off = schedule.offsets(members, a)
    frames = members[0].sim.frames
    label = subspec.handover_place_label('carton', 'h1')
    last = max(k for k, f in enumerate(frames) if f.phase == label)
    assert off['r1'] == 0.0 and off['r2'] == pytest.approx(frames[last + 1].t)
    assert 0 < off['r2'] < members[0].sim.duration_s
    episodes, events, gap = schedule.robot_clearance(members, off)
    assert episodes == 0 and events == [] and gap > 0
    assert schedule.fleet_period(members) == pytest.approx(max(m.sim.duration_s for m in members))
    fc = {c.requirement_id: c for c in checks.fleet_checks(spec, a, lay, members, (episodes, events, gap), LIMITS)}
    assert set(fc) == {'fleet_robot_count', 'fleet_base_spacing', 'fleet_handover_reach', 'fleet_robot_clearance',
                       'fleet_cycle_time'}
    assert fc['fleet_robot_count'].passed and fc['fleet_base_spacing'].passed and fc['fleet_robot_clearance'].passed
    assert fc['fleet_handover_reach'].passed, fc['fleet_handover_reach'].detail
    assert fc['fleet_cycle_time'].passed and 'one-item station buffer' in fc['fleet_cycle_time'].detail


def _crafted_member(mid, base, yaw):
    """A default serial arm that is upright, stretches horizontally towards +x of its base during 1..2 s, and is
    upright again from 2.5 s on (no task: frames written by hand)."""
    up = {f'j{i}': 0.0 for i in range(1, 7)}
    out = {**up, 'j2': math.pi / 2}
    frames = [Frame(t=0.0, joints=up, phase='wait'), Frame(t=0.6, joints=up, phase='wait'),
              Frame(t=1.0, joints=out, phase='reach out'), Frame(t=2.0, joints=out, phase='reach out'),
              Frame(t=2.5, joints=up, phase='back up'), Frame(t=4.0, joints=up, phase='back up')]
    robot = RobotConcept(family='serial_arm', name=f'arm {mid}', base=BasePose(position=base, yaw=yaw))
    return FleetMember(id=mid, spec=bottle_spec(), requirements=[], robot=robot, urdf='',
                       sim=SimResult(frames=frames, duration_s=4.0))


def test_robot_clearance_finds_a_crafted_collision_and_delay_clears_it():
    members = [_crafted_member('r1', (0.0, 0.0, 0.0), 0.0), _crafted_member('r2', (1.3, 0.0, 0.0), math.pi)]
    off = {'r1': 0.0, 'r2': 0.0}
    episodes, events, gap = schedule.robot_clearance(members, off)
    assert episodes > 0 and gap < 0 and events[0].kind == 'collision'
    assert events[0].text.startswith('Robot r1 segment') and '"reach out" / "reach out"' in events[0].text
    new, log = schedule.resolve_by_delay(members, off, cycle_s=10.0)
    assert new['r1'] == 0.0 and new['r2'] > 0 and log[-1] == 'Robot-robot contact cleared by delaying'
    assert schedule.robot_clearance(members, new)[0] == 0
    stuck, log = schedule.resolve_by_delay(members, off, cycle_s=0.5)       # no room in the cycle to delay
    assert schedule.robot_clearance(members, stuck)[0] > 0 and any(x.startswith('Stopped') for x in log)
    assert log[-1].endswith('remain after delaying')


# --- limit checks ------------------------------------------------------------------------------------------------------

def test_member_limit_checks():
    spec, a, lay = plan()
    s = subspec.member_spec(spec, a, lay, 'r1')
    concept, reqs = sized(s)
    lim = FleetLimits(max_reach_m=0.5, max_payload_kg=100, families=['gantry'])
    c = {x.requirement_id: x for x in checks.member_limit_checks(concept, reqs, lim)}
    assert c['limit_family'].passed is False and c['limit_reach'].passed is False and c['limit_payload'].passed is True
    assert c['limit_reach'].actual > 0.5
    open_limits = {x.requirement_id for x in checks.member_limit_checks(concept, reqs, FleetLimits())}
    assert open_limits == {'limit_family'}
    mobile = RobotConcept(family='mobile_base', name='cart')
    assert checks.member_limit_checks(mobile, reqs, FleetLimits())[0].passed is False


# --- sized fit (the reach check's own metric) -----------------------------------------------------------------------------

def low_line(x0=-1.6, x1=1.6, z=0.3):
    spec = line_spec(x0, x1)
    return spec.model_copy(update={'markers': [m.model_copy(update={'position': (m.position[0], 0.0, z)})
                                               for m in spec.markers]})


def test_auto_assign_uses_the_sized_reach_of_the_members():
    """The xy fit alone accepts two arms on a 3.2 m line, but code sizes those arms above the user's 1.3 m limit
    (the arm's own margins and base height); the split that the sizing accepts needs a relay."""
    from studio.fleet import fit
    spec, lim = low_line(), FleetLimits(max_reach_m=1.3, max_robots=3, families=['serial_arm'])
    xy_only = assign.validate_assignment(spec, assign.auto_assign(spec, lim, reach={'r1': 1.3, 'r2': 1.3}), lim)
    assert len(xy_only.members) == 2 and fit.sized_fit(spec, xy_only, lim)[0] > 1
    a = assign.validate_assignment(spec, assign.auto_assign(spec, lim), lim)
    assert [m.actions for m in a.members] == [[0], [], [1]]
    ratio, lay, reaches = fit.sized_fit(spec, a, lim)
    assert ratio <= 1 and all(r['serial_arm'] <= 1.3 for r in reaches.values())
    for m in a.members:                       # the member sized as size_and_simulate does it passes limit_reach
        s = subspec.member_spec(spec, a, lay, m.id)
        concept, reqs = sized(s)
        c = {x.requirement_id: x for x in checks.member_limit_checks(concept, reqs, lim)}
        assert c['limit_reach'].passed, c['limit_reach']
    assert len(assign.auto_assign(spec, lim, min_robots=3).members) == 3


def test_relay_parks_beside_the_line():
    spec = low_line(-2.5, 2.5)
    lim = FleetLimits(max_reach_m=1.3, max_robots=3)
    a = assign.validate_assignment(spec, assign.auto_assign(spec, lim), lim)
    lay = layout.plan_layout(spec, a, lim)
    (start, end), base = lay.parks['r2'], lay.bases['r2'].position
    assert start == end and abs(start[0] - base[0]) < 1e-6
    assert abs(start[1] - base[1]) == pytest.approx(layout.POINT_CLEARANCE_M)


def _off_line_bottle():
    """bottle_spec with the robot mounted off the task line, so place_base's yaw (facing the first pick) is ~0.54 rad."""
    spec = bottle_spec()
    mount = Marker(id='robot_base', name='Robot base', position=(0.0, 0.0, 0.75))
    return spec.model_copy(update={'markers': [*spec.markers, mount]})


def test_gantry_strokes_follow_a_rotated_base():
    spec = _off_line_bottle()
    base = place_base(spec)
    assert 0.3 < base.yaw < 1.2
    reqs = derive(spec, base)
    concept, _, sim = size_and_simulate(spec, reqs, SceneSpec(robot_base=base), RobotConcept(family='gantry', name='g'), base)
    reach = next(c for c in sim.checks if c.requirement_id == 'reach_max')
    assert reach.passed is True and not any(e.kind == 'unreachable' for e in sim.events)


def test_shortfall_ignores_a_reach_failure_one_robot_can_fix():
    spec = _off_line_bottle()
    base = place_base(spec)
    reqs = derive(spec, base)
    concept, _, _ = size_and_simulate(spec, reqs, SceneSpec(robot_base=base), RobotConcept(family='serial_arm', name='arm'), base)
    short = concept.model_copy(update={'params': {**concept.params, 'upper_arm_m': 0.15, 'forearm_m': 0.15}})
    sim = simulate(spec, reqs, SceneSpec(robot_base=base), short)
    assert next(c for c in sim.checks if c.requirement_id == 'reach_max').passed is False
    assert feasibility.one_robot_reaches(spec, reqs) is not None
    assert feasibility.single_robot_shortfall(spec, reqs, short, sim) == []
