// Photo-AR camera maths (see docs/AR_PHOTO.md). No imports and no DOM, so `node --test` can run it directly.
// Matrices returned as 16 numbers are column-major, the order of three.js `Matrix4.elements`.
// Frames: F is the floor frame (z up, metres); three.js is y-up with three = [x, z, -y] of world/F coordinates;
// the camera is OpenCV (x right, y down, z forward) while three.js cameras look down -z with y up.

export type ArPoint3 = [number, number, number];
/** The pinhole parameters of the photo, in pixels. */
export type ArK = { fx: number; fy: number; cx: number; cy: number };
/** 4x4 matrix, row-major (as `ArCalibration.world_from_camera`). */
export type Mat4Rows = number[][];

/** F (z up) to three.js (y up): rotation of -90 degrees about X. Same matrix as core's `Z_UP`, column-major. */
export const AR_Z_UP: readonly number[] = [
  1, 0, 0, 0,
  0, 0, -1, 0,
  0, 1, 0, 0,
  0, 0, 0, 1,
];

/** Column-major product a * b. */
export function mulColMajor(a: ArrayLike<number>, b: ArrayLike<number>): number[] {
  const out = new Array<number>(16).fill(0);
  for (let c = 0; c < 4; c++) {
    for (let r = 0; r < 4; r++) {
      let s = 0;
      for (let k = 0; k < 4; k++) s += a[k * 4 + r] * b[c * 4 + k];
      out[c * 4 + r] = s;
    }
  }
  return out;
}

/** Row-major 4x4 to column-major 16 numbers. */
export function rowsToColMajor(m: Mat4Rows): number[] {
  const out = new Array<number>(16);
  for (let r = 0; r < 4; r++) for (let c = 0; c < 4; c++) out[c * 4 + r] = m[r][c];
  return out;
}

/**
 * OpenGL projection (camera looks down -z) that reproduces the OpenCV pinhole u = fx*X/Z + cx, v = fy*Y/Z + cy
 * for a photo of width x height pixels, origin top-left, v down.
 *   x_ndc = 2 fx / W * X/Z + (2 cx / W - 1)
 *   y_ndc = 1 - 2 v / H = -2 fy / H * Y/Z + (1 - 2 cy / H)
 * With GL camera coordinates (X, -Y, -Z) and w_clip = Z this gives the entries below.
 */
export function projectionFromIntrinsics(fx: number, fy: number, cx: number, cy: number, width: number, height: number, near: number, far: number): number[] {
  const e = new Array<number>(16).fill(0);
  e[0] = (2 * fx) / width;
  e[5] = (2 * fy) / height;
  e[8] = 1 - (2 * cx) / width;
  e[9] = (2 * cy) / height - 1;
  e[10] = -(far + near) / (far - near);
  e[11] = -1;
  e[14] = (-2 * far * near) / (far - near);
  return e;
}

/** three.js camera matrixWorld: Z_UP * T_FC * diag(1, -1, -1, 1). `worldFromCamera` is row-major. */
export function cameraMatrixWorld(worldFromCamera: Mat4Rows): number[] {
  const flip = [1, 0, 0, 0, 0, -1, 0, 0, 0, 0, -1, 0, 0, 0, 0, 1];
  return mulColMajor(mulColMajor(AR_Z_UP, rowsToColMajor(worldFromCamera)), flip);
}

/** T_FW = Translate(x, y, 0) * RotZ(yaw_deg): maps the design world W into the floor frame F. */
export function placementMatrix(p: { x?: number; y?: number; yaw_deg?: number }): number[] {
  const a = ((p.yaw_deg ?? 0) * Math.PI) / 180;
  const c = Math.cos(a), s = Math.sin(a);
  return [
    c, s, 0, 0,
    -s, c, 0, 0,
    0, 0, 1, 0,
    p.x ?? 0, p.y ?? 0, 0, 1,
  ];
}

/** Reference OpenCV projection of a point in F to pixels (u, v), given T_FC (row-major, camera to F). */
export function projectPixel(pointF: ArPoint3, k: ArK, worldFromCamera: Mat4Rows): [number, number] {
  const t = worldFromCamera;
  const d = [pointF[0] - t[0][3], pointF[1] - t[1][3], pointF[2] - t[2][3]];
  // Rigid inverse: Pc = R^T (P - t)
  const xc = t[0][0] * d[0] + t[1][0] * d[1] + t[2][0] * d[2];
  const yc = t[0][1] * d[0] + t[1][1] * d[1] + t[2][1] * d[2];
  const zc = t[0][2] * d[0] + t[1][2] * d[1] + t[2][2] * d[2];
  return [(k.fx * xc) / zc + k.cx, (k.fy * yc) / zc + k.cy];
}

/** Projects a point in F through the GL matrices to pixels, as the renderer does. `camWorld` is from `cameraMatrixWorld`. */
export function glPixel(pointF: ArPoint3, proj: ArrayLike<number>, camWorld: ArrayLike<number>, width: number, height: number): [number, number] {
  // three.js point
  const p = [pointF[0], pointF[2], -pointF[1]];
  // Rigid inverse of camWorld (rotation part transposed).
  const m = camWorld;
  const d = [p[0] - m[12], p[1] - m[13], p[2] - m[14]];
  const v = [
    m[0] * d[0] + m[1] * d[1] + m[2] * d[2],
    m[4] * d[0] + m[5] * d[1] + m[6] * d[2],
    m[8] * d[0] + m[9] * d[1] + m[10] * d[2],
    1,
  ];
  const clip = [0, 1, 2, 3].map((r) => proj[r] * v[0] + proj[4 + r] * v[1] + proj[8 + r] * v[2] + proj[12 + r] * v[3]);
  const nx = clip[0] / clip[3], ny = clip[1] / clip[3];
  return [((nx + 1) / 2) * width, ((1 - ny) / 2) * height];
}
