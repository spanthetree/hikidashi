<script lang="ts">
  import { fade } from "svelte/transition";
  import { filterUrl, isOn, type Filters } from "#lib/filters.js";
  import { duration, nf, profileUrl, savedDate, tint } from "#lib/format.js";
  import { tagLabel } from "#lib/i18n.js";
  import { motion, receive, send } from "#lib/motion.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { FilterKind, Item } from "#lib/types.js";

  let { item, href, filters, active = false }: {
    item: Item;
    href: string;
    /** What the grid is showing, so each chip can link to its own filter. */
    filters: Filters;
    /** This reel is open in the sheet; its picture has moved over there. */
    active?: boolean;
  } = $props();

  /* Why a search returned this reel, for diagnosing a bad result without
     reading SQL. Only in `npm run dev`, or with localStorage "explain" set to
     "1" on a built app. */
  const explain = import.meta.env.DEV || (() => {
    try { return localStorage.getItem("explain") === "1"; } catch { return false; }
  })();
  const why = $derived(explain && item.match ? [
    ...item.match.match,
    ...(item.match.similarity != null ? [item.match.similarity.toFixed(2)] : []),
  ].join(" · ") : "");

  // In the Recipes view the card leads with the recipe, not the caption.
  const recipeView = $derived(filters.view === "recipes");

  const on = (kind: FilterKind, value: string) => isOn(filters, kind, value);

  /* A few hashtags, not all: a reel can carry thirty. Those the grid is
     filtered by always make the cut, so every card shows why it is here. */
  const HASHTAGS = 3;
  const hashtags = $derived(
    [...item.hashtags].sort((a, b) => Number(on("hashtag", b)) - Number(on("hashtag", a)))
      .slice(0, HASHTAGS),
  );
  const chipTitle = (kind: FilterKind, value: string, label = value) =>
    on(kind, value) ? m.chip_stop({ value: label }) : m.chip_only({ value: label });

  let loaded = $state(false);
  let broken = $state(false);
  let previewing = $state(false);
  let timer: ReturnType<typeof setTimeout> | undefined;

  // Captions ship in the export, so there is real text to show from day one.
  const caption = $derived((item.caption ?? "").replace(/\s+/g, " ").trim());
  const len = $derived(duration(item.duration));
  const showImage = $derived(Boolean(item.thumb) && !broken);
  const recipe = $derived(item.recipe);
  const recipeFacts = $derived(recipe ? [
    recipe.time_minutes ? m.minutes({ minutes: nf.format(recipe.time_minutes) }) : null,
    recipe.ingredients ? m.ingredient_count({ count: recipe.ingredients }) : null,
  ].filter(Boolean).join(" · ") : "");

  /* Hover to preview: the muted mp4 plays in place of the thumbnail. A short
     delay keeps a mouse passing over the grid from starting forty downloads. */
  function enter() {
    if (!item.video || !motion.canHover || motion.reduced) return;
    clearTimeout(timer);
    timer = setTimeout(() => (previewing = true), 260);
  }

  function leave() {
    clearTimeout(timer);
    previewing = false;
  }

  $effect(() => {
    if (active) leave();
    return () => clearTimeout(timer);
  });

  // Coming back from the sheet, the picture shrinks from where the sheet's
  // media column is, while that is still on screen closing.
  const sheetMedia = () =>
    document.querySelector(`.sheet-media[data-shortcode="${CSS.escape(item.shortcode)}"]`);

  const play = (video: HTMLVideoElement) => {
    video.muted = true;               // the attribute alone loses to some autoplay policies
    video.play().catch(() => {});
    return () => { video.pause(); video.removeAttribute("src"); video.load(); };
  };
</script>

<article class="card" data-card={item.shortcode}>
  <a class="thumb" {href} data-sveltekit-noscroll
     title={item.caption ? item.caption.slice(0, 120) : item.shortcode}
     onmouseenter={enter} onmouseleave={leave} onfocus={enter} onblur={leave}>
    {#if !active}
      <div class="visual" in:receive={{ from: sheetMedia }} out:send
           style:background={tint(item.shortcode)}>
        {#if showImage}
          <img src={item.thumb} alt="" loading="lazy" decoding="async" class:loaded
               onload={() => (loaded = true)} onerror={() => (broken = true)}>
          {#if !loaded}<span class="shimmer" aria-hidden="true"></span>{/if}
        {/if}
        {#if previewing}
          <video src={item.video} muted loop playsinline preload="auto" {@attach play}
                 transition:fade={{ duration: 160 }}></video>
        {/if}
      </div>
    {/if}
    {#if item.state === "failed"}<span class="badge is-failed">{m.badge_unavailable()}</span>
    {:else if recipe && !recipeView}<span class="badge is-recipe">{m.badge_recipe()}</span>
    {:else if recipe?.incomplete && recipeView}<span class="badge" title={m.recipe_incomplete_check()}>{m.badge_incomplete()}</span>{/if}
    {#if why}<span class="why" title={m.card_why()}>{why}</span>{/if}
    {#if len}<span class="dur">{len}</span>{/if}
    {#if !showImage}<span class="code">{item.shortcode}</span>{/if}
  </a>
  <span class="cmeta">
    {#if item.author}
      <a class="mention" href={profileUrl(item.author)} target="_blank"
         rel="noopener noreferrer">@{item.author}</a>
    {:else}{m.card_unknown_author()}{/if}
  </span>
  {#if recipeView && recipe}
    <p class="ctitle">{recipe.title || caption || m.recipe_untitled()}</p>
    {#if recipeFacts}<span class="cdate">{recipeFacts}</span>{/if}
  {:else if caption}<p class="ccap">{caption}</p>{/if}
  <span class="cdate">{savedDate(item.saved_at)}</span>
  {#if item.tag || hashtags.length || item.collections.length}
    <div class="chips">
      {#if item.tag}
        <a class="is-tagchip" href={filterUrl(filters, "tag", item.tag)} data-sveltekit-keepfocus
           title={chipTitle("tag", item.tag, tagLabel(item.tag))}
           aria-current={on("tag", item.tag) || undefined}>{tagLabel(item.tag)}</a>
      {/if}
      {#each hashtags as h (h)}
        <a class="is-hash" href={filterUrl(filters, "hashtag", h)} data-sveltekit-keepfocus
           title={chipTitle("hashtag", h, `#${h}`)} aria-current={on("hashtag", h) || undefined}>#{h}</a>
      {/each}
      {#each item.collections as c (c)}
        <a href={filterUrl(filters, "collection", c)} data-sveltekit-keepfocus
           title={chipTitle("collection", c)} aria-current={on("collection", c) || undefined}>{c}</a>
      {/each}
    </div>
  {/if}
</article>

<style>
  .card { display: flex; flex-direction: column; gap: 6px; min-width: 0; }

  .thumb {
    display: block;
    position: relative;
    aspect-ratio: 3 / 4;
    border-radius: var(--radius);
    overflow: hidden;
    text-decoration: none;
    /* What shows while the picture is away in the sheet: a placeholder, not
       a hole in the grid. */
    background: var(--surface-2);
    box-shadow: inset 0 0 0 1px rgb(0 0 0 / 6%);
    transition: box-shadow 140ms ease, transform 200ms var(--ease-out);
  }

  .thumb:hover { transform: translateY(-2px); }

  /* Hover rings the reel in the theme's --brand gradient. The
     mask keeps only the border band of a gradient-filled box. */
  .thumb::after {
    content: "";
    position: absolute;
    inset: 0;
    z-index: 2;
    padding: 3px;
    border-radius: inherit;
    background: var(--brand);
    -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
    -webkit-mask-composite: xor;
    mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
    mask-composite: exclude;
    opacity: 0;
    pointer-events: none;
    transition: opacity 140ms ease;
  }

  .thumb:hover::after { opacity: 1; }

  .visual { position: absolute; inset: 0; }

  img, video {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
  }

  img { opacity: 0; transition: opacity 260ms ease; }
  img.loaded { opacity: 1; }

  /* Skeleton: the per-reel tint with a soft sheen passing over it. */
  .shimmer {
    position: absolute;
    inset: 0;
    background: linear-gradient(100deg, transparent 30%, rgb(255 255 255 / 18%) 50%, transparent 70%);
    background-size: 220% 100%;
    animation: sheen 1.4s ease-in-out infinite;
  }

  @keyframes sheen {
    from { background-position: 120% 0; }
    to { background-position: -120% 0; }
  }

  .dur, .badge, .code, .why {
    position: absolute;
    z-index: 1;
    font-size: 10px;
    font-weight: 600;
    color: #fff;
  }

  .code { font-family: var(--mono); font-weight: 400; }

  .dur {
    right: 6px;
    bottom: 6px;
    padding: 1px 5px;
    border-radius: var(--radius-sm);
    background: rgb(0 0 0 / 60%);
  }

  .why {
    left: 6px;
    bottom: 6px;
    max-width: calc(100% - 52px);
    padding: 1px 5px;
    border-radius: var(--radius-sm);
    background: rgb(0 149 246 / 85%);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .badge {
    top: 6px;
    left: 6px;
    padding: 2px 6px;
    border-radius: var(--radius-sm);
    text-transform: capitalize;
    background: rgb(0 0 0 / 55%);
  }

  .badge.is-failed { background: rgb(180 51 40 / 85%); }
  .badge.is-recipe { background: rgb(13 107 74 / 85%); }

  .code {
    inset: auto 6px 6px;
    font-size: 10px;
    text-shadow: 0 1px 3px rgb(0 0 0 / 60%);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .cmeta { font-size: 13px; font-weight: 600; color: var(--ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .cmeta .mention { color: inherit; }
  .cmeta .mention:hover { color: var(--accent); }
  .cdate { font-size: 11.5px; color: var(--muted); }

  .ccap {
    margin: 0;
    font-size: 12px;
    color: var(--ink-2);
    line-height: 1.35;
    display: -webkit-box;
    -webkit-line-clamp: 3;
    line-clamp: 3;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .ctitle {
    margin: 0;
    font-size: 13px;
    font-weight: 600;
    line-height: 1.3;
    color: var(--ink);
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .chips { display: flex; flex-wrap: wrap; gap: 3px; }

  .chips a {
    font-size: 11px;
    padding: 2px 8px;
    border-radius: var(--radius-pill);
    background: var(--surface-2);
    color: var(--ink-2);
    text-decoration: none;
    box-shadow: inset 0 0 0 1px transparent;
    transition: background-color 120ms ease, color 120ms ease, box-shadow 120ms ease;
  }

  .chips a:hover { color: var(--ink); box-shadow: inset 0 0 0 1px var(--rule); }

  .chips .is-hash { background: none; color: var(--muted); padding-inline: 2px; }

  /* Ours vs Instagram's, at a glance. */
  .chips .is-tagchip { background: var(--accent-soft); color: var(--accent); }
  .chips .is-tagchip:hover { color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); }

  /* The filter the grid is showing, on every card that carries it. */
  .chips a[aria-current] { background: var(--ink-2); color: var(--surface); font-weight: 600; }
  .chips .is-tagchip[aria-current] { background: var(--accent-fill); color: var(--on-accent); }
</style>
