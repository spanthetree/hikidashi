<script lang="ts">
  import { onMount } from "svelte";
  import { page } from "$app/state";
  import { getAuth, login } from "$lib/api";
  import Credentials from "$lib/components/Credentials.svelte";
  import Rich from "$lib/components/Rich.svelte";
  import { m } from "$lib/paraglide/messages";
  import type { AuthState } from "$lib/types";

  let auth = $state<AuthState | null>(null);

  /* Only back to a page of this app: a ?next= pointing anywhere else would
     make this page a way to send people off-site after they sign in. */
  const next = $derived.by(() => {
    const to = page.url.searchParams.get("next") ?? "/";
    return to.startsWith("/") && !to.startsWith("//") ? to : "/";
  });

  onMount(async () => {
    auth = await getAuth();
    // Nothing to sign in to: everyone is the library's owner.
    if (auth.mode === "none") location.replace(next);
    // Signing in is the proxy's; going back would only come here again.
  });

  // A full load, not goto: everything the app has loaded so far was loaded
  // signed out.
  async function signIn(username: string, password: string) {
    await login(username, password);
    location.assign(next);
  }
</script>

<Credentials title={m.signin_title()} button={m.signin_button()} onsubmit={signIn}
             disabled={auth?.setup || auth?.mode === "proxy"}>
  {#snippet lede()}
    {#if auth?.mode === "proxy"}{m.signin_proxy()}
    {:else if auth?.setup}<Rich parts={m.signin_setup.parts()} />
    {:else}{m.signin_lede()}{/if}
  {/snippet}
</Credentials>
