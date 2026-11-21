// "Link a phone": a QR code a phone on the same Wi-Fi scans to send photos straight into this project's AR scenes.
// The phone talks to a separate listener on the desktop (docs/AR_PHOTO.md, "Phone link"), never to /api/v1.
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api";
import type { ArPairing } from "@robode/studio-core";

function remaining(expiresAt: string, now: number): string {
  const s = Math.max(0, Math.round((Date.parse(expiresAt) - now) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export default function PhoneLink({ pid, onReceived, onClose, receivedLabel = "Photos received" }: {
  pid: string;
  /** What the counter calls the uploads ("Photos received" for photo AR, "Sent from the phone" for site videos). */
  receivedLabel?: string;
  /** Called when the phone has sent another photo, so the scenes list can be fetched again. */
  onReceived: () => void;
  onClose: () => void;
}) {
  const [pairing, setPairing] = useState<ArPairing | null>(null);
  const [url, setUrl] = useState("");
  const [qr, setQr] = useState("");
  const [now, setNow] = useState(() => Date.now());
  const [error, setError] = useState("");
  const [ended, setEnded] = useState(false);
  const [busy, setBusy] = useState(false);
  const seen = useRef(0);
  const received = useRef(onReceived);
  received.current = onReceived;

  // One start at a time: a second concurrent start (React's double mount in development) would replace the
  // first link while its QR code is being fetched.
  const starting = useRef<Promise<void> | null>(null);
  const begin = useCallback(() => {
    if (starting.current) return starting.current;
    starting.current = start().finally(() => { starting.current = null; });
    return starting.current;
  }, [pid]); // eslint-disable-line react-hooks/exhaustive-deps
  const start = async () => {
    setBusy(true); setError(""); setEnded(false);
    try {
      let p: ArPairing;
      try { p = await api.arPairing(pid); } catch (e) {
        if ((e as { status?: number }).status !== 404) throw e;
        p = await api.startArPairing(pid);
      }
      seen.current = p.uploads;
      setPairing(p);
      setUrl((cur) => (cur && p.urls.includes(cur) ? cur : p.url));
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  useEffect(() => { void begin(); }, [begin]);

  // The QR code of the chosen address. The SVG goes in through an <img> data URL, never as markup.
  const urlsKey = pairing?.urls.join("|");
  useEffect(() => {
    if (!pairing || !url) { setQr(""); return; }
    let live = true;
    api.arPairingQr(pid, url).then((svg) => { if (live) { setQr(svg); setError(""); } }).catch((e) => { if (live) setError((e as Error).message); });
    return () => { live = false; };
  }, [pid, url, urlsKey]); // eslint-disable-line react-hooks/exhaustive-deps

  // Every 2 s: new photos refresh the list; a 404 means the link expired or ended.
  const active = pairing !== null;
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => {
      api.arPairing(pid).then((p) => {
        if (p.uploads > seen.current) received.current();
        seen.current = p.uploads;
        setPairing(p);
      }).catch((e) => {
        if ((e as { status?: number }).status === 404) { setPairing(null); setQr(""); setEnded(true); }
        else setError((e as Error).message);
      });
    }, 2000);
    return () => window.clearInterval(timer);
  }, [pid, active]);

  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);

  async function stop() {
    setBusy(true); setError("");
    try { await api.stopArPairing(pid); setPairing(null); setQr(""); onClose(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }

  return (
    <div className="stack phone-link" role="region" aria-label="Link a phone">
      {pairing && (
        <>
          {qr && <img alt="QR code to open the photo page on your phone" width={200} height={200}
            src={`data:image/svg+xml;charset=utf-8,${encodeURIComponent(qr)}`} />}
          {pairing.urls.length > 1 ? (
            <label className="field"><span>Address</span>
              <select value={url} onChange={(e) => setUrl(e.target.value)}>
                {pairing.urls.map((u) => <option key={u} value={u}>{u.replace(/\/p\/.*$/, "")}</option>)}
              </select></label>
          ) : null}
          <p className="small"><code>{url}</code></p>
          <p className="small">Time left {remaining(pairing.expires_at, now)}. {receivedLabel}: {pairing.uploads}</p>
          <div className="row"><button disabled={busy} onClick={() => void stop()}>Stop linking</button></div>
        </>
      )}
      {!pairing && ended && (
        <div className="row">
          <span className="small muted">The link has ended.</span>
          <button disabled={busy} onClick={() => void begin()}>Start a new link</button>
        </div>
      )}
      <p className="small muted">Your phone must be on the same Wi-Fi. Windows may ask to allow RoboDE through the firewall: allow Private networks only.</p>
      {error && <div className="notice error" role="alert">{error}</div>}
    </div>
  );
}
