// One slider and a pair of jog buttons per joint (and the gripper). Moving a slider sets the target; the twin steps
// toward it under the build's limits. Holding a jog button keeps the target a short way ahead of the joint.
import { fmt } from "../common";
import type { TwinJointLimit } from "../../types";
import { fromDisplay, toDisplay, unitOf } from "./units";

export type Jog = { joint: string; dir: 1 | -1 } | null;

export default function JointSliders({ limits, target, disabled, onTarget, onJog, onNudge }: {
  limits: TwinJointLimit[]; target: Record<string, number>; disabled: boolean;
  onTarget: (joint: string, value: number) => void; onJog: (jog: Jog) => void; onNudge: (joint: string, dir: 1 | -1) => void;
}) {
  return (
    <div className="joint-sliders">
      {limits.map((l) => {
        const lo = toDisplay(l.kind, l.lower), hi = toDisplay(l.kind, l.upper), unit = unitOf(l.kind);
        const hold = (dir: 1 | -1) => ({
          onPointerDown: (e: React.PointerEvent) => { if (disabled) return; (e.target as Element).setPointerCapture?.(e.pointerId); onJog({ joint: l.joint, dir }); },
          onPointerUp: () => onJog(null), onPointerCancel: () => onJog(null), onPointerLeave: () => onJog(null),
          onClick: (e: React.MouseEvent) => { if (e.detail === 0) onNudge(l.joint, dir); },  // keyboard activation
        });
        return (
          <div key={l.joint} className="joint-row">
            <label htmlFor={`tw-${l.joint}`} className="joint-name">{l.joint}</label>
            <button type="button" disabled={disabled} aria-label={`Jog ${l.joint} down`} {...hold(-1)}>−</button>
            <input id={`tw-${l.joint}`} type="range" min={lo} max={hi} step={(hi - lo) / 500 || 0.01} disabled={disabled}
              value={toDisplay(l.kind, target[l.joint] ?? 0)} onChange={(e) => onTarget(l.joint, fromDisplay(l.kind, Number(e.target.value)))} />
            <button type="button" disabled={disabled} aria-label={`Jog ${l.joint} up`} {...hold(1)}>+</button>
            <span className="small mono joint-val">{fmt(toDisplay(l.kind, target[l.joint] ?? 0))} {unit}</span>
          </div>);
      })}
    </div>
  );
}
