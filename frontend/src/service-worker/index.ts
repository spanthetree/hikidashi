/// <reference lib="webworker" />
import { version } from "$app/env";
import { assets, immutable } from "$app/manifest";
import { self as sw } from "$app/service-worker";

/* What makes Hikidashi installable, and its share target reachable (#52).
   It keeps the app's shell only: the built scripts and styles and the icons.
   Never the API or the media: those are a person's library, and a cached
   answer would be a stale or, after signing out, someone else's. Pages come
   from the network first, so a new release is never hidden behind an old
   index.html; offline, the cached one opens and says it cannot reach the
   server. */
const CACHE = `shell-${version}`;
// The API explorer's Swagger UI is 1.5 MB that nobody needs offline.
const SHELL = [
  ...immutable.map((f) => f.path),
  ...assets.map((f) => f.path).filter((p) => !p.startsWith("/explorer/")),
  "/",
];
const CACHED = new Set(SHELL);

sw.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => sw.skipWaiting()));
});

sw.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) if (key !== CACHE) await caches.delete(key);
    await sw.clients.claim();
  })());
});

sw.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== location.origin) return;

  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(async () =>
      (await caches.match("/")) ?? Response.error()));
    return;
  }
  if (CACHED.has(url.pathname)) {
    event.respondWith((async () => (await caches.match(url.pathname)) ?? fetch(request))());
  }
});
