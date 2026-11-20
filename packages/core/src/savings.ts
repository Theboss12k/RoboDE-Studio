// Time saved: person-hours a robot frees compared with doing the same items by hand. DOM-free, no runtime imports.
// The same formulas, names and Calc texts as server/studio/physics/savings.py; parity with Python is checked by
// `node scripts/savings-parity.mjs` against contracts/savings_vectors.json (part of `npm run contracts:check`).
// The robot cycle is the kinematic simulation's: this is an estimate, not a time study.
import type { Baseline, Calc, Savings, SavingsInputs } from "./generated/types";

export const DEFAULT_OPERATORS = 1;
export const DEFAULT_HOURS_PER_SHIFT = 8;
export const DEFAULT_DAYS_PER_WEEK = 5;
export const DEFAULT_DAYS_PER_YEAR = 250;
export const DEFAULT_SUPERVISION_MIN_PER_SHIFT = 15;

/** The project's saved inputs (project view `savings_inputs`, PUT /projects/{id}/baseline). */
export interface SavingsOverrides {
  manual_cycle_s?: number; operators?: number; hours_per_shift?: number; days_per_week?: number; days_per_year?: number;
  supervision_min_per_shift?: number;
}

function calc(formula: string, inputs: Record<string, [number, string]>, result: number, unit: string): Calc {
  const q: Calc["inputs"] = {};
  for (const [k, [value, u]] of Object.entries(inputs)) q[k] = { value, unit: u };
  return { formula, inputs: q, result, unit, note: "" };
}

/** The time-saved outputs, each with its calculation (`result` is the value). Throws on an input that makes no sense. */
export function savings(i: SavingsInputs): Savings {
  if (!(i.robot_cycle_s > 0)) throw new Error("robot_cycle_s must be above 0");
  if (!(i.manual_cycle_s > 0)) throw new Error("manual_cycle_s must be above 0");
  if (!(i.operators >= 1)) throw new Error("operators must be at least 1");
  const robotPh = 3600 / i.robot_cycle_s;
  const manualPh = i.operators * 3600 / i.manual_cycle_s;
  const perDay = robotPh * i.hours_per_shift * i.shifts_per_day;
  const manualH = perDay * i.manual_cycle_s / 3600;
  const supH = i.supervision_min_per_shift / 60 * i.shifts_per_day;
  const freedD = manualH - supH;
  const freedW = freedD * i.days_per_week;
  const freedY = freedD * i.days_per_year;
  const change = robotPh / manualPh - 1;
  return {
    robot_items_per_hour: calc("robot_items_per_hour = 3600 / robot_cycle_s", { robot_cycle_s: [i.robot_cycle_s, "s"] }, robotPh, "items/h"),
    manual_items_per_hour: calc("manual_items_per_hour = operators * 3600 / manual_cycle_s", { operators: [i.operators, ""], manual_cycle_s: [i.manual_cycle_s, "s"] }, manualPh, "items/h"),
    items_per_day: calc("items_per_day = robot_items_per_hour * hours_per_shift * shifts_per_day",
      { robot_items_per_hour: [robotPh, "items/h"], hours_per_shift: [i.hours_per_shift, "h"], shifts_per_day: [i.shifts_per_day, ""] }, perDay, "items"),
    manual_hours_per_day: calc("manual_hours_per_day = items_per_day * manual_cycle_s / 3600", { items_per_day: [perDay, "items"], manual_cycle_s: [i.manual_cycle_s, "s"] }, manualH, "h"),
    supervision_hours_per_day: calc("supervision_hours_per_day = supervision_min_per_shift / 60 * shifts_per_day",
      { supervision_min_per_shift: [i.supervision_min_per_shift, "min"], shifts_per_day: [i.shifts_per_day, ""] }, supH, "h"),
    hours_freed_per_day: calc("hours_freed_per_day = manual_hours_per_day - supervision_hours_per_day", { manual_hours_per_day: [manualH, "h"], supervision_hours_per_day: [supH, "h"] }, freedD, "h"),
    hours_freed_per_week: calc("hours_freed_per_week = hours_freed_per_day * days_per_week", { hours_freed_per_day: [freedD, "h"], days_per_week: [i.days_per_week, "days"] }, freedW, "h"),
    hours_freed_per_year: calc("hours_freed_per_year = hours_freed_per_day * days_per_year", { hours_freed_per_day: [freedD, "h"], days_per_year: [i.days_per_year, "days"] }, freedY, "h"),
    throughput_change: calc("throughput_change = robot_items_per_hour / manual_items_per_hour - 1", { robot_items_per_hour: [robotPh, "items/h"], manual_items_per_hour: [manualPh, "items/h"] }, change, "fraction"),
  };
}

/** The robot's cycle of a design version: the fleet period for a fleet version, else the simulation's duration. */
export function robotCycleS(version: { sim?: { duration_s: number } | null; fleet?: { period_s: number } | null }): number | null {
  if (version.fleet && version.fleet.period_s > 0) return version.fleet.period_s;
  return version.sim && version.sim.duration_s > 0 ? version.sim.duration_s : null;
}

const set = (v: number | undefined | null): v is number => typeof v === "number" && v > 0;

/** Manual time and people from the spec's baseline and the user's saved inputs (which win). `manual_cycle_s` is null when neither gives it (nothing is invented). `source` is "user" when the user
 *  supplied either value (an answer or an edit), else "estimate". */
export function resolveBaseline(baseline: Baseline | null | undefined, overrides?: SavingsOverrides | null): { manual_cycle_s: number | null; operators: number; source: "user" | "estimate" } {
  const o = overrides ?? {};
  let manual = set(o.manual_cycle_s) ? o.manual_cycle_s : undefined;
  let operators = set(o.operators) ? o.operators : undefined;
  let user = manual !== undefined || operators !== undefined;
  if (baseline) {
    user = user || baseline.source === "user";
    manual = manual ?? baseline.manual_cycle_s;
    operators = operators ?? baseline.operators;
  }
  return { manual_cycle_s: manual ?? null, operators: operators ?? DEFAULT_OPERATORS, source: user ? "user" : "estimate" };
}

/** SavingsInputs and the baseline source for a spec, a robot cycle and the project's saved inputs. */
export function savingsInputsFor(spec: { baseline?: Baseline | null; physics_inputs: { shifts_per_day: number } }, cycleS: number, overrides?: SavingsOverrides | null): { inputs: SavingsInputs; source: "user" | "estimate" } {
  const o = overrides ?? {};
  const b = resolveBaseline(spec.baseline, o);
  if (b.manual_cycle_s === null) throw new Error("no manual time per item is known");
  return {
    source: b.source,
    inputs: {
      robot_cycle_s: cycleS, manual_cycle_s: b.manual_cycle_s, operators: b.operators, shifts_per_day: spec.physics_inputs.shifts_per_day,
      hours_per_shift: o.hours_per_shift ?? DEFAULT_HOURS_PER_SHIFT, days_per_week: o.days_per_week ?? DEFAULT_DAYS_PER_WEEK,
      days_per_year: o.days_per_year ?? DEFAULT_DAYS_PER_YEAR, supervision_min_per_shift: o.supervision_min_per_shift ?? DEFAULT_SUPERVISION_MIN_PER_SHIFT,
    },
  };
}

/** The time saved by a design version (or null without a robot cycle), with the inputs used and the baseline source. */
export function versionSavings(version: { spec: { baseline?: Baseline | null; physics_inputs: { shifts_per_day: number } }; sim?: { duration_s: number } | null; fleet?: { period_s: number } | null }, overrides?: SavingsOverrides | null): { savings: Savings; inputs: SavingsInputs; source: "user" | "estimate" } | null {
  const cycle = robotCycleS(version);
  if (cycle === null || resolveBaseline(version.spec.baseline, overrides).manual_cycle_s === null) return null;
  const { inputs, source } = savingsInputsFor(version.spec, cycle, overrides);
  return { savings: savings(inputs), inputs, source };
}

/** The caveat shown with every time-saved figure (the manufacturer PDF prints the same words). */
export function savingsLabel(source: "user" | "estimate"): string {
  return `Estimate based on ${source === "user" ? "your figures" : "estimated figures"}; kinematic simulation, not a time study.`;
}
