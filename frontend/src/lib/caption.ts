import { profileUrl } from "./format";
import type { Place } from "./types";

export type Segment =
  | { kind: "text"; text: string }
  | { kind: "place"; text: string; url: string }
  | { kind: "mention"; text: string; url: string };

/* Split a caption into plain text, place links and @mention links. The caption
   is never parsed as markup: each segment is rendered as text by Svelte, so a
   caption cannot inject anything. */
export function segments(caption: string, places: Pick<Place, "text" | "url">[]): Segment[] {
  let parts: Segment[] = [{ kind: "text", text: caption }];

  for (const place of places) {
    if (!place.text) continue;
    parts = parts.flatMap((part) => {
      if (part.kind !== "text" || !part.text.includes(place.text)) return [part];
      const out: Segment[] = [];
      part.text.split(place.text).forEach((chunk, i) => {
        if (i) out.push({ kind: "place", text: place.text, url: place.url });
        if (chunk) out.push({ kind: "text", text: chunk });
      });
      return out;
    });
  }

  // Places are already links, and map URLs carry "@lat,lng", so mentions are
  // only looked for in plain text. The lookbehind keeps emails like a@b.com
  // from matching.
  return parts.flatMap((part) => (part.kind === "text" ? mentions(part.text) : [part]));
}

const MENTION = /(?<![\w.@/])@([A-Za-z0-9._]{1,30})/g;

function mentions(text: string): Segment[] {
  const out: Segment[] = [];
  let last = 0;
  for (const m of text.matchAll(MENTION)) {
    // A handle never ends in a dot; that is the end of the sentence.
    const handle = m[1].replace(/\.+$/, "");
    if (!handle) continue;
    const start = m.index!;
    if (start > last) out.push({ kind: "text", text: text.slice(last, start) });
    out.push({ kind: "mention", text: `@${handle}`, url: profileUrl(handle) });
    last = start + 1 + handle.length;
  }
  if (last < text.length) out.push({ kind: "text", text: text.slice(last) });
  return out;
}
