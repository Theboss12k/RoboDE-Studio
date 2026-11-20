// Parity check of the twin math: packages/core/src/twin.ts against contracts/twin_vectors.json (written by
// scripts/twin_vectors.py from server/studio/robot/twin.py). Fails when the vectors are stale or TS differs.
//   node scripts/twin-parity.mjs          (needs Node >= 22.18 / 23.6, which runs .ts files directly)
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(fileURLToPath(new URL("../", import.meta.url)));
const python = process.env.STUDIO_PYTHON || join(root, process.platform === "win32" ? ".venv/Scripts/python.exe" : ".venv/bin/python");
const failures = [];

if (existsSync(python)) {
  const r = spawnSync(python, [join(root, "scripts", "twin_vectors.py"), "--check"], { stdio: "inherit" });
  if (r.status !== 0) failures.push("twin_vectors.json is stale");
} else console.warn(`Python not found at ${python}: staleness of the vectors was not checked`);

const T = await import("../packages/core/src/twin.ts");
const vec = JSON.parse(readFileSync(join(root, "contracts", "twin_vectors.json"), "utf8"));
const near = (a, b, tol) => a.length === b.length && a.every((v, i) => Math.abs(v - b[i]) <= tol);
const fail = (msg) => failures.push(msg);
let checks = 0;

for (const c of vec.fk) { checks++; if (!near(T.chainFk(c.chain, c.q), c.tcp, 1e-7)) fail(`fk ${c.name}`); }

for (const c of vec.ik) {
  checks++;
  const r = T.ikSolve(c.chain, c.target, c.q0, c.opts);
  if (r.ok !== c.expected.ok) fail(`ik ${c.name}: ok ${r.ok} != ${c.expected.ok}`);
  if (Math.abs(r.posError - c.expected.posError) > 1e-5) fail(`ik ${c.name}: posError ${r.posError} vs ${c.expected.posError}`);
  if (c.compareQ && !near(r.q, c.expected.q, 1e-4)) fail(`ik ${c.name}: q ${r.q} vs ${c.expected.q}`);
}

for (const c of vec.clamp) {
  checks++;
  let q = c.q0, qd = c.qd0;
  c.steps.forEach((s, k) => {
    const o = T.clampStep(q, c.target, c.dt, c.limits, qd, c.speedScale, c.accelCap ?? undefined);
    q = o.q; qd = o.qd;
    const hits = o.hits.map(([i, kind]) => `${i}:${kind}`).sort();
    if (!near(q, s.q, 1e-7) || !near(qd, s.qd, 1e-6) || JSON.stringify(hits) !== JSON.stringify(s.hits)) {
      if (!failures.some((f) => f.startsWith(`clamp ${c.name}`))) fail(`clamp ${c.name}: step ${k} q ${q} vs ${s.q}, hits ${hits} vs ${s.hits}`);
    }
  });
}

for (const c of vec.torque) {
  checks++;
  const { gravity, inertia } = T.gravityInertia(c.chain, c.q, c.payload);
  const use = T.torqueUse(gravity, inertia, c.qdd, c.maxTorque);
  const cap = T.accelCapFromTorque(gravity, inertia, c.maxTorque, c.q.map(() => 5));
  if (!near(gravity, c.gravity, 1e-5) || !near(inertia, c.inertia, 1e-5) || !near(use, c.use, 1e-5) || !near(cap, c.cap, 1e-4)) fail(`torque ${c.name}`);
}

if (failures.length) { console.error(`twin parity FAILED (${failures.length}):\n  ` + failures.join("\n  ")); process.exit(1); }
console.log(`twin parity OK: ${checks} vector groups match (${vec.ik.length} IK, ${vec.clamp.length} clampStep sequences)`);
