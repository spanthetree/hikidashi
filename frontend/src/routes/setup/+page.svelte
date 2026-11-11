<script lang="ts">
  import { page } from "$app/state";
  import { setup } from "$lib/api";
  import Credentials from "$lib/components/Credentials.svelte";
  import { m } from "$lib/paraglide/messages";

  /* The link the server logs while nobody can sign in: /setup?token=… */
  const token = $derived(page.url.searchParams.get("token") ?? "");

  async function claim(username: string, password: string) {
    await setup(token, username, password);
    location.assign("/");
  }
</script>

<Credentials title={m.setup_title()} button={m.setup_button()} fresh disabled={!token}
             onsubmit={claim}>
  {#snippet lede()}{token ? m.setup_lede() : m.setup_no_token()}{/snippet}
</Credentials>
