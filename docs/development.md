# Development

Working on Hikidashi itself. [Back to the README](../README.md)

## Running the server

`./hikidashi` picks Docker when the `hikidashi` container exists or uv is not
installed, and uv otherwise. `--docker` or `--uv` as the first argument, or
`HIKIDASHI_MODE`, overrides that. `HIKIDASHI_HOST` and `HIKIDASHI_PORT` (default
`0.0.0.0:8080`) apply in both.

Under uv the pid is kept in `data/run/server.pid` and uvicorn's raw output in
`data/logs/server.out`; the server writes its own log to
`data/logs/server.log`, which is what `logs` and the `/logs` page show. A
pidfile left by a crash is noticed and cleared, and `stop` sends SIGTERM, then
SIGKILL after 10 seconds (`HIKIDASHI_STOP_TIMEOUT`).

`start` also starts the worker (`hikidashi worker`), with its pid in
`data/run/worker.pid`, its raw output in `data/logs/worker.out` and its log in
`data/logs/worker.log`; `stop` stops it first, giving it the same time to give
back the reels it holds. Under uv one worker runs every stage. Under Docker
there are two, `server-worker` (enrich and embed, in the server image) and
`worker` (asr, ocr, tag and recipe, in the worker image), which `docker
compose up` starts with the server. See
[the pipeline](pipeline.md#in-docker) for what each image holds.

Under uv, `start` needs both extras (`uv sync --extra server --extra worker`)
and says so when fastapi or uvicorn is missing. A bare `uv sync` installs no
third-party package at all; it is enough for the CLI, import, exports, and a
worker that only tags and finds recipes. `--extra worker --extra cuda` adds
ASR on a GPU.

The script is POSIX sh and needs no Python on the host. Under Docker it hands
pipeline commands to `docker compose exec <service> hikidashi ...`, in the
container whose image has the command's tools, and an import
or export path under the data folder becomes the same path under `/data` in the
container. The data folder is the source of the running container's `/data`
mount, from `docker container inspect`, since compose reads `HIKIDASHI_DATA`
from `.env` and the script does not; else `HIKIDASHI_DATA`, else `./data`.

Under uv the script exports `UV_ENV_FILE=.env` when `.env` exists and
`UV_ENV_FILE` is not set already, so every `uv run` it makes reads `.env` as
Compose would, with the shell's variables winning. The few settings it reads
itself (`HIKIDASHI_HOST`, `HIKIDASHI_PORT`, `HIKIDASHI_RUN`, `HIKIDASHI_LOGS`,
`HIKIDASHI_STOP_TIMEOUT`) come from the shell, else that file, else their
defaults.

### Without the wrapper

Each command is a module run with `python -m`, and the wrapper only picks how:

| `./hikidashi` | uv | Docker Compose |
|---------------|----|----------------|
| `start` | `uv run uvicorn app.api.main:app --port 8080` | `docker compose pull --ignore-pull-failures && docker compose up -d` (`--build`: `docker compose up -d --build`) |
| `import <zip>` | `uv run python -m app.importer <zip>` | `docker compose exec app python -m app.importer <zip>` |
| `enrich` | `uv run python -m app.enrich` | `docker compose exec app python -m app.enrich` |
| `transcribe` | `uv run python -m app.asr` | `docker compose exec worker python -m app.asr` |
| `ocr` | `uv run python -m app.ocr` | `docker compose exec worker python -m app.ocr` |
| `tag` | `uv run python -m app.tagging` | `docker compose exec worker python -m app.tagging` |
| `recipe` | `uv run python -m app.recipes` | `docker compose exec worker python -m app.recipes` |
| `embed` | `uv run python -m app.embed` | `docker compose exec app python -m app.embed` |
| `places` | `uv run python -m app.places` | `docker compose exec app python -m app.places` |
| `worker` | `uv run python -m app.jobs` | `docker compose up -d server-worker worker` |

`uv run hikidashi <command>` and `docker compose exec <service> hikidashi
<command>` work too: that is the CLI the wrapper calls (`app/cli.py`).

## The front end

The web UI is a Svelte 5 / SvelteKit app in `frontend/`, built to static files
in `app/web` that FastAPI serves; there is no Node process at runtime. The
server image builds it in a stage of its own; the worker image has none. Under uv, `./hikidashi start`
builds it when it is missing or older than its sources, which needs Node 20
or later on the host.

While working on it, run the API and Vite's dev server side by side:

```bash
uv run uvicorn app.api.main:app --reload --port 8080   # terminal 1
npm run dev --prefix frontend                          # terminal 2: http://localhost:5173
```

Vite reloads the page as you edit and proxies `/api` and `/media` to port 8080
(set `HIKIDASHI_API` to point it elsewhere). `npm run check` type-checks,
`npm test` runs the unit tests, and `npm run build` writes `app/web` for
FastAPI to serve.

### Translations

Every string the UI shows is a message in `frontend/messages/en.json`, with its
translation in `ja.json` under the same key. [Paraglide](https://paraglidejs.com)
compiles them to typed functions in `src/lib/paraglide/` (generated, not
checked in), so a component writes `{m.save()}` and a missing message or a
wrong parameter is a type error. `npm test` fails on text written straight into
a `.svelte` file, on a key one language has and the other lacks, on a
translation whose `{inputs}` differ from the English, and on a message nothing
uses. Text meant literally goes in `<code>` or under `translate="no"`.

Counts that need a plural, and markup like `{#b}…{/b}`, use inlang's message
format (`ingredient_count` and `empty_body` in `en.json` are examples).
Numbers and dates go through `src/lib/format.ts`, which formats them for the
language on screen.

The tag taxonomy, the ingredient groups and the pipeline states are English in
the database and in URLs; `src/lib/i18n.ts` translates them for display. A
category added to `app/tagging/taxonomy.py` or `app/recipes/groups.py` needs a
message there too, or it shows in English. Instagram's own text (captions,
hashtags, collection names) and the model's summaries are shown as they are.

To add a language:

1. Copy `frontend/messages/en.json` to `<code>.json` (`ko.json`, say) and
   translate the values. Keep the keys and the `{inputs}`.
2. Add the code to `locales` in `frontend/project.inlang/settings.json`.
3. Add its own name to `LANGUAGES` in `frontend/src/lib/i18n.ts`, which the
   switcher shows.
4. `npm test --prefix frontend`, then look at it at phone width: labels grow.

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

No third-party dependencies and no network access. The fetcher and
the Whisper model are mocked. `app/db.py`, `app/importer/`, `app/enrich/`,
`app/asr/`, `app/ocr/`, `app/tagging/`, `app/recipes/`, `app/ollama.py`,
`app/embed/`, `app/search/`, `app/profiles.py`, `app/places/` and
`app/cli.py` are stdlib-only exactly so this works on a bare checkout, with or
without `uv sync` having run. `faster-whisper` is only imported once a model is
actually loaded, and `fastembed` once an embedder is. The tests that need
`sqlite-vec`, the web layer, argon2 or Hypothesis are skipped without them; CI
installs them (`.github/workflows/tests.yml`) so they run there.

[testing.md](testing.md) has the rest: the browser tests, coverage, property
tests, ruff and mypy, the seeded library they share, and what CI runs when.

Search quality has its own check against the real library, separate from the
tests: `uv run python -m app.search.eval` (see [search.md](search.md)). Run it
before and after any change to `app/search/` or to what gets embedded.

## Regenerating the diagrams

`scripts/make_diagrams.py` writes the diagrams whose source of truth is code:
the ER diagrams in [architecture.md](architecture.md) from `app/schema.sql`, the
stage graph in [operation.md](operation.md) from the stage registry in
`app/jobs/queue.py`, and the animated SVGs in `docs/diagrams/` and
`docs/pipeline.svg` from its own functions.

```bash
uv run python scripts/make_diagrams.py          # rewrite what is stale
uv run python scripts/make_diagrams.py --check  # exit 1 if anything is
```

Generated Mermaid sits between `<!-- make_diagrams: NAME -->` and
`<!-- /make_diagrams -->`; edit the code, not the block. `tests/test_diagrams.py`
fails when any output is stale, so CI does too. A new table must be added to
one of `GROUPS` in the script, or the run stops and says so.

The animated SVGs follow #88's rules, which the script's docstring repeats:
CSS animation only, a dark theme under `prefers-color-scheme`, the end state as
the base style (what reduced motion and non-animating viewers show), anything
still to appear drawn faint in the first frame, no raster images, and an
`aria-label` saying what the animation shows. To check one by eye, open it in a
browser, and toggle dark mode and reduced motion in the developer tools'
rendering panel.

The hand-written Mermaid in architecture.md and operation.md (context,
containers, layouts, security, sequences, the worker loop) is checked by
review only: GitHub renders it in the PR, so look at the rendered diff.

## Recording the demo

The animation at the top of the README (`docs/demo/app.webp`), the longer
feature tour (`docs/demo/tour.mp4`) and the remote worker's terminal in
[remote-workers.md](remote-workers.md) are recordings of the real app and the
real workers, on a demo library of freely licensed photos and clips from
Wikimedia Commons ([credits](demo/CREDITS.md)). Never record your own library:
its thumbnails, captions and creators are other people's, and so are your saves.

```bash
uv run python scripts/demo_library.py sources   # download and cut the media, once
uv run python scripts/demo_library.py build     # a fresh library in ~/.cache/hikidashi-demo
uv run python scripts/demo_library.py serve     # the server, on 127.0.0.1:8090; leave it running
uv run python scripts/demo_library.py worker    # enrich and embed, beside it; leave it running
uv run python scripts/demo_library.py cast      # asr, ocr, tag and recipe as a remote worker, recorded
uv run python scripts/make_diagrams.py          # draws the terminal from that recording
cd frontend
node scripts/record-demo.mjs hero               # docs/demo/app.webp
node scripts/record-demo.mjs tour               # docs/demo/tour.mp4
```

The demo runs on its own files and port, so it never touches `data/` or a
running Hikidashi, but it reads models from `data/models` and asks the Ollama at
`OLLAMA_URL`. It uses the CPU profile unless `HIKIDASHI_PROFILE` says otherwise.
Places are looked up on a stub geocoder inside `demo_library.py`, which knows
only the places the captions name.

`record-demo.mjs` needs Xvfb, ffmpeg with libwebp and libx264, and Playwright's
Chromium (`npx playwright install chromium`). It films a real browser at
30 fps, with a drawn pointer that follows the mouse, in dark mode (`--light`
for light). The default theme is drawn in the system's own fonts, which under
Xvfb are DejaVu Sans, so the filmed browser alone is given Segoe UI, Yu Gothic
UI, Segoe UI Emoji and Consolas from `DEMO_FONTS`: by default
`/mnt/c/Windows/Fonts`, the Windows fonts as WSL sees them. The fonts are
only read, never copied into the repository. Without them it warns and films
in the machine's fonts. Re-record when the UI
changes enough that the README no longer shows it. The recordings are not
checked in CI: no two takes are the same.

To add or change a reel, edit `REELS` in `demo_library.py`: only CC0, public
domain or CC BY files, never share-alike, since the recordings are made from
them. `sources` checks the licence and rewrites the credits.
