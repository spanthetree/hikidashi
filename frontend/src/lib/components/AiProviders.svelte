<script lang="ts">
  /* Hosted AI (#116, #125): one API's providers, keys, budget and switch.
     `whose` is "mine" (a user's own, on the Account page) or "server" (the
     server's, for admins). Keys are write-only: once saved they come back
     only as sk-…abcd. */
  import { onMount } from "svelte";
  import { ai, getMyPipeline, type AiWhose } from "#lib/api.js";
  import { ago, nf } from "#lib/format.js";
  import { m } from "#lib/paraglide/messages.js";
  import {
    AI_STAGES, type AiBacklog, type AiPage, type AiStage, type AiTest, type AiUse, type AiUserRow,
    type MyPipeline, type NoticeState,
  } from "#lib/types.js";

  let { whose, notice = $bindable() }: { whose: AiWhose; notice: NoticeState } = $props();

  let page = $state.raw<AiPage | null>(null);
  let models = $state<Record<string, string[]>>({});
  let keyDraft = $state<Record<string, string>>({});
  let budget = $state<number | null>(null);
  let language = $state("");
  let tests = $state.raw<Partial<Record<AiStage, AiTest>>>({});
  let testing = $state(false);
  let asking = $state.raw<AiBacklog | null>(null);   // the backlog, while turning on
  let scope = $state<"me" | "all">("me");
  let users = $state.raw<AiUserRow[]>([]);
  let mine = $state.raw<MyPipeline | null>(null);

  // Examples and a currency code: the same in every language.
  const URL_EXAMPLE = "http://server.lan:8000/v1";
  const LANGUAGE_EXAMPLE = "ja";
  const USD = "USD";

  const STAGE_LABEL: Record<AiStage, () => string> = {
    asr: m.ai_stage_asr, ocr: m.ai_stage_ocr, tag: m.ai_stage_tag, recipe: m.ai_stage_recipe,
  };

  const preset = (name: string | null) => page?.presets.find((p) => p.name === name) ?? null;
  /* The providers its stages use: whose keys this page asks for. */
  const used = $derived(
    page ? [...new Set(AI_STAGES.map((s) => page!.stages[s].provider.value).filter((p): p is string => !!p))] : [],
  );
  const locked = (source: string) => source.startsWith("env:");
  const envName = (source: string) => source.slice(4);

  function show(p: AiPage) {
    page = p;
    budget = p.budget.value;
    language = p.language.value ?? "";
    if (p.switch.scope) scope = p.switch.scope;
  }

  async function act(change: () => Promise<unknown>, done?: string) {
    try {
      const got = await change();
      if (got && typeof got === "object" && "stages" in got) show(got as AiPage);
      notice = done ? { text: done, kind: "ok" } : null;
      return true;
    } catch (err) {
      notice = { text: (err as Error).message, kind: "error" };
      return false;
    }
  }

  async function load() {
    await act(() => ai.get(whose));
    if (whose === "server") ai.users().then((u) => (users = u)).catch(() => {});
    else getMyPipeline().then((p) => (mine = p)).catch(() => {});
  }

  onMount(load);

  async function listModels(provider: string | null) {
    if (!provider || models[provider]) return;
    try {
      models[provider] = await ai.models(whose, provider);
    } catch {
      models[provider] = [];
    }
  }

  const setStage = (stage: AiStage | "all", body: { provider?: string; model?: string; base_url?: string }) =>
    act(() => ai.setStage(whose, stage, body), m.saved());

  async function saveKey(provider: string) {
    const key = (keyDraft[provider] ?? "").trim();
    if (!key) return;
    if (await act(() => ai.setKey(whose, provider, key), m.ai_key_saved())) keyDraft[provider] = "";
  }

  const saveBudget = (e: SubmitEvent) => {
    e.preventDefault();
    // A number input binds a number, or null when empty.
    const n = typeof budget === "number" && !Number.isNaN(budget) ? budget : null;
    return act(() => ai.setBudget(whose, n), m.saved());
  };

  const saveLanguage = (e: SubmitEvent) => {
    e.preventDefault();
    return act(() => ai.setLanguage(whose, language.trim() || null), m.saved());
  };

  async function test() {
    testing = true;
    try {
      tests = await ai.test(whose);
    } catch (err) {
      notice = { text: (err as Error).message, kind: "error" };
    } finally {
      testing = false;
    }
  }

  async function startOn() {
    try {
      asking = await ai.backlog(whose, whose === "server" ? scope : undefined);
    } catch (err) {
      notice = { text: (err as Error).message, kind: "error" };
    }
  }

  async function confirmOn(backlog: boolean) {
    const ok = await act(
      () => ai.switchOn(whose, backlog, whose === "server" ? scope : undefined), m.ai_on_done());
    if (ok) asking = null;
  }

  const off = () => act(() => ai.switchOff(whose), m.ai_off_done());
  const setUse = (use: AiUse) => act(() => ai.setUse(use), m.saved());

  const cost = (b: AiBacklog) =>
    Math.abs(b.usd_high - b.usd_low) < 0.005
      ? m.ai_cost_about({ usd: b.usd_low.toFixed(2) })
      : m.ai_cost_range({ low: b.usd_low.toFixed(2), high: b.usd_high.toFixed(2) });
</script>

{#if page}
  {@const sw = page.switch}
  <div class="ai">
    {#if whose === "mine" && page.applies}
      <p class="ai-applies">
        {#if page.applies === "mine"}{m.ai_applies_mine()}
        {:else if page.applies === "server"}{m.ai_applies_server({ providers: page.server?.providers.join(", ") ?? "" })}
        {:else}{m.ai_applies_workers()}{/if}
      </p>
      <fieldset class="ai-use">
        <legend>{m.ai_use_title()}</legend>
        {#each (["default", "mine", "server", "workers"] as AiUse[]) as u (u)}
          {#if u !== "server" || page.server?.available || page.use === "server"}
            <label>
              <input type="radio" name="ai-use" value={u} checked={page.use === u}
                     onchange={() => setUse(u)}>
              {({ default: m.ai_use_default, mine: m.ai_use_mine, server: m.ai_use_server, workers: m.ai_use_workers })[u]()}
            </label>
          {/if}
        {/each}
        <p class="hint">{m.ai_use_hint()}</p>
      </fieldset>
    {/if}

    <div class={['ai-switch', { on: sw.on }]}>
      <div>
        <b>{sw.on ? (whose === "server" ? (sw.scope === "all" ? m.ai_on_all() : m.ai_on_me()) : m.ai_on())
               : m.ai_off()}</b>
        {#if !sw.on && sw.reason}<span class="ai-reason">{m.ai_off_because({ reason: sw.reason })}</span>{/if}
        <span class="hint">{m.ai_spent({ usd: page.spent.toFixed(2), budget: page.budget.value != null ? String(page.budget.value) : "–" })}</span>
      </div>
      {#if whose === "server"}
        <div class="ai-scope" role="radiogroup" aria-label={m.ai_scope_label()}>
          <label><input type="radio" name="ai-scope" value="me" bind:group={scope}> {m.ai_scope_me()}</label>
          <label><input type="radio" name="ai-scope" value="all" bind:group={scope}> {m.ai_scope_all()}</label>
        </div>
      {/if}
      <div class="a-row">
        {#if !sw.on || (whose === "server" && scope !== sw.scope)}
          <button class="btn primary" type="button" disabled={page.problems.length > 0} onclick={startOn}>
            {sw.on ? m.ai_change_scope() : m.ai_turn_on()}
          </button>
        {/if}
        {#if sw.on}<button class="btn" type="button" onclick={off}>{m.ai_turn_off()}</button>{/if}
      </div>
      {#if page.problems.length && !sw.on}
        <ul class="ai-problems">{#each page.problems as p (p)}<li>{p}</li>{/each}</ul>
      {/if}
      <p class="hint">{m.ai_switch_hint()}</p>
    </div>

    {#if asking}
      <div class="ai-ask" role="dialog" aria-label={m.ai_backlog_title()}>
        <p>{m.ai_backlog({ count: asking.reels, cost: cost(asking) })}</p>
        {#if asking.unpriced.length}<p class="hint">{m.ai_unpriced({ models: asking.unpriced.join(", ") })}</p>{/if}
        <div class="a-row">
          <button class="btn primary" type="button" onclick={() => confirmOn(true)}>{m.ai_process_these()}</button>
          <button class="btn" type="button" onclick={() => confirmOn(false)}>{m.ai_only_new()}</button>
          <button class="link" type="button" onclick={() => (asking = null)}>{m.cancel()}</button>
        </div>
      </div>
    {/if}

    <div class="a-table">
      <table class="ai-stages">
        <thead><tr><th>{m.ai_stage()}</th><th>{m.ai_provider()}</th><th>{m.ai_model()}</th></tr></thead>
        <tbody>
          {#each AI_STAGES as s (s)}
            {@const st = page.stages[s]}
            {@const p = preset(st.provider.value)}
            <tr>
              <td>{STAGE_LABEL[s]()}</td>
              <td>
                <select aria-label={m.ai_provider_for({ stage: STAGE_LABEL[s]() })}
                        disabled={locked(st.provider.source)} value={st.provider.value ?? ""}
                        onchange={(e) => setStage(s, { provider: e.currentTarget.value })}>
                  <option value="">–</option>
                  {#each page.presets.filter((x) => x.stages.includes(s)) as x (x.name)}
                    <option value={x.name}>{x.label}</option>
                  {/each}
                </select>
                {#if locked(st.provider.source)}<span class="hint">{m.ai_env({ name: envName(st.provider.source) })}</span>{/if}
                {#if p?.needs_base_url}
                  <input type="url" placeholder={URL_EXAMPLE} value={st.base_url.value ?? ""}
                         aria-label={m.ai_base_url()} disabled={locked(st.base_url.source)}
                         onchange={(e) => setStage(s, { base_url: e.currentTarget.value })}>
                {/if}
              </td>
              <td>
                <input list={`ai-models-${s}`} placeholder={st.default_model ?? ""} value={st.model.value ?? ""}
                       aria-label={m.ai_model_for({ stage: STAGE_LABEL[s]() })} disabled={!p || locked(st.model.source)}
                       onfocus={() => listModels(st.provider.value)}
                       onchange={(e) => setStage(s, { model: e.currentTarget.value })}>
                <datalist id={`ai-models-${s}`}>
                  {#each models[st.provider.value ?? ""] ?? [] as id (id)}<option value={id}></option>{/each}
                </datalist>
                {#if locked(st.model.source)}<span class="hint">{m.ai_env({ name: envName(st.model.source) })}</span>{/if}
                {#if s === "asr" && p && !p.segments.includes(st.model.value ?? st.default_model ?? "")}
                  <span class="hint ai-warn">{m.ai_no_music_guard()}</span>
                {/if}
                {#if tests[s]}
                  <span class={['ai-test', { bad: !tests[s]!.ok }]} title={tests[s]!.detail}>{tests[s]!.said}</span>
                {/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    <div class="a-row">
      <button class="btn" type="button" disabled={testing || !used.length} onclick={test}>
        {testing ? m.ai_testing() : m.ai_test()}
      </button>
    </div>

    {#each used as name (name)}
      {@const k = page.keys[name]}
      {@const p = preset(name)}
      <div class="ai-key">
        <span class="x-label">{m.ai_key_for({ provider: p?.label ?? name })}</span>
        {#if k?.set}
          <code>{k.masked}</code>
          {#if locked(k.source)}<span class="hint">{m.ai_env({ name: envName(k.source) })}</span>
          {:else}<button class="link" type="button" onclick={() => act(() => ai.removeKey(whose, name), m.ai_key_removed())}>{m.ai_key_remove()}</button>{/if}
        {/if}
        {#if !locked(k?.source ?? "")}
          <form class="a-row" onsubmit={(e) => { e.preventDefault(); saveKey(name); }}>
            <input type="password" autocomplete="off" spellcheck="false" placeholder={k?.set ? m.ai_key_replace() : m.ai_key_paste()}
                   aria-label={m.ai_key_for({ provider: p?.label ?? name })} bind:value={keyDraft[name]}>
            <button class="btn" type="submit" disabled={!(keyDraft[name] ?? "").trim()}>{m.save()}</button>
          </form>
        {/if}
        {#if k?.problem}<span class="hint ai-warn">{k.problem}</span>{/if}
        {#if p}<span class="hint">{m.ai_terms({ free: p.free, terms: p.terms, checked: p.checked })}</span>{/if}
      </div>
    {/each}

    <form class="a-row" onsubmit={saveBudget}>
      <label for={`ai-budget-${whose}`}>{m.ai_budget()}</label>
      <input id={`ai-budget-${whose}`} type="number" min="0.01" step="0.01" inputmode="decimal"
             disabled={locked(page.budget.source)} bind:value={budget}>
      <span class="hint">{USD}</span>
      <button class="btn" type="submit" disabled={locked(page.budget.source)}>{m.save()}</button>
      {#if locked(page.budget.source)}<span class="hint">{m.ai_env({ name: envName(page.budget.source) })}</span>{/if}
    </form>
    <form class="a-row" onsubmit={saveLanguage}>
      <label for={`ai-lang-${whose}`}>{m.ai_language()}</label>
      <input id={`ai-lang-${whose}`} type="text" maxlength="3" size="4" placeholder={LANGUAGE_EXAMPLE}
             disabled={locked(page.language.source)} bind:value={language}>
      <button class="btn" type="submit" disabled={locked(page.language.source)}>{m.save()}</button>
      <span class="hint">{m.ai_language_hint()}</span>
    </form>

    <p class="hint ai-privacy">{m.ai_privacy()}</p>
    {#if whose === "mine"}<p class="hint">{m.ai_privacy_shared()}</p>{/if}

    {#if mine}
      <div class="ai-mine">
        <span class="x-label">{m.ai_your_reels()}</span>
        <span class="hint">
          {mine.stages.map((s) => `${s.stage} ${nf.format(s.waiting)}`).join(" · ")}
          · {mine.worker_seen_at ? m.ai_worker_seen({ ago: ago(mine.worker_seen_at, mine.now) }) : m.ai_no_worker()}
          {#if mine.api.on}· {m.ai_in_flight({ count: mine.api.in_flight })}{/if}
        </span>
      </div>
    {/if}

    {#if whose === "server" && users.length}
      <div class="a-table">
        <table>
          <thead><tr><th>{m.ai_user()}</th><th>{m.ai_their_choice()}</th><th>{m.ai_their_keys()}</th><th class="num">{m.ai_this_month()}</th></tr></thead>
          <tbody>
            {#each users as u (u.id)}
              <tr>
                <td>{u.username}{#if u.on}<span class="a-flag">{m.ai_on()}</span>{/if}</td>
                <td>{u.use}</td>
                <td>
                  {#each u.keys as k (k)}
                    <span>{k}</span>
                    <button class="link" type="button"
                            onclick={() => act(async () => { users = await ai.removeUserKey(u.id, k); }, m.ai_key_removed())}>
                      {m.ai_key_remove()}</button>
                  {:else}–{/each}
                </td>
                <td class="num">{u.spent.toFixed(2)}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  </div>
{/if}

<style>
  .ai { display: flex; flex-direction: column; gap: 12px; font-size: 13.5px; }
  .ai-applies { margin: 0; font-weight: 500; }
  .ai-use { display: flex; flex-wrap: wrap; gap: 6px 16px; border: 0; padding: 0; margin: 0; }
  .ai-use legend { font-size: 13px; color: var(--ink-2); margin-bottom: 4px; }
  .ai-use .hint { flex-basis: 100%; margin: 0; }
  .ai-switch { display: flex; flex-direction: column; gap: 8px; padding: 12px; border-radius: var(--radius);
               background: var(--surface-2); }
  .ai-switch.on { background: var(--accent-soft); }
  .ai-switch > div:first-child { display: flex; flex-wrap: wrap; gap: 4px 10px; align-items: baseline; }
  .ai-reason { color: var(--danger, #b3261e); }
  .ai-scope { display: flex; gap: 16px; }
  .ai-problems { margin: 0; padding-left: 18px; color: var(--ink-2); }
  .ai-ask { padding: 12px; border: 1px solid var(--accent); border-radius: var(--radius); }
  .ai-ask p { margin: 0 0 8px; }
  .ai-stages td { vertical-align: top; white-space: normal; }
  .ai-stages select, .ai-stages input, .ai-key input, form input {
    padding: 6px 8px; background: var(--field); border: 1px solid transparent; border-radius: var(--radius);
    font: inherit; max-width: 100%;
  }
  .ai-stages td > * + * { display: block; margin-top: 4px; }
  .ai-key { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; padding-top: 8px;
            border-top: 1px solid var(--rule); }
  .ai-key .hint { flex-basis: 100%; margin: 0; }
  .ai-warn { color: var(--danger, #b3261e); }
  .ai-test { font-size: 12px; color: var(--accent); }
  .ai-test.bad { color: var(--danger, #b3261e); }
  .ai-privacy { margin: 0; }
  .ai-mine { display: flex; flex-direction: column; gap: 2px; }
  .x-label { font-size: 13px; color: var(--ink-2); }
  .a-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .a-table { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; font-weight: 500; color: var(--muted); font-size: 12px; padding: 0 10px 6px 0; }
  td { padding: 7px 10px 7px 0; border-top: 1px solid var(--rule); }
  .num { text-align: right; font-variant-numeric: tabular-nums; }
  .a-flag { margin-left: 6px; padding: 1px 8px; font-size: 11.5px; font-weight: 600; color: var(--accent);
            background: var(--accent-soft); border-radius: var(--radius-pill); }
  .link { padding: 0; background: none; border: 0; color: var(--accent); font-size: 13px; cursor: pointer; }
  .link:hover { text-decoration: underline; }
  /* A phone: each stage a block of its own, provider above model. */
  @media (max-width: 700px) {
    .ai-stages thead { display: none; }
    .ai-stages tr { display: grid; grid-template-columns: minmax(0, 1fr); gap: 6px; padding: 8px 0;
                    border-top: 1px solid var(--rule); }
    .ai-stages td { padding: 0; border: 0; }
    .ai-stages td:first-child { font-weight: 600; }
    .ai-stages select, .ai-stages input { width: 100%; }
  }

  input:focus, select:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); outline: none; }
</style>
