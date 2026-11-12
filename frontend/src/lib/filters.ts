import type { FilterKind, Layout, Media, PickKind, Sort, View } from "./types";

/* Search, filters and sort live in the query string, so any view of the
   library can be bookmarked or shared, and Back walks through them. */

export interface Filters {
  q: string;
  sort: Sort;
  /* Everything chosen narrows: a reel must match every one (#117). A reel is
     in one collection here at a time, has one tag and shows one game, so
     those hold one value each; it has many hashtags, so those add up. */
  collection: string;
  tag: string;
  game: string;
  hashtag: string[];
  /** Only videos or only images. Narrows any view, alongside the rest. */
  media: Media;
  /** "recipes" shows only reels with a recipe, and the recipe filters below;
      "places" only reels whose caption names a place. */
  view: View;
  /** Places only: the reels as a grid, or their places on a map. */
  layout: Layout;
  /** The map only: where it looks, as "lat,lng,zoom"; "" to fit the pins. */
  at: string;
  /** Ingredients or groups ("Dairy") a recipe must have, or must not. */
  has: string[];
  without: string[];
  /** Ready in at most this many minutes; 0 for any time. */
  time: number;
}

export const DEFAULT_SORT: Sort = "saved_desc";
const SORTS: Sort[] = ["relevance", "saved_desc", "saved_asc", "author"];
export const KINDS: FilterKind[] = ["tag", "game", "collection", "hashtag"];
export const TIMES = [15, 30, 60];
export const MEDIA: Exclude<Media, "">[] = ["video", "image"];

const VIEWS: View[] = ["library", "recipes", "places"];
const AT = /^-?\d{1,2}(\.\d+)?,-?\d{1,3}(\.\d+)?,\d{1,2}$/;

// Read-only: page.url is a read-only URL since SvelteKit 3.
export function readFilters(params: Pick<URLSearchParams, "get" | "getAll">): Filters {
  const sort = params.get("sort") as Sort | null;
  const asked = params.get("view") as View | null;
  const view: View = asked && VIEWS.includes(asked) ? asked : "library";
  const map = view === "places" && params.get("layout") === "map";
  const at = params.get("at") ?? "";
  // The recipe filters mean nothing outside the Recipes view, so a stray one
  // in a hand-edited URL is ignored rather than emptying the library.
  const recipes = view === "recipes";
  const time = Number(params.get("time"));
  const media = params.get("media") as Media | null;
  return {
    q: params.get("q")?.trim() ?? "",
    sort: sort && SORTS.includes(sort) ? sort : DEFAULT_SORT,
    collection: params.get("collection") ?? "",
    tag: params.get("tag") ?? "",
    game: params.get("game") ?? "",
    hashtag: unique(params.getAll("hashtag")),
    media: media && MEDIA.includes(media) ? media : "",
    view,
    layout: map ? "map" : "grid",
    at: map && AT.test(at) ? at : "",
    has: recipes ? unique(params.getAll("has")) : [],
    without: recipes ? unique(params.getAll("without")) : [],
    time: recipes && TIMES.includes(time) ? time : 0,
  };
}

const unique = (xs: string[]) => [...new Set(xs.map((x) => x.trim()).filter(Boolean))];

/** "?q=…&tag=…", or "" for the plain library. Defaults are left out. */
export function toSearch(f: Filters): string {
  const p = new URLSearchParams();
  if (f.view !== "library") p.set("view", f.view);
  if (f.layout === "map") p.set("layout", "map");
  if (f.q) p.set("q", f.q);
  if (f.sort !== DEFAULT_SORT) p.set("sort", f.sort);
  setLabels(p, f);
  if (f.media) p.set("media", f.media);
  for (const v of f.has) p.append("has", v);
  for (const v of f.without) p.append("without", v);
  if (f.time) p.set("time", String(f.time));
  // Added by hand so its commas stay commas: readFilters lets through
  // nothing but digits, dots and minus signs between them.
  const s = [p.toString(), f.at && `at=${f.at}`].filter(Boolean).join("&");
  return s ? `?${s}` : "";
}

/** The part of the filters that decides which items show: not how they are
    drawn, or where the map looks. */
export const filterKey = (f: Filters) => toSearch({ ...f, layout: "grid", at: "" });

function setLabels(p: URLSearchParams, f: Filters) {
  for (const kind of KINDS) for (const v of values(f, kind)) p.append(kind, v);
}

/** One kind's chosen values: none, one, or for hashtags any number. */
export function values(f: Filters, kind: FilterKind): string[] {
  const v = f[kind];
  return Array.isArray(v) ? v : v ? [v] : [];
}

/** Whether a value is among those chosen. */
export const isOn = (f: Filters, kind: FilterKind, value: string) =>
  values(f, kind).includes(value);

/** Every tag, game, collection and hashtag chosen, in that order. */
export function activeFilters(f: Filters): { kind: FilterKind; value: string }[] {
  return KINDS.flatMap((kind) => values(f, kind).map((value) => ({ kind, value })));
}

/** Any recipe filter is on. */
export const recipeFiltered = (f: Filters) =>
  Boolean(f.has.length || f.without.length || f.time);

/* Choosing narrows what is already chosen; choosing it again takes it off.
   A second tag, game or collection takes the place of the first, since no
   reel could match both; hashtags add up. */
export function withFilter(f: Filters, kind: FilterKind, value: string): Filters {
  if (kind === "hashtag") {
    const on = f.hashtag.includes(value);
    return { ...f, hashtag: on ? f.hashtag.filter((h) => h !== value) : [...f.hashtag, value] };
  }
  return { ...f, [kind]: f[kind] === value ? "" : value };
}

/** Nothing chosen: no tag, game, collection or hashtag. */
export const withoutFilters = (f: Filters): Filters =>
  ({ ...f, collection: "", tag: "", game: "", hashtag: [] });

/** Where choosing that filter goes: the library, with the rest kept. */
export const filterUrl = (f: Filters, kind: FilterKind, value: string) =>
  `/${toSearch(withFilter(f, kind, value))}`;

/* Switching view keeps the search and the collection or tag. Leaving Recipes
   drops its filters, and leaving Places its map, which the others have no
   use for. */
export function withView(f: Filters, view: View): Filters {
  const next = { ...f, view, layout: "grid" as Layout, at: "" };
  if (view === "places") Object.assign(next, { layout: f.layout, at: f.at });
  return view === "recipes" ? next : { ...next, has: [], without: [], time: 0 };
}

/* Grid or map, within Places. The map's position is the map's alone. */
export function withLayout(f: Filters, layout: Layout): Filters {
  return { ...f, view: "places", layout, at: layout === "map" ? f.at : "" };
}

/* Recipe filters do intersect: "has chicken, without dairy" is the point.
   An ingredient is in one list at most, so picking it for the other moves it;
   picking it again where it already is takes it off. */
export function withPick(f: Filters, kind: PickKind, value: string): Filters {
  const other: PickKind = kind === "has" ? "without" : "has";
  const on = f[kind].includes(value);
  return {
    ...f,
    view: "recipes",
    [kind]: on ? f[kind].filter((v) => v !== value) : [...f[kind], value],
    [other]: f[other].filter((v) => v !== value),
  };
}

/* Videos or images is its own axis: it narrows a collection, tag or recipe
   search rather than replacing it. Choosing the active one again clears it. */
export function withMedia(f: Filters, media: Media): Filters {
  return { ...f, media: f.media === media ? "" : media };
}

export function withTime(f: Filters, minutes: number): Filters {
  return { ...f, view: "recipes", time: f.time === minutes ? 0 : minutes };
}

/* A search is ranked by match until you pick another order; clearing it goes
   back to newest, since "best match" for nothing means nothing. */
export function withQuery(f: Filters, q: string): Filters {
  let sort = f.sort;
  if (q && !f.q && sort === DEFAULT_SORT) sort = "relevance";
  if (!q && sort === "relevance") sort = DEFAULT_SORT;
  return { ...f, q, sort };
}

export function apiParams(f: Filters, offset: number, limit: number): URLSearchParams {
  const p = new URLSearchParams({ sort: f.sort, limit: String(limit), offset: String(offset) });
  if (f.q) p.set("q", f.q);
  setLabels(p, f);
  if (f.media) p.set("media", f.media);
  if (f.view === "recipes") {
    p.set("recipes", "true");
    for (const v of f.has) p.append("has", v);
    for (const v of f.without) p.append("without", v);
    if (f.time) p.set("max_time", String(f.time));
  }
  if (f.view === "places") p.set("places", "true");
  return p;
}

/** The map's query: every pin at once, in the grid's order. */
export function mapParams(f: Filters): URLSearchParams {
  const p = apiParams(f, 0, 1);
  p.delete("limit");
  p.delete("offset");
  return p;
}
