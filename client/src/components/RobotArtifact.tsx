import { Component, Suspense, lazy, useCallback, useEffect, useRef, useState, type ReactNode } from "react";

const Viewer = lazy(() => import("./RobotArtifactViewer"));
const artifacts = {
  brief: ["pick-and-place", "Pick-and-place robot"],
  reference: ["reference-scanner", "Machine-vision scanner"],
  questions: ["gripper-fixture", "Parallel-jaw gripper"],
  design: ["cartesian-gantry", "Cartesian gantry"],
  options: ["robot-lineup", "Arm and mobile robot"],
  analysis: ["load-test", "Instrumented load test"],
  decision: ["pallet-indexer", "Rotary pallet indexer"],
  settings: ["servo-calibration", "Servo calibration"],
} as const;

export type ArtifactPage = keyof typeof artifacts;

class ViewerBoundary extends Component<{ children: ReactNode; onError: () => void }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch() { this.props.onError(); }
  render() { return this.state.failed ? null : this.props.children; }
}

export default function RobotArtifact({ page, hero = false }: { page: ArtifactPage; hero?: boolean }) {
  const [file, label] = artifacts[page];
  const url = `${import.meta.env.BASE_URL}robotics-artifacts/${file}`;
  const root = useRef<HTMLDivElement>(null);
  const [started, setStarted] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const onReady = useCallback(() => setReady(true), []);
  const onError = useCallback(() => { setFailed(true); setPlaying(false); }, []);
  const loading = started && !ready && !failed;

  useEffect(() => {
    const node = root.current;
    if (!node || typeof IntersectionObserver === "undefined") return;
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    const observer = new IntersectionObserver((entries) => {
      const entry = entries[entries.length - 1];
      if (!entry) return;
      if (entry.isIntersecting && entry.intersectionRatio >= 0.25) { setStarted(true); setPlaying(true); }
      else setPlaying(false);
    }, { threshold: [0, 0.25] });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return (
    <div className={`robot-art${hero ? " robot-art-hero" : ""}`} data-artifact={file} ref={root}>
      <div className="robot-art-display" role="img" aria-label={label} aria-busy={loading}>
        <img src={`${url}.webp`} alt="" width={760} height={400} decoding="async" hidden={ready && !failed} />
        {started && !failed && (
          <div className={`robot-art-canvas${ready ? " is-ready" : ""}`} aria-hidden="true">
            <ViewerBoundary onError={onError}>
              <Suspense fallback={null}>
                <Viewer url={`${url}.glb`} playing={playing} onReady={onReady} />
              </Suspense>
            </ViewerBoundary>
          </div>
        )}
      </div>
      {failed && <span className="robot-art-status" role="status">3D preview unavailable</span>}
    </div>
  );
}
