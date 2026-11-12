<script lang="ts">
  import { slide } from "svelte/transition";
  import { nf } from "#lib/format.js";
  import { chooseLocale, groupLabel, LANGUAGES, locale, stateLabel, tagLabel } from "#lib/i18n.js";
  import { m } from "#lib/paraglide/messages.js";
  import { locales } from "#lib/paraglide/runtime.js";
  import { motion } from "#lib/motion.svelte.js";
  import { sidebar } from "#lib/library.svelte.js";
  import { session } from "#lib/session.svelte.js";
  import { MEDIA, TIMES, type Filters } from "#lib/filters.js";
  import type { IconName } from "#lib/icons.js";
  import Icon from "./Icon.svelte";
  import type { FilterKind, Media, PickKind, View } from "#lib/types.js";

  let { filters, open = false, onfilter, onall, onview, onmedia, onpick, ontime }: {
    filters: Filters;
    /** The drawer is showing, at phone width. */
    open?: boolean;
    onfilter: (kind: FilterKind, value: string) => void;
    onall: () => void;
    onview: (view: View) => void;
    onmedia: (media: Media) => void;
    onpick: (kind: PickKind, value: string) => void;
    ontime: (minutes: number) => void;
  } = $props();

  const stats = $derived(sidebar.stats);
  // The pipeline and its logs are the whole library's, not yours: admins only.
  const admin = $derived(Boolean(session.me?.is_admin));
  const WORKER = {
    running: m.side_worker_running, problem: m.side_worker_problem, lost: m.side_worker_lost,
    stopped: m.side_worker_stopped, none: m.side_worker_none,
  };
  const used = $derived(sidebar.tags.filter((t) => t.count > 0));
  const recipes = $derived(filters.view === "recipes");
  const places = $derived(filters.view === "places");
  const allActive = $derived(
    filters.view === "library" && !filters.collection && !filters.tag && !filters.game
      && !filters.hashtag.length
      && !filters.media,
  );
  // Reels are videos; everything else (posts, carousels) is images.
  const mediaCount = (kind: Media) =>
    stats ? (kind === "video" ? stats.reels : stats.total - stats.reels) : null;
  const MEDIA_LABEL = { video: m.media_video, image: m.media_image };
  // Places' kinds are tags: a place reel tagged Food is somewhere to eat.
  const PLACE_KINDS = [["Food", m.places_restaurants], ["Travel", m.places_travel]] as const;

  const facets = $derived(sidebar.facets);
  $effect(() => { if (recipes && !sidebar.facets) void sidebar.refreshFacets(); });
  const timeCount = (m: number) => facets?.times.find((t) => t.minutes === m)?.count ?? 0;

  const coverage = $derived(stats && [
    m.coverage_kinds({ reels: nf.format(stats.reels), photos: nf.format(stats.total - stats.reels) }),
    m.coverage_media({ thumbs: nf.format(stats.with_thumb), videos: nf.format(stats.with_media) }),
    m.coverage_text({
      captions: nf.format(stats.with_caption), transcripts: nf.format(stats.with_transcript),
    }),
    m.coverage_profile({ profile: stats.profile }),
  ].join("\n"));

  /* Collapsed groups stay collapsed across visits. */
  function remembered(name: string): boolean {
    try { return localStorage.getItem(`fold:${name}`) !== "closed"; } catch { return true; }
  }

  function remember(name: string, isOpen: boolean) {
    try { localStorage.setItem(`fold:${name}`, isOpen ? "open" : "closed"); } catch { /* blocked */ }
  }

  const folds = $state({
    collections: remembered("collections"), tags: remembered("tags"),
    ingredients: remembered("ingredients"),
  });

  function toggle(name: keyof typeof folds) {
    folds[name] = !folds[name];
    remember(name, folds[name]);
  }

  const reveal = { duration: motion.reduced ? 0 : 160 };

  /* On a desktop with a mouse the sidebar is a rail of section icons that
     opens while hovered. Pinned, it stays open as a column of the layout;
     the choice is kept in this browser. */
  let pinned = $state((() => {
    try { return localStorage.getItem("sidebar") === "pinned"; } catch { return false; }
  })());

  function pin() {
    pinned = !pinned;
    try { localStorage.setItem("sidebar", pinned ? "pinned" : "auto"); } catch { /* blocked */ }
  }
</script>

<!-- One per section: beside its heading, or alone while the rail is collapsed. -->
{#snippet icon(name: IconName)}
  <span class="sicon"><Icon {name} /></span>
{/snippet}

<aside id="sidebar" class="side" class:open class:pinned aria-label={m.filters()}>
  <button class="pin" type="button" aria-pressed={pinned} onclick={pin}
          title={pinned ? m.side_unpin() : m.side_pin()}
          aria-label={pinned ? m.side_unpin() : m.side_pin()}>
    <Icon name="pin" size={15} />
  </button>

  <section class="sgroup">
    {@render icon("library")}
    <h2 class="slabel">{m.side_library()}</h2>
    <button class="srow" class:is-active={allActive} type="button" onclick={onall}>
      <span>{m.side_all()}</span><b>{stats ? nf.format(stats.total) : "—"}</b>
    </button>
    {#each MEDIA as kind (kind)}
      {@const n = mediaCount(kind)}
      <button class="srow sub" class:is-active={filters.media === kind} type="button"
              aria-pressed={filters.media === kind} onclick={() => onmedia(kind)}>
        <span>{MEDIA_LABEL[kind]()}</span><b>{n === null ? "—" : nf.format(n)}</b>
      </button>
    {/each}
    <button class="srow" class:is-active={recipes} type="button"
            aria-pressed={recipes} onclick={() => onview(recipes ? "library" : "recipes")}>
      <span>{m.recipes()}</span><b>{stats ? nf.format(stats.recipes) : "—"}</b>
    </button>
    {#if recipes || stats?.shopping}
      <a class="srow sub" href="/shopping" transition:slide={reveal}>
        <span>{m.shop_title()}</span><b>{stats ? nf.format(stats.shopping) : "—"}</b>
      </a>
    {/if}
    <button class="srow" class:is-active={places} type="button"
            aria-pressed={places} onclick={() => onview(places ? "library" : "places")}>
      <span>{m.places()}</span><b>{stats ? nf.format(stats.places) : "—"}</b>
    </button>
    {#if places}
      {#each PLACE_KINDS as [tag, label] (tag)}
        <button class="srow sub" class:is-active={filters.tag === tag} type="button"
                aria-pressed={filters.tag === tag} onclick={() => onfilter("tag", tag)}
                transition:slide={reveal}>
          <span>{label()}</span><b>{stats ? nf.format(stats.place_tags[tag] ?? 0) : "—"}</b>
        </button>
      {/each}
    {/if}
  </section>

  {#if recipes}
    <!-- Ingredient groups are hand-written in app/recipes/groups.py. The
         count beside "without" is what is left once it is excluded. -->
    <section class="sgroup" transition:slide={reveal}>
      {@render icon("time")}
      <h2 class="slabel">{m.side_ready_in()}</h2>
      <div class="rows">
        {#each TIMES as t (t)}
          <button class="srow" class:is-active={filters.time === t} type="button"
                  aria-pressed={filters.time === t} onclick={() => ontime(t)}>
            <span>{t < 60 ? m.time_within({ minutes: nf.format(t) }) : m.time_within_hour()}</span><b>{facets ? nf.format(timeCount(t)) : ""}</b>
          </button>
        {/each}
      </div>
    </section>

    <section class="sgroup" transition:slide={reveal}>
      {@render icon("without")}
      <h2 class="slabel">{m.side_without()}</h2>
      <div class="rows">
        {#each facets?.groups ?? [] as g (g.name)}
          <button class="srow" class:is-active={filters.without.includes(g.name)} type="button"
                  aria-pressed={filters.without.includes(g.name)}
                  onclick={() => onpick("without", g.name)}>
            <span>{m.side_no_group({ group: groupLabel(g.name).toLocaleLowerCase(locale) })}</span><b>{nf.format(facets!.total - g.count)}</b>
          </button>
        {:else}
          <p class="hint">{facets ? m.side_no_ingredients() : ""}</p>
        {/each}
      </div>
    </section>

    {#if facets?.ingredients.length}
      <section class="sgroup" transition:slide={reveal}>
        {@render icon("with")}
        <button class="slabel fold" type="button" aria-expanded={folds.ingredients}
                onclick={() => toggle("ingredients")}>
          {m.side_with()} <span class="slabel-note">{m.side_with_note()}</span>
        </button>
        {#if folds.ingredients}
          <div class="rows" transition:slide={reveal}>
            {#each facets.ingredients as i (i.name)}
              <button class="srow" class:is-active={filters.has.includes(i.name)} type="button"
                      aria-pressed={filters.has.includes(i.name)}
                      onclick={() => onpick("has", i.name)}>
                <span>{i.name}</span><b>{nf.format(i.count)}</b>
              </button>
            {/each}
          </div>
        {/if}
      </section>
    {/if}
  {/if}

  <section class="sgroup">
    {@render icon("collections")}
    <button class="slabel fold" type="button" aria-expanded={folds.collections}
            onclick={() => toggle("collections")}>
      {m.collections()}
    </button>
    {#if folds.collections}
      <div class="rows" transition:slide={reveal}>
        {#each sidebar.collections as c (c.name)}
          <button class="srow" class:is-active={filters.collection === c.name} type="button"
                  onclick={() => onfilter("collection", c.name)}>
            <span>{c.name}</span><b>{nf.format(c.count)}</b>
          </button>
        {:else}
          <p class="hint">{stats ? m.side_no_collections() : ""}</p>
        {/each}
      </div>
    {/if}
  </section>

  <section class="sgroup">
    {@render icon("tags")}
    <button class="slabel fold" type="button" aria-expanded={folds.tags}
            onclick={() => toggle("tags")}>
      {m.tags()}
    </button>
    {#if folds.tags}
      <div class="rows" transition:slide={reveal}>
        {#each used as t (t.name)}
          <button class="srow is-tag" class:is-active={filters.tag === t.name} type="button"
                  onclick={() => onfilter("tag", t.name)}>
            <span>{tagLabel(t.name)}</span><b>{nf.format(t.count)}</b>
          </button>
        {:else}
          <p class="hint">{stats ? m.side_no_tags({ command: "./hikidashi tag" }) : ""}</p>
        {/each}
      </div>
    {/if}
  </section>

  <section class="sgroup">
    {@render icon("pipeline")}
    <svelte:element this={admin ? "a" : "div"} class="pipeline-link"
                    href={admin ? "/pipeline" : undefined} title={admin ? m.pl_title() : undefined}>
      <h2 class="slabel">{m.pipeline()}</h2>
      <dl class="states">
        {#each Object.entries(stats?.states ?? {}) as [k, v] (k)}
          <div><dt>{stateLabel(k)}</dt><dd>{nf.format(v)}</dd></div>
        {:else}
          <p class="hint">{stats ? m.side_nothing_imported() : ""}</p>
        {/each}
      </dl>
      {#if admin && stats?.worker}
        <p class="worker is-{stats.worker}"><span aria-hidden="true"></span>{WORKER[stats.worker]()}</p>
      {/if}
    </svelte:element>
  </section>

  {#if coverage}<p class="hint">{coverage}</p>{/if}

  <!-- Each language in its own name, so whoever reads it can find theirs. -->
  <section class="sgroup">
    {@render icon("language")}
    <h2 class="slabel">{m.language()}</h2>
    <div class="langs">
      {#each locales as l (l)}
        <button class="lang" type="button" lang={l} aria-pressed={l === locale}
                onclick={() => { if (l !== locale) chooseLocale(l); }}>{LANGUAGES[l]}</button>
      {/each}
    </div>
  </section>
</aside>

<style>
  .side {
    position: sticky;
    top: var(--topbar-h);
    max-height: calc(100vh - var(--topbar-h));
    overflow-y: auto;
    padding: 18px 14px;
    display: flex;
    flex-direction: column;
    gap: 22px;
    border-right: 1px solid var(--rule);
    background: var(--surface);
    container: side / inline-size;
  }

  /* The section's icon beside its heading; everything below spans both. */
  .sgroup {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr);
    align-items: center;
    gap: 6px 8px;
  }

  .sgroup > * { grid-column: 1 / -1; }
  .sgroup > .sicon { grid-column: 1; display: grid; place-items: center; color: var(--muted); }
  .sgroup > .sicon + * { grid-column: 2; }
  /* The pipeline's heading is inside its link, above the counts. */
  .sgroup > .sicon:has(+ .pipeline-link) { align-self: start; margin-top: 2px; }
  .sgroup:has(.is-active) > .sicon { color: var(--accent); }

  .pin { display: none; }

  /* ---------- the rail ---------- */
  /* Desktop with a mouse: collapsed to icons until hovered, or reached from
     the keyboard, or pinned. Open, it lies over the grid rather than moving
     it; the layout keeps a narrow column unless pinned. A short delay each
     way keeps a passing pointer from flicking it open and shut. */
  @media (min-width: 701px) and (hover: hover) {
    /* Always above the grid, not only while hovered: closing takes a moment,
       and the cards must not show through the narrowing sidebar. */
    .side {
      z-index: 4;
      width: 64px;
      min-height: calc(100vh - var(--topbar-h));
      overflow-x: hidden;
      transition: width 220ms var(--ease-out) 260ms, box-shadow 220ms ease 260ms;
    }

    .side:is(:hover, :has(:focus-visible)), .side.pinned { width: 220px; }
    .side:is(:hover, :has(:focus-visible)) { transition-delay: 90ms; }
    .side:not(.pinned):is(:hover, :has(:focus-visible)) {
      box-shadow: 14px 0 32px -20px rgb(0 0 0 / 55%);
    }

    .pin {
      display: grid;
      place-items: center;
      align-self: flex-end;
      width: 28px;
      height: 28px;
      margin: -10px -6px -14px 0;
      padding: 0;
      background: none;
      border: 0;
      border-radius: var(--radius);
      color: var(--muted);
      cursor: pointer;
      transition: background-color 120ms ease, color 120ms ease, transform 160ms var(--ease-out);
    }

    .pin:hover { background: var(--surface-2); color: var(--ink); }
    .pin:not([aria-pressed="true"]) :global(svg:not(.pixel)) { transform: rotate(35deg); }
    .pin[aria-pressed="true"] { color: var(--accent); }
    .pin[aria-pressed="true"] :global(svg) { fill: currentColor; }
  }

  /* Collapsed: each section shows its icon and nothing else. */
  @container side (width < 140px) {
    .sgroup > :not(.sicon), .side > .hint, .pin { display: none; }
    .sgroup { display: flex; flex-direction: column; align-items: center; }

    .sgroup > .sicon {
      width: 32px;
      height: 32px;
      margin: 0;
      border-radius: var(--radius);
    }

    .sicon :global(svg:not(.pixel)) { width: 18px; height: 18px; stroke-width: 1.4; }

    /* Lit while a filter in that section is on. */
    .sgroup:has(.is-active) > .sicon { background: var(--accent-soft); }
  }

  /* Opening: the content keeps its full width and is uncovered, rather than
     reflowing as the rail widens. */
  @container side (width >= 140px) {
    .sgroup, .side > .hint { min-width: 192px; }
  }

  .slabel {
    margin: 0;
    padding: 0;
    font-family: var(--display);
    font-size: 13px;
    font-weight: 600;
    color: var(--muted);
    text-align: left;
  }

  .slabel-note { font-weight: 400; opacity: 0.65; }

  /* Collapsible groups. The marker is drawn so it matches the label and turns
     with the state. */
  .fold { background: none; border: 0; cursor: pointer; user-select: none; }
  .fold::before {
    content: "▸";
    display: inline-block;
    width: 1.1em;
    transition: transform 140ms ease;
  }
  .fold[aria-expanded="true"]::before { transform: rotate(90deg); }
  .fold:hover { color: var(--ink-2); }

  .rows { display: flex; flex-direction: column; gap: 2px; }

  .srow {
    display: flex;
    justify-content: space-between;
    gap: 8px;
    width: 100%;
    padding: 6px 10px;
    text-align: left;
    background: none;
    border: 0;
    border-radius: var(--radius);
    color: var(--ink-2);
    cursor: pointer;
    transition: background-color 120ms ease, color 120ms ease;
  }

  /* Videos and Images sit under All saves, as part of it. */
  .srow.sub { padding-left: 20px; }
  a.srow { text-decoration: none; }

  .srow span { min-width: 0; overflow-wrap: anywhere; }
  .srow:hover { background: var(--surface-2); }
  .srow.is-active { background: var(--surface-2); color: var(--ink); font-weight: 700; }

  .srow b {
    font-size: 12.5px;
    font-weight: 500;
    color: var(--muted);
    font-variant-numeric: tabular-nums;
  }

  .srow.is-active b { color: var(--ink); }

  .pipeline-link {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin: -6px;
    padding: 6px;
    border-radius: var(--radius);
    color: inherit;
    text-decoration: none;
  }
  a.pipeline-link:hover { background: var(--surface-2); }

  .worker { display: flex; align-items: center; gap: 6px; margin: 0; font-size: 12px; color: var(--muted); }
  .worker span { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
  .worker.is-running { color: var(--ok); }
  .worker.is-problem { color: var(--warn); }
  .worker.is-lost { color: var(--risk); }
  a.pipeline-link:hover .slabel { color: var(--accent); }

  /* Inset on the right so the counts line up with the rows' counts above,
     rather than running to the sidebar's edge. */
  .states { margin: 0; padding-right: 10px; display: flex; flex-direction: column; gap: 4px; }

  .states div {
    display: flex;
    justify-content: space-between;
    gap: 8px;
    font-size: 13px;
    color: var(--ink-2);
  }

  .states dd { margin: 0; color: var(--muted); font-variant-numeric: tabular-nums; }

  .langs {
    display: flex;
    gap: 2px;
    padding: 3px;
    align-self: flex-start;
    background: var(--surface-2);
    border-radius: var(--radius-sm);
  }

  .lang {
    padding: 3px 10px;
    font-size: 12.5px;
    border: 0;
    border-radius: var(--radius-sm);
    background: none;
    color: var(--ink-2);
    cursor: pointer;
    transition: background-color 140ms ease, color 140ms ease;
  }

  .lang:hover { color: var(--ink); }
  .lang[aria-pressed="true"] {
    background: var(--surface);
    color: var(--ink);
    font-weight: 600;
    box-shadow: 0 1px 2px rgb(0 0 0 / 12%);
    cursor: default;
  }

  /* Phone width: a drawer from the left, under the top bar. */
  @media (max-width: 700px) {
    .side {
      position: fixed;
      z-index: 4;
      left: 0;
      bottom: 0;
      width: min(300px, 86vw);
      max-height: none;
      box-shadow: 12px 0 40px -20px rgb(0 0 0 / 50%);
      transform: translateX(-104%);
      visibility: hidden;
      transition: transform 260ms var(--ease-out), visibility 0s linear 260ms;
    }

    .side.open {
      transform: none;
      visibility: visible;
      transition: transform 260ms var(--ease-out), visibility 0s;
    }
  }
</style>
