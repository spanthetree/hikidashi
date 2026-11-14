# Changelog

All notable changes to Hikidashi are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.28.0] — 2026-11-14

### Added

- **Logging and monitoring on the Account page**: an admin switches
  Prometheus metrics on and off, sends the log to syslog or Loki, and sets the
  level, format and the rest, without a restart. It shows what is on. A
  variable set in the environment still wins, and shows locked; the metrics
  token and Loki's token and password are saved sealed and never shown back.
  Workers beside the server follow a change within 30 seconds; a worker on
  another machine keeps to its own environment (ADR 0023).
  `GET`/`PUT /api/admin/monitoring`.

### Changed

- **The swipe that fills a reel's sheet with its video follows the finger**,
  both ways, and eases to full or back when you let go, as the swipe that
  closes the sheet already did.
- `/metrics` is always routed, and answers 404 while metrics are off.

## [0.27.0] — 2026-11-14

### Added

- **Full-screen video on a phone**: swipe down on a reel's video and the text
  makes way, so the video takes the whole sheet. Swipe down again to close
  it, or up to bring the text back.

### Changed

- **Search has its own row on a phone**, full width under the header,
  instead of squeezing between the logo and the sort.
- **Ask sits at the end of the search bar**, at every width, rather than in
  the toolbar with Shuffle, Export and Import.

### Fixed

- **A reel's sheet sat cut off on a phone** in every theme but Hikidashi:
  the wider type in the toolbar ran past the screen, Chrome on Android
  widened the page to fit, and the sheet stretched with it. The themes size
  the toolbar to fit, and with Ask moved, searching no longer widens it.

## [0.26.0] — 2026-11-13

### Added

- **Add a reel from your phone** (#52): Hikidashi installs as an app, and
  then sits in the share sheet. Share a reel in the Instagram app, pick
  Hikidashi, and it is in your library, fetched by the workers like any new
  reel. **Import** has an **Add by link** field too, and on an iPhone, which
  has no share targets, an iOS Shortcut posts the link with an API token
  ([how](docs/using.md#adding-a-reel-from-your-phone)). Adding one you have
  already changes nothing, and a later export only moves its save time back.
  Installing needs the server on HTTPS. `POST /api/items` takes the link, with
  a `write:library` token from a script.
- **Prometheus metrics** (#149), off unless `HIKIDASHI_METRICS=on`: the
  backlog and failures per stage, workers up, reels by state, coverage,
  imports, search and request latency, hosted AI spend, the database's size.
  A bearer token or private addresses only; never a shortcode, caption,
  query or username in a label. One scrape covers every container, and costs
  under 2 ms from its cache. `deploy/monitoring/` has a scrape job, alert
  rules and a Grafana dashboard, and `docker-compose.monitoring.yml` runs
  Prometheus, Loki and Grafana beside the app (ADR 0022).
- **`/healthz` and `/readyz`** (#149), with no sign-in. The image, the compose
  files and the NAS templates check `/readyz`, which migrates an old database
  first.
- **The log to syslog and Loki** (#149), off unless set: `HIKIDASHI_SYSLOG`
  (UDP, TCP or a unix socket; RFC 5424 or 3164) and `HIKIDASHI_LOKI_URL`, from
  every container, redacted first (tokens, keys, passwords, search queries).
  `HIKIDASHI_LOG_FORMAT=json` makes stdout and what is sent JSON lines. The
  Unraid and TrueNAS templates have the new settings.

### Fixed

- **A reel without its video says why** (#8): `photo` for a photo post, or
  the download's error, where a failed download used to vanish without a
  word. `./hikidashi enrich --media-only` stops asking for photos, counts what
  is missing and why, and gives photo posts and carousels the width and
  height their thumbnail has, which yt-dlp never reported.

### Changed

- The service worker caches the app's shell only; pages still come from the
  network first, so an update is never hidden behind an old page.

## [0.25.0] — 2026-11-13

### Added

- **More like this** (#53): the reels in your library closest in meaning to
  the one open, in a row at the bottom of its sheet.
- **Rediscover** (#50): above the grid, saves from this day in earlier years
  and saves you have never opened, each strip hidden until tomorrow by its ×.
  **Shuffle** opens a random reel from whatever the grid shows, and
  **Another** picks again. Whether you opened a reel is yours alone.
- **Jump to the moment a search matched** (#44): transcripts keep when each
  line was said. A search that matches the voiceover shows the line on the
  card and opens the reel there (`?t=`); the transcript reads line by line,
  the one playing lit, and pressing a line plays from it. Remote workers send
  the timings with the transcript.
- **Reposts and duplicate saves** (#58): every reel is fingerprinted from its
  frames, and copies of the same video are grouped. Cards say how many copies
  you have, the sheet lists them under **Also saved as** with **Not the same**
  to split a wrong match, and a setting on your Account page shows a reel and
  its reposts as one card (off by default). 4 groups on a 420-reel library,
  all right.
- **Incomplete recipes say where the rest is** (#114): the page the caption
  links to, the creator's profile, or the keyword to comment. When it links
  to the recipe's page, the rest is fetched from there (schema.org JSON-LD)
  the first time you open it, and shown in place of the incomplete one.
  `HIKIDASHI_RECIPE_FETCH=off` turns that off (ADR 0020).
- **Recipes in your language** (#114): the recipe stage translates each recipe
  into the app's languages as it reads it, on the same model (a worker's
  Ollama or your hosted API), so the server needs no model for it. Read the
  app in Japanese and you see the translation; **Show the original**
  switches back. `RECIPE_V` is 6: every recipe is read once more.
- **Ollama as an AI provider** (#125): pick "Ollama (self-hosted)" under AI
  providers for on-screen text, tags and recipes, with `qwen2.5:7b` (what the
  GPU worker runs) and `qwen2.5vl:7b` as defaults and others suggested. No key.
  It has no speech to text, so transcription needs another provider. Set for
  the recipe stage, it also answers Ask and translates corrected recipes on
  the server, even with the hosted switch off: nothing leaves your network.
- **Ask your library** (#45): **Ask**, beside a search, answers the question
  from your own saves, citing the reels it used; each citation opens the reel
  at the moment it was said. It says so when nothing covers the question.
- **API explorer** (#147) at `/api/docs`: every route by area, with what it
  takes and returns and the scope it needs, to try with your session or an
  API token. Signed-in only; no admin routes for non-admins; nothing loaded
  from a CDN; changes off until **Allow changes**.

### Changed

- **The server asks a model while you wait, for Ask only** (ADR 0021): your
  hosted API if your reels use one, else a self-hosted provider, else the
  Ollama at `OLLAMA_URL`. The app container now gets `OLLAMA_URL`
  (`HIKIDASHI_OLLAMA_URL` in Docker) and `HIKIDASHI_RECIPE_FETCH`, in the
  compose file and the Unraid and TrueNAS templates.
- `ASR_V` is 3: the next transcription pass transcribes every reel again, to
  keep its timings. On a CPU that takes a while; with a hosted API it costs
  what a transcription costs. The embed stage also fingerprints every reel
  once (about a quarter of a second each).
- `/api/openapi.json` needs a signed-in user, and is filtered for
  non-admins.

## [0.24.1] — 2026-11-13

### Changed

- **Section descriptions behind an info button.** On the Pipeline, Account
  and Logs pages, the paragraph under a title now sits behind an "i" beside
  it: hover to read it, or click or tap to keep it open. Hints and status
  lines stay where they were.
- **README pitch line:** Instagram's Saved tab "can't find a reel by what it
  says or shows", rather than "has no search", in English and Japanese.

## [0.24.0] — 2026-11-13

### Changed

- **One AI choice each,** on Account → AI providers (#125), admins
  included: System default, Hosted AI (your own provider and key) or Workers
  only, with the settings under Hosted AI. The **system default** that
  System default follows (workers, or hosted AI with the server's own key) is
  set by an admin on the Pipeline page. No "just me / all users".
  `hikidashi ai use` takes the same choices; `--server-api` sets the system
  default.

### Fixed

- Choosing how your reels are processed on Account → AI providers crashed
  the page ("500 Internal Error").
- Saving an API key on a server missing the `cryptography` package was a
  server error; it now says to sync the server extra, and `./hikidashi start`
  checks for it.

## [0.23.0] — 2026-11-12

### Added

- **Hosted AI, behind an on/off switch** (#116, #125;
  [ADR 0019](docs/adr/0019-hosted-ai-behind-a-switch.md)). A provider's API
  can do transcription, on-screen text, tags and recipes instead of a worker,
  with no GPU and no Ollama. Off by default: nothing is sent anywhere until
  someone turns a switch on.
  - **Per user and for the server.** Each user can use their own provider and
    key. The admin's can be on for just them or for all users, and users
    choose: server default, their own API, the server's, or workers only.
  - **All or nothing per reel:** the API that takes a reel does every stage it
    still needs. Providers can be mixed per stage.
  - **Providers:** OpenAI, Groq (free Whisper large), Mistral, Gemini,
    OpenRouter, Cerebras, GitHub Models, Cloudflare Workers AI, Hugging Face,
    or any OpenAI-compatible server of your own. Free tiers and data terms
    are checked and dated in [operation](docs/operation.md#providers).
  - **Multilingual transcripts:** a hosted Whisper detects the language, kept
    with the transcript.
  - **Budgets and usage:** a monthly budget is required, and estimated spend is
    recorded per request. Turning a switch on shows the reels waiting and what
    they might cost. It turns itself off, saying why, on a rejected key, a
    used-up quota or the budget reached.
  - **Account → AI providers** in the web UI, and `hikidashi ai` on the command
    line, on the host or with `--server` and an API token. New token scopes
    `read:ai` and `write:ai`.
  - **`hikidashi worker --backend hosted`:** the server's API run on another
    machine, with that machine's own key.
- **A view of your own reels' progress** (`/api/me/pipeline`), for everyone,
  not only admins.

### Changed

- **Remote worker protocol 2.** Run the same release on the server and on
  every remote worker.
- API keys saved on the settings page are encrypted with `data/secret.key` (or
  `HIKIDASHI_SECRET_KEY`). Back it up with the database; lose it, and the keys
  are simply entered again.

### Fixed

- Signing out no longer sometimes lands on the sign-in page with
  `?next=/account`.

## [0.22.0] — 2026-11-12

### Added

- **Six more categories** (#145): Movies & TV, Nature & science, Family &
  wellbeing, Style & beauty, Home & living, and Shopping, for reels that
  fitted none of the ten. On 50 hand-labelled reels the model now gets 36
  right where it got 31, and leaves 5 reels of 411 without a category where
  it left 36. Why these six is in
  [ADR 0018](docs/adr/0018-categories-from-hand-labels.md) and
  [docs/tags.md](docs/tags.md#six-more-categories).

### Upgrading

- Every reel is re-tagged with the new list: the hashtag pass at once, and
  the model pass by your workers as they get to it, which on a CPU takes a
  while for a large library. Tags you set yourself are kept.

## [0.21.0] — 2026-11-12

### Added

- **Evals by release** ([docs/eval.md](docs/eval.md)): search and tagging
  scored on the demo library at every release, charted across versions. CI
  fails a pull request that makes them fall too far.
- **Tag calibration** (#6): `python -m app.tagging --pick 50` lists reels
  worth labelling by hand in your library, and `--eval --labels FILE` scores
  against a file of labels, where a reel can be labelled "none fits". The
  demo library's labels are in `tests/tag_labels.json`.

### Changed

- A search that finds nothing with vectors now falls back to any two of its
  words, as keyword-only search does, rather than showing an empty page.

## [0.20.0] — 2026-11-12

### Changed

- **Node 22.17 or later** is needed to build the web UI from source (with
  uv, `./hikidashi start` checks it). The image builds on Node 24, the
  current LTS. Docker installs need nothing new.
- The web UI is on SvelteKit 3, Vite 8, TypeScript 6 and paraglide 3. It
  looks and behaves as before.

## [0.19.0] — 2026-11-12

The first public release. Hikidashi turns your Instagram data export into a
searchable library on your own machine, with local models.

### What's in it

- **Search by meaning and by word,** over captions, transcripts
  (faster-whisper) and on-screen text (Tesseract), in one ranked list.
  Japanese searches find English reels on the same subject.
- **Tags, summaries and keywords** from a local model through Ollama, with
  the video game a gaming reel shows. Your own choice always wins.
- **Recipes** read from captions and voiceovers: ingredient checklists and
  steps, filters by time and ingredient, and a shopping list merged by aisle.
- **Places on a map,** from the addresses and pins in captions, with an
  opt-in geocoder and your own tiles if you like.
- **Your own copy** of every reel's video and thumbnail.
- **Exports** for Mealie, Tandoor and Paprika, an Obsidian vault, JSON and
  CSV.
- **Several people on one server,** each with a private library, signed in
  with a password or by your proxy, and scoped API tokens for scripts.
- **Workers** that process new saves as they arrive, and can run the heavy
  stages on another machine with a GPU.
- **English and Japanese,** four themes in light and dark, and pixel art.

### Install

Published images, `ghcr.io/spanthetree/hikidashi-server`, `hikidashi-worker`
and `hikidashi-worker-cuda`, for Docker Compose, Unraid and TrueNAS SCALE.
See [Installing](docs/install.md).

### Upgrading from a build before 0.19.0

Earlier versions were not published, but if you ran one from the
repository, under its old name ReelVault (before 0.18.0):

- Rename the `REELVAULT_` settings in `.env` to `HIKIDASHI_`, and rename
  `data/reelvault.sqlite3` (with its `-wal` and `-shm` files) to
  `hikidashi.sqlite3`, with the server and workers stopped. 0.19.0 no
  longer reads the old names.
- The database migrates itself from any earlier version.

The notes for the versions before 0.19.0 are in the git history.
