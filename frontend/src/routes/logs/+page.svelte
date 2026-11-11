<script lang="ts">
  import { onMount, tick } from "svelte";
  import { page } from "$app/state";
  import { getLogs, type LogName } from "$lib/api";
  import Notice from "$lib/components/Notice.svelte";
  import Rich from "$lib/components/Rich.svelte";
  import Topbar from "$lib/components/Topbar.svelte";
  import { locale } from "$lib/i18n";
  import { m } from "$lib/paraglide/messages";
  import type { NoticeState } from "$lib/types";

  const POLL_MS = 3000;
  const LINES = 1000;

  type Kind = "info" | "warn" | "error" | "access";
  type Level = "all" | "info" | "warn";

  let lines = $state.raw<string[]>([]);
  let file = $state("");
  let updated = $state<Date | null>(null);
  let level = $state<Level>("all");
  // The server's own log, or a worker's on this host (/logs?log=worker). Under
  // Docker the server-worker, which fetches and embeds, writes its own.
  const asked = page.url.searchParams.get("log");
  let which = $state<LogName>(asked === "worker" || asked === "server-worker" ? asked : "server");
  let follow = $state(true);
  let notice = $state<NoticeState>(null);
  let out = $state<HTMLPreElement>();

  // "2026-10-21 16:02:11,532 WARNING uvicorn.error  semantic search off…"
  // A traceback's continuation lines have no level; they take the one above.
  function classify(all: string[]) {
    let last: Kind = "info";
    return all.map((text) => {
      const m = /^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+ (\w+)\s+(\S+)/.exec(text);
      if (m) {
        const [, lvl, name] = m;
        last = lvl === "WARNING" ? "warn"
          : lvl === "ERROR" || lvl === "CRITICAL" ? "error"
          : name === "uvicorn.access" ? "access" : "info";
      }
      return { text, kind: last };
    });
  }

  const SHOWN: Record<Level, (k: Kind) => boolean> = {
    all: () => true,
    info: (k) => k !== "access",
    warn: (k) => k === "warn" || k === "error",
  };

  const shown = $derived(classify(lines).filter((l) => SHOWN[level](l.kind)));

  // Stick to the end while following, after every render of new lines.
  $effect(() => {
    void shown;
    if (follow) void tick().then(() => { if (out) out.scrollTop = out.scrollHeight; });
  });

  async function refresh() {
    try {
      const data = await getLogs(LINES, which);
      const changed = data.lines.length !== lines.length ||
        data.lines[data.lines.length - 1] !== lines[lines.length - 1];
      if (changed) lines = data.lines;
      file = data.file;
      updated = new Date();
      notice = null;
    } catch (err) {
      notice = { text: m.logs_failed({ error: (err as Error).message }), kind: "error" };
    }
  }

  // Scrolling up to read stops following; scrolling back to the end resumes it.
  function scrolled() {
    if (!out) return;
    const atEnd = out.scrollHeight - out.scrollTop - out.clientHeight < 8;
    if (follow !== atEnd) follow = atEnd;
  }

  onMount(() => {
    let timer: ReturnType<typeof setInterval> | undefined;
    const start = () => { void refresh(); timer = setInterval(refresh, POLL_MS); };
    // No polling while the tab is hidden.
    const visibility = () => { clearInterval(timer); if (!document.hidden) start(); };
    start();
    document.addEventListener("visibilitychange", visibility);
    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", visibility); };
  });
</script>

<svelte:head><title>{m.logs_title()} · Hikidashi</title></svelte:head>

<Topbar />

<main class="page logs">
  <a class="back" href="/">{m.logs_back()}</a>
  <header>
    <h1>{m.logs_title()}</h1>
    <p class="lede"><Rich parts={m.logs_lede.parts()} /></p>
  </header>

  <Notice bind:notice />

  <div class="l-bar">
    <label>{m.logs_which()}
      <select bind:value={which} onchange={() => { lines = []; void refresh(); }}>
        <option value="server">{m.logs_server()}</option>
        <option value="worker">{m.logs_worker()}</option>
        <option value="server-worker">{m.logs_server_worker()}</option>
      </select>
    </label>
    <label>{m.logs_show()}
      <select bind:value={level}>
        <option value="all">{m.logs_level_all()}</option>
        <option value="info">{m.logs_level_info()}</option>
        <option value="warn">{m.logs_level_warn()}</option>
      </select>
    </label>
    <label><input type="checkbox" bind:checked={follow}> {m.logs_follow()}</label>
    {#if updated}
      <span class="l-status">{m.logs_updated({ file, time: updated.toLocaleTimeString(locale) })}</span>
    {/if}
  </div>

  <pre class="l-out" bind:this={out} onscroll={scrolled}>{#each shown as l, i (i)}<span class="l-{l.kind}">{l.text}</span>{"\n"}{:else}<span class="l-access">{level === "all" ? m.logs_empty() : m.logs_empty_level()}</span>{/each}</pre>
</main>

<style>
  .logs { max-width: 1100px; }

  .l-bar { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; font-size: 13px; color: var(--ink-2); }
  .l-bar label { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; }
  .l-bar select { font: inherit; }
  .l-bar input { accent-color: var(--accent); }
  .l-status { margin-left: auto; font-family: var(--mono); font-size: 11px; color: var(--muted); overflow-wrap: anywhere; }

  .l-out {
    margin: 0;
    height: calc(100vh - 250px);
    min-height: 240px;
    overflow: auto;
    padding: 12px 14px;
    background: var(--surface);
    border: 1px solid var(--rule);
    border-radius: var(--radius);
    font-family: var(--mono);
    font-size: 12px;
    line-height: 1.55;
    color: var(--ink-2);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }

  .l-warn { color: var(--warn); }
  .l-error { color: var(--risk); font-weight: 600; }
  .l-access { color: var(--muted); }
</style>
