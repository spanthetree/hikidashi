<script lang="ts">
  import type { Snippet } from "svelte";
  import { m } from "#lib/paraglide/messages.js";

  /* A title whose description sits behind an "i" beside it: shown while the
     mouse is over the button, kept open by a click or tap, closed by Escape
     or a click anywhere else. Pass the description as children. */
  let { title, level = 2, id, children }: {
    title: string;
    level?: 1 | 2;
    id?: string;
    children: Snippet;
  } = $props();

  const tipId = $props.id();
  let open = $state(false);
  let wrap: HTMLElement | undefined = $state();

  function toggle(e: MouseEvent) {
    // Inside a <summary>, the click would also fold the section.
    e.preventDefault();
    open = !open;
  }
</script>

<svelte:window
  onclick={(e) => { if (open && !wrap?.contains(e.target as Node)) open = false; }}
  onkeydown={(e) => { if (e.key === "Escape") open = false; }} />

<div class="info-head" bind:this={wrap}>
  <svelte:element this={`h${level}`} {id}>{title}</svelte:element>
  <button class="info" type="button" aria-expanded={open} aria-controls={tipId}
          aria-label={m.about_section({ title })} onclick={toggle}></button>
  <span class="tip" id={tipId} role="tooltip" class:open>{@render children()}</span>
</div>

<style>
  .info-head { display: flex; align-items: center; gap: 8px; position: relative; }
  .info-head :global(:is(h1, h2)) { margin: 0; }

  .info {
    flex: none;
    width: 20px;
    height: 20px;
    display: grid;
    place-items: center;
    padding: 0;
    border: 1px solid currentColor;
    border-radius: 50%;
    background: transparent;
    color: var(--muted);
    font: italic 700 12px/1 Georgia, serif;
    cursor: pointer;
  }
  /* A glyph, not a word: the button is named by its aria-label. */
  .info::before { content: "i"; }
  .info:hover, .info:focus-visible, .info[aria-expanded="true"] {
    color: var(--accent);
    box-shadow: var(--glow, none);
    outline: none;
  }

  .tip {
    position: absolute;
    top: calc(100% + 8px);
    left: 0;
    z-index: 20;
    width: min(62ch, 100%);
    padding: 10px 14px;
    border: 1px solid var(--accent);
    border-radius: var(--radius);
    background: var(--surface-2);
    box-shadow: var(--glow, 0 4px 14px rgb(0 0 0 / 18%));
    color: var(--ink-2);
    font-family: var(--sans);
    font-size: 14px;
    font-weight: normal;
    line-height: 1.5;
    text-transform: none;
    letter-spacing: normal;
    opacity: 0;
    visibility: hidden;
    transform: translateY(-4px);
    transition: opacity 0.12s, transform 0.12s, visibility 0.12s;
  }
  .tip :global(code) { font-size: 12.5px; }
  .tip.open, .info:focus-visible + .tip { opacity: 1; visibility: visible; transform: none; }
  @media (hover: hover) {
    .info:hover + .tip { opacity: 1; visibility: visible; transform: none; }
  }
</style>
