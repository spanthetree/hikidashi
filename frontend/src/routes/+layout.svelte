<script lang="ts">
  import "../app.css";
  import "../themes/index.css";
  import "$lib/theme.svelte";
  import { locale } from "$lib/i18n";

  let { children } = $props();

  // app.html says "en" until this runs. The browser picks fonts and line
  // breaking by it, so Japanese text gets Japanese glyphs.
  document.documentElement.lang = locale;
</script>

{@render children()}
