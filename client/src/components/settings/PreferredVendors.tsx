// Settings: the shops to search first for robots, motors and electronics. A global setting kept by the server.
import { useEffect, useState } from "react";
import { api } from "../../api";
import { Add, Delete } from "../icons";

type Category = "robots" | "motors" | "electronics";
type Vendor = { name: string; domain: string; categories: Category[]; note: string };
const CATEGORIES: { id: Category; label: string }[] = [
  { id: "robots", label: "Robots" }, { id: "motors", label: "Motors" }, { id: "electronics", label: "Electronics" },
];
const MAX = 50;
const same = (a: Vendor[], b: Vendor[]) => JSON.stringify(a) === JSON.stringify(b);

export default function PreferredVendors() {
  const [saved, setSaved] = useState<Vendor[] | null>(null);
  const [rows, setRows] = useState<Vendor[]>([]);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  useEffect(() => {
    api.get<{ vendors: Vendor[] }>("/settings/vendors").then((r) => { setSaved(r.vendors); setRows(r.vendors); }).catch((e) => setError(e.message));
  }, []);

  const change = (i: number, patch: Partial<Vendor>) => { setNote(""); setRows(rows.map((v, j) => (j === i ? { ...v, ...patch } : v))); };
  const toggle = (i: number, c: Category) => {
    const has = rows[i].categories.includes(c);
    change(i, { categories: CATEGORIES.map((x) => x.id).filter((x) => (x === c ? !has : rows[i].categories.includes(x))) });
  };
  async function save() {
    setError(""); setNote("");
    try {
      const r = await api.put<{ vendors: Vendor[] }>("/settings/vendors", { vendors: rows.map((v) => ({ ...v, name: v.name.trim() || v.domain.trim(), domain: v.domain.trim(), note: v.note.trim() })) });
      setSaved(r.vendors); setRows(r.vendors); setNote("Saved. New searches and builds use this list.");
    } catch (e) { setError((e as Error).message); }
  }
  const dirty = saved !== null && !same(saved, rows);

  return (
    <div className="panel">
      <h3>Preferred vendors</h3>
      <p className="small muted">Shops you want searched first. Robot, motor and electronics searches look at these sites before others and mark matching results "Preferred vendor". Use the bare domain, for example robu.in. A preferred product is ranked first; it still has to meet the requirements.</p>
      {error && <div className="notice error" role="alert">{error}</div>}
      {saved === null && !error ? <div className="busy">Loading</div> : (
        <>
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Preferred vendors table">
            <table>
              <thead><tr><th>Name</th><th>Domain</th><th>Use for</th><th>Note</th><th><span className="sr-only">Remove</span></th></tr></thead>
              <tbody>
                {rows.length === 0 && <tr><td colSpan={5} className="muted small">No preferred vendors yet.</td></tr>}
                {rows.map((v, i) => (
                  <tr key={i}>
                    <td><input aria-label={`Vendor ${i + 1} name`} value={v.name} maxLength={120} onChange={(e) => change(i, { name: e.target.value })} placeholder="Robu" /></td>
                    <td><input aria-label={`Vendor ${i + 1} domain`} className="mono" value={v.domain} spellCheck={false} autoCapitalize="none" onChange={(e) => change(i, { domain: e.target.value })} placeholder="robu.in" /></td>
                    <td><div className="row">{CATEGORIES.map((c) => (
                      <label key={c.id} className="row small"><input type="checkbox" checked={v.categories.includes(c.id)} onChange={() => toggle(i, c.id)} /> {c.label}</label>))}</div></td>
                    <td><input aria-label={`Vendor ${i + 1} note`} value={v.note} maxLength={300} onChange={(e) => change(i, { note: e.target.value })} placeholder="Ships within India" /></td>
                    <td><button type="button" className="link" aria-label={`Remove ${v.name || v.domain || `vendor ${i + 1}`}`} onClick={() => { setNote(""); setRows(rows.filter((_, j) => j !== i)); }}><Delete size={16} aria-hidden="true" /> Remove</button></td>
                  </tr>))}
              </tbody>
            </table>
          </div>
          <div className="row">
            <button type="button" disabled={rows.length >= MAX} onClick={() => { setNote(""); setRows([...rows, { name: "", domain: "", categories: ["robots", "motors", "electronics"], note: "" }]); }}><Add size={16} aria-hidden="true" /> Add vendor</button>
            <button type="button" className="go" disabled={!dirty} onClick={() => void save()}>Save vendors</button>
            {note && <span className="small muted" role="status">{note}</span>}
          </div>
        </>)}
    </div>
  );
}
