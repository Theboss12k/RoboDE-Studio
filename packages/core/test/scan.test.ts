import { test } from "node:test";
import assert from "node:assert/strict";
import { glPixel, placementMatrix, projectionFromIntrinsics, projectPixel } from "../src/ar.ts";
import {
  applyPoint, bestRobotFrame, cameraPoseInSite, distance, frameSphere, projectSitePoint, scanCameraMatrixWorld, invertAffine, placementForBase, rToS, rToW, sFromR, siteFromWorld, sToR, sToW, wToR, wToS,
  type ScanPoint3, type ScanRows,
} from "../src/scan.ts";

const close = (a: ArrayLike<number>, b: ArrayLike<number>, eps = 1e-9) => {
  assert.equal(a.length, b.length);
  for (let i = 0; i < a.length; i++) assert.ok(Math.abs(a[i] - b[i]) < eps, `index ${i}: ${a[i]} vs ${b[i]}`);
};

/** A rigid A_from_R: rotation of 90 degrees about x (R's y becomes up), then a shift; row-major. */
const ALIGN: ScanRows = [
  [1, 0, 0, 0.5],
  [0, 0, -1, 2],
  [0, 1, 0, -1],
  [0, 0, 0, 1],
];

test("sFromR: no alignment and no scale is the identity", () => {
  close(sFromR(null, null), [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
});

test("sFromR: the factor scales the rotation and the translation, column-major", () => {
  const s = sFromR(ALIGN, 2);
  // Column-major: column 0 is the image of x, column 3 the translation; the last row stays 0 0 0 1.
  close(s, [2, 0, 0, 0, 0, 0, 2, 0, 0, -2, 0, 0, 1, 4, -2, 1]);
  close(applyPoint(s, [1, 2, 3]), [2 * 1 + 1, 2 * -3 + 4, 2 * 2 - 2]);
});

test("sFromR: a scale without a floor only scales; a missing or invalid factor means f = 1", () => {
  close(applyPoint(sFromR(null, 3), [1, 2, 3]), [3, 6, 9]);
  close(sFromR(ALIGN, null), sFromR(ALIGN, 1));
  close(sFromR(ALIGN, 0), sFromR(ALIGN, 1));
  close(sFromR(ALIGN, Number.NaN), sFromR(ALIGN, 1));
});

test("R to S and back, and to W and back", () => {
  const s = sFromR(ALIGN, 1.7);
  const placement = { x: 1.2, y: -0.4, yaw_deg: 33 };
  const p: ScanPoint3 = [0.3, -1.1, 2.4];
  close(sToR(rToS(p, s), s), p);
  close(wToS(sToW(p, placement), placement), p);
  close(wToR(rToW(p, s, placement), s, placement), p);
  close(rToW(p, s, placement), sToW(rToS(p, s), placement));
});

test("the placement is the matrix of ar.ts, and S to W is its inverse", () => {
  const placement = { x: 2, y: 3, yaw_deg: 90 };
  close(siteFromWorld(placement), placementMatrix(placement));
  // W's x axis points along S's y after a quarter turn, then the shift is added.
  close(wToS([1, 0, 0.5], placement), [2, 4, 0.5]);
  close(sToW([2, 4, 0.5], placement), [1, 0, 0.5]);
  close(siteFromWorld({}), siteFromWorld({ x: 0, y: 0, yaw_deg: 0 }));
});

test("invertAffine: general matrix, product with the original is the identity, and singular is null", () => {
  const m = [2, 0.3, 0, 0, -0.1, 1.5, 0.2, 0, 0.4, 0, 3, 0, 5, -6, 7, 1];
  const inv = invertAffine(m)!;
  for (const p of [[0, 0, 0], [1, 2, 3], [-4, 0.5, 9]]) close(applyPoint(inv, applyPoint(m, p)), p);
  assert.equal(invertAffine([0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]), null);
});

test("distance: invariant under the alignment, proportional to the factor", () => {
  const a: ScanPoint3 = [0, 0, 0], b: ScanPoint3 = [3, 4, 0];
  assert.equal(distance(a, b), 5);
  assert.equal(distance(a, b, null), 5);
  assert.equal(distance(a, b, 0.4), 2);
  const s = sFromR(ALIGN, 0.4);
  const sa = rToS(a, s), sb = rToS(b, s);
  assert.ok(Math.abs(Math.hypot(sa[0] - sb[0], sa[1] - sb[1], sa[2] - sb[2]) - distance(a, b, 0.4)) < 1e-9);
});

test("placementForBase puts the base point at the target for any yaw", () => {
  const base: ScanPoint3 = [0.5, -0.2, 0];
  for (const yaw of [0, 30, -75, 180]) {
    const p = placementForBase(base, yaw, [4, 2]);
    const s = wToS(base, p);
    assert.ok(Math.abs(s[0] - 4) < 1e-9 && Math.abs(s[1] - 2) < 1e-9, `yaw ${yaw}: ${s}`);
    assert.equal(p.yaw_deg, yaw);
  }
});

// A registered frame: camera at t looking along a rotated axis (rigid, row-major R_from_C).
const yawPitch = (yaw: number, pitch: number, t: number[]): ScanRows => {
  const cy = Math.cos(yaw), sy = Math.sin(yaw), cp = Math.cos(pitch), sp = Math.sin(pitch);
  // Rz(yaw) * Rx(pitch)
  return [[cy, -sy * cp, sy * sp, t[0]], [sy, cy * cp, -cy * sp, t[1]], [0, sp, cp, t[2]], [0, 0, 0, 1]];
};
const K = { fx: 700, fy: 690, cx: 480, cy: 270, width: 960, height: 540 };

test("scanCameraMatrixWorld: the page camera sees a point where the R-frame pinhole does, scale included", () => {
  const wfc = yawPitch(0.7, 1.9, [0.3, -0.4, 1.1]);
  const s = sFromR(ALIGN, 0.37);
  const proj = projectionFromIntrinsics(K.fx, K.fy, K.cx, K.cy, K.width, K.height, 0.02, 300);
  const world = scanCameraMatrixWorld(s, wfc);
  // the rotation of the camera matrix is a rotation: no scale leaks in
  for (const c of [0, 4, 8]) assert.ok(Math.abs(Math.hypot(world[c], world[c + 1], world[c + 2]) - 1) < 1e-9);
  for (const pr of [[0.2, 0.5, 1.9], [1.1, -0.2, 2.5], [-0.3, 0.1, 3.4]] as ScanPoint3[]) {
    const [u, v] = projectPixel(pr, K, wfc); // R point, pinhole in R
    const ps = rToS(pr, s);
    const [gu, gv] = glPixel(ps, proj, world, K.width, K.height);
    assert.ok(Math.abs(u - gu) < 1e-6 && Math.abs(v - gv) < 1e-6, `${[u, v]} vs ${[gu, gv]}`);
    const q = projectSitePoint(ps, s, wfc, K);
    assert.ok(Math.abs(u - q.u) < 1e-6 && Math.abs(v - q.v) < 1e-6);
  }
  const { rot, eye } = cameraPoseInSite(s, wfc);
  close(eye, applyPoint(s, [0.3, -0.4, 1.1]));
  assert.ok(Math.abs(rot[0] * rot[0] + rot[3] * rot[3] + rot[6] * rot[6] - 1) < 1e-9);
});

test("bestRobotFrame picks the frame that sees the base nearest the centre at a reasonable distance", () => {
  const s = sFromR(null, 1);
  const robot: ScanPoint3 = [0, 0, 0];
  // Look-at cameras: z forward, x right (horizontal), y down.
  const look = (eye: number[], target: number[]): ScanRows => {
    const f = [target[0] - eye[0], target[1] - eye[1], target[2] - eye[2]]; const n = Math.hypot(...f); f.forEach((_, i) => (f[i] /= n));
    const r = [f[1], -f[0], 0]; const rn = Math.hypot(r[0], r[1]) || 1; const rr = [r[0] / rn, r[1] / rn, 0];
    const d = [f[1] * rr[2] - f[2] * rr[1], f[2] * rr[0] - f[0] * rr[2], f[0] * rr[1] - f[1] * rr[0]]; // y = f x r (down)
    return [[rr[0], d[0], f[0], eye[0]], [rr[1], d[1], f[1], eye[1]], [rr[2], d[2], f[2], eye[2]], [0, 0, 0, 1]];
  };
  const frames = [
    { world_from_camera: null },
    { world_from_camera: look([-8, 0, 1.5], robot) }, // too far
    { world_from_camera: look([-2.5, 0, 1.5], [0, 2.5, 0.5]) }, // right distance, robot off to the side
    { world_from_camera: look([-2.5, 0, 1.5], robot) }, // best
    { world_from_camera: look([2.5, 0, 1.5], [6, 0, 1]) }, // looks away
  ];
  assert.equal(bestRobotFrame(frames, s, robot, K), 3);
  assert.equal(bestRobotFrame([{ world_from_camera: null }], s, robot, K), null);
});

test("frameSphere puts the eye in the given direction so the sphere fills the view", () => {
  const { eye, distance: d } = frameSphere([1, 2, 0.5], 0.8, 45, 1.6, [0, -1, 0], 0.8);
  close(eye, [1, 2 - d, 0.5]);
  const half = Math.asin(0.8 / d); // angular radius of the sphere
  assert.ok(Math.abs(half - (45 * Math.PI) / 360 * 0.8) < 1e-9);
});
