"""Compare the generated design with a candidate robot on the same task. Severity is set by code from the checks."""
from ..schemas import Difference

BLOCKING = ('reach', 'payload', 'grip', 'collision')
MARGIN = 0.10


def show(check, candidate=False):
    """A check as text. A verdict without a number reads 'passes'/'fails' plus its detail; an unchecked value of the
    candidate is 'not published' (the manufacturer did not state it), with the reason."""
    if check is None:
        return 'not available'
    why = f' ({check.detail})' if check.detail else ''
    if check.passed is None:
        return f'not published{why}' if candidate else f'not checked{why}'
    state = 'passes' if check.passed else 'fails'
    if check.actual is None:
        return state + why
    need = f', needs {check.required:.3g}' if check.required is not None else ''
    return f'{check.actual:.3g} {check.unit} ({state}{need})'


def severity(check):
    if check is None or check.passed is None:
        return 'minor'
    if check.passed is False:
        return 'blocking' if check.requirement_id.startswith(BLOCKING) else 'major'
    if check.required and check.actual is not None and abs(check.actual - check.required) <= MARGIN * abs(check.required):
        return 'minor'
    return 'ok'


def differences(generated, candidate_sim, candidate, provenance=None):
    """Rows of the comparison. Requirements with comparator `info` have no verdict and are left out; the rated payload
    and repeatability rows appear only when no requirement row (payload, accuracy) covers them."""
    out = []
    info = {r.id for r in getattr(generated, 'requirements', []) if r.comparator == 'info'}
    provenance = provenance or {}
    own = {c.requirement_id: c for c in generated.sim.checks}
    seen = set()
    for check in candidate_sim.checks:
        seen.add(check.requirement_id)
        if check.requirement_id in info:
            continue
        out.append(Difference(topic=check.name, generated=show(own.get(check.requirement_id)), candidate=show(check, True),
                              severity=severity(check)))
    for rid, check in own.items():
        if rid not in seen and rid not in info:
            out.append(Difference(topic=check.name, generated=show(check), candidate='not checked for the candidate', severity='minor'))
    if not any(c.requirement_id.startswith('cycle') for c in candidate_sim.checks):
        target = generated.spec.physics_inputs.cycle_time_s
        mine, theirs = generated.sim.duration_s, candidate_sim.duration_s
        level = 'major' if theirs > target else 'minor' if theirs > mine * (1 + MARGIN) else 'ok'
        out.append(Difference(topic='Task duration', generated=f'{mine:.3g} s', candidate=f'{theirs:.3g} s (target {target:.3g} s)', severity=level))
    gp, cp = generated.robot.params, candidate.params
    for topic, key, scale, unit, covered in (('Rated payload', 'payload_kg', 1, 'kg', 'payload'),
                                             ('Repeatability', 'repeatability_m', 1000, 'mm', 'accuracy')):
        if covered in seen:
            continue
        a, b = _find(gp, key), _find(cp, key)
        if a is not None and b is not None:
            level = 'ok' if abs(a - b) <= MARGIN * max(abs(a), 1e-12) else 'minor'
            out.append(Difference(topic=topic, generated=f'{a * scale:.3g} {unit}', candidate=f'{b * scale:.3g} {unit}', severity=level))
    ga, ca = _find(gp, 'max_torque_nm'), _find(cp, 'max_torque_nm')
    if ga and ca and not _default(provenance, 'max_torque_nm'):
        for i, (a, b) in enumerate(zip(ga, ca), 1):
            out.append(Difference(topic=f'Joint {i} torque rating', generated=f'{a:.3g} N*m', candidate=f'{b:.3g} N*m',
                                  severity='ok' if b >= a * (1 - MARGIN) else 'minor'))
    return out


def _default(provenance, key):
    return any(k.rsplit('.', 1)[-1] == key and text.startswith('default') for k, text in provenance.items())


def _find(params, key):
    """A parameter from a family's params dict, looking one level down (mobile manipulators keep an `arm`)."""
    if key in params:
        return params[key]
    for value in params.values():
        if isinstance(value, dict) and key in value:
            return value[key]
    return None
