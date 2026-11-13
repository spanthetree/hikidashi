<script lang="ts">
  import { addLink } from "#lib/api.js";
  import { library } from "#lib/library.svelte.js";
  import { m } from "#lib/paraglide/messages.js";

  /* Add one reel by its link (#52): the way in on a phone with no share
     target, as on iOS, or for a reel seen somewhere other than Instagram. */
  let link = $state("");
  let busy = $state(false);
  let done = $state<{ shortcode: string; added: boolean } | null>(null);
  let error = $state<string | null>(null);

  async function submit(e: SubmitEvent) {
    e.preventDefault();
    if (busy || !link.trim()) return;
    busy = true;
    done = error = null;
    try {
      done = await addLink(link.trim());
      link = "";
      if (done.added) library.reload();
    } catch (err) {
      error = (err as Error).message;
    } finally {
      busy = false;
    }
  }
</script>

<form class="addlink" onsubmit={submit}>
  <label for="add-link">{m.add_link_label()}</label>
  <div class="row">
    <input id="add-link" type="url" inputmode="url" autocomplete="off" spellcheck="false"
           maxlength="2000" bind:value={link}>
    <button class="btn" type="submit" disabled={busy || !link.trim()}>
      {busy ? m.add_link_adding() : m.add_link_button()}
    </button>
  </div>
  <p class="status" role="status">
    {#if done}
      {done.added ? m.add_link_added() : m.add_link_had()}
      <a href={`/item/${encodeURIComponent(done.shortcode)}`}>{m.add_link_open()}</a>
    {:else if error}
      <span class="err">{m.add_link_failed({ error })}</span>
    {/if}
  </p>
</form>

<style>
  .addlink { display: flex; flex-direction: column; gap: 6px; }
  label { font-size: 14px; font-weight: 600; }
  .row { display: flex; gap: 8px; }
  input {
    flex: 1;
    min-width: 0;
    padding: 8px 11px;
    background: var(--field);
    border: 1px solid transparent;
    border-radius: var(--radius);
  }
  input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); outline: none; }
  .status { margin: 0; min-height: 1.4em; font-size: 13px; color: var(--ink-2); }
  .status a { color: var(--accent); }
  .err { color: var(--risk); }
</style>
