<script lang="ts">
  import { onMount } from "svelte";
  import {
    getAllTokens, getPipeline, getWorkerTokens, makeWorkerToken, retryStage, revokeAnyToken, revokeWorkerToken,
  } from "#lib/api.js";
  import Notice from "#lib/components/Notice.svelte";
  import Rich from "#lib/components/Rich.svelte";
  import Topbar from "#lib/components/Topbar.svelte";
  import { copyFailure, copyText } from "#lib/clipboard.js";
  import { ago, list, nf, savedDate } from "#lib/format.js";
  import { stageLabel } from "#lib/i18n.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { ApiToken, NoticeState, PipelineState, Stage, WorkerInfo, WorkerToken } from "#lib/types.js";

  const POLL_MS = 5000;

  let data = $state<PipelineState | null>(null);
  let notice = $state<NoticeState>(null);
  let busy = $state<Stage | "fetch" | null>(null);

  const STATE_LABEL = { running: m.pl_running, stopped: m.pl_stopped, lost: m.pl_lost };

  // Tokens for workers on other machines (#35). A new one is shown once.
  let tokens = $state.raw<WorkerToken[]>([]);
  let tokensLoaded = false;
  let tokenName = $state("");
  let fresh = $state<{ name: string; token: string } | null>(null);
  let copied = $state<"token" | "command" | null>(null);
  let making = $state(false);

  // What to run on the other machine, with this server's address filled in.
  const command = $derived(
    fresh ? `HIKIDASHI_SERVER=${location.origin} HIKIDASHI_WORKER_TOKEN=${fresh.token} uv run hikidashi worker` : "",
  );

  const used = (t: { last_used_at: number | null }, now: number) =>
    t.last_used_at ? m.pl_token_used({ ago: ago(t.last_used_at, now) }) : m.pl_token_unused();

  function tokenLine(t: WorkerToken, now: number) {
    return `${m.pl_token_made_by({ name: t.created_by, date: savedDate(t.created_at) })} · ${used(t, now)}`;
  }

  // Everyone's API tokens (#103): who can reach what from a script.
  let apiTokens = $state.raw<ApiToken[]>([]);

  function apiTokenLine(t: ApiToken, now: number) {
    return `${t.owner} · ${t.scopes.join(" ")} · ${m.tokens_until({ date: savedDate(t.expires_at) })} · ${used(t, now)}`;
  }

  async function loadTokens() {
    try {
      [tokens, apiTokens] = await Promise.all([getWorkerTokens(), getAllTokens()]);
    } catch (err) {
      notice = { text: (err as Error).message, kind: "error" };
    }
  }

  async function revokeApiToken(t: ApiToken) {
    try {
      apiTokens = await revokeAnyToken(t.id);
      notice = { text: m.pl_api_token_revoked({ name: t.name, owner: t.owner }), kind: "ok" };
    } catch (err) {
      notice = { text: (err as Error).message, kind: "error" };
    }
  }

  async function makeToken(e: SubmitEvent) {
    e.preventDefault();
    const name = tokenName.trim();
    if (!name) return;
    making = true;
    try {
      const made = await makeWorkerToken(name);
      fresh = { name, token: made.token };
      copied = null;
      tokens = made.tokens;
      tokenName = "";
    } catch (err) {
      notice = { text: (err as Error).message, kind: "error" };
    } finally {
      making = false;
    }
  }

  async function revoke(t: WorkerToken) {
    try {
      tokens = await revokeWorkerToken(t.id);
      notice = { text: m.pl_token_revoked({ name: t.name }), kind: "ok" };
    } catch (err) {
      notice = { text: (err as Error).message, kind: "error" };
    }
  }

  async function copy(what: "token" | "command") {
    if (!fresh) return;
    const field = document.querySelector<HTMLInputElement>(`#p-fresh-${what}`);
    const how = await copyText(what === "token" ? fresh.token : command, field);
    copied = how === "copied" ? what : null;
    if (how !== "copied") notice = { text: copyFailure(how), kind: "error" };
    else if (notice?.kind === "error") notice = null;
  }

  async function refresh() {
    try {
      data = await getPipeline();
      if (notice?.kind === "error") notice = null;
      // Admin-only like the pipeline, so asked for once it has loaded: asked
      // alongside, its refusal could land last and hide the pipeline's.
      if (!tokensLoaded) {
        tokensLoaded = true;
        void loadTokens();
      }
    } catch (err) {
      notice = { text: m.pl_load_failed({ error: (err as Error).message }), kind: "error" };
    }
  }

  async function retry(stage: Stage | "fetch") {
    busy = stage;
    try {
      data = await retryStage(stage);
      notice = { text: stage === "fetch" ? m.pl_fetch_retried() : m.pl_retried({ stage: stageLabel(stage) }), kind: "ok" };
    } catch (err) {
      notice = { text: (err as Error).message, kind: "error" };
    } finally {
      busy = null;
    }
  }

  function seen(w: WorkerInfo, now: number) {
    const parts = [m.pl_seen({ ago: ago(w.seen_at, now) })];
    if (w.doing) parts.push(m.pl_busy({ stage: stageLabel(w.doing) }));
    return parts.join(" · ");
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

<svelte:head><title>{m.pl_title()} · Hikidashi</title></svelte:head>

<Topbar />

<main class="page pipeline">
  <a class="back" href="/">{m.logs_back()}</a>
  <header>
    <h1>{m.pl_title()}</h1>
    <p class="lede">{m.pl_lede()}</p>
  </header>

  <Notice bind:notice />

  {#if data}
    <section class="p-sec" aria-labelledby="p-workers">
      <h2 id="p-workers">{m.pl_workers()}</h2>
      {#each data.workers as w (w.name)}
        <article class="p-worker is-{w.state}" class:has-problem={Object.keys(w.problems).length > 0}>
          <div class="p-head">
            <span class="p-dot" aria-hidden="true"></span>
            <b class="p-name">{w.name}</b>
            <span class="p-state">{STATE_LABEL[w.state]()}</span>
          </div>
          {#if w.state === "running"}
            <p class="p-line">
              {w.stages.length ? m.pl_runs({ stages: list(w.stages.map(stageLabel)) }) : m.pl_runs_none()}
              · {seen(w, data.now)}
            </p>
            {#each Object.entries(w.problems) as [stage, why] (stage)}
              <p class="p-problem">{m.pl_problem({ stage: stageLabel(stage), why: why ?? "" })}</p>
            {/each}
          {:else if w.state === "stopped" && w.stopped_at}
            <p class="p-line">{m.pl_stopped_at({ ago: ago(w.stopped_at, data.now) })}</p>
          {:else}
            <p class="p-line p-lost">{m.pl_lost_body({ ago: ago(w.seen_at, data.now) })}</p>
          {/if}
        </article>
      {:else}
        <p class="p-empty"><Rich parts={m.pl_none.parts()} /></p>
      {/each}
    </section>

    {#if data.apis.some((a) => a.on || a.reason || a.spent)}
      <section class="p-sec" aria-labelledby="p-apis">
        <h2 id="p-apis">{m.pipeline_apis()}</h2>
        {#each data.apis.filter((a) => a.on || a.reason || a.spent) as a (a.name)}
          <article class="p-worker" class:is-running={a.on} class:has-problem={!a.on && !!a.reason}>
            <div class="p-head">
              <span class="p-dot" aria-hidden="true"></span>
              <b class="p-name">{a.name}</b>
              {#if a.on !== null}
                <span class="p-state">{a.on ? (a.scope === "all" ? m.ai_on_all() : a.scope === "me" ? m.ai_on_me() : m.ai_on()) : m.ai_off()}</span>
              {/if}
            </div>
            {#if a.on === null}
              <p class="p-line">{m.pipeline_api_hosted({ spent: a.spent.toFixed(2) })}</p>
            {:else}
              <p class="p-line">{m.pipeline_api_line({ waiting: a.waiting ?? 0, in_flight: a.in_flight ?? 0, spent: a.spent.toFixed(2) })}</p>
              {#if !a.on && a.reason}<p class="p-problem">{m.ai_off_because({ reason: a.reason })}</p>{/if}
            {/if}
          </article>
        {/each}
      </section>
    {/if}

    <section class="p-sec" aria-labelledby="p-tokens">
      <h2 id="p-tokens">{m.pl_tokens_title()}</h2>
      <p class="p-lede"><Rich parts={m.pl_tokens_lede.parts()} /></p>
      <form class="p-make" onsubmit={makeToken}>
        <input id="p-token-name" type="text" maxlength="64" required autocomplete="off"
               aria-label={m.pl_token_name()} placeholder={m.pl_token_placeholder()} bind:value={tokenName}>
        <button class="btn primary" type="submit" disabled={making || !tokenName.trim()}>{m.pl_token_make()}</button>
      </form>
      {#if fresh}
        <div class="p-fresh">
          <label for="p-fresh-token">{m.pl_token_new({ name: fresh.name })}</label>
          <div class="p-row">
            <input id="p-fresh-token" type="text" readonly value={fresh.token}
                   onfocus={(e) => e.currentTarget.select()}>
            <button class="btn" type="button" aria-live="polite" onclick={() => copy("token")}>
              {copied === "token" ? m.invites_copied() : m.invites_copy()}
            </button>
          </div>
          <label for="p-fresh-command">{m.pl_token_command()}</label>
          <div class="p-row">
            <input id="p-fresh-command" type="text" readonly value={command}
                   onfocus={(e) => e.currentTarget.select()}>
            <button class="btn" type="button" aria-live="polite" onclick={() => copy("command")}>
              {copied === "command" ? m.invites_copied() : m.invites_copy()}
            </button>
          </div>
          <p class="hint"><Rich parts={m.pl_token_docker.parts()} /></p>
        </div>
      {/if}
      {#each tokens as t (t.id)}
        <div class="p-token">
          <div class="p-token-text">
            <b>{t.name}</b>
            <span class="hint">{tokenLine(t, data.now)}</span>
          </div>
          <button class="link" type="button" onclick={() => revoke(t)}>{m.invites_revoke()}</button>
        </div>
      {:else}
        <p class="p-empty">{m.pl_tokens_none()}</p>
      {/each}
    </section>

    <section class="p-sec" aria-labelledby="p-api-tokens">
      <h2 id="p-api-tokens">{m.pl_api_tokens_title()}</h2>
      <p class="p-lede"><Rich parts={m.pl_api_tokens_lede.parts()} /></p>
      {#each apiTokens as t (t.id)}
        <div class="p-token">
          <div class="p-token-text">
            <b>{t.name}</b>
            <span class="hint">{apiTokenLine(t, data.now)}</span>
          </div>
          <button class="link" type="button" onclick={() => revokeApiToken(t)}>{m.invites_revoke()}</button>
        </div>
      {:else}
        <p class="p-empty">{m.pl_api_tokens_none()}</p>
      {/each}
    </section>

    <section class="p-sec" aria-labelledby="p-stages">
      <h2 id="p-stages">{m.pl_stages()}</h2>
      <div class="p-table">
        <table>
          <thead>
            <tr>
              <th>{m.pl_stage()}</th>
              <th class="num">{m.pl_ready()}</th>
              <th class="num">{m.pl_waiting()}</th>
              <th class="num">{m.pl_working()}</th>
              <th class="num">{m.pl_failed()}</th>
            </tr>
          </thead>
          <tbody>
            {#each data.stages as s (s.stage)}
              <tr>
                <td>{stageLabel(s.stage)}</td>
                <td class="num">{nf.format(s.ready)}</td>
                <td class="num">{nf.format(s.waiting)}</td>
                <td class="num">{nf.format(s.working)}</td>
                <td class="num" class:is-failed={s.failed > 0}>{nf.format(s.failed)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
      <p class="hint">{m.pl_legend({ attempts: data.attempts })}</p>
      {#if data.stages.some((s) => s.stage === "embed" && s.without.length > 0)}
        <p class="p-without">{m.pl_embed_captions()}</p>
      {/if}

      {#if data.fetch.failed > 0}
        <div class="p-failed">
          <details class="p-failures">
            <summary>{m.pl_fetch_failures({ count: data.fetch.failed })}</summary>
            <ul>
              {#each data.fetch.failures as f (f.shortcode)}
                <li>
                  {#if f.permalink}
                    <a href={f.permalink} target="_blank" rel="noopener noreferrer"><code>{f.shortcode}</code></a>
                  {:else}<code>{f.shortcode}</code>{/if}
                  <span>{f.reason ?? m.pl_no_reason()}</span>
                  <span class="hint">{m.pl_tries({ count: f.attempts })} · {
                    f.permanent ? m.pl_fetch_permanent() : f.retrying ? m.pl_fetch_retrying() : m.pl_fetch_gave_up()
                  }</span>
                </li>
              {/each}
            </ul>
          </details>
          {#if data.fetch.retryable > 0}
            <button class="link" type="button" disabled={busy === "fetch"}
                    onclick={() => retry("fetch")}>{m.pl_retry()}</button>
          {/if}
        </div>
      {/if}

      {#each data.stages.filter((s) => s.failures.length) as s (s.stage)}
        <div class="p-failed">
          <details class="p-failures">
            <summary>{m.pl_failures({ stage: stageLabel(s.stage), count: nf.format(s.failed) })}</summary>
            <ul>
              {#each s.failures as f (f.shortcode)}
                <li>
                  {#if f.permalink}
                    <a href={f.permalink} target="_blank" rel="noopener noreferrer"><code>{f.shortcode}</code></a>
                  {:else}<code>{f.shortcode}</code>{/if}
                  <span>{f.reason ?? m.pl_no_reason()}</span>
                  <span class="hint">{m.pl_tries({ count: f.attempts })} · {ago(f.last_at, data.now)}</span>
                </li>
              {/each}
            </ul>
          </details>
          <button class="link" type="button" disabled={busy === s.stage}
                  onclick={() => retry(s.stage)}>{m.pl_retry()}</button>
        </div>
      {/each}
    </section>

    <p class="p-links">
      <a href="/logs">{m.pl_logs()}</a>
      <a href="/logs?log=worker">{m.pl_worker_log()}</a>
    </p>
  {/if}
</main>

<style>
  .pipeline { max-width: 760px; }

  .p-sec {
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 18px;
    background: var(--surface);
    border: 1px solid var(--rule);
    border-radius: var(--radius);
  }

  .p-sec h2 { margin: 0; font-size: 15px; }

  .p-worker { display: flex; flex-direction: column; gap: 4px; padding-top: 10px; border-top: 1px solid var(--rule); }
  .p-worker:first-of-type { padding-top: 0; border-top: 0; }

  .p-head { display: flex; align-items: center; gap: 8px; min-width: 0; }
  .p-name { font-size: 14px; overflow-wrap: anywhere; }
  .p-state { font-size: 12px; font-weight: 600; color: var(--muted); }

  .p-dot { flex: none; width: 8px; height: 8px; border-radius: 50%; background: var(--muted); }
  .is-running .p-dot { background: var(--ok); }
  .is-running .p-state { color: var(--ok); }
  .is-running.has-problem .p-dot { background: var(--warn); }
  .is-lost .p-dot { background: var(--risk); }
  .is-lost .p-state { color: var(--risk); }

  .p-line { margin: 0; font-size: 13px; color: var(--ink-2); }
  .p-lost { color: var(--ink); }
  .p-problem { margin: 0; font-size: 13px; color: var(--warn); overflow-wrap: anywhere; }
  .p-without { margin: 0; font-size: 13px; color: var(--warn); }
  .p-empty { margin: 0; font-size: 14px; color: var(--ink-2); }

  .p-table { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
  th { text-align: left; font-weight: 500; color: var(--muted); font-size: 12px; padding: 0 10px 6px 0; white-space: nowrap; }
  td { padding: 7px 10px 7px 0; border-top: 1px solid var(--rule); white-space: nowrap; }
  .num { text-align: right; font-variant-numeric: tabular-nums; }
  td.is-failed { color: var(--risk); font-weight: 600; }

  .link {
    padding: 0;
    background: none;
    border: 0;
    color: var(--accent);
    font-size: 13px;
    cursor: pointer;
  }

  .link:hover { text-decoration: underline; text-underline-offset: 2px; }
  .link:disabled { opacity: 0.5; cursor: default; }

  .p-failed { display: flex; align-items: flex-start; gap: 12px; }
  .p-failures { flex: 1; min-width: 0; }
  .p-failures summary { cursor: pointer; font-size: 13px; color: var(--ink-2); }
  .p-failures ul { margin: 8px 0 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 6px; }
  .p-failures li { display: flex; flex-wrap: wrap; gap: 4px 10px; font-size: 13px; overflow-wrap: anywhere; }
  .p-failures code { font-size: 12px; }

  .p-lede { margin: 0; font-size: 13.5px; color: var(--ink-2); }

  .p-make, .p-row { display: flex; gap: 10px; align-items: center; }

  .p-sec input {
    flex: 1;
    min-width: 0;
    padding: 7px 10px;
    background: var(--field);
    border: 1px solid transparent;
    border-radius: var(--radius);
  }

  .p-sec input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); outline: none; }
  .p-fresh { display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
  .p-fresh input { font-family: var(--mono); font-size: 12px; }

  .p-token {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px;
    padding-top: 8px;
    border-top: 1px solid var(--rule);
    font-size: 13px;
  }

  .p-token-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; overflow-wrap: anywhere; }

  .p-links { display: flex; gap: 16px; margin: 0; font-size: 13px; }
  .p-links a { color: var(--accent); }

  @media (max-width: 480px) {
    .p-sec { padding: 14px; }
    .p-make { flex-wrap: wrap; }
    .p-make button { width: 100%; }
    th, td { padding-right: 6px; }
    td { font-size: 13px; }
  }
</style>
