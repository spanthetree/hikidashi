<script lang="ts" module>
  /* A button that opens a list of downloads: what is on screen, or one reel,
     in each format another tool reads. A choice is a link the browser saves,
     or for "copy", something to do. */
  export interface Choice {
    label: string;
    hint?: string;
    href?: string;
    onclick?: () => void | Promise<void>;
    /** Stay open after, to show what it did: "Copied". */
    keep?: boolean;
  }
</script>

<script lang="ts">
  import { m } from "$lib/paraglide/messages";
  import Icon from "./Icon.svelte";

  let { groups, label = m.export_button(), align = "end", up = false, small = true }: {
    groups: { title: string; choices: Choice[] }[];
    label?: string;
    /** Which edge of the button the list lines up with. */
    align?: "start" | "end";
    /** Open above the button, at the foot of a sheet. */
    up?: boolean;
    small?: boolean;
  } = $props();

  let open = $state(false);
  let root = $state<HTMLElement>();
  let list = $state<HTMLElement>();
  let flip = $state(false);   // the other edge, as the asked-for one does not fit

  /* The box the list must stay inside: the nearest ancestor that clips, such
     as the sheet's panel, else the window. */
  function bounds(el: HTMLElement): { left: number; right: number } {
    for (let p = el.parentElement; p; p = p.parentElement) {
      if (getComputedStyle(p).overflowX !== "visible") return p.getBoundingClientRect();
    }
    return { left: 0, right: innerWidth };
  }

  // Measured as asked for: flip is false again from when it last closed.
  $effect(() => {
    if (!open || !list || !root) {
      flip = false;
      return;
    }
    const r = list.getBoundingClientRect();
    const b = bounds(root);
    flip = r.left < b.left + 8 || r.right > b.right - 8;
  });

  const side = $derived(flip ? (align === "start" ? "end" : "start") : align);

  function outside(e: PointerEvent) {
    if (open && root && !root.contains(e.target as Node)) open = false;
  }

  function key(e: KeyboardEvent) {
    if (open && e.key === "Escape") {
      // Not the sheet's Escape too: that would close the sheet under it.
      e.preventDefault();
      e.stopPropagation();
      open = false;
      root?.querySelector<HTMLButtonElement>(".x-toggle")?.focus();
    }
  }

  async function choose(c: Choice) {
    await c.onclick?.();
    if (!c.keep) open = false;
  }
</script>

<svelte:window onpointerdown={outside} onkeydown={key} />

<div class="x" bind:this={root}>
  <button type="button" class="btn x-toggle" class:sm={small} aria-expanded={open}
          aria-haspopup="true" onclick={() => (open = !open)}>
    <Icon name="export" size={14} /> {label}
  </button>
  {#if open}
    <div class="x-list" class:start={side === "start"} class:up bind:this={list}>
      {#each groups.filter((g) => g.choices.length) as g (g.title)}
        <div class="x-group">
          <span class="x-title">{g.title}</span>
          {#each g.choices as c, i (i)}
            {#if c.href}
              <a class="x-choice" href={c.href} download onclick={() => (open = false)}>
                {c.label}{#if c.hint}<small>{c.hint}</small>{/if}
              </a>
            {:else}
              <button type="button" class="x-choice" aria-live={c.keep ? "polite" : undefined}
                      onclick={() => choose(c)}>
                {c.label}{#if c.hint}<small>{c.hint}</small>{/if}
              </button>
            {/if}
          {/each}
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .x { position: relative; display: inline-block; }

  .x-toggle { display: inline-flex; align-items: center; gap: 6px; }

  .x-list {
    position: absolute;
    z-index: 30;
    top: calc(100% + 6px);
    right: 0;
    width: min(280px, calc(100vw - 32px));
    max-height: min(70dvh, 520px);
    overflow-y: auto;
    padding: 6px;
    display: flex;
    flex-direction: column;
    gap: 4px;
    color: var(--ink);
    background: var(--surface);
    border: 1px solid var(--rule);
    border-radius: var(--radius-lg);
    box-shadow: 0 12px 40px -12px rgb(0 0 0 / 35%);
    animation: drop 160ms var(--ease-out);
  }

  .x-list.start { right: auto; left: 0; }
  .x-list.up { top: auto; bottom: calc(100% + 6px); }

  @keyframes drop { from { opacity: 0; transform: translateY(-4px); } }

  .x-group { display: flex; flex-direction: column; }
  .x-group + .x-group { border-top: 1px solid var(--rule); padding-top: 4px; }

  .x-title {
    padding: 6px 10px 4px;
    font-family: var(--display);
    font-size: 12px;
    font-weight: 600;
    color: var(--muted);
  }

  .x-choice {
    display: flex;
    flex-direction: column;
    gap: 1px;
    padding: 7px 10px;
    border: 0;
    border-radius: var(--radius);
    background: none;
    color: inherit;
    font: inherit;
    font-size: 13.5px;
    text-align: left;
    text-decoration: none;
    cursor: pointer;
  }

  .x-choice small { font-size: 12px; color: var(--muted); }
  .x-choice:hover, .x-choice:focus-visible { background: var(--surface-2); }
</style>
