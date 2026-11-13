import { describe, expect, it } from "vitest";
import { sharedText } from "./share";

describe("sharedText", () => {
  it("takes the link from wherever the app put it", () => {
    expect(sharedText(new URLSearchParams("url=https://instagram.com/reel/A1/"))).toBe(
      "https://instagram.com/reel/A1/");
    expect(sharedText(new URLSearchParams({ text: "Look https://instagram.com/reel/A1/" })))
      .toBe("Look https://instagram.com/reel/A1/");
    expect(sharedText(new URLSearchParams({ title: "x", url: "https://y" }))).toBe("https://y x");
  });

  it("is empty when nothing came", () => {
    expect(sharedText(new URLSearchParams("url=&text=%20"))).toBe("");
  });
});
