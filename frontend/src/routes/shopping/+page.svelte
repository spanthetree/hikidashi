<script lang="ts">
  import { onMount } from "svelte";
  import { clearList, dropFromList, getShopping, tickLine } from "#lib/api.js";
  import Icon from "#lib/components/Icon.svelte";
  import Notice from "#lib/components/Notice.svelte";
  import Topbar from "#lib/components/Topbar.svelte";
  import { copyFailure, copyText } from "#lib/clipboard.js";
  import { list, nf, tint } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import { asText, byAisle } from "#lib/shopping.js";
  import type { Aisle, NoticeState, ShoppingLine, ShoppingList } from "#lib/types.js";

  /* Several recipes' ingredients as one list, by aisle, to tick off in the
     shop. Ticks are saved as you go, so the list on your phone in the shop is
     the one you made at home. */

  let shop = $state<ShoppingList | null>(null);
  let notice = $state<NoticeState>(null);
  let confirming = $state(false);
  let copied = $state(false);

  onMount(async () => {
    try {
      shop = await getShopping();
    } catch (err) {
      notice = { text: m.shop_failed({ error: (err as Error).message }), kind: "error" };
    }
  });

  const AISLE: Record<Aisle, () => string> = {
    produce: m.aisle_produce, meat: m.aisle_meat, dairy: m.aisle_dairy, bakery: m.aisle_bakery,
    pantry: m.aisle_pantry, spices: m.aisle_spices, condiments: m.aisle_condiments,
    frozen: m.aisle_frozen, drinks: m.aisle_drinks, other: m.aisle_other,
  };

  const grouped = $derived(shop ? byAisle(shop.lines) : { groups: [], staples: [] });
  const total = $derived(shop?.lines.filter((l) => !l.staple).length ?? 0);
  const left = $derived(shop?.lines.filter((l) => !l.staple && !l.ticked).length ?? 0);
  const titles = $derived(new Map(shop?.recipes.map((r) => [r.shortcode, r.title || m.recipe_untitled()])));

  /* Folded unless opened: salt and oil are rarely what you went out for. */
  let staplesOpen = $state((() => {
    try { return localStorage.getItem("fold:staples") === "open"; } catch { return false; }
  })());

  function foldStaples(e: Event) {
    staplesOpen = (e.currentTarget as HTMLDetailsElement).open;
    try { localStorage.setItem("fold:staples", staplesOpen ? "open" : "closed"); } catch { /* blocked */ }
  }

  async function change(call: () => Promise<ShoppingList>) {
    try {
      shop = await call();
    } catch (err) {
      notice = { text: m.shop_failed({ error: (err as Error).message }), kind: "error" };
    }
  }

  /* Ticked here at once, and saved behind; put back if the save fails. */
  async function tick(line: ShoppingLine) {
    const ticked = !line.ticked;
    line.ticked = ticked;
    try {
      await tickLine(line.key, ticked);
    } catch (err) {
      line.ticked = !ticked;
      notice = { text: m.shop_failed({ error: (err as Error).message }), kind: "error" };
    }
  }

  async function clear() {
    confirming = false;
    await change(clearList);
  }

  const text = () => asText(m.shop_title(), shop?.lines ?? [], (a) => AISLE[a](), m.shop_staples());
  const canShare = typeof navigator !== "undefined" && "share" in navigator;

  async function share() {
    try {
      await navigator.share({ title: m.shop_title(), text: text() });
    } catch { /* cancelled */ }
  }

  async function copy() {
    const how = await copyText(text());
    if (how !== "copied") {
      notice = { text: copyFailure(how), kind: "error" };
      return;
    }
    copied = true;
    setTimeout(() => (copied = false), 2000);
  }
</script>

<svelte:head><title>{m.shop_title()} · Hikidashi</title></svelte:head>

<Topbar />

<main class="page shopping">
  <a class="back" href="/?view=recipes">{m.shop_back()}</a>
  <header>
    <h1>{m.shop_title()}</h1>
    {#if shop?.recipes.length}
      <p class="lede">
        {m.shop_recipes({ count: shop.recipes.length })} · {m.shop_left({ left: nf.format(left), total: nf.format(total) })}
      </p>
    {/if}
  </header>

  <Notice bind:notice />

  {#if shop && !shop.recipes.length}
    <div class="empty">
      <h3>{m.shop_empty_title()}</h3>
      <p>{m.shop_empty_body()}</p>
      <a class="btn primary" href="/?view=recipes">{m.shop_go_recipes()}</a>
    </div>
  {:else if shop}
    <div class="s-actions">
      {#if canShare}
        <button class="btn primary" type="button" onclick={share}>{m.shop_share()}</button>
      {/if}
      <button class="btn" type="button" aria-live="polite" onclick={copy}>{copied ? m.shop_copied() : m.shop_copy()}</button>
      {#if !confirming}
        <button class="btn quiet" type="button" onclick={() => (confirming = true)}>{m.shop_clear()}</button>
      {/if}
    </div>
    {#if confirming}
      <div class="s-confirm" role="group" aria-label={m.shop_clear()}>
        <p>{m.shop_clear_confirm()}</p>
        <button class="btn danger" type="button" onclick={clear}>{m.shop_clear_yes()}</button>
        <button class="btn" type="button" onclick={() => (confirming = false)}>{m.cancel()}</button>
      </div>
    {/if}

    <ul class="s-recipes">
      {#each shop.recipes as r (r.shortcode)}
        <li>
          <a href="/item/{encodeURIComponent(r.shortcode)}?view=recipes">
            <span class="s-thumb" style:background={tint(r.shortcode)}>
              {#if r.thumb}<img src={r.thumb} alt="" loading="lazy">{/if}
            </span>
            <span class="s-name">{r.title || m.recipe_untitled()}</span>
          </a>
          <button type="button" class="s-drop" aria-label={m.shop_remove_recipe({ title: r.title || m.recipe_untitled() })}
                  title={m.shop_remove_recipe({ title: r.title || m.recipe_untitled() })}
                  onclick={() => change(() => dropFromList(r.shortcode))}>
            <Icon name="close" size={10} />
          </button>
        </li>
      {/each}
    </ul>

    {#snippet row(line: ShoppingLine)}
      <li>
        <label class:done={line.ticked}>
          <input type="checkbox" checked={line.ticked} onchange={() => tick(line)}>
          <span class="s-line">
            <span class="s-item">{line.name}</span>
            {#if line.amounts.length}<span class="s-amt">{line.amounts.join(" + ")}</span>{/if}
          </span>
          {#if line.recipes.length > 1}
            <span class="s-n" title={m.shop_in_recipes({ recipes: list(line.recipes.map((c) => titles.get(c) ?? c)) })}>
              ×{nf.format(line.recipes.length)}
            </span>
          {/if}
        </label>
      </li>
    {/snippet}

    {#each grouped.groups as g (g.aisle)}
      <section class="s-aisle">
        <h2>{AISLE[g.aisle]()}</h2>
        <ul class="s-lines">{#each g.lines as line (line.key)}{@render row(line)}{/each}</ul>
      </section>
    {/each}

    {#if grouped.staples.length}
      <details class="s-aisle s-staples" open={staplesOpen} ontoggle={foldStaples}>
        <summary>
          <h2>{m.shop_staples()} <span>{nf.format(grouped.staples.length)}</span></h2>
          <p>{m.shop_staples_note()}</p>
        </summary>
        <ul class="s-lines">{#each grouped.staples as line (line.key)}{@render row(line)}{/each}</ul>
      </details>
    {/if}
  {/if}
</main>

<style>
  .shopping { max-width: 640px; }
  h2 { margin: 0; font-size: 13px; font-family: var(--display); font-weight: 600; color: var(--muted);
       text-transform: uppercase; letter-spacing: 0.06em; }

  .empty { display: flex; flex-direction: column; gap: 10px; align-items: flex-start; padding: 24px 0; }
  .empty h3 { margin: 0; font-size: 17px; }
  .empty p { margin: 0; color: var(--ink-2); font-size: 14px; }

  .s-actions, .s-confirm { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  .s-confirm p { margin: 0; font-size: 14px; flex-basis: 100%; }
  .btn.quiet { border-color: transparent; background: none; color: var(--muted); }
  .btn.quiet:hover { color: var(--risk); }
  .btn.danger { background: var(--risk); color: var(--on-accent); }

  .s-recipes {
    display: flex;
    gap: 8px;
    margin: 0;
    padding: 2px 0 6px;
    list-style: none;
    overflow-x: auto;
  }
  .s-recipes li {
    flex: none;
    display: flex;
    align-items: center;
    gap: 2px;
    max-width: 240px;
    padding: 4px 4px 4px 4px;
    border-radius: var(--radius-pill);
    background: var(--surface-2);
  }
  .s-recipes a { display: flex; align-items: center; gap: 8px; min-width: 0; color: var(--ink); text-decoration: none; }
  .s-thumb { flex: none; width: 30px; height: 30px; border-radius: 50%; overflow: hidden; }
  .s-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .s-name { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .s-drop {
    flex: none;
    display: grid;
    place-items: center;
    width: 30px;
    height: 30px;
    border: 0;
    border-radius: 50%;
    background: none;
    color: var(--muted);
    cursor: pointer;
  }
  .s-drop:hover { color: var(--risk); background: var(--surface); }

  .s-aisle { display: flex; flex-direction: column; gap: 4px; }
  .s-lines { margin: 0; padding: 0; list-style: none; }
  .s-lines label {
    display: flex;
    align-items: center;
    gap: 14px;
    min-height: 52px;
    padding: 6px 2px;
    border-bottom: 1px solid var(--rule);
    cursor: pointer;
  }
  .s-lines input { flex: none; width: 22px; height: 22px; accent-color: var(--accent); }
  .s-line { flex: 1; min-width: 0; display: flex; flex-wrap: wrap; gap: 2px 10px; align-items: baseline; }
  .s-item { font-size: 16px; }
  .s-amt { font-size: 14px; color: var(--ink-2); font-variant-numeric: tabular-nums; }
  .s-n { flex: none; font-size: 12px; color: var(--muted); }
  .done .s-item, .done .s-amt { color: var(--muted); text-decoration: line-through; }

  .s-staples summary { list-style: none; cursor: pointer; display: flex; flex-direction: column; gap: 2px; }
  .s-staples summary::-webkit-details-marker { display: none; }
  .s-staples summary h2::before { content: "▸ "; }
  .s-staples[open] summary h2::before { content: "▾ "; }
  .s-staples summary span { font-weight: 400; }
  .s-staples summary p { margin: 0; font-size: 12px; color: var(--muted); }
</style>
