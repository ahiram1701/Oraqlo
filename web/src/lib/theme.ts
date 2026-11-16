// Tema elegido por el usuario: "system" sigue al sistema; "light"/"dark" lo fijan.
export type ThemeChoice = "system" | "light" | "dark";

const KEY = "oraqlo.theme";
const COLORS = { dark: "#0b0d12", light: "#f6f7fb" };

export function getTheme(): ThemeChoice {
  try {
    const value = localStorage.getItem(KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(choice: ThemeChoice = getTheme()): void {
  const root = document.documentElement;
  if (choice === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", choice);
  // Color de la barra de estado del celular: el del tema efectivo.
  for (const meta of Array.from(document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]'))) {
    const scheme = meta.media.includes("light") ? "light" : "dark";
    meta.content = choice === "system" ? COLORS[scheme] : COLORS[choice];
  }
}

export function setTheme(choice: ThemeChoice): void {
  try {
    if (choice === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, choice);
  } catch {
    /* sin almacenamiento: solo esta sesión */
  }
  applyTheme(choice);
}
