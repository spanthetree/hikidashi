# Changelog

All notable changes to Hikidashi are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.24.0] — 2026-11-13

### Changed

- **One AI choice each,** on Account → AI providers (#125). A user chooses
  System default (whatever the admin chose), Hosted AI (their own provider
  and key) or Workers only, and the settings show under Hosted AI. An admin
  chooses Hosted AI or Workers only, and that is the system default: there
  is no separate server section, and no "just me / all users".
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
