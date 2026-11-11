<script lang="ts">
  import { onMount } from "svelte";
  import { page } from "$app/state";
  import { acceptInvite, inviteState } from "$lib/api";
  import Credentials from "$lib/components/Credentials.svelte";
  import { m } from "$lib/paraglide/messages";

  const token = $derived(page.params.token ?? "");
  let open = $state(true);

  onMount(async () => {
    try {
      open = (await inviteState(token)).open;
    } catch {
      open = false;
    }
  });

  async function make(username: string, password: string) {
    await acceptInvite(token, username, password);
    location.assign("/");
  }
</script>

<Credentials title={m.invite_title()} button={m.invite_button()} fresh disabled={!open}
             onsubmit={make}>
  {#snippet lede()}{open ? m.invite_lede() : m.invite_closed()}{/snippet}
</Credentials>
