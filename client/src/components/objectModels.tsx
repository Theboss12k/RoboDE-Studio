// Procedural 3D models for task objects (bottle, apple, mug, tote ...), chosen by keyword on the object's name or id.
// No assets are loaded: every model is built from three.js geometry, deterministically (seeded hash, no Math.random).
// Models are built in a y-up local frame whose bounding box is exactly the object's world box (x, y, z up) so that
// grasp points and the held-object offset stay as for the primitive they replace; unknown objects keep the primitive.
import { useMemo } from "react";
import { BoxGeometry, BufferGeometry, CylinderGeometry, ExtrudeGeometry, LatheGeometry, Shape, SphereGeometry, TorusGeometry, Vector2 } from "three";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";

export type ObjectKind =
  | "bottle" | "can" | "mug" | "cup" | "apple" | "orange" | "ball" | "egg" | "box" | "tote" | "dumbbell" | "book" | "pallet" | "screwdriver";

/** Words (singular, lower case) that select a model. Earlier rows win. */
export const OBJECT_KEYWORDS: [ObjectKind, string[]][] = [
  ["mug", ["mug", "teacup"]],
  ["cup", ["cup", "glass", "tumbler"]],
  ["bottle", ["bottle", "flask", "jug"]],
  ["can", ["can", "tin", "soda"]],
  ["apple", ["apple", "pear"]],
  ["orange", ["orange", "tangerine", "citrus"]],
  ["ball", ["ball", "sphere"]],
  ["egg", ["egg"]],
  ["tote", ["tote", "crate", "bin", "basket", "tray"]],
  ["dumbbell", ["dumbbell", "barbell"]],
  ["book", ["book", "notebook", "manual"]],
  ["pallet", ["pallet"]],
  ["screwdriver", ["screwdriver", "driver"]],
  ["box", ["box", "carton", "parcel", "package", "case"]],
];

const words = (s: string) => s.toLowerCase().split(/[^a-z]+/).filter(Boolean).map((w) => (w.length > 3 && w.endsWith("s") ? w.slice(0, -1) : w));

/** The model kind for an object, or "primitive" when nothing matches (the caller then draws its box/cylinder/sphere). */
export function objectKind(name: string | undefined, id: string, _shape?: string): ObjectKind | "primitive" {
  for (const text of [name ?? "", id]) {
    const ws = words(text);
    for (const [kind, keys] of OBJECT_KEYWORDS) if (ws.some((w) => keys.includes(w))) return kind;
  }
  return "primitive";
}

type Mat = { color: string; roughness?: number; metalness?: number; opacity?: number };
type Part = { g: BufferGeometry; m: Mat; pos?: [number, number, number]; rot?: [number, number, number]; scale?: [number, number, number] };
type Dims = { w: number; h: number; d: number };

function hash(i: number, j: number, k: number): number {
  let x = Math.imul(i + 374761393, 668265263) ^ Math.imul(j + 1274126177, 2246822519) ^ Math.imul(k + 97, 3266489917);
  x = Math.imul(x ^ (x >>> 13), 1274126177);
  return ((x ^ (x >>> 16)) >>> 0) / 4294967295;
}

/** Lathe from (radius, y) points; radius is scaled by `rx`, the mesh's z scale makes it elliptical when w differs from d. */
function lathe(points: [number, number][], segs = 32): LatheGeometry {
  return new LatheGeometry(points.map(([r, y]) => new Vector2(r, y)), segs);
}
const cyl = (r: number, h: number, segs = 24) => new CylinderGeometry(r, r, h, segs);
const ellipse = (w: number, d: number): [number, number, number] => [1, 1, w > 0 ? d / w : 1];

function bottle({ w, h, d }: Dims, color: string, plastic: boolean): Part[] {
  const R = w / 2, y0 = -h / 2;
  const body: [number, number][] = [[0, 0], [0.82 * R, 0], [0.97 * R, 0.02 * h], [R, 0.06 * h], [R, 0.58 * h], [0.93 * R, 0.66 * h], [0.62 * R, 0.76 * h], [0.38 * R, 0.82 * h], [0.34 * R, 0.9 * h]]
    .map(([r, y]) => [r, y0 + y] as [number, number]);
  const label: [number, number][] = [[1.012 * R, y0 + 0.2 * h], [1.012 * R, y0 + 0.5 * h]];
  const capR = 0.4 * R, capGeo = cyl(capR, 0.11 * h, 24);
  const p = capGeo.attributes.position;
  for (let i = 0; i < p.count; i++) { // ribs on the cap side
    const x = p.getX(i), z = p.getZ(i), r = Math.hypot(x, z);
    if (r > capR * 0.9) { const k = 1 + 0.05 * Math.cos(Math.atan2(z, x) * 20); p.setXYZ(i, x * k, p.getY(i), z * k); }
  }
  capGeo.computeVertexNormals();
  const e = ellipse(w, d);
  return [
    { g: lathe(body), m: { color: plastic ? "#bcd9ec" : "#2f5d3a", roughness: plastic ? 0.15 : 0.05, opacity: plastic ? 0.5 : 0.62 }, scale: e },
    { g: lathe([[0.001, y0 + 0.07 * h], [0.97 * R, y0 + 0.07 * h], [0.97 * R, y0 + 0.5 * h], [0.001, y0 + 0.5 * h]]), m: { color: "#7fb8e6", roughness: 0.1, opacity: 0.5 }, scale: e },
    { g: lathe(label, 40), m: { color, roughness: 0.6 }, scale: e },
    { g: lathe([[0.36 * R, y0 + 0.86 * h], [0.4 * R, y0 + 0.87 * h], [0.4 * R, y0 + 0.88 * h]], 24), m: { color: "#d9e6ee", roughness: 0.3 }, scale: e },
    { g: capGeo, m: { color, roughness: 0.4 }, pos: [0, y0 + 0.945 * h, 0], scale: e },
    { g: cyl(capR, 0.005 * h, 24), m: { color, roughness: 0.4 }, pos: [0, y0 + h, 0], scale: e },
  ];
}

function can({ w, h, d }: Dims, color: string): Part[] {
  const R = w / 2, y0 = -h / 2, e = ellipse(w, d);
  const body: [number, number][] = [[0, 0], [0.93 * R, 0], [0.96 * R, 0.015 * h], [0.96 * R, 0.985 * h], [0.93 * R, h], [0, h]].map(([r, y]) => [r, y0 + y]);
  return [
    { g: lathe(body, 40), m: { color: "#c5ccd3", metalness: 0.85, roughness: 0.3 }, scale: e },
    { g: lathe([[0.97 * R, y0 + 0.1 * h], [0.97 * R, y0 + 0.85 * h]], 40), m: { color, roughness: 0.45, metalness: 0.3 }, scale: e },
    { g: lathe([[0.9 * R, y0 + h], [R, y0 + h], [R, y0 + 0.985 * h], [0.9 * R, y0 + 0.985 * h]], 40), m: { color: "#dfe4e8", metalness: 0.9, roughness: 0.25 }, scale: e },
    { g: lathe([[R, y0], [R, y0 + 0.015 * h], [0.9 * R, y0 + 0.015 * h]], 40), m: { color: "#dfe4e8", metalness: 0.9, roughness: 0.25 }, scale: e },
  ];
}

function cup({ w, h, d }: Dims, color: string, handle: boolean): Part[] {
  const R = (handle ? 0.38 : 0.5) * w, y0 = -h / 2, wall = 0.07 * R, e = ellipse(w, d), cx = handle ? -0.1 * w : 0;
  const prof: [number, number][] = [[0, 0], [0.8 * R, 0], [R * 0.92, 0.04 * h], [R, 0.12 * h], [R * 1.03, h], [R * 1.03 - wall, h], [R - wall, 0.14 * h], [0.8 * R, 0.09 * h], [0, 0.09 * h]]
    .map(([r, y]) => [r, y0 + y] as [number, number]);
  const parts: Part[] = [{ g: lathe(prof, 40), m: { color, roughness: 0.35 }, pos: [cx, 0, 0], scale: e }];
  if (handle) {
    const tube = 0.04 * h, hr = Math.max(0.01 * h, Math.min(0.24 * h, w / 2 - (cx + R) - tube));
    parts.push({ g: new TorusGeometry(hr, tube, 10, 24, Math.PI), m: { color, roughness: 0.35 }, pos: [cx + R, y0 + 0.52 * h, 0], rot: [0, 0, -Math.PI / 2] });
  }
  return parts;
}

function apple({ w, h, d }: Dims, red: string): Part[] {
  const R = w / 2, y0 = -h / 2, top = 0.9 * h; // body takes 90 % of the height, the stem the rest
  const pts: [number, number][] = [];
  for (let i = 0; i <= 28; i++) {
    const t = (i / 28) * Math.PI; // 0 at the bottom, pi at the top
    const r = R * Math.sin(t) * (1 + 0.1 * Math.cos(t + 0.6));
    let y = (1 - Math.cos(t)) / 2;
    y += 0.05 * Math.exp(-((t / 0.45) ** 2)) - 0.09 * Math.exp(-(((Math.PI - t) / 0.5) ** 2));
    pts.push([Math.max(r, 0.0005), y0 + y * top]);
  }
  const e = ellipse(w, d), dimple = y0 + top * (1 - 0.09 * 1);
  const stem = new CylinderGeometry(0.014 * w, 0.02 * w, 0.14 * h, 8);
  const leaf = new SphereGeometry(1, 10, 6);
  return [
    { g: lathe(pts, 40), m: { color: red, roughness: 0.4 }, scale: e },
    { g: stem, m: { color: "#5b3a1e", roughness: 0.8 }, pos: [0.01 * w, Math.min(dimple + 0.04 * h, y0 + 0.93 * h), 0], rot: [0, 0, 0.18] },
    { g: leaf, m: { color: "#3f8f3a", roughness: 0.6 }, pos: [0.09 * w, y0 + 0.945 * h, 0], scale: [0.09 * w, 0.012 * h, 0.04 * w], rot: [0, 0, 0.3] },
  ];
}

function noisySphere(seg: number, amp: number, salt: number): BufferGeometry {
  const g = new SphereGeometry(0.5, seg, Math.round(seg / 2));
  const p = g.attributes.position;
  for (let i = 0; i < p.count; i++) {
    const x = p.getX(i), y = p.getY(i), z = p.getZ(i);
    const k = 1 + amp * (hash(Math.round(x * 24), Math.round(y * 24), Math.round(z * 24) + salt) - 0.5);
    p.setXYZ(i, x * k, y * k, z * k);
  }
  g.computeVertexNormals();
  return g;
}

function orange({ w, h, d }: Dims, color: string, citrus: boolean): Part[] {
  const g = noisySphere(32, citrus ? 0.05 : 0.012, citrus ? 7 : 3).scale(w, h, d);
  const parts: Part[] = [{ g, m: { color, roughness: citrus ? 0.85 : 0.5 } }];
  if (citrus) parts.push({ g: cyl(0.025 * w, 0.015 * h, 8), m: { color: "#3f6b2a", roughness: 0.8 }, pos: [0, h / 2 - 0.006 * h, 0] });
  return parts;
}

function egg({ w, h, d }: Dims): Part[] {
  const pts: [number, number][] = [];
  for (let i = 0; i <= 24; i++) {
    const t = (i / 24) * Math.PI; // 0 bottom (broad), pi top (narrow)
    pts.push([Math.max(0.0005, 0.5 * w * Math.sin(t) * (1 - 0.18 * (1 - Math.cos(t)) / 2 * 1.4)), -h / 2 + h * (1 - Math.cos(t)) / 2]);
  }
  return [{ g: lathe(pts, 32), m: { color: "#f1e6d2", roughness: 0.55 }, scale: ellipse(w, d) }];
}

function box({ w, h, d }: Dims, color: string): Part[] {
  const r = Math.min(w, h, d) * 0.04;
  return [
    { g: new RoundedBoxGeometry(w, h, d, 2, r), m: { color, roughness: 0.75 } },
    { g: new BoxGeometry(0.16 * w, 0.006 * h, d * 1.002), m: { color: "#e8d9b0", roughness: 0.4 }, pos: [0, h / 2 + 0.0005 * h, 0] },
    { g: new BoxGeometry(0.16 * w, h * 1.002, 0.004 * d), m: { color: "#e8d9b0", roughness: 0.4 }, pos: [0, 0, d / 2] },
    { g: new BoxGeometry(0.16 * w, h * 1.002, 0.004 * d), m: { color: "#e8d9b0", roughness: 0.4 }, pos: [0, 0, -d / 2] },
  ];
}

function tote({ w, h, d }: Dims, color: string): Part[] {
  const t = Math.max(0.02 * Math.min(w, d), 0.002), m: Mat = { color, roughness: 0.6 };
  const hh = 0.18 * h, handle = new Shape();
  handle.moveTo(-w / 2, -h / 2); handle.lineTo(w / 2, -h / 2); handle.lineTo(w / 2, h / 2); handle.lineTo(-w / 2, h / 2); handle.lineTo(-w / 2, -h / 2);
  const hole = new Shape(); // rounded slot, wound the other way
  const sx = 0.22 * w, sy = hh / 2, cy = 0.22 * h;
  hole.moveTo(-sx, cy - sy); hole.lineTo(-sx, cy + sy); hole.lineTo(sx, cy + sy); hole.lineTo(sx, cy - sy); hole.lineTo(-sx, cy - sy);
  handle.holes.push(hole);
  const endWall = new ExtrudeGeometry(handle, { depth: t, bevelEnabled: false });
  return [
    { g: new BoxGeometry(w, t, d), m, pos: [0, -h / 2 + t / 2, 0] },
    { g: endWall, m, pos: [0, 0, d / 2 - t] },
    { g: endWall, m, pos: [0, 0, -d / 2] },
    { g: new BoxGeometry(t, h, d - 2 * t), m, pos: [w / 2 - t / 2, 0, 0] },
    { g: new BoxGeometry(t, h, d - 2 * t), m, pos: [-w / 2 + t / 2, 0, 0] },
    { g: new BoxGeometry(w * 1.01, 0.03 * h, 0.03 * d), m: { color, roughness: 0.5 }, pos: [0, h / 2 - 0.015 * h, d / 2 - 0.015 * d] },
    { g: new BoxGeometry(w * 1.01, 0.03 * h, 0.03 * d), m: { color, roughness: 0.5 }, pos: [0, h / 2 - 0.015 * h, -d / 2 + 0.015 * d] },
  ];
}

function dumbbell({ w, h, d }: Dims): Part[] {
  const r = Math.min(h, d) / 2, head = 0.22 * w, m: Mat = { color: "#2b2e33", roughness: 0.4, metalness: 0.6 };
  const hexHead = new CylinderGeometry(r, r, head, 6);
  return [
    { g: cyl(0.22 * r, w - 2 * head + 0.02 * w, 16), m: { color: "#a9b0b8", roughness: 0.3, metalness: 0.9 }, rot: [0, 0, Math.PI / 2] },
    { g: cyl(0.3 * r, 0.4 * w, 20), m: { color: "#6b7178", roughness: 0.5, metalness: 0.7 }, rot: [0, 0, Math.PI / 2] },
    { g: hexHead, m, pos: [-(w - head) / 2, 0, 0], rot: [0, 0, Math.PI / 2] },
    { g: hexHead, m, pos: [(w - head) / 2, 0, 0], rot: [0, 0, Math.PI / 2] },
  ];
}

function book({ w, h, d }: Dims, color: string): Part[] {
  const c = Math.min(0.06 * h + 0.002, h / 4), m: Mat = { color, roughness: 0.7 }, pages: Mat = { color: "#f2ead3", roughness: 0.9 };
  const inset = 0.03 * Math.min(w, d);
  return [
    { g: new BoxGeometry(w, c, d), m, pos: [0, h / 2 - c / 2, 0] },
    { g: new BoxGeometry(w, c, d), m, pos: [0, -h / 2 + c / 2, 0] },
    { g: new BoxGeometry(c, h, d), m, pos: [-w / 2 + c / 2, 0, 0] },
    { g: new BoxGeometry(w - c - inset, h - 2 * c, d - 2 * inset), m: pages, pos: [c / 2 - inset / 2 + 0.0 * w, 0, 0] },
  ];
}

function pallet({ w, h, d }: Dims): Part[] {
  const m: Mat = { color: "#c9a26a", roughness: 0.9 }, parts: Part[] = [];
  const deck = 0.14 * h, block = h - 2 * deck, bw = 0.16 * w, bd = 0.12 * d, boardW = 0.15 * d;
  const dark: Mat = { color: "#b58d55", roughness: 0.9 };
  for (let i = 0; i < 5; i++) { // top boards across z
    const z = -d / 2 + boardW / 2 + (i * (d - boardW)) / 4;
    parts.push({ g: new BoxGeometry(w, deck, boardW), m, pos: [0, h / 2 - deck / 2, z] });
  }
  for (let i = 0; i < 3; i++) {
    const z = -d / 2 + boardW / 2 + (i * (d - boardW)) / 2;
    parts.push({ g: new BoxGeometry(w, deck, boardW), m: dark, pos: [0, -h / 2 + deck / 2, z] });
    for (let j = 0; j < 3; j++) parts.push({ g: new BoxGeometry(bw, block, Math.max(bd, boardW)), m: dark, pos: [-w / 2 + bw / 2 + (j * (w - bw)) / 2, 0, z] });
  }
  return parts;
}

function screwdriver({ w, h, d }: Dims, color: string): Part[] {
  const r = Math.min(h, d) / 2;
  const handle = lathe([[0.001, 0], [0.7 * r, 0], [r, 0.1 * w], [r, 0.3 * w], [0.8 * r, 0.38 * w], [0.001, 0.38 * w]], 20);
  return [
    { g: handle, m: { color, roughness: 0.5 }, pos: [-w / 2, 0, 0], rot: [0, 0, -Math.PI / 2] },
    { g: cyl(0.16 * r, 0.62 * w, 12), m: { color: "#b9c0c7", metalness: 0.9, roughness: 0.25 }, pos: [0.19 * w, 0, 0], rot: [0, 0, Math.PI / 2] },
    { g: cyl(0.3 * r, 0.04 * w, 12), m: { color: "#6b7178", metalness: 0.8, roughness: 0.3 }, pos: [-0.12 * w, 0, 0], rot: [0, 0, Math.PI / 2] },
  ];
}

/** Natural colour for fruit: green/yellow apples by name, else red. */
function appleColour(name: string | undefined, id: string): string {
  const t = `${name ?? ""} ${id}`.toLowerCase();
  return t.includes("green") ? "#7bb33a" : t.includes("yellow") ? "#e0c341" : "#b3202a";
}

function build(kind: ObjectKind, dims: Dims, color: string, name: string | undefined, id: string): Part[] {
  const t = `${name ?? ""} ${id}`.toLowerCase();
  switch (kind) {
    case "bottle": return bottle(dims, color, !t.includes("glass"));
    case "can": return can(dims, color);
    case "mug": return cup(dims, color, true);
    case "cup": return cup(dims, color, false);
    case "apple": return apple(dims, appleColour(name, id));
    case "orange": return orange(dims, "#f08a1c", true);
    case "ball": return orange(dims, color, false);
    case "egg": return egg(dims);
    case "box": return box(dims, color);
    case "tote": return tote(dims, color);
    case "dumbbell": return dumbbell(dims);
    case "book": return book(dims, color);
    case "pallet": return pallet(dims);
    case "screwdriver": return screwdriver(dims, color);
  }
}

const cache = new Map<string, Part[]>();
/** Parts for a kind at a size (world x, y, z up), memoised so playback never rebuilds geometry. */
function partsFor(kind: ObjectKind, size: [number, number, number], color: string, name: string | undefined, id: string): Part[] {
  const key = [kind, ...size.map((s) => s.toFixed(4)), color, name ?? "", id].join("|");
  let parts = cache.get(key);
  if (!parts) { parts = build(kind, { w: size[0], h: size[2], d: size[1] }, color, name, id); cache.set(key, parts); }
  return parts;
}

/** A realistic model for `o`, centred on its origin in the z-up world frame; null when no model matches (draw the primitive). */
export function ObjectModel({ o, name, kind }: { o: { id: string; shape: string; size: [number, number, number]; color: string }; name?: string; kind: ObjectKind }) {
  const parts = useMemo(() => partsFor(kind, o.size, o.color, name, o.id), [kind, o.size[0], o.size[1], o.size[2], o.color, name, o.id]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <group rotation={[Math.PI / 2, 0, 0]}>
      {parts.map((p, i) => (
        <mesh key={i} geometry={p.g} position={p.pos} rotation={p.rot} scale={p.scale} castShadow>
          <meshStandardMaterial color={p.m.color} roughness={p.m.roughness ?? 0.5} metalness={p.m.metalness ?? 0} transparent={p.m.opacity !== undefined} opacity={p.m.opacity ?? 1} />
        </mesh>
      ))}
    </group>
  );
}

