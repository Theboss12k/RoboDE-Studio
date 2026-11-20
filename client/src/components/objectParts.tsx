// Draws an `ObjectModel` (primitives made by the objects agent). Coordinates are the model's own frame: origin at the
// centre of the object's box, z up, so the caller puts it inside the z-up world group at the object's (or the held)
// centre, exactly as the primitive it replaces. three.js cylinders, cones and capsules have their axis along y: the
// mesh is turned a quarter turn about x so the axis is z. Torus rings already lie in the x-y plane.
import type { ObjectModelPart } from "../types";

const SEGMENTS = 28;

function Geometry({ p }: { p: ObjectModelPart }) {
  const [sx, sy, sz] = p.size;
  switch (p.shape) {
    case "box": return <boxGeometry args={[sx, sy, sz]} />;
    case "sphere": return <sphereGeometry args={[0.5, SEGMENTS, 16]} />;
    case "cylinder": return <cylinderGeometry args={[sx / 2, sx / 2, sz, SEGMENTS]} />;
    case "cone": return <coneGeometry args={[sx / 2, sz, SEGMENTS]} />;
    case "capsule": {
      const r = Math.min(sx, sz) / 2;
      return <capsuleGeometry args={[r, Math.max(sz - 2 * r, 1e-4), 8, SEGMENTS]} />;
    }
    case "torus": {
      const tube = sz / 2;
      return <torusGeometry args={[Math.max(sx / 2 - tube, 1e-4), tube, 12, SEGMENTS]} />;
    }
  }
}

/** Mesh-local scale: spheres are unit spheres stretched to the size; round parts stretch their y extent (which is the
 *  mesh-local z after the quarter turn; torus rings are not turned). */
function scaleOf(p: ObjectModelPart): [number, number, number] {
  const [sx, sy, sz] = p.size;
  if (p.shape === "sphere") return [sx, sy, sz];
  if (p.shape === "box") return [1, 1, 1];
  const k = sx > 0 ? sy / sx : 1;
  if (p.shape === "torus") return [1, k, 1];
  return [1, 1, k];
}

export function ModelParts({ model }: { model: { parts: ObjectModelPart[] } }) {
  return (
    <>
      {model.parts.map((p, i) => (
        <group key={i} position={p.center} rotation={p.rotation}>
          <mesh rotation={p.shape === "cylinder" || p.shape === "cone" || p.shape === "capsule" ? [Math.PI / 2, 0, 0] : [0, 0, 0]}
            scale={scaleOf(p)} castShadow receiveShadow>
            <Geometry p={p} />
            <meshStandardMaterial color={p.color} roughness={p.roughness} metalness={p.metalness}
              transparent={p.opacity < 1} opacity={p.opacity} />
          </mesh>
        </group>
      ))}
    </>
  );
}
