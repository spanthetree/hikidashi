<script lang="ts">
  import type { MessagePart } from "#lib/paraglide/runtime.js";

  /* A message with a little markup in it, `{#b}Saved{/b}` or
     `{#code}./hikidashi tag{/code}`, drawn without {@html}: each run of text
     is rendered as text, inside the one tag it sits in. Pass `m.x.parts()`. */
  let { parts }: { parts: MessagePart[] } = $props();

  const runs = $derived.by(() => {
    const out: { tag: string | null; text: string }[] = [];
    let tag: string | null = null;
    for (const p of parts) {
      if (p.type === "markup-start") tag = p.name;
      else if (p.type === "markup-end") tag = null;
      else if (p.type === "text") out.push({ tag, text: p.value });
    }
    return out;
  });
</script>

{#each runs as r, i (i)}{#if r.tag === "b"}<b>{r.text}</b>{:else if r.tag === "code"}<code>{r.text}</code>{:else}{r.text}{/if}{/each}
