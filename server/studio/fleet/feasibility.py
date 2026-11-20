"""Code-side trigger for a fleet: reasons why one robot is not a good answer for a task.

PRACTICAL_REACH_M and PRACTICAL_STROKE_M are documented heuristics (typical upper ends of catalogue serial arms and
single-frame gantries). They only SUGGEST a fleet; they never decide pass or fail of a design.
"""
from __future__ import annotations

from .. import families
from ..schemas import Requirement, RobotConcept, SimResult, TechnicalSpec

PRACTICAL_REACH_M = {'serial_arm': 2.5}     # longest-reach catalogue arms sit around 2.5-3 m
PRACTICAL_STROKE_M = {'gantry': 6.0}        # longer single-frame gantries need special frames and supports


def _cycle_shortfall(sim: SimResult, spec: TechnicalSpec) -> float | None:
    """Fastest feasible time / cycle time when the cycle_time check fails because the run is too long, else None
    (same reading as pipeline/design.py cycle_shortfall)."""
    c = next((c for c in sim.checks if c.requirement_id == 'cycle_time'), None)
    cycle = spec.physics_inputs.cycle_time_s
    if c is None or c.passed is not False or c.actual is None or c.actual <= cycle:
        return None
    return c.actual / cycle


def single_robot_shortfall(spec: TechnicalSpec, requirements: list[Requirement], concept: RobotConcept,
                           sim: SimResult) -> list[str]:
    """Plain-English reasons why the sized single robot falls short; [] = one robot is fine."""
    out = []
    reach = next((c for c in sim.checks if c.requirement_id == 'reach_max'), None)
    unreachable = [e for e in sim.events if e.kind == 'unreachable']
    if reach is not None and reach.passed is False:
        out.append(f'The reach check fails: {reach.detail or "the robot cannot reach every task point"}.')
    elif unreachable:
        out.append(f'{len(unreachable)} waypoint(s) cannot be reached by one robot.')
    fam = families.get(concept.family)
    p = fam.parse(concept.params)
    if concept.family in PRACTICAL_REACH_M:
        r = fam.capabilities(p).reach_max_m
        if r > PRACTICAL_REACH_M[concept.family]:
            out.append(f'The task needs a {concept.family.replace("_", " ")} reaching {r:.2f} m, above the practical '
                       f'{PRACTICAL_REACH_M[concept.family]:g} m of typical arms (a heuristic, not a check).')
    if concept.family in PRACTICAL_STROKE_M:
        s = max(getattr(p, 'x_stroke_m', 0.0), getattr(p, 'y_stroke_m', 0.0))
        if s > PRACTICAL_STROKE_M[concept.family]:
            out.append(f'The task needs a gantry stroke of {s:.2f} m, above the practical '
                       f'{PRACTICAL_STROKE_M[concept.family]:g} m of a single frame (a heuristic, not a check).')
    ratio = _cycle_shortfall(sim, spec)
    if ratio is not None:
        out.append(f'One robot needs {ratio * spec.physics_inputs.cycle_time_s:.1f} s per cycle, more than the '
                   f'{spec.physics_inputs.cycle_time_s:g} s cycle time, even after speed sizing.')
    return out
