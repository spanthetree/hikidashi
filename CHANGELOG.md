# Changelog

All notable changes to Hikidashi are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.18.7] — 2026-11-12

### Changed

- **The docs no longer describe the real library** (#123). The search,
  tags, design and operation pages, ADR 0009 and the README quoted real
  search queries, captions and tag counts. Their examples now come from the
  demo library or are described generically. The measured numbers are
  unchanged.

## [0.18.5] — 2026-11-11

### Changed

- **The browser tests run in Playwright's own image** (#131),
  `mcr.microsoft.com/playwright`, which already has Chromium and its system
  packages. CI no longer installs them from Ubuntu's mirrors, so e2e can't
  fail when those are unreachable before a test has run. Python 3.12 for the
  test server comes from uv, the same release the Dockerfile uses.
  `tests/test_ci.py` checks that the image's tag matches `@playwright/test`.

## [0.18.2] — 2026-11-11

### Added

- **Published images** (#77). Each release publishes
  `ghcr.io/spanthetree/hikidashi-server` and `hikidashi-worker` (amd64 and
  arm64) and `hikidashi-worker-cuda` (amd64), tagged with the version, its
  minor series and `latest`. Each image is started in CI before it's pushed.
  The Unraid templates and the TrueNAS file, which already named these
  images, now work as written.
- **Merging a version bump releases it.** `.github/workflows/release.yml`
  publishes the images when a new version lands on `master`, then tags the
  commit and makes a GitHub release from the changelog.
- `HIKIDASHI_VERSION` picks the release of the images (unset: `latest`), and
  `HIKIDASHI_WORKER_IMAGE` picks `hikidashi-worker` or `hikidashi-worker-cuda`.

### Changed

- **Docker pulls instead of building.** `docker-compose.yml` and
  `docker-compose.worker.yml` name the published images, and
  `./hikidashi start` pulls them before it starts, so starting also updates.
  `--build` still builds from the checkout, and so does Compose when there is
  nothing to pull. To keep running your own builds, set `HIKIDASHI_VERSION`
  to a tag nobody published, such as `dev`, so a start never pulls over them.
- Compose Manager on Unraid pulls the images too: `docker compose pull`, then
  **Compose Up**.

## [0.18.1] — 2026-11-11

### Fixed

- **Copy buttons work over plain HTTP** (#107). A NAS is usually opened as
  `http://<host>:8080`, where the browser has no clipboard API, and every
  copy button (worker and API tokens, the worker command, invite links, the
  shopping list, a reel's note) quietly did nothing. They now fall back to
  the browser's older copy command, and if that fails too, say so, select the
  text for Ctrl+C / ⌘C, and mention that HTTPS would make the button work.
  "Copied" is announced to screen readers.

## [0.18.0] — 2026-11-11

**ReelVault is now Hikidashi** (#121): 引き出し, a drawer you pull your saves
out of again. Another product was already called ReelVault: an Instagram
video downloader.

**Upgrading:** stop the old server and workers first (`./reelvault stop`, or
`docker compose down`): the containers have new names, and the old ones
would keep the port. Then pull, and start with `./hikidashi start`, adding
`--build` under Docker. Until 0.19.0, what you already have keeps working:

- **`REELVAULT_*` settings** still count. `./hikidashi` rewrites an `.env`
  that has them to `HIKIDASHI_*` and keeps the old one as
  `.env.reelvault.bak`. Rename them by hand in an Unraid template, the
  Compose Manager's env file, a TrueNAS app or a remote worker's shell.
- **`data/reelvault.sqlite3`** is used while there is no
  `hikidashi.sqlite3`. To move it, rename the file and its `-wal` and `-shm`
  with everything stopped.
- **API and worker tokens** made before (`rv_api_…`, `rv_wkr_…`) still work.
  New ones read `hk_api_…` and `hk_wkr_…`.
- **Everyone signs in once more:** the session cookie is now `hk_session`.

### Changed

- **The name, everywhere:** the `hikidashi` command and `./hikidashi`
  script, `HIKIDASHI_*` settings, the `hikidashi-server` and
  `hikidashi-worker` images, the Unraid and TrueNAS templates, the UI in
  English and Japanese, and github.com/spanthetree/hikidashi.
- **A new icon:** an open drawer holding three cards, the middle one
  vermilion. It replaces the vault door on Instagram's gradient, which
  borrowed Meta's look. Light and dark, in the favicon, the top bar and the
  Unraid templates. Its sources and colours are in `docs/brand/`.
- **The README animation, the tour and the import box** are recorded again
  under the new name.
- **The search eval's query set** (#123) is 22 queries over the demo
  library. A set over your own library goes in `data/search_eval.json`,
  git-ignored, and `python -m app.search.eval` reads it first.

## [0.17.0] — 2026-11-10

**Upgrading:** `TAG_V` is 3, so the tagger reads every reel once more, and
the search index is rebuilt on first start. A few borderline categories may
come back different. Run the same release on a remote worker.

### Added

- **The video game a gaming reel shows** (#117). The tagger asks the reels it
  files under Gaming or Sim racing, and any whose text reads like video
  games, which game they show, and checks the title on its own ("Grand
  Turismo 4" is Gran Turismo 4). It applies the title only on evidence: one
  game, high confidence, a title the check can place, no other game named,
  and the title in the reel's own text. Then the reel shows it, search finds
  it, and a reel filed elsewhere becomes a gaming reel. Anything short of
  that is a question in the detail sheet: "Is this Cyberpunk 2077?", with
  Yes, No game, or another title. Your answer is yours alone, and no re-tag
  changes it. On this library, 6 of 39 candidates were sure, all right, and
  7 were questions. Offline: the same local model, no games database.
  `PUT`/`DELETE /api/items/{shortcode}/game`. ADR 0014.
- **Filters that intersect** (#117). Tags, games, collections and hashtags
  narrow one another instead of replacing one another, each as its own pill:
  "Gaming + Minecraft", "Food + #ramen + #tokyo". A reel has one tag, one
  game and here one collection, so a second of those swaps in for the first;
  hashtags add up. The API takes `tag`, `game` and `hashtag` repeated. ADR
  0015.

### Changed

- **A reel's tag sources** are yours, then the game pass's, then the model's,
  then the hashtags'. Older databases have the `item_tag` triggers rebuilt.

## [0.16.0] — 2026-11-10

### Removed

- **Cook mode.** Recipes export to Mealie and Tandoor, which have their own
  cooking views, so the full-screen step view goes, with its kitchen timers,
  servings scaling and the recipe tab's **Cook** button. The recipe tab keeps
  its ingredient checklist, and the shopping list is unchanged.
- **`timers` and `ingredients[].amount` in a recipe from the API.** Only
  cook mode read them. The step duration parser goes too; the shopping list
  still adds amounts up.

## [0.15.2] — 2026-10-30

### Fixed

- **The reels that couldn't be downloaded show on the Pipeline page**
  (#113). The sidebar's "failed" counts reels whose fetch failed, but the
  Pipeline page listed only the stages' failures, so it showed none of them.
  It now lists them above the stages' failures, each with why and whether a
  retry could help: restricted, deleted and login-only posts fail the same
  way every time. **Retry** shows only when some ran out of tries, and offers
  only those again. "1 tries" reads "1 try".

## [0.15.1] — 2026-10-29

### Added

- **Compose Manager stack folders** (#111).
  `deploy/unraid/compose-manager/everything` and `server-only` each hold one
  file that includes the checkout's compose files by relative path, so a
  Compose Manager stack points at a folder with nothing else in it, and
  `git pull` keeps it current. Tests check each holds one compose file and
  never the remote worker's.
- **Moving a library to Unraid** (#111), in the install guide: a
  single-file copy of the database, the new install's `-wal` and `-shm`
  removed, rsync of the database and media, a `chown` for `nobody`, and a
  count to check against the old machine's.

### Fixed

- **The Compose Manager install** (#111). The guide pointed the stack at the
  checkout, but the plugin passes every `*compose*.yml` in a stack's folder
  with `-f`, so `docker-compose.worker.yml` was merged in and failed without
  `HIKIDASHI_SERVER`. `COMPOSE_FILE` in `compose.env`, which picked the
  server-only layout, is never read there, and Compose Up does not rebuild
  an image that exists. The guide now uses the stack folders, checks the
  layout with `docker compose config --services`, builds by hand, says to
  untick *All profile-based services* in the profiles dialog, and updates
  with a build before Compose Up. Checked on Compose Manager Plus.

## [0.15.0] — 2026-10-29

**Upgrading with a remote worker:** if your server has
`HIKIDASHI_WORKER_TOKEN` set in its own environment, a remote worker using it
is refused after this upgrade. Make a worker token on the Pipeline page, or
with `./hikidashi token make desktop --worker`, give it to the worker as its
`HIKIDASHI_WORKER_TOKEN`, and unset the variable on the server. Worker tokens
made on the Pipeline page keep working.

### Added

- **API tokens** (#103): a way into the API for anything that isn't a
  browser, as `Authorization: Bearer <token>`. Anyone makes their own on
  **Your account**, with a name, the scopes they tick (`read:library`,
  `write:library`, and for admins `read:admin` and `write:admin`), and a life
  of 30 days at most. A token is shown once, acts as its owner, and reads only
  their library. It can't change a password, sign out sessions, or make or
  revoke tokens. Admins see everyone's tokens on the Pipeline page and can
  revoke any of them.
- **`hikidashi token`** (#103): `make`, `list` and `revoke`, for API tokens
  and, with `make --worker`, worker tokens, on a server with no browser at
  hand.
- **ADR 0013** (#103): one token system for API and worker tokens, checked
  against GitHub, GitLab, Gitea, Immich and Buildkite.

### Changed

- **Tokens look like `hk_api_…` and `hk_wkr_…`** (#103), with a checksum, so
  a leaked one is recognisable and a mistyped one is refused at once. API and
  worker tokens are kept in one table, `api_token`. Existing worker tokens
  move into it on the first start and keep working.
- **Worker tokens open only the worker routes** (#103). They work only while
  the admin who made them is still an admin, and no API token, even an
  admin's, opens the worker routes.
- **Refusals follow RFC 6750** (#103): a 401 carries `WWW-Authenticate:
  Bearer`, with `invalid_token` for a bad, expired or revoked token, and a
  token without the scope is a 403 with `insufficient_scope`.
- **Disabling an account revokes its tokens** (#103), from the web page or
  with `hikidashi user disable`, as it already ended its sessions.

### Removed

- **The server's own `HIKIDASHI_WORKER_TOKEN`** (#103). It was a shared
  secret with no owner, revoked only by a restart. Set on the server, it is
  now ignored, with a warning at start-up. On a remote worker it is still
  the token the worker sends. The setting is gone from `docker-compose.yml`
  and the Unraid and TrueNAS files.
## [0.14.0] — 2026-10-29

### Added

- **`hikidashi worker --profile` and `--ollama-url`** (#108), beside
  `--server`. Each defaults to its variable (`HIKIDASHI_PROFILE`,
  `OLLAMA_URL`, `HIKIDASHI_SERVER`), and the flag wins. The profile stays
  `auto`, which finds a GPU by itself; `cuda` insists on one.
- **The worker asks for its token** (#108). With `--server` and no
  `HIKIDASHI_WORKER_TOKEN`, it asks at the terminal, with nothing shown as it
  is typed. The token is never a flag, so it stays out of shell history and
  the process list. With no terminal, as under Docker, it refuses as before.

### Changed

- **`docker-compose.worker.yml` builds the cuda image by default** (#108).
  `HIKIDASHI_EXTRAS` set to nothing builds the CPU one. The GPU `deploy`
  block stays commented, so a host with no NVIDIA toolkit still starts, on
  its CPU. `.env.example` now leaves `HIKIDASHI_EXTRAS` commented out, so a
  copied `.env` keeps each compose file's default.

### Security

- **The worker token is kept out of logs and tracebacks** (#108): the
  settings leave it out of their repr.

## [0.13.0] — 2026-10-29

### Added

- **The import box** (#105). An empty library opens on it, and **Import** in
  the toolbar opens it in a dialog. It links to Instagram's *Export your
  information* page in a new tab, lists what to choose there (Export to
  device, Saved only, All time, JSON), with the phone's path under "On your
  phone?", and has a zone to drop the ZIP on that is also a button, so a
  click, a tap or the keyboard chooses it instead. Each ZIP shows its name,
  size, an upload bar, then the reading step, and its counts or its error
  with Retry, in the box. Several ZIPs dropped at once import one after
  another. A file not named `.zip` is refused there, without being sent.
- **Drop a ZIP anywhere on the library** (#105). A full-page overlay says
  it can be dropped, and the drop opens the import dialog to show it going in.

### Changed

- **Import** in the toolbar opens the import box instead of a file picker
  (#105). Uploads carry on with the dialog closed, and one that lands then
  says so in a notice.

### Fixed

- **A ZIP dropped on the page no longer opens or downloads it** (#105). The
  empty library said "drop the ZIP here", and nothing took the drop.

## [0.12.0] — 2026-10-28

### Added

- **Install files for Unraid and TrueNAS SCALE** (#87).
  [`deploy/unraid/`](deploy/unraid/) has a Community Applications template
  for each container (the server, its worker, and the CPU and CUDA workers)
  and a `.env` for the Compose Manager plugin.
  [`deploy/truenas/hikidashi.yaml`](deploy/truenas/hikidashi.yaml) is a
  compose file for *Install via YAML*. Both pull the published images, which
  wait on #77.
- **Install guides**, [docs/install-unraid.md](docs/install-unraid.md) and
  [docs/install-truenas.md](docs/install-truenas.md) (#87): the pool or
  dataset, the first run, a GPU, a remote worker, updating, backing up, and
  troubleshooting.
- **`HIKIDASHI_USER`** (#87): the user the compose files run the containers
  as. Root unless set, so nothing changes for an existing install.
- **ADR 0012** (#87): why templates and YAML, not the TrueNAS catalog, and
  why `--user` rather than PUID/PGID.

### Changed

- **The images run as any user** (#87). `HOME` is `/tmp`, so yt-dlp and
  onnxruntime no longer fail to write to `/` as a user with no home.

### Fixed

- **A first start no longer crashes the server-worker** (#87). The
  containers open a new database at once, and one could migrate a database
  that another was half-way through creating, then fail with `no such table:
  main.tag`. Opening the database now takes turns, with a lock file beside
  it, `hikidashi.sqlite3-init.lock`.
## [0.11.3] — 2026-10-28

### Fixed

- **Cards that ignored clicks after the sheet closed** (#94). In Chromium, a
  card that was in the grid under the open sheet, and was then moved when the
  results changed, could keep the inertness the modal gave it: no click, no
  focus, and nothing in the DOM to show it. The grid now has the browser work
  each card's inertness out again after the results change. On the tour's
  steps, 11 of 14 takes failed before and none of 14 after.
- **Whisper's stock phrases on reels with no speech** (#95). `Thank you.`,
  `you`, `Oh` and the like, written over a second of music or crowd noise, are
  dropped: a transcript with under 2.5 s of speech, under half a word a second
  of it, or mostly stock phrases is no transcript. On the library 31 of 227
  transcripts went, all scraps, and the shortest real sentence (2.7 s) stayed.
  `ASR_V` is 2, so every reel is transcribed again. A reel whose transcript
  changes, or is now gone, is tagged and checked for a recipe again, and
  search and embeddings follow.
- **Single-photo posts said "Video not downloaded"** (#96). A post whose fetch
  found no video now shows its photo alone. The enrich hint stays for a reel
  whose mp4 is missing. The item API gains `photo`.
- **Recipes that are not food, or not recipes** (#97). The recipe check now
  names the dish and whether it is edible, and says no for food sold at a stall
  and kitchen tips that make no dish. A reel not tagged Food needs a firm,
  edible yes. On qwen2.5:3b against the hand labels, precision went from 0.89
  to 0.93 with recall from 0.97 to 0.96. On the demo library it went from 0.73
  to 0.92, with Transparent Wood, Washing Salad Greens, a bakery and a ramen
  lunch set no longer recipes. `RECIPE_V` is 5, so every reel is checked again.
  Your own edits and "Not a recipe" are kept.

Both version bumps change what a remote worker must run: upgrade it with the
server, or the server refuses it with a 409 naming both versions.

## [0.11.2] — 2026-10-28

### Added

- **Architecture and design docs with diagrams** (#88).
  [docs/architecture.md](docs/architecture.md) covers the system from the top
  down: context, images, deployment layouts, the data model, storage, and the
  security boundaries. [docs/operation.md](docs/operation.md) covers it at
  runtime: a reel's lifecycle, the worker loop, the remote protocol, a search,
  an import and a signed-in request. Static diagrams are Mermaid, which GitHub
  renders.
- **Decision records** in [docs/adr/](docs/adr/README.md) (#88). Eleven records
  back-filled from the issues, from SQLite over Postgres to search by rank
  fusion with no reranker.
- **Five animated SVGs** (#88): a reel through the pipeline, with a failure
  that backs off (replacing `docs/pipeline.svg`); leases; the remote worker's
  round trip; search fusion; and the shared cache with separate libraries.
  Each has light and dark themes, and shows its end state under reduced
  motion.
- **`scripts/make_diagrams.py`** (#88) generates the ER diagrams from
  `app/schema.sql`, the stage graph from `app/jobs`, and the animated SVGs.
  `tests/test_diagrams.py` fails when any output is stale.
- **CONTRIBUTING: keeping the docs true** (#88). A table of which code change
  updates which doc, diagram or record, in the same PR.
- **A new README animation, recorded from the real app** (#88).
  `docs/demo/app.webp` replaces `docs/app.svg`, a drawing of the UI that had
  fallen behind it. A longer tour is in `docs/demo/tour.mp4`, and a remote
  worker's real terminal is in [remote-workers.md](docs/remote-workers.md).
  All three are made on a demo library of freely licensed Wikimedia Commons
  clips and photos ([credits](docs/demo/CREDITS.md)), never a personal one:
  `scripts/demo_library.py` builds it through the real import and workers,
  and `frontend/scripts/record-demo.mjs` films a real browser at 30 fps, in
  dark mode and the fonts a Windows browser shows the default theme in.
  [development.md](docs/development.md#recording-the-demo) has the steps.

### Removed

- `docs/app.svg` and `scripts/make_app_svg.py` (#88), which embedded
  thumbnails from the live library.

## [0.11.1] — 2026-10-28

### Fixed

- **`.env` applies under uv too.** Compose read it; uv never did, so under uv
  a library with `HIKIDASHI_AUTH=local` in `.env` started with no sign-in, and
  every other value there was ignored. `./hikidashi` now hands `.env` to each
  `uv run` as `UV_ENV_FILE` (unless one is set), and reads its own port, host
  and folders from it. A variable set in the shell still wins, as under
  Compose. The README covers moving from Docker to uv.

## [0.11.0] — 2026-10-27

### Changed

- **Two Docker images instead of one** (#86). `hikidashi-server` has the web
  UI, the API, yt-dlp, ffprobe and the embedder; `hikidashi-worker` has
  faster-whisper, ffmpeg and tesseract, with no web stack, embedder or front
  end; `--extra cuda` makes `hikidashi-worker-cuda`. Ollama stays the upstream
  `ollama/ollama`. On disk: one 2.37 GB image before; 1.57 GB server and
  2.15 GB worker after. `docker-compose.yml` runs `app` and a new
  `server-worker` (enrich and embed) from the server image, and `worker` (asr,
  ocr, tag and recipe) from the worker image. `docker-compose.worker.yml` runs
  the worker image.
- **The server never talks to Ollama** (#86). `HIKIDASHI_OLLAMA_URL` is set on
  the `worker` container only. Option A, the server's worker pointed at a
  desktop's Ollama, is gone from the docs: a remote worker does all it did,
  with the desktop's own model.
- **Dependencies are extras** (#86): `server` and `worker`, beside `cuda`. Under
  uv, install with `uv sync --extra server --extra worker`. A bare `uv sync`
  is enough for the CLI, import, exports and a tag/recipe worker.
  `./hikidashi start` says which extra is missing.
- **`./hikidashi` runs each stage where its tools are** (#86). Under Docker,
  transcribe, ocr, tag and recipe run in `worker`, the rest in `app`, and
  `pipeline` runs in three parts with the new `hikidashi pipeline --steps`.
  `./hikidashi logs server-worker` shows the new container's log.

### Added

- **A server without a worker container** (#86).
  `COMPOSE_FILE=docker-compose.yml:docker-compose.server-only.yml` runs only
  `app` and `server-worker`, and never builds the worker image: for a NAS
  whose heavy stages are all done on a desktop.
- **Stages run by hand on a desktop work for the server** (#86). With
  `HIKIDASHI_SERVER` set, `./hikidashi transcribe`, `ocr`, `tag`, `recipe` and
  `pipeline` claim their reels from the server, as `hikidashi worker --once`,
  under a name of their own. The server's commands refuse there, rather than
  act on an empty local database, and `status` names the server.

### Fixed

- **New reels are embedded while no worker can transcribe** (#86). Embed went
  ahead without the tagger, but still waited for ASR and OCR, so a server
  whose desktop was off left new reels out of semantic search. It now embeds
  from captions, and again as transcripts and summaries arrive.
- **The server-worker writes a log of its own** (#86). It and the worker
  container shared `/data/logs/worker.log`, each rotating it, so lines were
  lost at every rotation. `hikidashi worker --log FILE` names the file;
  `server-worker` writes `server-worker.log`, a stage run by hand for a server
  writes `worker-once.log`, and the Logs page shows each.
- **A Nominatim on the Docker host is reachable** as `host.docker.internal`
  from the `app` container on Linux too, as the README's Maps section says.
- **`./hikidashi restart` no longer starts a worker left out by a profile**,
  and `./hikidashi transcribe` on a server without a worker says where it runs.
- **A worker missing a stage's tools gives its reels back uncounted** (#86).
  Without faster-whisper, ASR failed every reel after three tries; without
  yt-dlp, enrich was mistaken for the server being away. Both are now reported
  as unable to run, like a missing tesseract, with the missing module or
  program named.
- **`./hikidashi status` no longer asks a localhost Ollama from the server
  container**, and leaves Whisper and the profile to the workers where they
  are not installed.

### Upgrading

- uv: `uv sync --extra server --extra worker` (plus `--extra cuda` on a GPU),
  as a plain `uv sync` now removes the server and ASR packages.
- Docker: `./hikidashi start --build`. Under Docker, `HIKIDASHI_LOCAL_STAGES`
  is now the `worker` container's alone. Remove an `all` or `enrich,embed`
  from `.env`: both list stages the worker image cannot run, which it reports
  as unable to run rather than doing them. For a server that leaves ASR and
  tagging to a desktop, use `none`. The server's own stages are
  `HIKIDASHI_SERVER_STAGES`. The old `hikidashi:local` image can be removed.

## [0.10.1] — 2026-10-26

### Changed

- **Recipe exports leave out hashtags** (#84). Recipe JSON-LD no longer has
  `keywords`. Mealie and Tandoor make a tag of every keyword, so importing 74
  recipes into Mealie brought 321 hashtag tags. This covers the Nextcloud ZIP,
  the `.json` exports and the JSON-LD on the recipe sheet.

### Fixed

- **The export hints say where in Mealie to upload** (#84). They used to say
  to "import" the files, which led to Mealie's Backups page. Backups only
  restores Mealie's own backups and fails on these files with "something went
  wrong". The hints, [docs/export.md](docs/export.md) and the README now say
  to use *Data Migrations*, as *Nextcloud Cookbook* or *Paprika*.

## [0.10.0] — 2026-10-26

### Added

- **Remote workers** (#35): a desktop with a GPU can transcribe, read
  on-screen text, tag and find recipes for a server on another machine, such
  as a NAS. `hikidashi worker --server http://nas.lan:8080` (or
  `HIKIDASHI_SERVER`) claims reels through the server's new `/api/worker/`
  routes, downloads their media into a cache (`HIKIDASHI_CACHE`), and sends
  the results back. The server checks and records them, and stays the only
  writer of the database. Remote workers use the same leases, readiness and
  attempts cap as a local worker. They show up in `./hikidashi status` and on
  the Pipeline page. Enrich and embed stay on the server. A desktop that
  shuts down mid-batch loses nothing, and a worker on a different release is
  refused. `docker-compose.worker.yml` runs just the worker.
- **Worker tokens on the Pipeline page** (#35). Admins make a token for each
  machine under "Workers on other machines". Each token is shown once, along
  with the command to run on that machine. The page lists who made each token
  and when it was last used, and revoking one refuses its worker from the
  next request. Remote workers stay off until there is a token, either made
  there or set as `HIKIDASHI_WORKER_TOKEN`.
- **[docs/remote-workers.md](docs/remote-workers.md)**: the four ways of
  splitting the server from the GPU that were considered, and why this one;
  how to set it up; what happens when either side goes away; and measurements.

### Fixed

- **A new transcript re-tags its reel** (#35). A reel tagged while its
  transcription had failed kept that tag once a transcript arrived. New OCR
  text already sent a reel back to the tagger and the recipe pass, and now a
  new transcript does too.
- **The README caught up with the code.** It no longer lists Proposals or
  `tag --propose`, both gone since #21. It says which stages the worker runs,
  that places are the server's own job, and where worker tokens are made.
  `HIKIDASHI_STOP_TIMEOUT` is in the configuration table, and docs/search.md
  in the documentation list.

## [0.9.2] — 2026-10-26

### Fixed

- **`./hikidashi restart` under Docker** (#81) recreates the server and the
  worker, so a change to `.env`, like `HIKIDASHI_AUTH=local`, applies.
  `docker compose restart` kept each container's old environment.
- **README** (#81): the first Docker start says `--docker`, since with uv also
  installed `./hikidashi` picks uv until the container exists; Docker imports
  come from `data/imports/`; a new **Updating** section; and the Pipeline and
  Logs pages are told apart.

## [0.9.1] — 2026-10-26

### Fixed

- **Docker builds** (#79): `./hikidashi start --build`, or a first start with
  no image, failed with `image "hikidashi:local": already exists`, as the
  server and the worker each built the image at once. The server's service
  builds it, and the worker runs it.

## [0.9.0] — 2026-10-26

Ready for an always-on server with no Ollama of its own (#76).

### Added

- **`HIKIDASHI_OLLAMA_URL`**: the Ollama a Docker install tags and reads
  recipes with, such as a GPU desktop's (`http://desktop.lan:11434`). Unset, it
  is the bundled `ollama` service, as before. A name of its own, so `.env`'s
  `OLLAMA_URL`, which is for uv, is never read inside a container. While that
  Ollama is off, tagging and recipes back off and catch up once it is back.
- **`HIKIDASHI_DATA`**: the host folder mounted at `/data`, `./data` by
  default, so the data can live on an Unraid pool
  (`/mnt/cache/appdata/hikidashi`) or a TrueNAS dataset without cloning the
  repo there. `./hikidashi import` and `export` take paths under it.
  [Where the data lives](docs/pipeline.md#where-the-data-lives) says where it
  belongs on each kind of host, and why never on a network or FUSE share.

### Changed

- **The worker embeds from captions while no worker can tag**: none running
  has the tagger, or every one that does reports it cannot run. New reels
  reach semantic search rather than wait for summaries that are not coming,
  and are embedded again from their summaries once tagged. `./hikidashi
  status` and the Pipeline page say so while it lasts. The recipe pass still
  waits for the tagger.

## [0.8.0] — 2026-10-25

### Added

- **An always-on worker** (#72): new reels are fetched, transcribed, read,
  tagged, checked for a recipe and embedded as they arrive, with no command.
  `./hikidashi start` runs it beside the server, and an import from the web or
  the command line wakes it at once. `./hikidashi worker [--stages ...]
  [--once] [--retry-failed]` runs one by hand. [docs/pipeline.md](docs/pipeline.md#the-worker)
  has the details.
  - **Leases**: a worker claims a few reels for one stage and renews the claim
    as it works. A worker killed mid-batch loses nothing; its reels go back to
    the pool when the claim expires, and a clean stop gives them back at once.
  - **Readiness**: a reel is offered to a stage once every stage before it is
    done with it, so a fresh import is tagged once, with its transcript and
    on-screen text, rather than before them and again after.
  - **An attempts cap**: a reel a stage cannot finish, by failing or by
    crashing the worker, is tried again after a minute and then two, and after
    three tries is set aside at that stage until the stage's version changes
    or someone retries it. A stage that cannot run at all, like the tagger
    with no Ollama, counts nothing against the reels and is tried again later.
  - **Idle**: an empty queue is checked less and less often, up to once a
    minute, and Whisper and the embedder are unloaded after five minutes with
    no work (`HIKIDASHI_UNLOAD_AFTER`).
- **A `worker` service in `docker-compose.yml`**, apart from the server so a
  crash mid-transcription never takes the web UI down. It may use 2 cores
  (`HIKIDASHI_WORKER_CPUS`) and runs at a lower priority than the server.
- **`HIKIDASHI_LOCAL_STAGES`**: which stages this host's worker runs, `all`,
  `none`, or a list such as `asr,ocr`.
- **A Pipeline page** for admins, from the sidebar: each worker, running,
  stopped, or not responding (gone quiet without saying it was stopping); per
  stage, the reels ready, waiting, being worked on and failed; the reels each
  stage gave up on, and why, with a button to retry them. The sidebar says how
  the workers are.
- **`./hikidashi status`** lists the workers and each stage's queue the same
  way, and `./hikidashi logs worker` and the logs page show the worker's log.

### Changed

- `./hikidashi start`, `stop` and `restart` start, stop and restart the worker
  with the server, under uv and Docker alike.
- CPU counts honour a container's CPU limit, for OCR's parallelism and the
  `cpu` profile's threads.

## [0.7.1] — 2026-10-25

### Added

- **Badges in the README**: version, build status (the tests workflow on
  `master`), project status (alpha), and the Python and Node versions it
  needs. The version badge is bumped with the rest (CONTRIBUTING.md).

## [0.7.0] — 2026-10-25

### Added

- **Exports** (#56): take your library elsewhere, from the web app or with
  `./hikidashi export <format> [--user NAME] OUT`.
  [docs/export.md](docs/export.md) has the details.
  - **Your account** has an export form: pick a format, what to export (the
    whole library, only reels with recipes, a collection or a tag), and for
    the Markdown vault, thumbnails and videos. Admins can export anyone's
    library.
  - **Export, above the grid**, exports whatever the filters show: a
    collection, a search, the Recipes view and its ingredient filters.
  - **Export on a reel's sheet** downloads its recipe, its Markdown note or
    its JSON, or copies the note to the clipboard.
  - **Recipes as schema.org JSON-LD**: name, time, servings, ingredients,
    steps, thumbnail, the reel as a video, its author, and a link back to the
    post. A folder of one file per recipe, or one file of them all.
  - **For Mealie and Tandoor**: a Nextcloud Cookbook ZIP, with photos.
    **For Paprika**: a `.paprikarecipes` file, which Mealie and Tandoor import
    too. Both were imported into Mealie and Tandoor, all 74 of a real
    library's recipes each time.
  - **The recipe sheet embeds its recipe's JSON-LD** in the page, for browser
    extensions and recipe managers to find.
  - **An Obsidian vault**: a Markdown note per reel, with YAML properties
    (shortcode, author, tag, hashtags, collections, saved_at, permalink,
    places), its summary, recipe as a checklist, caption, transcript and
    on-screen text, linked to notes for each tag, author and collection.
    `--thumbs` and `--with-media` copy thumbnails and videos in, or `--link`
    links them. Running it again updates the vault in place and keeps what you
    wrote below each note's marker line.
  - **JSON and CSV** of every reel, for scripts and spreadsheets.

## [0.6.1] — 2026-10-24

### Added

- **Browser tests** (#39): Playwright in `frontend/e2e/` drives the real
  server and the built front end on Chromium, in CI on every pull request.
  They cover claiming a new library and inviting someone, signing in and out,
  one person's library staying invisible to another (grid, sidebar, search,
  deep links and media), and importing an export through the upload button.
  Traces, screenshots and video are kept when one fails.
- **A seeded library to test against**: `python3 -m tests.seed DIR` builds two
  people, a dozen reels, tags, collections, a recipe and a failed reel,
  without yt-dlp, Whisper, Ollama or an embedder; `python3 -m tests.serve`
  runs the server on a throwaway copy of it.
- **Property tests** with Hypothesis and fast-check: search queries are valid
  FTS5 for any text, every permalink shape gives its shortcode back, the parser
  never raises on any JSON, and filter URLs round-trip.
- **Coverage reports** for Python and the frontend in CI, report only.
- **ruff and mypy**: the Python is formatted with `ruff format` at 100
  columns, linted with ruff, and type-checked with `mypy --strict`; a CI job
  fails on any finding. `.git-blame-ignore-revs` lets blame look past the
  reformat.
- [docs/testing.md](docs/testing.md): every check, how to run it, and what CI
  runs when.

### Fixed

- `hikidashi user invite` crashed when no enabled admin existed; it now says
  to claim the library first.
- OCR no longer tries to read a missing thumbnail for a reel with neither
  video nor image, and removing a reel that another request removed a moment
  earlier is a 404 rather than a server error.

## [0.6.0] — 2026-10-24

### Added

- **Cook mode** (#48): **Cook** on a recipe's sheet opens it full screen, one
  step at a time in large type, with big **Back** and **Next** buttons (or
  swipe, or the arrow keys). The screen stays on while it is open, where the
  browser supports the Wake Lock API. The reel plays muted on a loop beside
  the step: all of it for now, since looping just the step's part needs the
  transcript's timings (#44). It is in the URL as `?cook=<step>`, so Back
  leaves it and a reload keeps your place.
  - **Timers**: a duration in a step ("simmer for 20 minutes", "6-8
    minutes", "an hour and a half") is a button that starts a timer. Timers
    keep running from step to step, and through a reload. When one goes off it
    beeps and vibrates until you stop it or add a minute.
  - **Ingredients** open in a drawer that keeps the recipe tab's ticks, and
    scale with **Serves** (or ×½ to ×4 when the recipe doesn't say how many it
    serves). Amounts with no number to scale, like "to taste", are left as
    written.
- **Shopping list** (#48): put a recipe on it from its sheet, pick several in
  Recipes with **Select**, or add every recipe the filters show (a
  collection, a search) at once. `/shopping`, linked from the sidebar, merges
  their ingredients by name, adds up amounts whose units agree (grams with
  kilograms, too) and groups them by aisle. Salt, pepper, cooking oil and
  water are folded into **Pantry staples**. Ticks are saved as you go, each
  person has their own list, and **Share** or **Copy as text** sends what is
  left to buy. A recipe added later unticks anything it needs more of.
- `GET /api/items/{shortcode}` returns each ingredient's parsed `amount` and
  each step's `timers`, and whether the recipe is `listed` on your list.

## [0.5.2] — 2026-10-24

### Fixed

- **Search no longer gets worse for some queries to get better for others**
  (#20). Measured against search as it was before #20, on the same library
  and 73 graded queries, blended nDCG@10 goes from 0.86 to 0.92, and no kind
  of query scores lower. 0.2.0 had made descriptions and place queries worse
  (paraphrase 0.86 to 0.84, place 0.88 to 0.86).
- **Queries with no good answer stay quiet again**: 5 of 8 return three reels
  or fewer, as before #20, against 3 of 8 in 0.2.0. A sentence matched any one
  of its words, so "grooming a long haired cat" found 24 reels for "long". A
  reel must now have every word that says what you are after; before
  `./hikidashi embed` has run, any two of them will do.
- **Half-remembered voiceovers come first**: every one in the eval set now
  ranks its reel first (voiceover nDCG@10 1.00, from 0.96). A reel's closest
  stretch of transcript competes with its summary for one place in the
  ranking, instead of counting for a second time, which had let reels with a
  voiceover beat closer matches without one.

### Added

- **`python -m app.search.eval --against <git ref>`** scores search as it was
  at that ref beside today's, on the same library and query set, and lists
  the queries whose score moved. A baseline recorded two days earlier no
  longer reproduced once reels had been re-summarised.
  [docs/search.md](docs/search.md) is re-measured this way.

## [0.5.1] — 2026-10-24

### Fixed

- **The Pipeline counts in the sidebar** line up with the other counts. They ran
  to the sidebar's edge, where a scrollbar could cut them off.

## [0.5.0] — 2026-10-24

### Added

- **Places**, under Library in the sidebar: every reel whose caption names a
  place (a 📍 pin, a street address, a `Where:` line), as a grid or on a map.
  Search, tags, collections and Videos or Images narrow it like any view.
  **Restaurants** and **Travel** under it narrow the grid and the map to the
  reels tagged Food or Travel.
- **A map of your saved places** (#46). Clustered pins beside a list of their
  reels, which narrows to what is in view as you pan. Hovering a reel lights
  its pins, and hovering a pin lights its reel. A pin's popup shows the
  thumbnail and opens the reel. Where the map looks is in the URL, so a map
  view can be bookmarked and Back works. The count says what the map leaves
  out ("26 of 38 on the map"). At phone width the map fills the screen and
  the list pulls up from the bottom.
- **Near me, or a city.** Centre the map on where you are, or on a city you
  type, and sort the list by distance.
- **A small map in the detail sheet** for a reel with a place on the map.
- **`./hikidashi places`**, a pipeline stage. It reads places from captions,
  free and offline, then looks them up on a Nominatim server, a self-hosted
  one or OpenStreetMap's public one. Each place is looked up once. Places the
  geocoder is unsure of stay off the map, and a second look inside the region
  a reel's other places agree on fixes ones that land on the wrong continent.
  `hikidashi status` reports on it.
- **The server keeps the map up to date.** With a geocoder set, it looks up
  the places new reels name as they arrive: straight after an import through
  the web, and every five minutes otherwise (`HIKIDASHI_LOCATE_EVERY`). It and
  `./hikidashi places` take turns, and it backs off when the geocoder fails.
- `HIKIDASHI_GEOCODER`, `HIKIDASHI_GEOCODER_DELAY`, `HIKIDASHI_TILES` and
  `HIKIDASHI_TILES_ATTRIBUTION` and `HIKIDASHI_LOCATE_EVERY`. Nothing is
  looked up until a geocoder is set.

### Changed

- `hikidashi pipeline` ends with the places stage. Without a geocoder it only
  reads captions.
- In Docker mode, `./hikidashi` passes `HIKIDASHI_GEOCODER` and
  `HIKIDASHI_GEOCODER_DELAY` from your shell into the container, so
  `HIKIDASHI_GEOCODER=public ./hikidashi places` works for a single run.

## [0.4.0] — 2026-10-23

### Added

- **Themes**, each in light and dark with its own fonts and motion:
  - **Cyberpunk**: neon on black or black on hazard yellow, cut corners,
    glitching buttons, scan lines on cards, and scanlines over the page.
  - **Retrowave**: a sunset wordmark, a neon glow that pulses on hover, and
    a grid rolling toward you beneath the library.
  - **Retrofuture**: the fifties' space age in cream, atomic orange and
    turquoise, or a starry midnight. Capsule buttons that flare a starburst,
    an atom orbiting the chosen row, and print-style offset shadows.
- **Pixel art**, a switch that redraws any theme in pixels: pixel fonts,
  pixel icons, square corners, block outlines, and motion in whole steps.
  Pictures are left as they are.
- **Light, dark or system**, chosen in the browser rather than only
  following the operating system.

  Theme fonts are served with the app, never fetched from elsewhere.

### Changed

- **The sidebar collapses to a rail of icons** on a desktop with a mouse,
  one per section and lit while its filter is on. It opens over the grid
  when hovered, with each icon beside its section's heading, and the pin
  keeps it open.
- **The account button is now a menu**, holding the theme (each marks its
  own pick in its own style), the light/dark switch, pixel art, and the
  link to your account.
- **An Instagram look in place of violet on navy.** Light mode is white with
  grey rules and dark mode is Instagram's #0c1014. Buttons and links use
  Instagram's blue, and buttons and inputs are filled and borderless.
- **Sans type throughout the interface.** Section labels, counts, dates,
  badges and the details page's facts drop the uppercase monospace.
  Usernames are bold and chips are rounded. Shortcodes, transcripts and the
  log stay monospace.
- **The brand gradient as a story ring.** It rings a card on hover and the
  avatar on the menu button. Carousel arrows are white discs, as on
  Instagram.
- **A neutral app icon**, and the README animation redrawn in the new
  palette.

## [0.3.1] — 2026-10-23

### Fixed

- **The close button on the details page** no longer overlaps the text
  column's scrollbar.

## [0.3.0] — 2026-10-23

### Added

- **Videos and Images under Library** in the sidebar. Each narrows the
  current view, alongside a collection, tag or recipe filters, and shows as a
  pill above the grid; **All saves** clears it. Reels count as videos, photo
  posts and carousels as images. The API takes it as `media=video|image` on
  `/api/items`, and the URL as `?media=`.

### Changed

- **A quieter sidebar.** The notes beside Collections ("Instagram's"), Tags
  ("ours") and Pipeline ("logs →") are gone. Pipeline still links to the log
  for admins.

## [0.2.1] — 2026-10-23

### Added

- **[CONTRIBUTING.md](CONTRIBUTING.md)** sets out how changes land: a new
  branch off a freshly pulled `master`, one PR per issue, and a final commit
  that updates the changelog and README and bumps the version.
  `CLAUDE.md` points Claude Code at the same rules.

## [0.2.0] — 2026-10-23

Phases 01 and 02, plus the first pass at tags and a detail view. The library is
now browsable with real thumbnails, playable video, tag and hashtag filters, and
Google Maps links for places mentioned in captions. It is also searchable by
what is said in each reel.

### Changed

#### Search quality, measured (#20)

- **`python -m app.search.eval`** scores search on 69 graded queries
  (`tests/search_eval.json`): recall@10, MRR and nDCG@10 per kind of query,
  negatives, latency. [docs/search.md](docs/search.md) records the baseline,
  the results and what was rejected. Blended nDCG@10 is 0.91, from 0.88.
- **Sentences match on their words.** When no reel has every word, search
  falls back to the distinctive ones, minus stopwords and words in over 10%
  of the library, at a quarter weight. Keyword-only nDCG goes from 0.43 to
  0.83.
- **What was said is embedded.** Transcripts and on-screen text are embedded
  in 60-word chunks (`item_chunk_vec`), for "that video where he says…"
  searches (0.81 to 0.96). `EMBED_V` is 2: run `./hikidashi embed` once.
- **Japanese and other unspaced scripts match inside words**, and a creator
  typed with spaces ("tokyo supercars") finds the handle.
- **Each search hit says why it matched** (`match` in `/api/items`), shown as
  a badge on cards in dev mode or with `localStorage.explain = "1"`.
- `app/search.py` is now the package `app/search/`.
- CI runs the Python tests with sqlite-vec installed, and the front-end
  checks.

#### One tag per reel (#21)

- **Each reel has one tag**, its primary category, instead of up to five. It
  is yours if you set one, else the model's, else the one its hashtags point
  at most. The sidebar lists only categories. `item_tag` holds one row per
  source, and triggers keep the answer in the new `item.primary_tag`.
- **The model tagger answers one category with a confidence** (high, medium,
  low). `TAG_V` is 2, so the next `./hikidashi tag --llm` re-tags every reel.
- **Cleaned hashtags** in `item.clean_hashtags`: normalised, engagement bait
  dropped, each once. Cards show the tag and three of them, the detail sheet
  all of them; each filters the grid. `item.hashtags` is kept as posted.
- **Search covers the tag, cleaned hashtags and keywords.** `item_fts` gains
  three columns and is rebuilt on first start.
- **Proposals are gone**: the `/proposals` page, its API, and `--propose` /
  `--accept`. The 29 accepted proposals (`texas`, `birria`, `corolla`…) became
  keywords on the reels they tagged, and still find them by search.
- **Pets and Music are no longer categories.** [docs/tags.md](docs/tags.md)
  records the decision for each of the 41 tags.
- **Choosing a reel's tag** in the detail sheet replaces adding several:
  `PUT` and `DELETE /api/items/<shortcode>/tag`. The sheet notes what the model
  and the hashtags said when they disagree.
- **`./hikidashi tag --eval`** scores the model and the hashtags against the
  tags you set by hand (#6), and `--db` scores an older database's answers on
  the same labels.
- The Recipes candidate filter reads the Food tag (#13).

The migration runs once, on first start: multi-tag items collapse to one tag
per source, preferring the category the hashtags agree with. Back up
`data/hikidashi.sqlite3` first if you want to compare the old tagger with
`--eval --db` later.

### Added

#### Paging through carousels

- **A post of several photos shows every one**, not just the first. The
  detail sheet pages through them with arrow buttons, the left and right
  keys, or a swipe at phone width, and counts where you are ("2 / 13").
- Fetching saves each slide's image (`item_slide`, `slides/` under the media
  folder). Carousels fetched before this have only their first: run
  `./hikidashi enrich --media-only` once to fill the rest in. A carousel with
  a video among its slides still shows its first slide only.

#### A library each (#12)

- **Accounts, off by default.** `HIKIDASHI_AUTH=none` keeps today's single
  library with no sign-in. `local` signs people in with a username and an
  argon2id password; `proxy` takes the user from a trusted proxy's header
  (Authelia, Tailscale, Cloudflare Access). No open sign-up: an admin makes
  one-time invite links. The README's *Sharing it with others* walks through
  it.
- **Each person sees only their own saves.** `item` stays a shared cache, so a
  reel two people saved is fetched, transcribed and tagged once; save times,
  collections, imports, the tag you chose and recipe corrections are yours.
  Someone else's reel is a 404 on every route, media included, and search
  filters to your library inside each ranking list.
- **Remove a reel from your library**, from its sheet. The last person to
  remove it deletes it and its media from the server.
- **Retry a failed fetch** from the sheet, for anyone who has the reel saved.
- **Your account page**: Instagram @handle (self-declared), password, sign out
  everywhere. Admins list, disable and promote accounts, and make invites.
- `./hikidashi user add|passwd|rename|list|disable|enable|invite`, and
  `./hikidashi import --user NAME`.
- Every request that changes something must come from the app's own pages
  (Sec-Fetch-Site or Origin), in every mode.
- The migration runs once on start and gives the existing library to user 1,
  `local`. The tag you chose moves out of `item_tag` into `user_item`, so
  `item.primary_tag` is now the shared answer: the model's, else the
  hashtags'.
- The Docker health check and `hikidashi status` probe `/api/auth`, which needs
  no session, instead of `/api/stats`, which is now per library.

#### Japanese (#33)

- **The UI speaks Japanese.** Every string is a message in
  `frontend/messages/en.json` and `ja.json`, compiled by Paraglide JS to typed
  functions. The language follows the browser, or the English / 日本語
  switcher at the foot of the sidebar, which this browser remembers. It is
  never in the URL. `<html lang>` follows it, so Japanese gets Japanese fonts
  and line breaking.
- **Dates, numbers and counts are formatted for the language**: `2026年10月11日`,
  `材料5点`, `9.9 MB`. English counts use plural messages instead of
  appending "s".
- **Our vocabularies are translated for display**: tag categories (`Food` is
  料理・グルメ), ingredient groups and pipeline states. The database, the API
  and URLs keep the English names; typing 旅行 into a reel's tag box chooses
  Travel. Instagram's text and the model's summaries are shown as they are.
- **A test fails on untranslated text**: a literal in a `.svelte` file, a key
  missing from a language, a translation whose inputs differ, or a message
  nothing uses. [docs/development.md](docs/development.md#translations)
  covers adding a language.
- **Japanese search**: recipe titles and ingredients match as substrings, like
  captions. Four Japanese queries joined the search eval; the trigram tokenizer
  was measured and rejected ([docs/search.md](docs/search.md#japanese-33)).
- Building the web UI needs **Node 20**, up from 18.13, for Paraglide;
  `./hikidashi start` says so instead of failing in the compiler. It also
  rebuilds when a translation changes.

#### Editing tags (#7, #27)

- **Tag a reel from its detail sheet.** Type into the `+ tag` box (it suggests
  existing tags) to add one of yours; the cross on a tag of yours takes it off.
  `POST /api/items/<shortcode>/tags` and `DELETE …/tags/<name>` underneath.
  Yours are stored as `source='user'`, so no re-tag removes them, and adding a
  tag a pass already wrote confirms it as yours. Typing `food` reuses `Food`.
- **Dismiss proposals** on `/proposals`, alongside Approve
  (`POST /api/proposals/dismiss`). A dismissed tag is kept as
  `kind='dismissed'`, so `--propose` does not suggest it again.
- **Chips on cards filter the grid.** A tag or collection chip under a card is
  now a link to the library filtered by it, keeping the search and sort.
  Clicking the active one clears it, the active one is highlighted on every
  card, and middle-click opens the filtered view in a new tab.

#### Recipes (#13)

- **`hikidashi recipe`** finds the reels that are recipes and reads each one
  out of its caption, transcript and on-screen text: title, servings, time,
  ingredients with their amounts as written, and numbered steps. A new
  pipeline stage between `tag --llm` and `embed`, on the tagger's model.
- **Cheapest first.** A candidate filter with no model (a Food tag, a
  quantity with a unit, or the word for ingredients or recipe), then a capped
  yes/no check, then extraction only for a yes. Not a candidate, or a no, is
  stamped and not asked again.
- **No invented ingredients.** The model copies each ingredient's words from
  the text, and any that no source contains is dropped and recorded in
  `recipe.dropped`. `source_fields` comes from where the words were found. A
  recipe that points elsewhere ("link in bio") is marked incomplete rather
  than filled in.
- **New `recipe` and `recipe_ingredient` tables** keyed by shortcode and
  source, `model` or `user`, with a `recipe_now` view that prefers yours.
  `recipe_v` and `recipe_profile` on `item`, as for every other stage, so
  reels found not to be recipes are recorded too. Bumping `RECIPE_V` re-runs
  only this stage, and never touches a correction.
- **Recipe titles and ingredients are searchable** (`recipe_fts`), ranked after
  reels that say the words themselves.
- **A Recipes view** in the sidebar: only recipes, with cards led by the dish,
  its time and how many ingredients. It filters by time, by ingredient and by
  hand-written ingredient groups (`app/recipes/groups.py`: Dairy, Eggs,
  Gluten, Meat, Poultry, Fish & seafood, Nuts), and the filters stack. All of
  it lives in the URL (`/?view=recipes&without=Dairy&time=30`).
- **A Recipe tab on the detail sheet**, beside the video: an ingredient
  checklist and numbered steps. You can edit it, go back to the model's version,
  or mark it as not a recipe. Opened from Recipes it shows first; from the
  library, Details does. Cards in the library carry a small recipe badge.
- `--label` and `--eval`: label reels by hand into `data/recipe_labels.json`
  (never committed), then report precision, recall and any stored ingredient
  the text does not name.
- `PUT` and `DELETE /api/items/{shortcode}/recipe`, `GET /api/recipes/facets`,
  and `recipes`, `has`, `without` and `max_time` on `/api/items`.
- `hikidashi status` reports the recipe pass.

#### Svelte front end (#18)

- **The web UI is rewritten in Svelte 5 + SvelteKit** (`frontend/`), built
  with `adapter-static` into `app/web`, which FastAPI serves. There is still
  no Node process at runtime: the Docker image builds the front end in a
  `node:22-slim` stage and copies only the static output across. `npm run dev`
  runs Vite beside `uvicorn`, proxying `/api` and `/media`.
- **Real routes.** `/item/<shortcode>` opens a reel over the grid and can be
  shared; search, filters and sort live in the query string, so a filtered
  view can be bookmarked and Back walks through them. `/#<shortcode>` links
  from before no longer open anything.
- **Infinite scroll** in place of the Load more button. Opening and closing a
  reel keeps the grid and your place in it.
- **Motion.** The picture grows out of its card into the sheet and shrinks
  back on close; the grid reflows with `animate:flip` when filters change; new
  cards fade up in a stagger; thumbnails show a shimmering placeholder in their
  own tint until they load; at phone width the sheet slides up from the bottom
  and swipes down to close. Hovering a card plays its video, muted, on devices
  with a mouse. Under `prefers-reduced-motion` all of it becomes a short fade
  or nothing.
- **The detail sheet is a native modal `<dialog>`.** Focus moves into it,
  Tab wraps inside it, Esc closes it even from inside the video's controls,
  and focus goes back to the card it was opened from.
- **At phone width** the sidebar becomes a drawer behind a menu button.
- **Caching by content hash.** Built chunks under `/_app/immutable/` are cached
  for a year; `index.html` and other files are revalidated on every load. This
  replaces the mtime-stamped asset URLs. Unknown paths under `/api` and
  `/media`, and missing asset files, are 404s, never the app.
- `npm run check` (svelte-check) and `npm test` (Vitest) for the front end.
- Under uv, **`./hikidashi start` builds the web UI** when `app/web` is missing
  or older than `frontend/`.

#### One CLI (#24)

- **`hikidashi`**, a command in the uv venv and the Docker image alike
  (`[project.scripts]`). `import`, `enrich`, `transcribe`, `ocr`, `tag` and
  `embed` hand their flags unchanged to the `python -m app.*` module they stand
  for, help included, and return its exit code.
- **`hikidashi pipeline [<zip>]`** runs import (when given), `enrich --media`,
  `transcribe`, `ocr`, `tag --llm` and `embed` in order, each in its own
  process, and stops at the first failure with that stage's exit code. OCR is
  skipped with a note where `tesseract` is missing. A lock on
  `data/run/pipeline.lock` keeps a second pipeline from starting, and is
  released however the first one ends.
- **`hikidashi status`**: server up or down, pipeline running or idle, counts
  per stage, and whether the Whisper, embedding and Ollama models for the
  current profile are ready.
- **`./hikidashi`**, a POSIX sh wrapper at the repo root that adds `start`,
  `stop`, `restart`, `status` and `logs [-f]`. It picks Docker Compose when the
  `hikidashi` container exists or uv is missing, uv otherwise; `--docker`,
  `--uv` or `HIKIDASHI_MODE` overrides. Under uv the server runs in the
  background in its own process group, with its pid in `data/run/server.pid`;
  `stop` sends SIGTERM, then SIGKILL after 10 s, and a stale pidfile is
  cleared. Under Docker it drives `docker compose`, and an export path under
  `./data` is rewritten to `/data` for the container.
- `HIKIDASHI_HOST` and `HIKIDASHI_PORT` (default `0.0.0.0:8080`) apply to both;
  `docker-compose.yml` now publishes the port from them.

#### Sidebar and logs

- **Collections and Tags fold**, and remember being folded. Collections now
  come first.
- **A server log page** at `/logs`, opened by clicking Pipeline in the sidebar.
  The server now also writes its log to `HIKIDASHI_LOGS/server.log` (default
  `data/logs`, rotated at 1 MB, three backups), and `GET /api/logs` reads back
  the end of it. The page follows new lines every few seconds and can show only
  warnings and errors. Thumbnail requests and its own polling stay out of the
  file.

#### OCR (phase 04)

- **The OCR worker** (`python -m app.ocr`). Reads the text burned into photo
  posts and into reels whose transcription found no speech: 187 items that
  otherwise have nothing but a caption. Photos are read from the thumbnail,
  reels from the cover plus five frames at 10–90% of their length. ffmpeg pipes
  each frame into `tesseract`, one single-threaded process per core, up to 8.
  Writes `ocr_text` (searchable through the existing FTS triggers) and stamps
  `ocr_v`. One transaction per item.
- **Filtered, not dumped.** Words under 60% confidence go, and so do lines with
  no word of three or more letters. Lines repeated across frames are kept once,
  the fullest reading winning.
- **Feeds the tagger.** On-screen text is sent to the model as its own section.
  It also counts toward the three-word minimum, so a thin caption over a
  wordy image is tagged. New text clears `tag_v`, so only items OCR added to
  are re-tagged. The tagger waits for OCR as it already waits for transcripts,
  and does not wait for an item whose read failed.
- **A video downloaded later** (`app.enrich --media-only`) clears `ocr_v`, so
  an item first read from its thumbnail is read again from the video.
- **Failures.** A failed read records `fail_stage = 'ocr'` and is retried. A
  missing `tesseract` or `ffmpeg` stops the run without marking anything.
- **"On screen"** in the detail panel.
- **Measured on the library:** 115 of 187 items with text, 558 lines, in 30
  seconds on CPU. Re-tagging them took 1.9 minutes with `qwen2.5:7b`, no
  failures. Items with no tag from any source went from 27 to 25. Categories
  changed on 20 items: by hand count, 3 right ones added and 1 wrong one, about
  6 wrong ones removed and about 3 arguably right ones lost. Measuring this
  against a calibration set waits on #6.

#### Semantic search (#4)

- **The embedding worker** (`python -m app.embed`). Embeds each item's summary
  and keywords, or its caption until the tagger reaches it, with
  `embeddinggemma-300m` on ONNX Runtime through fastembed (no torch). Vectors
  go into a plain `item_vec` table as float32 blobs. Stamps `embed_v`, one
  transaction per batch. The library embeds in ~25 s on CPU.
- **Re-embeds only what changed.** Each vector stores a digest of the text it
  came from and the model that made it, so a new summary or a changed caption
  re-embeds that one item.
- **Search blends keywords and vectors** (`app/search.py`). Exact-phrase
  matches come first. Everything else is ranked by reciprocal rank fusion of
  bm25 and cosine ranks. Vector hits need a similarity of at least 0.27, and
  at most 24 are kept.
- **sqlite-vec** provides `vec_distance_cosine` for a brute-force scan, under
  a millisecond at this size. The schema stays applicable with no extension
  loaded.
- **A "Best match" sort**, chosen automatically when you start a search and
  dropped when you clear it. `GET /api/items` accepts `sort=relevance`.
- **Degrades to keyword search.** The API never downloads the model. Until
  the worker has run, or while the model cannot load, search is FTS5 only.
- **Why not bge-small-en-v1.5**, which the issue proposed: over eight
  paraphrased queries against the library it found 0.53 of the right reels in
  its top 10. embeddinggemma found 0.76. bge-large and mxbai-embed-large found
  0.54–0.58. Embedding captions as well as summaries made every model worse.

#### Model tagging (phase 03)

- **The LLM tagger** (`python -m app.tagging --llm`). Sends each reel's
  caption, hashtags and transcript to a local model in Ollama and writes
  `source = 'model'` tags, a one-sentence `summary` (searchable through the
  existing FTS triggers) and free-text `keywords`. Stamps `tag_v` and
  `tag_profile`. One transaction per reel, so a run resumes where it stopped.
- **Strict JSON, held to the taxonomy.** The request carries a JSON schema
  whose categories are an enum of the taxonomy, so an off-taxonomy category
  cannot be generated; the reply is validated again on arrival.
- **Built to abstain.** Each category is sent with a one-line definition, the
  schema offers `Other` (dropped on parsing) so the model is not forced to pick,
  and at most two categories are kept. A caption under three real words, with
  no hashtags or transcript, is stamped without asking the model, which
  otherwise invents a subject for it. Summaries and keywords are asked for in
  English whatever the caption's language.
- **Output capped, input not.** `num_predict` is 200 tokens; `num_ctx` is raised
  to 8192 so Ollama does not silently drop the start of a long transcript.
- **Keywords feed the proposals queue.** `--propose` now counts model keywords
  alongside hashtags, once per item, and `--accept` attaches a keyword match as
  a model tag.
- **Waits for transcripts.** A reel with a video but no transcript yet is left
  for the next run, so it is read once with everything. One whose transcription
  failed does not wait.
- **Failures.** A malformed or truncated reply records `fail_stage = 'tag'` and
  is retried next run. An unreachable server or a missing model stops the run
  without marking anything. The model is pulled on first use.
- **`tag_model` per hardware profile**: `qwen2.5:3b` on `cpu`, `qwen2.5:7b` on
  `cuda`. `OLLAMA_URL` (default `http://localhost:11434`) says where to find it.
- **Keywords and `tag_profile`** in the detail response and panel.
- **An approvals page** at `/proposals`, linked from the sidebar's "N proposed"
  note. One checkbox per proposal with the number of reels it would tag, and
  Approve / Approve all buttons. Backed by `GET /api/proposals` and
  `POST /api/proposals/accept`, which approves all the names given or none.
- **Measured on the library:** 411 reels in 5.6 minutes on an RTX 3080 with
  `qwen2.5:7b`, no failures. Items with no tag from any source went from 171 to
  29. A 40-tag spot check put precision near 90%; the calibration set (#6) is
  what will actually measure it.

#### Transcription (phase 02)

- **The transcription worker** (`python -m app.asr`). Transcribes every reel
  with a video on disk and no transcript at the current `asr_v`, and stamps
  `asr_v` and `asr_profile`. One transaction per item, so a run survives Ctrl-C
  or `kill -9` without losing a finished transcript. Ctrl-C exits at once
  instead of waiting for the reels in flight.
- **Search over spoken words.** The existing FTS5 triggers index `transcript`,
  so a phrase only spoken in a reel finds that reel with no change to search.
- **VAD on.** faster-whisper's voice-activity filter skips music-only reels
  instead of writing invented lyrics into them. No speech and no audio track
  both leave `transcript` NULL and stamp the row, so neither is queued again.
- **ASR errors are not post failures.** They record `fail_stage = 'asr'` with a
  reason and are retried on the next run, but never set `state = 'failed'`.
  That state is reserved for posts that cannot be reached at all.
- **Hardware profiles** (`app/profiles.py`), the resolver for
  `HIKIDASHI_PROFILE`. `cpu` is `distil-small.en` at `int8` with four workers
  sharing the cores, and `cuda` is `distil-large-v3` at `float16`. `auto` picks
  `cuda` when CTranslate2 can see a GPU. An explicit `cuda` without one is an
  error rather than a quiet fallback.
- **A `cuda` extra** with the cuBLAS and cuDNN wheels. They are preloaded at
  startup, so no `LD_LIBRARY_PATH` change is needed. The model runs once when
  it loads, so a missing library stops the run before it starts instead of
  failing every item. In Docker it comes through the `EXTRAS` build arg.
- **`HIKIDASHI_MODELS`** (default `data/models`) holds the downloaded weights,
  on the data volume in Docker so they survive a rebuild.
- **Transcript count** in `/api/stats` and the sidebar, and `asr_profile` in
  the detail response.

#### Enrichment (phase 01)

- **The enrichment worker** (`python -m app.enrich`). Fetches metadata and
  thumbnails for every item not yet fetched at the current `fetch_v`, with
  `--media` to also download the original mp4s untouched. Bounded concurrency,
  a polite delay before each request, and one transaction per item so a run can
  be killed and resumed without losing or repeating work.
- **`--media-only`** downloads mp4s for items that are already fetched.
  Necessary because such items are no longer behind on `fetch_v`, so the
  ordinary pending queue cannot see them. It selects on `media_path IS NULL`
  and touches no other column.
- **Failure classification.** Deleted, private and restricted posts are
  permanent and stamped so they stop coming round; timeouts and rate limits are
  transient and retried up to three times. `--retry-failed` reaches past the
  permanent stamp. Reasons are mapped to short readable labels
  (`restricted audience`, `login required`, `deleted`, `private account`)
  rather than storing a fragment of a long error message.
- **Fetched metadata columns** — `duration`, `width`, `height`, `like_count`,
  `comment_count` — plus `fetch_meta` holding the yt-dlp response verbatim,
  alongside the untouched `raw_meta` from the export.
- **Duration via `ffprobe`.** Instagram's metadata carries no duration field at
  all, so it is read from the downloaded file.
- **Schema migration helper.** `db.migrate()` adds columns gained after a
  release, because `CREATE TABLE IF NOT EXISTS` will not alter a table that
  already holds rows.

#### The library UI

- **Detail panel.** Clicking a reel opens its metadata: caption, tags,
  collections, hashtags, places, engagement counts, dimensions, pipeline state,
  and links to both the post and the creator's profile. Hash-routed, so
  `/#<shortcode>` deep-links and the back button closes it.
- **Video player**, autoplaying muted and looped — the only way a browser will
  start playback without a click — paused and unloaded when the panel closes so
  it does not keep streaming behind the grid.
- **Everything in the panel is a filter.** Tags, collections and hashtags all
  navigate to more of the same. They are separate axes: choosing one clears the
  others rather than silently intersecting into an empty grid, and the active
  filter is always shown next to the result count with a way to clear it.
- **Media serving.** `/media` is mounted with range-request support, so video
  can be scrubbed in place.
- **Real thumbnails** in the grid, with duration badges and an `unavailable`
  badge on items that could not be fetched.

#### Tagging

- **Tags, kept separate from collections.** Collections are Instagram's; tags
  are ours. Separate tables, separate filters, labelled as such in the sidebar.
- **A shallow fixed taxonomy** with hashtag mapping rules
  (`python -m app.tagging`). Deterministic and offline — no model involved.
- **Proposals.** Frequent hashtags the taxonomy does not cover become
  `kind='proposed'` tags, attached to nothing until accepted with `--accept`.
  Engagement bait is filtered out so the queue stays readable.
- **Tag provenance** via `item_tag.source` (`hashtag` / `model` / `user`), so
  re-running one source replaces only its own rows and hand-written tags
  survive every automatic pass.

#### Places

- **Addresses and venues extracted from captions** and linked to Google Maps,
  both inline in the caption text and as a list underneath. Calibrated against
  the real library: of 420 captions, 59 use a 📍 pin, 22 contain a street
  address and 2 use a `Where:` label.

#### Docs

- **Animated SVG diagrams** of the running app and the item pipeline, embedded
  in the README. `scripts/make_app_svg.py` regenerates the app diagram from the
  live library, with `--placeholders` to avoid embedding real thumbnails.
- **App icon and a centered README header.** The favicon and header mark is a
  vault with Instagram's Reels glyph inside the doorway. The README opens with
  the logo, name, tagline and section links centered above the overview.

### Changed

- The project is now built (uv_build, installed editable) so that the
  `hikidashi` command exists. The Dockerfile installs dependencies, then the
  project. Rebuild the image once: `./hikidashi start --build`.
- README command blocks use `./hikidashi`; the raw `python -m` forms are in a
  short reference table.
- **The hashtag pass stamps `hashtag_v`, not `tag_v`.** `tag_v` now belongs to
  the model tagger and pairs with `tag_profile`. Existing stamps move across in
  the migration that adds the column, once.
- **`propose_frequent_hashtags` is now `propose_frequent`**, since it also
  counts model keywords.
- **The Ollama client is `app/ollama.py`**, shared by the tagger and the
  recipe pass. `app.tagging.llm` still exports `Ollama`, `OllamaError` and
  `TagError` (now `ollama.ReplyError`).
- New text from OCR clears `recipe_v` as well as `tag_v`, so both passes read
  the item again with it.

### Fixed

- **Accepted proposals were lost on re-tag.** `--accept` attached them as
  hashtag tags, but the hashtag pass only knew the built-in taxonomy, so
  `--force` deleted them. Both passes now attach accepted tags themselves.
- **A user tag could be deleted by a pass.** An item holds each tag once, so
  adding a tag a pass had already written was ignored, and the pass's next
  re-run removed it. Adding it now takes the row over as `source = 'user'`.
- **Enrichment failed inside the container.** The image carried only the
  Python dependencies, so `python -m app.enrich` worked from a local checkout
  but not under Docker Compose — the actual deployment target. `ffmpeg` and
  `tesseract-ocr` now come from apt; `yt-dlp` is pinned in `pyproject.toml` so
  it moves with the lockfile rather than with the distro's lagging package.
- **Photo posts failed to fetch entirely.** yt-dlp reports "No video formats
  found" for Instagram image posts and aborts; `--ignore-no-formats-error`
  recovers their metadata and thumbnails. This affected 85 of 420 items.
- **`--retry-failed` selected nothing.** A permanent failure stamps `fetch_v`,
  so the `fetch_v < current` predicate excluded exactly the rows the flag was
  meant to pick up.
- **The detail panel could not be hidden.** `.sheet { display: grid }` and the
  user agent's `[hidden] { display: none }` have identical specificity, so this
  stylesheet won on source order and the panel rendered as an empty overlay
  with the `hidden` attribute set. `.empty` had the same bug. A global
  `[hidden] { display: none !important }` guard fixes both.
- **Stale assets in the browser.** `index.html` is now served with
  `Cache-Control: no-store` and mtime-stamped asset URLs, so a cached `app.js`
  can no longer render new markup with old behaviour — which looks exactly like
  a bug in the app.
- **Hashtag filtering matched substrings.** `json_each` gives an exact match on
  one element, so `#ai` no longer also returns `#aigenerated`.
- **Thread-bound SQLite connection.** The worker's pool could not write through
  the shared connection. `check_same_thread=False` is now set deliberately,
  with every writer holding a lock.
- **The detail panel's video overflowed its box.** A grid row sizes to its
  tallest content, so a long caption stretched the row, the media column
  stretched to match, and a `height: 100%` video grew past the panel's clipped
  edge — showing a cropped middle rather than the whole frame. Fixed with
  `minmax(0, 1fr)` on the row and `min-height: 0` on both columns, which is
  what lets a grid item shrink below its content and allows the metadata column
  to scroll on its own.
- **Over-eager place detection.** `🗺️` carries a U+FE0F variation selector, and
  inside a character class that selector matched on its own — so every emoji in
  a caption acted as a location pin, pulling in prose. Spelled out explicitly,
  with a quality gate that drops descriptions after a venue name and rejects
  itinerary markers like `STOP 1`. Matches went from 103 captions (mostly
  wrong) to 39 (all real).

### Notes

A carousel post makes yt-dlp report a *child* item's id rather than the post
requested, so the fetched id is treated as informational only and the shortcode
already held stays the key — otherwise thumbnails would be filed under the
wrong name.

Run against the real library: **411 of 420 thumbnails** (108 MB) and **322 of
335 reel videos** (3.5 GB), each in about eight minutes at concurrency 3. The 9
unfetchable items are 6 `restricted audience` and 3 `login required`, both
permanent. 89 photo posts correctly skipped the video download while keeping
everything else.

Hashtag tagging covers **249 of 420 items** across 10 categories. The 171
without tags are the honest limit of hashtag-only classification: 81 have no
hashtags at all, and the rest use hashtags too idiosyncratic to map. Reading
captions and transcripts is phase 03.

The Docker image installs no `ffmpeg`, `yt-dlp` or `tesseract`, so enrichment
works from a local checkout but not yet inside the container — which is the
actual deployment target. Tracked as an issue.

Search remains **keyword, not semantic**. FTS5 over captions is effective
because Instagram captions are long and hashtag-stuffed, but matching "pizza" to
a caption that only says "the best margherita in Naples" needs embeddings.

## [0.1.0] — 2026-10-21

Phase 00. An Instagram export ZIP goes in; a browsable, full-text searchable
library comes out. No network calls, no models, no credentials.

### Added

- **Import pipeline.** Reads `saved_posts.json` and `saved_collections.json`
  from an export ZIP or an unpacked directory. Idempotent by contract: the same
  export imported twice changes zero rows.
- **Shortcode identity.** The shortcode parsed from the permalink is the primary
  key, which is what makes dedupe, incremental import, stage retries and
  hand-written tags survive each other. Handles `/p/`, `/reel/`, `/reels/` and
  `/tv/` URLs with or without locale prefixes, query strings or trailing slash.
- **Tolerant parser** with three strategies tried in order — the current
  `label_values` shape, the older documented `string_map_data` shape, and a
  generic permalink sweep — so a reshape of the file degrades the import rather
  than breaking it. The import reports which strategy matched.
- **Captions, creators, hashtags and `fbid`** extracted from the export, plus
  the raw record kept verbatim in `item.raw_meta` so a field we did not parse
  today costs a SQL query rather than another crawl.
- **Full-text search** over captions via SQLite FTS5, with sync triggers already
  wired for the transcript and OCR columns that phases 02 and 04 will fill.
- **Schema for the whole roadmap**: per-stage version columns (`fetch_v`,
  `asr_v`, `ocr_v`, `tag_v`, `embed_v`) and `asr_profile` / `tag_profile`, so a
  later re-run costs only the stage that changed; `item_tag.source` separating
  model tags from user tags; `import_run` bookkeeping.
- **Collection membership**, including the 20 reels that appear in a collection
  but not in `saved_posts.json`. Collections embed full records, so those
  arrive with their caption and creator rather than as bare URLs.
- **Web UI** — grid with collection filter, search, sort, drag-free ZIP upload,
  and a pipeline-state sidebar. FastAPI backend, stdlib-only core.
- **CLI** — `python -m app.importer <zip-or-dir>`.
- **Docker Compose** targeting a single VM, with an `ai`-profiled Ollama service
  ready for phase 03 and its GPU block documented but commented out.
- **30 tests**, no third-party dependencies, covering idempotency, enrichment
  preservation, earliest-save-wins, collection adoption, every parser strategy
  and the mojibake repair.
- **Animated SVG diagrams** of the running app and the item pipeline.

### Fixed

- **Mojibake in all exported text.** Instagram writes UTF-8 bytes escaped as
  latin-1, so captions arrived as `âMOST FAMOUS...`. Repaired on import by
  round-tripping, with text that was already clean left untouched.
- **Creator attribution.** The per-record `Title` field is present on every
  record and always empty; the handle lives in a nested `Owner` group. Reading
  `Title` would have produced a library with no creators in it.
- **Double-counted import statistics.** A reel listed in `saved_posts.json` and
  embedded in a collection was upserted twice and counted twice. Stats now track
  one outcome per shortcode rather than per upsert call.
- **Collection members were skipped entirely.** Records embedded in a collection
  key their field list `dict` rather than `label_values`, so none of them
  parsed; the 20 collection-only reels were being adopted as bare URLs with no
  caption or creator.
- **Missing `python-multipart`.** The ZIP upload endpoint crashed the app at
  import time, which would have broken the Docker image identically.

### Changed

- Dependency management moved from `requirements.txt` + pip to `pyproject.toml`
  + `uv`, with a committed `uv.lock`. The Docker build now does
  `uv sync --frozen --no-dev` before copying the app, so the dependency layer
  caches until the lockfile actually changes.
- `HIKIDASHI_EXPORTS` / `data/exports` renamed to `HIKIDASHI_IMPORTS` /
  `data/imports`. Instagram exports; we import.

### Notes

Measured against a real export rather than assumed: **420 items** (335 reels,
85 photo posts) across 7 collections, with captions on all 420 and hashtags on
339. That is roughly a twelfth of the 5,000 the plan was sized for, which brings
the recommended VM down to 4 vCPU / 8 GB / 40 GB and the full backfill to about
two hours.

Photo posts have no audio and will never have a transcript, so for roughly a
fifth of the library OCR is the only thing that can read the content — phase 04
is no longer optional cleanup.

[Unreleased]: https://github.com/spanthetree/hikidashi/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/spanthetree/hikidashi/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/spanthetree/hikidashi/releases/tag/v0.1.0
