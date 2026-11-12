<script lang="ts">
  import { onMount, untrack } from "svelte";
  import type * as Leaflet from "leaflet";
  import { geocode } from "#lib/api.js";
  import { filterKey, type Filters } from "#lib/filters.js";
  import { nf } from "#lib/format.js";
  import { baseLayer, distance, km, leaflet, readAt, writeAt, type Point } from "#lib/geo.js";
  import { tagLabel } from "#lib/i18n.js";
  import { pins, sidebar } from "#lib/library.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import Icon from "./Icon.svelte";
  import Rich from "./Rich.svelte";
  import type { Pin, PlacedItem } from "#lib/types.js";

  /* Places on a map, beside a list of their reels. The list follows the map:
     pan or zoom and it narrows to what is in view. Hovering a reel lights its
     pins, and hovering a pin lights its reel. At phone width the map fills
     the screen and the list is a sheet that pulls up from the bottom. */
  let { filters, search, active = null, onmove }: {
    filters: Filters;
    /** The query string the library is at, for links that keep it. */
    search: string;
    /** The reel open in the sheet, if any. */
    active?: string | null;
    /** The map moved: where it looks now, for the URL. */
    onmove: (at: string) => void;
  } = $props();

  let el: HTMLDivElement;
  let L: typeof Leaflet | undefined;
  let map: Leaflet.Map | undefined;
  let cluster: Leaflet.MarkerClusterGroup | undefined;
  let you: Leaflet.CircleMarker | undefined;
  /** Each reel's pins, by the place's seq. */
  const markers = new Map<string, Map<number, Leaflet.Marker>>();

  let ready = $state(false);
  /** What the map shows, for narrowing the list. Null until it has a view. */
  let view = $state.raw<Leaflet.LatLngBounds | null>(null);
  /** The place lit on both sides, and which side lit it. */
  let hot = $state<{ code: string; seq: number; from: "list" | "map" } | null>(null);
  /** Near me, or a typed city: the list is sorted by distance from it. */
  let origin = $state<(Point & { label: string }) | null>(null);
  let sheet = $state(false);
  let problem = $state<string | null>(null);
  let city = $state("");
  let finding = $state(false);

  let written = "";              // the last position we put in the URL
  // Whether the map has a view of its own yet: from the URL, or fitted to
  // the first pins. Until then its placeholder view is not worth recording.
  let settled = false;

  const items = $derived(pins.items);
  const placed = $derived(items.length);
  /* A row for each place, not each reel: a reel naming three spots on one
     street lists all three. */
  const rows = $derived(items.flatMap((item) => item.places.map((place) => ({ item, place }))));
  const inView = $derived(view
    ? rows.filter((r) => view!.contains([r.place.lat, r.place.lng]))
    : rows);
  const reelsInView = $derived(new Set(inView.map((r) => r.item.shortcode)).size);
  const listed = $derived(origin
    ? [...inView].sort((a, b) => km(a.place, origin!) - km(b.place, origin!))
    : inView);
  const settings = $derived(sidebar.stats?.map ?? null);

  const away = (place: Pin) => {
    const d = distance(km(place, origin!));
    return d.unit === "m" ? m.map_m({ m: nf.format(d.value) }) : m.map_km({ km: nf.format(d.value) });
  };

  const href = (code: string) => `/item/${encodeURIComponent(code)}${search}`;

  /* ---------- the map ---------- */

  onMount(() => {
    let gone = false;
    void leaflet().then((lib) => {
      if (gone) return;
      L = lib;
      // The whole world fits a phone only at zoom 1; anywhere wider, 1 leaves
      // empty bands above and below it.
      const minZoom = el.clientWidth < 600 ? 1 : 2;
      map = L.map(el, { zoomControl: true, worldCopyJump: true, minZoom, maxZoom: 19 });
      map.attributionControl.setPrefix('<a href="https://leafletjs.com">Leaflet</a>');
      cluster = L.markerClusterGroup({
        showCoverageOnHover: false,
        maxClusterRadius: 48,
        iconCreateFunction: (c) => L!.divIcon({
          className: "hk-cluster",
          // Numbers only: nothing a caption wrote reaches this HTML.
          html: `<span title="${m.map_cluster({ count: nf.format(c.getChildCount()) })}">${
            nf.format(c.getChildCount())}</span>`,
          iconSize: [36, 36],
        }),
      });
      cluster.on("animationend", light);
      map.addLayer(cluster);
      map.on("moveend", moved);
      const at = readAt(filters.at);
      if (at) {
        written = filters.at;
        settled = true;
        map.setView(at.center, at.zoom);
      } else {
        map.setView([20, 0], 2);
      }
      ready = true;
    });
    // The map's box changes with the window, the sidebar's pin and the
    // phone's toolbar; Leaflet only measures it when told to.
    const resized = new ResizeObserver(() => map?.invalidateSize());
    resized.observe(el);
    return () => {
      gone = true;
      resized.disconnect();
      map?.remove();
      map = undefined;
    };
  });

  // The tiles come with the library's stats, which may arrive after the map.
  let tiled = false;
  $effect(() => {
    if (!ready || !settings || tiled) return;
    tiled = true;
    baseLayer(L!, settings).addTo(map!);
  });

  function moved() {
    if (!settled) return;
    view = map!.getBounds();
    const at = writeAt(map!.getCenter(), map!.getZoom());
    if (at !== written) {
      written = at;
      onmove(at);
    }
    light();
  }

  // Back and Forward move the map, as they would a page.
  $effect(() => {
    const at = filters.at;
    if (!ready || !at || at === written) return;
    const where = readAt(at);
    if (!where) return;
    written = at;
    map!.setView(where.center, where.zoom);
  });

  /* Every pin, redrawn when the results change. Other filters, as Places'
     Restaurants or Travel, are other pins: the map moves to show them all.
     So does a map left looking at nothing the new results have. */
  let drawn: string | null = null;    // the filters the pins on the map are for
  $effect(() => {
    const now = items;
    if (!ready || pins.pending) return;
    untrack(() => {
      const key = filterKey(filters);
      draw(now, drawn !== null && key !== drawn);
      drawn = key;
    });
  });

  function draw(now: PlacedItem[], refiltered: boolean) {
    cluster!.clearLayers();
    markers.clear();
    const all: Leaflet.Marker[] = [];
    for (const item of now) {
      const mine = new Map(item.places.map((p) => [p.seq, pin(item, p)]));
      markers.set(item.shortcode, mine);
      all.push(...mine.values());
    }
    cluster!.addLayers(all);
    const bounds = map!.getBounds();
    const fitted = !settled || refiltered || !all.some((mk) => bounds.contains(mk.getLatLng()));
    settled = true;
    if (fitted) fit();
    else moved();
  }

  function pin(item: PlacedItem, p: Pin): Leaflet.Marker {
    const mk = L!.marker([p.lat, p.lng], {
      icon: L!.divIcon({ className: "hk-pin", html: "<span></span>", iconSize: [22, 22],
                         iconAnchor: [11, 11], popupAnchor: [0, -10] }),
      title: p.text,
      alt: p.text,
      riseOnHover: true,
    });
    mk.on("mouseover", () => { hot = { code: item.shortcode, seq: p.seq, from: "map" }; });
    mk.on("mouseout", () => { if (isHot(item.shortcode, p.seq)) hot = null; });
    mk.bindPopup(() => popup(item, p), { className: "hk-popup", maxWidth: 280, minWidth: 240 });
    return mk;
  }

  /* Built as elements, not HTML, so a caption's text can never be markup.
     The link is an ordinary one: the app routes it, and the sheet opens. */
  function popup(item: PlacedItem, p: Pin): HTMLElement {
    const a = document.createElement("a");
    a.className = "hk-pop";
    a.href = href(item.shortcode);
    if (item.thumb) {
      const img = document.createElement("img");
      img.src = item.thumb;
      img.alt = "";
      a.append(img);
    }
    const text = document.createElement("span");
    const name = document.createElement("strong");
    name.textContent = p.text;
    if (p.label) name.title = p.label;
    const who = document.createElement("small");
    who.textContent = [item.author && `@${item.author}`, item.tag && tagLabel(item.tag)]
      .filter(Boolean).join(" · ");
    const open = document.createElement("em");
    open.textContent = m.map_open();
    text.append(name, who, open);
    a.append(text);
    return a;
  }

  function fit() {
    map!.invalidateSize();
    const bounds = cluster!.getBounds();
    // At phone width the list's strip covers the bottom of the map.
    const below = el.clientWidth < 600 ? 60 : 36;
    if (bounds.isValid()) {
      map!.fitBounds(bounds, { paddingTopLeft: [36, 36], paddingBottomRight: [36, below], maxZoom: 13 });
    } else {
      map!.setView([20, 0], 2);
    }
  }

  const isHot = (code: string, seq: number) => hot?.code === code && hot.seq === seq;

  /* The lit place's pin, or the open reel's pins, or the clusters they are
     hidden in. */
  let lit: HTMLElement[] = [];
  function light() {
    for (const e of lit) e.classList.remove("is-hot");
    lit = [];
    if (!cluster) return;
    const code = hot?.code ?? active;
    if (!code) return;
    const mine = markers.get(code);
    if (!mine) return;
    const marks = hot ? [mine.get(hot.seq)].filter((mk) => mk !== undefined) : [...mine.values()];
    for (const mk of marks) {
      const shown = cluster.getVisibleParent(mk) as Leaflet.Marker | null;
      const e = shown?.getElement();
      if (e) { e.classList.add("is-hot"); lit.push(e); }
    }
  }

  $effect(() => {
    void hot; void active;
    if (ready) light();
  });

  // A pin hovered on the map brings its reel into view in the list.
  $effect(() => {
    if (hot?.from !== "map") return;
    document.querySelector(`[data-row="${CSS.escape(`${hot.code}:${hot.seq}`)}"]`)
      ?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  });

  /* ---------- near me, or a city ---------- */

  function goTo(p: Point & { label: string }) {
    origin = p;
    you?.remove();
    you = L!.circleMarker([p.lat, p.lng], { radius: 7, className: "hk-you" }).addTo(map!);
    map!.setView([p.lat, p.lng], Math.max(map!.getZoom(), 11));
  }

  function nearMe() {
    problem = null;
    if (!isSecureContext || !navigator.geolocation) {
      problem = m.map_near_me_insecure();
      return;
    }
    finding = true;
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        finding = false;
        goTo({ lat: pos.coords.latitude, lng: pos.coords.longitude, label: m.map_from_you() });
      },
      (err) => {
        finding = false;
        problem = m.map_near_me_failed({ error: err.message });
      },
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 },
    );
  }

  async function goToCity(e: SubmitEvent) {
    e.preventDefault();
    const q = city.trim();
    if (!q) return;
    problem = null;
    finding = true;
    try {
      const found = await geocode(q);
      goTo({ lat: found.lat, lng: found.lng, label: q });
    } catch (err) {
      problem = m.map_go_failed({ place: q, error: (err as Error).message });
    } finally {
      finding = false;
    }
  }

  function clearOrigin() {
    origin = null;
    you?.remove();
    you = undefined;
  }
</script>

<div class="mapview" class:sheet-open={sheet}>
  <section class="maplist" aria-label={m.map_list()}>
    <button class="handle" type="button" aria-expanded={sheet} onclick={() => (sheet = !sheet)}>
      <span class="grip" aria-hidden="true"></span>
      <span>{m.map_in_view({ shown: nf.format(reelsInView) })}</span>
    </button>

    <div class="tools">
      <p class="count">
        {m.map_count({ placed: nf.format(placed), total: nf.format(pins.total) })}
        {#if view && reelsInView < placed}
          · {m.map_in_view({ shown: nf.format(reelsInView) })}
          <button class="linkish" type="button" onclick={fit}>{m.map_show_all()}</button>
        {/if}
      </p>
      {#if !pins.pending && pins.named > placed}
        <p class="hint">
          {#if !placed && !settings?.geocoder}<Rich parts={m.map_no_geocoder.parts()} />
          {:else if !placed}<Rich parts={m.map_none.parts()} />
          {:else}{m.map_unplaced({ count: nf.format(pins.named - placed) })}{/if}
        </p>
      {/if}

      <div class="find">
        <button class="btn near" type="button" onclick={nearMe} disabled={!ready || finding}>
          <Icon name="locate" size={15} /> {m.map_near_me()}
        </button>
        {#if settings?.geocoder}
          <form class="city" onsubmit={goToCity}>
            <input type="search" bind:value={city} placeholder={m.map_go_placeholder()}
                   aria-label={m.map_go_placeholder()} disabled={!ready}>
          </form>
        {/if}
      </div>
      {#if origin}
        <p class="from">
          {m.map_from({ place: origin.label })}
          <button class="linkish" type="button" onclick={clearOrigin}
                  aria-label={m.map_from_clear()} title={m.map_from_clear()}>&times;</button>
        </p>
      {/if}
      {#if problem}<p class="hint problem" role="alert">{problem}</p>{/if}
    </div>

    <ol class="rows">
      {#each listed as { item, place } (`${item.shortcode}:${place.seq}`)}
        {@const lit = () => (hot = { code: item.shortcode, seq: place.seq, from: "list" })}
        {@const unlit = () => { if (isHot(item.shortcode, place.seq)) hot = null; }}
        <li data-row={`${item.shortcode}:${place.seq}`} class:is-hot={isHot(item.shortcode, place.seq)}
            class:is-active={item.shortcode === active}>
          <a href={href(item.shortcode)} onmouseenter={lit} onmouseleave={unlit} onfocus={lit} onblur={unlit}>
            {#if item.thumb}<img src={item.thumb} alt="" loading="lazy">{:else}<span class="nothumb"></span>{/if}
            <span class="what">
              <strong>{place.text}</strong>
              <small>{[item.author && `@${item.author}`, item.tag && tagLabel(item.tag)].filter(Boolean).join(" · ")}</small>
            </span>
            {#if origin}<span class="away">{away(place)}</span>{/if}
          </a>
        </li>
      {/each}
    </ol>
  </section>

  <div class="map" bind:this={el} role="region" aria-label={m.map_label()}></div>
</div>

<style>
  .mapview {
    display: grid;
    grid-template-columns: minmax(260px, 340px) 1fr;
    gap: 14px;
    flex: 1;
    min-height: 0;
  }

  .maplist {
    display: flex;
    flex-direction: column;
    min-height: 0;
    background: var(--surface);
    border: 1px solid var(--rule);
    border-radius: var(--radius-lg);
    overflow: hidden;
  }

  .handle { display: none; }

  .tools {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 12px 12px 10px;
    border-bottom: 1px solid var(--rule);
  }

  .count { margin: 0; font-size: 13px; color: var(--ink-2); font-variant-numeric: tabular-nums; }
  .hint { margin: 0; font-size: 12.5px; color: var(--muted); }
  .hint.problem { color: var(--risk); }
  .from { margin: 0; font-size: 12.5px; color: var(--accent); }

  .linkish {
    padding: 0 2px;
    background: none;
    border: 0;
    color: var(--accent);
    font: inherit;
    cursor: pointer;
  }

  .linkish:hover { text-decoration: underline; }

  .find { display: flex; gap: 8px; align-items: center; }
  .near { display: inline-flex; align-items: center; gap: 6px; flex: none; padding: 6px 10px; font-size: 13px; }
  .city { flex: 1; min-width: 0; }

  .city input {
    width: 100%;
    padding: 6px 10px;
    font-size: 13px;
    background: var(--field);
    border: 1px solid transparent;
    border-radius: var(--radius);
  }

  .city input:focus { border-color: var(--rule); background: var(--surface); outline: none; }

  .rows { list-style: none; margin: 0; padding: 6px; overflow-y: auto; flex: 1; min-height: 0; }

  .rows a {
    display: grid;
    grid-template-columns: 48px minmax(0, 1fr) auto;
    gap: 10px;
    align-items: center;
    padding: 6px;
    border-radius: var(--radius);
    color: inherit;
    text-decoration: none;
    transition: background-color 120ms ease;
  }

  .rows li.is-hot a, .rows a:hover { background: var(--surface-2); }
  .rows li.is-active a { box-shadow: inset 3px 0 0 var(--accent); }

  .rows img, .nothumb {
    width: 48px;
    height: 48px;
    object-fit: cover;
    border-radius: var(--radius-sm);
    background: var(--surface-2);
  }

  .what { display: flex; flex-wrap: wrap; gap: 0 6px; min-width: 0; font-size: 13px; }
  .what strong { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%; }
  .what small { flex-basis: 100%; color: var(--muted); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .away { font-size: 12px; color: var(--accent); font-variant-numeric: tabular-nums; }

  /* Leaflet's panes stack to z-index 1000; keep them under the sidebar and
     the sheet, which open over the map. */
  .map {
    position: relative;
    z-index: 0;
    isolation: isolate;
    min-height: 320px;
    border-radius: var(--radius-lg);
    border: 1px solid var(--rule);
    overflow: hidden;
    background: var(--surface-2);
  }

  .map :global(.leaflet-container) { font: inherit; }

  /* Dark themes: the light tiles, inverted and turned back to their hues. */
  :global(:root[data-scheme="dark"]) .map :global(.leaflet-tile-pane) {
    filter: invert(1) hue-rotate(180deg) brightness(0.92) contrast(0.88);
  }

  .map :global(.hk-pin span) {
    display: block;
    width: 16px;
    height: 16px;
    margin: 3px;
    border-radius: 50%;
    background: var(--accent-fill);
    border: 2.5px solid #fff;
    box-shadow: 0 1px 4px rgb(0 0 0 / 45%);
    transition: transform 140ms var(--ease-out);
  }

  .map :global(.hk-cluster) {
    display: grid;
    place-items: center;
    border-radius: 50%;
    background: color-mix(in srgb, var(--accent-fill) 30%, transparent);
  }

  .map :global(.hk-cluster span) {
    display: grid;
    place-items: center;
    width: 28px;
    height: 28px;
    border-radius: 50%;
    background: var(--accent-fill);
    color: var(--on-accent);
    font-size: 12px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    box-shadow: 0 1px 4px rgb(0 0 0 / 35%);
    transition: transform 140ms var(--ease-out);
  }

  .map :global(.is-hot) { z-index: 1000 !important; }
  .map :global(.hk-pin.is-hot span) { transform: scale(1.45); background: var(--risk); }
  .map :global(.hk-cluster.is-hot span) { transform: scale(1.2); background: var(--risk); }

  .map :global(.hk-you) { fill: var(--accent); fill-opacity: 0.35; stroke: var(--accent); stroke-width: 2; }

  .map :global(.hk-popup .leaflet-popup-content-wrapper) {
    padding: 0;
    border-radius: var(--radius);
    background: var(--surface);
    color: var(--ink);
  }

  .map :global(.hk-popup .leaflet-popup-tip) { background: var(--surface); }
  .map :global(.hk-popup .leaflet-popup-content) { margin: 0; }

  .map :global(.hk-pop) {
    display: grid;
    grid-template-columns: 64px 1fr;
    gap: 10px;
    padding: 8px;
    color: inherit;
    text-decoration: none;
  }

  .map :global(.hk-pop img) { width: 64px; height: 80px; object-fit: cover; border-radius: var(--radius-sm); }
  .map :global(.hk-pop span) { display: flex; flex-direction: column; gap: 3px; min-width: 0; font-size: 12.5px; }
  .map :global(.hk-pop strong) { font-size: 13px; line-height: 1.3; }
  .map :global(.hk-pop small) { color: var(--muted); }
  .map :global(.hk-pop em) { margin-top: auto; font-style: normal; color: var(--accent); font-weight: 600; }

  /* Phone width: the map fills the screen; the list is a sheet over it, a
     strip at the bottom until it is pulled up. */
  @media (max-width: 700px) {
    .mapview { display: block; position: relative; }
    .map { position: absolute; inset: 0; border-radius: var(--radius); }

    .maplist {
      position: absolute;
      z-index: 1;
      left: 0;
      right: 0;
      bottom: 0;
      height: 62%;
      border-radius: var(--radius-lg) var(--radius-lg) 0 0;
      box-shadow: 0 -8px 30px -12px rgb(0 0 0 / 45%);
      transform: translateY(calc(100% - 44px));
      transition: transform 260ms var(--ease-out);
    }

    .sheet-open .maplist { transform: none; }

    .handle {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 6px;
      flex: none;
      height: 44px;
      padding: 8px 0 0;
      background: none;
      border: 0;
      color: var(--ink-2);
      font-size: 13px;
      cursor: pointer;
    }

    .grip { width: 36px; height: 4px; border-radius: 2px; background: var(--surface-3); }
  }
</style>
