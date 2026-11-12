<script lang="ts">
  /* The mark (docs/brand/icon.svg), drawn inline so each theme can recolour
     it through --logo-card, --logo-lit, --logo-body, --logo-slot and
     --logo-filter. With pixel art on it is redrawn on a 16-pixel grid, two
     screen pixels to each, as the icons are (Icon.svelte). */
  import { look } from "#lib/theme.svelte.js";
</script>

{#if look.pixel}
  <svg class="logo pixel" viewBox="0 0 16 16" width="32" height="32" shape-rendering="crispEdges"
       aria-hidden="true">
    <rect class="card" x="3" y="4" width="2" height="5" />
    <rect class="card" x="11" y="5" width="2" height="4" />
    <!-- In bands, so a theme can give it a pixel sunset (--logo-lit-1 to 3). -->
    <rect class="lit l1" x="7" y="1" width="2" height="3" />
    <rect class="lit l2" x="7" y="4" width="2" height="3" />
    <rect class="lit l3" x="7" y="7" width="2" height="2" />
    <!-- The drawer, its corners knocked out, and its pull. -->
    <rect class="body" x="2" y="8" width="12" height="6" />
    <rect class="body" x="1" y="9" width="14" height="4" />
    <rect class="slot" x="6" y="10" width="4" height="1" />
  </svg>
{:else}
  <svg class="logo" viewBox="0 0 64 64" width="26" height="26" aria-hidden="true">
    <defs>
      <!-- Retrowave's lit card is a sunset (--logo-lit: url(#logo-sun)). -->
      <linearGradient id="logo-sun" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#ffd319" />
        <stop offset="0.55" stop-color="#ff2975" />
        <stop offset="1" stop-color="#8c1eff" />
      </linearGradient>
    </defs>
    <rect class="card" x="13" y="17" width="10" height="22" rx="1.6" />
    <rect class="card" x="41" y="19" width="10" height="20" rx="1.6" />
    <rect class="lit" x="27" y="5" width="10" height="34" rx="1.6" />
    <rect class="body" x="6" y="33" width="52" height="24" rx="4" />
    <rect class="slot" x="25" y="43.3" width="14" height="3.4" rx="1.7" />
  </svg>
{/if}

<style>
  .logo { display: block; flex: none; overflow: visible; filter: var(--logo-filter, none); }
  .card { fill: var(--logo-card); }
  .lit { fill: var(--logo-lit); }
  .body { fill: var(--logo-body); clip-path: var(--logo-body-clip, none); }
  .slot { fill: var(--logo-slot); }
  .pixel .body { clip-path: none; }
  /* A pixel card can't hold a gradient: a theme whose --logo-lit is one sets
     the three bands instead. */
  .pixel .l1 { fill: var(--logo-lit-1, var(--logo-lit)); }
  .pixel .l2 { fill: var(--logo-lit-2, var(--logo-lit)); }
  .pixel .l3 { fill: var(--logo-lit-3, var(--logo-lit)); }
</style>
