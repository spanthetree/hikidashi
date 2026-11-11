import { describe, expect, it } from "vitest";
import { paragraphs, readingTime } from "./transcript";

const sentence = (n: number) => `This is sentence number ${n}, and it goes on for a while longer.`;

describe("paragraphs", () => {
  it("changes no words", () => {
    const text = Array.from({ length: 30 }, (_, i) => sentence(i)).join(" ");
    expect(paragraphs(text).join(" ")).toBe(text);
  });

  it("groups sentences into paragraphs of a few hundred characters", () => {
    const text = Array.from({ length: 30 }, (_, i) => sentence(i)).join(" ");
    const paras = paragraphs(text);
    expect(paras.length).toBeGreaterThan(3);
    for (const p of paras.slice(0, -1)) expect(p.length).toBeGreaterThanOrEqual(320);
  });

  it("does not end a sentence at an abbreviation", () => {
    const text = `Ask Dr. Smith about it. ${"Then more words follow here. ".repeat(20)}`;
    expect(paragraphs(text)[0]).toMatch(/^Ask Dr\. Smith about it\./);
  });

  it("still breaks speech with no full stops", () => {
    const text = Array.from({ length: 80 }, (_, i) => `and then word${i} happened`).join(" ");
    expect(paragraphs(text).length).toBeGreaterThan(1);
  });
});

it("estimates reading time", () => {
  expect(readingTime("one two three")).toBe("3 words · 1 min read");
  expect(readingTime(Array(690).fill("w").join(" "))).toBe("690 words · 3 min read");
});
