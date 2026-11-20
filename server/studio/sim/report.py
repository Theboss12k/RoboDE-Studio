"""Turns requirement + actual value into a Check."""
from __future__ import annotations

from ..schemas import Calc, Check, Requirement


def _passes(comparator, actual, target):
    tol = 1e-9 * max(1.0, abs(target))
    if comparator == '>=':
        return actual >= target - tol
    if comparator == '<=':
        return actual <= target + tol
    return None


def check(requirement: Requirement, actual: float | None, calc: Calc | None = None, detail: str = '') -> Check:
    """The comparator decides: '>=' the robot's value must reach the target, '<=' stay under it, 'info' no verdict.
    A missing actual value gives passed=None (could not be checked)."""
    passed = None if actual is None else _passes(requirement.comparator, actual, requirement.target)
    return Check(requirement_id=requirement.id, name=requirement.name, required=requirement.target,
                 actual=None if actual is None else float(actual), unit=requirement.unit, passed=passed,
                 detail=detail, calc=calc)


def custom(rid: str, name: str, required, actual, unit: str, comparator: str = '>=', detail: str = '',
           calc: Calc | None = None, passed: bool | None = None) -> Check:
    """A check without a physics requirement (joint torque, collisions...). `passed` overrides the comparator."""
    if passed is None and required is not None and actual is not None:
        passed = _passes(comparator, actual, required)
    return Check(requirement_id=rid, name=name, required=required, actual=actual, unit=unit, passed=passed,
                 detail=detail, calc=calc)


def all_ok(checks: list[Check]) -> bool:
    return all(c.passed for c in checks if c.passed is not None)
