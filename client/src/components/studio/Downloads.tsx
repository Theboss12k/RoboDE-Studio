// Downloads of one build: assembly STEP, per-part STEP/STL, the URDF package files, and, once approved, the PDF and ZIP.
import { Download } from "../icons";
import type { BuildFile, StudioBuildView } from "../../types";
import { buildFileUrl } from "./PartList";

const size = (b: number) => (b >= 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} kB`);

export default function Downloads({ pid, build }: { pid: string; build: StudioBuildView }) {
  const n = build.build;
  const link = (f: BuildFile) => <li key={f.path}><a href={buildFileUrl(pid, n, f.path)} download><Download size={14} aria-hidden="true" style={{ verticalAlign: "-2px" }} /> {f.path}</a> <span className="small muted">{size(f.bytes)}</span></li>;
  const assembly = build.files.filter((f) => f.path === "assembly.step" || f.kind === "csv");
  const parts = build.files.filter((f) => f.path.startsWith("parts/"));
  const urdf = build.files.filter((f) => f.path.startsWith("urdf/"));
  return (
    <div className="grid-2 downloads">
      <div className="stack">
        <h4>Assembly and bill of materials</h4>
        <ul className="plain">{assembly.map(link)}{!assembly.length && <li className="small muted">No assembly file.</li>}</ul>
        <h4>URDF package (robot and meshes)</h4>
        <ul className="plain">{urdf.map(link)}{!urdf.length && <li className="small muted">No URDF files.</li>}</ul>
      </div>
      <div className="stack">
        <h4>Per-part STEP and STL</h4>
        <ul className="plain">{parts.map(link)}{!parts.length && <li className="small muted">No part files.</li>}</ul>
      </div>
      <div className="stack" style={{ gridColumn: "1 / -1" }}>
        <h4>Manufacturer package</h4>
        {build.approved
          ? <div className="row">
              <a className="button primary" href={`/api/v1/projects/${pid}/studio/builds/${n}/pdf`} download><Download size={15} aria-hidden="true" /> Manufacturer PDF</a>
              <a className="button primary" href={`/api/v1/projects/${pid}/studio/builds/${n}/package`} download><Download size={15} aria-hidden="true" /> Build package (ZIP)</a>
            </div>
          : <p className="small muted">The manufacturer PDF and the build package ZIP are made when you approve this build.</p>}
      </div>
    </div>
  );
}
