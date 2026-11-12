# Remote workers

Hikidashi can split in two: an always-on **server** (the web UI, the API, the
database, search) on a small box such as a NAS or a VM, and a **worker** on a
desktop with a GPU that is only on some of the time. The worker transcribes,
reads on-screen text, tags and checks for recipes, and sends what it found back
to the server. When the desktop is off, new reels wait, and nothing is lost.

This page covers the options that were weighed ([#35](https://github.com/spanthetree/hikidashi/issues/35)), how the chosen one
works, and how to set it up. [The pipeline](pipeline.md#the-worker) covers the
worker itself.

## The options

The hard constraint is SQLite: one file, one host. A worker on another machine
cannot open the database over the network. SQLite over NFS or SMB is a
corruption path.

| | Option | What it takes | Verdict |
|---|---|---|---|
| A | Remote Ollama only | the server's worker pointed at the desktop's Ollama | Retired ([#86](https://github.com/spanthetree/hikidashi/issues/86)). Only tagging and recipes moved, with the server's model; a remote worker does all of it with the desktop's own |
| B | Workers as API clients | a claim/result API on the server | **Built.** Everything heavy moves; the server stays the only writer |
| C | Postgres instead of SQLite | a rewrite of the SQL, a database service to run | Ruled out for now |
| D | Ship the database around | a replica on the desktop, changesets back | Ruled out |

**A** needed no code, and was the baseline while B was built. It helped the
stages that need a large model most, but the server still transcribed on its
own CPU, and asked for the model of its own profile. Since [#86](https://github.com/spanthetree/hikidashi/issues/86) the server
never talks to Ollama: a worker does, its own.

**B** is what this page describes. Every stage is already resumable and
versioned, so "what is left to do" is a query the server can answer, and the
always-on worker ([#72](https://github.com/spanthetree/hikidashi/issues/72)) already claims reels with leases. A remote worker
claims through the server instead of the database, and posts results, which
the server records with the same functions a local worker uses.

**C** would let workers connect straight to the database, at the cost of
rewriting the SQLite-specific parts (FTS5 and its triggers, `bm25`,
sqlite-vec, the JSON helpers), re-tuning search, running a database service,
and needing one for every test run. It still would not stop a worker from
writing a vector from the wrong model, or keep workers and server on one
schema. Worth revisiting only if the server needs several replicas (k8s) or
many people use it at once. The full comparison is in [#35](https://github.com/spanthetree/hikidashi/issues/35).

**D** avoids an API, but merging changesets back makes conflicts Hikidashi's
problem, for no gain over B.

## How it works

```
  server (always on)                          worker (GPU desktop)
  ──────────────────                          ────────────────────
  POST /api/worker/claim     {stage, limit} ◀── which reels are ready?
     leases them; sends their rows ─────────▶ downloads the media they name
  GET  /api/worker/media/<path>          ◀── (cached; byte ranges work)
                                              runs the stage on its GPU
  POST /api/worker/results   one per reel  ◀── transcript, text, tag, recipe
     records each with the stage's own              or why it failed
     record function; releases the leases
  POST /api/worker/check-in  every 30 s    ◀── alive; keep my leases
```

- **One writer.** The server records every result itself, with the stage's
  own `record` and `record_failure`, exactly as a local worker would. The
  worker never sees the schema. The shared protocol is
  `app/jobs/protocol.py`.
- **The same queue.** Claims go through the same leases, readiness and
  attempts cap as a local worker's (`app/jobs/queue.py`). A remote worker
  shows up in `./hikidashi status` and on the Pipeline page, and a local
  worker and a remote one can share a stage without colliding.
- **Results are checked.** Each result's shape is checked before any is
  recorded. A tag the taxonomy does not have is no tag, as in the tagger's own
  parse. A game is sent as the model's answer and decided on the server,
  against the reel's text as the server has it. A result for a reel the worker no longer holds (its lease ran out
  and someone else took it) is dropped.
- **Versions must match.** Each request carries the worker's version of each
  stage (`ASR_V`, `TAG_V`…) and a protocol number. A mismatch is refused with
  a 409 naming both, and the worker stops: run the same release on both
  machines.
- **What stays on the server.** Enrich writes media files and fetches from
  Instagram, and the server is on the same home connection as the desktop
  anyway. Embedding must use the same model as search, which runs on the
  server for every query, and is cheap on CPU. A remote worker runs `asr`,
  `ocr`, `tag` and `recipe`; the server refuses claims for the rest.

### When something goes away

| What happens | What the worker does | What the server does |
|---|---|---|
| The desktop shuts down mid-batch | nothing: it is gone | its leases run out after 5 minutes, count one attempt each, and the reels go back in the queue |
| The worker is stopped (Ctrl-C, `docker stop`) | gives its leases back, uncounted, and checks out | shows it as stopped |
| The server is unreachable | waits, trying again every 5 s up to a minute; at start it waits too | leases run out as above |
| Ollama is off on the desktop | gives the reels back uncounted, tries again after a minute, then longer | shows the problem on the Pipeline page |
| A video the server no longer has | that reel fails; the batch goes on | records the failure |
| The token is wrong, or the releases differ | stops, saying why | refuses (401, 409) |

### Media

The worker downloads each video or thumbnail it needs into its cache,
`HIKIDASHI_CACHE` (`data/cache`), under the same paths as on the server, and
keeps it: ASR and OCR can both read one reel. Past 2 GB, the least recently
used files go. A full pass over the library this was built on, 328 videos and
4.5 hours of audio, is 3.8 GB, of which OCR needs about 1 GB (the reels with
no speech). In steady state only new reels move.

## Setting it up

### The server

Open the **Pipeline** page as an admin, and under **Workers on other
machines** make a token for each machine that will work for this server: name
it (`desktop`), and copy the token, or the whole command, shown once. The page
lists each token with who made it and when a worker last used it. Revoking one
refuses its worker from the next request. The server keeps only a hash of each
token.

For a server with no browser at hand, make one on the server instead:

```sh
./hikidashi token make desktop --worker
```

A worker token starts `hk_wkr_`, never expires, and opens `/api/worker/*` and
nothing else; an API token, even an admin's, does not open those routes. It
works only while the admin who made it is still an admin. Until there is one,
`/api/worker/*` is a 404: remote workers are off.

Before 0.15.0 the server also took `HIKIDASHI_WORKER_TOKEN` from its own
environment as a shared secret. It no longer does, and logs a warning at
start-up if it is set there: make a worker token as above, give it to the
desktop, and unset the variable on the server. Tokens made on the Pipeline
page before 0.15.0 keep working.

Then decide what the server's own workers keep doing. Under Docker,
`server-worker` fetches and embeds new reels whatever you choose, and the
`worker` container runs what `HIKIDASHI_LOCAL_STAGES` lists:

```sh
# .env on the server: transcribe and read here too, slowly, when the desktop is off
HIKIDASHI_LOCAL_STAGES=asr,ocr
# or no worker container at all, so the worker image is never built here
COMPOSE_FILE=docker-compose.yml:docker-compose.server-only.yml
```

Unraid's Compose Manager never reads `COMPOSE_FILE`: it passes its own `-f`.
There, the stack folder picks the layout, as in
[Installing on Unraid](install-unraid.md#the-alternative-compose-manager).

Whatever the server does not run waits for the desktop, and new reels are
searchable meanwhile: by caption at once, and semantically too, as embedding
goes ahead from captions while no worker can transcribe or tag, and again as
each transcript and summary arrives. List them too, with
an `ollama` here (`--ai`), for speed with the CPU profile's model: whichever
worker claims a reel first does it. Under uv, where one worker runs every
stage, `HIKIDASHI_LOCAL_STAGES=enrich,embed` leaves the rest to the desktop.

If a proxy in front signs people in (`HIKIDASHI_AUTH=proxy`), let
`/api/worker/` through it: the worker has a token, not a login. Use HTTPS if
the worker reaches the server over anything but your own network.

### The desktop, with uv

```sh
git clone … && cd hikidashi
uv sync --extra worker --extra cuda   # Whisper and the GPU libraries
uv run hikidashi worker --server http://nas.lan:8080
  worker token for http://nas.lan:8080:      # typed or pasted; nothing shows
```

Each setting is a flag or its variable, and the flag wins:

| Flag | Variable | Default |
|---|---|---|
| `--server URL` | `HIKIDASHI_SERVER` | none: work on the database here |
| `--profile auto\|cpu\|cuda` | `HIKIDASHI_PROFILE` | `auto`: the GPU if one is visible, else the CPU. `cuda` insists on it |
| `--ollama-url URL` | `OLLAMA_URL` | `http://localhost:11434`, this machine's Ollama |
| *(none)* | `HIKIDASHI_WORKER_TOKEN` | asked for at the terminal, hidden |

The token is never a flag, so it stays out of shell history and the process
list, and the worker never logs it. With no terminal to ask at, as under
Docker or a service, set `HIKIDASHI_WORKER_TOKEN`, or the worker refuses to
start.

For tag and recipe alone, a bare `uv sync` is enough: they need only Ollama.
Without `--extra worker`, ASR is reported as unable to run, never failed, and
`--stages tag,recipe` keeps it quiet. Without `cuda`, `auto` runs on the CPU.

It runs until stopped; `--once` does what is ready and exits, and `--stages
asr,ocr` limits it.

<img src="diagrams/remote-worker-terminal.svg" alt="A terminal running hikidashi worker with --server and --once on the demo library: it loads Whisper, then claims batches from the server in rounds of asr, ocr, tag and recipe, logging what each batch did, until nothing is left." width="100%">

A real run, `--once` on the [demo library](development.md#recording-the-demo)
on a CPU, with long waits shortened. Each line is one batch: how many reels it
claimed, how many gave a result (`done`), how many had nothing to find (music
with no speech, a photo with no text), and how much media it downloaded first.

**By hand.** A desktop needn't be left working. With `HIKIDASHI_SERVER` set,
the stage commands claim from the server too, and exit when it has nothing
left for them. They take no flags: the profile and Ollama come from their
variables, and the token is asked for as above:

```sh
./hikidashi transcribe     # hikidashi worker --once --stages asr
./hikidashi tag            # the same for tag; ocr and recipe too
./hikidashi pipeline       # asr, ocr, tag and recipe, in order
```

These run under the name `<name>-once`, so one never takes the reels of an
always-on worker on the same machine. The server's own commands (import,
enrich, embed, places, user, export) refuse, rather than act on an empty
database here, and `./hikidashi status` names the server instead. Its name in status is the host's name
(`HIKIDASHI_WORKER_NAME` or `--name`), which must differ from every other
worker on the server. It keeps no database; `data/` holds only its models and
the media cache.

### The desktop, with Docker

`docker-compose.worker.yml` runs just the worker image, with no web stack and
no database:

```sh
HIKIDASHI_SERVER=http://nas.lan:8080 HIKIDASHI_WORKER_TOKEN=<the token> \
  docker compose -f docker-compose.worker.yml up -d
```

Tag and recipe use the desktop's own Ollama, a native install or
`ollama/ollama`, at `HIKIDASHI_OLLAMA_URL` (unset: the Docker host's,
`http://host.docker.internal:11434`). It pulls the published cuda image
(`ghcr.io/spanthetree/hikidashi-worker-cuda`) unless
`HIKIDASHI_WORKER_IMAGE=hikidashi-worker` asks for the smaller CPU one. Set
`HIKIDASHI_VERSION` to the server's release, such as `1.2.3`; unset, it is
`latest`. With `--build` it builds from the checkout instead, the cuda image
unless `HIKIDASHI_EXTRAS` is set to nothing. For the GPU itself, it needs the NVIDIA container
toolkit and the `deploy` block uncommented, as for the single-host setup
([#64](https://github.com/spanthetree/hikidashi/issues/64)); without them, `auto` runs on the CPU. A container has no terminal to
ask for the token at, so `HIKIDASHI_WORKER_TOKEN` is required here.

By hand, with no container left running:

```sh
docker compose -f docker-compose.worker.yml run --rm worker hikidashi pipeline
```

### Layouts

Which image runs where, for the setups this was built for. The images are in
[the pipeline](pipeline.md#in-docker).

| Setup | Containers | Stages |
|---|---|---|
| One host | `app`, `server-worker`, `worker`, `ollama` (`--ai`), all from `docker-compose.yml` | everything, here |
| Always-on box, tagging left to a desktop (Unraid) | `app`, `server-worker`, `worker` with `HIKIDASHI_LOCAL_STAGES=asr,ocr`; no `ollama` | enrich and embed; ASR and OCR on its CPU while the desktop is off |
| Always-on box, everything heavy on a desktop | `app` and `server-worker` only: `COMPOSE_FILE=docker-compose.yml:docker-compose.server-only.yml` | enrich and embed |
| GPU desktop | `docker-compose.worker.yml` (the cuda image by default) and its `deploy` block, and Ollama; left running, or run by hand | asr, ocr, tag and recipe for either server above |

## Measured

On a copy of the library, a server and a remote worker on the same desktop
(Ryzen, CPU profile), over HTTP:

| | |
|---|---|
| Server memory, no stages in it, search embedder loaded | ~835 MB |
| Search, server only | 10–56 ms |
| 6 reels transcribed remotely (`distil-small.en`, CPU) | 29 s, 68 MB downloaded |
| The same 6 tagged and checked for recipes (Ollama, `qwen2.5:3b`) | 74 s |
| Requests for that ASR batch of 6 | 2 claims, 2 results, 6 downloads, a check-in per batch |

Still to measure on the real split: the server's memory and CPU on the NAS,
the transfer over the LAN for a full pass, and GPU throughput against running
on one machine.
