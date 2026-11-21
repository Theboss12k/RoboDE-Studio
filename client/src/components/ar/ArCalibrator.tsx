// Calibrate a photo from a known rectangle on the floor: drag four handles onto its corners, pick its size, calibrate.
// Handles live in image pixel coordinates (the SVG viewBox is the photo's pixel grid).
import { useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { api } from "../../api";
import type { ArScene } from "../../types";

/** What the calibrator needs of an image: its pixel size and, optionally, corners to start from. A photo scene or a scan frame. */
export type CalibratorImage = { width: number; height: number; reference?: { corners_px: number[][] } | null };
/** What a custom submit answers with (a scan frame sets the scale instead of calibrating a photo). */
export type CalibratorResult = { residual: number | null; warnings: string[] };

type Pt = [number, number];
const PRESETS = {
  a4: { label: "A4 (0.297 x 0.210 m)", size: [0.297, 0.21] },
  letter: { label: "US Letter (0.2794 x 0.2159 m)", size: [0.2794, 0.2159] },
  custom: { label: "Custom size", size: [0.3, 0.2] },
} as const;
type PresetId = keyof typeof PRESETS;

function startCorners(scene: CalibratorImage): Pt[] {
  const saved = scene.reference?.corners_px;
  if (saved && saved.length === 4) return saved.map((p) => [p[0], p[1]] as Pt);
  const { width: w, height: h } = scene;
  const x0 = w * 0.35, x1 = w * 0.65, y0 = h * 0.55, y1 = h * 0.8;
  return [[x0, y1], [x1, y1], [x1, y0], [x0, y0]];
}

export default function ArCalibrator({ pid, scene, photoUrl, onScene, onSubmit, submitLabel = "Calibrate", successText = "Calibrated." }: {
  pid: string; scene: CalibratorImage & { id?: string }; photoUrl: string; onScene?: (s: ArScene) => void;
  /** Replaces the photo calibration: gets the corners and the size and answers with what to show. */
  onSubmit?: (corners: number[][], size: [number, number]) => Promise<CalibratorResult | void>;
  submitLabel?: string; successText?: string;
}) {
  const [corners, setCorners] = useState<Pt[]>(() => startCorners(scene));
  const [preset, setPreset] = useState<PresetId>("a4");
  const [custom, setCustom] = useState<[number, number]>([0.3, 0.2]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<{ residual: number | null; warnings: string[] } | null>(null);
  const svg = useRef<SVGSVGElement>(null);
  const drag = useRef<number | null>(null);
  const { width, height } = scene;
  const r = Math.max(8, width / 70);
  const size = preset === "custom" ? custom : PRESETS[preset].size;

  const clamp = (p: Pt): Pt => [Math.min(Math.max(p[0], 0), width), Math.min(Math.max(p[1], 0), height)];
  const move = (i: number, p: Pt) => setCorners((c) => c.map((q, j) => (j === i ? clamp(p) : q)));
  function toImage(e: PointerEvent): Pt | null {
    const el = svg.current;
    const m = el?.getScreenCTM();
    if (!el || !m) return null;
    const pt = el.createSVGPoint();
    pt.x = e.clientX; pt.y = e.clientY;
    const q = pt.matrixTransform(m.inverse());
    return [q.x, q.y];
  }
  function onKey(i: number, e: KeyboardEvent) {
    const step = Math.max(1, width / 400) * (e.shiftKey ? 10 : 1);
    const d: Record<string, Pt> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    const v = d[e.key];
    if (!v) return;
    e.preventDefault();
    move(i, [corners[i][0] + v[0], corners[i][1] + v[1]]);
  }

  async function calibrate() {
    setBusy(true); setError(""); setResult(null);
    try {
      const corners4 = corners.map((c) => [c[0], c[1]]);
      if (onSubmit) {
        setResult((await onSubmit(corners4, [size[0], size[1]])) ?? { residual: null, warnings: [] });
      } else {
        const updated = await api.calibrateArScene(pid, scene.id!, { corners_px: corners4, size_m: [size[0], size[1]] });
        onScene?.(updated);
        setResult({ residual: updated.calibration?.residual_px ?? null, warnings: updated.calibration?.warnings ?? [] });
      }
    } catch (e) {
      setError((e as Error).message);
    } finally { setBusy(false); }
  }

  const valid = size[0] > 0 && size[1] > 0;
  return (
    <div className="stack">
      <p className="small muted">Drag the four handles onto the corners of a rectangle lying flat on the floor, such as a sheet of paper. Go around it in order 1 to 4. The side from 1 to 2 is the first size.</p>
      <svg ref={svg} className="ar-calib" viewBox={`0 0 ${width} ${height}`} role="group" aria-label="Photo with four corner handles"
        onPointerMove={(e) => { if (drag.current === null) return; const p = toImage(e); if (p) move(drag.current, p); }}
        onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }}>
        <image href={photoUrl} x={0} y={0} width={width} height={height} />
        <polygon points={corners.map((c) => c.join(",")).join(" ")} className="ar-quad" strokeWidth={Math.max(2, width / 500)} />
        {corners.map((c, i) => (
          <g key={i} transform={`translate(${c[0]} ${c[1]})`} className="ar-handle" tabIndex={0} role="button"
            aria-label={`Corner ${i + 1} at ${Math.round(c[0])}, ${Math.round(c[1])}. Use the arrow keys to move it, with Shift for larger steps.`}
            onKeyDown={(e) => onKey(i, e)}
            onPointerDown={(e) => { e.preventDefault(); (e.currentTarget as SVGGElement).setPointerCapture(e.pointerId); drag.current = i; }}>
            <circle r={r} />
            <text fontSize={r * 1.2} textAnchor="middle" dominantBaseline="central">{i + 1}</text>
          </g>
        ))}
      </svg>
      <div className="row">
        <label className="field"><span>Rectangle size</span>
          <select value={preset} onChange={(e) => setPreset(e.target.value as PresetId)}>
            {(Object.keys(PRESETS) as PresetId[]).map((id) => <option key={id} value={id}>{PRESETS[id].label}</option>)}
          </select></label>
        {preset === "custom" && (
          <>
            <label className="field"><span>1 to 2 (m)</span>
              <input type="number" min={0.01} step={0.001} value={custom[0]} onChange={(e) => setCustom([Number(e.target.value), custom[1]])} /></label>
            <label className="field"><span>2 to 3 (m)</span>
              <input type="number" min={0.01} step={0.001} value={custom[1]} onChange={(e) => setCustom([custom[0], Number(e.target.value)])} /></label>
          </>
        )}
        <button className="primary" disabled={busy || !valid} onClick={() => void calibrate()}>{busy ? (onSubmit ? "Working" : "Calibrating") : submitLabel}</button>
      </div>
      <p className="small muted">Corner 1 to 2 is {size[0]} m. Corner 2 to 3 is {size[1]} m.</p>
      {error && <div className="notice error" role="alert">{error}</div>}
      {result && (
        <div className="notice ok small" role="status">
          {successText}{result.residual != null && <> Residual {result.residual.toFixed(2)} px.</>}
          {result.warnings.length > 0 && <ul>{result.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
        </div>
      )}
    </div>
  );
}
