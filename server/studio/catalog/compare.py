"""Compare the generated design with a candidate robot on the same task. Severity is set by code from the checks."""
from ..schemas import Difference

BLOCKING = ('reach', 'payload', 'grip', 'collision')
MARGIN = 0.10


def show(check):
    if check is None or check.actual is None:
        return 'not available'
    state = {True: 'passes', False: 'fails', None: 'not checked'}[check.passed]
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


def differences(generated, candidate_sim, candidate):
    out = []
    own = {c.requirement_id: c for c in generated.sim.checks}
    seen = set()
    for check in candidate_sim.checks:
        seen.add(check.requirement_id)
        out.append(Difference(topic=check.name, generated=show(own.get(check.requirement_id)), candidate=show(check),
                              severity=severity(check)))
    for rid, check in own.items():
        if rid not in seen:
            out.append(Difference(topic=check.name, generated=show(check), candidate='not checked for the candidate', severity='minor'))
    if not any(c.requirement_id.startswith('cycle') for c in candidate_sim.checks):
        target = generated.spec.physics_inputs.cycle_time_s
        mine, theirs = generated.sim.duration_s, candidate_sim.duration_s
        level = 'major' if theirs > target else 'minor' if theirs > mine * (1 + MARGIN) else 'ok'
        out.append(Difference(topic='Task duration', generated=f'{mine:.3g} s', candidate=f'{theirs:.3g} s (target {target:.3g} s)', severity=level))
    gp, cp = generated.robot.params, candidate.params
    for topic, key, scale, unit in (('Rated payload', 'payload_kg', 1, 'kg'), ('Repeatability', 'repeatability_m', 1000, 'mm')):
        a, b = _find(gp, key), _find(cp, key)
        if a is not None and b is not None:
            level = 'ok' if abs(a - b) <= MARGIN * max(abs(a), 1e-12) else 'minor'
            out.append(Difference(topic=topic, generated=f'{a * scale:.3g} {unit}', candidate=f'{b * scale:.3g} {unit}', severity=level))
    ga, ca = _find(gp, 'max_torque_nm'), _find(cp, 'max_torque_nm')
    if ga and ca:
        for i, (a, b) in enumerate(zip(ga, ca), 1):
            out.append(Difference(topic=f'Joint {i} torque rating', generated=f'{a:.3g} N*m', candidate=f'{b:.3g} N*m',
                                  severity='ok' if b >= a * (1 - MARGIN) else 'minor'))
    return out


def _find(params, key):
    """A parameter from a family's params dict, looking one level down (mobile manipulators keep an `arm`)."""
    if key in params:
        return params[key]
    for value in params.values():
        if isinstance(value, dict) and key in value:
            return value[key]
    return None
