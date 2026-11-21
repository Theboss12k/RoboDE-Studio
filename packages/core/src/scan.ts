// Site-scan maths (see docs/SITE_SCAN.md). No imports and no DOM, so `node --test` can run it directly.
// Matrices returned as 16 numbers are column-major, the order of three.js `Matrix4.elements` (as in ar.ts).
// Frames: R is the raw reconstruction frame, A is aligned (z up, floor at z = 0, still in scan units),
// S is the site in metres (S_from_R = diag(f, f, f, 1) * A_from_R) and W is the design world (z up, metres).
// The placement maps W into S: T_SW = Translate(x, y, 0) * RotZ(yaw_deg).

export type ScanPoint3 = [number, number, number];
/** 4x4 matrix, row-major (as `SiteScan.alignment`). */
export type ScanRows = number[][];
/** The pose of the design world in the site (`ArPlacement`). */
export type SitePlacement = { x?: number; y?: number; yaw_deg?: number };

const IDENTITY: readonly number[] = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];

/**
 * S_from_R, column-major: diag(f, f, f, 1) * A_from_R. `alignment` is row-major (null = identity, as before a floor is
 * found); `factor` null or missing means unscaled, f = 1.
 */
export function sFromR(alignment: ScanRows | null | undefined, factor: number | null | undefined): number[] {
  const f = factor != null && Number.isFinite(factor) && factor > 0 ? factor : 1;
  const out = IDENTITY.slice();
  if (alignment && alignment.length >= 3) {
    for (let r = 0; r < 3; r++) for (let c = 0; c < 4; c++) out[c * 4 + r] = (alignment[r][c] ?? 0) * f;
  } else {
    out[0] = out[5] = out[10] = f;
  }
  return out;
}

/** T_SW, column-major: Translate(x, y, 0) * RotZ(yaw_deg), the same matrix as `placementMatrix` in ar.ts. */
export function siteFromWorld(p: SitePlacement): number[] {
  const a = ((p.yaw_deg ?? 0) * Math.PI) / 180;
  const c = Math.cos(a), s = Math.sin(a);
  return [c, s, 0, 0, -s, c, 0, 0, 0, 0, 1, 0, p.x ?? 0, p.y ?? 0, 0, 1];
}

/** Applies a column-major affine matrix to a point. */
export function applyPoint(m: ArrayLike<number>, p: ArrayLike<number>): ScanPoint3 {
  return [
    m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12],
    m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13],
    m[2] * p[0] + m[6] * p[1] + m[10] * p[2] + m[14],
  ];
}

/** Inverse of a column-major affine matrix (last row 0 0 0 1), or null when it is singular. */
export function invertAffine(m: ArrayLike<number>): number[] | null {
  const a = m[0], b = m[4], c = m[8], d = m[1], e = m[5], f = m[9], g = m[2], h = m[6], i = m[10];
  const co0 = e * i - f * h, co1 = f * g - d * i, co2 = d * h - e * g;
  const det = a * co0 + b * co1 + c * co2;
  if (!Number.isFinite(det) || Math.abs(det) < 1e-300) return null;
  const k = 1 / det;
  const r = [
    co0 * k, (c * h - b * i) * k, (b * f - c * e) * k,
    co1 * k, (a * i - c * g) * k, (c * d - a * f) * k,
    co2 * k, (b * g - a * h) * k, (a * e - b * d) * k,
  ]; // row-major 3x3 inverse
  const tx = m[12], ty = m[13], tz = m[14];
  return [
    r[0], r[3], r[6], 0,
    r[1], r[4], r[7], 0,
    r[2], r[5], r[8], 0,
    -(r[0] * tx + r[1] * ty + r[2] * tz), -(r[3] * tx + r[4] * ty + r[5] * tz), -(r[6] * tx + r[7] * ty + r[8] * tz), 1,
  ];
}

/** R to S. `s` is `sFromR(...)`. */
export const rToS = (p: ArrayLike<number>, s: ArrayLike<number>): ScanPoint3 => applyPoint(s, p);

/** S to R. */
export function sToR(p: ArrayLike<number>, s: ArrayLike<number>): ScanPoint3 {
  const inv = invertAffine(s);
  if (!inv) throw new Error("The scan matrix cannot be inverted");
  return applyPoint(inv, p);
}

/** W to S through the placement. */
export const wToS = (p: ArrayLike<number>, placement: SitePlacement): ScanPoint3 => applyPoint(siteFromWorld(placement), p);

/** S to W: T_SW inverse. */
export function sToW(p: ArrayLike<number>, placement: SitePlacement): ScanPoint3 {
  const inv = invertAffine(siteFromWorld(placement));
  return applyPoint(inv!, p); // a rotation and a translation are always invertible
}

/** R to W (R to S, then S to W). */
export const rToW = (p: ArrayLike<number>, s: ArrayLike<number>, placement: SitePlacement): ScanPoint3 => sToW(rToS(p, s), placement);

/** W to R. */
export const wToR = (p: ArrayLike<number>, s: ArrayLike<number>, placement: SitePlacement): ScanPoint3 => sToR(wToS(p, placement), s);

/**
 * Distance between two points in R, in metres: A is a rigid motion, so only the scale factor matters.
 * `factor` null or missing gives scan units (unscaled).
 */
export function distance(p1: ArrayLike<number>, p2: ArrayLike<number>, factor?: number | null): number {
  const f = factor != null && Number.isFinite(factor) && factor > 0 ? factor : 1;
  return Math.hypot(p1[0] - p2[0], p1[1] - p2[1], p1[2] - p2[2]) * f;
}

/**
 * The placement (x, y) that puts the design world's base point `baseW` (x, y) at `targetS` (x, y) in the site,
 * for a given yaw: the world turns about its own origin, then moves.
 */
export function placementForBase(baseW: ArrayLike<number>, yawDeg: number, targetS: ArrayLike<number>): { x: number; y: number; yaw_deg: number } {
  const a = (yawDeg * Math.PI) / 180;
  const c = Math.cos(a), s = Math.sin(a);
  return { x: targetS[0] - (c * baseW[0] - s * baseW[1]), y: targetS[1] - (s * baseW[0] + c * baseW[1]), yaw_deg: yawDeg };
}

// -- Through the camera (docs/SITE_SCAN.md): a registered keyframe's camera inside S, and where the robot is seen ---------

/** Pinhole parameters in pixels at the frame resolution (the scan camera, or a photo's). */
export type ScanIntrinsics = { fx: number; fy: number; cx: number; cy: number; width: number; height: number };

/**
 * The camera of a registered frame inside S, OpenCV axes (x right, y down, z forward): `rot` is the row-major 3x3 camera to S
 * rotation and `eye` the camera centre in metres. S_from_R carries the scale, so the rotation columns are normalised: only the
 * translation takes the factor.
 */
export function cameraPoseInSite(s: ArrayLike<number>, worldFromCamera: ScanRows): { rot: number[]; eye: ScanPoint3 } {
  const rot = new Array<number>(9).fill(0);
  for (let c = 0; c < 3; c++) {
    const col = [0, 1, 2].map((r) => s[0 + r] * worldFromCamera[0][c] + s[4 + r] * worldFromCamera[1][c] + s[8 + r] * worldFromCamera[2][c]);
    const n = Math.hypot(col[0], col[1], col[2]) || 1;
    for (let r = 0; r < 3; r++) rot[r * 3 + c] = col[r] / n;
  }
  const t = [worldFromCamera[0][3], worldFromCamera[1][3], worldFromCamera[2][3]];
  return { rot, eye: applyPoint(s, t) };
}

/**
 * three.js `matrixWorld` of the camera of a registered frame: Z_UP * S_from_R * R_from_C * diag(1, -1, -1, 1), column-major.
 * It is ar.ts `cameraMatrixWorld` composed with S_from_R, with the scale kept out of the rotation.
 */
export function scanCameraMatrixWorld(s: ArrayLike<number>, worldFromCamera: ScanRows): number[] {
  const { rot, eye } = cameraPoseInSite(s, worldFromCamera);
  const sign = [1, -1, -1];
  const out = new Array<number>(16).fill(0);
  const toThree = (v: number[]) => [v[0], v[2], -v[1]]; // Z_UP: S (z up) to three.js (y up)
  for (let c = 0; c < 3; c++) {
    const v = toThree([rot[c] * sign[c], rot[3 + c] * sign[c], rot[6 + c] * sign[c]]);
    out[c * 4] = v[0]; out[c * 4 + 1] = v[1]; out[c * 4 + 2] = v[2];
  }
  const e = toThree(eye);
  out[12] = e[0]; out[13] = e[1]; out[14] = e[2]; out[15] = 1;
  return out;
}

/** A point of S in a registered frame: pixel (u, v) at the frame resolution and the depth along the optical axis (metres). */
export function projectSitePoint(p: ArrayLike<number>, s: ArrayLike<number>, worldFromCamera: ScanRows, k: ScanIntrinsics): { u: number; v: number; depth: number } {
  const { rot, eye } = cameraPoseInSite(s, worldFromCamera);
  const d = [p[0] - eye[0], p[1] - eye[1], p[2] - eye[2]];
  const x = rot[0] * d[0] + rot[3] * d[1] + rot[6] * d[2];
  const y = rot[1] * d[0] + rot[4] * d[1] + rot[7] * d[2];
  const z = rot[2] * d[0] + rot[5] * d[1] + rot[8] * d[2];
  return { u: (k.fx * x) / z + k.cx, v: (k.fy * y) / z + k.cy, depth: z };
}

/**
 * The frame, as an index into `frames`, whose camera sees the robot base closest to the image centre at a reasonable distance
 * (about 2.5 m once scaled; unscaled, the typical distance of the frames that see it). Unregistered frames and frames where the
 * base is behind the camera or outside the picture are skipped; null when no frame sees it.
 */
export function bestRobotFrame(frames: { world_from_camera: ScanRows | null }[], s: ArrayLike<number>, robotS: ArrayLike<number>, k: ScanIntrinsics, scaled = true): number | null {
  const seen: { i: number; off: number; z: number }[] = [];
  frames.forEach((f, i) => {
    if (!f.world_from_camera) return;
    const { u, v, depth } = projectSitePoint(robotS, s, f.world_from_camera, k);
    if (!(depth > 1e-6) || u < 0.04 * k.width || u > 0.96 * k.width || v < 0.04 * k.height || v > 0.96 * k.height) return;
    seen.push({ i, z: depth, off: Math.hypot((u - k.width / 2) / (k.width / 2), (v - k.height / 2) / (k.height / 2)) });
  });
  if (!seen.length) return null;
  const zs = seen.map((x) => x.z).sort((a, b) => a - b);
  const preferred = scaled ? 2.5 : zs[Math.floor(zs.length / 2)];
  let best = seen[0], bestScore = Infinity;
  for (const x of seen) {
    const score = x.off + 0.35 * Math.abs(Math.log(x.z / preferred));
    if (score < bestScore) { best = x; bestScore = score; }
  }
  return best.i;
}

/**
 * Where to put an orbiting camera so that a sphere (centre, radius, in S) fills `fill` of the narrower side of the view:
 * the eye lies `direction` away from the centre (z up). `fovDeg` is the vertical field of view.
 */
export function frameSphere(centre: ArrayLike<number>, radius: number, fovDeg: number, aspect: number, direction: ArrayLike<number>, fill = 0.8): { eye: ScanPoint3; distance: number } {
  const half = (fovDeg * Math.PI) / 360;
  const halfNarrow = Math.min(half, Math.atan(Math.tan(half) * Math.max(aspect, 1e-3)));
  const distance = radius / Math.sin(Math.max(halfNarrow * fill, 1e-3));
  const n = Math.hypot(direction[0], direction[1], direction[2]) || 1;
  return { eye: [centre[0] + (direction[0] / n) * distance, centre[1] + (direction[1] / n) * distance, centre[2] + (direction[2] / n) * distance], distance };
}
