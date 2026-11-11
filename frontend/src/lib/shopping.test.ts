import { describe, expect, it } from "vitest";
import { asText, byAisle, lineText } from "./shopping";
import type { Aisle, ShoppingLine } from "./types";

const line = (name: string, aisle: Aisle, amounts: string[] = [], more: Partial<ShoppingLine> = {}) =>
  ({ key: name, name, aisle, staple: false, amounts, recipes: ["A"], ticked: false, ...more });

const lines = [
  line("onion", "produce", ["3"]),
  line("butter", "dairy", ["150 g", "2 tbsp"]),
  line("salt", "spices", ["to taste"], { staple: true }),
  line("garlic", "produce", [], { ticked: true }),
];

describe("byAisle", () => {
  it("groups by aisle in aisle order, staples apart", () => {
    const { groups, staples } = byAisle(lines);
    expect(groups.map((g) => [g.aisle, g.lines.map((l) => l.name)])).toEqual([
      ["produce", ["onion", "garlic"]], ["dairy", ["butter"]],
    ]);
    expect(staples.map((l) => l.name)).toEqual(["salt"]);
  });
});

describe("asText", () => {
  it("writes out what is left to buy", () => {
    const label = (a: Aisle) => a.toUpperCase();
    expect(asText("List", lines, label, "STAPLES")).toBe(
      "List\n\nPRODUCE\n- onion · 3\n\nDAIRY\n- butter · 150 g + 2 tbsp\n\nSTAPLES\n- salt · to taste");
  });

  it("names a line with no amount alone", () => {
    expect(lineText(line("parmesan", "dairy"))).toBe("parmesan");
  });
});
