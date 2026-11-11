import { describe, expect, it } from "vitest";
import { segments } from "./caption";

const place = { text: "8800 Preston Rd, Frisco", url: "https://maps.example/?q=preston" };

describe("segments", () => {
  it("leaves plain text alone", () => {
    expect(segments("Homemade pizza 101", [])).toEqual([{ kind: "text", text: "Homemade pizza 101" }]);
  });

  it("links a place every time it appears", () => {
    const got = segments(`At ${place.text}. Again: ${place.text}`, [place]);
    expect(got.filter((s) => s.kind === "place")).toHaveLength(2);
    expect(got.map((s) => s.text).join("")).toBe(`At ${place.text}. Again: ${place.text}`);
  });

  it("links mentions to the profile", () => {
    expect(segments("Where: @examplegrill // here", [])).toEqual([
      { kind: "text", text: "Where: " },
      { kind: "mention", text: "@examplegrill", url: "https://www.instagram.com/examplegrill/" },
      { kind: "text", text: " // here" },
    ]);
  });

  it("keeps a sentence's full stop out of the handle", () => {
    const got = segments("Thanks @some.chef.", []);
    expect(got[1]).toMatchObject({ kind: "mention", text: "@some.chef" });
    expect(got[2]).toEqual({ kind: "text", text: "." });
  });

  it("does not take emails for mentions", () => {
    expect(segments("mail me@example.com", [])).toEqual([{ kind: "text", text: "mail me@example.com" }]);
  });

  it("does not look for mentions inside a place link", () => {
    const at = { text: "Pizza @ the Pier", url: "https://maps.example/@35.1,-80.2" };
    const got = segments("Try Pizza @ the Pier with @chef", [at]);
    expect(got.map((s) => s.kind)).toEqual(["text", "place", "text", "mention"]);
  });

  it("never produces markup, only text", () => {
    const got = segments('<img src=x onerror="alert(1)"> @a', []);
    expect(got[0]).toEqual({ kind: "text", text: '<img src=x onerror="alert(1)"> ' });
  });
});
