import { m } from "#lib/paraglide/messages.js";

/* Copying to the clipboard, wherever the app is opened (#107).

   `navigator.clipboard` exists only in a secure context: HTTPS, or
   http://localhost. A NAS install is usually opened over plain HTTP on the
   LAN (http://192.168.1.20:8080), where it is undefined. There the old
   `document.execCommand("copy")` still works from a click, deprecated as it
   is, so it is the fallback. When both fail, the caller says so: a copy
   button must never fail silently. */

/** How a copy went. "selected": it failed, but the text is selected in the
 *  given field, for Ctrl+C / ⌘C. */
export type Copied = "copied" | "selected" | "failed";

type Field = HTMLInputElement | HTMLTextAreaElement;

export async function copyText(text: string, field?: Field | null): Promise<Copied> {
  if (globalThis.isSecureContext && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text);
      return "copied";
    } catch {
      // Refused (a permission, or focus lost): try the old way.
    }
  }
  if (legacyCopy(text)) return "copied";
  if (field) {
    field.focus();
    field.select();
    return "selected";
  }
  return "failed";
}

/* Selects the text in an off-screen textarea and asks the browser to copy
   the selection, then puts the focus back where it was. */
function legacyCopy(text: string): boolean {
  if (typeof document === "undefined" || !document.body) return false;
  const was = document.activeElement as HTMLElement | null;
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.top = "0";
  area.style.left = "-9999px";
  area.style.opacity = "0";
  document.body.appendChild(area);
  let ok = false;
  try {
    area.select();
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  } finally {
    area.remove();
    was?.focus?.();
  }
  return ok;
}

/** What to tell someone a copy didn't work: whether the text is selected
 *  for them, and, off HTTPS, that HTTPS would let the button work. */
export function copyFailure(how: Exclude<Copied, "copied">): string {
  const said = how === "selected" ? m.copy_selected() : m.copy_failed();
  return globalThis.isSecureContext ? said : `${said} ${m.copy_https()}`;
}
