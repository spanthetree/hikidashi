<script lang="ts">
  import { slide } from "svelte/transition";
  import { motion } from "#lib/motion.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { NoticeState } from "#lib/types.js";

  let { notice = $bindable() }: { notice: NoticeState } = $props();
</script>

{#if notice}
  <div class="notice is-{notice.kind}" role={notice.kind === "error" ? "alert" : "status"}
       transition:slide={{ duration: motion.reduced ? 0 : 180 }}>
    <span>{notice.text}</span>
    <button type="button" aria-label={m.dismiss()} onclick={() => (notice = null)}>&times;</button>
  </div>
{/if}

<style>
  .notice {
    display: flex;
    align-items: flex-start;
    gap: 12px;
    padding: 10px 14px;
    border-radius: var(--radius);
    border: 1px solid var(--rule);
    background: var(--surface);
    font-size: 13.5px;
    white-space: pre-wrap;
  }

  .notice span { flex: 1; min-width: 0; }
  .is-error { border-color: var(--risk); color: var(--risk); }
  .is-ok { border-color: var(--ok); color: var(--ok); }

  button {
    flex: none;
    padding: 0 4px;
    background: none;
    border: 0;
    font-size: 16px;
    line-height: 1;
    opacity: 0.6;
    cursor: pointer;
  }

  button:hover { opacity: 1; }
</style>
