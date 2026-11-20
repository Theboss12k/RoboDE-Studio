// Digital-twin math shared with the Python twin (server/studio/robot/twin.py): chain forward kinematics, bounded
// damped-least-squares IK (tool down), one limit-clamped step, and the torque use of a joint. DOM-free, no imports.
// Parity with Python is checked by `node scripts/twin-parity.mjs` against contracts/twin_vectors.json.
//
// Torque use here is the same formula as Python's `torque_use`: (|gravity| + |inertia * qdd|) / max_torque, with
// gravity and inertia from point masses on the chain (`gravityInertia`). Link masses are point masses without
// rotational inertia: a static + acceleration estimate, not a dynamics simulation.

export type V3 = [number, number, number];
export interface ChainElement {
  name: string; type: "fixed" | "revolute" | "prismatic"; xyz: V3; rpy: V3; axis: V3; lower: number; upper: number;
}
export interface MassPoint { after: number; local: V3; mass: number }
/** Kinematic chain in the base frame (Python `chain_for`). `active` = joints IK may move (indices into the movable joints). */
export interface TwinChain { elements: ChainElement[]; tool: V3; active: number[]; masses: MassPoint[] }
export interface JointLimit { lower: number; upper: number; max_speed: number; max_accel: number; max_torque?: number }
export type LimitKind = "position" | "speed" | "acceleration" | "torque";
export interface IkOptions { toolDown?: boolean; maxIter?: number }
export interface IkResult { q: number[]; posError: number; oriError: number; ok: boolean; iterations: number; toolDown: boolean }

const TOL_M = 1e-3, TOL_ORI = 0.02, LAMBDA = 0.05, W_ORI = 0.3, STEP_MAX = 0.4, EPS = 1e-9, G = 9.80665;
const TORQUE_ACCEL_FLOOR = 0.05;
const DOWN: V3 = [0, 0, -1];

type M4 = number[]; // 4x4 row-major

const eye = (): M4 => [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
function mul(a: M4, b: M4): M4 {
  const o = new Array<number>(16).fill(0);
  for (let i = 0; i < 4; i++) for (let k = 0; k < 4; k++) { const v = a[i * 4 + k]; if (v !== 0) for (let j = 0; j < 4; j++) o[i * 4 + j] += v * b[k * 4 + j]; }
  return o;
}
function rpyMat(r: number, p: number, y: number): M4 {
  const cr = Math.cos(r), sr = Math.sin(r), cp = Math.cos(p), sp = Math.sin(p), cy = Math.cos(y), sy = Math.sin(y);
  return [cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr, 0,
    sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr, 0,
    -sp, cp * sr, cp * cr, 0, 0, 0, 0, 1];
}
function axisRot(axis: V3, a: number): M4 {
  const [x, y, z] = axis, c = Math.cos(a), s = Math.sin(a), C = 1 - c;
  return [c + x * x * C, x * y * C - z * s, x * z * C + y * s, 0,
    y * x * C + z * s, c + y * y * C, y * z * C - x * s, 0,
    z * x * C - y * s, z * y * C + x * s, c + z * z * C, 0, 0, 0, 0, 1];
}
const movable = (chain: TwinChain) => chain.elements.filter((e) => e.type !== "fixed");

/** ([base frame, frame after element 0, ...], TCP pose), 4x4 row-major in the base frame. */
export function chainFrames(chain: TwinChain, q: ArrayLike<number>): { frames: M4[]; tcp: M4 } {
  let T = eye();
  const frames = [T];
  let k = 0;
  for (const el of chain.elements) {
    const A = rpyMat(el.rpy[0], el.rpy[1], el.rpy[2]);
    A[3] = el.xyz[0]; A[7] = el.xyz[1]; A[11] = el.xyz[2];
    T = mul(T, A);
    if (el.type !== "fixed") {
      const M = eye();
      if (el.type === "prismatic") { M[3] = el.axis[0] * q[k]; M[7] = el.axis[1] * q[k]; M[11] = el.axis[2] * q[k]; }
      else { const R = axisRot(el.axis, q[k]); for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) M[i * 4 + j] = R[i * 4 + j]; }
      T = mul(T, M);
      k++;
    }
    frames.push(T);
  }
  const tool = eye();
  tool[3] = chain.tool[0]; tool[7] = chain.tool[1]; tool[11] = chain.tool[2];
  return { frames, tcp: mul(T, tool) };
}
/** Tool-centre-point position in the base frame. */
export function chainFk(chain: TwinChain, q: ArrayLike<number>): V3 {
  const t = chainFrames(chain, q).tcp;
  return [t[3], t[7], t[11]];
}
const toolZ = (t: M4): V3 => [t[2], t[6], t[10]];
const cross = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const norm = (v: ArrayLike<number>) => Math.hypot(...Array.from(v));
const clip = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), hi);

function residual(chain: TwinChain, q: number[], target: V3, toolDown: boolean): number[] {
  const t = chainFrames(chain, q).tcp;
  const r = [t[3] - target[0], t[7] - target[1], t[11] - target[2]];
  if (toolDown) { const c = cross(toolZ(t), DOWN); r.push(W_ORI * c[0], W_ORI * c[1], W_ORI * c[2]); }
  return r;
}
/** Solves A x = b (A square, Gaussian elimination with partial pivoting). */
function solve(A: number[][], b: number[]): number[] {
  const n = b.length, M = A.map((row, i) => [...row, b[i]]);
  for (let c = 0; c < n; c++) {
    let p = c;
    for (let r = c + 1; r < n; r++) if (Math.abs(M[r][c]) > Math.abs(M[p][c])) p = r;
    [M[c], M[p]] = [M[p], M[c]];
    for (let r = c + 1; r < n; r++) { const f = M[r][c] / M[c][c]; for (let k = c; k <= n; k++) M[r][k] -= f * M[c][k]; }
  }
  const x = new Array<number>(n).fill(0);
  for (let r = n - 1; r >= 0; r--) { let s = M[r][n]; for (let k = r + 1; k < n; k++) s -= M[r][k] * x[k]; x[r] = s / M[r][r]; }
  return x;
}
function dls(chain: TwinChain, q0: number[], lo: number[], hi: number[], target: V3, toolDown: boolean, maxIter: number) {
  let q = q0.map((v, i) => clip(v, lo[i], hi[i]));
  const active = chain.active;
  for (let it = 0; it < maxIter; it++) {
    const r = residual(chain, q, target, toolDown);
    if (norm(r.slice(0, 3)) < 1e-5 && (!toolDown || norm(r.slice(3)) < 1e-5)) return { q, it };
    const m = r.length;
    const J: number[][] = Array.from({ length: m }, () => new Array<number>(active.length).fill(0));
    active.forEach((i, k) => {
      const a = [...q], b = [...q];
      a[i] += 1e-6; b[i] -= 1e-6;
      const ra = residual(chain, a, target, toolDown), rb = residual(chain, b, target, toolDown);
      for (let row = 0; row < m; row++) J[row][k] = (ra[row] - rb[row]) / 2e-6;
    });
    const JJ = J.map((ri, a) => J.map((rj, b) => ri.reduce((s, v, k) => s + v * rj[k], 0) + (a === b ? LAMBDA * LAMBDA : 0)));
    const y = solve(JJ, r);
    const step = active.map((_, k) => -J.reduce((s, row, a) => s + row[k] * y[a], 0));
    const n = norm(step);
    const f = n > STEP_MAX ? STEP_MAX / n : 1;
    q = [...q];
    active.forEach((i, k) => { q[i] += step[k] * f; });
    q = q.map((v, i) => clip(v, lo[i], hi[i]));
  }
  return { q, it: maxIter };
}

/** Closed-form planar tool-down seeds for an anthropomorphic chain (Python `_analytic_seeds`); [] for other chains. */
function analyticSeeds(chain: TwinChain, target: V3, n: number): number[][] {
  const els = chain.elements;
  const ax = (e: ChainElement) => e.axis.join(",");
  if (n < 4 || els.length < 5 || ax(els[0]) !== "0,0,1" || ax(els[1]) !== "0,1,0" || ax(els[2]) !== "0,1,0" || ax(els[3]) !== "0,1,0") return [];
  const h = els[1].xyz[2], L1 = els[2].xyz[2], L2 = els[3].xyz[2], wrist = els[4].xyz[2];
  const [x, y, z] = target;
  const dz = z + chain.tool[2] + wrist - h, yaw = Math.atan2(y, x);
  const out: number[][] = [];
  for (const [q1, r] of [[yaw, Math.hypot(x, y)], [yaw - (yaw < 0 || Object.is(yaw, -0) ? -1 : 1) * Math.PI, -Math.hypot(x, y)]]) {
    const D = Math.max(Math.hypot(r, dz), 1e-9), phi = Math.atan2(r, dz);
    const alpha = Math.acos(clip((L1 * L1 + D * D - L2 * L2) / (2 * L1 * D), -1, 1));
    for (const sgn of [1, -1]) {
      const a2 = phi - sgn * alpha, a3 = Math.atan2(r - L1 * Math.sin(a2), dz - L1 * Math.cos(a2));
      const q = new Array<number>(n).fill(0);
      q[0] = q1; q[1] = a2; q[2] = a3 - a2; q[3] = Math.PI - a3;
      out.push(q);
    }
  }
  return out;
}

/** Deterministic IK seeds (same list as Python `ik_seeds`). */
export function ikSeeds(chain: TwinChain, q0: number[], lo: number[], hi: number[], target: V3): number[][] {
  const n = q0.length;
  const c = (s: number[]) => s.map((v, i) => clip(v, lo[i], hi[i]));
  const seeds = [c(q0)];
  const mv = movable(chain);
  const revZ = n > 0 && mv[0].type === "revolute" && mv[0].axis[0] === 0 && mv[0].axis[1] === 0 && mv[0].axis[2] === 1;
  const yaw = Math.atan2(target[1], target[0]);
  if (revZ) for (const a of [yaw, yaw - (yaw < 0 || Object.is(yaw, -0) ? -1 : 1) * Math.PI]) { const s = [...seeds[0]]; s[0] = a; seeds.push(c(s)); }
  for (const g of analyticSeeds(chain, target, n)) seeds.push(c(g));
  seeds.push(c(new Array<number>(n).fill(0)));
  seeds.push(lo.map((l, i) => (l + hi[i]) / 2));
  if (revZ && n >= 3 && mv[1].type === "revolute" && mv[2].type === "revolute") {
    for (const [a, b] of [[0.7, 1.2], [0.7, -1.2], [-0.7, 1.2], [-0.7, -1.2]]) { const s = new Array<number>(n).fill(0); s[0] = yaw; s[1] = a; s[2] = b; seeds.push(c(s)); }
  }
  return seeds;
}

/** Bounded DLS to `target` (base frame), tool down first and dropped when it cannot be met. Matches Python `ik_solve`. */
export function ikSolve(chain: TwinChain, target: V3, q0: number[], opts: IkOptions = {}): IkResult {
  const toolDownWanted = opts.toolDown ?? true, maxIter = opts.maxIter ?? 120;
  const mv = movable(chain);
  const lo = mv.map((e) => e.lower), hi = mv.map((e) => e.upper);
  const seeds = ikSeeds(chain, q0, lo, hi, target);
  let best: { good: boolean; res: IkResult } | undefined;
  for (const useTool of toolDownWanted ? [true, false] : [false]) {
    for (const s of seeds) {
      const { q, it } = dls(chain, s, lo, hi, target, useTool, maxIter);
      const t = chainFrames(chain, q).tcp;
      const pos = norm([t[3] - target[0], t[7] - target[1], t[11] - target[2]]);
      const ori = Math.acos(clip(toolZ(t)[0] * DOWN[0] + toolZ(t)[1] * DOWN[1] + toolZ(t)[2] * DOWN[2], -1, 1));
      const good = pos < TOL_M && (!useTool || ori < TOL_ORI);
      const res: IkResult = { q, posError: pos, oriError: ori, ok: pos < TOL_M, iterations: it, toolDown: useTool };
      if (!best || (good && !best.good) || (good === best.good && -pos > -best.res.posError)) best = { good, res };
      if (good) return res;
    }
    if (best?.good) break;
  }
  return best!.res;
}

/** One step of every joint toward `target` under `limits` (same order as q). Matches Python `clampStep`: target
 *  clamped to the range ('position'), cruise speed max_speed*s never above max_speed ('speed' when s > 1),
 *  acceleration max_accel*s^2 never above max_accel ('acceleration') nor `accelCap[i]` ('torque'); velocity follows
 *  sqrt(2 a |error|) and changes by at most a*dt; the position stops exactly on the target. */
export function clampStep(q: number[], target: number[], dt: number, limits: JointLimit[], qd: number[],
  speedScale: number | number[] = 1, accelCap?: number[]): { q: number[]; qd: number[]; hits: [number, LimitKind][] } {
  const qn: number[] = [], vn: number[] = [], hits: [number, LimitKind][] = [];
  q.forEach((qi, i) => {
    const L = limits[i];
    const s = Array.isArray(speedScale) ? speedScale[i] : speedScale;
    const tgt = clip(target[i], L.lower, L.upper);
    if (Math.abs(tgt - target[i]) > EPS) hits.push([i, "position"]);
    const aReq = L.max_accel * s * s;
    const cap = accelCap ? accelCap[i] : Infinity;
    const aUse = Math.min(aReq, L.max_accel, cap);
    const e = tgt - qi;
    let vDes = Math.abs(e) > 0 ? Math.sign(e) * Math.min(L.max_speed * s, Math.sqrt(2 * aUse * Math.abs(e)), Math.abs(e) / dt) : 0;
    if (Math.abs(vDes) > L.max_speed * (1 + EPS)) { vDes = Math.sign(vDes) * L.max_speed; hits.push([i, "speed"]); }
    let dv = vDes - qd[i];
    if (Math.abs(dv) > aUse * dt + EPS) {
      dv = Math.sign(dv) * aUse * dt;
      if (cap < Math.min(aReq, L.max_accel) - EPS) hits.push([i, "torque"]);
      else if (aReq > L.max_accel + EPS) hits.push([i, "acceleration"]);
    }
    let v = clip(qd[i] + dv, -L.max_speed, L.max_speed);
    let x = qi + v * dt;
    if ((x - tgt) * (qi - tgt) <= 0 || Math.abs(tgt - x) < 1e-9) { x = tgt; v = 0; }
    else if (x < L.lower || x > L.upper) { x = clip(x, L.lower, L.upper); v = 0; hits.push([i, "position"]); }
    qn.push(x); vn.push(v);
  });
  return { q: qn, qd: vn, hits };
}

/** Static gravity torque/force (sign kept) and point-mass inertia per joint at q; payload hangs at the TCP. */
export function gravityInertia(chain: TwinChain, q: number[], payloadKg = 0): { gravity: number[]; inertia: number[] } {
  const points = (qq: number[]) => {
    const { frames, tcp } = chainFrames(chain, qq);
    const pts = chain.masses.map((m) => {
      const F = frames[m.after + 1];
      return { m: m.mass, p: [0, 1, 2].map((i) => F[i * 4] * m.local[0] + F[i * 4 + 1] * m.local[1] + F[i * 4 + 2] * m.local[2] + F[i * 4 + 3]) };
    });
    if (payloadKg > 0) pts.push({ m: payloadKg, p: [tcp[3], tcp[7], tcp[11]] });
    return pts;
  };
  const n = q.length, base = points(q);
  const gravity = new Array<number>(n).fill(0), inertia = new Array<number>(n).fill(0);
  for (let i = 0; i < n; i++) {
    const a = [...q], b = [...q];
    a[i] += 1e-6; b[i] -= 1e-6;
    const pa = points(a), pb = points(b);
    base.forEach((pt, k) => {
      const d = [0, 1, 2].map((c) => (pa[k].p[c] - pb[k].p[c]) / 2e-6);
      gravity[i] += G * pt.m * d[2];
      inertia[i] += pt.m * (d[0] * d[0] + d[1] * d[1] + d[2] * d[2]);
    });
  }
  return { gravity, inertia };
}

/** (|gravity| + |inertia * qdd|) / max_torque per joint (1 = at the limit; Infinity when max_torque <= 0). */
export function torqueUse(gravity: number[], inertia: number[], qdd: number[], maxTorque: number[]): number[] {
  return gravity.map((g, i) => (maxTorque[i] > 0 ? (Math.abs(g) + Math.abs(inertia[i] * qdd[i])) / maxTorque[i] : Infinity));
}

/** Largest acceleration the torque allows, never below 5 % of max_accel (Python `accel_cap_from_torque`). */
export function accelCapFromTorque(gravity: number[], inertia: number[], maxTorque: number[], maxAccel: number[]): number[] {
  return gravity.map((g, i) => Math.max((maxTorque[i] - Math.abs(g)) / Math.max(inertia[i], 1e-9), TORQUE_ACCEL_FLOOR * maxAccel[i]));
}
