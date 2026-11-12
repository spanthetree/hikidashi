<script lang="ts">
  import { fileSize, nf } from "#lib/format.js";
  import { active, draggingNonZip, hasFiles, imports, type Upload } from "#lib/imports.svelte.js";
  import { motion } from "#lib/motion.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import Icon from "./Icon.svelte";
  import Rich from "./Rich.svelte";

  /* From "I have no export" to "it's imported" in one place: where to ask
     Instagram for the export and what to choose there, then a zone to drop
     the ZIP on that is also a button, so clicking, tapping or the keyboard
     choose it instead. What it sends joins the page's queue (imports.svelte). */
  let { level = 3 }: {
    /** The steps' heading level, one below whatever the box sits under. */
    level?: 3 | 4;
  } = $props();

  const EXPORT_PAGE = "https://accountscenter.instagram.com/info_and_permissions/dyi/";

  let input = $state<HTMLInputElement>();

  function picked(e: Event) {
    const el = e.currentTarget as HTMLInputElement;
    if (el.files?.length) imports.add(el.files);
    el.value = "";                    // the same ZIP can be chosen again
  }

  /* ---------- dragging onto the zone ---------- */

  // enter and leave fire for every child crossed, so count them.
  let depth = 0;
  let over = $state(false);
  let bad = $state(false);

  function enter(e: DragEvent) {
    if (!hasFiles(e.dataTransfer)) return;
    e.preventDefault();
    depth++;
    over = true;
    bad = draggingNonZip(e.dataTransfer);
  }

  function leave() {
    if (depth && --depth === 0) over = bad = false;
  }

  function drop(e: DragEvent) {
    if (!hasFiles(e.dataTransfer)) return;
    // Handled: the page-wide drop sees defaultPrevented and leaves it be.
    e.preventDefault();
    depth = 0;
    over = bad = false;
    imports.add(e.dataTransfer!.files);
  }

  function dragover(e: DragEvent) {
    if (hasFiles(e.dataTransfer)) e.preventDefault();
  }

  /* ---------- the queue ---------- */

  const pct = (u: Upload) => Math.round(u.sent * 100);

  function result(u: Upload) {
    const r = u.result!;
    const counts = {
      file: u.file.name, strategy: r.strategy, inserted: nf.format(r.inserted),
      updated: nf.format(r.updated), unchanged: nf.format(r.unchanged),
    };
    return (r.unparsed ? m.import_done_unparsed({ ...counts, unparsed: nf.format(r.unparsed) })
      : m.import_done(counts)) + (r.already_imported ? `  ${m.import_again()}` : "");
  }

  /** What has happened to it, in words. */
  function status(u: Upload) {
    switch (u.state) {
      case "waiting": return m.import_waiting();
      case "sending": return m.import_sending();
      case "reading": return m.import_reading();
      case "done": return result(u);
      default: return u.error ?? "";
    }
  }

  const zoneLabel = $derived(
    bad ? m.import_zone_bad() : over ? m.import_zone_over()
      : motion.canHover ? m.import_zone() : m.import_zone_touch(),
  );
</script>

<ol class="ibox">
  <li class="step">
    <span class="num" aria-hidden="true">1</span>
    <div class="body">
      <svelte:element this={`h${level}`}>{m.import_step_get()}</svelte:element>
      <a class="btn primary ig" href={EXPORT_PAGE} target="_blank" rel="noopener noreferrer">
        {m.import_open_instagram()} <span aria-hidden="true">↗</span>
        <span class="sr">{m.import_new_tab()}</span>
      </a>
      <p>{m.import_choices()}</p>
      <ul class="checks">
        <li><Rich parts={m.import_choice_create.parts()} /></li>
        <li><Rich parts={m.import_choice_saved.parts()} /></li>
        <li><Rich parts={m.import_choice_time.parts()} /></li>
        <li><Rich parts={m.import_choice_json.parts()} /></li>
      </ul>
      <p class="hint">{m.import_arrives()}</p>
      <details>
        <summary>{m.import_phone()}</summary>
        <p class="hint"><Rich parts={m.import_phone_steps.parts()} /></p>
      </details>
    </div>
  </li>

  <li class="step">
    <span class="num" aria-hidden="true">2</span>
    <div class="body">
      <svelte:element this={`h${level}`}>{m.import_step_drop()}</svelte:element>
      <button type="button" class="zone" class:over class:bad onclick={() => input?.click()}
              ondragenter={enter} ondragover={dragover} ondragleave={leave} ondrop={drop}>
        <span class="zone-icon"><Icon name="import" size={22} /></span>
        <b>{zoneLabel}</b>
        <span class="hint">{m.import_zone_hint()}</span>
      </button>
      <input bind:this={input} type="file" accept=".zip,application/zip" multiple hidden
             onchange={picked}>

      <!-- Read out as each changes state; the percentage is not, or every
           step of the bar would be. -->
      <ul class="uploads" aria-live="polite">
        {#each imports.list as u (u.id)}
          <li class="up is-{u.state}">
            <div class="up-head">
              <span class="up-name">{u.file.name}</span>
              <span class="up-size">{fileSize(u.file.size)}</span>
              {#if !active(u)}
                <button type="button" class="up-x" aria-label={m.dismiss()} title={m.dismiss()}
                        onclick={() => imports.dismiss(u.id)}><Icon name="close" size={12} /></button>
              {/if}
            </div>
            <!-- Reading the export has no fraction: an indeterminate bar. -->
            {#if u.state === "sending"}
              <progress max="100" value={pct(u)} aria-label={u.file.name}></progress>
            {:else if u.state === "reading"}
              <progress aria-label={u.file.name}></progress>
            {/if}
            <span class="up-note">
              {status(u)}{#if u.state === "sending"}<span aria-hidden="true"> · {nf.format(pct(u))}%</span>{/if}
            </span>
            {#if u.state === "failed"}
              <button type="button" class="btn sm" onclick={() => imports.retry(u.id)}>{m.retry()}</button>
            {/if}
          </li>
        {/each}
      </ul>
    </div>
  </li>
</ol>

<style>
  .ibox {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 22px;
    text-align: start;
    width: 100%;
  }

  .step { display: grid; grid-template-columns: 28px 1fr; gap: 12px; }

  .num {
    display: grid;
    place-items: center;
    width: 28px;
    height: 28px;
    border-radius: 50%;
    background: var(--accent-soft);
    color: var(--accent);
    font-family: var(--display);
    font-weight: 700;
    font-size: 14px;
  }

  .body { display: flex; flex-direction: column; align-items: flex-start; gap: 10px; min-width: 0; }
  .body :is(h3, h4) { margin: 3px 0 0; font-size: 15px; }
  .body p { margin: 0; font-size: 14px; color: var(--ink-2); }

  .ig { display: inline-flex; align-items: center; gap: 6px; }

  .checks { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 4px; }
  .checks li { position: relative; padding-left: 22px; font-size: 14px; }
  .checks li::before {
    content: "";
    position: absolute;
    left: 3px;
    top: 0.3em;
    width: 5px;
    height: 10px;
    border: solid var(--ok);
    border-width: 0 2px 2px 0;
    transform: rotate(45deg);
  }

  details { font-size: 13px; }
  summary { cursor: pointer; color: var(--accent); }
  details .hint { margin-top: 6px; white-space: normal; }

  .zone {
    width: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
    padding: 26px 16px;
    border: 2px dashed var(--rule);
    border-radius: var(--radius-lg);
    background: var(--surface);
    color: var(--ink);
    text-align: center;
    cursor: pointer;
    transition: border-color 140ms ease, background-color 140ms ease, transform 140ms ease;
  }

  .zone b { font-family: var(--display); font-size: 15px; }
  .zone .hint { white-space: normal; }
  .zone-icon { color: var(--accent); display: inline-flex; }

  .zone:hover, .zone:focus-visible { border-color: var(--accent); background: var(--accent-soft); }
  .zone.over { border-color: var(--accent); background: var(--accent-soft); transform: scale(1.01); }
  .zone.bad { border-color: var(--risk); background: var(--surface-2); }
  .zone.bad b, .zone.bad .zone-icon { color: var(--risk); }

  .uploads:empty { display: none; }
  .uploads { list-style: none; margin: 0; padding: 0; width: 100%; display: flex; flex-direction: column; gap: 8px; }

  .up {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 6px;
    padding: 10px 12px;
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    background: var(--surface);
    font-size: 13px;
  }

  .up-head { display: flex; align-items: center; gap: 8px; width: 100%; }
  .up-name { font-weight: 600; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .up-size { color: var(--muted); font-variant-numeric: tabular-nums; white-space: nowrap; }

  .up-x {
    margin-left: auto;
    display: grid;
    place-items: center;
    width: 22px;
    height: 22px;
    padding: 0;
    border: 0;
    border-radius: 50%;
    background: none;
    color: var(--muted);
    cursor: pointer;
  }
  .up-x:hover { background: var(--surface-2); color: var(--ink); }

  .up-note { color: var(--ink-2); font-variant-numeric: tabular-nums; }
  .is-done .up-note { color: var(--ok); }
  .is-failed .up-note, .is-refused .up-note { color: var(--risk); }

  progress {
    width: 100%;
    height: 6px;
    border: 0;
    border-radius: var(--radius-pill);
    background: var(--surface-2);
    overflow: hidden;
    appearance: none;
  }
  progress::-webkit-progress-bar { background: var(--surface-2); }
  progress::-webkit-progress-value { background: var(--accent-fill); transition: width 160ms ease; }
  progress::-moz-progress-bar { background: var(--accent-fill); }

  /* Reading the export: no fraction to show, so a band that sweeps across. */
  progress:indeterminate {
    background: linear-gradient(90deg, var(--surface-2) 0 30%, var(--accent-fill) 50%, var(--surface-2) 70% 100%)
      0 0 / 300% 100%;
    animation: sweep 1.2s linear infinite;
  }
  progress:indeterminate::-webkit-progress-bar { background: transparent; }
  progress:indeterminate::-moz-progress-bar { background: transparent; }

  @keyframes sweep { from { background-position: 100% 0; } to { background-position: 0 0; } }

  @media (prefers-reduced-motion: reduce) {
    .zone, .zone.over { transition: none; transform: none; }
    progress::-webkit-progress-value { transition: none; }
    progress:indeterminate { animation: none; background: var(--accent-soft); }
  }

  .sr {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }
</style>
