<script lang="ts">
  import { untrack } from "svelte";
  import { fade } from "svelte/transition";
  import { replaceState } from "$app/navigation";
  import { page } from "$app/state";
  import { clearGame, clearTag, exportUrl, getItem, getNote, getSimilar, markOpened, notACopy, removeItem, retryFetch, setGame, setTag } from "#lib/api.js";
  import ExportMenu from "#lib/components/ExportMenu.svelte";
  import MiniMap from "#lib/components/MiniMap.svelte";
  import RecipePanel from "#lib/components/RecipePanel.svelte";
  import Rich from "#lib/components/Rich.svelte";
  import { segments } from "#lib/caption.js";
  import { copyFailure, copyText } from "#lib/clipboard.js";
  import { paragraphs, readingTime } from "#lib/transcript.js";
  import { duration, megabytes, nf, profileUrl, savedDate, tint } from "#lib/format.js";
  import { tagLabel, tagName } from "#lib/i18n.js";
  import { sidebar } from "#lib/library.svelte.js";
  import { backdrop, motion, panel, receive, send } from "#lib/motion.svelte.js";
  import { m } from "#lib/paraglide/messages.js";
  import Icon from "./Icon.svelte";
  import type { FilterKind, Item, ItemDetail, ItemGame, ItemTag, Recipe, TagChoice } from "#lib/types.js";

  let { shortcode, preview, recipeFirst = false, onclose, onclosed, onfilter, ontagged,
        onerror, onchanged, onremoved, onanother }: {
    shortcode: string;
    /** The grid's copy of this reel, if it is loaded: enough to draw the
        picture and the header at once while the full record is fetched. */
    preview?: Item;
    /** Opened from the Recipes view: show the recipe before the details. */
    recipeFirst?: boolean;
    /** Asks to close: the parent navigates away from /item/…. */
    onclose: () => void;
    /** It has closed and the page behind is interactive again. */
    onclosed?: () => void;
    onfilter: (kind: FilterKind, value: string) => void;
    /** Its tag changed here; the grid and the sidebar counts are behind. */
    ontagged?: (shortcode: string, tag: string | null) => void;
    onerror: (message: string) => void;
    /** Its recipe was corrected, or it was fetched again, so counts and the
        grid may be stale. */
    onchanged?: () => void;
    /** It is out of your library now; `name` is what the sheet called it. */
    onremoved?: (shortcode: string, name: string) => void;
    /** Opened by Shuffle: another random reel in its place (#50). */
    onanother?: () => void;
  } = $props();

  let detail = $state<ItemDetail | null>(null);
  let similar = $state<Item[]>([]);
  let tab = $state<"recipe" | "details">("details");
  let panelEl = $state<HTMLElement>();
  let closeEl = $state<HTMLButtonElement>();

  $effect(() => {
    const code = shortcode;
    let stale = false;
    detail = null;
    slide = aim = 0;
    confirming = false;
    retryError = removeError = null;
    getItem(code)
      .then((d) => {
        if (stale) return;
        tab = recipeFirst && d.recipe && !d.recipe.hidden ? "recipe" : "details";
        detail = d;
      })
      .catch((err) => { if (!stale) onerror(err.message); });
    similar = [];
    void markOpened(code).catch(() => {});
    // Extra, not the reel itself: no error shown when it fails.
    getSimilar(code).then((r) => { if (!stale) similar = r.items; }).catch(() => {});
    return () => { stale = true; };
  });

  // Whatever is known so far: the full record once it arrives, the grid's
  // copy before then.
  const author = $derived(detail?.author ?? preview?.author ?? null);
  const title = $derived(detail?.author_name || author || shortcode);
  const savedAt = $derived(detail?.saved_at ?? preview?.saved_at ?? null);
  const video = $derived(detail ? detail.video : preview?.video ?? null);
  const thumb = $derived(detail ? detail.thumb : preview?.thumb ?? null);
  const slides = $derived(detail?.slides ?? []);
  const permalink = $derived(detail?.permalink ?? preview?.permalink);

  /* ?t= (seconds): where a search matched in the voiceover (#44). Start a
     moment before it, so the line is heard whole. */
  const startAt = $derived(Number(page.url.searchParams.get("t")) || 0);
  let videoEl = $state<HTMLVideoElement>();
  let now = $state(0); // ms into the video

  /* The transcript line by line when its timing is known: the one playing
     lights up, and pressing one plays from there. */
  const segs = $derived(detail?.segments ?? null);
  const current = $derived(segs ? segs.findLastIndex(([start]) => start <= now) : -1);
  const hit = $derived(segs && startAt ? segs.findIndex(([start]) => Math.abs(start - startAt * 1000) < 100) : -1);
  let linesEl = $state<HTMLElement>();

  function seekTo(ms: number) {
    if (videoEl) {
      videoEl.currentTime = ms / 1000;
      void videoEl.play().catch(() => {});
    }
    const url = new URL(page.url.href);
    url.searchParams.set("t", (ms / 1000).toFixed(1));
    replaceState(url, page.state);
  }

  // Keep the playing line in view inside the transcript, not the whole sheet.
  $effect(() => {
    const box = linesEl, i = current;
    const line = box?.children[i] as HTMLElement | undefined;
    if (!box || !line) return;
    if (line.offsetTop < box.scrollTop || line.offsetTop + line.offsetHeight > box.scrollTop + box.clientHeight) {
      box.scrollTo({ top: line.offsetTop - box.clientHeight / 3, behavior: motion.reduced ? "auto" : "smooth" });
    }
  });

  const facts = $derived(detail ? [
    [detail.duration ? duration(detail.duration) : null, m.fact_length()],
    [detail.like_count !== null ? nf.format(detail.like_count) : null, m.fact_likes()],
    [detail.comment_count !== null ? nf.format(detail.comment_count) : null, m.fact_comments()],
    [detail.width && detail.height ? `${detail.width}×${detail.height}` : null, m.fact_pixels()],
    [detail.bytes ? megabytes(detail.bytes) : null, m.fact_video()],
  ].filter(([v]) => v) as [string, string][] : []);

  // The picture grows out of its card in the grid, when that is loaded.
  const card = () =>
    document.querySelector(`[data-card="${CSS.escape(shortcode)}"] a.thumb`);

  const caption = $derived(detail?.caption ? segments(detail.caption, detail.places) : null);
  const located = $derived((detail?.places ?? []).filter(
    (p): p is typeof p & { lat: number; lng: number } => p.lat != null && p.lng != null));

  /* One tag per reel. Choosing one makes it yours, and no pass changes it
     after; taking yours off shows the model's or the hashtags' again. Even
     choosing the tag it already shows is worth doing: that confirms it, and
     makes the reel part of the calibration set (#6). */
  let newTag = $state("");
  let tagging = $state(false);
  let tagError = $state<string | null>(null);

  const shown = $derived<ItemTag | undefined>(detail?.tags[0]);
  const yours = $derived(shown?.source === "user");
  const SOURCE = { user: m.tag_source_user, game: m.tag_source_game, model: m.tag_source_model,
                   hashtag: m.tag_source_hashtag };
  /* The model says high, medium or low; app/tagging/llm.py stores 0.9, 0.6, 0.3. */
  const sure = (c: number) => (c >= 0.8 ? m.tag_sure_high() : c >= 0.5 ? m.tag_sure_mid() : m.tag_sure_low());
  const why = (t: ItemTag) => {
    const source = SOURCE[t.source]();
    return t.confidence === null ? m.tag_from({ source })
      : m.tag_from_sure({ source, sure: sure(t.confidence) });
  };
  /* The automatic answers that differ from the one shown. */
  const others = $derived(detail?.tags.slice(1).filter((t) => t.name !== shown?.name) ?? []);
  const note = $derived([
    ...(shown && !yours ? [why(shown)] : []),
    ...others.map((t) => m.tag_said({ source: SOURCE[t.source](), name: tagLabel(t.name) })),
  ].join(m.tag_note_sep()));

  const suggestions = $derived(
    sidebar.tags.filter((t) => t.name !== shown?.name).map((t) => t.name),
  );

  async function retag(change: Promise<TagChoice>) {
    const code = shortcode;
    tagging = true;
    tagError = null;
    try {
      const { tag, tags } = await change;
      if (detail?.shortcode === code) detail = { ...detail, tag, tags };
      ontagged?.(code, tag);
      return true;
    } catch (err) {
      tagError = (err as Error).message;
      return false;
    } finally {
      tagging = false;
    }
  }

  async function tag(e: SubmitEvent) {
    e.preventDefault();
    const name = tagName(newTag);
    if (!name || tagging) return;
    if (await retag(setTag(shortcode, name))) newTag = "";
  }

  const untag = () => retag(clearTag(shortcode));

  function tagKey(e: KeyboardEvent) {
    // Esc clears what is typed first, and closes the sheet only after.
    if (e.key === "Escape" && newTag) { e.preventDefault(); newTag = ""; }
  }

  /* The video game it shows (#117). A sure one is shown; a guess is asked
     about, to confirm, correct or turn down. Your answer is yours alone and
     no pass changes it; undoing it gives the reel back to the pass. */
  let newGame = $state("");
  let gaming = $state(false);
  let gameError = $state<string | null>(null);
  const game = $derived<ItemGame | undefined>(detail?.game);
  /* Worth a section: a game, a question, your answer, or a gaming reel the
     pass found none on, where you may name one. */
  const gameSection = $derived(!!game && (!!game.game || !!game.guess || game.yours
    || ["Gaming", "Sim racing"].includes(shown?.name ?? "")));

  async function regame(change: Promise<ItemGame>) {
    const code = shortcode;
    gaming = true;
    gameError = null;
    try {
      const answer = await change;
      if (detail?.shortcode === code) detail = { ...detail, game: answer };
      return true;
    } catch (err) {
      gameError = (err as Error).message;
      return false;
    } finally {
      gaming = false;
    }
  }

  async function nameGame(e: SubmitEvent) {
    e.preventDefault();
    const name = newGame.trim();
    if (!name || gaming) return;
    if (await regame(setGame(shortcode, name))) newGame = "";
  }

  function gameKey(e: KeyboardEvent) {
    if (e.key === "Escape" && newGame) { e.preventDefault(); newGame = ""; }
  }

  /* Ticked ingredients, while you cook. Kept for this sheet only: a new
     reel, or the same one opened again, starts unticked. */
  let ticked = $state<Record<number, boolean>>({});
  $effect(() => { void shortcode; ticked = {}; });

  function listed(on: boolean) {
    if (detail) detail = { ...detail, listed: on };
    void sidebar.refresh();
  }

  function recipeSaved(recipe: Recipe | null) {
    if (detail) detail = { ...detail, recipe };
    if (!recipe) tab = "details";
    onchanged?.();
    // The page's JSON-LD is the server's, so it follows the correction from there.
    const code = shortcode;
    getItem(code).then((d) => {
      if (detail && code === shortcode) detail = { ...detail, jsonld: d.jsonld };
    }).catch(() => {});
  }

  /* This reel, or its recipe, to take elsewhere. */
  let copied = $state(false);

  async function splitCopy(other: string) {
    try {
      await notACopy(shortcode, other);
      if (detail) detail = { ...detail, copies: detail.copies.filter((c) => c.shortcode !== other) };
      onchanged?.();
    } catch (err) {
      onerror((err as Error).message);
    }
  }

  async function copyNote() {
    let note: string;
    try {
      note = await getNote(shortcode);
    } catch (err) {
      onerror(m.export_copy_failed({ error: (err as Error).message }));
      return;
    }
    const how = await copyText(note);
    if (how !== "copied") {
      onerror(copyFailure(how));
      return;
    }
    copied = true;
    setTimeout(() => (copied = false), 2000);
  }

  const exports = $derived.by(() => {
    const at = (kind: string) => exportUrl(kind, new URLSearchParams({ shortcode }));
    const recipe = detail?.recipe && !detail.recipe.hidden;
    return [
      {
        title: m.export_this_recipe(),
        choices: recipe
          ? [
              { label: m.export_recipes_jsonld(), href: at("recipes.json") },
              { label: m.export_recipes_nextcloud(), href: at("recipes-nextcloud.zip") },
              { label: m.export_recipes_paprika(), href: at("recipes.paprikarecipes") },
            ]
          : [],
      },
      {
        title: m.export_this_reel(),
        choices: [
          { label: m.export_note(), href: at("note.md") },
          { label: copied ? m.export_copied() : m.export_copy_note(), onclick: copyNote, keep: true },
          { label: m.export_json(), href: at("library.json") },
        ],
      },
    ];
  });

  /* The recipe as schema.org JSON-LD, in the page's head while the sheet is
     open, where browser extensions and recipe managers look for one. Every
     "<" is escaped, so no text in a recipe can close the script early. */
  const jsonld = $derived(detail?.jsonld
    ? `<script type="application/ld+json">${JSON.stringify(detail.jsonld).replace(/</g, "\\u003c")}</` + "script>"
    : "");

  /* A fetch that failed can be tried again by anyone who has the reel saved:
     the reel is shared, so it works for everyone who has it. */
  let retrying = $state(false);
  let retryError = $state<string | null>(null);

  async function retry() {
    const code = shortcode;
    retrying = true;
    retryError = null;
    try {
      const fetched = await retryFetch(code);
      if (detail?.shortcode === code) detail = fetched;
      onchanged?.();
    } catch (err) {
      retryError = (err as Error).message;
    } finally {
      retrying = false;
    }
  }

  /* Removing asks first, in the sheet: it can't be undone short of
     importing the export again. */
  let confirming = $state(false);
  let removing = $state(false);
  let removeError = $state<string | null>(null);

  async function remove() {
    const code = shortcode;
    removing = true;
    removeError = null;
    try {
      await removeItem(code);
      onremoved?.(code, title);
    } catch (err) {
      removeError = (err as Error).message;
    } finally {
      removing = false;
    }
  }

  /* Arrow keys move between the two tabs, as a tablist's should. */
  function tabKey(e: KeyboardEvent) {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    tab = tab === "recipe" ? "details" : "recipe";
    (e.currentTarget as HTMLElement).parentElement
      ?.querySelector<HTMLElement>(`[data-tab="${tab}"]`)?.focus();
  }

  /* A carousel of photos is a row of slides that scrolls sideways and snaps,
     so a swipe on a phone is the browser's own. The buttons and the arrow
     keys scroll it a slide at a time; the scroll position says which is
     showing, however it got there. */
  let slide = $state(0);
  let trackEl = $state<HTMLElement>();
  /* Where a scroll is headed, so a second press while it is still moving
     counts from there and not from the slide it is passing. */
  let aim = 0;

  function step(by: number) {
    if (!trackEl) return;
    aim = Math.max(0, Math.min(slides.length - 1, aim + by));
    trackEl.scrollTo({ left: aim * trackEl.clientWidth,
                       behavior: motion.reduced ? "instant" : "smooth" });
  }

  const at = () => trackEl ? Math.round(trackEl.scrollLeft / Math.max(1, trackEl.clientWidth)) : 0;

  /* Left and right page through from anywhere in the sheet, except where they
     already mean something: a text field, the tabs, the video. */
  function slideKey(e: KeyboardEvent) {
    if (slides.length < 2 || e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    const el = e.target as HTMLElement;
    if (el.closest("input, textarea, select, video, [contenteditable], [role=tab]")) return;
    e.preventDefault();
    step(e.key === "ArrowRight" ? 1 : -1);
  }

  /* Muted is not decoration: browsers refuse to autoplay audible video, so an
     unmuted player would sit on the poster frame. On teardown the stream is
     cut, or it keeps downloading behind the grid. */
  const play = (el: HTMLVideoElement) => {
    el.muted = true;
    videoEl = el;
    const t = untrack(() => startAt);
    if (t) {
      const seek = () => { el.currentTime = Math.max(0, t - 1.5); };
      if (el.readyState >= 1) seek();
      else el.addEventListener("loadedmetadata", seek, { once: true });
    }
    el.play().catch(() => {});
    return () => { el.pause(); el.removeAttribute("src"); el.load(); };
  };

  /* A native modal <dialog>: the page behind goes inert and Esc arrives as a
     `cancel` event. Esc can't be a keydown handler, because once focus is in
     the video's own controls Chrome stops key events reaching the page. */
  const modal = (dialog: HTMLDialogElement) => {
    dialog.showModal();
    closeEl?.focus({ preventScroll: true });
    // Runs once the closing animation is over. Until the dialog closes the
    // page is still inert, so only now can focus go back to it.
    return () => {
      if (dialog.open) dialog.close();
      onclosed?.();
    };
  };

  function cancel(e: Event) {
    e.preventDefault();          // close with the outro, not instantly
    onclose();
  }

  /* Tab and Shift+Tab wrap around inside the panel rather than wandering
     out to the browser's own UI. */

  function trap(e: KeyboardEvent) {
    if (e.key !== "Tab" || !panelEl) return;
    const items = [...panelEl.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), input:not([disabled]), video[controls], [tabindex]:not([tabindex="-1"])',
    )].filter((el) => el.offsetParent !== null || el === document.activeElement);
    if (!items.length) return;
    const first = items[0], last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }

  /* Swipe down at phone width: the first swipe pushes the text away and gives
     the video the whole sheet, a second one closes it. Swipe up brings the
     text back. Only from the picture or the grab handle: the text column
     scrolls, and a drag there should scroll it. */
  let drag = $state<{ y0: number; t0: number; dy: number } | null>(null);
  let settling = $state(false);
  let full = $state(false);

  function down(e: PointerEvent) {
    if (!motion.narrow || e.pointerType === "mouse" || e.button !== 0) return;
    drag = { y0: e.clientY, t0: e.timeStamp, dy: 0 };
    settling = false;
  }

  function move(e: PointerEvent) {
    if (!drag) return;
    drag.dy = e.clientY - drag.y0;
    if (Math.abs(drag.dy) > 6) (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
  }

  function up(e: PointerEvent) {
    if (!drag) return;
    const { dy, t0 } = drag;
    const speed = Math.abs(dy) / Math.max(1, e.timeStamp - t0);   // px per ms
    drag = null;
    const far = Math.abs(dy) > 120 || (Math.abs(dy) > 30 && speed > 0.6);
    if (far && dy < 0) full = false;
    else if (far && !full) full = true;
    else if (far) onclose();
    else settling = full && dy > 0;   // spring back from a pull that fell short
  }

  /* Everything follows the finger. A swipe that would close moves the sheet;
     one that would grow or shrink the video moves the line between video and
     text (--grow), and on release it eases to wherever the swipe lands. */
  const pull = $derived(full && drag && drag.dy > 0 ? drag.dy : 0);
  const grow = $derived(drag && (full ? Math.min(0, drag.dy) : Math.max(0, drag.dy)));
  const panelStyle = $derived(pull ? `transform: translateY(${pull}px)`
                              : grow ? `--grow: ${grow}px` : undefined);
</script>

<svelte:head>
  {#if jsonld}{@html jsonld}{/if}
</svelte:head>

<dialog class="sheet" aria-labelledby="d-title" {@attach modal} oncancel={cancel}>
  <div class="sheet-backdrop" role="presentation" onclick={onclose} transition:backdrop
       style:opacity={pull ? Math.max(0.25, 1 - pull / 500) : undefined}></div>

  <!-- svelte-ignore a11y_no_static_element_interactions (Tab wrapping only) -->
  <div class="sheet-panel" class:settling class:full class:growing={grow} bind:this={panelEl}
       onkeydown={(e) => { trap(e); slideKey(e); }} transition:panel style={panelStyle}
       ontransitionend={() => (settling = false)}>
    <div class="grab" aria-hidden="true"
         onpointerdown={down} onpointermove={move} onpointerup={up} onpointercancel={up}></div>

    <!-- svelte-ignore a11y_autofocus -->
    <button class="sheet-close" type="button" aria-label={m.close()} bind:this={closeEl} autofocus
            onclick={onclose}>
      <Icon name="close" size={12} />
    </button>

    <!-- Swiping is a touch shortcut; Esc and the close button do the same. -->
    <div class="sheet-media" role="presentation" style:--tint={tint(shortcode)}
         data-shortcode={shortcode} in:receive={{ from: card }} out:send
         onpointerdown={down} onpointermove={move} onpointerup={up} onpointercancel={up}>
      {#if video}
        <video src={video} poster={thumb ?? undefined} controls loop muted autoplay playsinline
               preload="metadata" {@attach play}
               ontimeupdate={(e) => (now = e.currentTarget.currentTime * 1000)}></video>
      {:else if slides.length > 1}
        <div class="slides" role="group" aria-roledescription="carousel" aria-label={m.slides()}
             bind:this={trackEl} onscroll={() => (slide = at())} onscrollend={() => (aim = at())}>
          {#each slides as src, i (src)}
            <img {src} alt={m.slide_of({ n: i + 1, total: slides.length })}
                 loading={i <= 1 ? "eager" : "lazy"} aria-hidden={i !== slide}>
          {/each}
        </div>
        <button class="slide-step prev" type="button" aria-label={m.slide_prev()}
                disabled={slide === 0} onclick={() => step(-1)}>
          <Icon name="prev" size={12} />
        </button>
        <button class="slide-step next" type="button" aria-label={m.slide_next()}
                disabled={slide === slides.length - 1} onclick={() => step(1)}>
          <Icon name="next" size={12} />
        </button>
        <p class="slide-count" aria-live="polite">{slide + 1} / {slides.length}</p>
      {:else if thumb}
        <img src={thumb} alt="">
        {#if detail && !detail.photo}
          <p class="none">{m.sheet_no_video()}<br><code>./hikidashi enrich --media-only</code></p>
        {/if}
      {:else if detail}
        <p class="none">{m.sheet_nothing()}</p>
      {/if}
    </div>

    <div class="sheet-body">
      <header class="sheet-head">
        <h2 id="d-title">{title}</h2>
        <p class="sheet-sub">
          {#if author}
            <a class="mention" href={profileUrl(author)} target="_blank" rel="noopener noreferrer">@{author}</a>
            &nbsp;·&nbsp;
          {/if}
          <span class="nowrap">{savedDate(savedAt)}</span>&nbsp;·&nbsp;<span translate="no">{shortcode}</span>
        </p>
      </header>

      {#if !detail}
        <div class="loading" aria-hidden="true">
          <span></span><span></span><span style:width="62%"></span>
        </div>
      {:else}
        <div class="sections" in:fade={{ duration: motion.reduced ? 0 : 180 }}>
          {#if detail.recipe}
            <div class="d-tabs" role="tablist" aria-label={m.sheet_tabs()}>
              {#each [["recipe", m.recipe()], ["details", m.details()]] as [id, label] (id)}
                <button type="button" role="tab" id="tab-{id}" data-tab={id}
                        aria-selected={tab === id} aria-controls="panel-{id}"
                        tabindex={tab === id ? 0 : -1}
                        onclick={() => (tab = id as typeof tab)} onkeydown={tabKey}>{label}</button>
              {/each}
            </div>
          {/if}

          {#if detail.recipe && tab === "recipe"}
            <div role="tabpanel" id="panel-recipe" aria-labelledby="tab-recipe">
              <RecipePanel {shortcode} recipe={detail.recipe} listed={detail.listed} bind:ticked
                           onsaved={recipeSaved} onlisted={listed} />
            </div>
          {:else}
            <div class="sections" role={detail.recipe ? "tabpanel" : undefined}
                 id={detail.recipe ? "panel-details" : undefined}
                 aria-labelledby={detail.recipe ? "tab-details" : undefined}>
              {#if detail.state === "failed"}
                <div class="d-fail">
                  <p><Rich parts={m.sheet_failed.parts({
                    reason: detail.fail_reason || m.unknown(), attempts: detail.attempts,
                  })} /></p>
                  {#if detail.fail_stage === "fetch"}
                    <button class="btn" type="button" disabled={retrying}
                            onclick={retry}>{retrying ? m.sheet_retrying() : m.sheet_retry()}</button>
                  {/if}
                  {#if retryError}<p role="alert">{m.sheet_retry_error({ error: retryError })}</p>{/if}
                </div>
              {/if}

              {#if facts.length}
                <div class="d-facts">
                  {#each facts as [value, label] (label)}
                    <div class="d-fact"><b>{value}</b><span>{label}</span></div>
                  {/each}
                </div>
              {/if}

              <section class="d-sec">
                <p class="d-label">{m.caption()}</p>
                {#if caption}
                  <div class="d-body">{#each caption as seg, i (i)}{#if seg.kind === "text"}{seg.text}{:else if seg.kind === "place"}<a class="maplink" href={seg.url} target="_blank" rel="noopener noreferrer" title={m.sheet_map()}>{seg.text}</a>{:else}<a class="mention" href={seg.url} target="_blank" rel="noopener noreferrer">{seg.text}</a>{/if}{/each}</div>
                {:else}
                  <div class="d-body quiet">{m.sheet_no_caption()}</div>
                {/if}
              </section>

              {#if detail.places.length}
                <section class="d-sec">
                  <p class="d-label">{m.places()}</p>
                  <div class="d-chips">
                    {#each detail.places as p (p.url)}
                      <a class="is-place" href={p.url} target="_blank" rel="noopener noreferrer">{p.text}</a>
                    {/each}
                  </div>
                  {#if located.length}<MiniMap places={located} />{/if}
                </section>
              {/if}

              {#if detail.transcript}
                <!-- Folded shut: the longest thing on the sheet and the least
                     often read. -->
                <details class="d-sec d-fold" open={Boolean(startAt && segs)}>
                  <summary class="d-label">{m.transcript()} <span>{readingTime(detail.transcript)}</span></summary>
                  {#if segs && video}
                    <div class="d-body d-lines" bind:this={linesEl}>
                      {#each segs as [start, , text], i (i)}
                        <button type="button" class:on={i === current} class:hit={i === hit}
                                onclick={() => seekTo(start)}><time>{duration(Math.floor(start / 1000)) || "0:00"}</time>{text}</button>
                      {/each}
                    </div>
                  {:else}
                    <div class="d-body d-prose">
                      {#each paragraphs(detail.transcript) as p, i (i)}<p>{p}</p>{/each}
                    </div>
                  {/if}
                </details>
              {/if}

              {#if detail.ocr_text}
                <section class="d-sec"><p class="d-label">{m.on_screen()}</p><div class="d-body">{detail.ocr_text}</div></section>
              {/if}

              {#if detail.summary}
                <section class="d-sec"><p class="d-label">{m.summary()}</p><div class="d-body">{detail.summary}</div></section>
              {/if}

              {#if detail.keywords.length}
                <section class="d-sec">
                  <p class="d-label">{m.keywords()}</p>
                  <div class="d-chips">{#each detail.keywords as k (k)}<span>{k}</span>{/each}</div>
                </section>
              {/if}

              <section class="d-sec">
                <p class="d-label">{m.tag()}</p>
                <div class="d-chips">
                  {#if shown && yours}
                    <span class="d-tag">
                      <button type="button"
                              title="{m.tag_yours()} · {m.tag_show_only({ name: tagLabel(shown.name) })}"
                              onclick={() => onfilter("tag", shown.name)}>{tagLabel(shown.name)}</button>
                      <button type="button" class="d-untag" aria-label={m.tag_untag()}
                              title={m.tag_untag()} disabled={tagging} onclick={untag}>
                        <Icon name="close" size={8} />
                      </button>
                    </span>
                  {:else if shown}
                    <button type="button" class="d-auto"
                            title="{why(shown)} · {m.tag_show_only({ name: tagLabel(shown.name) })}"
                            onclick={() => onfilter("tag", shown.name)}>{tagLabel(shown.name)}</button>
                  {/if}
                  <form class="d-addtag" onsubmit={tag}>
                    <input type="text" list="d-tag-names"
                           placeholder={shown ? m.tag_change_placeholder() : m.tag_add_placeholder()}
                           aria-label={shown ? m.tag_change() : m.tag_choose()}
                           maxlength="64" autocomplete="off" enterkeyhint="done"
                           bind:value={newTag} onkeydown={tagKey} disabled={tagging}>
                  </form>
                </div>
                <datalist id="d-tag-names">
                  {#each suggestions as name (name)}<option value={tagLabel(name)}></option>{/each}
                </datalist>
                {#if note}<p class="d-note">{note}</p>{/if}
                {#if tagError}<p class="d-tagerr" role="alert">{m.tag_error({ error: tagError })}</p>{/if}
              </section>

              {#if gameSection && game}
                <section class="d-sec d-game">
                  <p class="d-label">{m.game()}</p>
                  {#if game.guess}
                    <p class="d-ask">{m.game_ask({ name: game.guess })}</p>
                  {/if}
                  <div class="d-chips">
                    {#if game.game && game.yours}
                      <span class="d-tag">
                        <button type="button"
                                title="{m.game_yours()} · {m.tag_show_only({ name: game.game })}"
                                onclick={() => onfilter("game", game.game!)}>{game.game}</button>
                        <button type="button" class="d-untag" aria-label={m.game_untag()}
                                title={m.game_untag()} disabled={gaming}
                                onclick={() => regame(clearGame(shortcode))}>
                          <Icon name="close" size={8} />
                        </button>
                      </span>
                    {:else if game.game}
                      <button type="button" class="d-auto"
                              title="{m.game_auto()} · {m.tag_show_only({ name: game.game })}"
                              onclick={() => onfilter("game", game.game!)}>{game.game}</button>
                    {:else if game.guess}
                      <button type="button" class="d-yes" disabled={gaming}
                              onclick={() => regame(setGame(shortcode, game.guess))}>{m.game_yes()}</button>
                      <button type="button" disabled={gaming}
                              onclick={() => regame(setGame(shortcode, null))}>{m.game_none()}</button>
                    {/if}
                    {#if !(game.yours && !game.game)}
                      <form class="d-addtag" onsubmit={nameGame}>
                        <input type="text"
                               placeholder={game.guess ? m.game_other_placeholder()
                                 : game.game ? m.tag_change_placeholder() : m.game_add_placeholder()}
                               aria-label={m.game_change()}
                               maxlength="120" autocomplete="off" enterkeyhint="done"
                               bind:value={newGame} onkeydown={gameKey} disabled={gaming}>
                      </form>
                    {/if}
                  </div>
                  {#if game.yours && !game.game}
                    <p class="d-note">{m.game_said_none()}
                      <button type="button" class="d-undo" disabled={gaming}
                              onclick={() => regame(clearGame(shortcode))}>{m.game_undo()}</button></p>
                  {/if}
                  {#if gameError}<p class="d-tagerr" role="alert">{m.game_error({ error: gameError })}</p>{/if}
                </section>
              {/if}

              {#if detail.collections.length}
                <section class="d-sec">
                  <p class="d-label">{m.collections()}</p>
                  <div class="d-chips">
                    {#each detail.collections as c (c)}
                      <button type="button" onclick={() => onfilter("collection", c)}>{c}</button>
                    {/each}
                  </div>
                </section>
              {/if}

              {#if detail.hashtags.length}
                <section class="d-sec">
                  <p class="d-label">{m.hashtags()}</p>
                  <div class="d-chips">
                    {#each detail.hashtags as h (h)}
                      <button type="button" class="is-hash" onclick={() => onfilter("hashtag", h)}>{h}</button>
                    {/each}
                  </div>
                </section>
              {/if}

              {#if detail.copies.length}
                <section class="d-sec">
                  <p class="d-label">{m.copies_title()}</p>
                  <p class="d-note">{m.copies_lede()}</p>
                  {#each detail.copies as c (c.shortcode)}
                    <div class="d-copy">
                      <a href={`/item/${encodeURIComponent(c.shortcode)}${page.url.search}`} data-sveltekit-noscroll>
                        <b>{c.author ? `@${c.author}` : c.shortcode}</b> · {savedDate(c.saved_at)}
                        {#if c.caption}<span>{c.caption}</span>{/if}
                      </a>
                      <button type="button" class="btn sm" onclick={() => splitCopy(c.shortcode)}>{m.copies_not_same()}</button>
                    </div>
                  {/each}
                </section>
              {/if}

              {#if similar.length}
                <section class="d-sec">
                  <p class="d-label">{m.more_like_this()}</p>
                  <div class="d-similar">
                    {#each similar as s (s.shortcode)}
                      <a href={`/item/${encodeURIComponent(s.shortcode)}${page.url.search}`}
                         data-sveltekit-noscroll title={s.caption ?? s.shortcode}
                         style:--tint={tint(s.shortcode)}>
                        {#if s.thumb}<img src={s.thumb} alt="" loading="lazy"
                                             onerror={(e) => (e.currentTarget as HTMLElement).remove()}>{/if}
                        <span>{s.author_name || (s.author ? `@${s.author}` : s.shortcode)}</span>
                      </a>
                    {/each}
                  </div>
                </section>
              {/if}

              <section class="d-sec">
                <p class="d-label">{m.pipeline()}</p>
                <div class="d-body mono" translate="no">fetch_v {detail.fetch_v} · asr_v {detail.asr_v} · ocr_v {detail.ocr_v}
                  · tag_v {detail.tag_v}{detail.tag_profile ? ` (${detail.tag_profile})` : ""}
                  · recipe_v {detail.recipe_v}{detail.recipe_profile ? ` (${detail.recipe_profile})` : ""}
                  · embed_v {detail.embed_v} · state {detail.state}</div>
              </section>
            </div>
          {/if}
        </div>
      {/if}

      <div class="sheet-actions">
        {#if permalink}
          <a class="btn primary" href={permalink} target="_blank" rel="noopener noreferrer">{m.sheet_open_instagram()}</a>
        {/if}
        {#if detail?.profile_url}
          <a class="btn" href={detail.profile_url} target="_blank" rel="noopener noreferrer">
            {detail.author ? `@${detail.author}` : m.sheet_creator()}
          </a>
        {/if}
        {#if onanother}
          <button class="btn" type="button" onclick={onanother}>
            <Icon name="shuffle" size={14} /> {m.shuffle_another()}
          </button>
        {/if}
        {#if detail}
          <ExportMenu groups={exports} up small={false} />
        {/if}
        {#if detail && !confirming}
          <button class="btn d-remove" type="button" onclick={() => (confirming = true)}>{m.sheet_remove()}</button>
        {/if}
      </div>
      {#if confirming}
        <div class="d-confirm" role="group" aria-label={m.sheet_remove()}
             {@attach (el) => el.scrollIntoView({ block: "nearest", behavior: motion.reduced ? "auto" : "smooth" })}>
          <p>{m.sheet_remove_confirm()}</p>
          <div class="sheet-actions">
            <button class="btn d-remove" type="button" disabled={removing}
                    onclick={remove}>{m.sheet_remove_yes()}</button>
            <button class="btn" type="button" onclick={() => (confirming = false)}>{m.cancel()}</button>
          </div>
          {#if removeError}<p class="d-tagerr" role="alert">{m.notice_remove_failed({ error: removeError })}</p>{/if}
        </div>
      {/if}
    </div>
  </div>
</dialog>

<style>
  /* The dialog is only a full-screen frame in the top layer; the backdrop
     and panel inside it are drawn, and animated, by us. */
  .sheet {
    position: fixed;
    inset: 0;
    width: 100%;
    height: 100%;
    max-width: none;
    max-height: none;
    margin: 0;
    padding: 24px;
    border: 0;
    background: none;
    color: inherit;
    overflow: hidden;
    display: grid;
    place-items: center;
  }

  .sheet::backdrop { background: none; }

  .sheet-backdrop {
    position: absolute;
    inset: 0;
    background: rgb(0 0 0 / 65%);
    backdrop-filter: blur(3px);
  }

  .sheet-panel {
    position: relative;
    display: grid;
    grid-template-columns: minmax(0, 340px) minmax(0, 1fr);
    /* A single row that may shrink below its content. Without minmax(0, …)
       the row sizes to the caption, the media column stretches to match, and
       the video overflows the clipped panel. */
    grid-template-rows: minmax(0, 1fr);
    width: min(880px, 100%);
    height: min(760px, 92vh);
    overflow: hidden;
    background: var(--surface);
    border: 1px solid var(--rule);
    border-radius: var(--radius-lg);
    box-shadow: 0 24px 60px -24px rgb(0 0 0 / 60%);
  }

  /* Springs back after a swipe that was not far enough to close. */
  .sheet-panel.settling { transition: transform 240ms var(--ease-out); }

  .grab { display: none; }

  .sheet-close {
    position: absolute;
    top: 8px;
    /* Clear of the text column's scrollbar, which runs down this same edge. */
    right: 24px;
    z-index: 2;
    display: grid;
    place-items: center;
    width: 28px;
    height: 28px;
    padding: 0;
    background: var(--surface-2);
    border: 0;
    border-radius: 50%;
    color: var(--ink);
    cursor: pointer;
    transition: background-color 120ms ease, transform 160ms var(--ease-out);
  }

  .sheet-close :global(svg) { stroke-width: 2.1; }
  .sheet-close:hover { background: var(--surface-3); transform: rotate(90deg); }

  .sheet-media {
    position: relative;
    display: grid;
    place-items: center;
    /* Shrink inside the grid instead of growing to the video's natural size. */
    min-height: 0;
    min-width: 0;
    overflow: hidden;
    background: #000;
  }

  /* A trace of the reel's tint in the letterbox, so the frame matches the
     card it grew from. */
  .sheet-media::before {
    content: "";
    position: absolute;
    inset: 0;
    background: var(--tint);
    opacity: 0.16;
  }

  /* Scale to fit the box rather than fill it: a 1080x1920 reel in a 340px
     column letterboxes instead of overflowing. */
  .sheet-media img,
  .sheet-media video {
    position: relative;
    display: block;
    width: auto;
    height: auto;
    max-width: 100%;
    max-height: 100%;
    object-fit: contain;
  }

  .sheet-media video { background: #000; }

  .slides {
    position: relative;
    display: flex;
    width: 100%;
    height: 100%;
    overflow-x: auto;
    overscroll-behavior-x: contain;
    scroll-snap-type: x mandatory;
    scrollbar-width: none;
  }

  .slides::-webkit-scrollbar { display: none; }

  /* Each slide is the whole box, its photo fitted inside it as a single
     still is. One swipe moves one slide, however fast. */
  .sheet-media .slides img {
    flex: 0 0 100%;
    width: 100%;
    height: 100%;
    scroll-snap-align: center;
    scroll-snap-stop: always;
  }

  .slide-step {
    position: absolute;
    top: 50%;
    z-index: 1;
    display: grid;
    place-items: center;
    width: 30px;
    height: 30px;
    padding: 0;
    /* Instagram's carousel arrows: white discs, dark chevrons. */
    background: rgb(255 255 255 / 85%);
    border: 0;
    border-radius: 50%;
    color: #262626;
    box-shadow: 0 1px 4px rgb(0 0 0 / 25%);
    cursor: pointer;
    transform: translateY(-50%);
    transition: opacity 160ms ease, background-color 120ms ease;
  }

  .slide-step.prev { left: 10px; }
  .slide-step.next { right: 10px; }
  .slide-step :global(svg) { stroke-width: 2.6; }
  .slide-step:hover { background: #fff; }
  .slide-step:disabled { opacity: 0; pointer-events: none; }

  .slide-count {
    position: absolute;
    bottom: 10px;
    left: 50%;
    margin: 0;
    padding: 2px 8px;
    font-size: 11.5px;
    font-weight: 600;
    color: #fff;
    background: rgb(0 0 0 / 60%);
    border-radius: var(--radius);
    transform: translateX(-50%);
    pointer-events: none;
  }

  .none {
    position: relative;
    margin: 0;
    padding: 24px;
    font-size: 12.5px;
    color: var(--muted);
    text-align: center;
  }

  /* The hint sits over the still while there is no video to play. */
  .sheet-media img + .none {
    position: absolute;
    inset: auto 0 0;
    background: rgb(0 0 0 / 80%);
    padding: 10px 12px;
  }

  .none code {
    font-size: 10.5px;
    color: var(--ink-2);
    background: rgb(255 255 255 / 8%);
    padding: 1px 5px;
    border-radius: var(--radius-sm);
  }

  /* Only this column scrolls. The video stays put. */
  .sheet-body {
    padding: 20px 22px;
    min-height: 0;
    overflow-y: auto;
    overscroll-behavior: contain;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  .sections { display: flex; flex-direction: column; gap: 16px; }

  .d-tabs {
    display: flex;
    gap: 2px;
    padding: 3px;
    align-self: flex-start;
    background: var(--surface-2);
    border-radius: var(--radius-sm);
  }
  .d-tabs button {
    padding: 4px 14px;
    font-size: 12.5px;
    border: 0;
    border-radius: var(--radius-sm);
    background: none;
    color: var(--ink-2);
    cursor: pointer;
    transition: background-color 140ms ease, color 140ms ease, box-shadow 140ms ease;
  }
  .d-tabs button:hover { color: var(--ink); }
  .d-tabs button[aria-selected="true"] {
    background: var(--surface);
    color: var(--ink);
    font-weight: 600;
    box-shadow: 0 1px 2px rgb(0 0 0 / 12%);
  }

  .sheet-head { display: flex; flex-direction: column; gap: 3px; padding-right: 40px; }
  .sheet-head h2 { margin: 0; font-size: 17px; line-height: 1.25; overflow-wrap: anywhere; }
  .sheet-sub { margin: 0; font-size: 12.5px; color: var(--muted); }
  /* "2026年10月14日" has no space in it, so it could break anywhere. */
  .sheet-sub .nowrap { white-space: nowrap; }

  .loading { display: flex; flex-direction: column; gap: 8px; }
  .loading span {
    height: 11px;
    border-radius: var(--radius-sm);
    background: var(--surface-2);
    animation: pulse 1.2s ease-in-out infinite alternate;
  }

  @keyframes pulse { to { opacity: 0.45; } }

  .d-sec { display: flex; flex-direction: column; gap: 5px; }
  .d-label { margin: 0; font-family: var(--display); font-size: 13px; font-weight: 600; color: var(--ink); }
  .d-body { font-size: 13.5px; color: var(--ink-2); line-height: 1.55; white-space: pre-wrap; overflow-wrap: anywhere; }
  .d-body.quiet { color: var(--muted); font-style: italic; }
  .d-body.mono { font-family: var(--mono); font-size: 11.5px; white-space: normal; }

  /* Same drawn marker as the sidebar groups. */
  .d-fold > summary { cursor: pointer; list-style: none; user-select: none; }
  .d-fold > summary::-webkit-details-marker { display: none; }
  .d-fold > summary::before {
    content: "▸";
    display: inline-block;
    width: 1.1em;
    transition: transform 140ms ease;
  }
  .d-fold[open] > summary::before { transform: rotate(90deg); }
  .d-fold > summary:hover { color: var(--ink-2); }
  .d-fold > summary span { margin-left: 0.6em; font-weight: 400; color: var(--muted); }
  .d-fold[open] > .d-prose { animation: unfold 200ms var(--ease-out); }

  @keyframes unfold { from { opacity: 0; transform: translateY(-4px); } }

  .d-prose { white-space: normal; max-width: 62ch; }
  .d-lines {
    position: relative; display: flex; flex-direction: column; white-space: normal;
    max-height: 260px; overflow-y: auto; max-width: 62ch;
  }
  .d-lines button {
    all: unset; cursor: pointer; padding: 3px 6px; border-radius: 6px; display: flex; gap: 8px;
  }
  .d-lines button:hover { background: var(--surface-2); }
  .d-lines button:focus-visible { outline: 2px solid var(--accent); }
  .d-lines time { flex: 0 0 auto; font-family: var(--mono); font-size: 11px; color: var(--muted); padding-top: 2px; }
  .d-lines .hit { box-shadow: inset 2px 0 0 var(--accent); }
  .d-lines .on { background: var(--accent-soft); color: var(--ink); }
  .d-prose p { margin: 0 0 0.8em; }
  .d-prose p:last-child { margin-bottom: 0; }

  .d-facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(96px, 1fr)); gap: 10px 14px; }
  .d-fact { display: flex; flex-direction: column; gap: 1px; }
  .d-fact b { font-size: 15px; font-weight: 700; color: var(--ink); font-variant-numeric: tabular-nums; }
  .d-fact span { font-size: 12px; color: var(--muted); }

  .d-chips { display: flex; flex-wrap: wrap; gap: 4px; }
  .d-copy { display: flex; gap: 10px; align-items: flex-start; justify-content: space-between; }
  .d-copy a { color: var(--ink-2); text-decoration: none; font-size: 13px; min-width: 0; }
  .d-copy .btn { flex: 0 0 auto; white-space: nowrap; }
  .d-copy a span {
    display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical;
    overflow: hidden; color: var(--muted);
  }
  .d-copy a:hover b { color: var(--accent); }
  /* More like this (#53): a row of small cards that scrolls sideways. */
  .d-similar { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 4px; scroll-snap-type: x proximity; }
  .d-similar a {
    flex: 0 0 96px; display: flex; flex-direction: column; gap: 4px;
    color: var(--ink-2); text-decoration: none; font-size: 11.5px; scroll-snap-align: start;
  }
  .d-similar img, .d-similar a::before {
    width: 96px; aspect-ratio: 4 / 5; object-fit: cover; border-radius: var(--radius-sm, 6px);
    background: var(--tint, var(--surface-2));
  }
  .d-similar a:not(:has(img))::before { content: ""; display: block; }
  .d-similar span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .d-similar a:hover span { color: var(--accent); }
  .d-chips span, .d-chips button {
    font-size: 12px;
    padding: 3px 10px;
    border-radius: var(--radius-pill);
    background: var(--surface-2);
    color: var(--ink-2);
    border: 0;
  }
  .d-chips button { cursor: pointer; transition: background-color 120ms ease, color 120ms ease; }
  .d-chips button:hover { background: var(--accent-soft); color: var(--accent); }

  /* Yours: the name filters, the cross takes it off. */
  .d-chips .d-tag { display: inline-flex; padding: 0; overflow: hidden; background: var(--accent-soft); }
  .d-chips .d-tag button { background: none; color: var(--accent); font-weight: 600; }
  .d-chips .d-tag button:first-child { padding-right: 4px; }
  .d-chips .d-untag { display: grid; place-items: center; padding: 0 9px 0 5px; }
  .d-chips .d-untag :global(svg) { stroke-width: 2.4; opacity: 0.7; }
  .d-chips .d-untag:hover { background: var(--accent-fill); color: var(--on-accent); }
  .d-chips .d-untag:hover :global(svg) { opacity: 1; }

  .d-addtag { display: contents; }
  .d-addtag input {
    width: 11ch;
    font-size: 12px;
    padding: 2px 10px;
    border: 1px dashed var(--rule);
    border-radius: var(--radius-pill);
    background: none;
    color: var(--ink);
    transition: width 160ms var(--ease-out), border-color 120ms ease;
  }
  .d-addtag input::placeholder { color: var(--muted); }
  .d-addtag input:hover { border-color: var(--accent); }
  .d-addtag input:focus { width: 18ch; border-style: solid; border-color: var(--accent); outline: none; }
  .d-addtag input:disabled { opacity: 0.5; }

  .d-chips .d-auto { background: var(--accent-soft); color: var(--accent); }
  .d-note { margin: 0; font-size: 11.5px; color: var(--muted); }
  .d-note::first-letter { text-transform: uppercase; }
  .d-tagerr { margin: 0; font-size: 12px; color: var(--risk); }
  .d-ask { margin: 0; font-size: 13px; }
  /* Room for "another game" before it is focused and widens. */
  .d-game .d-addtag input:not(:focus) { width: 14ch; }
  .d-chips .d-yes { background: var(--accent-fill); color: var(--on-accent); }
  .d-undo { margin-left: 4px; padding: 0; border: 0; background: none; font: inherit;
            color: var(--accent); text-decoration: underline; cursor: pointer; }
  .d-chips .is-hash::before { content: "#"; opacity: 0.55; }

  .d-chips a.is-place {
    font-size: 11.5px;
    padding: 4px 10px;
    border-radius: var(--radius-pill);
    background: var(--surface-2);
    color: var(--ink-2);
    text-decoration: none;
  }
  .d-chips a.is-place::before { content: "📍 "; }
  .d-chips a.is-place:hover { background: var(--accent-soft); color: var(--accent); }

  .maplink { color: var(--accent); text-decoration: underline; text-underline-offset: 2px; }
  .maplink::after { content: " ↗"; font-size: 0.8em; opacity: 0.7; }

  .d-fail { display: flex; flex-direction: column; align-items: flex-start; gap: 8px; padding: 9px 12px; border-radius: var(--radius-sm); background: rgb(180 51 40 / 12%); color: var(--risk); font-size: 13px; }
  .d-fail p { margin: 0; }

  .sheet-actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: auto; padding-top: 4px; }

  /* Away from the links, and quiet until hovered: it is not undone easily. */
  .sheet-actions > .d-remove:first-of-type:not(:only-child) { margin-left: auto; }
  .d-remove:hover { border-color: var(--risk); color: var(--risk); }

  .d-confirm {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 12px;
    border: 1px solid var(--risk);
    border-radius: var(--radius-sm);
    font-size: 13px;
  }

  .d-confirm p { margin: 0; color: var(--ink-2); }
  .d-confirm .d-remove { border-color: var(--risk); color: var(--risk); }

  /* Phone width: a bottom sheet with a grab handle. Swiped down, the video
     takes the whole sheet; swiped down again, it closes. */
  @media (max-width: 760px) {
    .sheet { padding: 0; place-items: end stretch; }

    .sheet-panel {
      grid-template-columns: 1fr;
      /* The picture gets a fixed share; the rest scrolls underneath it. */
      grid-template-rows: minmax(0, calc(42dvh + var(--grow, 0px))) minmax(0, 1fr);
      width: 100%;
      height: calc(100dvh - 12px);
      border-radius: 14px 14px 0 0;
      border-bottom: 0;
      transition: grid-template-rows 240ms var(--ease-out);
    }

    .sheet-panel.full { grid-template-rows: minmax(0, calc(100% + var(--grow, 0px))) minmax(0, 1fr); }
    .sheet-panel.full:not(.growing) .sheet-body { visibility: hidden; }
    /* No easing while the finger holds the line, or it lags behind. */
    .sheet-panel.growing { transition: none; }

    .grab {
      display: block;
      position: absolute;
      z-index: 3;
      top: 0;
      left: 0;
      right: 52px;
      height: 28px;
      touch-action: none;
    }

    .grab::after {
      content: "";
      position: absolute;
      top: 8px;
      left: calc(50% + 26px);
      width: 38px;
      height: 4px;
      border-radius: 2px;
      background: rgb(255 255 255 / 55%);
      transform: translateX(-50%);
    }

    .sheet-media { touch-action: pan-x; }
  }
</style>
