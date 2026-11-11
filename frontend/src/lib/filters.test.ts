import { describe, expect, it } from "vitest";
import {
  activeFilters, apiParams, filterKey, filterUrl, mapParams, readFilters, toSearch, withFilter,
  withLayout, withPick, withQuery, withMedia, withTime, withView,
} from "./filters";

const read = (s: string) => readFilters(new URLSearchParams(s));

describe("filters in the URL", () => {
  it("round-trips, leaving defaults out", () => {
    expect(toSearch(read(""))).toBe("");
    expect(toSearch(read("sort=saved_desc"))).toBe("");
    expect(toSearch(read("q=pizza&sort=relevance&tag=Food"))).toBe("?q=pizza&sort=relevance&tag=Food");
  });

  it("ignores an unknown sort", () => {
    expect(read("sort=random").sort).toBe("saved_desc");
  });

  it("trims the query", () => {
    expect(read("q=%20pizza%20").q).toBe("pizza");
  });
});

describe("withFilter", () => {
  it("narrows what is already chosen: a reel must match every one", () => {
    let f = withFilter(read("collection=Travel&q=x"), "tag", "Gaming");
    f = withFilter(withFilter(f, "game", "Minecraft"), "hashtag", "build");
    f = withFilter(f, "hashtag", "server");
    expect(f).toMatchObject({ tag: "Gaming", game: "Minecraft", collection: "Travel",
                              hashtag: ["build", "server"], q: "x" });
    expect(activeFilters(f)).toEqual([
      { kind: "tag", value: "Gaming" }, { kind: "game", value: "Minecraft" },
      { kind: "collection", value: "Travel" },
      { kind: "hashtag", value: "build" }, { kind: "hashtag", value: "server" },
    ]);
    expect(toSearch(f)).toBe(
      "?q=x&tag=Gaming&game=Minecraft&collection=Travel&hashtag=build&hashtag=server");
  });

  it("swaps a tag, game or collection, which a reel has one of", () => {
    expect(withFilter(read("tag=Food&hashtag=nyc"), "tag", "Travel"))
      .toMatchObject({ tag: "Travel", hashtag: ["nyc"] });
    expect(withFilter(read("game=Doom"), "game", "Quake").game).toBe("Quake");
  });

  it("takes one off when chosen again, and leaves the rest", () => {
    expect(activeFilters(withFilter(read("tag=Food"), "tag", "Food"))).toEqual([]);
    expect(withFilter(read("hashtag=a&hashtag=b&tag=Food"), "hashtag", "a"))
      .toMatchObject({ hashtag: ["b"], tag: "Food" });
  });

  it("reads each hashtag once", () => {
    expect(read("hashtag=a&hashtag=a&hashtag=%20").hashtag).toEqual(["a"]);
  });
});

describe("withMedia", () => {
  it("narrows alongside a collection or recipe filters", () => {
    const f = withMedia(read("view=recipes&collection=Food&has=egg"), "video");
    expect(toSearch(f)).toBe("?view=recipes&collection=Food&media=video&has=egg");
    expect(apiParams(f, 0, 60).get("media")).toBe("video");
  });

  it("switches, and toggles off when chosen again", () => {
    expect(withMedia(read("media=video"), "image").media).toBe("image");
    expect(withMedia(read("media=image"), "image").media).toBe("");
  });

  it("ignores an unknown kind", () => {
    expect(read("media=gif").media).toBe("");
  });
});

describe("filterUrl", () => {
  it("links a chip to the library narrowed by it, keeping search, sort and the rest", () => {
    expect(filterUrl(read("q=pizza&sort=author&hashtag=nyc"), "tag", "Food"))
      .toBe("/?q=pizza&sort=author&tag=Food&hashtag=nyc");
  });

  it("links the active chip back to the unfiltered library", () => {
    expect(filterUrl(read("collection=Travel"), "collection", "Travel")).toBe("/");
  });

  it("escapes names", () => {
    expect(filterUrl(read(""), "tag", "new york & co")).toBe("/?tag=new+york+%26+co");
  });
});

describe("withQuery", () => {
  it("ranks a new search by match", () => {
    expect(withQuery(read(""), "pizza").sort).toBe("relevance");
  });

  it("keeps an order you picked", () => {
    expect(withQuery(read("sort=author"), "pizza").sort).toBe("author");
  });

  it("goes back to newest when the search is cleared", () => {
    expect(withQuery(read("q=pizza&sort=relevance"), "").sort).toBe("saved_desc");
  });
});

it("builds the API query", () => {
  const p = apiParams(read("q=pizza&hashtag=food"), 60, 60);
  expect(Object.fromEntries(p)).toEqual({
    sort: "saved_desc", limit: "60", offset: "60", q: "pizza", hashtag: "food",
  });
});

describe("the Recipes view", () => {
  it("round-trips its filters, repeated params and all", () => {
    const s = "view=recipes&has=chicken&has=garlic&without=Dairy&time=30";
    const f = read(s);
    expect(f).toMatchObject({ view: "recipes", has: ["chicken", "garlic"], without: ["Dairy"], time: 30 });
    expect(toSearch(f)).toBe(`?${s}`);
  });

  it("ignores recipe filters outside it, and an unknown time", () => {
    expect(read("has=chicken&time=30")).toMatchObject({ view: "library", has: [], time: 0 });
    expect(read("view=recipes&time=45").time).toBe(0);
  });

  it("intersects picks, moving a value between has and without", () => {
    let f = withPick(read("view=recipes"), "has", "chicken");
    f = withPick(f, "without", "Dairy");
    expect(f).toMatchObject({ has: ["chicken"], without: ["Dairy"] });
    f = withPick(f, "without", "chicken");
    expect(f).toMatchObject({ has: [], without: ["Dairy", "chicken"] });
    expect(withPick(f, "without", "Dairy").without).toEqual(["chicken"]);
  });

  it("toggles a time", () => {
    expect(withTime(read("view=recipes"), 15).time).toBe(15);
    expect(withTime(read("view=recipes&time=15"), 15).time).toBe(0);
  });

  it("keeps the search and collection across views, and drops recipe filters on leaving", () => {
    const f = withView(read("view=recipes&q=noodles&collection=Food&has=egg"), "library");
    expect(f).toMatchObject({ view: "library", q: "noodles", collection: "Food", has: [] });
    expect(toSearch(withView(read("q=x"), "recipes"))).toBe("?view=recipes&q=x");
  });

  it("asks the API for recipes only", () => {
    const p = apiParams(read("view=recipes&has=egg&without=Meat&time=60"), 0, 60);
    expect(p.get("recipes")).toBe("true");
    expect(p.getAll("has")).toEqual(["egg"]);
    expect(p.getAll("without")).toEqual(["Meat"]);
    expect(p.get("max_time")).toBe("60");
    expect(apiParams(read(""), 0, 60).has("recipes")).toBe(false);
  });
});

describe("the Places view", () => {
  it("round-trips the map and where it looks", () => {
    const s = "view=places&layout=map&q=ramen&at=34.69,135.5,12";
    expect(read(s)).toMatchObject({ view: "places", layout: "map", at: "34.69,135.5,12" });
    expect(toSearch(read(s))).toBe(`?${s}`);
  });

  it("has no map outside it, and no position without the map", () => {
    expect(read("layout=map&at=1,2,3")).toMatchObject({ view: "library", layout: "grid", at: "" });
    expect(read("view=places&at=1,2,3").at).toBe("");
    expect(read("view=places&layout=map&at=javascript:1").at).toBe("");
  });

  it("switches layout, forgetting the map's position for the grid", () => {
    const map = withLayout(read("view=places&q=x"), "map");
    expect(toSearch(map)).toBe("?view=places&layout=map&q=x");
    expect(toSearch(withLayout(read("view=places&layout=map&at=1,2,3"), "grid")))
      .toBe("?view=places");
  });

  it("drops the map on leaving Places", () => {
    const f = withView(read("view=places&layout=map&at=1,2,3&tag=Food"), "recipes");
    expect(toSearch(f)).toBe("?view=recipes&tag=Food");
  });

  it("reloads nothing when only the drawing or the map moves", () => {
    expect(filterKey(read("view=places&layout=map&at=1,2,3"))).toBe(filterKey(read("view=places")));
    expect(filterKey(read("view=places&tag=Food"))).not.toBe(filterKey(read("view=places")));
  });

  it("asks the API for places only, every pin at once", () => {
    const p = mapParams(read("view=places&layout=map&media=video"));
    expect(Object.fromEntries(p)).toEqual({ sort: "saved_desc", media: "video", places: "true" });
    expect(apiParams(read(""), 0, 60).has("places")).toBe(false);
  });
});
