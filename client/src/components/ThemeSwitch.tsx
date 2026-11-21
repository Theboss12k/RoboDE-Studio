import { ThemeDark, ThemeLight, ThemeSystem } from "./icons";
import { useThemePreference, type ThemePreference } from "../theme";

const OPTIONS: { id: ThemePreference; label: string; Icon: typeof ThemeLight }[] = [
  { id: "light", label: "Light theme", Icon: ThemeLight },
  { id: "dark", label: "Dark theme", Icon: ThemeDark },
  { id: "system", label: "System theme", Icon: ThemeSystem },
];

export default function ThemeSwitch() {
  const [pref, setPref] = useThemePreference();
  return (
    <div className="seg theme-switch" role="group" aria-label="Colour theme">
      {OPTIONS.map(({ id, label, Icon }) => (
        <button key={id} type="button" aria-label={label} title={label} aria-pressed={pref === id} onClick={() => setPref(id)}>
          <Icon size={15} aria-hidden="true" />
        </button>
      ))}
    </div>
  );
}
