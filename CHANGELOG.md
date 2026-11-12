# Changelog

All notable changes to Hikidashi are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
