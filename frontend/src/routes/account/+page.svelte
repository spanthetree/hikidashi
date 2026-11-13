<script lang="ts">
  import { onMount } from "svelte";
  import {
    changeAccount, changePassword, exportUrl, getAccounts, getExportOptions, getInvites, getMyTokens,
    logout, makeInvite, makeMyToken, revokeInvite, revokeMyToken, signOutEverywhere, updateMe,
  } from "#lib/api.js";
  import AiProviders from "#lib/components/AiProviders.svelte";
  import Notice from "#lib/components/Notice.svelte";
  import InfoHead from "#lib/components/InfoHead.svelte";
  import Rich from "#lib/components/Rich.svelte";
  import Topbar from "#lib/components/Topbar.svelte";
  import { copyFailure, copyText, type Copied } from "#lib/clipboard.js";
  import { ago, nf, savedDate } from "#lib/format.js";
  import { tagLabel } from "#lib/i18n.js";
  import { m } from "#lib/paraglide/messages.js";
  import { session } from "#lib/session.svelte.js";
  import type { Account, ApiToken, ExportOptions, Invite, MyTokens, NoticeState } from "#lib/types.js";

  const me = $derived(session.me);
  const local = $derived(me?.auth === "local");

  let notice = $state<NoticeState>(null);
  let handle = $state("");
  let current = $state("");
  let next = $state("");
  let accounts = $state.raw<Account[]>([]);
  let invites = $state.raw<Invite[]>([]);
  let fresh = $state<string | null>(null);      // a new invite link, shown once
  let copied = $state(false);

  /* ---------- API tokens (#103) ---------- */

  const SCOPE_HINT: Record<string, () => string> = {
    "read:library": m.tokens_read_library,
    "write:library": m.tokens_write_library,
    "read:ai": m.tokens_read_ai,
    "write:ai": m.tokens_write_ai,
    "read:admin": m.tokens_read_admin,
    "write:admin": m.tokens_write_admin,
  };
  const LIVES = [1, 7, 14, 30];

  let mine = $state.raw<MyTokens | null>(null);
  let tokenName = $state("");
  let scopes = $state<string[]>(["read:library"]);
  let days = $state(30);
  let making = $state(false);
  let freshToken = $state<{ name: string; token: string } | null>(null);
  let tokenCopied = $state<"token" | "example" | null>(null);

  // Something to try it with, against this server.
  const example = $derived(
    freshToken ? `curl -H "Authorization: Bearer ${freshToken.token}" ${location.origin}/api/stats` : "",
  );

  function tokenLine(t: ApiToken) {
    const now = Date.now() / 1000;
    const used = t.last_used_at ? m.pl_token_used({ ago: ago(t.last_used_at, now) }) : m.pl_token_unused();
    return `${t.scopes.join(" ")} · ${m.tokens_until({ date: savedDate(t.expires_at) })} · ${used}`;
  }

  async function makeToken(e: SubmitEvent) {
    e.preventDefault();
    const name = tokenName.trim();
    if (!name || !scopes.length) return;
    making = true;
    await act(async () => {
      const made = await makeMyToken(name, scopes, days);
      freshToken = { name, token: made.token };
      tokenCopied = null;
      mine = made;
      tokenName = "";
    });
    making = false;
  }

  async function copyToken(what: "token" | "example") {
    if (!freshToken) return;
    const field = document.querySelector<HTMLInputElement>(`#t-fresh-${what}`);
    const how = await copyText(what === "token" ? freshToken.token : example, field);
    tokenCopied = how === "copied" ? what : null;
    copyNotice(how);
  }

  /* A copy that didn't work says so at the top; one that did clears that. */
  function copyNotice(how: Copied) {
    if (how !== "copied") notice = { text: copyFailure(how), kind: "error" };
    else if (notice?.kind === "error") notice = null;
  }

  /* ---------- the export form ---------- */

  const FORMATS = [
    { kind: "recipes.json", group: m.export_recipes, label: m.export_recipes_jsonld,
      hint: m.export_recipes_jsonld_hint },
    { kind: "recipes-nextcloud.zip", group: m.export_recipes, label: m.export_recipes_nextcloud,
      hint: m.export_recipes_nextcloud_hint },
    { kind: "recipes.paprikarecipes", group: m.export_recipes, label: m.export_recipes_paprika,
      hint: m.export_recipes_paprika_hint },
    { kind: "markdown.zip", group: m.export_library, label: m.export_markdown,
      hint: m.export_markdown_hint },
    { kind: "library.json", group: m.export_library, label: m.export_json, hint: m.export_json_hint },
    { kind: "library.csv", group: m.export_library, label: m.export_csv, hint: m.export_csv_hint },
  ] as const;

  let kind = $state<string>("recipes-nextcloud.zip");
  let owner = $state("");            // whose library; "" is yours
  let scope = $state("all");         // all | recipes | c:<collection> | t:<tag>
  let images = $state(false);
  let videos = $state(false);
  let options = $state.raw<ExportOptions | null>(null);
  let people = $state.raw<Account[]>([]);

  const format = $derived(FORMATS.find((f) => f.kind === kind) ?? FORMATS[0]);
  const forRecipes = $derived(kind.startsWith("recipes"));

  // A new library has other collections and tags, so the scope starts over.
  $effect(() => {
    const who = owner;
    options = null;
    scope = "all";
    getExportOptions(who || undefined).then((o) => { if (who === owner) options = o; })
      .catch((err) => { notice = { text: (err as Error).message, kind: "error" }; });
  });

  const href = $derived.by(() => {
    const p = new URLSearchParams();
    if (owner) p.set("user", owner);
    if (scope === "recipes") p.set("recipes", "true");
    else if (scope.startsWith("c:")) p.set("collection", scope.slice(2));
    else if (scope.startsWith("t:")) p.set("tag", scope.slice(2));
    if (kind === "markdown.zip" && (images || videos)) p.set("images", "true");
    if (kind === "markdown.zip" && videos) p.set("videos", "true");
    return exportUrl(kind, p);
  });

  /* How many reels the choice covers, when that is known. */
  const covered = $derived.by(() => {
    if (!options) return null;
    if (scope === "all") return forRecipes ? null : options.total;
    if (scope === "recipes") return options.recipes;
    const name = scope.slice(2);
    const list = scope.startsWith("c:") ? options.collections : options.tags;
    return list.find((x) => x.name === name)?.count ?? null;
  });

  onMount(async () => {
    await session.load();
    if (session.me?.is_admin) getAccounts().then((a) => (people = a)).catch(() => {});
    handle = session.me?.ig_handle ?? "";
    if (session.me && session.me.auth !== "none") {
      getMyTokens().then((t) => (mine = t))
        .catch((err) => { notice = { text: (err as Error).message, kind: "error" }; });
    }
    if (session.me?.is_admin && session.me.auth === "local") {
      [accounts, invites] = await Promise.all([getAccounts(), getInvites()]);
    }
  });

  /* Runs a change, and says how it went in the notice at the top. */
  async function act(change: () => Promise<unknown>, done?: string) {
    try {
      await change();
      notice = done ? { text: done, kind: "ok" } : null;
      return true;
    } catch (err) {
      notice = { text: (err as Error).message, kind: "error" };
      return false;
    }
  }

  async function saveHandle(e: SubmitEvent) {
    e.preventDefault();
    await act(async () => {
      const updated = await updateMe(handle);
      session.set(updated);
      handle = updated.ig_handle ?? "";
    }, m.saved());
  }

  async function savePassword(e: SubmitEvent) {
    e.preventDefault();
    if (await act(() => changePassword(current, next), m.password_changed())) {
      current = next = "";
    }
  }

  async function signOut() {
    await logout();
    location.assign("/login");
  }

  const toggle = (a: Account, change: { disabled?: boolean; is_admin?: boolean }) =>
    act(async () => { accounts = await changeAccount(a.id, change); });

  async function invite() {
    await act(async () => {
      const made = await makeInvite();
      fresh = location.origin + made.path;
      copied = false;
      invites = await getInvites();
    });
  }

  async function copy() {
    if (!fresh) return;
    const how = await copyText(fresh, document.querySelector<HTMLInputElement>("#a-fresh"));
    copied = how === "copied";
    copyNotice(how);
  }
</script>

<svelte:head><title>{m.account_title()} · Hikidashi</title></svelte:head>

<Topbar />

<main class="page account">
  <a class="back" href="/">{m.logs_back()}</a>
  <header>
    <h1>{m.account_title()}</h1>
    {#if me}
      <p class="lede">
        {#if local}{m.account_signed_in({ name: me.username })}
        {:else if me.auth === "proxy"}{m.account_proxy({ name: me.username })}
        {:else}<Rich parts={m.account_no_auth.parts()} />{/if}
      </p>
    {/if}
  </header>

  <Notice bind:notice />

  {#if me}
    <section class="a-sec">
      <form class="a-row" onsubmit={saveHandle}>
        <label for="a-handle">{m.handle_label()}</label>
        <span class="a-at">@<input id="a-handle" type="text" maxlength="31" autocapitalize="none"
                                   spellcheck="false" bind:value={handle}></span>
        <button class="btn" type="submit">{m.save()}</button>
      </form>
      <p class="hint">{m.handle_hint()}</p>
    </section>

    <section class="a-sec">
      <InfoHead title={m.export_title()}>{m.export_lede()}</InfoHead>
      <div class="a-export">
        {#if people.length > 1}
          <label for="x-owner">{m.export_whose()}</label>
          <select id="x-owner" bind:value={owner}>
            <option value="">{m.export_yours()}</option>
            {#each people.filter((p) => p.id !== me.id) as p (p.id)}
              <option value={p.username}>{p.username}</option>
            {/each}
          </select>
        {/if}

        <span class="x-label" id="x-format">{m.export_format()}</span>
        <div class="x-formats" role="radiogroup" aria-labelledby="x-format">
          {#each [m.export_recipes, m.export_library] as group (group)}
            <span class="x-group">{group()}</span>
            {#each FORMATS.filter((f) => f.group === group) as f (f.kind)}
              <label class="x-format">
                <input type="radio" name="x-format" value={f.kind} bind:group={kind}>
                {f.label()}
              </label>
            {/each}
          {/each}
        </div>
        <span></span>
        <p class="hint">
          {#if kind === "markdown.zip"}<Rich parts={m.export_markdown_hint.parts()} />{:else}{format.hint()}{/if}
        </p>

        <label for="x-scope">{m.export_what()}</label>
        <select id="x-scope" bind:value={scope} disabled={!options}>
          <option value="all">{forRecipes ? m.export_all_recipes() : m.export_everything()}</option>
          {#if !forRecipes}<option value="recipes">{m.export_only_recipes()}</option>{/if}
          {#if options?.collections.length}
            <optgroup label={m.export_collections()}>
              {#each options.collections as c (c.name)}
                <option value={`c:${c.name}`}>{c.name} ({nf.format(c.count)})</option>
              {/each}
            </optgroup>
          {/if}
          {#if options?.tags.length}
            <optgroup label={m.export_tags()}>
              {#each options.tags as t (t.name)}
                <option value={`t:${t.name}`}>{tagLabel(t.name)} ({nf.format(t.count)})</option>
              {/each}
            </optgroup>
          {/if}
        </select>

        {#if kind === "markdown.zip"}
          <span class="x-label x-top">{m.export_media()}</span>
          <div class="x-checks">
            <label><input type="checkbox" bind:checked={images}> {m.export_with_images()}</label>
            <label><input type="checkbox" bind:checked={videos}> {m.export_with_videos()}</label>
            {#if images || videos}<p class="hint">{m.export_media_hint()}</p>{/if}
          </div>
        {/if}

        <span></span>
        <div class="a-row">
          <a class="btn primary" href={href} download>{m.export_download()}</a>
          {#if options}
            <span class="hint">
              {#if covered !== null}{m.export_covers({ count: covered })}
              {:else}{m.export_count({ count: nf.format(options.total), recipes: nf.format(options.recipes) })}{/if}
            </span>
          {/if}
        </div>
      </div>
    </section>

    <section class="a-sec" id="ai">
      <InfoHead title={m.ai_title()}>{m.ai_lede()}</InfoHead>
      <!-- One choice each, for your own library. An admin sets the system
           default, which "System default" follows, on the Pipeline page. -->
      <AiProviders whose="mine" bind:notice />
    </section>

    {#if mine}
      <section class="a-sec">
        <InfoHead title={m.tokens_title()}><Rich parts={m.tokens_lede.parts()} /></InfoHead>
        <form class="a-export" onsubmit={makeToken}>
          <label for="t-name">{m.tokens_name()}</label>
          <input id="t-name" type="text" maxlength="64" required autocomplete="off"
                 placeholder={m.tokens_placeholder()} bind:value={tokenName}>

          <span class="x-label x-top" id="t-scopes">{m.tokens_scopes()}</span>
          <div class="t-scopes" role="group" aria-labelledby="t-scopes">
            {#each mine.scopes as s (s)}
              <label>
                <input type="checkbox" value={s} bind:group={scopes}>
                <code>{s}</code>
                <span class="hint">{SCOPE_HINT[s]?.() ?? ""}</span>
              </label>
            {/each}
          </div>

          <label for="t-days">{m.tokens_life()}</label>
          <select id="t-days" bind:value={days}>
            {#each LIVES.filter((d) => d <= mine!.max_days) as d (d)}
              <option value={d}>{m.tokens_days({ count: d })}</option>
            {/each}
          </select>
          <span></span>
          <p class="hint">{m.tokens_life_hint({ days: mine.max_days })}</p>

          <span></span>
          <div class="a-row">
            <button class="btn primary" type="submit"
                    disabled={making || !tokenName.trim() || !scopes.length}>{m.tokens_make()}</button>
          </div>
        </form>
        {#if freshToken}
          <div class="a-fresh">
            <label for="t-fresh-token">{m.tokens_new({ name: freshToken.name })}</label>
            <div class="a-row">
              <input id="t-fresh-token" type="text" readonly value={freshToken.token}
                     onfocus={(e) => e.currentTarget.select()}>
              <button class="btn" type="button" aria-live="polite" onclick={() => copyToken("token")}>
                {tokenCopied === "token" ? m.invites_copied() : m.invites_copy()}
              </button>
            </div>
            <label for="t-fresh-example">{m.tokens_example()}</label>
            <div class="a-row">
              <input id="t-fresh-example" type="text" readonly value={example}
                     onfocus={(e) => e.currentTarget.select()}>
              <button class="btn" type="button" aria-live="polite" onclick={() => copyToken("example")}>
                {tokenCopied === "example" ? m.invites_copied() : m.invites_copy()}
              </button>
            </div>
          </div>
        {/if}
        {#each mine.tokens as t (t.id)}
          <div class="a-invite">
            <div class="t-text">
              <b>{t.name}</b>
              <span class="hint">{tokenLine(t)}</span>
            </div>
            <button class="link" type="button"
                    onclick={() => act(async () => { mine = await revokeMyToken(t.id); },
                                       m.tokens_revoked({ name: t.name }))}>{m.invites_revoke()}</button>
          </div>
        {:else}
          <p class="hint">{m.tokens_none()}</p>
        {/each}
      </section>
    {/if}

    {#if local}
      <section class="a-sec">
        <h2>{m.password_change()}</h2>
        <form class="a-grid" onsubmit={savePassword}>
          <!-- For password managers, which file a new password under the account. -->
          <input type="text" autocomplete="username" value={me.username} hidden readonly>
          <label for="a-current">{m.password_current()}</label>
          <input id="a-current" type="password" autocomplete="current-password" required
                 bind:value={current}>
          <label for="a-new">{m.password_new()}</label>
          <input id="a-new" type="password" autocomplete="new-password" required minlength="8"
                 bind:value={next}>
          <span></span>
          <p class="hint">{m.password_hint()}</p>
          <span></span>
          <button class="btn" type="submit">{m.password_change()}</button>
        </form>
      </section>

      <section class="a-sec a-buttons">
        <button class="btn" type="button"
                onclick={() => act(signOutEverywhere, m.signed_out_elsewhere())}>{m.sign_out_everywhere()}</button>
        <button class="btn" type="button" onclick={signOut}>{m.sign_out()}</button>
      </section>

      {#if me.is_admin}
        <section class="a-sec">
          <InfoHead title={m.people_title()}>{m.people_lede()}</InfoHead>
          <div class="a-table">
            <table>
              <thead>
                <tr><th>{m.username()}</th><th>{m.handle_label()}</th><th class="num">{m.people_reels()}</th><th></th><th></th></tr>
              </thead>
              <tbody>
                {#each accounts as a (a.id)}
                  <tr class:off={a.disabled}>
                    <td>
                      {a.username}
                      {#if a.id === me.id}<span class="a-flag">{m.people_you()}</span>{/if}
                      {#if a.is_admin}<span class="a-flag">{m.people_admin()}</span>{/if}
                      {#if a.disabled}<span class="a-flag is-off">{m.people_disabled()}</span>{/if}
                      {#if !a.has_password}<span class="a-flag is-off">{m.people_no_password()}</span>{/if}
                    </td>
                    <td>{a.ig_handle ? `@${a.ig_handle}` : ""}</td>
                    <td class="num">{nf.format(a.reels)}</td>
                    <td>
                      {#if a.id !== me.id}
                        <button class="link" type="button" onclick={() => toggle(a, { is_admin: !a.is_admin })}>
                          {a.is_admin ? m.people_unmake_admin() : m.people_make_admin()}
                        </button>
                      {/if}
                    </td>
                    <td>
                      {#if a.id !== me.id}
                        <button class="link" type="button" onclick={() => toggle(a, { disabled: !a.disabled })}>
                          {a.disabled ? m.people_enable() : m.people_disable()}
                        </button>
                      {/if}
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        </section>

        <section class="a-sec">
          <InfoHead title={m.invites_title()}>{m.invites_lede()}</InfoHead>
          <div class="a-buttons">
            <button class="btn primary" type="button" onclick={invite}>{m.invites_make()}</button>
          </div>
          {#if fresh}
            <div class="a-fresh">
              <label for="a-fresh">{m.invites_new()}</label>
              <div class="a-row">
                <input id="a-fresh" type="text" readonly value={fresh}
                       onfocus={(e) => e.currentTarget.select()}>
                <button class="btn" type="button" aria-live="polite" onclick={copy}>{copied ? m.invites_copied() : m.invites_copy()}</button>
              </div>
            </div>
          {/if}
          {#each invites as inv (inv.id)}
            <div class="a-invite">
              <span>{m.invites_expires({ name: inv.created_by, date: savedDate(inv.expires_at) })}</span>
              <button class="link" type="button"
                      onclick={() => act(async () => { invites = await revokeInvite(inv.id); })}>{m.invites_revoke()}</button>
            </div>
          {:else}
            <p class="hint">{m.invites_none()}</p>
          {/each}
        </section>
      {/if}
    {/if}
  {/if}

  <section class="a-sec a-about">
    <h2>{m.about_title()}</h2>
    <p>{m.about_license()}
      <a href="https://github.com/spanthetree/hikidashi#license" target="_blank" rel="noopener noreferrer">{m.about_terms()}</a></p>
    <p class="hint">{m.about_not_affiliated()}</p>
  </section>
</main>

<style>
  .account { max-width: 760px; }

  .account :global(h2) { margin: 0; font-size: 16px; }

  /* The export form: a label beside each choice; stacked on a phone. */
  .a-export {
    display: grid;
    grid-template-columns: max-content minmax(0, 1fr);
    align-items: center;
    gap: 10px 14px;
  }

  .a-export > label, .x-label { font-size: 13px; color: var(--ink-2); }
  .a-export > .hint { margin: -4px 0 0; }
  .a-export select { justify-self: start; max-width: 100%; padding: 7px 10px; background: var(--field);
                     border: 1px solid transparent; border-radius: var(--radius); font: inherit; }
  .a-export .btn { text-decoration: none; }
  #x-format { align-self: start; padding-top: 6px; }
  .x-top { align-self: start; padding-top: 1px; }

  .x-formats { display: flex; flex-wrap: wrap; gap: 6px; min-width: 0; }
  .x-group { flex-basis: 100%; font-size: 12px; font-weight: 600; color: var(--muted); margin-top: 4px; }
  .x-group:first-child { margin-top: 0; }

  .x-format {
    display: inline-flex;
    align-items: center;
    padding: 6px 12px;
    font-size: 13.5px;
    background: var(--surface-2);
    border: 1px solid transparent;
    border-radius: var(--radius-pill);
    cursor: pointer;
  }

  .x-format:has(input:checked) { color: var(--accent); background: var(--accent-soft); border-color: var(--accent); }
  .x-format:has(input:focus-visible) { box-shadow: 0 0 0 3px var(--accent-soft); }
  .x-format input { position: absolute; opacity: 0; pointer-events: none; }

  .x-checks { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 16px; font-size: 13.5px; }
  .x-checks .hint { flex-basis: 100%; margin: 0; }

  .a-sec {
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 18px;
    background: var(--surface);
    border: 1px solid var(--rule);
    border-radius: var(--radius);
  }

  .a-about p { margin: 0; font-size: 13.5px; color: var(--ink-2); }
  .a-about a { color: var(--accent); }

  .a-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .a-row label { font-size: 14px; }

  .a-grid {
    display: grid;
    grid-template-columns: max-content minmax(0, 280px);
    align-items: center;
    gap: 8px 12px;
  }

  .a-grid label { font-size: 13px; color: var(--ink-2); }
  .a-grid button { justify-self: start; }

  input:not([type="checkbox"]) {
    padding: 7px 10px;
    background: var(--field);
    border: 1px solid transparent;
    border-radius: var(--radius);
    min-width: 0;
  }

  input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); outline: none; }

  .a-at { display: inline-flex; align-items: center; gap: 4px; color: var(--muted); }
  .a-at input { color: var(--ink); }

  .a-buttons { flex-direction: row; flex-wrap: wrap; gap: 10px; display: flex; }

  .a-table { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
  th { text-align: left; font-weight: 500; color: var(--muted); font-size: 12px; padding: 0 10px 6px 0; }
  td { padding: 7px 10px 7px 0; border-top: 1px solid var(--rule); white-space: nowrap; }
  .num { text-align: right; font-variant-numeric: tabular-nums; }
  tr.off td { color: var(--muted); }

  .a-flag {
    margin-left: 6px;
    padding: 1px 8px;
    font-size: 11.5px;
    font-weight: 600;
    color: var(--accent);
    background: var(--accent-soft);
    border-radius: var(--radius-pill);
  }

  .a-flag.is-off { color: var(--muted); background: var(--surface-2); }

  .link {
    padding: 0;
    background: none;
    border: 0;
    color: var(--accent);
    font-size: 13px;
    cursor: pointer;
  }

  .link:hover { text-decoration: underline; text-underline-offset: 2px; }

  .a-fresh { display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
  .a-fresh input { flex: 1; font-family: var(--mono); font-size: 12px; }

  .t-scopes { display: flex; flex-direction: column; gap: 6px; font-size: 13.5px; min-width: 0; }
  .t-scopes label { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 8px; }
  .t-scopes code { font-size: 12.5px; }
  .t-text { display: flex; flex-direction: column; gap: 2px; min-width: 0; overflow-wrap: anywhere; }
  .t-text b { color: var(--ink); }

  .a-invite {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    padding-top: 8px;
    border-top: 1px solid var(--rule);
    font-size: 13px;
    color: var(--ink-2);
  }

  @media (max-width: 700px) {
    .a-grid, .a-export { grid-template-columns: minmax(0, 1fr); }
    .a-export > span:empty { display: none; }
    .a-grid > span { display: none; }
  }
</style>
