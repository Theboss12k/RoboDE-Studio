import { Suspense, useEffect, useLayoutEffect, useMemo } from "react";
import { Canvas, useFrame, useLoader, useThree } from "@react-three/fiber";
import { AnimationMixer, Box3, Matrix4, Mesh, Object3D, OrthographicCamera, PMREMGenerator, Vector3, WebGLRenderer } from "three";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

const viewDirection = new Vector3(2.8, 2.05, 4.9).normalize();
const shadowVertex = `
  varying vec2 shadowUv;
  void main() {
    shadowUv = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;
const shadowFragment = `
  varying vec2 shadowUv;
  void main() {
    float distanceFromCenter = length((shadowUv - 0.5) * 2.0);
    gl_FragColor = vec4(0.07, 0.06, 0.05, 0.42 * (1.0 - smoothstep(0.20, 1.0, distanceFromCenter)));
  }
`;

function Reflections() {
  const { gl, scene, invalidate } = useThree();
  useLayoutEffect(() => {
    if (!(gl instanceof WebGLRenderer)) return;
    const room = new RoomEnvironment();
    const pmrem = new PMREMGenerator(gl);
    const reflection = pmrem.fromScene(room, .035);
    pmrem.dispose();
    room.dispose();
    const previous = scene.environment;
    const intensity = scene.environmentIntensity;
    scene.environment = reflection.texture;
    scene.environmentIntensity = .65;
    invalidate();
    return () => {
      scene.environment = previous;
      scene.environmentIntensity = intensity;
      reflection.dispose();
    };
  }, [gl, scene, invalidate]);
  return null;
}

function modelView(camera: OrthographicCamera, model: Object3D, bounds: Box3, width: number, height: number) {
  const middle = bounds.getCenter(new Vector3());
  camera.position.copy(middle).addScaledVector(viewDirection, 6);
  camera.lookAt(middle);
  camera.updateMatrixWorld();
  const projected = new Box3();
  const view = new Matrix4();
  const corner = new Vector3();
  model.updateWorldMatrix(true, true);
  model.traverse((object) => {
    if (!(object instanceof Mesh)) return;
    const geometry = object.geometry;
    if (!geometry.boundingBox) geometry.computeBoundingBox();
    const { min, max } = geometry.boundingBox!;
    view.multiplyMatrices(camera.matrixWorldInverse, object.matrixWorld);
    for (const x of [min.x, max.x]) {
      for (const y of [min.y, max.y]) {
        for (const z of [min.z, max.z]) projected.expandByPoint(corner.set(x, y, z).applyMatrix4(view));
      }
    }
  });
  const size = projected.getSize(new Vector3());
  const center = projected.getCenter(new Vector3());
  const aspect = width / Math.max(1, height);
  const halfHeight = Math.max(size.y, size.x / Math.max(.1, aspect)) * .56;
  camera.left = center.x - halfHeight * aspect;
  camera.right = center.x + halfHeight * aspect;
  camera.top = center.y + halfHeight;
  camera.bottom = center.y - halfHeight;
  camera.updateProjectionMatrix();
}

function Artifact({ url, playing, onReady }: { url: string; playing: boolean; onReady: () => void }) {
  const gltf = useLoader(GLTFLoader, url);
  const model = useMemo(() => {
    const copy = gltf.scene.clone(true);
    copy.traverse((object) => {
      if (object instanceof Mesh) object.castShadow = object.receiveShadow = true;
    });
    return copy;
  }, [gltf.scene]);
  const bounds = useMemo(() => new Box3().setFromObject(model), [model]);
  const mixer = useMemo(() => new AnimationMixer(model), [model]);
  const { camera, size, invalidate } = useThree();
  const { width, height } = size;
  const extent = bounds.getSize(new Vector3());
  const middle = bounds.getCenter(new Vector3());

  useLayoutEffect(() => {
    if (camera instanceof OrthographicCamera && width > 0 && height > 0) {
      modelView(camera, model, bounds, width, height);
      invalidate();
    }
  }, [camera, model, bounds, width, height, invalidate]);
  useEffect(() => {
    const clip = gltf.animations[0];
    if (!clip) throw new Error("The model has no embedded motion.");
    mixer.clipAction(clip).play();
    onReady();
    return () => {
      mixer.stopAllAction();
      mixer.uncacheRoot(model);
    };
  }, [mixer, gltf.animations, model, onReady]);

  useFrame((_, delta) => { if (playing) mixer.update(Math.min(delta, .06)); });
  return (
    <>
      <mesh position={[middle.x, -.006, middle.z]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[extent.x * 1.28, extent.z * 1.35]} />
        <shaderMaterial vertexShader={shadowVertex} fragmentShader={shadowFragment} transparent depthWrite={false} />
      </mesh>
      <primitive object={model} />
    </>
  );
}

export default function RobotArtifactViewer({ url, playing, onReady }: {
  url: string; playing: boolean; onReady: () => void;
}) {
  return (
    <Canvas
      orthographic
      shadows="percentage"
      camera={{ near: .05, far: 25 }}
      dpr={[1, 1.5]}
      frameloop={playing ? "always" : "demand"}
      gl={{ alpha: true, antialias: true }}
    >
      <hemisphereLight args={["#f5f1e9", "#a69b8f", .65]} />
      <directionalLight position={[2.1, 4.7, 3.1]} color="#fff0dd" intensity={2.2} castShadow
        shadow-mapSize={[768, 768]} shadow-normalBias={.013}
        shadow-camera-left={-2.2} shadow-camera-right={2.2} shadow-camera-top={2.2} shadow-camera-bottom={-2.2} />
      <directionalLight position={[-3, 2, -2.8]} color="#d4e8ff" intensity={1.3} />
      <Suspense fallback={null}>
        <Reflections />
        <Artifact url={url} playing={playing} onReady={onReady} />
      </Suspense>
    </Canvas>
  );
}
