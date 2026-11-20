// The bill of materials: name, quantity, material, process, mass and the CAD files of each part.
import { fmt } from "../common";
import type { BuildPart } from "../../types";

export const buildFileUrl = (pid: string, build: number, path: string) => `/api/v1/projects/${pid}/studio/builds/${build}/files/${path.split("/").map(encodeURIComponent).join("/")}`;

export default function PartList({ pid, build, parts }: { pid: string; build: number; parts: BuildPart[] }) {
  const total = parts.reduce((s, p) => s + p.mass_kg * p.qty, 0);
  return (
    <div className="stack">
      <div className="table-wrap">
        <table>
          <caption className="sr-only">Parts of build b{build}</caption>
          <thead><tr><th>Part</th><th>Qty</th><th>Material</th><th>Process</th><th>Mass each</th><th>Files</th></tr></thead>
          <tbody>
            {parts.map((p) => (
              <tr key={p.id}>
                <td>{p.url ? <a href={p.url} target="_blank" rel="noreferrer noopener">{p.name}</a> : p.name}
                  {(p.link || p.joint) && <div className="small muted">{[p.link && `link ${p.link}`, p.joint && `joint ${p.joint}`].filter(Boolean).join(", ")}</div>}
                  {p.spec && <div className="small muted">{p.spec}</div>}</td>
                <td>{p.qty}</td><td>{p.material || "–"}</td><td>{p.process}</td><td className="mono">{fmt(p.mass_kg * 1000, "g")}</td>
                <td>{p.files.length ? p.files.map((f) => <a key={f.path} className="file-link" href={buildFileUrl(pid, build, f.path)} download>{f.kind.toUpperCase()}</a>) : <span className="muted small">{p.source === "custom" ? "–" : p.source}</span>}</td>
              </tr>))}
          </tbody>
          <tfoot><tr><td colSpan={4}><b>Total</b></td><td className="mono"><b>{fmt(total * 1000, "g")}</b></td><td /></tr></tfoot>
        </table>
      </div>
    </div>
  );
}
