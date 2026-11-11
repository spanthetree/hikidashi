import { nf } from "./format";
import { m } from "./paraglide/messages";

/* Whisper's segments arrive joined into one line. Regrouping its sentences
   into short paragraphs is enough to make that readable; no words change. */

const SENTENCE_END = /(?<=[.!?…]["')\]]?)\s+(?=["'(¿¡]?\p{Lu})/u;
const NOT_AN_END = /(?:\b(?:[A-Z]\.){2,}|\b(?:Mr|Mrs|Ms|Dr|St|vs|No)\.)$/;
// Speech with no full stops at all still breaks, at a comma or a conjunction.
const CLAUSE_START = /(?<=,)\s+|\s+(?=(?:and|so|then|but)\s)/;
const PARA_CHARS = 320;

export function paragraphs(text: string): string[] {
  const sentences: string[] = [];
  for (const piece of text.trim().split(SENTENCE_END)) {
    const last = sentences.length - 1;
    if (last >= 0 && NOT_AN_END.test(sentences[last])) sentences[last] += ` ${piece}`;
    else sentences.push(piece);
  }
  const paras: string[] = [];
  let cur = "";
  const clauses = sentences.flatMap((s) =>
    s.length > 2 * PARA_CHARS ? s.split(CLAUSE_START) : [s]);
  for (const s of clauses) {
    cur = cur ? `${cur} ${s}` : s;
    if (cur.length >= PARA_CHARS) { paras.push(cur); cur = ""; }
  }
  if (cur) paras.push(cur);
  return paras;
}

/** "1,234 words · 5 min read", at an unhurried 230 words a minute. */
export function readingTime(text: string): string {
  const words = text.trim().split(/\s+/).length;
  const minutes = Math.max(1, Math.round(words / 230));
  return m.reading_time({ words: nf.format(words), minutes: nf.format(minutes) });
}
