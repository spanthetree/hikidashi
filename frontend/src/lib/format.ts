import { locale } from "./i18n";
import { m } from "./paraglide/messages";

/* Numbers and dates in the language chosen in the app, not the browser's.
   Switching language reloads the page, so these are made once. */

export const nf = new Intl.NumberFormat(locale);

const day = new Intl.DateTimeFormat(locale, { year: "numeric", month: "short", day: "numeric" });

export function savedDate(ts: number | null): string {
  if (!ts) return m.date_unknown();
  return day.format(new Date(ts * 1000));
}

const mb = new Intl.NumberFormat(locale, {
  style: "unit", unit: "megabyte", minimumFractionDigits: 1, maximumFractionDigits: 1,
});

export const megabytes = (bytes: number) => mb.format(bytes / 1048576);

const kb = new Intl.NumberFormat(locale, { style: "unit", unit: "kilobyte", maximumFractionDigits: 0 });

/** A file's size: kB under a megabyte, where "0.0 MB" would say nothing. */
export const fileSize = (bytes: number) =>
  bytes < 1048576 ? kb.format(Math.max(1, Math.round(bytes / 1024))) : megabytes(bytes);

/** "the caption and the voiceover": a list, joined the way the language joins one. */
export const list = (items: string[]) =>
  new Intl.ListFormat(locale, { type: "conjunction" }).format(items);

const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });

/** "12 seconds ago", "in 3 minutes": `ts` (unix seconds) from `now`. */
export function ago(ts: number, now: number): string {
  const s = ts - now, a = Math.abs(s);
  if (a < 90) return rtf.format(Math.round(s), "second");
  if (a < 90 * 60) return rtf.format(Math.round(s / 60), "minute");
  if (a < 36 * 3600) return rtf.format(Math.round(s / 3600), "hour");
  return rtf.format(Math.round(s / 86400), "day");
}

export function duration(secs: number | null): string {
  if (!secs) return "";
  const min = Math.floor(secs / 60), s = String(secs % 60).padStart(2, "0");
  return `${min}:${s}`;
}

/* A stable colour per shortcode. Shown until a thumbnail has loaded, and as
   the backdrop behind one that fails to. */
export function tint(shortcode: string): string {
  let h = 0;
  for (const ch of shortcode) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return `linear-gradient(145deg, hsl(${h} 32% 52%), hsl(${(h + 40) % 360} 38% 26%))`;
}

export const profileUrl = (handle: string) =>
  `https://www.instagram.com/${encodeURIComponent(handle)}/`;
