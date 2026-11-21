import { test } from "node:test";
import assert from "node:assert/strict";
import { AR_Z_UP, cameraMatrixWorld, glPixel, placementMatrix, projectionFromIntrinsics, projectPixel, type Mat4Rows } from "../src/ar.ts";

function rng(seed: number) {
  let s = seed >>> 0;
  return () => { s = (Math.imul(s, 1664525) + 1013904223) >>> 0; return s / 4294967296; };
}

/** Camera 1.4 m up at the origin of F, looking along +y of F and pitched down by `pitch` degrees (OpenCV axes). */
function pitchedPose(pitch: number): Mat4Rows {
  const a = (pitch * Math.PI) / 180;
  // Camera z (forward) in F: (0, cos a, -sin a); camera x (right): (1, 0, 0); camera y (down) = z cross x.
  const fwd = [0, Math.cos(a), -Math.sin(a)];
  const right = [1, 0, 0];
  const down = [fwd[1] * right[2] - fwd[2] * right[1], fwd[2] * right[0] - fwd[0] * right[2], fwd[0] * right[1] - fwd[1] * right[0]];
  return [
    [right[0], down[0], fwd[0], 0],
    [right[1], down[1], fwd[1], 0],
    [right[2], down[2], fwd[2], 1.4],
    [0, 0, 0, 1],
  ];
}

test("GL projection agrees with the OpenCV pinhole within 0.5 px", () => {
  const W = 1600, H = 1200;
  const k = { fx: 1350, fy: 1320, cx: 840, cy: 580 };
  const T = pitchedPose(35);
  const proj = projectionFromIntrinsics(k.fx, k.fy, k.cx, k.cy, W, H, 0.05, 100);
  const cam = cameraMatrixWorld(T);
  const rand = rng(12345);
  let n = 0;
  while (n < 50) {
    const p: [number, number, number] = [(rand() - 0.5) * 3, 0.8 + rand() * 4, rand() * 1.5];
    const ref = projectPixel(p, k, T);
    const got = glPixel(p, proj, cam, W, H);
    assert.ok(Math.abs(got[0] - ref[0]) < 0.5, `u ${got[0]} vs ${ref[0]}`);
    assert.ok(Math.abs(got[1] - ref[1]) < 0.5, `v ${got[1]} vs ${ref[1]}`);
    n++;
  }
});

test("placement with yaw 90 degrees maps (1,0,0) to (0,1,0)", () => {
  const m = placementMatrix({ x: 0, y: 0, yaw_deg: 90 });
  const p = [1, 0, 0];
  const out = [0, 1, 2].map((r) => m[r] * p[0] + m[4 + r] * p[1] + m[8 + r] * p[2] + m[12 + r]);
  assert.ok(Math.abs(out[0]) < 1e-12 && Math.abs(out[1] - 1) < 1e-12 && Math.abs(out[2]) < 1e-12);
  const moved = placementMatrix({ x: 2, y: 3, yaw_deg: 0 });
  assert.deepEqual([moved[12], moved[13], moved[14]], [2, 3, 0]);
});

test("identity T_FC looks along world +z after Z_UP", () => {
  const identity: Mat4Rows = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]];
  const m = cameraMatrixWorld(identity);
  // A three.js camera looks along its local -z; in three space that must be world +z, i.e. three +y.
  const dir = [0, 1, 2].map((r) => -m[8 + r]);
  assert.ok(Math.abs(dir[0]) < 1e-12 && Math.abs(dir[1] - 1) < 1e-12 && Math.abs(dir[2]) < 1e-12, `dir ${dir}`);
});

test("local Z_UP matches core's Z_UP when three is installed", async (t) => {
  let three: any;
  try { three = await import("three"); } catch { t.skip("three is not installed"); return; }
  const z = new three.Matrix4().makeRotationX(-Math.PI / 2).elements as number[];
  AR_Z_UP.forEach((v, i) => assert.ok(Math.abs(v - z[i]) < 1e-12, `element ${i}`));
});
