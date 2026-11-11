import { getCollections, getItems, getPlaces, getRecipeFacets, getStats, getTags } from "./api";
import { apiParams, filterKey, mapParams, type Filters } from "./filters";
import type { Item, Named, PlacedItem, RecipeFacets, Stats, Tag } from "./types";

const PAGE = 60;

/* The grid's items for the current filters. Opening a reel does not touch
   this, so the grid, and how far down it has been scrolled, survive the
   detail sheet. */
class Library {
  items = $state.raw<Item[]>([]);
  total = $state(0);
  loading = $state(false);
  /** True until the first page for the current filters has arrived. */
  pending = $state(true);
  error = $state<string | null>(null);

  #key: string | null = null;
  #filters: Filters | null = null;
  #ctrl: AbortController | null = null;

  get done() { return this.items.length >= this.total; }

  /** Show these filters. A no-op if they are already showing. */
  show(filters: Filters, force = false) {
    const key = filterKey(filters);
    if (key === this.#key && !force) return;
    this.#key = key;
    this.#filters = filters;
    this.pending = true;
    void this.#load(0);
  }

  reload() {
    if (this.#filters) this.show(this.#filters, true);
  }

  /** Change one loaded item in place, after an edit made elsewhere. */
  patch(shortcode: string, change: Partial<Item>) {
    this.items = this.items.map((i) => (i.shortcode === shortcode ? { ...i, ...change } : i));
  }

  more() {
    if (this.loading || this.pending || this.done) return;
    void this.#load(this.items.length);
  }

  async #load(offset: number) {
    this.#ctrl?.abort();
    const ctrl = (this.#ctrl = new AbortController());
    this.loading = true;
    try {
      const page = await getItems(apiParams(this.#filters!, offset, PAGE), ctrl.signal);
      // Old items stay up until the new ones arrive, so the grid reflows from
      // one result to the next instead of flashing empty in between.
      this.items = offset ? [...this.items, ...page.items] : page.items;
      this.total = page.total;
      this.error = null;
      this.pending = false;
    } catch (err) {
      if (ctrl.signal.aborted) return;
      this.error = (err as Error).message;
      this.pending = false;
    } finally {
      if (this.#ctrl === ctrl) this.loading = false;
    }
  }
}

export const library = new Library();

/* Places' reels that are on the map, every one at once. Loaded alongside the
   grid while Places is showing, grid or map, so the Map toggle can say how
   many there are before it is pressed. */
class Pins {
  items = $state.raw<PlacedItem[]>([]);
  /** How many reels the filters chose, and how many of those name a place. */
  total = $state(0);
  named = $state(0);
  pending = $state(true);
  error = $state<string | null>(null);

  #key: string | null = null;
  #filters: Filters | null = null;
  #ctrl: AbortController | null = null;

  show(filters: Filters, force = false) {
    const key = filterKey(filters);
    if (key === this.#key && !force) return;
    this.#key = key;
    this.#filters = filters;
    this.pending = true;
    void this.#load();
  }

  reload() {
    if (this.#filters) this.show(this.#filters, true);
  }

  async #load() {
    this.#ctrl?.abort();
    const ctrl = (this.#ctrl = new AbortController());
    try {
      const page = await getPlaces(mapParams(this.#filters!), ctrl.signal);
      this.items = page.items;
      this.total = page.total;
      this.named = page.named;
      this.error = null;
    } catch (err) {
      if (ctrl.signal.aborted) return;
      this.error = (err as Error).message;
    }
    this.pending = false;
  }
}

export const pins = new Pins();

class Sidebar {
  stats = $state<Stats | null>(null);
  collections = $state.raw<Named[]>([]);
  tags = $state.raw<Tag[]>([]);
  /** What the Recipes view filters by. Loaded the first time it is shown. */
  facets = $state.raw<RecipeFacets | null>(null);
  error = $state<string | null>(null);

  async refresh() {
    try {
      [this.stats, this.collections, this.tags] =
        await Promise.all([getStats(), getCollections(), getTags()]);
      this.error = null;
    } catch (err) {
      this.error = (err as Error).message;
    }
    if (this.facets) await this.refreshFacets();
  }

  async refreshFacets() {
    try {
      this.facets = await getRecipeFacets();
    } catch (err) {
      this.error = (err as Error).message;
    }
  }
}

export const sidebar = new Sidebar();
