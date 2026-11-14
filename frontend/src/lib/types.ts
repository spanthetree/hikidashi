/* The shapes app/api/main.py returns. Written by hand: the endpoints return
   plain dicts, so FastAPI's OpenAPI schema has nothing to generate from. Keep
   these in step with the dict literals there. */

export interface Item {
  shortcode: string;
  permalink: string;
  author: string | null;
  author_name: string | null;
  saved_at: number | null;
  state: string;            // "imported", "failed", or a pipeline stage
  caption: string | null;
  duration: number | null;
  like_count: number | null;
  thumb: string | null;
  video: string | null;
  /** Cleaned: normalised, engagement-bait dropped, each once. */
  hashtags: string[];
  collections: string[];
  /** Its one category: yours, else the model's, else the hashtags'. */
  tag: string | null;
  /** What a card shows of the reel's recipe; null when it has none. */
  recipe: RecipeCard | null;
  /** This reel and its reposts in your library (#58); 1 for most. */
  copies: number;
  /** Why a search returned it; null without a query. */
  match: SearchMatch | null;
}

/** app/search/: which sides found the reel, and how close the vectors were. */
export interface SearchMatch {
  match: ("phrase" | "keyword" | "semantic" | "transcript")[];
  similarity: number | null;
  /** When in the reel the query was said, and the line (#44). */
  at_ms?: number;
  said?: string;
}

/** [start_ms, end_ms, text]: when a part of the transcript was said. */
export type Segment = [number, number, string];

export interface RecipeCard {
  title: string | null;
  time_minutes: number | null;
  ingredients: number;
  incomplete: boolean;
}

export interface Ingredient {
  item: string;
  quantity: string | null;
  unit: string | null;
  note: string | null;
}

export interface RecipeIngredient extends Ingredient {
  groups: string[];
}

export interface Recipe {
  title: string | null;
  servings: number | null;
  time_minutes: number | null;
  ingredients: RecipeIngredient[];
  steps: string[];
  incomplete: boolean;
  /** You said it is not a recipe after all. */
  hidden: boolean;
  confidence: number | null;
  source_fields: string[];
  /** This is your correction, not the model's output. */
  edited: boolean;
  /** The model has its own version, which reverting brings back. */
  has_model: boolean;
  /** Fetched from the page the reel points to (#114). */
  web_url: string | null;
  /** An incomplete one: where the rest is, and whether the server fetches it. */
  link?: RecipeLink | null;
  fetchable?: boolean;
}

export type RecipeLink =
  | { kind: "recipe" | "site"; url: string; site: string }
  | { kind: "comment"; keyword: string | null }
  | { kind: "bio" };

/** The body of PUT /api/items/{shortcode}/recipe. */
export interface RecipeIn {
  title: string | null;
  servings: number | null;
  time_minutes: number | null;
  ingredients: Ingredient[];
  steps: string[];
  incomplete: boolean;
  hidden: boolean;
}

export interface RecipeFacets {
  total: number;
  groups: Named[];
  ingredients: Named[];
  times: { minutes: number; count: number }[];
}

export interface ItemPage {
  total: number;
  offset: number;
  limit: number;
  items: Item[];
}

export interface Place {
  text: string;
  url: string;
  /** Where it is, once looked up and sure enough to show; else null. */
  lat: number | null;
  lng: number | null;
}

/** A place on the map: always located. */
export interface Pin {
  text: string;
  url: string;
  seq: number;
  lat: number;
  lng: number;
  /** The geocoder's name for what it found. */
  label: string | null;
  kind: string | null;
}

export interface PlacedItem extends Item {
  places: Pin[];
}

/** GET /api/places: the chosen reels that are on the map. */
export interface PlacePage {
  /** How many reels the filters chose, on the map or not. */
  total: number;
  /** How many of them name a place, found or not. */
  named: number;
  items: PlacedItem[];
}

export interface MapSettings {
  /** A {z}/{x}/{y} tile URL template. */
  tiles: string;
  /** The credit the tiles require, as HTML. */
  attribution: string;
  /** A typed place can be looked up. */
  geocoder: boolean;
}

/** One source's answer for a reel's tag. */
export interface ItemTag {
  name: string;
  kind: string;
  source: "user" | "game" | "model" | "hashtag";
  confidence: number | null;
}

/** What PUT and DELETE /api/items/{shortcode}/tag return. */
export interface TagChoice {
  tag: string | null;
  /** Every source's answer, the one shown first. */
  tags: ItemTag[];
}

/** The video game a reel shows you (#117), and what PUT and DELETE
    /api/items/{shortcode}/game return. */
export interface ItemGame {
  /** Yours, else the one the game pass is sure of; null for none. */
  game: string | null;
  /** You said it (or said there is none); no pass changes it. */
  yours: boolean;
  /** A title the pass was not sure of, to ask you about; null if not asking. */
  guess: string | null;
}

/** The same video saved again from another post (#58). */
export interface Copy {
  shortcode: string;
  author: string | null;
  caption: string | null;
  saved_at: number | null;
  thumb: string | null;
}

export interface ItemDetail extends Omit<Item, "recipe" | "copies"> {
  copies: Copy[];
  fbid: string | null;
  fail_stage: string | null;
  fail_reason: string | null;
  attempts: number;
  width: number | null;
  height: number | null;
  comment_count: number | null;
  bytes: number | null;
  fetch_v: number;
  asr_v: number;
  ocr_v: number;
  tag_v: number;
  embed_v: number;
  recipe_v: number;
  asr_profile: string | null;
  tag_profile: string | null;
  recipe_profile: string | null;
  transcript: string | null;
  /** When each part of the transcript was said; null when not known. */
  segments: Segment[] | null;
  ocr_text: string | null;
  summary: string | null;
  keywords: string[];
  /** The post has no video to download: a photo, not a reel missing its mp4. */
  photo: boolean;
  /** A carousel of photos, one image a slide, in order; else empty. */
  slides: string[];
  tags: ItemTag[];
  game: ItemGame;
  places: Place[];
  profile_url: string | null;
  recipe: Recipe | null;
  /** The recipe as schema.org JSON-LD, for the page's head; null with none. */
  jsonld: Record<string, unknown> | null;
  /** Its recipe is on your shopping list. */
  listed: boolean;
}

export interface Stats {
  total: number;
  states: Record<string, number>;
  collections: number;
  with_author: number;
  with_caption: number;
  with_hashtags: number;
  with_thumb: number;
  with_media: number;
  with_transcript: number;
  reels: number;
  last_import: Record<string, unknown> | null;
  profile: string;
  recipes: number;
  /** Recipes on your shopping list. */
  shopping: number;
  /** Reels whose caption names a place. */
  places: number;
  /** Those reels by the tag each shows. */
  place_tags: Record<string, number>;
  /** The pipeline's workers in a word, for admins; null for everyone else. */
  worker: WorkerOverall | null;
  map: MapSettings;
}

/* ---------- the pipeline's workers (/api/pipeline) ---------- */

export type Stage = "enrich" | "asr" | "ocr" | "tag" | "recipe" | "embed";

/** problem: running, but a stage it runs cannot. lost: it stopped checking in
    without saying it was stopping, so it crashed or was killed. */
export type WorkerOverall = "problem" | "running" | "lost" | "stopped" | "none";

export interface StageFailure {
  shortcode: string;
  permalink: string | null;
  attempts: number;
  reason: string | null;
  last_at: number;
}

/** A reel whose fetch failed: before the stages, which have nothing of it to work on. */
export interface FetchFailure {
  shortcode: string;
  permalink: string | null;
  reason: string | null;
  attempts: number;
  /** Fails the same way every time (restricted, deleted, login only). */
  permanent: boolean;
  /** Has tries left, and will be fetched again by itself. */
  retrying: boolean;
}

export interface FetchFailures {
  failed: number;
  /** Out of tries but not permanent: what Retry would offer again. */
  retryable: number;
  failures: FetchFailure[];
}

export interface StageQueue {
  stage: Stage;
  /** Can be claimed now. */
  ready: number;
  /** An earlier stage still has them to do. */
  waiting: number;
  /** Held by a worker. */
  working: number;
  /** Tried too often; not offered again until retried. */
  failed: number;
  /** Earlier stages it is going ahead without, as no worker can run them. */
  without: Stage[];
  failures: StageFailure[];
}

export interface WorkerInfo {
  name: string;
  pid: number | null;
  state: "running" | "stopped" | "lost";
  stages: Stage[];
  started_at: number;
  seen_at: number;
  stopped_at: number | null;
  doing: Stage | null;
  /** The stages it cannot run now, and why. */
  problems: Partial<Record<Stage, string>>;
}

export interface PipelineState {
  fetch: FetchFailures;
  stages: StageQueue[];
  workers: WorkerInfo[];
  /** The hosted APIs (#125): each one's switch, why it went off, in flight, spend. */
  apis: ApiStatus[];
  overall: WorkerOverall;
  attempts: number;
  /** Seconds of silence before a running worker counts as lost. */
  stale: number;
  now: number;
}

export interface Named {
  name: string;
  count: number;
}

export interface Tag extends Named {
  kind: string;
}

export interface ImportResult {
  strategy: string;
  inserted: number;
  updated: number;
  unchanged: number;
  unparsed: number;
  already_imported: boolean;
}

export interface LogTail {
  file: string;
  lines: string[];
}

/** How this server signs people in: GET /api/auth, which needs no session. */
export interface AuthState {
  /** "none": everyone is the library's owner. "local": usernames and passwords.
      "proxy": a proxy in front signs people in and names them in a header. */
  mode: "none" | "local" | "proxy";
  /** No admin can sign in yet: the library is waiting to be claimed. */
  setup: boolean;
}

/** Whoever is signed in: GET /api/me. */
export interface Me {
  id: number;
  username: string;
  /** Their Instagram @handle, without the @. Self-declared, never verified. */
  ig_handle: string | null;
  is_admin: boolean;
  auth: AuthState["mode"];
  /** One card for a reel and its reposts (#58). */
  collapse_copies: boolean;
}

/** An account, as an admin sees it: GET /api/users. */
export interface Account {
  id: number;
  username: string;
  ig_handle: string | null;
  is_admin: boolean;
  disabled: boolean;
  has_password: boolean;
  created_at: number;
  /** How many reels are in their library. */
  reels: number;
}

/** An invite link not used yet: GET /api/invites. Its token is never shown again. */
export interface Invite {
  id: number;
  created_at: number;
  expires_at: number;
  created_by: string;
}

/** A token a worker on another machine signs in with: GET /api/worker-tokens.
    The token itself is shown once, when it is made, and never again. */
export interface WorkerToken {
  id: number;
  name: string;
  created_at: number;
  /** The admin who made it, whose it is. */
  created_by: string;
  /** To the minute; null if no worker has used it yet. */
  last_used_at: number | null;
}

/** A token for scripts, with the scopes it was given (#103): your own at
    GET /api/me/tokens, and for an admin, everyone's at GET /api/tokens. The
    token itself is shown once, when it is made, and never again. */
export interface ApiToken {
  id: number;
  name: string;
  /** As read:library or write:admin. Writing includes reading. */
  scopes: string[];
  /** Whose it is: it reads their library. */
  owner: string;
  created_at: number;
  expires_at: number;
  /** To the minute; null if nothing has used it yet. */
  last_used_at: number | null;
}

/** GET /api/me/tokens: your tokens, the scopes you may give one, and the
    longest one may live, in days. */
export interface MyTokens {
  tokens: ApiToken[];
  scopes: string[];
  max_days: number;
}

export type NoticeState = { text: string; kind: "ok" | "error" } | null;

export type Sort ="relevance" | "saved_desc" | "saved_asc" | "author" | "random";
export type FilterKind = "collection" | "tag" | "game" | "hashtag";
export type View = "library" | "recipes" | "places";
/** How Places draws its reels. */
export type Layout = "grid" | "map";
/** Reels are videos; posts and carousels are images. "" is both. */
export type Media = "" | "video" | "image";
/** Recipe filters by ingredient or ingredient group. */
export type PickKind = "has" | "without";

/** app/recipes/aisles.py: the aisles, in the order a list shows them. */
export type Aisle = "produce" | "meat" | "dairy" | "bakery" | "pantry" | "spices"
  | "condiments" | "frozen" | "drinks" | "other";

/** GET /api/shopping: your list's recipes and their ingredients, merged. */
export interface ShoppingList {
  recipes: { shortcode: string; title: string | null; servings: number | null;
             thumb: string | null }[];
  lines: ShoppingLine[];
}

export interface ShoppingLine {
  /** What a tick is kept by. */
  key: string;
  name: string;
  aisle: Aisle;
  /** Salt, pepper, oil, water: folded away. */
  staple: boolean;
  /** Summed where the units agree, else as written: ["150 g", "2 tbsp"]. */
  amounts: string[];
  /** The shortcodes of the recipes that need it. */
  recipes: string[];
  ticked: boolean;
}

/** What an export of one library can be narrowed to (/api/export/options). */
export interface ExportOptions {
  user: string;
  total: number;
  recipes: number;
  collections: Named[];
  tags: Tag[];
}

/* ---------- Hosted AI (#116, #125) ---------- */

export type AiStage = "asr" | "ocr" | "tag" | "recipe";
export const AI_STAGES: AiStage[] = ["asr", "ocr", "tag", "recipe"];

/** A setting's value and where it comes from: env:NAME, saved, default or unset. */
export interface AiValue<T> {
  value: T | null;
  source: string;
}

export interface AiStageSetting {
  provider: AiValue<string>;
  model: AiValue<string>;
  base_url: AiValue<string>;
  default_model: string | null;
}

export interface AiKey {
  set: boolean;
  masked: string | null;
  source: string;
  problem: string | null;
}

export interface AiSwitch {
  on: boolean;
  scope: "me" | "all" | null;
  since: number | null;
  reason: string | null;
  at: number | null;
  by: number | null;
}

export interface AiPreset {
  name: string;
  label: string;
  stages: AiStage[];
  models: Partial<Record<AiStage, string | null>>;
  segments: string[];
  free: string;
  terms: string;
  checked: string;
  needs_base_url: boolean;
  key_env: string;
  /** False for a server of your own, which takes no key. */
  needs_key: boolean;
  /** What to type for its base URL, when that is yours to give. */
  url_hint: string;
  /** It runs on your own machine: nothing leaves your network. */
  self_hosted: boolean;
  /** Per stage, models worth trying, the default first. */
  recommended: Partial<Record<AiStage, string[]>>;
}

export type AiUse = "mine" | "server" | "workers" | "default";

export interface AiPage {
  owner: number | null;
  stages: Record<AiStage, AiStageSetting>;
  keys: Record<string, AiKey>;
  budget: AiValue<number>;
  language: AiValue<string>;
  switch: AiSwitch;
  problems: string[];
  presets: AiPreset[];
  spent: number;
  history: { at: number; action: string; detail: string | null; actor: string | null }[];
  /** The one choice: a user's default / mine / workers; the admin's mine / workers. */
  choice: AiUse;
  choices: AiUse[];
  // A user's own page only.
  use?: AiUse;
  applies?: "mine" | "server" | "workers";
  server?: { available: boolean; providers: string[] };
}

export interface AiBacklog {
  reels: number;
  usd_low: number;
  usd_high: number;
  unpriced: string[];
}

export interface AiTest {
  ok: boolean;
  said: string;
  detail: string;
  model?: string;
}

export interface AiUserRow {
  id: number;
  username: string;
  use: AiUse;
  keys: string[];
  on: boolean;
  reason: string | null;
  spent: number;
}

export interface ApiStatus {
  name: string;
  owner: number | null;
  /** null for a hosted worker on another machine, which only reports spend. */
  on: boolean | null;
  scope?: "me" | "all" | null;
  reason?: string | null;
  waiting?: number;
  in_flight?: number;
  left_to_workers?: number;
  spent: number;
  budget?: number | null;
  providers?: string[];
  last?: number;
}

export interface MyPipeline {
  stages: { stage: string; waiting: number }[];
  worker_seen_at: number | null;
  applies: "mine" | "server" | "workers";
  api: { on: boolean; reason: string | null; in_flight: number; spent: number; budget: number | null; waiting: number };
  now: number;
}

/** GET /api/ask: an answer from your saves (#45). */
export interface Answer {
  found: boolean;
  /** With [n] where it cites citations' n. */
  answer: string;
  citations: { n: number; shortcode: string; at_ms: number | null }[];
  items: Item[];
  model: string;
}

/** A logging or monitoring setting on the Account page (admins). A secret's
    value is only whether one is set. */
export interface MonitoringSetting {
  name: string;
  kind: "text" | "bool" | "choice" | "secret";
  choices: string[];
  default: string;
  source: "env" | "saved" | "default";
  value: string | boolean;
}

export interface Monitoring {
  settings: MonitoringSetting[];
  metrics: boolean;
  metrics_port: number | null;
  shipping: string[];
  problem: string | null;
}
