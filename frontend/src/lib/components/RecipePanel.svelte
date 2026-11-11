<script lang="ts">
  import { fade } from "svelte/transition";
  import { addToList, dropFromList, revertRecipe, saveRecipe } from "$lib/api";
  import { list, nf } from "$lib/format";
  import { motion } from "$lib/motion.svelte";
  import { m } from "$lib/paraglide/messages";
  import type { Ingredient, Recipe, RecipeIn } from "$lib/types";
  import Icon from "./Icon.svelte";

  let { shortcode, recipe, listed, ticked = $bindable(), onsaved, onlisted }: {
    shortcode: string;
    recipe: Recipe;
    /** It is on your shopping list. */
    listed: boolean;
    /** Ticked ingredients, while you cook. */
    ticked: Record<number, boolean>;
    /** A correction was saved or dropped; this is what the reel shows now. */
    onsaved: (recipe: Recipe | null) => void;
    /** It went on your shopping list, or came off it. */
    onlisted: (listed: boolean) => void;
  } = $props();

  const SOURCES: Record<string, () => string> = {
    caption: m.recipe_source_caption, transcript: m.recipe_source_transcript, ocr: m.recipe_source_ocr,
  };

  const amount = (i: Ingredient) => [i.quantity, i.unit].filter(Boolean).join(" ");

  const facts = $derived([
    [recipe.servings ? nf.format(recipe.servings) : null, m.recipe_serves()],
    [recipe.time_minutes ? m.minutes({ minutes: nf.format(recipe.time_minutes) }) : null, m.recipe_time()],
  ].filter(([v]) => v) as [string, string][]);

  const from = $derived(recipe.source_fields.map((f) => SOURCES[f]?.() ?? f));

  /* ---------- editing ---------- */

  let editing = $state(false);
  let busy = $state(false);
  let error = $state<string | null>(null);
  let draft = $state<{
    title: string; servings: string; time: string;
    ingredients: Ingredient[]; steps: string; incomplete: boolean;
  }>({ title: "", servings: "", time: "", ingredients: [], steps: "", incomplete: false });

  function edit() {
    draft = {
      title: recipe.title ?? "",
      servings: recipe.servings ? String(recipe.servings) : "",
      time: recipe.time_minutes ? String(recipe.time_minutes) : "",
      ingredients: recipe.ingredients.map(({ item, quantity, unit, note }) =>
        ({ item, quantity, unit, note })),
      steps: recipe.steps.join("\n"),
      incomplete: recipe.incomplete,
    };
    if (!draft.ingredients.length) addIngredient();
    error = null;
    editing = true;
  }

  function addIngredient() {
    draft.ingredients.push({ item: "", quantity: "", unit: "", note: "" });
  }

  const whole = (s: string) => {
    const n = Number.parseInt(s, 10);
    return Number.isFinite(n) && n > 0 ? n : null;
  };

  function body(hidden: boolean): RecipeIn {
    if (!editing) {
      // "Not a recipe" from the reading view: keep the recipe as it is.
      return {
        title: recipe.title, servings: recipe.servings, time_minutes: recipe.time_minutes,
        ingredients: recipe.ingredients.map(({ item, quantity, unit, note }) =>
          ({ item, quantity, unit, note })),
        steps: recipe.steps, incomplete: recipe.incomplete, hidden,
      };
    }
    return {
      title: draft.title.trim() || null,
      servings: whole(draft.servings),
      time_minutes: whole(draft.time),
      ingredients: draft.ingredients.filter((i) => i.item.trim()),
      steps: draft.steps.split("\n").map((s) => s.trim()).filter(Boolean),
      incomplete: draft.incomplete,
      hidden,
    };
  }

  async function run(call: () => Promise<Recipe | null>) {
    busy = true;
    error = null;
    try {
      const next = await call();
      editing = false;
      onsaved(next);
    } catch (err) {
      error = (err as Error).message;
    } finally {
      busy = false;
    }
  }

  async function toggleList() {
    busy = true;
    error = null;
    try {
      const shop = listed ? await dropFromList(shortcode) : await addToList([shortcode]);
      onlisted(shop.recipes.some((r) => r.shortcode === shortcode));
    } catch (err) {
      error = (err as Error).message;
    } finally {
      busy = false;
    }
  }

  const save = () => run(() => saveRecipe(shortcode, body(false)));
  const hide = () => run(() => saveRecipe(shortcode, body(true)));
  const revert = () => run(() => revertRecipe(shortcode));
</script>

{#if recipe.hidden}
  <div class="r-note">
    {m.recipe_hidden()}
    <button class="btn" type="button" disabled={busy} onclick={revert}>{m.undo()}</button>
  </div>
{:else if editing}
  <form class="r-edit" in:fade={{ duration: motion.reduced ? 0 : 140 }}
        onsubmit={(e) => { e.preventDefault(); void save(); }}>
    <label class="r-field">
      <span>{m.recipe_title()}</span>
      <input type="text" bind:value={draft.title} placeholder={m.recipe_title_placeholder()}>
    </label>
    <div class="r-pair">
      <label class="r-field"><span>{m.recipe_serves_label()}</span>
        <input type="number" min="1" inputmode="numeric" bind:value={draft.servings}></label>
      <label class="r-field"><span>{m.recipe_minutes_label()}</span>
        <input type="number" min="1" inputmode="numeric" bind:value={draft.time}></label>
    </div>

    <fieldset class="r-field">
      <legend>{m.ingredients()}</legend>
      <div class="r-rows">
        {#each draft.ingredients as ing, i (i)}
          <div class="r-row">
            <input type="text" aria-label={m.recipe_amount()} placeholder="200" bind:value={ing.quantity}>
            <input type="text" aria-label={m.recipe_unit()} placeholder={m.recipe_unit_placeholder()}
                   bind:value={ing.unit}>
            <input type="text" aria-label={m.recipe_ingredient()}
                   placeholder={m.recipe_ingredient_placeholder()} bind:value={ing.item}>
            <input type="text" class="r-note-in" aria-label={m.recipe_note()}
                   placeholder={m.recipe_note_placeholder()} bind:value={ing.note}>
            <button type="button" class="r-x"
                    aria-label={m.recipe_remove({ item: ing.item || m.recipe_ingredient() })}
                    onclick={() => draft.ingredients.splice(i, 1)}>&times;</button>
          </div>
        {/each}
      </div>
      <button type="button" class="btn r-add" onclick={addIngredient}>{m.recipe_add_ingredient()}</button>
    </fieldset>

    <label class="r-field">
      <span>{m.recipe_steps()} <em>{m.recipe_steps_note()}</em></span>
      <textarea rows="7" bind:value={draft.steps}></textarea>
    </label>
    <label class="r-check">
      <input type="checkbox" bind:checked={draft.incomplete}>
      {m.recipe_incomplete_check()}
    </label>

    {#if error}<p class="r-error" role="alert">{error}</p>{/if}
    <div class="r-actions">
      <button class="btn primary" type="submit" disabled={busy}>{m.save()}</button>
      <button class="btn" type="button" disabled={busy} onclick={() => (editing = false)}>{m.cancel()}</button>
    </div>
  </form>
{:else}
  <article class="r-view" in:fade={{ duration: motion.reduced ? 0 : 140 }}>
    <h3 class="r-title">{recipe.title || m.recipe_untitled()}</h3>

    {#if facts.length}
      <div class="r-facts">
        {#each facts as [value, label] (label)}
          <div><b>{value}</b><span>{label}</span></div>
        {/each}
      </div>
    {/if}

    {#if recipe.incomplete}
      <p class="r-note">{m.recipe_incomplete_note()}</p>
    {/if}

    {#if recipe.ingredients.length}
      <section class="r-sec">
        <p class="r-label">{m.ingredients()}</p>
        <ul class="r-list">
          {#each recipe.ingredients as ing, i (i)}
            <li>
              <label class:done={ticked[i]}>
                <input type="checkbox" bind:checked={ticked[i]}>
                <span>
                  {#if amount(ing)}<b>{amount(ing)}</b>{/if}
                  {ing.item}{#if ing.note}<em>, {ing.note}</em>{/if}
                </span>
              </label>
            </li>
          {/each}
        </ul>
      </section>
    {/if}

    {#if recipe.steps.length}
      <section class="r-sec">
        <p class="r-label">{m.recipe_method()}</p>
        <ol class="r-steps">
          {#each recipe.steps as step, i (i)}<li>{step}</li>{/each}
        </ol>
      </section>
    {/if}

    <p class="r-prov">
      {#if recipe.edited}{recipe.has_model ? m.recipe_yours_over_model() : m.recipe_yours()}
      {:else if from.length}{m.recipe_read_from({ sources: list(from) })}
      {:else}{m.recipe_read()}{/if}
    </p>

    {#if error}<p class="r-error" role="alert">{error}</p>{/if}
    <div class="r-actions">
      <button class="btn r-shop" type="button" aria-pressed={listed} disabled={busy}
              onclick={toggleList}>
        <Icon name="basket" size={14} /> {listed ? m.shop_listed() : m.shop_add()}
      </button>
      {#if listed}<a class="btn quiet" href="/shopping">{m.shop_open()}</a>{/if}
      <button class="btn" type="button" disabled={busy} onclick={edit}>{m.edit()}</button>
      {#if recipe.edited && recipe.has_model}
        <button class="btn" type="button" disabled={busy} onclick={revert}>{m.recipe_use_model()}</button>
      {/if}
      <button class="btn quiet" type="button" disabled={busy} onclick={hide}>{m.recipe_not_a_recipe()}</button>
    </div>
  </article>
{/if}

<style>
  .r-view, .r-edit { display: flex; flex-direction: column; gap: 16px; }

  .r-title { margin: 0; font-size: 18px; line-height: 1.25; letter-spacing: -0.01em; }

  .r-facts { display: flex; gap: 22px; }
  .r-facts div { display: flex; flex-direction: column; gap: 1px; }
  .r-facts b { font-size: 16px; font-weight: 700; font-variant-numeric: tabular-nums; }
  .r-facts span { font-size: 12px; color: var(--muted); }
  .r-label { font-family: var(--display); font-size: 13px; font-weight: 600; color: var(--ink); }

  .r-label { margin: 0; }
  .r-sec { display: flex; flex-direction: column; gap: 6px; }

  .r-note {
    margin: 0;
    padding: 9px 12px;
    border-radius: var(--radius-sm);
    background: var(--surface-2);
    color: var(--ink-2);
    font-size: 13px;
    display: flex;
    gap: 10px;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
  }

  .r-list { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; }
  .r-list label {
    display: flex;
    gap: 10px;
    align-items: baseline;
    padding: 5px 2px;
    border-bottom: 1px solid var(--rule);
    font-size: 14px;
    color: var(--ink);
    cursor: pointer;
    transition: color 140ms ease;
  }
  .r-list li:last-child label { border-bottom: 0; }
  .r-list input { accent-color: var(--accent); transform: translateY(1px); }
  .r-list b { font-size: 13.5px; font-weight: 700; margin-right: 0.45em; }
  .r-list em { font-style: normal; color: var(--muted); }
  .r-list .done { color: var(--muted); }
  .r-list .done span { text-decoration: line-through; text-decoration-color: var(--muted); }

  .r-steps { margin: 0; padding-left: 1.4em; display: flex; flex-direction: column; gap: 8px; }
  .r-steps li { font-size: 14px; line-height: 1.55; color: var(--ink-2); padding-left: 0.2em; }
  .r-steps li::marker { font-size: 13px; font-weight: 700; color: var(--accent); }

  .r-prov { margin: 0; font-size: 12px; color: var(--muted); }

  .r-actions { display: flex; gap: 8px; flex-wrap: wrap; }
  .r-shop { display: inline-flex; align-items: center; gap: 6px; }
  .r-shop[aria-pressed="true"] { background: var(--accent-soft); color: var(--accent); }
  .btn.quiet { border-color: transparent; background: none; color: var(--muted); }
  .btn.quiet:hover { color: var(--risk); border-color: transparent; }

  .r-error { margin: 0; color: var(--risk); font-size: 13px; }

  /* ---------- the form ---------- */
  .r-field { display: flex; flex-direction: column; gap: 5px; min-width: 0; margin: 0; padding: 0; border: 0; }
  .r-field > span, .r-field legend {
    padding: 0;
    font-size: 12.5px;
    font-weight: 600;
    color: var(--ink-2);
  }
  .r-field em { font-style: normal; font-weight: 400; color: var(--muted); }
  .r-pair { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }

  .r-edit input[type="text"], .r-edit input[type="number"], .r-edit textarea {
    width: 100%;
    min-width: 0;
    padding: 6px 9px;
    font-size: 13.5px;
    background: var(--field);
    border: 1px solid transparent;
    border-radius: var(--radius);
  }
  .r-edit textarea { resize: vertical; line-height: 1.5; font-family: inherit; }
  .r-edit input:focus, .r-edit textarea:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); outline: none; }

  .r-rows { display: flex; flex-direction: column; gap: 6px; }
  .r-row { display: grid; grid-template-columns: 4.2em 3.6em minmax(0, 1fr) minmax(0, 0.8fr) 26px; gap: 5px; }
  .r-x {
    padding: 0;
    border: 0;
    border-radius: var(--radius-sm);
    background: none;
    color: var(--muted);
    font-size: 17px;
    cursor: pointer;
  }
  .r-x:hover { color: var(--risk); background: var(--surface-2); }
  .r-add { align-self: flex-start; margin-top: 4px; font-size: 12px; padding: 5px 10px; }

  .r-check { display: flex; gap: 8px; align-items: center; font-size: 13px; color: var(--ink-2); }
  .r-check input { accent-color: var(--accent); }

  @media (max-width: 760px) {
    /* Item and note take a line of their own below the amount. */
    .r-row { grid-template-columns: 4.2em 3.6em minmax(0, 1fr) 26px; }
    .r-row .r-note-in { grid-column: 1 / 4; }
  }
</style>
