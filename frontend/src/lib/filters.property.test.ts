import fc from "fast-check";
import { describe, expect, it } from "vitest";
import { readFilters, toSearch } from "./filters";

/* Properties of the filter URLs for any query string, not only the ones the
   example tests thought of (#39). */

const KEYS = ["q", "sort", "collection", "tag", "game", "hashtag", "media", "view", "layout", "at",
              "has", "without", "time", "other"];
const VALUES = fc.oneof(
  fc.string(),
  fc.constantFrom("", " ", "relevance", "author", "saved_asc", "video", "image", "recipes",
                  "places", "map", "grid", "15", "30", "45", "Dairy", "35.6,139.7,12",
                  "-90,180,3", "a,b,c", "日本", "#tag", "a&b=c", "%", "+"),
);
const QUERY = fc.array(fc.tuple(fc.constantFrom(...KEYS), VALUES), { maxLength: 12 })
  .map((pairs) => new URLSearchParams(pairs));

const read = (search: string) => readFilters(new URLSearchParams(search));

describe("filter URLs, for any query string", () => {
  it("read, written and read again, come back the same", () => {
    fc.assert(fc.property(QUERY, (params) => {
      const once = readFilters(params);
      expect(read(toSearch(once))).toEqual(once);
    }));
  });

  it("are written the same way every time", () => {
    fc.assert(fc.property(QUERY, (params) => {
      const search = toSearch(readFilters(params));
      expect(toSearch(read(search))).toBe(search);
    }));
  });

  it("never write the defaults", () => {
    fc.assert(fc.property(QUERY, (params) => {
      const search = toSearch(readFilters(params));
      for (const plain of ["view=library", "sort=saved_desc", "layout=grid", "time=0"]) {
        expect(search).not.toContain(plain);
      }
    }));
  });
});
