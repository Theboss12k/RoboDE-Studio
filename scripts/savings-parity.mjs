// Parity check of the time-saved math: packages/core/src/savings.ts against contracts/savings_vectors.json (written by
// scripts/savings_vectors.py from server/studio/physics/savings.py). Fails when the vectors are stale or TS differs.
//   node scripts/savings-parity.mjs          (needs Node >= 22.18 / 23.6, which runs .ts files directly)
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(fileURLToPath(new URL("../", import.meta.url)));
const python = process.env.STUDIO_PYTHON || join(root, process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python");
const failures = [];

if (existsSync(python)) {
  const r = spawnSync(python, [join(root, "scripts", "savings_vectors.py"), "--check"], { stdio: "inherit" });
  if (r.status !== 0) failures.push("savings_vectors.json is stale");
} else console.warn(`Python not found at ${python}: staleness of the vectors was not checked`);

const S = await import("../packages/core/src/savings.ts");
const vec = JSON.parse(readFileSync(join(root, "contracts", "savings_vectors.json"), "utf8"));
const close = (a, b) => Math.abs(a - b) <= 1e-9 * Math.max(1, Math.abs(a), Math.abs(b));
let checks = 0;

for (const c of vec.savings) {
  checks++;
  const got = S.savings(c.inputs);
  const keys = Object.keys(c.expected);
  if (Object.keys(got).sort().join() !== [...keys].sort().join()) { failures.push(`savings ${c.name}: outputs differ`); continue; }
  for (const k of keys) {
    const e = c.expected[k], g = got[k];
    if (g.formula !== e.formula || g.unit !== e.unit || !close(g.result, e.result)) failures.push(`savings ${c.name}: ${k} ${g.result} vs ${e.result}`);
    const ei = Object.keys(e.inputs);
    if (Object.keys(g.inputs).join() !== ei.join() || ei.some((n) => g.inputs[n].unit !== e.inputs[n].unit || !close(g.inputs[n].value, e.inputs[n].value))) failures.push(`savings ${c.name}: ${k} inputs differ`);
  }
}
for (const c of vec.resolve) {
  checks++;
  const got = S.resolveBaseline(c.baseline, c.overrides);
  if (got.source !== c.expected.source || !close(got.manual_cycle_s, c.expected.manual_cycle_s) || !close(got.operators, c.expected.operators)) failures.push(`resolve ${c.name}: ${JSON.stringify(got)} vs ${JSON.stringify(c.expected)}`);
}

if (failures.length) { console.error(`savings parity FAILED (${failures.length}):\n  ` + failures.join("\n  ")); process.exit(1); }
console.log(`savings parity OK: ${checks} vector groups match (${vec.savings.length} savings, ${vec.resolve.length} baseline resolutions)`);
