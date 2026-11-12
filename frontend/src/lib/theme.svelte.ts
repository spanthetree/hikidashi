/* The look: a theme (its palette, type and motion), a mode (light, dark, or
   whatever the system says), and whether to draw it all as pixel art. They
   are saved in this browser and land on <html> as data-theme, data-scheme
   and data-pixel, which is all the CSS reads. app.html sets them before the
   first paint from the same keys, so a dark page never flashes white; keep
   the two in step. */

export const THEMES = ["hikidashi", "cyberpunk", "retrowave", "retrofuture"] as const;
export const MODES = ["system", "light", "dark"] as const;

export type Theme = (typeof THEMES)[number];
export type Mode = (typeof MODES)[number];

const THEME_KEY = "theme";
const MODE_KEY = "mode";
const PIXEL_KEY = "pixel";

function saved<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const value = localStorage.getItem(key);
    return value && (allowed as readonly string[]).includes(value) ? (value as T) : fallback;
  } catch {
    return fallback;             // storage blocked
  }
}

function keep(key: string, value: string) {
  try { localStorage.setItem(key, value); } catch { /* blocked: the next visit forgets it */ }
}

const system = window.matchMedia("(prefers-color-scheme: dark)");

// A theme saved before 0.18.8 may be "instagram", the old default: not in
// THEMES, so it falls back to the default that replaced it.
let theme = $state<Theme>(saved(THEME_KEY, THEMES, "hikidashi"));
let mode = $state<Mode>(saved(MODE_KEY, MODES, "system"));
let pixel = $state(saved(PIXEL_KEY, ["on", "off"], "off") === "on");
let systemDark = $state(system.matches);

system.addEventListener("change", (e) => { systemDark = e.matches; });

const scheme = $derived(mode === "system" ? (systemDark ? "dark" : "light") : mode);

$effect.root(() => {
  $effect(() => {
    const root = document.documentElement;
    root.dataset.theme = theme;
    root.dataset.scheme = scheme;
    if (pixel) root.dataset.pixel = "on";
    else delete root.dataset.pixel;
  });
});

/* A press is over before most animations are: :active lasts only while the
   pointer is down. A pressed button carries .is-pressed for long enough that
   a theme can play something whole on it (a flash, a burst). */
const PRESSABLE = ".btn, .upload, .srow";
const PRESS_MS = 600;

document.addEventListener("pointerdown", (e) => {
  const el = (e.target as Element | null)?.closest?.(PRESSABLE);
  if (!(el instanceof HTMLElement)) return;
  el.classList.remove("is-pressed");
  void el.offsetWidth;           // restart the animation on a quick second press
  el.classList.add("is-pressed");
  clearTimeout(Number(el.dataset.pressTimer));
  el.dataset.pressTimer = String(setTimeout(() => el.classList.remove("is-pressed"), PRESS_MS));
});

export const look = {
  get theme() { return theme; },
  set theme(value: Theme) { theme = value; keep(THEME_KEY, value); },
  get mode() { return mode; },
  set mode(value: Mode) { mode = value; keep(MODE_KEY, value); },
  get pixel() { return pixel; },
  set pixel(value: boolean) { pixel = value; keep(PIXEL_KEY, value ? "on" : "off"); },
  /** The mode as drawn: system resolved to light or dark. */
  get scheme() { return scheme; },
};
