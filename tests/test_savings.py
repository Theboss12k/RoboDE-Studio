"""Time saved: formulas (hand calculations), spec questions and baseline source, PDF section. No model, no network."""
import math

import pytest
from fixtures_specs import bottle_spec
from pypdf import PdfReader
from studio_fixtures import make_build, make_version
from studio_scripts import spec_dict

from studio.agents import spec
from studio.llm.scripted import ScriptedLLM
from studio.physics import savings as sv
from studio.robot import report
from studio.schemas import Baseline, SavingsInputs, TechnicalSpec

QUESTION = {'id': 'cycle', 'text': 'How fast must one cycle be?', 'kind': 'number', 'unit': 's', 'default': 6}
INPUTS = dict(robot_cycle_s=6.0, manual_cycle_s=15.0, operators=1, shifts_per_day=1.0, hours_per_shift=8.0,
              days_per_week=5.0, days_per_year=250.0, supervision_min_per_shift=15.0)


def spec_llm(*answers):
    items = list(answers)
    return ScriptedLLM([('requirements engineer', lambda i, s, im: items.pop(0) if len(items) > 1 else items[0])])


# --- formulas ---------------------------------------------------------------------------------------------------------

def test_savings_hand_calculation():
    s = sv.savings(INPUTS)
    # robot 600 items/h; one person 240 items/h; 4800 items in an 8 h shift = 4800 * 15 s = 20 person-hours by hand;
    # minus 15 min of supervision = 19.75 h a day
    assert s.robot_items_per_hour.result == pytest.approx(600)
    assert s.manual_items_per_hour.result == pytest.approx(240)
    assert s.items_per_day.result == pytest.approx(4800)
    assert s.manual_hours_per_day.result == pytest.approx(20)
    assert s.supervision_hours_per_day.result == pytest.approx(0.25)
    assert s.hours_freed_per_day.result == pytest.approx(19.75)
    assert s.hours_freed_per_week.result == pytest.approx(98.75)
    assert s.hours_freed_per_year.result == pytest.approx(4937.5)
    assert s.throughput_change.result == pytest.approx(1.5)
    assert s.hours_freed_per_week.unit == 'h' and s.robot_items_per_hour.unit == 'items/h'
    assert s.hours_freed_per_day.inputs['manual_hours_per_day'].value == pytest.approx(20)
    assert '19.75' not in s.hours_freed_per_day.formula        # formulas hold names, not numbers


def test_savings_two_operators_two_shifts():
    s = sv.savings({**INPUTS, 'robot_cycle_s': 10.0, 'manual_cycle_s': 30.0, 'operators': 2, 'shifts_per_day': 2.0})
    assert s.manual_items_per_hour.result == pytest.approx(240)           # 2 * 3600 / 30
    assert s.items_per_day.result == pytest.approx(360 * 8 * 2)
    assert s.manual_hours_per_day.result == pytest.approx(5760 * 30 / 3600)   # 48 h
    assert s.supervision_hours_per_day.result == pytest.approx(0.5)
    assert s.hours_freed_per_day.result == pytest.approx(47.5)
    assert s.throughput_change.result == pytest.approx(360 / 240 - 1)


def test_savings_rejects_nonsense_and_can_be_negative():
    with pytest.raises(ValueError):
        sv.savings({**INPUTS, 'robot_cycle_s': 0})
    with pytest.raises(ValueError):
        sv.savings({**INPUTS, 'operators': 0.5})
    slow = sv.savings({**INPUTS, 'robot_cycle_s': 60.0, 'manual_cycle_s': 5.0, 'supervision_min_per_shift': 60.0})
    assert slow.throughput_change.result < 0 and math.isfinite(slow.hours_freed_per_day.result)


def test_robot_cycle_source_and_inputs_for_a_version():
    v = make_version()
    assert sv.robot_cycle_s(v) == v.sim.duration_s
    found = sv.version_savings(v, {'manual_cycle_s': 20, 'operators': 2, 'days_per_week': 6})
    sav, inp, source = found
    assert source == 'user' and inp.manual_cycle_s == 20 and inp.operators == 2 and inp.days_per_week == 6
    assert inp.robot_cycle_s == v.sim.duration_s and inp.shifts_per_day == v.spec.physics_inputs.shifts_per_day
    assert sav.items_per_day.result == pytest.approx(3600 / v.sim.duration_s * 8 * inp.shifts_per_day)


# --- baseline source --------------------------------------------------------------------------------------------------

def test_baseline_source_logic():
    est = Baseline(manual_cycle_s=20, operators=2, source='estimate')
    usr = Baseline(manual_cycle_s=20, operators=2, source='user')
    assert sv.resolve_baseline(None, None) == {'manual_cycle_s': None, 'operators': 1, 'source': 'estimate'}
    assert sv.resolve_baseline(est, None)['source'] == 'estimate'
    assert sv.resolve_baseline(usr, None)['source'] == 'user'
    over = sv.resolve_baseline(est, {'manual_cycle_s': 33})          # the user's saved inputs win and make it a user figure
    assert over == {'manual_cycle_s': 33, 'operators': 2, 'source': 'user'}
    assert sv.resolve_baseline(est, {'hours_per_shift': 7})['source'] == 'estimate'
    assert sv.resolve_baseline(None, {'manual_cycle_s': 9}) == {'manual_cycle_s': 9, 'operators': 1, 'source': 'user'}


def test_no_manual_time_means_no_savings_and_nothing_invented():
    v = make_version()
    assert v.spec.baseline is None
    assert sv.version_savings(v, None) is None
    assert sv.version_savings(v, {'hours_per_shift': 7}) is None
    assert sv.version_savings(v, {'manual_cycle_s': 10}) is not None


def test_baseline_from_answers():
    assert sv.baseline_from_answers({}) is None
    assert sv.baseline_from_answers({'operators': 2}) is None            # no manual time, no baseline
    assert sv.baseline_from_answers({'manual_cycle_s': 12, 'operators': '3'}).model_dump() == dict(
        manual_cycle_s=12, operators=3, source='user', note='From your answers: manual time per item and operators.')
    one = sv.baseline_from_answers({'manual_cycle_s': 12})
    assert one.source == 'user' and one.operators == 1 and 'One person is assumed' in one.note
    assert sv.baseline_from_answers({'manual_cycle_s': 'soon', 'operators': True}) is None
    # untouched defaults are sent back by the client: they are not answers
    assert sv.baseline_from_answers({'manual_cycle_s': 15, 'operators': 1}, {'manual_cycle_s': 15, 'operators': 1}) is None
    assert sv.baseline_from_answers({'manual_cycle_s': 20, 'operators': 1}, {'manual_cycle_s': 15, 'operators': 1}).source == 'user'


# --- spec agent -------------------------------------------------------------------------------------------------------

def test_spec_prompt_asks_for_the_baseline_questions():
    assert 'manual_cycle_s' in spec.SYSTEM and 'operators' in spec.SYSTEM and 'estimate' in spec.SYSTEM


async def test_spec_questions_are_not_touched_by_code():
    asked = await spec.run(spec_llm({'questions': [QUESTION]}), 'move a bottle', None, {}, 1)
    assert [q.id for q in asked['questions']] == ['cycle']


async def test_spec_baseline_from_answers_or_absent():
    answered = await spec.run(spec_llm({'spec': spec_dict()}), 'x', None, {'manual_cycle_s': 12, 'operators': 2}, 2)
    b = answered['spec'].baseline
    assert (b.manual_cycle_s, b.operators, b.source) == (12, 2, 'user')
    nobody = await spec.run(spec_llm({'spec': spec_dict()}), 'x', None, {}, 1)
    assert nobody['spec'].baseline is None
    # the agent's own estimate stays, but never as a user figure
    own = {'manual_cycle_s': 11, 'operators': 2, 'source': 'user'}
    kept = await spec.run(spec_llm({'spec': spec_dict(baseline=own)}), 'x', None, {}, 1)
    assert kept['spec'].baseline.source == 'estimate' and kept['spec'].baseline.manual_cycle_s == 11
    # an answer that is the asked question's untouched default is not an answer
    defaults = await spec.run(spec_llm({'spec': spec_dict()}), 'x', None, {'manual_cycle_s': 15}, 2,
                              {'question_defaults': {'manual_cycle_s': 15}})
    assert defaults['spec'].baseline is None


def test_baseline_is_dropped_from_json_when_absent():
    s = bottle_spec()
    assert 'baseline' not in s.model_dump(mode='json')
    with_b = s.model_copy(update={'baseline': Baseline(manual_cycle_s=10, operators=1, source='user')})
    assert with_b.model_dump(mode='json')['baseline']['manual_cycle_s'] == 10
    assert TechnicalSpec.model_validate(with_b.model_dump(mode='json')).baseline.source == 'user'
    SavingsInputs.model_validate(INPUTS)


# --- PDF --------------------------------------------------------------------------------------------------------------

def _text(path):
    return '\n'.join(' '.join(p.extract_text().split()) for p in PdfReader(str(path)).pages)


def test_pdf_has_the_expected_time_savings_section(tmp_path):
    version = make_version()
    build = make_build(version)
    project = {'title': 'T', 'savings_inputs': {'manual_cycle_s': 20, 'operators': 2}}
    report.write_pdf(build, version, project, tmp_path, [], tmp_path / 'a.pdf')
    text = _text(tmp_path / 'a.pdf')
    assert 'Expected time savings' in text
    assert text.index('Task and requirements met') < text.index('Expected time savings') < text.index('Bill of materials')
    assert 'Estimate based on your figures; kinematic simulation, not a time study.' in text
    for needle in ('Labour hours freed per day', 'per week', 'per year', 'Robot items per hour', 'Manual items per hour',
                   'Throughput change', 'Supervision time', 'manual time 20 s per item', '2 operator(s)'):
        assert needle in text, needle
    sav = sv.version_savings(version, project['savings_inputs'])[0]
    assert f'{sav.hours_freed_per_week.result:.3g} h' in text
    report.write_pdf(build, version, {'title': 'T'}, tmp_path, [], tmp_path / 'b.pdf')
    text_b = _text(tmp_path / 'b.pdf')
    assert 'Expected time savings' in text_b and 'No time saving was estimated' in text_b and 'Estimate based on' not in text_b
    est = version.model_copy(update={'spec': version.spec.model_copy(update={'baseline': Baseline(manual_cycle_s=9, operators=1, source='estimate')})})
    report.write_pdf(build, est, {'title': 'T'}, tmp_path, [], tmp_path / 'e.pdf')
    assert 'Estimate based on estimated figures;' in _text(tmp_path / 'e.pdf')


def test_pdf_renders_sourcing_notes_rejected_products_and_unverified_prices(tmp_path):
    version = make_version()
    build = make_build(version)
    build.__dict__['sourcing_notes'] = ['Two shops blocked the server fetch.']
    motor = build.motors[0]
    motor.__dict__['rejected'] = [{'id': 'x1', 'name': 'Tiny motor 0.1 Nm', 'shop': 'robu.in',
                                   'url': 'https://robu.in/p/tiny', 'reason': 'fails_margin: continuous 0.1 < 12'}]
    motor.price.__dict__['status'] = 'unverified'
    report.write_pdf(build, version, {}, tmp_path, [], tmp_path / 'c.pdf')
    text = _text(tmp_path / 'c.pdf')
    for needle in ('Sourcing notes', 'Two shops blocked the server fetch.', 'Rejected products', 'Tiny motor 0.1 Nm',
                   'fails_margin: continuous 0.1 < 12', 'unverified: the shop page was blocked'):
        assert needle in text, needle
