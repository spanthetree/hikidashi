import type { Aisle, ShoppingLine } from "./types";

/* The shopping list's lines, grouped for showing and written out for
   sharing. The server merges and sorts them (app/recipes/shopping.py). */

export const AISLES: Aisle[] = [
  "produce", "meat", "dairy", "bakery", "pantry", "spices", "condiments", "frozen", "drinks",
  "other",
];

/** Lines by aisle, in aisle order, with the pantry staples kept apart. */
export function byAisle(lines: ShoppingLine[]) {
  const groups = AISLES.map((aisle) => ({
    aisle, lines: lines.filter((l) => l.aisle === aisle && !l.staple),
  })).filter((g) => g.lines.length);
  return { groups, staples: lines.filter((l) => l.staple) };
}

/** "onion · 3", "butter · 150 g + 2 tbsp", or the name alone. */
export const lineText = (l: ShoppingLine) =>
  l.amounts.length ? `${l.name} · ${l.amounts.join(" + ")}` : l.name;

/** What is still to buy, as plain text to paste into a message or a notes
    app: a heading per aisle, and staples last, since you may have them. */
export function asText(title: string, lines: ShoppingLine[], label: (a: Aisle) => string,
                       staplesLabel: string): string {
  const left = lines.filter((l) => !l.ticked);
  const { groups, staples } = byAisle(left);
  const blocks = groups.map((g) => [label(g.aisle), ...g.lines.map((l) => `- ${lineText(l)}`)]);
  if (staples.length) blocks.push([staplesLabel, ...staples.map((l) => `- ${lineText(l)}`)]);
  return [title, ...blocks.map((b) => b.join("\n"))].join("\n\n");
}
