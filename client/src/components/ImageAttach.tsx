// Shared image-attach pieces: a file button, a drop/paste target and removable thumbnails (Questions, Brief).
import { useRef, useState, type ReactNode } from "react";
import { Add, Delete } from "./icons";

export function imagesFrom(files: FileList | File[] | null | undefined): File[] {
  return Array.from(files ?? []).filter((f) => f.type.startsWith("image/"));
}
export function imageFrom(files: FileList | File[] | null | undefined): File | undefined {
  return imagesFrom(files)[0];
}

export function AttachButton({ label, disabled, onFile, onFiles }: {
  label: string; disabled: boolean; onFile?: (f: File) => void; onFiles?: (f: File[]) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <span className="attach">
      <input ref={input} type="file" accept="image/*" multiple={!!onFiles} hidden onChange={(e) => {
        if (onFiles) { const f = imagesFrom(e.target.files); if (f.length) onFiles(f); }
        else { const f = imageFrom(e.target.files); if (f) onFile?.(f); }
        e.target.value = "";
      }} />
      <button type="button" disabled={disabled} onClick={() => input.current?.click()}><Add size={14} aria-hidden="true" /> {label}</button>
    </span>
  );
}

/** A dashed area that accepts dropped and pasted images. */
export function ImageDrop({ onFiles, disabled, children }: { onFiles: (f: File[]) => void; disabled?: boolean; children: ReactNode }) {
  const [over, setOver] = useState(false);
  return (
    <div className={`drop ${over ? "over" : ""}`} tabIndex={0}
      onDragOver={(e) => { e.preventDefault(); if (!disabled) setOver(true); }} onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); const f = imagesFrom(e.dataTransfer.files); if (f.length && !disabled) onFiles(f); }}
      onPaste={(e) => { const f = imagesFrom(e.clipboardData.files); if (f.length && !disabled) { e.preventDefault(); onFiles(f); } }}>
      {children}
    </div>
  );
}

export function Thumbs({ images, src, onRemove, disabled }: {
  images: { name: string }[]; src: (name: string) => string; onRemove: (name: string) => void; disabled?: boolean;
}) {
  if (!images.length) return null;
  return (
    <ul className="thumbs">
      {images.map((i) => (
        <li key={i.name}>
          <img src={src(i.name)} alt={i.name} />
          <button type="button" aria-label={`Remove ${i.name}`} disabled={disabled} onClick={() => onRemove(i.name)}><Delete size={14} aria-hidden="true" /></button>
        </li>))}
    </ul>
  );
}
