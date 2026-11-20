"""Time saved: how many person-hours a robot frees compared with doing the same items by hand.

The same formulas are in packages/core/src/savings.ts; `contracts/savings_vectors.json` (scripts/savings_vectors.py) pins
them to each other. The robot cycle is the kinematic simulation's (`sim.duration_s`, or `fleet.period_s` for a fleet);
the manual time comes from the user's answers or from stated estimates. It is an estimate, not a time study.
"""
from __future__ import annotations

from ..schemas import Calc, Quantity, Savings, SavingsInputs

DEFAULT_OPERATORS = 1
DEFAULT_HOURS_PER_SHIFT = 8.0
DEFAULT_DAYS_PER_WEEK = 5.0
DEFAULT_DAYS_PER_YEAR = 250.0
DEFAULT_SUPERVISION_MIN_PER_SHIFT = 15.0
FIELDS = ('manual_cycle_s', 'operators', 'hours_per_shift', 'days_per_week', 'days_per_year', 'supervision_min_per_shift')


def _calc(formula: str, inputs: dict[str, tuple[float, str]], result: float, unit: str) -> Calc:
    return Calc(formula=formula, inputs={k: Quantity(value=v, unit=u) for k, (v, u) in inputs.items()}, result=result,
                unit=unit)


def savings(inputs: SavingsInputs | dict) -> Savings:
    """The time-saved outputs, each with its calculation."""
    i = inputs if isinstance(inputs, SavingsInputs) else SavingsInputs.model_validate(inputs)
    robot_ph = 3600 / i.robot_cycle_s
    manual_ph = i.operators * 3600 / i.manual_cycle_s
    per_day = robot_ph * i.hours_per_shift * i.shifts_per_day
    manual_h = per_day * i.manual_cycle_s / 3600
    sup_h = i.supervision_min_per_shift / 60 * i.shifts_per_day
    freed_d = manual_h - sup_h
    freed_w = freed_d * i.days_per_week
    freed_y = freed_d * i.days_per_year
    change = robot_ph / manual_ph - 1
    return Savings(
        robot_items_per_hour=_calc('robot_items_per_hour = 3600 / robot_cycle_s',
                                   {'robot_cycle_s': (i.robot_cycle_s, 's')}, robot_ph, 'items/h'),
        manual_items_per_hour=_calc('manual_items_per_hour = operators * 3600 / manual_cycle_s',
                                    {'operators': (i.operators, ''), 'manual_cycle_s': (i.manual_cycle_s, 's')},
                                    manual_ph, 'items/h'),
        items_per_day=_calc('items_per_day = robot_items_per_hour * hours_per_shift * shifts_per_day',
                            {'robot_items_per_hour': (robot_ph, 'items/h'), 'hours_per_shift': (i.hours_per_shift, 'h'),
                             'shifts_per_day': (i.shifts_per_day, '')}, per_day, 'items'),
        manual_hours_per_day=_calc('manual_hours_per_day = items_per_day * manual_cycle_s / 3600',
                                   {'items_per_day': (per_day, 'items'), 'manual_cycle_s': (i.manual_cycle_s, 's')},
                                   manual_h, 'h'),
        supervision_hours_per_day=_calc('supervision_hours_per_day = supervision_min_per_shift / 60 * shifts_per_day',
                                        {'supervision_min_per_shift': (i.supervision_min_per_shift, 'min'),
                                         'shifts_per_day': (i.shifts_per_day, '')}, sup_h, 'h'),
        hours_freed_per_day=_calc('hours_freed_per_day = manual_hours_per_day - supervision_hours_per_day',
                                  {'manual_hours_per_day': (manual_h, 'h'), 'supervision_hours_per_day': (sup_h, 'h')},
                                  freed_d, 'h'),
        hours_freed_per_week=_calc('hours_freed_per_week = hours_freed_per_day * days_per_week',
                                   {'hours_freed_per_day': (freed_d, 'h'), 'days_per_week': (i.days_per_week, 'days')},
                                   freed_w, 'h'),
        hours_freed_per_year=_calc('hours_freed_per_year = hours_freed_per_day * days_per_year',
                                   {'hours_freed_per_day': (freed_d, 'h'), 'days_per_year': (i.days_per_year, 'days')},
                                   freed_y, 'h'),
        throughput_change=_calc('throughput_change = robot_items_per_hour / manual_items_per_hour - 1',
                                {'robot_items_per_hour': (robot_ph, 'items/h'),
                                 'manual_items_per_hour': (manual_ph, 'items/h')}, change, 'fraction'))


def robot_cycle_s(version) -> float | None:
    """The robot's cycle of a design version: the fleet period for a fleet version, else the simulation's duration."""
    fleet = getattr(version, 'fleet', None)
    if fleet is not None and fleet.period_s > 0:
        return float(fleet.period_s)
    sim = getattr(version, 'sim', None)
    return float(sim.duration_s) if sim is not None and sim.duration_s > 0 else None


def resolve_baseline(baseline, overrides: dict | None) -> dict:
    """{manual_cycle_s, operators, source} from the spec's baseline and the user's saved inputs (which win).
    manual_cycle_s is None when neither gives it (nothing is invented); operators default to 1. source is 'user' when the user supplied either value (an answer or an edit), else 'estimate'."""
    o = overrides or {}
    manual = o.get('manual_cycle_s') if o.get('manual_cycle_s') else None
    operators = o.get('operators') if o.get('operators') else None
    user = manual is not None or operators is not None
    if baseline is not None:
        user = user or baseline.source == 'user'
        manual = manual if manual is not None else baseline.manual_cycle_s
        operators = operators if operators is not None else baseline.operators
    return {'manual_cycle_s': manual,
            'operators': operators if operators is not None else DEFAULT_OPERATORS,
            'source': 'user' if user else 'estimate'}


def inputs_for(spec, cycle_s: float, overrides: dict | None = None) -> tuple[SavingsInputs, str]:
    """(SavingsInputs, baseline source) for a spec, a robot cycle and the project's saved `savings_inputs`."""
    o = overrides or {}
    b = resolve_baseline(spec.baseline, o)
    if b['manual_cycle_s'] is None:
        raise ValueError('no manual time per item is known')
    return SavingsInputs(
        robot_cycle_s=cycle_s, manual_cycle_s=b['manual_cycle_s'], operators=b['operators'],
        shifts_per_day=spec.physics_inputs.shifts_per_day,
        hours_per_shift=o.get('hours_per_shift', DEFAULT_HOURS_PER_SHIFT),
        days_per_week=o.get('days_per_week', DEFAULT_DAYS_PER_WEEK),
        days_per_year=o.get('days_per_year', DEFAULT_DAYS_PER_YEAR),
        supervision_min_per_shift=o.get('supervision_min_per_shift', DEFAULT_SUPERVISION_MIN_PER_SHIFT)), b['source']


def version_savings(version, overrides: dict | None = None) -> tuple[Savings, SavingsInputs, str] | None:
    """(Savings, inputs, baseline source) of a design version; None without a robot cycle or a manual time."""
    cycle = robot_cycle_s(version)
    if cycle is None or resolve_baseline(version.spec.baseline, overrides)['manual_cycle_s'] is None:
        return None
    inputs, source = inputs_for(version.spec, cycle, overrides)
    return savings(inputs), inputs, source


def baseline_from_answers(answers: dict | None, defaults: dict | None = None):
    """A Baseline from the answers to the `manual_cycle_s` and `operators` questions (source 'user'); None when
    neither was answered. The answers may be numbers or numeric text. An answer equal to the
    asked question's default (`defaults`, by question id) counts as not answered: the client sends untouched defaults."""
    from ..schemas import Baseline

    def num(key):
        v = (answers or {}).get(key)
        if isinstance(v, bool):
            return None
        try:
            x = float(str(v).strip().split()[0]) if isinstance(v, str) else float(v)
        except (TypeError, ValueError, IndexError):
            return None
        if not (x == x and 0 < x < 1e9):
            return None
        d = (defaults or {}).get(key)
        try:
            if d is not None and float(d) == x:
                return None
        except (TypeError, ValueError):
            pass
        return x
    manual, ops = num('manual_cycle_s'), num('operators')
    if ops is not None:
        ops = max(1, round(ops))
    answered = [n for n, v in (('manual time per item', manual), ('operators', ops)) if v is not None]
    if manual is None:        # without a manual time there is no baseline (operators alone say nothing)
        return None
    note = 'From your answers: ' + ' and '.join(answered) + '.'
    if ops is None:
        note += ' One person is assumed.'
    return Baseline(manual_cycle_s=min(manual, 86400),
                    operators=min(ops if ops is not None else DEFAULT_OPERATORS, 100), source='user', note=note)
