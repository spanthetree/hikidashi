<script lang="ts">
  import { m } from "#lib/paraglide/messages.js";
  import AddLink from "./AddLink.svelte";
  import Icon from "./Icon.svelte";
  import ImportBox from "./ImportBox.svelte";

  /* The import box over the library, from the toolbar's Import button or a
     ZIP dropped on the page. A native modal <dialog>: focus moves in, the
     page behind goes inert, Esc arrives as `cancel`, and on close focus goes
     back to where it was. Uploads carry on once it is closed. */
  let { onclose }: { onclose: () => void } = $props();

  const modal = (dialog: HTMLDialogElement) => {
    dialog.showModal();
    return () => { if (dialog.open) dialog.close(); };
  };

  function cancel(e: Event) {
    e.preventDefault();
    onclose();
  }

  /* Tab and Shift+Tab wrap around inside the panel rather than wandering
     out to the browser's own UI, as in the reel's sheet. */
  let panel = $state<HTMLElement>();

  function trap(e: KeyboardEvent) {
    if (e.key !== "Tab" || !panel) return;
    const items = [...panel.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), input:not([disabled]), summary, [tabindex]:not([tabindex="-1"])',
    )].filter((el) => el.offsetParent !== null || el === document.activeElement);
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }

  // A click on the backdrop lands on the dialog itself, outside its panel.
  function backdrop(e: MouseEvent) {
    if (e.target === e.currentTarget) onclose();
  }
</script>

<dialog class="imp" aria-labelledby="imp-title" {@attach modal} oncancel={cancel} onclick={backdrop}
        onkeydown={trap}>
  <div class="panel" bind:this={panel}>
    <header>
      <h2 id="imp-title">{m.import_title()}</h2>
      <button type="button" class="x" aria-label={m.close()} title={m.close()} onclick={onclose}>
        <Icon name="close" size={16} />
      </button>
    </header>
    <ImportBox level={3} />
    <hr>
    <AddLink />
  </div>
</dialog>

<style>
  .imp {
    width: min(560px, calc(100% - 24px));
    max-height: calc(100dvh - 48px);
    padding: 0;
    border: 1px solid var(--rule);
    border-radius: var(--radius-lg);
    background: var(--surface);
    color: var(--ink);
    box-shadow: 0 24px 60px -20px rgb(0 0 0 / 50%);
    animation: rise 200ms var(--ease-out);
  }

  .imp::backdrop { background: rgb(0 0 0 / 45%); animation: fade 200ms ease; }

  .panel { padding: 18px 20px 22px; display: flex; flex-direction: column; gap: 16px; }

  hr { width: 100%; margin: 0; border: 0; border-top: 1px solid var(--rule); }

  header { display: flex; align-items: center; gap: 12px; }
  h2 { margin: 0; font-size: 18px; flex: 1; }

  .x {
    display: grid;
    place-items: center;
    width: 32px;
    height: 32px;
    padding: 0;
    border: 0;
    border-radius: 50%;
    background: var(--surface-2);
    color: var(--ink);
    cursor: pointer;
  }
  .x:hover { background: var(--surface-3); }

  @keyframes rise { from { opacity: 0; transform: translateY(12px); } }
  @keyframes fade { from { opacity: 0; } }

  @media (prefers-reduced-motion: reduce) {
    .imp, .imp::backdrop { animation: none; }
  }
</style>
