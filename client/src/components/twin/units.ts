// Display units for the twin: revolute joints in degrees, prismatic joints in millimetres; everything else stays SI.
import type { TwinJointLimit } from "../../types";

export const toDisplay = (kind: TwinJointLimit["kind"], v: number) => (kind === "prismatic" ? v * 1000 : (v * 180) / Math.PI);
export const fromDisplay = (kind: TwinJointLimit["kind"], v: number) => (kind === "prismatic" ? v / 1000 : (v * Math.PI) / 180);
export const unitOf = (kind: TwinJointLimit["kind"]) => (kind === "prismatic" ? "mm" : "°");
export const speedUnitOf = (kind: TwinJointLimit["kind"]) => (kind === "prismatic" ? "mm/s" : "°/s");
