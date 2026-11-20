"""Task-specific checks, evaluated in code. Pure: no store access, no LLM.

The checks agent proposes a `CheckPlan`: an expression, a limit (an expression too), their units and a few constants.
This module decides what becomes a `Check`:

- the text is parsed with Python's `ast` and only a small whitelist of nodes is accepted (numbers, names, + - * / **,
  unary + -, and the calls sqrt abs min max sin cos tan). Nothing from the model is ever passed to eval, exec or compile;
- names resolve to pint Quantities from the variable catalogue (physics/quantities.py) and the plan's own constants;
- the expression and the limit must have the dimension of their declared units and of each other, every value must be
  finite, and every constant needs a unit and a justification.

A model's plan is a proposal until this code has evaluated it; the numbers in the resulting Check come from the
catalogue (stored spec, parameters and simulation) and from the declared constants only.
"""
from __future__ import annotations

import ast
import math
import re
import threading

from ..schemas import Calc, Check, Quantity

MAX_PLANS = 12
MAX_CONSTANTS = 6
MAX_EXPR_CHARS = 400
MAX_NODES = 120
MAX_POWER = 12.0
ID_PATTERN = re.compile(r'^[a-z][a-z0-9_]{0,40}$')
NAME_PATTERN = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,40}$')
FUNCTIONS = {'sqrt': (1, 1), 'abs': (1, 1), 'sin': (1, 1), 'cos': (1, 1), 'tan': (1, 1), 'min': (2, 6), 'max': (2, 6)}
BINARY = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)
UNARY = (ast.USub, ast.UAdd)


class PlanError(ValueError):
    """A plan code cannot accept; the message goes back to the model (once) and into `check_plan_rejected`."""


_REG = None
_LOCK = threading.Lock()


def registry():
    global _REG
    with _LOCK:
        if _REG is None:
            import pint
            _REG = pint.UnitRegistry()
        return _REG


def units(text):
    """A pint unit from a unit string ('N*m', 'rad/s**2', '1'); PlanError when it does not parse."""
    ureg = registry()
    text = '' if text is None else str(text).strip()
    if len(text) > 40:
        raise PlanError(f'unit "{text[:40]}..." is too long')
    if text in ('', '1'):
        return ureg.dimensionless
    try:
        return ureg.parse_units(text)
    except Exception as error:            # pint raises several error types for unknown or malformed units
        raise PlanError(f'unit "{text}" is not a known unit ({type(error).__name__})') from error


def quantity(value, unit):
    return registry().Quantity(float(value), units(unit))


def _describe(q):
    return str(q.units) if str(q.units) != 'dimensionless' else 'a plain number'


# --- parsing ----------------------------------------------------------------------------------------------------------

def parse(text):
    """The checked ast of an expression, and the names it uses (functions excluded). PlanError on anything outside the
    whitelist. The text is parsed, never executed."""
    if not isinstance(text, str) or not text.strip():
        raise PlanError('the expression is empty')
    if len(text) > MAX_EXPR_CHARS:
        raise PlanError(f'the expression is longer than {MAX_EXPR_CHARS} characters')
    try:
        tree = ast.parse(text.strip(), mode='eval')
    except (SyntaxError, ValueError, RecursionError, MemoryError) as error:
        raise PlanError(f'"{text[:80]}" is not a valid expression ({type(error).__name__})') from error
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise PlanError('the expression is too long')
    names = set()

    def visit(node):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise PlanError(f'only numbers are allowed as literals, not {type(node.value).__name__}')
            if not math.isfinite(float(node.value)):
                raise PlanError('a number in the expression is not finite')
        elif isinstance(node, ast.Name):
            if '__' in node.id:
                raise PlanError(f'name "{node.id}" is not allowed')
            names.add(node.id)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, BINARY):
            visit(node.left)
            visit(node.right)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, UNARY):
            visit(node.operand)
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
                raise PlanError(f'only these functions are allowed: {", ".join(sorted(FUNCTIONS))}')
            lo, hi = FUNCTIONS[node.func.id]
            if node.keywords or not lo <= len(node.args) <= hi:
                raise PlanError(f'{node.func.id}() takes {lo if lo == hi else f"{lo} to {hi}"} argument(s), no keywords')
            for a in node.args:
                visit(a)
        else:
            raise PlanError(f'"{type(node).__name__}" is not allowed in an expression '
                            '(use numbers, names, + - * / **, parentheses and sqrt abs min max sin cos tan)')

    visit(tree.body)
    return tree.body, names


# --- evaluation -------------------------------------------------------------------------------------------------------

def _real(q):
    mag = q.magnitude
    if isinstance(mag, complex) or not isinstance(mag, (int, float)) or not math.isfinite(mag):
        raise PlanError('the value is not a finite real number')
    return q


def _eval(node, env):
    ureg = registry()
    if isinstance(node, ast.Constant):
        return ureg.Quantity(float(node.value), ureg.dimensionless)
    if isinstance(node, ast.Name):
        return env[node.id]
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, env)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp):
        a, b = _eval(node.left, env), _eval(node.right, env)
        try:
            if isinstance(node.op, ast.Add):
                return _real(a + b)
            if isinstance(node.op, ast.Sub):
                return _real(a - b)
            if isinstance(node.op, ast.Mult):
                return _real(a * b)
            if isinstance(node.op, ast.Div):
                return _real(a / b)
            if b.dimensionality != ureg.dimensionless.dimensionality:
                raise PlanError(f'an exponent must be a plain number, not {_describe(b)}')
            e = float(b.to(ureg.dimensionless).magnitude)
            if abs(e) > MAX_POWER:
                raise PlanError(f'an exponent must be between -{MAX_POWER:g} and {MAX_POWER:g}')
            if a.magnitude < 0 and e != int(e):
                raise PlanError('a negative value cannot be raised to a fractional power')
            return _real(a ** e)
        except PlanError:
            raise
        except ZeroDivisionError as error:
            raise PlanError('division by zero') from error
        except (ArithmeticError, ValueError, TypeError) as error:
            raise PlanError(f'cannot combine {_describe(a)} and {_describe(b)} ({type(error).__name__}: {str(error)[:120]})') from error
    if isinstance(node, ast.Call):
        fn = node.func.id
        args = [_eval(a, env) for a in node.args]
        a0 = args[0]
        try:
            if fn == 'abs':
                return abs(a0)
            if fn == 'sqrt':
                if a0.magnitude < 0:
                    raise PlanError('sqrt of a negative value')
                return _real(a0 ** 0.5)
            if fn in ('min', 'max'):
                conv = [a0] + [a.to(a0.units) for a in args[1:]]
                return (min if fn == 'min' else max)(conv, key=lambda q: q.magnitude)
            if a0.dimensionality != ureg.dimensionless.dimensionality:
                raise PlanError(f'{fn}() needs an angle or a plain number, not {_describe(a0)}')
            rad = float(a0.to(ureg.radian).magnitude)
            return ureg.Quantity(getattr(math, fn)(rad), ureg.dimensionless)
        except PlanError:
            raise
        except (ArithmeticError, ValueError, TypeError) as error:
            raise PlanError(f'{fn}() cannot be applied here ({type(error).__name__}: {str(error)[:120]})') from error
    raise PlanError('unsupported expression')       # unreachable: parse() accepted only the nodes above


def _entries(catalogue):
    out = {}
    for e in catalogue:
        get = e.get if isinstance(e, dict) else (lambda k, e=e: getattr(e, k, None))
        name = get('name')
        if name and name not in out:
            out[name] = (float(get('value')), get('unit') or '1')
    return out


def _constants(plan, known):
    """{name: (value, unit)} of the plan's declared constants; PlanError when one is unusable."""
    constants = plan.get('constants') or []
    if not isinstance(constants, list):
        raise PlanError('constants must be a list')
    if len(constants) > MAX_CONSTANTS:
        raise PlanError(f'at most {MAX_CONSTANTS} constants per check (got {len(constants)})')
    out = {}
    for c in constants:
        if not isinstance(c, dict):
            raise PlanError('each constant needs name, value, unit and justification')
        name = c.get('name')
        if not isinstance(name, str) or not NAME_PATTERN.match(name) or '__' in name or name in FUNCTIONS:
            raise PlanError(f'constant name {name!r} is not a plain identifier')
        if name in known or name in out:
            raise PlanError(f'constant "{name}" repeats a catalogue name or another constant; use the catalogue value')
        value = c.get('value')
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise PlanError(f'constant "{name}" needs a finite number as its value')
        if not isinstance(c.get('unit'), str):
            raise PlanError(f'constant "{name}" needs a unit ("1" for a plain number)')
        units(c['unit'])
        if not isinstance(c.get('justification'), str) or len(c['justification'].strip()) < 3:
            raise PlanError(f'constant "{name}" needs a justification (where the number comes from)')
        out[name] = (float(value), c['unit'].strip() or '1', c['justification'].strip())
    return out


def _as_dict(plan):
    return plan.model_dump(mode='json') if hasattr(plan, 'model_dump') else dict(plan)


def evaluate_plan(plan, catalogue):
    """`(Check, '')` when the plan is sound, else `(None, reason)`. `plan` is a CheckPlan or a plain dict; `catalogue`
    the list of {name, value, unit, source} entries of physics.quantities.catalogue."""
    try:
        return _evaluate(_as_dict(plan), catalogue), ''
    except PlanError as error:
        return None, str(error)


def _evaluate(plan, catalogue):
    pid = plan.get('id')
    if not isinstance(pid, str) or not ID_PATTERN.match(pid):
        raise PlanError(f'id {pid!r} must match ^[a-z][a-z0-9_]{{0,40}}$')
    if plan.get('comparator') not in ('>=', '<='):
        raise PlanError('comparator must be ">=" or "<="')
    if not str(plan.get('name') or '').strip():
        raise PlanError('the check needs a name')
    base = _entries(catalogue)
    consts = _constants(plan, base)
    expr, expr_names = parse(plan.get('expression'))
    lim, lim_names = parse(plan.get('limit'))
    used = (expr_names | lim_names) - {'pi'}
    unknown = sorted(n for n in used if n not in base and n not in consts)
    if unknown:
        raise PlanError('unknown name(s): ' + ', '.join(unknown) + ' (use only catalogue names and your declared constants)')
    u_expr, u_lim = units(plan.get('unit')), units(plan.get('unit_limit'))
    ureg = registry()
    env = {'pi': ureg.Quantity(math.pi, ureg.dimensionless)}
    for n in used:
        value, unit = (base[n][0], base[n][1]) if n in base else (consts[n][0], consts[n][1])
        env[n] = quantity(value, unit)
    got = _real(_eval(expr, env))
    bound = _real(_eval(lim, env))
    if got.dimensionality != ureg.Quantity(1.0, u_expr).dimensionality:
        raise PlanError(f'the expression has dimension {got.units}, which does not match its unit "{plan.get("unit")}"')
    if bound.dimensionality != ureg.Quantity(1.0, u_lim).dimensionality:
        raise PlanError(f'the limit has dimension {bound.units}, which does not match unit_limit "{plan.get("unit_limit")}"')
    try:
        actual = float(got.to(u_lim).magnitude)
        required = float(bound.to(u_lim).magnitude)
    except Exception as error:
        raise PlanError(f'the expression ({got.units}) and the limit ({bound.units}) have different dimensions') from error
    if not (math.isfinite(actual) and math.isfinite(required)):
        raise PlanError('the result is not finite')
    tol = 1e-9 * max(1.0, abs(required))
    passed = actual >= required - tol if plan['comparator'] == '>=' else actual <= required + tol
    unit_limit = str(plan.get('unit_limit') or '1').strip() or '1'
    inputs = {n: Quantity(value=env[n].magnitude, unit=(base[n][1] if n in base else consts[n][1])) for n in sorted(used)}
    note = '; '.join(f'{n} = {v:g} {u} ({why})' for n, (v, u, why) in consts.items())
    formula = f'{plan["expression"].strip()} {plan["comparator"]} {plan["limit"].strip()}'
    calc = Calc(formula=formula, inputs=inputs, result=actual, unit=unit_limit,
                note=('Declared constants: ' + note) if note else '')
    return Check(requirement_id=f'planned_{pid}', name=str(plan['name']).strip()[:120], required=required, actual=actual,
                 unit=unit_limit, passed=passed, detail=str(plan.get('why') or '')[:500], calc=calc, source='planned')


def evaluate_plans(plans, catalogue):
    """(accepted plans, checks, rejected) for a list of plans (CheckPlan or dict). Duplicate ids and plans beyond
    MAX_PLANS are dropped; `rejected` is [{'id', 'reason'}] for plans code could not accept."""
    accepted, checks, rejected, seen = [], [], [], set()
    for plan in plans:
        data = _as_dict(plan)
        pid = data.get('id') if isinstance(data.get('id'), str) else ''
        if pid in seen:
            continue
        seen.add(pid)
        if len(accepted) + len(rejected) >= MAX_PLANS:
            break
        check, reason = evaluate_plan(data, catalogue)
        if check is None:
            rejected.append({'id': pid[:60], 'reason': reason[:600]})
        else:
            accepted.append(plan if hasattr(plan, 'model_dump') else data)
            checks.append(check)
    return accepted, checks, rejected
