<script lang="ts">
  import { onMount } from "svelte";
  import { page } from "$app/state";
  import { addLink } from "#lib/api.js";
  import Topbar from "#lib/components/Topbar.svelte";
  import { m } from "#lib/paraglide/messages.js";
  import { sharedText } from "#lib/share.js";

  /* Where the phone's share sheet lands (the manifest's share_target, #52):
     shared from the Instagram app, the reel goes into your library and the
     workers fetch it. Signed out, api() goes to sign in and comes back here
     with the same query, so nothing shared is lost. */
  let status = $state<"adding" | "added" | "had" | "none" | "failed">("adding");
  let shortcode = $state("");
  let error = $state("");

  onMount(async () => {
    const text = sharedText(page.url.searchParams);
    if (!text) { status = "none"; return; }
    try {
      const r = await addLink(text);
      shortcode = r.shortcode;
      status = r.added ? "added" : "had";
    } catch (err) {
      error = (err as Error).message;
      status = "failed";
    }
  });
</script>

<svelte:head><title>{m.share_title()} · Hikidashi</title></svelte:head>

<Topbar account={false} />

<main class="page share">
  <h1>{m.share_title()}</h1>
  <p role="status">
    {#if status === "adding"}{m.add_link_adding()}
    {:else if status === "added"}{m.add_link_added()}
    {:else if status === "had"}{m.add_link_had()}
    {:else if status === "none"}{m.share_none()}
    {:else}<span class="err">{m.add_link_failed({ error })}</span>{/if}
  </p>
  <p class="links">
    {#if shortcode}
      <a class="btn primary" href={`/item/${encodeURIComponent(shortcode)}`}>{m.add_link_open()}</a>
    {/if}
    <a class="btn" href="/">{m.share_library()}</a>
  </p>
</main>

<style>
  .share { max-width: 420px; padding-top: 48px; }
  .links { display: flex; gap: 8px; flex-wrap: wrap; }
  .err { color: var(--risk); }
</style>
