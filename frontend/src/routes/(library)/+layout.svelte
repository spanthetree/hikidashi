<script lang="ts">
  import { flip } from "svelte/animate";
  import { onMount, tick } from "svelte";
  import { SvelteSet } from "svelte/reactivity";
  import { afterNavigate, goto } from "$app/navigation";
  import { page } from "$app/state";
  import Card from "#lib/components/Card.svelte";
  import DetailSheet from "#lib/components/DetailSheet.svelte";
  import ExportMenu from "#lib/components/ExportMenu.svelte";
  import Icon from "#lib/components/Icon.svelte";
  import ImportBox from "#lib/components/ImportBox.svelte";
  import ImportDialog from "#lib/components/ImportDialog.svelte";
  import MapView from "#lib/components/MapView.svelte";
  import Notice from "#lib/components/Notice.svelte";
  import Rediscover from "#lib/components/Rediscover.svelte";
  import Rich from "#lib/components/Rich.svelte";
  import Sidebar from "#lib/components/Sidebar.svelte";
  import Topbar from "#lib/components/Topbar.svelte";
  import { addMatchingToList, addToList, exportUrl, getItems } from "#lib/api.js";
  import {
    activeFilters, apiParams, filterUrl, mapParams, readFilters, recipeFiltered, toSearch, withLayout, withPick,
    withQuery, withMedia, withoutFilters, withTime, withView, type Filters,
  } from "#lib/filters.js";
  import { nf } from "#lib/format.js";
  import { groupLabel, tagLabel } from "#lib/i18n.js";
  import { draggingNonZip, hasFiles, imports, type Upload } from "#lib/imports.svelte.js";
  import { library, pins, sidebar } from "#lib/library.svelte.js";
  import { cardIn, flipDuration } from "#lib/motion.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { FilterKind, Item, Layout, Media, NoticeState, PickKind, Sort, View } from "#lib/types.js";

  let { children } = $props();

  const filters = $derived(readFilters(page.url.searchParams));
  const search = $derived(toSearch(filters));
  const active = $derived(activeFilters(filters));
  const chosen = $derived(active.length > 0);
  const recipes = $derived(filters.view === "recipes");
  const places = $derived(filters.view === "places");
  const mapped = $derived(filters.layout === "map");
  const shortcode = $derived(page.params.shortcode ?? null);
  const preview = $derived(
    shortcode ? library.items.find((i) => i.shortcode === shortcode) : undefined,
  );

  let notice = $state<NoticeState>(null);
  let drawer = $state(false);

  $effect(() => { library.show(filters); });
  $effect(() => { if (places) pins.show(filters); });
  onMount(() => { void sidebar.refresh(); });

  const apiDown = $derived(library.error ?? sidebar.error ?? (places ? pins.error : null));
  $effect(() => {
    if (apiDown) notice = { text: m.notice_api_down({ error: apiDown }), kind: "error" };
  });

  /* ---------- navigation ---------- */

  const libraryUrl = (f: Filters) => `/${toSearch(f)}`;

  /* Filtering keeps focus where it is (the search box, a sidebar button):
     reset: false. A new set of results starts at the top unless `stay`. */
  function go(url: string, { stay = false, replace = false } = {}) {
    return goto(url, { reset: false, replace }).then(() => {
      if (!stay) window.scrollTo(0, 0);
    });
  }

  function setFilter(kind: FilterKind, value: string) {
    drawer = false;
    void go(filterUrl(filters, kind, value));
  }

  function clearFilters() {
    drawer = false;
    void go(libraryUrl(withView({ ...withoutFilters(filters), media: "" }, "library")));
  }

  function setView(view: View) {
    drawer = false;
    void go(libraryUrl(withView(filters, view)));
  }

  function setLayout(layout: Layout) {
    void go(libraryUrl(withLayout(filters, layout)), { stay: true });
  }

  /* Panning the map replaces its history entry: Back leaves the map rather
     than walking back through every drag. */
  function mapMoved(at: string) {
    void go(libraryUrl({ ...filters, at }), { stay: true, replace: true });
  }

  function setMedia(media: Media) {
    drawer = false;
    void go(libraryUrl(withMedia(filters, media)));
  }

  // Several recipe filters stack, so the drawer stays open while you pick.
  function pick(kind: PickKind, value: string) {
    void go(libraryUrl(withPick(filters, kind, value)), { stay: true });
  }

  function setTime(minutes: number) {
    void go(libraryUrl(withTime(filters, minutes)), { stay: true });
  }

  function setSort(e: Event) {
    const sort = (e.currentTarget as HTMLSelectElement).value as Sort;
    void go(libraryUrl({ ...filters, sort }), { stay: true });
  }

  /* Typing searches as you go. Refining a search replaces its history entry,
     so Back goes to before the search, not through every keystroke. */
  let q = $state("");
  let sent = "";
  let debounce: ReturnType<typeof setTimeout> | undefined;

  $effect(() => {
    // Back and Forward change the URL under the box; follow them. A value we
    // sent ourselves is already in the box, possibly with more typed since.
    const fromUrl = filters.q;
    if (fromUrl !== sent) q = sent = fromUrl;
  });

  function typed() {
    clearTimeout(debounce);
    debounce = setTimeout(() => {
      const next = q.trim();
      if (next === filters.q) return;
      sent = next;
      void go(libraryUrl(withQuery(filters, next)), { replace: !!filters.q });
    }, 220);
  }

  /* A reel opened from the grid closes with Back, so the history reads as
     you would expect. One opened from a shared link has no grid entry behind
     it, so closing replaces it with the library. */
  let openedFromGrid = false;
  afterNavigate(({ from, to, type }) => {
    // Only when the reel changes, not when its URL does.
    if (to?.params?.shortcode && to.params.shortcode !== from?.params?.shortcode) {
      openedFromGrid = type !== "enter" && Boolean(from) && !from?.params?.shortcode;
    }
  });

  function close() {
    if (openedFromGrid) history.back();
    else void go(libraryUrl(filters), { stay: true, replace: true });
  }

  /* A search hit whose voiceover matched opens at that moment (#44). */
  function itemHref(item: Item) {
    const s = new URLSearchParams(search);
    if (item.match?.at_ms != null) s.set("t", (item.match.at_ms / 1000).toFixed(1));
    const q = s.toString();
    return `/item/${encodeURIComponent(item.shortcode)}${q ? `?${q}` : ""}`;
  }

  /* Shuffle (#50): a random reel from whatever the filters show. "Another"
     in its sheet replaces it, so Back still leaves the shuffle in one step. */
  const shuffling = $derived(page.url.searchParams.has("shuffle"));
  async function shuffle() {
    const p = apiParams(filters, 0, 1);
    p.set("sort", "random");
    try {
      const [item] = (await getItems(p)).items;
      if (!item) return;
      const s = new URLSearchParams(search);
      s.set("shuffle", "1");
      await goto(`/item/${encodeURIComponent(item.shortcode)}?${s}`,
                 { replace: shuffling, reset: false });
    } catch (err) {
      notice = { text: m.notice_open_failed({ error: (err as Error).message }), kind: "error" };
    }
  }

  function sheetFilter(kind: FilterKind, value: string) {
    void goto(filterUrl(filters, kind, value), { replace: openedFromGrid });
  }

  function tagged(code: string, tag: string | null) {
    library.patch(code, { tag });
    void sidebar.refresh();
    if (places) pins.reload();
  }

  /* A corrected recipe can change the counts and which cards the Recipes
     view shows. The grid reloads under the sheet, keeping what is loaded
     until the new page arrives. */
  function recipeChanged() {
    void sidebar.refresh();
    library.reload();
  }

  /* Out of your library: close the sheet, and the grid and counts follow. */
  function removed(code: string, name: string) {
    notice = { text: m.notice_removed({ name }), kind: "ok" };
    close();
    void sidebar.refresh();
    library.reload();
    if (places) pins.reload();
  }

  function sheetError(message: string) {
    notice = { text: m.notice_open_failed({ error: message }), kind: "error" };
    void go(libraryUrl(filters), { stay: true, replace: true });
  }

  /* While a reel is open the page behind it does not scroll (the modal
     dialog makes it inert). When it closes, focus goes back to the card it
     was opened from. */
  let lastOpen: string | null = null;
  $effect(() => {
    const code = shortcode;
    document.body.classList.toggle("is-locked", Boolean(code));
    if (code) lastOpen = code;
  });

  /* Chromium can leave a card inert after the sheet has closed: one that was
     in the grid under the modal and is moved when the results change keeps
     the inertness the dialog gave it, so it takes no click and no focus, and
     nothing in the DOM says so (#94). Changing `inert` on each card's own
     cell makes the browser work it out again (on the grid it does not); set
     back at once, it is never seen. Once now, and once the cards have stopped
     moving, since a card styled mid-flip can pick it up again. */
  let gridEl = $state<HTMLElement>();
  $effect(() => {
    void library.items;
    const grid = gridEl;
    if (!grid || shortcode) return;
    const recheck = () => {
      for (const cell of grid.children) {
        if (!(cell instanceof HTMLElement)) continue;
        cell.inert = true;
        cell.inert = false;
      }
    };
    recheck();
    const timer = setTimeout(recheck, flipDuration() + 100);
    return () => clearTimeout(timer);
  });

  function returnFocus() {
    // A chip in the sheet may have changed the filter; then the card may be
    // gone, and focus stays where the browser puts it.
    if (!lastOpen || shortcode) return;
    document.querySelector<HTMLElement>(`[data-card="${CSS.escape(lastOpen)}"] a.thumb`)
      ?.focus({ preventScroll: true });
    lastOpen = null;
  }

  /* ---------- the shopping list ---------- */

  /* In Recipes, pick several for the shopping list, or put every one the
     filters show on it: a collection, say, or a search. */
  let selecting = $state(false);
  const picked = new SvelteSet<string>();

  function stopSelecting() {
    selecting = false;
    picked.clear();
  }

  $effect(() => { if (!recipes) stopSelecting(); });

  function pickOne(code: string) {
    if (picked.has(code)) picked.delete(code);
    else picked.add(code);
  }

  async function listed(add: () => ReturnType<typeof addToList>) {
    const before = sidebar.stats?.shopping ?? 0;
    try {
      const shop = await add();
      notice = { text: m.notice_listed({ count: Math.max(0, shop.recipes.length - before) }), kind: "ok" };
      stopSelecting();
      void sidebar.refresh();
    } catch (err) {
      notice = { text: m.shop_failed({ error: (err as Error).message }), kind: "error" };
    }
  }

  const listPicked = () => listed(() => addToList([...picked]));
  const listAll = () => listed(() => addMatchingToList(mapParams(filters)));

  const pickName = (code: string) => {
    const item = library.items.find((i) => i.shortcode === code);
    return item?.recipe?.title || item?.caption?.slice(0, 60) || code;
  };

  /* ---------- infinite scroll ---------- */

  const AHEAD = 900;          // px below the fold at which the next page loads
  let nearEnd = $state(false);
  let sentinelEl: HTMLElement | undefined;

  const sentinel = (node: HTMLElement) => {
    sentinelEl = node;
    const io = new IntersectionObserver(([e]) => { nearEnd = e.isIntersecting; },
                                        { rootMargin: `${AHEAD}px 0px` });
    io.observe(node);
    return () => io.disconnect();
  };

  // Keep loading while the end is in view: one page may not fill a tall
  // screen, and the observer only fires when visibility changes. Measured
  // after the new cards render, since `nearEnd` still describes the grid
  // before they arrived.
  $effect(() => {
    void library.items.length;
    if (!nearEnd || library.loading || library.pending || library.done || library.error) return;
    void tick().then(() => {
      if (sentinelEl && sentinelEl.getBoundingClientRect().top < innerHeight + AHEAD) library.more();
    });
  });

  // The library as it opens, nothing chosen: where Rediscover's strips go.
  const home = $derived(filters.view === "library" && !chosen && !filters.q && !filters.media
                        && library.total > 0 && !selecting);

  const countText = $derived.by(() => {
    if (library.pending && !library.items.length) return m.loading();
    const filtered = Boolean(chosen || filters.media || filters.q || recipeFiltered(filters));
    if (!library.total) {
      if (!filtered) return "";
      return recipes ? m.count_no_recipes() : places ? m.count_no_places() : m.count_no_reels();
    }
    const n = { shown: nf.format(library.items.length), total: nf.format(library.total) };
    if (recipes) return filtered ? m.count_recipes_matching(n) : m.count_recipes(n);
    if (places) return filtered ? m.count_places_matching(n) : m.count_places(n);
    return filtered ? m.count_reels_matching(n) : m.count_reels(n);
  });

  /* Export what is on screen: the filters, without the grid's paging or order. */
  const exports = $derived.by(() => {
    const p = mapParams(filters);
    p.delete("sort");
    const at = (kind: string, extra: Record<string, string> = {}) => {
      const q = new URLSearchParams(p);
      for (const [k, v] of Object.entries(extra)) q.set(k, v);
      return exportUrl(kind, q);
    };
    const count = library.total;
    return [
      {
        title: recipes ? m.export_these_recipes_all({ count }) : m.export_their_recipes(),
        choices: [
          { label: m.export_recipes_jsonld(), href: at("recipes.json") },
          { label: m.export_recipes_nextcloud(), href: at("recipes-nextcloud.zip") },
          { label: m.export_recipes_paprika(), href: at("recipes.paprikarecipes") },
        ],
      },
      {
        title: m.export_these({ count }),
        choices: [
          { label: m.export_markdown(), href: at("markdown.zip") },
          { label: m.export_markdown_images(), href: at("markdown.zip", { images: "true" }) },
          { label: m.export_json(), href: at("library.json") },
          { label: m.export_csv(), href: at("library.csv") },
        ],
      },
    ];
  });

  const unfiltered = $derived(
    !library.pending && !library.error && library.total === 0 && !chosen && !filters.media
      && !filters.q && !recipeFiltered(filters),
  );
  const showEmpty = $derived(unfiltered && filters.view === "library");
  const noRecipes = $derived(unfiltered && recipes);
  const noPlaces = $derived(unfiltered && places);

  /* The recipe filters that are on, as pills that take themselves off. */
  const recipePills = $derived([
    ...filters.has.map((v) => ({ key: `has:${v}`, label: m.pill_with(), value: groupLabel(v),
                                 off: () => pick("has", v) })),
    ...filters.without.map((v) => ({ key: `without:${v}`, label: m.pill_without(),
                                     value: groupLabel(v), off: () => pick("without", v) })),
    ...(filters.time ? [{ key: "time", label: m.pill_ready_in(),
                          value: m.minutes({ minutes: nf.format(filters.time) }),
                          off: () => setTime(filters.time) }] : []),
  ]);

  const MEDIA_LABEL = { video: m.media_video, image: m.media_image };

  const KIND = { tag: m.kind_tag, game: m.kind_game, collection: m.kind_collection,
                 hashtag: m.kind_hashtag };

  /** What a filter value looks like on screen: #hashtag, a tag in this language. */
  const shown = (kind: FilterKind, value: string) =>
    kind === "hashtag" ? `#${value}` : kind === "tag" ? tagLabel(value) : value;

  /* ---------- importing ---------- */

  /* The toolbar's Import opens the box in a dialog; an empty library shows
     it in place. Closing the dialog puts focus back on what opened it. */
  let importOpen = $state(false);
  let opener: HTMLElement | null = null;

  function openImport() {
    opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    importOpen = true;
  }

  async function closeImport() {
    importOpen = false;
    await tick();
    opener?.focus({ preventScroll: true });
    opener = null;
  }

  /* Each ZIP that lands refreshes the grid and the counts. One that lands
     with no box on screen, the dialog closed while it went up, says how it
     went in a notice instead. */
  function imported(u: Upload) {
    void sidebar.refresh();
    library.reload();
    if (places) pins.reload();
    if (importOpen || showEmpty) return;
    notice = u.state === "done"
      ? { text: m.import_landed({ file: u.file.name }), kind: "ok" }
      : { text: u.error ?? "", kind: "error" };
  }

  $effect(() => imports.listen(imported));

  /* A ZIP dragged anywhere over the library: an overlay says it can be
     dropped, and the drop goes to the same queue. Without this the browser
     opens or downloads the file. A drop on the box's own zone is taken
     there, and comes here already handled. */
  let dragDepth = 0;
  let dragging = $state(false);
  let dragBad = $state(false);

  function pageDragEnter(e: DragEvent) {
    if (!hasFiles(e.dataTransfer)) return;
    dragDepth++;
    dragging = true;
    dragBad = draggingNonZip(e.dataTransfer);
  }

  function pageDragLeave(e: DragEvent) {
    if (!hasFiles(e.dataTransfer)) return;
    if (dragDepth && --dragDepth === 0) dragging = false;
  }

  function pageDragOver(e: DragEvent) {
    if (!hasFiles(e.dataTransfer)) return;
    e.preventDefault();
    e.dataTransfer!.dropEffect = "copy";
  }

  function pageDrop(e: DragEvent) {
    dragDepth = 0;
    dragging = false;
    if (!hasFiles(e.dataTransfer) || e.defaultPrevented) return;
    e.preventDefault();
    imports.add(e.dataTransfer!.files);
    // Show where it went, unless the box is already on screen.
    if (!importOpen && !showEmpty) openImport();
  }

  function pageDragEnd() {
    dragDepth = 0;
    dragging = false;
  }

  const docTitle = $derived(
    [
      chosen ? active.map((a) => shown(a.kind, a.value)).join(" + ")
        : filters.q ? m.quoted({ text: filters.q }) : null,
      filters.media ? MEDIA_LABEL[filters.media]() : null,
      recipes ? m.recipes() : null,
      places ? (mapped ? m.layout_map() : m.places()) : null,
      "Hikidashi",
    ].filter(Boolean).join(" · "),
  );
</script>

<svelte:head><title>{docTitle}</title></svelte:head>

<svelte:window ondragenter={pageDragEnter} ondragleave={pageDragLeave} ondragover={pageDragOver}
               ondrop={pageDrop} ondragend={pageDragEnd} onkeydown={(e) => {
  if (e.key !== "Escape" || shortcode || importOpen) return;
  if (drawer) drawer = false;
  else if (selecting) stopSelecting();
}} />

<div class="app">
  <Topbar onmenu={() => (drawer = !drawer)} menuOpen={drawer}>
    <div class="search">
      <input type="search" autocomplete="off"
             placeholder={recipes ? m.search_placeholder_recipes() : m.search_placeholder()}
             aria-label={m.search()} bind:value={q} oninput={typed}>
    </div>
    <select class="sort" aria-label={m.sort_label()} value={filters.sort} onchange={setSort}>
      <option value="relevance">{m.sort_relevance()}</option>
      <option value="saved_desc">{m.sort_newest()}</option>
      <option value="saved_asc">{m.sort_oldest()}</option>
      <option value="author">{m.sort_author()}</option>
    </select>
  </Topbar>

  <div class="layout">
    <Sidebar {filters} open={drawer} onfilter={setFilter} onall={clearFilters}
             onview={setView} onmedia={setMedia} onpick={pick} ontime={setTime} />
    {#if drawer}
      <button class="scrim" type="button" aria-label={m.filters_close()} onclick={() => (drawer = false)}></button>
    {/if}

    <main class="main" class:is-map={places && mapped}>
      <div class="resbar">
        {#if places}
          <div class="layouts" role="group" aria-label={m.layout_label()}>
            <button type="button" aria-pressed={!mapped} onclick={() => setLayout("grid")}>
              <Icon name="library" size={14} /> {m.layout_grid()}
            </button>
            <button type="button" aria-pressed={mapped} onclick={() => setLayout("map")}
                    disabled={!mapped && !pins.pending && !pins.items.length}>
              <Icon name="map" size={14} /> {m.layout_map()}
              <b>{pins.pending ? "" : nf.format(pins.items.length)}</b>
            </button>
          </div>
        {/if}
        {#if !(places && mapped)}
          <span class="result-count" aria-live="polite">{countText}</span>
        {/if}
        {#if recipes && library.total && !selecting}
          <button type="button" class="btn sm" onclick={() => (selecting = true)}>
            <Icon name="basket" size={14} /> {m.select_recipes()}
          </button>
          {#if chosen || filters.q || recipeFiltered(filters)}
            <button type="button" class="btn sm" onclick={listAll}>
              {m.shop_add_all({ count: library.total })}
            </button>
          {/if}
        {/if}
        {#each active as a (`${a.kind}:${a.value}`)}
          <button type="button" class="pill-clear" title={m.filter_clear()}
                  onclick={() => setFilter(a.kind, a.value)}>
            <em>{KIND[a.kind]()}</em>
            {shown(a.kind, a.value)}
            <b>&times;</b>
          </button>
        {/each}
        {#if filters.media}
          <button type="button" class="pill-clear" title={m.filter_clear()}
                  onclick={() => setMedia(filters.media)}>
            <em>{m.pill_media()}</em> {MEDIA_LABEL[filters.media]()} <b>&times;</b>
          </button>
        {/if}
        {#each recipePills as p (p.key)}
          <button type="button" class="pill-clear" title={m.filter_clear()} onclick={p.off}>
            <em>{p.label}</em> {p.value} <b>&times;</b>
          </button>
        {/each}
        <span class="upload-slot">
          {#if library.total}
            <button type="button" class="btn sm" title={m.shuffle_title()} onclick={shuffle}>
              <Icon name="shuffle" size={14} /> {m.shuffle()}
            </button>
            <ExportMenu groups={exports} />
          {/if}
          <button type="button" class="btn sm" aria-haspopup="dialog" onclick={openImport}>
            <Icon name="import" size={14} /> {imports.busy ? m.importing() : m.import_button()}
          </button>
        </span>
      </div>

      <Notice bind:notice />

      {#if home}<Rediscover {search} />{/if}

      {#if places && mapped}
        <MapView {filters} {search} active={shortcode} onmove={mapMoved} />
      {:else}
      <div class="grid" class:stale={library.pending && library.items.length > 0} bind:this={gridEl}>
        {#each library.items as item, i (item.shortcode)}
          <div class="cell" animate:flip={{ duration: flipDuration() }} in:cardIn={{ index: i % 60 }}>
            <Card {item} {filters} href={itemHref(item)}
                  active={item.shortcode === shortcode} />
            {#if selecting}
              <button type="button" class="pick" aria-pressed={picked.has(item.shortcode)}
                      aria-label={m.select_item({ title: pickName(item.shortcode) })}
                      onclick={() => pickOne(item.shortcode)}><span aria-hidden="true"></span></button>
            {/if}
          </div>
        {/each}
        {#if library.pending && !library.items.length && !library.error}
          {#each { length: 12 } as _, i (i)}
            <div class="skeleton" aria-hidden="true" style:animation-delay="{i * 60}ms">
              <span class="s-thumb"></span><span class="s-line"></span><span class="s-line short"></span>
            </div>
          {/each}
        {/if}
      </div>

      {#if showEmpty}
        <div class="empty first">
          <h3>{m.empty_title()}</h3>
          <p>{m.empty_body()}</p>
          <ImportBox level={4} />
        </div>
      {/if}

      {#if noRecipes}
        <div class="empty">
          <h3>{m.no_recipes_title()}</h3>
          <p><Rich parts={m.no_recipes_body.parts()} /></p>
        </div>
      {/if}

      {#if noPlaces}
        <div class="empty">
          <h3>{m.no_places_title()}</h3>
          <p><Rich parts={m.no_places_body.parts()} /></p>
        </div>
      {/if}

      {#if library.error && library.items.length}
        <button class="btn more" type="button" onclick={() => library.more()}>{m.retry()}</button>
      {/if}
      <div class="sentinel" {@attach sentinel} aria-hidden="true">
        {#if library.loading && !library.pending}<span class="spinner"></span>{/if}
      </div>
      {/if}
    </main>
  </div>
</div>

{#if selecting}
  <div class="pickbar" role="group" aria-label={m.select_recipes()}>
    <b aria-live="polite">{m.select_count({ count: picked.size })}</b>
    <button type="button" class="btn primary" disabled={!picked.size} onclick={listPicked}>
      <Icon name="basket" size={14} /> {m.shop_add_short()}
    </button>
    <button type="button" class="btn" onclick={stopSelecting}>{m.cancel()}</button>
  </div>
{/if}

{#if importOpen}
  <ImportDialog onclose={closeImport} />
{/if}

{#if dragging}
  <div class="dropping" class:bad={dragBad} aria-hidden="true">
    <div class="dropping-card">
      <Icon name="import" size={32} />
      <b>{dragBad ? m.import_zone_bad() : m.import_drop_page()}</b>
      <span>{m.import_drop_page_hint()}</span>
    </div>
  </div>
{/if}

{#if shortcode}
  <DetailSheet {shortcode} {preview} recipeFirst={recipes} onclose={close} onclosed={returnFocus}
               onfilter={sheetFilter} ontagged={tagged} onerror={sheetError}
               onchanged={recipeChanged} onremoved={removed}
               onanother={shuffling ? shuffle : undefined} />
{/if}

{@render children()}

<style>
  .search { flex: 1; min-width: 0; }

  .search input, .sort {
    padding: 7px 12px;
    background: var(--field);
    border: 1px solid transparent;
    border-radius: var(--radius);
    transition: border-color 120ms ease, box-shadow 120ms ease;
  }

  .search input { width: 100%; }
  .search input:focus { border-color: var(--rule); background: var(--surface); outline: none; }

  .sort { flex: none; padding: 7px 10px; }

  .layout { display: grid; grid-template-columns: 220px 1fr; align-items: start; }

  /* The sidebar is a rail of icons until pinned (Sidebar.svelte). */
  @media (min-width: 701px) and (hover: hover) {
    .layout:not(:has(> :global(.side.pinned))) { grid-template-columns: 64px 1fr; }
  }

  .main { padding: 18px; display: flex; flex-direction: column; gap: 14px; min-width: 0; }

  .resbar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }

  .result-count {
    font-size: 13px;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }

  .upload-slot { margin-left: auto; display: inline-flex; align-items: center; gap: 8px; }

  /* The first thing a new library shows: the import box, wider than the
     other empty states so its steps have room. */
  .empty.first { max-width: 60ch; gap: 14px; }

  /* The map fills what is left of the screen below the bar. */
  .main.is-map { height: calc(100dvh - var(--topbar-h)); position: sticky; top: var(--topbar-h); }

  .layouts {
    display: inline-flex;
    gap: 2px;
    padding: 3px;
    background: var(--surface-2);
    border-radius: var(--radius);
  }

  .layouts button {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    font-size: 13px;
    background: none;
    border: 0;
    border-radius: var(--radius-sm);
    color: var(--ink-2);
    cursor: pointer;
    transition: background-color 140ms ease, color 140ms ease;
  }

  .layouts button:hover:not(:disabled) { color: var(--ink); }
  .layouts button:disabled { opacity: 0.45; cursor: default; }
  .layouts b { font-weight: 500; color: var(--muted); font-variant-numeric: tabular-nums; }

  .layouts button[aria-pressed="true"] {
    background: var(--surface);
    color: var(--ink);
    font-weight: 600;
    box-shadow: 0 1px 2px rgb(0 0 0 / 12%);
  }

  .pill-clear {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 12px;
    font-size: 12.5px;
    background: var(--accent-soft);
    color: var(--accent);
    border: 0;
    border-radius: var(--radius-pill);
    cursor: pointer;
    animation: pop 200ms var(--ease-out);
  }

  .pill-clear em { font-style: normal; opacity: 0.6; }
  .pill-clear b { font-weight: 600; }
  .pill-clear:hover { filter: brightness(1.08); }

  @keyframes pop { from { opacity: 0; transform: scale(0.92); } }

  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
    gap: 14px;
    transition: opacity 160ms ease;
  }

  /* The old results stay up, dimmed, until the new ones arrive. */
  .grid.stale { opacity: 0.55; }

  .cell { min-width: 0; position: relative; }

  /* Picking recipes: the whole card is the target, with a tick in its corner. */
  .pick {
    position: absolute;
    inset: 0;
    padding: 0;
    border: 0;
    border-radius: var(--radius);
    background: none;
    cursor: pointer;
  }
  .pick span {
    position: absolute;
    top: 8px;
    left: 8px;
    width: 26px;
    height: 26px;
    border: 2px solid #fff;
    border-radius: 50%;
    background: rgb(0 0 0 / 30%);
    box-shadow: 0 1px 4px rgb(0 0 0 / 40%);
  }
  .pick[aria-pressed="true"] { outline: 3px solid var(--accent-fill); outline-offset: 3px; }
  .pick[aria-pressed="true"] span { background: var(--accent-fill); }
  .pick[aria-pressed="true"] span::after {
    content: "";
    position: absolute;
    left: 7px;
    top: 3px;
    width: 6px;
    height: 11px;
    border: solid #fff;
    border-width: 0 2px 2px 0;
    transform: rotate(45deg);
  }

  .btn.sm { display: inline-flex; align-items: center; gap: 6px; padding: 5px 10px; font-size: 12.5px; }

  .pickbar {
    position: fixed;
    left: 50%;
    bottom: max(16px, env(safe-area-inset-bottom));
    z-index: 4;
    transform: translateX(-50%);
    display: flex;
    align-items: center;
    gap: 10px;
    width: max-content;
    max-width: calc(100% - 24px);
    padding: 8px 8px 8px 16px;
    border: 1px solid var(--rule);
    border-radius: var(--radius-pill);
    background: var(--surface);
    box-shadow: 0 12px 32px -12px rgb(0 0 0 / 45%);
    animation: pop 200ms var(--ease-out);
  }
  .pickbar b { font-size: 14px; font-variant-numeric: tabular-nums; white-space: nowrap; }
  .pickbar .btn { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }

  /* A phone: edge to edge above the bottom, the count taking what is left. */
  @media (max-width: 700px) {
    .pickbar { left: 12px; right: 12px; width: auto; max-width: none; transform: none; gap: 8px; }
    .pickbar b { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
    .pickbar .btn { padding: 7px 12px; }
  }

  .skeleton { display: flex; flex-direction: column; gap: 6px; animation: pulse 1.1s ease-in-out infinite alternate; }
  .skeleton span { display: block; border-radius: var(--radius-sm); background: var(--surface-2); }
  .s-thumb { aspect-ratio: 3 / 4; border-radius: var(--radius) !important; }
  .s-line { height: 10px; }
  .s-line.short { width: 55%; }

  @keyframes pulse { to { opacity: 0.45; } }

  .more { align-self: center; }

  .sentinel { display: grid; place-items: center; min-height: 40px; }

  .spinner {
    width: 18px;
    height: 18px;
    border: 2px solid var(--rule);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 700ms linear infinite;
  }

  @keyframes spin { to { transform: rotate(360deg); } }

  .scrim { display: none; }

  @media (max-width: 700px) {
    .layout { grid-template-columns: 1fr; }
    .main { padding: 14px 12px; }
    .grid { grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 12px; }
    .sort { padding: 7px 4px; font-size: 13px; }
    .search input { padding: 7px 10px; }

    .scrim {
      display: block;
      position: fixed;
      inset: var(--topbar-h) 0 0;
      z-index: 3;
      padding: 0;
      border: 0;
      background: rgb(0 0 0 / 45%);
      animation: fade-in 200ms ease;
    }
  }

  @keyframes fade-in { from { opacity: 0; } }

  /* Over everything but a modal, and never in the way of the drop itself:
     the events go through to the page, or to the box's zone below. */
  .dropping {
    position: fixed;
    inset: 0;
    z-index: 20;
    display: grid;
    place-items: center;
    padding: 16px;
    pointer-events: none;
    background: rgb(0 0 0 / 35%);
    animation: fade-in 140ms ease;
  }

  .dropping-card {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 8px;
    padding: 32px 40px;
    max-width: 100%;
    border: 2px dashed var(--accent);
    border-radius: var(--radius-lg);
    background: var(--surface);
    color: var(--accent);
    text-align: center;
    box-shadow: 0 24px 60px -20px rgb(0 0 0 / 50%);
  }

  .dropping-card b { font-family: var(--display); font-size: 18px; color: var(--ink); }
  .dropping-card span { font-size: 13px; color: var(--muted); }
  .dropping.bad .dropping-card { border-color: var(--risk); color: var(--risk); }
  .dropping.bad .dropping-card b { color: var(--risk); }

  @media (prefers-reduced-motion: reduce) { .dropping { animation: none; } }
</style>
