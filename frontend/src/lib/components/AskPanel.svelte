<script lang="ts">
  /* Ask your library (#45): the search box's text as a question, answered
     from your saves by your AI model, citing the reels it used. Each [n]
     opens that reel, at the moment it was said when that is known. */
  import { askLibrary } from "#lib/api.js";
  import { tint } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { Answer } from "#lib/types.js";
  import Icon from "./Icon.svelte";

  let { q, search, onclose }: { q: string; search: string; onclose: () => void } = $props();

  let answer = $state<Answer | null>(null);
  let error = $state<string | null>(null);

  $effect(() => {
    const ctrl = new AbortController();
    answer = null;
    error = null;
    askLibrary(q, ctrl.signal)
      .then((a) => (answer = a))
      .catch((err) => { if (!ctrl.signal.aborted) error = (err as Error).message; });
    return () => ctrl.abort();
  });

  function href(code: string, atMs: number | null) {
    const s = new URLSearchParams(search);
    if (atMs != null) s.set("t", (atMs / 1000).toFixed(1));
    const qs = s.toString();
    return `/item/${encodeURIComponent(code)}${qs ? `?${qs}` : ""}`;
  }

  const cite = $derived(new Map((answer?.citations ?? []).map((c) => [c.n, c])));
  const parts = $derived((answer?.answer ?? "").split(/(\[\d+\])/));
</script>

<section class="ask" aria-live="polite" aria-label={m.ask_heading()}>
  <header>
    <h3><Icon name="ask" size={14} /> {m.ask_heading()}</h3>
    <button type="button" class="x" aria-label={m.ask_close()} title={m.ask_close()}
            onclick={onclose}><Icon name="close" size={10} /></button>
  </header>
  {#if error}
    <p class="quiet">{m.ask_failed({ error })}</p>
  {:else if !answer}
    <p class="quiet thinking">{m.ask_thinking()}</p>
  {:else if !answer.found}
    <p class="quiet">{m.ask_nothing()}</p>
  {:else}
    <p class="text">
      {#each parts as part, i (i)}
        {@const n = /^\[(\d+)\]$/.exec(part)?.[1]}
        {@const c = n ? cite.get(Number(n)) : undefined}
        {#if c}<a class="cite" href={href(c.shortcode, c.at_ms)} data-sveltekit-noscroll>{c.n}</a>{:else}{part}{/if}
      {/each}
    </p>
    <div class="row">
      {#each answer.citations as c (c.n)}
        {@const item = answer.items.find((i) => i.shortcode === c.shortcode)}
        <a href={href(c.shortcode, c.at_ms)} data-sveltekit-noscroll style:--tint={tint(c.shortcode)}
           title={item?.caption ?? c.shortcode}>
          <span class="pic">{#if item?.thumb}<img src={item.thumb} alt="" loading="lazy"
              onerror={(e) => (e.currentTarget as HTMLElement).remove()}>{/if}<b>{c.n}</b></span>
          <span class="who">{item?.author ? `@${item.author}` : c.shortcode}</span>
        </a>
      {/each}
    </div>
    <p class="by">{m.ask_by({ model: answer.model })}</p>
  {/if}
</section>

<style>
  .ask {
    display: flex; flex-direction: column; gap: 10px; margin-bottom: 18px; padding: 14px 16px;
    border-radius: var(--radius, 10px); background: var(--surface); border: 1px solid var(--line, var(--surface-2));
    max-width: 72ch;
  }
  header { display: flex; align-items: center; justify-content: space-between; }
  h3 { margin: 0; display: flex; align-items: center; gap: 6px; font-family: var(--display); font-size: 14px; }
  .x { border: 0; background: none; color: var(--muted); cursor: pointer; padding: 4px; display: inline-flex; }
  .x:hover { color: var(--ink); }
  .text { margin: 0; font-size: 14.5px; line-height: 1.6; color: var(--ink); white-space: pre-wrap; }
  .cite {
    display: inline-block; min-width: 1.3em; padding: 0 4px; margin: 0 1px; border-radius: 4px;
    font-size: 11px; font-weight: 700; text-align: center; text-decoration: none; vertical-align: 2px;
    background: var(--accent-soft); color: var(--accent);
  }
  .quiet { margin: 0; color: var(--muted); font-size: 14px; }
  .thinking { animation: pulse 1.4s ease-in-out infinite; }
  @keyframes pulse { 50% { opacity: 0.45; } }
  @media (prefers-reduced-motion: reduce) { .thinking { animation: none; } }
  .row { display: flex; gap: 10px; overflow-x: auto; padding-bottom: 4px; }
  .row a { flex: 0 0 72px; display: flex; flex-direction: column; gap: 3px; text-decoration: none; color: var(--ink-2); font-size: 11px; }
  .pic { position: relative; display: block; width: 72px; aspect-ratio: 4 / 5; border-radius: 6px; overflow: hidden; background: var(--tint); }
  .pic img { width: 100%; height: 100%; object-fit: cover; }
  .pic b { position: absolute; left: 4px; top: 4px; font-size: 10px; padding: 1px 5px; border-radius: 4px; background: rgb(0 0 0 / 0.6); color: #fff; }
  .who { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .by { margin: 0; font-size: 11.5px; color: var(--muted); }
</style>
