import { useEffect, useState } from "react";

export type ThemePreference = "system" | "light" | "dark";
const KEY = "robode.theme";
const listeners = new Set<(p: ThemePreference) => void>();

export function readTheme(): ThemePreference {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "light" || v === "dark") return v;
  } catch { /* storage unavailable */ }
  return "system";
}

function apply(pref: ThemePreference) {
  if (pref === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = pref;
}

export function setTheme(pref: ThemePreference) {
  try { if (pref === "system") localStorage.removeItem(KEY); else localStorage.setItem(KEY, pref); } catch { /* ignore */ }
  apply(pref);
  listeners.forEach((l) => l(pref));
}

/** Apply the saved preference before the first render. The CSS follows the OS itself in system mode. */
export function initTheme() {
  apply(readTheme());
}

export function useThemePreference(): [ThemePreference, (p: ThemePreference) => void] {
  const [pref, setPref] = useState(readTheme);
  useEffect(() => { listeners.add(setPref); return () => { listeners.delete(setPref); }; }, []);
  return [pref, setTheme];
}

/** The theme actually showing ("light" or "dark"), following the OS in system mode. */
export function useResolvedTheme(): "light" | "dark" {
  const [pref] = useThemePreference();
  const query = typeof window !== "undefined" ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  const [osDark, setOsDark] = useState(query?.matches ?? false);
  useEffect(() => {
    if (!query) return;
    const on = () => setOsDark(query.matches);
    query.addEventListener("change", on);
    on();
    return () => query.removeEventListener("change", on);
  }, [query]);
  return pref === "system" ? (osDark ? "dark" : "light") : pref;
}
