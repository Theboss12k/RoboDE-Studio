// Build tabs b1, b2, ...: each shows the build it was based on, like the design version tabs.
import { Complete } from "../icons";
import type { StudioBuildSummary } from "../../types";

export default function BuildTabs({ builds, current, onSelect }: { builds: StudioBuildSummary[]; current: number | undefined; onSelect: (n: number) => void }) {
  return (
    <div className="tabs" role="tablist" aria-label="Robot builds">
      {builds.map((b) => (
        <button key={b.build} role="tab" aria-selected={current === b.build} onClick={() => onSelect(b.build)}>b{b.build}
          {b.parent_build != null && <span className="small muted"> based on b{b.parent_build}</span>}
          {b.approved && <><Complete size={14} aria-hidden="true" /><span className="sr-only"> (approved)</span></>}</button>
      ))}
    </div>
  );
}
