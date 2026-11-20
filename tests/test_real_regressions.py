"""Regressions from the first real-model run. Inputs are the saved real Claude output (spec, scene, robot concept)
in tests/fixtures/real_bottle_v1.json: no model is called."""
import json

import pytest
from conftest import FIXTURES

from studio import families, physics, sim
from studio.agents.scene import validate_scene
from studio.schemas import RobotConcept, SceneSpec, TechnicalSpec


@pytest.fixture(scope='module')
def real():
    d = json.loads((FIXTURES / 'real_bottle_v1.json').read_text())
    spec, scene = TechnicalSpec.model_validate(d['spec']), SceneSpec.model_validate(d['scene'])
    concept = RobotConcept.model_validate(d['robot'])
    base = physics.place_base(spec)
    reqs = physics.derive(spec, base)
    fam = families.get(concept.family)
    p = fam.size_from_physics(reqs, spec, concept.params)
    sized = concept.model_copy(update={'params': p.model_dump(), 'base': base})
    scene = scene.model_copy(update={'robot_base': base})
    return dict(spec=spec, scene=scene, base=base, reqs=reqs, sized=sized, sim=sim.simulate(spec, reqs, scene, sized))


def test_requirements_are_plausible_for_a_6_s_cycle(real):
    by = {r.id: r for r in real['reqs']}
    assert by['tcp_speed'].target < 1.5
    assert by['tcp_accel'].target < 15


def test_robot_base_marker_is_the_mounting_point(real):
    assert real['base'].position == (0.0, 0.0, 0.75)


def test_no_collision_at_start_or_during_carry(real):
    hits = [(e.t, e.text) for e in real['sim'].events if e.kind == 'collision']
    assert not hits, hits


def test_run_fills_the_cycle(real):
    res = real['sim']
    assert res.duration_s == pytest.approx(6.0, rel=0.05)
    cyc = next(c for c in res.checks if c.requirement_id == 'cycle_time')
    assert cyc.passed and 'fastest feasible' in cyc.detail and 'played at the 6.0 s cycle' in cyc.detail


def test_holding_between_grasp_and_release_only(real):
    frames = real['sim'].frames
    held = [i for i, f in enumerate(frames) if f.holding == 'bottle']
    assert held and all(not f.holding for f in frames[:held[0]]) and all(not f.holding for f in frames[held[-1] + 1:])
    assert frames[held[0]].phase.startswith('Close') and frames[0].holding == ''


def test_sized_rating_is_capped_at_family_limit(real):
    assert real['sized'].params['max_tcp_speed_m_s'] <= 4.0


def test_scene_validator_accepts_scene_without_floating_home_support(real):
    scene = real['scene']
    assert any(b.id == 'home_support' for b in scene.boxes)
    scene = scene.model_copy(update={'boxes': [b for b in scene.boxes if b.id != 'home_support']})
    validate_scene(scene, real['spec'], real['base'])


def test_move_goes_to_carry_height_and_holds_only_after_the_pick(real):
    wps = {w.name: w for w in sim.task.waypoints(real['spec'], real['scene'], real['base'])}
    a, b = wps['move_point_a'], wps['move_point_b']
    assert a.xyz[2] == pytest.approx(0.75 + 0.22 + 0.1) and a.object == ''
    assert b.xyz[2] == pytest.approx(0.75 + 0.22 + 0.1) and b.object == 'bottle'


def test_only_wait_inspect_press_count_as_dwell_and_dwell_is_capped(real):
    spec = real['spec']
    # every step of the real spec has a duration, but none is a wait/inspect/press: no dwell at all
    note = next(r for r in physics.derive(spec, real['base']) if r.id == 'tcp_speed').calc
    assert note.inputs['dwell'].value == 0 and 'capped' not in note.note
    waits = spec.model_copy(deep=True)
    waits.actions[0].verb, waits.actions[0].duration_s = 'wait', 5.0
    capped = next(r for r in physics.derive(waits, real['base']) if r.id == 'tcp_speed').calc
    assert capped.inputs['dwell'].value == 3.0 and 'capped' in capped.note


def test_spec_validator_rejects_dwell_not_below_cycle(real):
    from studio.agents.spec import validate_spec
    spec = real['spec'].model_copy(deep=True)
    spec.actions[0].verb, spec.actions[0].duration_s = 'wait', 6.0
    with pytest.raises(ValueError, match='dwell'):
        validate_spec(spec)
