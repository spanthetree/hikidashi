<script lang="ts">
  /* Old saves brought back to the surface (#50): saved on this day in
     earlier years, and never opened here. Each strip hides until tomorrow. */
  import { getRediscover } from "#lib/api.js";
  import { tint } from "#lib/format.js";
  import { locale } from "#lib/i18n.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { Item } from "#lib/types.js";
  import Icon from "./Icon.svelte";

  let { search }: { search: string } = $props();

  type Strip = "on_this_day" | "never_opened";
  const today = new Date().toDateString();
  const key = (s: Strip) => `rediscover:${s}`;
  const hiddenToday = (s: Strip) => {
    try { return localStorage.getItem(key(s)) === today; } catch { return false; }
  };

  let strips = $state<Record<Strip, Item[]>>({ on_this_day: [], never_opened: [] });
  let hidden = $state<Record<Strip, boolean>>({
    on_this_day: hiddenToday("on_this_day"), never_opened: hiddenToday("never_opened"),
  });

  $effect(() => {
    // Extra, not the library: a failure shows nothing rather than an error.
    getRediscover().then((r) => { strips = r; }).catch(() => {});
  });

  function hide(s: Strip) {
    hidden[s] = true;
    try { localStorage.setItem(key(s), today); } catch { /* blocked */ }
  }

  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  function ago(ts: number | null): string {
    if (!ts) return "";
    const d = (ts * 1000 - Date.now()) / 86400000;
    return Math.abs(d) >= 365 ? rtf.format(Math.round(d / 365), "year")
      : Math.abs(d) >= 30 ? rtf.format(Math.round(d / 30), "month")
      : rtf.format(Math.round(d), "day");
  }

  const TITLE: Record<Strip, () => string> = {
    on_this_day: m.rediscover_on_this_day, never_opened: m.rediscover_never_opened,
  };
  const shown = $derived((["on_this_day", "never_opened"] as Strip[])
    .filter((s) => strips[s].length && !hidden[s]));
</script>

{#each shown as s (s)}
  <section class="strip" aria-label={TITLE[s]()}>
    <header>
      <h3>{TITLE[s]()}</h3>
      <button type="button" class="hide" aria-label={m.rediscover_hide()} title={m.rediscover_hide()}
              onclick={() => hide(s)}><Icon name="close" size={10} /></button>
    </header>
    <div class="row">
      {#each strips[s] as item (item.shortcode)}
        <a href={`/item/${encodeURIComponent(item.shortcode)}${search}`} data-sveltekit-noscroll
           title={item.caption ?? item.shortcode} style:--tint={tint(item.shortcode)}>
          <span class="pic">{#if item.thumb}<img src={item.thumb} alt="" loading="lazy"
                                                  onerror={(e) => (e.currentTarget as HTMLElement).remove()}>{/if}</span>
          <span class="who">{item.author_name || (item.author ? `@${item.author}` : item.shortcode)}</span>
          <span class="when">{ago(item.saved_at)}</span>
        </a>
      {/each}
    </div>
  </section>
{/each}

<style>
  .strip { display: flex; flex-direction: column; gap: 6px; margin-bottom: 14px; }
  header { display: flex; align-items: center; gap: 8px; }
  h3 { margin: 0; font-family: var(--display); font-size: 14px; font-weight: 600; color: var(--ink); }
  .hide {
    border: 0; background: none; color: var(--muted); cursor: pointer; padding: 4px;
    border-radius: var(--radius-pill); display: inline-flex;
  }
  .hide:hover { color: var(--ink); background: var(--surface-2); }
  .row { display: flex; gap: 12px; overflow-x: auto; padding-bottom: 6px; scroll-snap-type: x proximity; }
  a {
    flex: 0 0 88px; display: flex; flex-direction: column; gap: 3px; min-width: 0;
    color: var(--ink-2); text-decoration: none; font-size: 12px; scroll-snap-align: start;
  }
  .pic {
    display: block; width: 88px; aspect-ratio: 4 / 5; overflow: hidden;
    border-radius: var(--radius, 8px); background: var(--tint);
  }
  img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .who, .when { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .when { color: var(--muted); font-size: 11px; }
  a:hover .who { color: var(--accent); }
</style>
