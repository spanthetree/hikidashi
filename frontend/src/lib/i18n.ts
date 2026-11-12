import { m } from "./paraglide/messages";
import {
  defineCustomClientStrategy, getLocale, isLocale, setLocale, type Locale,
} from "./paraglide/runtime";

/* The language is never in the URL: the query string is the library's state,
   and a self-hosted app has no search engine to serve one page per language.
   It is a choice saved in this browser, else the browser's own languages,
   else English (the `strategy` in vite.config.ts). Switching reloads the
   page, so every string and formatter is made again in the new language. */

const KEY = "locale";

function saved(): Locale | undefined {
  try {
    const value = localStorage.getItem(KEY);
    return value && isLocale(value) ? value : undefined;
  } catch {
    return undefined;            // storage blocked, or not a browser
  }
}

const choice = saved();

defineCustomClientStrategy("custom-saved", {
  getLocale: () => choice,
  // Paraglide also calls this with the locale it settled on at first load.
  // Only a choice made in the switcher is kept, so a browser whose languages
  // change later is followed.
  setLocale: () => {},
});

/** Each language in its own name, as the switcher shows it. */
export const LANGUAGES: Record<Locale, string> = { en: "English", ja: "日本語" };

export function chooseLocale(locale: Locale) {
  try { localStorage.setItem(KEY, locale); } catch { /* blocked: the reload forgets it */ }
  void setLocale(locale);
}

export const locale = getLocale();

/* Our fixed vocabularies are data in the database, stored in English: the
   tag taxonomy (app/tagging/taxonomy.py), the ingredient groups
   (app/recipes/groups.py) and the pipeline states. They are translated only
   for display; a URL or an API call still uses the English name. A tag you
   made up yourself is shown as you typed it. */

const TAGS: Record<string, () => string> = {
  "Food": m.tag_food,
  "Travel": m.tag_travel,
  "Automotive": m.tag_automotive,
  "Sim racing": m.tag_sim_racing,
  "Gaming": m.tag_gaming,
  "Technology": m.tag_technology,
  "Home & DIY": m.tag_home_diy,
  "Fitness": m.tag_fitness,
  "Design": m.tag_design,
  "Finance": m.tag_finance,
  "Movies & TV": m.tag_movies_tv,
  "Nature & science": m.tag_nature_science,
  "Family & wellbeing": m.tag_family_wellbeing,
  "Style & beauty": m.tag_style_beauty,
};

const GROUPS: Record<string, () => string> = {
  "Dairy": m.group_dairy,
  "Eggs": m.group_eggs,
  "Gluten": m.group_gluten,
  "Meat": m.group_meat,
  "Poultry": m.group_poultry,
  "Fish & seafood": m.group_fish,
  "Nuts": m.group_nuts,
};

const STATES: Record<string, () => string> = {
  imported: m.state_imported,
  fetched: m.state_fetched,
  transcribed: m.state_transcribed,
  tagged: m.state_tagged,
  failed: m.state_failed,
};

const label = (names: Record<string, () => string>) => (name: string) =>
  Object.hasOwn(names, name) ? names[name]() : name;

export const tagLabel = label(TAGS);
export const groupLabel = label(GROUPS);
export const stateLabel = label(STATES);

/* The pipeline's stages, by the names app/jobs gives them. */
const STAGES: Record<string, () => string> = {
  enrich: m.stage_enrich,
  asr: m.stage_asr,
  ocr: m.stage_ocr,
  tag: m.stage_tag,
  recipe: m.stage_recipe,
  embed: m.stage_embed,
};

export const stageLabel = label(STAGES);

/** The tag a typed name means: a category typed in the language on screen
    ("旅行") is that category ("Travel"), not a new tag of its own. */
export function tagName(typed: string): string {
  const name = typed.trim();
  const key = name.toLocaleLowerCase(locale);
  return Object.keys(TAGS).find((t) => TAGS[t]().toLocaleLowerCase(locale) === key) ?? name;
}
