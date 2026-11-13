import type {
  AiBacklog, AiPage, AiStage, AiTest, AiUse, AiUserRow, MyPipeline,
  Account, ApiToken, AuthState, ExportOptions, ImportResult, Invite, Item, ItemDetail, ItemGame, ItemPage,
  LogTail, Me,
  MyTokens, Named, WorkerToken,
  PipelineState, PlacePage, Recipe, RecipeFacets, RecipeIn, ShoppingList, Stage, Stats, Tag, TagChoice,
} from "./types";

/* Pages anyone may open, signed in or not. Anywhere else, a 401 means the
   session is gone, and the browser goes to sign in and comes back after. */
export const PUBLIC = ["/login", "/setup", "/invite/"];

/* Set on signing out: a request still in flight then comes back 401, and
   must not send the browser to sign in "and come back" to the page it left. */
let leaving = false;

function signIn() {
  if (leaving) return;
  const here = location.pathname + location.search;
  if (PUBLIC.some((p) => location.pathname.startsWith(p))) return;
  location.assign(`/login?next=${encodeURIComponent(here)}`);
}

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(path, options);
  if (res.status === 401 && !path.startsWith("/api/auth")) signIn();
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export const getItems = (params: URLSearchParams, signal?: AbortSignal) =>
  api<ItemPage>(`/api/items?${params}`, { signal });

export const getPlaces = (params: URLSearchParams, signal?: AbortSignal) =>
  api<PlacePage>(`/api/places?${params}`, { signal });

/** Where a typed place is: "Osaka". */
export const geocode = (q: string) =>
  api<{ lat: number; lng: number; label: string | null }>(
    `/api/geocode?q=${encodeURIComponent(q)}`);

export const getItem = (shortcode: string) =>
  api<ItemDetail>(`/api/items/${encodeURIComponent(shortcode)}`);

/** The reels in your library closest to this one in meaning (#53). */
export const getSimilar = (shortcode: string) =>
  api<{ items: Item[] }>(`/api/items/${encodeURIComponent(shortcode)}/similar`);

/** You opened it, for Rediscover's never-opened strip (#50). */
export const markOpened = (shortcode: string) =>
  fetch(`/api/items/${encodeURIComponent(shortcode)}/opened`, { method: "POST" });

/** Old saves to bring back: saved on this day in earlier years, and never opened. */
export function getRediscover() {
  const now = new Date();
  const md = `${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  return api<{ on_this_day: Item[]; never_opened: Item[] }>(
    `/api/rediscover?md=${md}&tz=${now.getTimezoneOffset()}`);
}

export const getStats = () => api<Stats>("/api/stats");
export const getCollections = () => api<Named[]>("/api/collections");
export const getTags = () => api<Tag[]>("/api/tags");
export type LogName = "server" | "worker" | "server-worker";
export const getLogs = (lines: number, log: LogName = "server") =>
  api<LogTail>(`/api/logs?lines=${lines}&log=${log}`);
export const getPipeline = () => api<PipelineState>("/api/pipeline");
export const getRecipeFacets = () => api<RecipeFacets>("/api/recipes/facets");

const recipeUrl = (shortcode: string) => `/api/items/${encodeURIComponent(shortcode)}/recipe`;

/** Fetch the rest of an incomplete recipe from where the reel points (#114). */
export const fetchRecipe = (shortcode: string) =>
  api<Recipe>(`${recipeUrl(shortcode)}/fetch`, { method: "POST" });

/** The recipe in this language; `recipe` is null when it needs no translating. */
export const getRecipeTranslation = (shortcode: string, lang: string) =>
  api<{ recipe: Recipe | null; model: string | null }>(
    `${recipeUrl(shortcode)}/translation?lang=${encodeURIComponent(lang)}`);

/** Save your version of a reel's recipe. Returns the recipe it now shows. */
export const saveRecipe = (shortcode: string, body: RecipeIn) =>
  api<Recipe | null>(recipeUrl(shortcode), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

/** Drop your version, so the model's shows again (or none, if it had none). */
export const revertRecipe = (shortcode: string) =>
  api<Recipe | null>(recipeUrl(shortcode), { method: "DELETE" });

/** Give the reels failed at one stage, or at the fetch, another round of tries. */
export const retryStage = (stage: Stage | "fetch") =>
  api<PipelineState>("/api/pipeline/retry", json("POST", { stage }));

/* ---------- the shopping list ---------- */

export const getShopping = () => api<ShoppingList>("/api/shopping");

const json = (method: string, body: unknown): RequestInit => ({
  method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});

/** Put these recipes on your list. Returns the list as it is now. */
export const addToList = (shortcodes: string[]) =>
  api<ShoppingList>("/api/shopping/recipes", json("POST", { shortcodes }));

/** Put every recipe these filters choose on your list. */
export const addMatchingToList = (params: URLSearchParams) =>
  api<ShoppingList>(`/api/shopping/recipes/matching?${params}`, { method: "POST" });

export const dropFromList = (shortcode: string) =>
  api<ShoppingList>(`/api/shopping/recipes/${encodeURIComponent(shortcode)}`,
                    { method: "DELETE" });

export const clearList = () => api<ShoppingList>("/api/shopping", { method: "DELETE" });

export const tickLine = (line: string, ticked: boolean) =>
  api<{ line: string; ticked: boolean }>("/api/shopping/ticks", json("PUT", { line, ticked }));

/** The connection went before the server answered. */
export class NetworkError extends Error {}

/* An export ZIP, sent with XMLHttpRequest rather than fetch, which cannot
   report how much of the body has gone. `onsent(fraction)` follows the
   upload; once it reaches 1 the server is reading the export. Otherwise as
   api(): a 401 goes to sign in, and a refusal throws the server's reason. */
export function importExport(file: File, onsent?: (fraction: number) => void) {
  return new Promise<ImportResult>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/import");
    xhr.responseType = "json";
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onsent?.(e.loaded / e.total); };
    xhr.upload.onload = () => onsent?.(1);
    xhr.onerror = () => reject(new NetworkError());
    xhr.onload = () => {
      if (xhr.status === 401) signIn();
      if (xhr.status >= 200 && xhr.status < 300) resolve(xhr.response as ImportResult);
      else reject(new Error(xhr.response?.detail || `${xhr.status} ${xhr.statusText}`));
    };
    const body = new FormData();
    body.append("file", file);
    xhr.send(body);
  });
}

const tagOf = (shortcode: string) => `/api/items/${encodeURIComponent(shortcode)}/tag`;

/** Choose a reel's tag yourself; no pass changes it after. */
export const setTag = (shortcode: string, name: string) =>
  api<TagChoice>(tagOf(shortcode), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });

/** Drop your choice, so the reel shows the automatic one again. */
export const clearTag = (shortcode: string) =>
  api<TagChoice>(tagOf(shortcode), { method: "DELETE" });

const gameOf = (shortcode: string) => `/api/items/${encodeURIComponent(shortcode)}/game`;

/** Say which game a reel shows: confirm or correct it, or null for none. */
export const setGame = (shortcode: string, name: string | null) =>
  api<ItemGame>(gameOf(shortcode), {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });

/** Drop your answer, so the reel shows the game pass's again. */
export const clearGame = (shortcode: string) =>
  api<ItemGame>(gameOf(shortcode), { method: "DELETE" });

/** Take a reel out of your library. `purged`: nobody else had it, so it is gone. */
export const removeItem = (shortcode: string) =>
  api<{ shortcode: string; purged: boolean }>(`/api/items/${encodeURIComponent(shortcode)}`,
                                              { method: "DELETE" });

/** Fetch a reel whose fetch failed again, now. Returns it as it is after. */
export const retryFetch = (shortcode: string) =>
  api<ItemDetail>(`/api/items/${encodeURIComponent(shortcode)}/retry`, { method: "POST" });

/* ---------- exports ---------- */

/** Where to download a library, or its recipes, in another tool's format.
    `params` narrows it: the grid's filters, a shortcode for one reel, images
    and videos for the vault, and for an admin, `user` for whose library. A
    plain link: the browser saves what it gets. */
export function exportUrl(kind: string, params: URLSearchParams = new URLSearchParams()) {
  const q = params.toString();
  return `/api/export/${encodeURIComponent(kind)}${q ? `?${q}` : ""}`;
}

/** What an export of this library can be narrowed to. */
export const getExportOptions = (user?: string) =>
  api<ExportOptions>(`/api/export/options${user ? `?user=${encodeURIComponent(user)}` : ""}`);

/** One reel's Markdown note, as text, to copy. */
export async function getNote(shortcode: string): Promise<string> {
  const res = await fetch(exportUrl("note.md", new URLSearchParams({ shortcode })));
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.text();
}

/* ---------- accounts ---------- */

const send = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: body === undefined ? undefined : { "Content-Type": "application/json" },
  body: body === undefined ? undefined : JSON.stringify(body),
});

export const getAuth = () => api<AuthState>("/api/auth");
export const getMe = () => api<Me>("/api/me");

export const login = (username: string, password: string) =>
  api<Me>("/api/auth/login", send("POST", { username, password }));
export const logout = () => {
  leaving = true;
  return api<object>("/api/auth/logout", send("POST"));
};

/** Claim the library with the token from the server's log. */
export const setup = (token: string, username: string, password: string) =>
  api<Me>("/api/auth/setup", send("POST", { token, username, password }));

const inviteUrl = (token: string) => `/api/auth/invite/${encodeURIComponent(token)}`;
export const inviteState = (token: string) => api<{ open: boolean }>(inviteUrl(token));
export const acceptInvite = (token: string, username: string, password: string) =>
  api<Me>(inviteUrl(token), send("POST", { username, password }));

export const updateMe = (ig_handle: string) => api<Me>("/api/me", send("PUT", { ig_handle }));
export const setCollapseCopies = (on: boolean) =>
  api<Me>("/api/me", send("PUT", { collapse_copies: on }));

/** Two reels are not the same video after all (#58). */
export const notACopy = (shortcode: string, other: string) =>
  api<{ copies: string[] }>(
    `/api/items/${encodeURIComponent(shortcode)}/not-copy/${encodeURIComponent(other)}`,
    { method: "POST" });
export const changePassword = (current: string, next: string) =>
  api<object>("/api/me/password", send("PUT", { current, new: next }));
export const signOutEverywhere = () => api<object>("/api/me/sign-out-everywhere", send("POST"));

export const getAccounts = () => api<Account[]>("/api/users");
export const changeAccount = (id: number, change: { disabled?: boolean; is_admin?: boolean }) =>
  api<Account[]>(`/api/users/${id}`, send("PUT", change));

export const getInvites = () => api<Invite[]>("/api/invites");
/** A new invite link. Its path is shown this once. */
export const makeInvite = () => api<{ path: string; expires_at: number }>("/api/invites", send("POST"));
export const revokeInvite = (id: number) => api<Invite[]>(`/api/invites/${id}`, send("DELETE"));

/* ---------- API tokens (#103) ---------- */

export const getMyTokens = () => api<MyTokens>("/api/me/tokens");
/** A new API token. The token is shown this once. */
export const makeMyToken = (name: string, scopes: string[], days: number) =>
  api<MyTokens & { id: number; token: string }>("/api/me/tokens", send("POST", { name, scopes, days }));
export const revokeMyToken = (id: number) => api<MyTokens>(`/api/me/tokens/${id}`, send("DELETE"));

/** Everyone's API tokens, for an admin. Never the tokens themselves. */
export const getAllTokens = () => api<ApiToken[]>("/api/tokens");
export const revokeAnyToken = (id: number) => api<ApiToken[]>(`/api/tokens/${id}`, send("DELETE"));

/* ---------- worker tokens (#35) ---------- */

export const getWorkerTokens = () => api<WorkerToken[]>("/api/worker-tokens");
/** A new worker token. The token is shown this once. */
export const makeWorkerToken = (name: string) =>
  api<{ id: number; token: string; tokens: WorkerToken[] }>("/api/worker-tokens", send("POST", { name }));
export const revokeWorkerToken = (id: number) =>
  api<WorkerToken[]>(`/api/worker-tokens/${id}`, send("DELETE"));

/* Hosted AI (#125): a user's own API under /api/me/providers, the server's
   under /api/admin/settings/providers. The same calls for both. */
export type AiWhose = "mine" | "server";
const aiBase = (whose: AiWhose) =>
  whose === "server" ? "/api/admin/settings/providers" : "/api/me/providers";

export const ai = {
  get: (w: AiWhose) => api<AiPage>(aiBase(w)),
  setStage: (w: AiWhose, stage: AiStage | "all", body: { provider?: string; model?: string; base_url?: string }) =>
    api<AiPage>(`${aiBase(w)}/stages/${stage}`, send("PUT", body)),
  setKey: (w: AiWhose, provider: string, key: string) =>
    api<AiPage>(`${aiBase(w)}/keys/${encodeURIComponent(provider)}`, send("PUT", { key })),
  removeKey: (w: AiWhose, provider: string) =>
    api<AiPage>(`${aiBase(w)}/keys/${encodeURIComponent(provider)}`, send("DELETE")),
  setBudget: (w: AiWhose, usd: number | null) => api<AiPage>(`${aiBase(w)}/budget`, send("PUT", { usd })),
  setLanguage: (w: AiWhose, code: string | null) =>
    api<AiPage>(`${aiBase(w)}/language`, send("PUT", { code })),
  backlog: (w: AiWhose) => api<AiBacklog>(`${aiBase(w)}/backlog`),
  switchOn: (w: AiWhose, backlog: boolean) =>
    api<AiPage>(`${aiBase(w)}/switch`, send("POST", { on: true, backlog })),
  switchOff: (w: AiWhose) => api<AiPage>(`${aiBase(w)}/switch`, send("POST", { on: false })),
  test: (w: AiWhose, stage?: AiStage) =>
    api<Record<AiStage, AiTest>>(`${aiBase(w)}/test`, send("POST", { stage: stage ?? null })),
  models: (w: AiWhose, provider: string) =>
    api<string[]>(`${aiBase(w)}/models?provider=${encodeURIComponent(provider)}`),
  setUse: (w: AiWhose, use: AiUse) => api<AiPage>(`${aiBase(w)}/use`, send("PUT", { use })),
  users: () => api<AiUserRow[]>("/api/admin/ai/users"),
  removeUserKey: (id: number, provider: string) =>
    api<AiUserRow[]>(`/api/admin/ai/users/${id}/keys/${encodeURIComponent(provider)}`, send("DELETE")),
};

export const getMyPipeline = () => api<MyPipeline>("/api/me/pipeline");
