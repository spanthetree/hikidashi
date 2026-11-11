<script lang="ts">
  import { onMount } from "svelte";
  import type * as Leaflet from "leaflet";
  import { baseLayer, leaflet } from "$lib/geo";
  import { sidebar } from "$lib/library.svelte";
  import { m } from "$lib/paraglide/messages";
  import type { Place } from "$lib/types";

  /* A reel's places, small, in the detail sheet. Only the ones that are on
     the map; the chips above still list every place the caption names. The
     wheel scrolls the sheet, not the map: zoom is the buttons. */
  let { places }: { places: (Place & { lat: number; lng: number })[] } = $props();

  let el: HTMLDivElement;

  onMount(() => {
    let map: Leaflet.Map | undefined;
    let gone = false;
    void leaflet().then((L) => {
      if (gone) return;
      map = L.map(el, { scrollWheelZoom: false, maxZoom: 19 });
      map.attributionControl.setPrefix(false);
      const settings = sidebar.stats?.map;
      if (settings) baseLayer(L, settings).addTo(map);
      const icon = L.divIcon({ className: "hk-pin", html: "<span></span>", iconSize: [18, 18] });
      for (const p of places) L.marker([p.lat, p.lng], { icon, title: p.text, alt: p.text }).addTo(map);
      map.fitBounds(L.latLngBounds(places.map((p) => [p.lat, p.lng])),
                    { padding: [24, 24], maxZoom: 14 });
    });
    return () => {
      gone = true;
      map?.remove();
    };
  });
</script>

<div class="mini" bind:this={el} role="region" aria-label={m.sheet_on_map()}></div>

<style>
  .mini {
    position: relative;
    z-index: 0;
    isolation: isolate;
    height: 180px;
    margin-top: 10px;
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    overflow: hidden;
    background: var(--surface-2);
  }

  .mini :global(.leaflet-container) { font: inherit; }

  :global(:root[data-scheme="dark"]) .mini :global(.leaflet-tile-pane) {
    filter: invert(1) hue-rotate(180deg) brightness(0.92) contrast(0.88);
  }

  .mini :global(.hk-pin span) {
    display: block;
    width: 12px;
    height: 12px;
    margin: 1px;
    border-radius: 50%;
    background: var(--accent-fill);
    border: 2px solid #fff;
    box-shadow: 0 1px 4px rgb(0 0 0 / 45%);
  }
</style>
