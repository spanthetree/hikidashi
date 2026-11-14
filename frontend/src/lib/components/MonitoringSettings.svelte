<script lang="ts">
  /* Logging and monitoring (admins): the syslog, Loki and Prometheus
     settings, saved on the server and on at once. One set in the server's
     environment wins and is shown locked. Secrets are write-only. */
  import { onMount } from "svelte";
  import { getMonitoring, setMonitoring } from "#lib/api.js";
  import { m } from "#lib/paraglide/messages.js";
  import type { Monitoring, MonitoringSetting, NoticeState } from "#lib/types.js";

  let { notice = $bindable() }: { notice: NoticeState } = $props();

  let page = $state.raw<Monitoring | null>(null);
  let draft = $state<Record<string, string | boolean>>({});
  let saving = $state(false);

  const LABEL: Record<string, () => string> = {
    HIKIDASHI_METRICS: m.mon_metrics,
    HIKIDASHI_METRICS_TOKEN: m.mon_metrics_token,
    HIKIDASHI_METRICS_ALLOW: m.mon_metrics_allow,
    HIKIDASHI_METRICS_PORT: m.mon_metrics_port,
    HIKIDASHI_METRICS_REFRESH: m.mon_metrics_refresh,
    HIKIDASHI_METRICS_PER_USER: m.mon_metrics_per_user,
    HIKIDASHI_LOG_LEVEL: m.mon_log_level,
    HIKIDASHI_LOG_FORMAT: m.mon_log_format,
    HIKIDASHI_SYSLOG: m.mon_syslog,
    HIKIDASHI_SYSLOG_FORMAT: m.mon_syslog_format,
    HIKIDASHI_SYSLOG_FACILITY: m.mon_syslog_facility,
    HIKIDASHI_LOKI_URL: m.mon_loki_url,
    HIKIDASHI_LOKI_TOKEN: m.mon_loki_token,
    HIKIDASHI_LOKI_USER: m.mon_loki_user,
    HIKIDASHI_LOKI_PASSWORD: m.mon_loki_password,
    HIKIDASHI_LOKI_TENANT: m.mon_loki_tenant,
    HIKIDASHI_LOKI_LABELS: m.mon_loki_labels,
  };
  // Examples: the same in every language.
  const EXAMPLE: Record<string, string> = {
    HIKIDASHI_SYSLOG: "udp://192.168.1.10:514",
    HIKIDASHI_LOKI_URL: "http://loki:3100",
    HIKIDASHI_LOKI_LABELS: "env=home",
    HIKIDASHI_METRICS_PORT: "9464",
  };

  const groups = $derived(page ? [
    { title: m.mon_group_metrics(), fields: page.settings.filter((s) => s.name.startsWith("HIKIDASHI_METRICS")) },
    { title: m.mon_group_logs(), fields: page.settings.filter((s) => !s.name.startsWith("HIKIDASHI_METRICS")) },
  ] : []);

  function load(got: Monitoring) {
    page = got;
    // Secrets start empty: typing one replaces what is saved.
    draft = Object.fromEntries(got.settings.map((s) => [s.name, s.kind === "secret" ? "" : s.value]));
  }

  onMount(() => { getMonitoring().then(load).catch((e) => (notice = { kind: "error", text: e.message })); });

  /* Only what changed, and nothing the environment sets. */
  const changes = $derived.by(() => {
    const out: Record<string, string | boolean | null> = {};
    for (const s of page?.settings ?? []) {
      if (s.source === "env") continue;
      const now = draft[s.name];
      if (s.kind === "secret") { if (typeof now === "string" && now.trim()) out[s.name] = now.trim(); }
      else if (now !== s.value) out[s.name] = now === "" ? null : now;
    }
    return out;
  });

  async function save(extra: Record<string, string | boolean | null> = {}) {
    saving = true;
    try {
      const got = await setMonitoring({ ...changes, ...extra });
      load(got);
      notice = got.problem ? { kind: "error", text: m.mon_problem({ problem: got.problem }) }
        : { kind: "ok", text: m.saved() };
    } catch (e) {
      notice = { kind: "error", text: (e as Error).message };
    } finally {
      saving = false;
    }
  }

  const status = $derived(page ? [
    page.metrics
      ? m.mon_status_metrics_on({ where: page.metrics_port ? `:${page.metrics_port}/metrics` : "/metrics" })
      : m.mon_status_metrics_off(),
    page.shipping.length ? m.mon_status_ship({ to: page.shipping.join(", ") }) : m.mon_status_ship_none(),
  ] : []);

  const id = (s: MonitoringSetting) => `mon-${s.name}`;
</script>

{#if page}
  <p class="mon-status" role="status">{status.join(" · ")}</p>
  <form onsubmit={(e) => { e.preventDefault(); void save(); }}>
    {#each groups as g (g.title)}
      <fieldset class="mon-group">
        <legend>{g.title}</legend>
        {#each g.fields as s (s.name)}
          {@const locked = s.source === "env"}
          <div class="mon-row">
            {#if s.kind === "bool"}
              <label class="mon-check">
                <input id={id(s)} type="checkbox" disabled={locked}
                       checked={Boolean(draft[s.name])}
                       onchange={(e) => (draft[s.name] = e.currentTarget.checked)}>
                {LABEL[s.name]()}
              </label>
            {:else}
              <label for={id(s)}>{LABEL[s.name]()}</label>
              {#if s.kind === "choice"}
                <select id={id(s)} disabled={locked} bind:value={draft[s.name]}>
                  <option value="">{m.mon_default({ value: s.default })}</option>
                  {#each s.choices as c (c)}<option value={c}>{c}</option>{/each}
                </select>
              {:else if s.kind === "secret"}
                <span class="mon-secret">
                  <input id={id(s)} type="password" autocomplete="off" spellcheck="false" disabled={locked}
                         placeholder={s.value ? m.mon_secret_saved() : ""} bind:value={draft[s.name]}>
                  {#if s.value && !locked}
                    <button class="btn" type="button" disabled={saving}
                            onclick={() => save({ [s.name]: null })}>{m.mon_secret_remove()}</button>
                  {/if}
                </span>
              {:else}
                <input id={id(s)} type="text" autocomplete="off" spellcheck="false" disabled={locked}
                       placeholder={s.default || EXAMPLE[s.name] || ""} bind:value={draft[s.name]}>
              {/if}
            {/if}
            <code class="mon-env">{locked ? m.mon_from_env({ name: s.name }) : s.name}</code>
          </div>
        {/each}
      </fieldset>
    {/each}
    <div class="mon-actions">
      <button class="btn primary" type="submit" disabled={saving || !Object.keys(changes).length}>{m.save()}</button>
      <span class="hint">{m.mon_remote_note()}</span>
    </div>
  </form>
{/if}

<style>
  .mon-status { margin: 0 0 10px; font-size: 13.5px; color: var(--ink-2); }
  form { display: flex; flex-direction: column; gap: 14px; }
  .mon-group { margin: 0; padding: 0; border: 0; display: flex; flex-direction: column; gap: 10px; }
  .mon-group legend { padding: 0 0 6px; font-family: var(--display); font-size: 13px; font-weight: 600; }
  .mon-row { display: grid; grid-template-columns: minmax(0, 14em) minmax(0, 1fr); gap: 2px 12px; align-items: center; }
  .mon-row > label:not(.mon-check) { font-size: 13.5px; }
  .mon-check { grid-column: 1 / -1; display: flex; gap: 8px; align-items: center; font-size: 13.5px; }
  .mon-row input[type="text"], .mon-row input[type="password"], .mon-row select { width: 100%; min-width: 0; }
  .mon-row :disabled { opacity: 0.6; cursor: not-allowed; }
  .mon-secret { display: flex; gap: 8px; align-items: center; min-width: 0; }
  .mon-env { grid-column: 2; font-family: var(--mono); font-size: 11px; color: var(--muted); overflow-wrap: anywhere; }
  .mon-check + .mon-env { grid-column: 1 / -1; }
  .mon-actions { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  @media (max-width: 560px) {
    .mon-row { grid-template-columns: minmax(0, 1fr); }
    .mon-env { grid-column: 1; }
  }
</style>
