<script lang="ts">
  import type { Snippet } from "svelte";
  import Topbar from "#lib/components/Topbar.svelte";
  import { m } from "#lib/paraglide/messages.js";

  /* The page for signing in, claiming the library, and accepting an invite:
     a title, a word on what this is, a username and a password. `fresh` is a
     new password, so the browser offers to make one up and the rule shows. */
  let { title, lede, button, fresh = false, disabled = false, onsubmit }: {
    title: string;
    lede: Snippet;
    button: string;
    fresh?: boolean;
    disabled?: boolean;
    onsubmit: (username: string, password: string) => Promise<void>;
  } = $props();

  let username = $state("");
  let password = $state("");
  let busy = $state(false);
  let error = $state<string | null>(null);

  async function submit(e: SubmitEvent) {
    e.preventDefault();
    if (busy) return;
    busy = true;
    error = null;
    try {
      await onsubmit(username.trim(), password);
    } catch (err) {
      error = (err as Error).message;
      password = "";
    } finally {
      busy = false;
    }
  }
</script>

<svelte:head><title>{title} · Hikidashi</title></svelte:head>

<Topbar account={false} />

<main class="page creds">
  <header>
    <h1>{title}</h1>
    <p class="lede">{@render lede()}</p>
  </header>

  <form onsubmit={submit}>
    <label for="c-user">{m.username()}</label>
    <input id="c-user" type="text" autocomplete="username" autocapitalize="none"
           spellcheck="false" required maxlength="32" bind:value={username} {disabled}>
    <label for="c-pass">{m.password()}</label>
    <input id="c-pass" type="password" required minlength={fresh ? 8 : undefined}
           autocomplete={fresh ? "new-password" : "current-password"}
           bind:value={password} {disabled}>
    {#if fresh}<p class="hint">{m.password_hint()}</p>{/if}
    {#if error}<p class="c-error" role="alert">{error}</p>{/if}
    <button class="btn primary" type="submit" disabled={busy || disabled}>{button}</button>
  </form>
</main>

<style>
  .creds { max-width: 420px; padding-top: 48px; }

  form {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 20px;
    background: var(--surface);
    border: 1px solid var(--rule);
    border-radius: var(--radius);
  }

  label { font-size: 13px; color: var(--ink-2); }
  label:not(:first-child) { margin-top: 8px; }

  input {
    padding: 8px 11px;
    background: var(--field);
    border: 1px solid transparent;
    border-radius: var(--radius);
  }

  input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); outline: none; }

  .c-error { margin: 6px 0 0; font-size: 13px; color: var(--risk); }

  button { margin-top: 14px; align-self: flex-start; }
</style>
