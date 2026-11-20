"""Task-specific checks in the design pipeline: ask the checks agent once, evaluate its plans in code, and re-evaluate
the stored plans (`DesignVersion.check_plan`) whenever a version is simulated again, without a new model call.

Pure with respect to the store: plain inputs in, results out. A failure of the agent call itself adds no checks and no
progress line, so the version is unchanged."""
from ..agents import checks as checks_agent
from ..llm.usage import set_context
from ..physics import formulas, quantities
from ..sim import report


def without_planned(sim):
    return [c for c in sim.checks if c.source != 'planned']


def with_planned(sim, planned):
    """The simulation result with its planned checks replaced by `planned`; `ok` counts them."""
    checks = [*without_planned(sim), *planned]
    return sim.model_copy(update={'checks': checks, 'ok': report.all_ok(checks)})


def reevaluate(spec, requirements, scene, concept, sim, plan):
    """`sim` with the stored `plan` (CheckPlans) evaluated in code against this simulation: (sim, accepted plans,
    rejected [{id, reason}]). A plan that no longer evaluates (a name that is gone) is dropped and reported."""
    base = without_planned(sim)
    catalogue = quantities.catalogue(spec, concept, requirements, sim.model_copy(update={'checks': base}), scene)
    accepted, planned, rejected = formulas.evaluate_plans(plan, catalogue)
    return with_planned(sim, planned), accepted, rejected


async def plan_and_apply(llms, spec, requirements, scene, concept, sim, progress, previous=None):
    """Ask the checks agent for plans, add the evaluated checks to the simulation and recompute `ok`.
    Returns (sim, plans, rejected), or None when the agent could not be used (nothing changes, no progress line)."""
    try:
        set_context(task='checks')
        base = sim.model_copy(update={'checks': without_planned(sim)})
        catalogue = quantities.catalogue(spec, concept, requirements, base, scene)
        result = await checks_agent.run(llms.for_role('checks'), spec, concept, requirements, base.checks, catalogue,
                                        previous=[p.model_dump(mode='json') for p in previous] if previous else None)
    except Exception:     # a failed call, a missing role or an unusable answer never fails or changes the design
        return None
    progress(f'Task-specific checks: {len(result["plans"])} planned')
    return with_planned(base, result['checks']), result['plans'], result['rejected']
