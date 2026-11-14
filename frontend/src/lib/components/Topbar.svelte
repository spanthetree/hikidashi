<script lang="ts">
  import { onMount, type Snippet } from "svelte";
  import { m } from "#lib/paraglide/messages.js";
  import { session } from "#lib/session.svelte.js";
  import Icon from "./Icon.svelte";
  import Logo from "./Logo.svelte";
  import { look, MODES, THEMES, type Mode, type Theme } from "#lib/theme.svelte.js";

  let { children, onmenu, menuOpen = false, account = true }: {
    children?: Snippet;
    /** Shown at phone width, where the sidebar becomes a drawer. */
    onmenu?: () => void;
    menuOpen?: boolean;
    /** Who is signed in, linking to their account from the menu. Off where
        nobody is yet; the menu still offers the theme. */
    account?: boolean;
  } = $props();

  onMount(() => { if (account) void session.load(); });

  // Your @handle if you gave one: on Instagram, that is who you are.
  const who = $derived(account && session.me
                       && (session.me.ig_handle ? `@${session.me.ig_handle}` : session.me.username));

  const THEME_NAMES: Record<Theme, () => string> = {
    hikidashi: m.theme_hikidashi, cyberpunk: m.theme_cyberpunk, retrowave: m.theme_retrowave,
    retrofuture: m.theme_retrofuture,
  };
  const MODE_NAMES: Record<Mode, () => string> = {
    system: m.mode_system, light: m.mode_light, dark: m.mode_dark,
  };

  let pop = $state<HTMLElement>();
</script>

<header class="topbar">
  {#if onmenu}
    <button class="menu" type="button" onclick={onmenu}
            aria-label={m.filters()} aria-expanded={menuOpen} aria-controls="sidebar">
      <Icon name="menu" />
    </button>
  {/if}
  <a class="brand" href="/" translate="no">
    <Logo />
    <strong>Hikidashi</strong>
  </a>
  {@render children?.()}
  <button class="who" type="button" popovertarget="look-menu"
          title={who ? m.account() : m.look_menu()}
          aria-label={who ? m.account_menu({ name: who }) : m.look_menu()}>
    <span class="avatar" aria-hidden="true">
      <Icon name={who ? "person" : "palette"} size={14} />
    </span>
    {#if who}<span class="name" translate="no">{who}</span>{/if}
  </button>

  <div class="pop" id="look-menu" popover bind:this={pop}>
    {#if who}
      <a class="pop-account" href="/account" onclick={() => pop?.hidePopover()}>
        <span translate="no">{who}</span>
        <small>{m.account()}</small>
      </a>
    {/if}

    <fieldset class="themes">
      <legend>{m.theme()}</legend>
      {#each THEMES as t (t)}
        <label class="theme">
          <input type="radio" name="theme" value={t} bind:group={look.theme}>
          <span class="swatch" data-swatch={t} aria-hidden="true"></span>
          <span>{THEME_NAMES[t]()}</span>
        </label>
      {/each}
    </fieldset>

    <fieldset class="modes">
      <legend>{m.appearance()}</legend>
      <div class="seg">
        {#each MODES as md (md)}
          <label>
            <input type="radio" name="mode" value={md} bind:group={look.mode}>
            <span>{MODE_NAMES[md]()}</span>
          </label>
        {/each}
      </div>
    </fieldset>

    <label class="switch">
      <span>
        {m.pixel_art()}
        <small>{m.pixel_art_note()}</small>
      </span>
      <input type="checkbox" role="switch" bind:checked={look.pixel}>
      <span class="knob" aria-hidden="true"></span>
    </label>
  </div>
</header>

<style>
  .topbar {
    position: sticky;
    top: 0;
    z-index: 5;
    display: flex;
    align-items: center;
    gap: 16px;
    height: var(--topbar-h);
    padding: 0 18px;
    background: var(--surface);
    border-bottom: 1px solid var(--rule);
  }

  .brand {
    display: flex;
    align-items: center;
    gap: 9px;
    flex: none;
    color: inherit;
    text-decoration: none;
  }

  .brand strong { font-family: var(--title); font-size: 17px; font-weight: 700; letter-spacing: -0.02em; }


  .menu {
    display: none;
    flex: none;
    place-items: center;
    width: 34px;
    height: 34px;
    padding: 0;
    background: none;
    border: 0;
    border-radius: var(--radius);
    cursor: pointer;
  }

  .menu:hover, .menu[aria-expanded="true"] { background: var(--surface-2); }

  .menu :global(svg) { stroke-width: 1.6; }

  .who {
    display: flex;
    align-items: center;
    gap: 8px;
    flex: none;
    max-width: 200px;
    margin-left: auto;
    padding: 4px 10px 4px 4px;
    background: none;
    border: 0;
    cursor: pointer;
    font-size: 13.5px;
    font-weight: 600;
    color: var(--ink);
    text-decoration: none;
    border-radius: var(--radius-pill);
    transition: background-color 120ms ease;
  }

  .who:hover, .who:has(+ .pop:popover-open) { background: var(--surface-2); }
  .who:not(:has(.name)) { padding: 4px; }

  /* The account in a ring of the theme's --brand, a gap of page, then the face. */
  .avatar {
    display: grid;
    place-items: center;
    flex: none;
    width: 30px;
    height: 30px;
    border-radius: 50%;
    background: var(--brand);
  }

  .avatar :global(svg) {
    box-sizing: content-box;
    padding: 4px;
    border: 2px solid var(--surface);
    border-radius: 50%;
    background: var(--surface-2);
    fill: none;
    stroke: currentColor;
    stroke-width: 1.5;
    stroke-linecap: round;
  }

  .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  /* ---------- the menu ---------- */
  .pop {
    position: fixed;
    inset: calc(var(--topbar-h) + 6px) 12px auto auto;
    margin: 0;
    width: 260px;
    max-height: calc(100dvh - var(--topbar-h) - 18px);
    overflow-y: auto;
    padding: 8px;
    color: var(--ink);
    background: var(--surface);
    border: 1px solid var(--rule);
    border-radius: var(--radius-lg);
    box-shadow: 0 12px 40px -12px rgb(0 0 0 / 35%);
  }

  .pop:popover-open { display: flex; flex-direction: column; gap: 6px; animation: drop 180ms var(--ease-out); }

  @keyframes drop { from { opacity: 0; transform: translateY(-6px) scale(0.98); } }

  .pop-account {
    display: flex;
    flex-direction: column;
    padding: 8px 10px;
    border-radius: var(--radius);
    color: inherit;
    text-decoration: none;
    font-weight: 600;
  }

  .pop-account small { font-weight: 400; font-size: 12.5px; color: var(--muted); }
  .pop-account:hover { background: var(--surface-2); }

  fieldset { margin: 0; padding: 6px 0 0; border: 0; border-top: 1px solid var(--rule); }
  .pop-account + fieldset { margin-top: 2px; }
  .pop > fieldset:first-child { border-top: 0; padding-top: 0; }

  legend {
    float: left;
    width: 100%;
    padding: 4px 10px 6px;
    font-family: var(--display);
    font-size: 12px;
    font-weight: 600;
    color: var(--muted);
  }

  input[type="radio"] { position: absolute; opacity: 0; pointer-events: none; }

  .theme {
    position: relative;
    clear: both;
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 6px 10px 6px 32px;
    border-radius: var(--radius);
    font-size: 14px;
    cursor: pointer;
  }

  .theme:hover { background: var(--surface-2); }
  .theme:has(:checked) { font-weight: 700; }
  /* The chosen theme is marked at the left, each theme in its own way (the
     themes' CSS); this is Hikidashi's, the lit card from the mark. */
  .theme:has(:checked)::before {
    content: "";
    position: absolute;
    left: 13px;
    top: calc(50% - 7px);
    width: 6px;
    height: 14px;
    border-radius: 1.5px;
    background: var(--accent-fill);
    animation: ring-in 320ms var(--ease-out);
  }

  @keyframes ring-in { from { opacity: 0; transform: translateY(5px) scaleY(0.4); } }
  .theme:has(:focus-visible), .seg label:has(:focus-visible) { outline: 2px solid var(--accent); outline-offset: -2px; }

  .swatch { flex: none; width: 22px; height: 22px; border-radius: var(--radius-sm); box-shadow: inset 0 0 0 1px rgb(0 0 0 / 12%); }
  .swatch[data-swatch="hikidashi"] { background: linear-gradient(135deg, #f3ece0 0 45%, #d13a20 45% 65%, #1c1b19 65%); }
  .swatch[data-swatch="cyberpunk"] { background: linear-gradient(135deg, #fcee0a 0 50%, #00f0ff 50% 75%, #ff003c 75%); }
  .swatch[data-swatch="retrofuture"] { background: conic-gradient(from 20deg, #d4491f, #f2b134, #3fb8af, #f3e9d2, #d4491f); }
  .swatch[data-swatch="retrowave"] { background: linear-gradient(180deg, #ffd319 0%, #ff2975 45%, #8c1eff 70%, #1b0b3a 70%); }

  .seg {
    clear: both;
    display: flex;
    gap: 2px;
    margin: 0 4px 4px;
    padding: 3px;
    background: var(--surface-2);
    border-radius: var(--radius);
  }

  .seg label {
    flex: 1;
    padding: 4px 6px;
    text-align: center;
    font-size: 13px;
    color: var(--ink-2);
    border-radius: var(--radius-sm);
    cursor: pointer;
    transition: background-color 140ms ease, color 140ms ease;
  }

  .seg label:hover { color: var(--ink); }

  .switch {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-top: 2px;
    padding: 8px 10px 6px;
    border-top: 1px solid var(--rule);
    font-size: 14px;
    cursor: pointer;
  }

  .switch > span:first-child { flex: 1; display: flex; flex-direction: column; }
  .switch small { font-size: 12px; color: var(--muted); }
  .switch input { position: absolute; opacity: 0; pointer-events: none; }

  .knob {
    position: relative;
    flex: none;
    width: 34px;
    height: 20px;
    border-radius: var(--radius-pill);
    background: var(--surface-3);
    transition: background-color 160ms ease;
  }

  .knob::after {
    content: "";
    position: absolute;
    top: 2px;
    left: 2px;
    width: 16px;
    height: 16px;
    border-radius: var(--radius-pill);
    background: #fff;
    box-shadow: 0 1px 2px rgb(0 0 0 / 30%);
    transition: transform 160ms var(--ease-out);
  }

  .switch :checked + .knob { background: var(--accent-fill); }
  .switch :checked + .knob::after { transform: translateX(14px); }
  .switch:has(:focus-visible) .knob { outline: 2px solid var(--accent); outline-offset: 2px; }
  .seg label:has(:checked) { background: var(--surface); color: var(--ink); font-weight: 600; box-shadow: 0 1px 2px rgb(0 0 0 / 12%); }

  @media (max-width: 700px) {
    /* Wraps when a page puts a second row in it (the library's search). */
    .topbar { flex-wrap: wrap; align-content: center; gap: 8px 10px; padding: 0 12px; }
    .menu { display: grid; }
    .brand strong { display: none; }
    .who, .who:not(:has(.name)) { padding: 0; width: 34px; height: 34px; justify-content: center; }
    .name { display: none; }
  }
</style>
