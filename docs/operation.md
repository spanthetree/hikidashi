# Operation

How Hikidashi behaves at runtime: a reel's life through the stages, the worker
loop, the remote worker protocol, a search, an import and a signed-in request.
The parts and where they run are in [architecture.md](architecture.md); each
stage's own detail is in [the pipeline](pipeline.md).
[Back to the README](../README.md)

- [Reel lifecycle](#reel-lifecycle)
- [The worker loop](#the-worker-loop)
- [The remote worker protocol](#the-remote-worker-protocol)
- [Hosted AI](#hosted-ai)
- [A search request](#a-search-request)
- [Import](#import)
- [Sign-in and requests](#sign-in-and-requests)

## Reel lifecycle

<img src="pipeline.svg" alt="A reel moving through the pipeline: imported, enrich, asr, ocr and tag, then recipe and embed side by side, then searchable. At asr it fails once, is counted in job_attempt as attempt 1 of 3, waits out its backoff and is claimed again, and this time succeeds." width="100%">

### The stage graph

Generated from the stage registry in `app/jobs/queue.py`; an arrow is "waits
for". Each node shows the `item` column the stage stamps with its version.

<!-- make_diagrams: stage-graph -->
```mermaid
flowchart LR
  import(["import<br/><small>caption, FTS5</small>"])
  subgraph server["server image · HIKIDASHI_LOCAL_STAGES=enrich,embed"]
    enrich["enrich<br/><small>fetch_v</small>"]
    embed["embed<br/><small>embed_v</small>"]
  end
  subgraph worker["worker image, here or remote · asr,ocr,tag,recipe"]
    asr["asr<br/><small>asr_v</small>"]
    ocr["ocr<br/><small>ocr_v</small>"]
    tag["tag<br/><small>tag_v</small>"]
    recipe["recipe<br/><small>recipe_v</small>"]
  end
  import --> enrich
  enrich --> asr
  asr --> ocr
  ocr --> tag
  tag --> recipe
  tag --> embed
  enrich -. "when no worker can run asr, ocr, tag" .-> embed
```
<!-- /make_diagrams -->

Import is not a stage: it writes `item` and `user_item` directly, and the
caption is searchable from that moment. The hashtag pass is not a stage either:
it is instant and needs no lease, so a local worker runs it before each round
of tagging, and the server runs it when a remote worker asks for reels to tag.
Places are the server's own background job, outside the queue.

OCR waits for ASR because it reads only reels with no speech: a transcript
means there is nothing to OCR. The dashed edge is the embedder going ahead from
captions when no running worker can transcribe, read or tag, so a server whose
heavy stages wait for a desktop still puts new reels in semantic search.

### A reel at one stage

Every stage keeps the same small state machine for each reel, made of the
stage's own pending query and the three job tables.

```mermaid
stateDiagram-v2
  [*] --> waiting : imported, or the stage's version bumped
  waiting --> ready : every upstream stage is done with it, or gave up on it
  ready --> leased : a worker claims it (job_lease, 5 min)
  leased --> leased : check-in every 30 s renews the lease
  leased --> done : released, and no longer pending
  leased --> ready : released uncounted (the stage could not run, or a clean stop)
  leased --> cooling : released still pending, or the lease expired. attempts += 1
  cooling --> ready : after 60 s × 2^(attempts − 1)
  cooling --> failed : attempts reach 3
  failed --> ready : Retry on the Pipeline page, --retry-failed, or a version bump
  done --> waiting : the stage's version bumped, or (embed) the text's digest changed
  done --> [*]
```

| State | Where it is | Status calls it |
|---|---|---|
| waiting | in the stage's pending query, and an upstream stage's too | waiting |
| ready | pending, no live lease, not cooling, under the cap | ready |
| leased | a `job_lease` row with `expires_at` in the future | working |
| cooling | a `job_attempt` row under the cap, inside its backoff | waiting |
| failed | a `job_attempt` row at `MAX_ATTEMPTS` (3) for the current version | failed |
| done | no longer in the pending query | (not counted) |

**What decides a re-run.** A stage's work is the rows stamped below its
current version (`fetch_v < FETCH_V`, `asr_v < ASR_V`…), so bumping a version in
code re-runs that stage on everything, and nothing else. Attempts are kept per
version, so a bump also starts every count again. The embedder adds a digest:
`item_vec.digest` is a hash of the text it embedded, so a new summary,
transcript or caption re-embeds that reel alone. That is also how a reel
embedded from its caption alone is embedded again when its transcript and
summary arrive. The embed stage also fingerprints each reel's frames for
finding reposts (`phash_v < PHASH_V`, [app/dupes](../app/dupes/__init__.py)):
three ffmpeg frames, about a quarter of a second a reel on CPU, then the
library is regrouped. That needs only ffmpeg, so it runs before the embedder
loads, and still runs when the embedder will not.

**`item.state`** is coarser and older than the queue. It records how far a reel
has come (`imported`, `fetched`, `transcribed`, `tagged`) and only moves
forward. `failed` means the post itself is gone (private, deleted, or a fetch
failed for good): the reel stays as a metadata-only row, because the save is
still a record. A failure at any other stage is `item.fail_stage` and
`fail_reason`, and later stages go on without what it would have made.

A failed fetch is not a `job_attempt` failure, so the stage table never
counts it. The Pipeline page lists those reels on their own, above the
stages' failures, as the sidebar's "failed" counts them. Each one is either
permanent (restricted, deleted, login only: `fetch_v` is stamped, and a
retry can't help) or transient, tried up to `enrich.worker.MAX_ATTEMPTS`
times. Retry there gives only the transient ones that ran out of tries
another round.

## The worker loop

`hikidashi worker`, as `./hikidashi start` and each worker container run it
(`app/jobs/worker.py`).

```mermaid
flowchart TD
  start(["start"]) --> reclaim["reclaim: leases still under this name<br/>are a dead run's; take back, count"]
  reclaim --> round
  subgraph round["a round, in pipeline order"]
    direction TB
    next["each stage in<br/>HIKIDASHI_LOCAL_STAGES"] --> backing{"backing off<br/>this stage?"}
    backing -- yes --> next
    backing -- no --> claim["claim a batch<br/>enrich 6 · asr 4 · ocr 16 · tag 8 · recipe 8 · embed 32"]
    claim --> run["run the stage on exactly those reels"]
    run -- "finished or failed" --> release["release: done ones cleared,<br/>pending ones counted"]
    run -- "Unavailable: no Ollama, no tesseract,<br/>model won't load" --> giveback["release uncounted;<br/>retry stage in 1 min, doubling to 1 h"]
    release --> next
    giveback --> next
  end
  round --> unload["unload Whisper or the embedder<br/>if unused for HIKIDASHI_UNLOAD_AFTER (300 s)"]
  unload --> any{"anything worked on?"}
  any -- yes --> round
  any -- no --> idle["wait 5 s, doubling to 60 s,<br/>or until data/run/worker.wake is touched"]
  idle --> round
  hb(["heartbeat thread, every 30 s"]) -.-> renew["renew leases, check in:<br/>stages, doing, problems"]
  stop(["SIGTERM or Ctrl-C"]) -.-> out["check out: every lease back, uncounted"]
```

- **Readiness by stage dependency.** A claim is the stage's pending reels in
  its own order, less those an upstream stage still has to do, those under a
  live lease, those cooling and those at the cap. The claim runs in a
  `BEGIN IMMEDIATE` transaction, so two workers' claims never interleave.
- **Leases and their expiry.** A claim lasts 5 minutes (`LEASE`) and is renewed
  by every 30-second check-in (`HEARTBEAT`), from a thread on its own
  connection, so a long transcription keeps its reels. A lease past its time
  is taken back by the next claim for that stage, and counts as an attempt:
  its worker died holding it. A worker not seen for 2 minutes (`STALE`) shows
  as **lost**; one that checked out shows as **stopped**.
- **Idle and wake-up.** A round with nothing to do waits 5 s, then 10, up to a
  minute. An import touches `data/run/worker.wake`, and every worker on the
  host checks its mtime once a second while it waits, so new reels start
  within a second rather than a minute.
- **Unloading models.** Whisper and the embedder load on first use and are
  dropped after `HIKIDASHI_UNLOAD_AFTER` seconds (300) with no work for them,
  since each holds a gigabyte or more. Ollama unloads its own.
- **A stage that cannot run** is not the reels' fault: it gives them back
  uncounted, is skipped for a minute, then 2, 4… up to an hour, and is
  reported as a problem at every check-in, which the Pipeline page shows.

<img src="diagrams/leases.svg" alt="Leases: six reels ready for asr. Worker A claims three and worker B the other three. Worker A stops checking in; its lease drains and expires, its three reels return to the pool with one attempt counted, and worker B claims and finishes them." width="100%">

## The remote worker protocol

A worker on another machine runs the same loop with `HttpJobs` in place of
`LocalJobs`: every claim, release and check-in is a request to the server,
which does the same with its own `LocalJobs`. The server stays the only writer
([ADR 0002](adr/0002-workers-write-through-a-job-source.md)). Setup is in
[remote-workers.md](remote-workers.md).

<img src="diagrams/remote-worker.svg" alt="A remote worker's round trip, as a sequence: the desktop claims asr work, the server leases four reels and sends their rows, the desktop downloads the videos it lacks, transcribes them on its GPU, posts one result per reel, and the server checks the versions, the shape and the lease, records them and releases the leases. A check-in every 30 seconds keeps the leases alive." width="100%">

What the desktop's terminal shows during a real run is in
[remote-workers.md](remote-workers.md#the-desktop-with-uv).

An asr result is `{shortcode, transcript}`, and since `ASR_V` 3 may carry
`segments` too: `[[start_ms, end_ms, text], ...]`, when the model timed what
it heard, so a search can open a reel where the words were said
([#44](https://github.com/spanthetree/hikidashi/issues/44)). The server checks
each is two whole numbers and some text before recording any.

```mermaid
sequenceDiagram
  autonumber
  participant D as worker (desktop)
  participant S as server
  participant DB as SQLite

  D->>S: POST /api/worker/reclaim (at start)
  S->>DB: leases under this name: take back, count
  loop every round
    D->>S: POST /api/worker/claim {stage, limit, protocol, versions}
    S->>S: protocol.check: a remote stage? same protocol? same stage version?
    S->>DB: LocalJobs.claim: readiness, leases, attempts cap
    S-->>D: {jobs: [row, ...]}: the columns the stage reads, media paths
    D->>S: GET /api/worker/media/<path>, for each file the cache lacks
    S-->>D: the video or thumbnail
    D->>D: transcribe_one / read / tag_one / read_one, per reel
    D->>S: POST /api/worker/results {stage, profile, results}
    S->>S: protocol.parse: every result's shape, before any is recorded
    S->>DB: record() for each reel still held, drop the rest
    S->>DB: release: done cleared, still pending counted
    S-->>D: {recorded, done, again, failed}
  end
  par every 30 s
    D->>S: POST /api/worker/check-in {renew: true}
    S->>DB: renew leases, job_worker.seen_at
  end
  D->>S: POST /api/worker/check-out (a clean stop)
  S->>DB: every lease back, uncounted
```

What is checked, and what happens when it fails:

| Check | Where | On failure |
|---|---|---|
| a worker token (`hk_wkr_…`) made on the Pipeline page or with `hikidashi token make --worker`, by an admin who is still one. Not an API token, and not `HIKIDASHI_WORKER_TOKEN` from the server's own environment | `worker_auth` | 401 `invalid_token`, and the worker stops. With no worker token made, 404: remote workers are off |
| the stage is one a remote worker may run (`asr`, `ocr`, `tag`, `recipe`) | `protocol.check` | 409, and the worker stops |
| the same `PROTOCOL`, and the same version of each stage (`ASR_V`, `OCR_V`, `TAG_V`, `RECIPE_V`) | `protocol.check` | 409 naming both versions, and the worker stops: run one release on both |
| the profile is `cpu` or `cuda`, or a hosted worker's `provider:model` | `worker_results` | 422 |
| every result's shape: types, lengths, ranges; a tag the taxonomy lacks is no tag; a tag's game answer is the model's words (kind, title, confidence, others, the check's title), each bounded | `protocol.parse` | 422 for the whole batch, before anything is recorded |
| the worker still holds the reel's lease | `protocol.record` | that result is dropped; another worker has the reel |
| a game is applied only if the reel's text, as the server has it, names it | `tagging.worker.record` | it is kept as a guess to confirm instead: a worker cannot make a title sure |

Embedding is not on this list because it never runs remotely: search embeds
queries on the server, and a vector from a different model would be wrong
without any error to say so ([ADR 0003](adr/0003-embedding-is-server-only.md)).

### When the desktop disappears mid-batch

```mermaid
sequenceDiagram
  participant D as worker (desktop)
  participant S as server
  participant W as another worker
  D->>S: claim asr, 4 reels
  S-->>D: rows (leases expire in 5 min)
  D->>S: GET media
  Note over D: powered off mid-transcription
  Note over S: no check-in: after 2 min the worker shows as lost
  Note over S: after 5 min the leases expire
  W->>S: claim asr
  S->>S: expired leases taken back,<br/>one attempt counted each (job_attempt)
  Note over S: the 4 reels cool for 60 s, then are ready
  W->>S: claim asr
  S-->>W: the same 4 reels
  Note over D: powered back on
  D->>S: POST /api/worker/reclaim
  S-->>D: taken: 0 (nothing left under its name)
```

Results the desktop finished before it went are already recorded: each batch
reports as it ends. A result that arrives late, after another worker took the
reel, is dropped, as the lease check in the table above says. While the server
is unreachable, the worker waits, trying again from 5 s up to a minute, and
its leases run out as if it had died.

## Hosted AI

A hosted API can do the four worker stages instead of a worker:
transcription, on-screen text, tags with the game pass, and recipes. Each
user can have their own, and the server can have one. Nothing is sent
anywhere unless someone has switched one on
([ADR 0019](adr/0019-hosted-ai-behind-a-switch.md)). Setting it up is on
**Account → AI providers**, or with `hikidashi ai`.

### The switch

```mermaid
stateDiagram-v2
  [*] --> Off
  Off --> On: turned on (all four stages set, keys, a budget)<br/>backlog: process it, or leave it to workers
  On --> Off: turned off: in-flight requests finish, the rest go back to workers
  On --> Off: by itself, with the reason:<br/>key rejected · quota used up · budget reached
  On --> Waiting: a rate limit, a free tier's daily cap, an outage
  Waiting --> On: the wait is over (Retry-After, or backoff to 15 min)
```

- **On**, the API claims new reels as soon as they are fetched. A worker that
  checks in later finds none of them. **Off**, workers get everything, as
  before.
- **One choice each**, on Account → AI providers, for your own library:
  **System default**, **Hosted AI** (your own provider and key), or
  **Workers only**. Admins have the same choice for theirs.
- **The system default**, on the Pipeline page (admins): workers, or hosted
  AI with the server's own provider and key, run as the server's API.
- The server's API takes a reel if at least one saver keeps the system
  default. A user's own hosted AI is offered their reels first, and the
  server's waits a minute (`GRACE`) after it first finds a reel ready.
- Env (`HIKIDASHI_*_PROVIDER`, keys, `HIKIDASHI_HOSTED_BUDGET`) sets the
  server's API and locks those fields on the page, but never turns it on.

### A claim

Each switched-on API is a claimer in the worker that runs `enrich`: the
server's own (`server-worker` in Docker). Its name in the queue is
`api:server` or `api:user:<id>`.

```mermaid
sequenceDiagram
  autonumber
  participant H as server-worker (api:user:2)
  participant DB as SQLite
  participant P as provider
  loop every round (10 s idle)
    H->>DB: switch on? budget left? eligible reels (the loose rule)
    H->>DB: lease asr, ocr, tag, recipe for each, at once
    loop each reel, stages in pipeline order
      H->>DB: still needed? (its own pending query)
      H->>P: audio / frames / text
      P-->>H: transcript / lines / JSON, checked against the schema
      H->>DB: the stage's record(), stamped provider:model
      H->>DB: hosted_usage: tokens or audio seconds, estimated cost
    end
    H->>DB: release every lease, uncounted
  end
```

A reel a worker already transcribed gets only its remaining stages. OCR runs
only where it does today (no transcript), and recipes only on candidates.
Leases are renewed every 30 s while a reel runs, and a crash lets them expire,
as for any worker: at most the requests in flight are sent again.

### When something fails

| What | Example | What happens |
|---|---|---|
| The provider can't be used | 401/403, `insufficient_quota`, a model the key can't use | The switch turns off, with the reason on Account, the Pipeline page and `hikidashi status`. Its reels go back to workers. Nothing is recorded against any reel. The owner turns it back on; a new month doesn't |
| The monthly budget is reached | this month's estimated spend ≥ the budget | The same, reason "monthly budget reached" |
| Not now | a 429 longer than 30 s, a free tier's daily cap (`per day`), a 5xx twice over, unreachable | Reels back to workers uncounted; the API waits `Retry-After`, or from 30 s doubling to 15 min |
| This one reel | a 400 on this file, a reply that doesn't fit the schema, a refusal, a reply cut short, audio ffmpeg can't read | An API error in `api_reel`, never a worker attempt, never `fail_stage`. After three, that API leaves the reel to workers, and the Pipeline page counts it |

Short 429s (≤ 30 s) are waited out inside the request, and a 5xx is tried
twice more, so only what outlasts that reaches the table.

### A hosted worker on another machine

`hikidashi worker --backend hosted --server URL --for me|all` is the server's
API run elsewhere, with **its own key** from its own env. It authenticates
with a worker token, which is an admin's, so it takes what the server's API
would: the token owner's reels (`me`) or every reel a saver relies on the
server's API for (`all`), after the same grace period. It needs
`HIKIDASHI_HOSTED_BUDGET`, and enforces it against the spend the server
recorded from its reports.

Protocol 2 ([remote worker protocol](#the-remote-worker-protocol)) adds:

| Route | |
|---|---|
| `POST /api/worker/hosted/claim {scope, limit}` | leases reels for all their stages; returns `{reels, spent}` |
| `POST /api/worker/hosted/rows {stage, shortcodes}` | each stage's row as the worker gets there, for reels it holds |
| `POST /api/worker/results` | as before, with a `provider:model` profile, `languages` and `usage` |
| `POST /api/worker/hosted/error {shortcode, reason}` | an API error: its stages back, uncounted |

Running it is switching it on. Ctrl-C or `docker stop` finishes the reel in
hand and gives the rest back.

### From the command line

Everything the page does, `hikidashi ai` does: on the server's host it opens
the database (`--user NAME`, or `--server-api`), and from anywhere else it
goes through the same routes with `--server URL` and an API token with
`read:ai` and `write:ai` (from `HIKIDASHI_API_TOKEN`, or asked).

```sh
hikidashi ai show [--json]
hikidashi ai set all --provider groq            # then tag/recipe/ocr: --provider openai
hikidashi ai key set groq                       # a hidden prompt, or --stdin; never an argument
hikidashi ai budget 5
hikidashi ai test                               # one tiny request per stage
hikidashi ai on --new-only                      # shows the backlog and estimate first
hikidashi ai on --server-api --for all --backlog --yes
hikidashi ai status --all
hikidashi ai usage --month 2026-11
hikidashi ai off
```

Exit codes: 0 OK, 1 failed (Test connection rejected the key), 2 usage, 3 not
allowed (not an admin, a missing scope, a setting locked by env, stages not
all set, no budget).

### Keys and settings

Settings are in the `setting` table, per user and (with `user_id` NULL) for
the server. Keys are encrypted with Fernet under `HIKIDASHI_SECRET_KEY`, or
`data/secret.key` (made on first use, mode 0600). A backup of all of `data/`
holds both and can read the keys; a copy of the database alone can't. If
`secret.key` is lost, saved keys read as "cannot be read; enter it again",
and nothing else is lost. Every change, and every switch on or off, is in
`ai_audit` (who, what, when, never a key).

Env, for the server's API (and a hosted worker's):

```env
HIKIDASHI_ASR_PROVIDER=groq          # openai, groq, mistral, compatible, …; mixing allowed
HIKIDASHI_ASR_MODEL=                 # optional; the preset's default
HIKIDASHI_OCR_PROVIDER=openai
HIKIDASHI_OCR_MODEL=
HIKIDASHI_LLM_PROVIDER=openai        # tags, game pass, recipes
HIKIDASHI_LLM_MODEL=
HIKIDASHI_ASR_LANGUAGE=              # force one, as ja; else detected
GROQ_API_KEY=                        # one per provider, or <NAME>_FILE for Docker secrets
OPENAI_API_KEY=
HIKIDASHI_OPENAI_BASE_URL=           # an OpenAI-compatible server: Speaches, vLLM, LiteLLM, …
HIKIDASHI_OPENAI_API_KEY=
HIKIDASHI_HOSTED_BUDGET=5            # USD a month; required to turn it on
HIKIDASHI_SECRET_KEY=                # optional; else data/secret.key
```

### Providers

Checked 2026-11-12, against each provider's own pages where they could be
read. Free tiers, model names, prices and data terms change often: check
before relying on one. Presets marked "chosen" have no default models, since
their names couldn't all be checked; pick one from the provider's own list
(the page's model box, or `hikidashi ai models <provider>`).

| Preset | Stages | Default models | Free tier | What it does with what is sent |
|---|---|---|---|---|
| `openai` | all four | `gpt-transcribe` ($0.0045/min), `gpt-6-luna` ($0.10 / $0.01 cached / $0.50 per 1M tokens) | none | not used for training by default; kept up to 30 days for abuse monitoring |
| `groq` | all four | `whisper-large-v3-turbo`, `openai/gpt-oss-20b` (strict schema), `meta-llama/llama-4-scout-17b-16e-instruct` (vision) | yes: Whisper at 20 a minute, 2,000 a day, 7,200 audio s an hour, 28,800 a day, 25 MB files | does not train on API data |
| `mistral` | all four (Voxtral for speech) | chosen | Experiment plan | paid: no training; check the free plan's terms |
| `gemini` | ocr, tag, recipe (no transcription endpoint) | chosen | yes, generous | **free tier: used to improve Google's products, and may be read by people**; paid: not |
| `openrouter` | ocr, tag, recipe | chosen (`:free` models) | small daily limits | the upstream provider's; free models may log |
| `cerebras` | tag, recipe | chosen | rate-limited per minute and day | Cerebras's terms |
| `github` | ocr, tag, recipe | chosen | low per-minute and per-day limits, with a GitHub account | GitHub Models' terms |
| `cloudflare` | tag, recipe (its Whisper is not on the OpenAI-compatible layer) | chosen; base URL `https://api.cloudflare.com/client/v4/accounts/<id>/ai/v1` | a daily allowance | does not train on inputs |
| `huggingface` | ocr, tag, recipe | chosen | monthly credits | the routed provider's |
| `ollama` | on-screen text, tags, recipes (no speech to text) | `qwen2.5:7b`, as the GPU worker runs, and `qwen2.5vl:7b` for on-screen text; others suggested in the model list; your base URL, as `http://host.docker.internal:11434/v1`. No key | your machine | nothing leaves your network. Also answers Ask, and translates corrected recipes, with the switch off (ADR 0021) |
| `compatible` | all four | chosen; your base URL | your server | your server |

**OpenAI's older models shut down soon:** `whisper-1`, `gpt-4o-transcribe`
and `gpt-4o-mini-transcribe` on 2027-02-26, and `gpt-4o-mini` and
`gpt-4.1-mini` on 2027-01-14. `gpt-transcribe` returns no segment timings, so
**the music guard is weaker on it**: only `is_noise` runs over the whole clip,
and a music-only reel may get lyrics. Groq's Whisper models return segments,
and the guard drops those with a `no_speech_prob` over 0.6. The settings page
warns when the chosen transcription model gives no segments.

Sources: OpenAI's [deprecations](https://developers.openai.com/api/docs/deprecations),
[pricing](https://developers.openai.com/api/docs/pricing) and
[gpt-transcribe](https://developers.openai.com/api/docs/models/gpt-transcribe);
Groq's [rate limits](https://console.groq.com/docs/rate-limits) and
[structured outputs](https://console.groq.com/docs/structured-outputs);
Google's [Gemini API terms](https://ai.google.dev/gemini-api/terms) and
[OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai);
Cloudflare's [OpenAI compatibility](https://developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility);
GitHub's [GitHub Models](https://github.blog/ai-and-ml/llms/solving-the-inference-problem-for-open-source-ai-projects-with-github-models/).

### Cost

Every request is recorded in `hosted_usage` with an estimated cost from the
dated price table in `app/ai/usage.py`. A model not in it costs nothing there
and is named as unpriced, so a budget can't be relied on for it. On all
users, the server's spend is split between the savers of each reel who rely on
it. The backlog estimate, shown when a switch goes on, comes from each reel's
duration (transcription), six frames (on-screen text), its text so far
(tags), and a range for recipes, which run only on candidates.

### Not yet measured

These need real keys and real reels, so they wait for the owner of a
library ([#116](https://github.com/spanthetree/hikidashi/issues/116)): the
music guard on the music-only reels, on Groq and on `gpt-transcribe`; the
tag, recipe and game evaluations against `qwen2.5:3b`/`7b`; and hosted OCR
against tesseract on recipe cards, Japanese text and stylised captions. To
run them on a copy of a library, switch an API on for it with `hikidashi ai`,
run `hikidashi worker` until the queue is empty, then the stages'
`--eval`.

## A search request

`GET /api/items?q=…` (`app/search/__init__.py`, `app/api/main.py`). The numbers
and what was tried are in [search.md](search.md).

<img src="diagrams/search-fusion.svg" alt="Search fusion for the query potato soup: a keyword list and a semantic list fill in, their reels merge into one list by reciprocal rank fusion, and the one reel containing the exact phrase potato soup moves from last to first." width="100%">

```mermaid
flowchart TD
  q(["query"]) --> embedq["embed the query<br/>embeddinggemma, on the server"]
  q --> kw["keyword_hits<br/>FTS5: every word, bm25<br/>then creators, hashtags, your tags and games<br/>then CJK words as substrings"]
  embedq --> sem["semantic_hits<br/>summary vector ≥ 0.27, top 24<br/>transcript chunk ≥ 0.5, top 12, less 0.1"]
  kw --> none{"no reel has<br/>every word?"}
  none -- yes --> cw["content_word_hits<br/>distinctive words only"]
  cw --> empty{"nothing in<br/>any list?"}
  sem --> empty
  empty -- yes --> loose["content_word_hits, loose<br/>any two distinctive words"]
  loose --> fuse
  kw --> fuse["reciprocal rank fusion<br/>Σ weight / (60 + rank)<br/>keyword 1 · semantic 1 · content words 0.5"]
  sem --> fuse
  cw --> fuse
  fuse --> pin["exact phrase first<br/>stable sort: each tier keeps its fused order"]
  pin --> out(["results, sorted by Best match"])
  scope["library.mine(user)<br/>inside every list"] -.-> kw
  scope -.-> sem
  scope -.-> cw
```

| Constant | Value | Why |
|---|---|---|
| `K` | 60 | the usual RRF constant. Sweeping 10–60 moved nDCG by under 0.01 |
| `MIN_SIMILARITY` | 0.27 | every reel is a little similar to every query (the median is near 0.1, on-topic reels 0.3–0.5). Without a floor any search returns the library. 0.36 was quieter but cut the reels of a query whose reels never name what it asks for, at 0.27–0.30 |
| `MAX_VECTOR_HITS` | 24 | a vague query is near everything a little, and past this the tail is noise. A fixed count, not relative to the best match, so one fluke at the top can't cut the real answers |
| `MIN_CHUNK_SIMILARITY` | 0.5 | below it, stretches of voiceover outrank reels that are about the query |
| `CHUNK_HANDICAP` | 0.1 | one sentence of speech matching is weaker evidence than the whole reel being about it |
| `CONTENT_WEIGHT` | 0.5 | measured flat from 0.25 to 0.75; at 1, a place query brings up every reel in the place |
| `COMMON` | 0.1 | a word in more than a tenth of the library ("best") doesn't count as distinctive |

The index (FTS5 `item_fts`) holds the caption, transcript, on-screen text,
summary, tag, cleaned hashtags, keywords and, since [#117](https://github.com/spanthetree/hikidashi/issues/117), the game a reel is
sure to show. A guess is not indexed: it is not yet the reel's game. A game
you named is yours, so like your tag it is matched in the second tier rather
than indexed. The filters (`tag`, `game`, `hashtag`, each repeatable, and
`collection`) intersect: a reel must have every one.

Every list is filtered to your library inside its query, not after, so the
caps on the vector lists are spent on reels you can see. Without the embedder,
or while it cannot load, search is keywords only, and the content-word list
settles for any two of the distinctive words when no reel has them all. With
the embedder, it settles for two only when the keyword, content-word and
vector lists all come back empty, so a search that keyword-only search would
answer never shows an empty page; a negative stays as quiet as it is without
vectors ([#57](https://github.com/spanthetree/hikidashi/issues/57)). There is no reranking step:
cross-encoders were measured at +0.02 nDCG for 10–250× the latency, and
rejected ([ADR 0011](adr/0011-hybrid-search-rank-fusion.md)).

## Import

`POST /api/import` from the web, or `./hikidashi import` (`app/importer`).

<img src="diagrams/shared-cache.svg" alt="Shared cache, separate libraries: user 1 imports reels a, b and c, making three item rows, three downloads and three user_item rows. User 2 imports b, c and d: b and c are already there, so only d is added and downloaded, and user 2 gets three user_item rows of their own." width="100%">

```mermaid
sequenceDiagram
  actor U as you
  participant A as server (/api/import)
  participant I as importer
  participant DB as SQLite
  participant W as workers

  U->>A: upload the export ZIP
  A->>A: save to data/imports/<user id>/
  A->>I: import_export(zip, user_id)
  I->>DB: import_run row: name, SHA-256 (a repeat is flagged, and harmless)
  I->>I: parse saved_posts.json: current shape,<br/>then the older one, then a permalink sweep
  I->>I: repair mojibake, read shortcodes from permalinks
  loop each saved post, then each collection's embedded posts
    I->>DB: upsert item: insert, or fill only what is empty
    I->>DB: upsert user_item: insert, or an earlier saved_at
  end
  I->>DB: collection rows for this user
  I->>DB: COMMIT: one transaction, all or nothing
  Note over DB: FTS5 triggers index the captions: searchable now
  A->>W: touch data/run/worker.wake
  A->>A: wake the places locator
  A-->>U: seen, inserted, updated, unchanged, collections
  W->>DB: claim: enrich has every new reel pending
```

An import only adds or widens: an existing `item` row has its empty columns
filled and nothing overwritten, a `user_item` row only moves its save time
earlier, and no stage stamp is reset. So re-importing the same export changes
no rows, and a newer export adds only what is new. Every new reel is
`fetch_v = 0`, so it is pending for enrich, and readiness carries it down
from there. The counts are of your library: a reel someone else saved first is
still `inserted` for you, though no new `item` row or download was made.

### One reel by its link

`POST /api/items {"url": …}` (#52) is the share sheet's way in, through the
`/share` page that the web app manifest names as its share target, and the
**Add by link** field's, and a script's with a `write:library` token. It reads
the first Instagram post link in the text, upserts the `item` the same way an
import does (a new one is `imported` with just its permalink), and adds a
`user_item` saved now, with `added_via = 'link'` and no `first_seen_in`. A
repeat is a no-op that answers `added: false`. Then it touches the wake file,
and the workers fetch it like any new reel. A later export finds the row and
only pulls `saved_at` back to the real save time.

The service worker that makes the app installable caches the app shell only
(the hashed build, the icons, `/`). It never answers for `/api/` or `/media/`,
and pages come from the network first, so a new release is never hidden
behind an old `index.html`.

## Sign-in and requests

`HIKIDASHI_AUTH=local`. Under `none`, the session steps are skipped and the
user is always user 1. Under `proxy`, the user comes from the trusted header
instead of the cookie. The scoping is the same in all three modes.

```mermaid
sequenceDiagram
  actor B as browser
  participant M as middleware
  participant R as route
  participant DB as SQLite

  B->>M: POST /api/auth/login {username, password}
  M->>M: same origin? (Sec-Fetch-Site, Origin)
  M->>R: login
  R->>R: throttle: 10 failures per username or address in 15 min
  R->>DB: user by name, argon2 verify
  R->>DB: session row: SHA-256 of a random token, 30 days
  R-->>B: Set-Cookie hk_session (HttpOnly, SameSite=Lax, Secure on HTTPS)

  B->>M: GET /api/items/ABC123 (cookie)
  M->>R: item_detail
  R->>DB: session by SHA-256(cookie): user 7, and slide the expiry
  R->>DB: SELECT … WHERE shortcode = ? AND library.mine(7)
  alt in user 7's library
    R-->>B: 200, the reel
  else not saved by user 7, or no such reel
    R-->>B: 404 "No reel with that shortcode in this library."
  end

  B->>M: DELETE /api/items/ABC123 from evil.example
  M-->>B: 403: requests from other sites are refused
```

A 404 rather than a 403 is the point: a 403 would tell you that someone else
has saved that reel. The same check guards `/media/<path>`, so a thumbnail URL
copied from someone else's library is a 404 to you too. Admin routes (users,
invites, everyone's tokens, the Pipeline page) are a 403 for anyone else,
because they are about the server, not a library.

### From a script: an API token

In `local` and `proxy` mode, anything that is not a browser brings an API
token, made on Your account or with `hikidashi token make`
([ADR 0013](adr/0013-standard-api-and-worker-tokens.md)).

```mermaid
sequenceDiagram
  participant S as script
  participant R as route
  participant DB as SQLite

  S->>R: GET /api/items (Authorization: Bearer hk_api_…, no cookie)
  R->>R: well formed? prefix, 30 characters, CRC32
  R->>DB: api_token by SHA-256, not expired, owner not disabled
  R->>DB: last_used_at, at most once a minute
  alt kind is worker
    R-->>S: 401 invalid_token: a worker token opens /api/worker/* only
  else no scope for this: read:library for GET, write:library otherwise
    R-->>S: 403 insufficient_scope, scope="write:library"
  else
    R->>DB: SELECT … AND library.mine(owner)
    R-->>S: 200, the owner's reels
  end
```

| Check | Where | On failure |
|---|---|---|
| a session cookie first (`local`), or the trusted proxy's header (`proxy`); a token only when neither names anyone | `current_user` | the cookie or header wins, and the token is not looked at |
| the token is well formed, known, not expired, and its owner not disabled | `accounts.find_token` | 401 `invalid_token` |
| an API token, not a worker token | `from_token` | 401 `invalid_token` |
| `read:` for `GET` and `HEAD`, `write:` otherwise, of `library` on library routes and `admin` on admin routes | `library_user`, `admin` | 403 `insufficient_scope`, naming the scope |
| `admin` scopes count only while the owner is an admin, every request | `Token.grants` | 403, as if the scope were missing |
| a password, signing out everywhere, making or revoking a token | `by_session` | 403: a token cannot |

The `/api/stats` worker summary and exporting someone else's library are
admin reach inside library routes: a token needs `read:admin` for them too. A
token is never read from the URL, where logs would keep it.
