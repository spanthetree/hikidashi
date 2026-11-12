<script lang="ts">
  import { ICONS, type IconName } from "#lib/icons.js";
  import { look } from "#lib/theme.svelte.js";

  /* An icon in the current style: a line drawing, or with pixel art on, its
     8x8 pixel version at a whole multiple of 8px so every pixel stays
     square. Colour comes from currentColor; a parent can restyle the svg
     with :global(svg), as for a ring or a fill. */
  let { name, size = 16 }: { name: IconName; size?: number } = $props();

  const px = $derived(Math.max(8, Math.round(size / 8) * 8));
</script>

{#if look.pixel}
  <svg class="icon pixel" viewBox="0 0 8 8" width={px} height={px} shape-rendering="crispEdges"
       aria-hidden="true" style="fill: currentColor; stroke: none"><path d={ICONS[name].pixel} /></svg>
{:else}
  <svg class="icon" viewBox="0 0 16 16" width={size} height={size} aria-hidden="true">
    <path d={ICONS[name].line} />
  </svg>
{/if}

<style>
  .icon {
    flex: none;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.5;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
</style>
