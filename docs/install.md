# Installing

Getting Hikidashi running on a computer, importing a library, and keeping it
up to date. For a NAS, see [Unraid](install-unraid.md) or
[TrueNAS SCALE](install-truenas.md). [Back to the README](../README.md)

- [Getting your Instagram export](#getting-your-instagram-export)
- [With Docker](#with-docker)
- [With uv](#with-uv)
- [Importing](#importing)
- [Processing](#processing)
- [Managing the server](#managing-the-server)
- [Updating](#updating)
- [Moving from Docker to uv](#moving-from-docker-to-uv)

## Getting your Instagram export

Hikidashi works from the data export Instagram gives you; it never asks for
your Instagram password.

1. Open [**Export your information**](https://accountscenter.instagram.com/info_and_permissions/dyi/)
   in Accounts Centre. In the app: **Accounts Centre** → *Your information and
   permissions* → *Export your information*.
2. **Create export** → *Export to device*.
3. Choose **Saved** only, **All time**, and format **JSON**.

A saved-only export usually arrives within the hour, as a ZIP. Import a newer
one whenever you like: reels already in the library are skipped.

## With Docker

You need Docker with Compose. Clone the repository for the compose files and
the `hikidashi` script, then start it:

```bash
git clone https://github.com/spanthetree/hikidashi.git && cd hikidashi
./hikidashi --docker start --ai
```

This pulls the published images from `ghcr.io/spanthetree` and starts the
server on <http://localhost:8080>, two workers, and, with `--ai`, Ollama for
tagging and recipes. Leave out `--ai` to use an Ollama you already run, at
`HIKIDASHI_OLLAMA_URL`. `--build` builds the images from the checkout instead
of pulling them.

`./hikidashi` picks Docker once its container exists, so later commands need no
`--docker`.

**On a low-power device,** such as a small NAS or an ARM board, run the server
without the worker container or Ollama. In `.env`:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.server-only.yml
```

Then `./hikidashi --docker start`, without `--ai`. The server still fetches new
reels and embeds them, so they are searchable by caption and by meaning.
Transcripts, on-screen text, tags and recipes are done by a
[remote worker](remote-workers.md) on another machine whenever it runs. To
transcribe and read on the device itself while that machine is off, leave
`COMPOSE_FILE` alone and set `HIKIDASHI_LOCAL_STAGES=asr,ocr` instead. The containers run as root unless `HIKIDASHI_USER` says otherwise
([configuration](configuration.md)).

## With uv

You need Python 3.12 with [uv](https://docs.astral.sh/uv/), FFmpeg, and Node 20
or later to build the web app. Tesseract (on-screen text) and
[Ollama](https://ollama.com) (tags and recipes) are optional; without them,
those stages wait.

```bash
git clone https://github.com/spanthetree/hikidashi.git && cd hikidashi
uv sync --extra server --extra worker
./hikidashi start
```

The web app is built on the first start, and rebuilt whenever its sources
change. Add `--extra cuda` to `uv sync` to transcribe on an NVIDIA GPU
([hardware profiles](pipeline.md#hardware-profiles)).

## Importing

Open <http://localhost:8080>. A new library opens on the import box: drop the
ZIP on it, or click to choose it. Later, **Import** in the toolbar opens the
same box, and a ZIP dropped anywhere on the library is imported too.

From the command line:

```bash
./hikidashi import data/imports/export.zip
```

Under Docker the ZIP must be inside the data folder, which is all the
container can see. Search works on captions straight away.

## Processing

The workers that `start` runs download, transcribe, read, tag and embed every
new reel on their own, each stage once the ones before it are done. Under uv
one worker runs every stage; under Docker `server-worker` fetches and embeds,
and `worker` transcribes, reads, tags and finds recipes. Follow them on the
**Pipeline** page or with `./hikidashi status`.

To run every stage over everything once instead, from cron for example:

```bash
./hikidashi pipeline
```

[The pipeline](pipeline.md) covers each stage, and
[remote workers](remote-workers.md) how to run the heavy ones on another
machine with a GPU.

## Managing the server

```bash
./hikidashi status       # server, workers, counts per stage, which models are ready
./hikidashi logs -f      # the server's log; `logs worker` or `logs server-worker` for one
./hikidashi stop         # the server and the workers
./hikidashi start        # all again; under Docker, --ai starts Ollama too
./hikidashi restart      # all, picking up changes to .env
```

`./hikidashi <command> --help` lists each command's options. The script is
POSIX sh and needs no Python on the host; [development.md](development.md#running-the-server)
describes what it runs under each mode.

## Updating

```bash
git pull && ./hikidashi start                                              # Docker
git pull && uv sync --extra server --extra worker && ./hikidashi restart   # uv
```

Add `--ai` to the Docker line if you run the bundled Ollama. Under uv, name
both extras every time: `uv sync` removes whatever the command leaves out.

The database migrates itself, and the workers re-run only the stages whose
version changed. The [changelog](../CHANGELOG.md) lists what changed in each
release. To stay on one release under Docker, set `HIKIDASHI_VERSION` (as
`0.18.7`) in `.env`.

## Moving from Docker to uv

On the same machine, with the same data:

```bash
docker compose --profile ai down          # or ./hikidashi keeps picking Docker
sudo chown -R "$(id -un)" data            # the containers wrote it as root
uv sync --extra server --extra worker && ./hikidashi start
```

The bundled Ollama's port isn't published, so the uv worker needs an Ollama of
its own at `OLLAMA_URL` (`localhost:11434` by default). To keep the models
already pulled, run the same volume with the port published:

```bash
docker run -d --name ollama -p 127.0.0.1:11434:11434 \
  -v hikidashi_ollama-models:/root/.ollama ollama/ollama
```
