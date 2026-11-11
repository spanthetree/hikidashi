# The pipeline

How an Instagram export becomes a searchable library, stage by stage.
[Back to the README](../README.md)

- [How an item flows](#how-an-item-flows)
- [What the export contains](#what-the-export-contains)
- [The worker](#the-worker)
- [Running the stages by hand](#running-the-stages-by-hand)
- [1. Import](#1-import) · [2. Enrich](#2-enrich) · [3. Transcribe](#3-transcribe) ·
  [4. Read on-screen text](#4-read-on-screen-text) · [5. Tag](#5-tag) ·
  [6. Recipes](#6-recipes) · [7. Embed](#7-embed) · [8. Places](#8-places)

## How an item flows

Each reel is a resumable job. Enrichment is long, partly-failing and
interruptible, so per-item state with a reason and an attempt count is what lets
a run be killed and restarted without losing progress.

<img src="pipeline.svg" alt="A reel moving through the pipeline: imported, enrich, asr, ocr and tag, then recipe and embed side by side, then searchable. At asr it fails once, is counted in job_attempt as attempt 1 of 3, waits out its backoff and is claimed again, and this time succeeds." width="100%">

[Operation](operation.md#reel-lifecycle) has the stage graph and the state
machine behind it.

Only **fetch** is expensive to redo: it is network-bound, rate-limited and
depends on posts that may be gone next year. Everything downstream is a pure
function of bytes already on disk, so a change to a cheap stage never
invalidates an expensive one.

## What the export contains

More than its reputation suggests. Per saved post: the permalink, the save
timestamp, the **caption**, the creator's handle and display name, the
**hashtags**, and Instagram's internal `fbid`. Collections embed a full copy of
every member record, so a reel that sits in a collection but is missing from
`saved_posts.json` still arrives complete.

What it does **not** contain: thumbnails, video, or audio. Those come from
enrichment, and transcripts from transcription.

Three things that trip up a naive parser, all handled:

- The shape documented all over the web — `saved_saved_media` wrapping
  `string_map_data` — **is not what ships today**. The current file is a bare
  list of `label_values` records.
- All text is UTF-8 double-encoded as latin-1, so every caption arrives as
  mojibake and is repaired on import.
- The per-record `Title` field is present on every record and is **always
  empty**. The creator handle lives in a nested `Owner` group; a parser that
  trusts `Title` silently builds a library with no creators in it.

## The worker

`./hikidashi start` starts a worker beside the server, and the worker runs
every stage below on new reels as they arrive. A reel imported through the
web, or with `./hikidashi import`, is fetched, transcribed, read, tagged,
checked for a recipe and embedded with no other command. Places are the
server's own job ([8. Places](#8-places)).

```bash
./hikidashi worker                    # what start runs: every stage, until stopped
./hikidashi worker --once             # what is ready now, then exit
./hikidashi worker --stages asr,ocr   # only these
./hikidashi worker --retry-failed     # give up-on reels another go first
```

Each stage's work is the query it already had: what is behind its version
stamp. On top of that, the worker adds three things.

**Leases.** A worker claims a few reels for one stage at a time and renews the
claim every 30 seconds while it works on them. Two workers never hold the same
reel. A claim that is not renewed for five minutes expires, so a worker killed
mid-batch, by `kill -9`, the OOM killer or a power cut, loses nothing: its
reels go back to the pool. A worker that is stopped cleanly (SIGTERM, Ctrl-C,
`./hikidashi stop`) gives them back at once.

**Readiness.** A reel is offered to a stage only once every stage before it is
done with it, or has given up on it. Without this a fresh import would be
tagged from its caption before its transcript existed, and tagged again after.
Transcription and OCR wait for the fetch, the tagger for both, and the recipe
pass and the embedder for the tagger. (Run by hand, the embedder works from
captions until summaries arrive; the worker waits, and embeds once.)

The embedder stops waiting when no worker can tag: none running has `tag` in
its stages, or every one that does reports it cannot run, as with its Ollama
off. A box with no Ollama, or whose tagging desktop is off, would otherwise
never put a new reel in semantic search. The embedder then works from captions,
as by hand, and a reel's summary, once the tagger reaches it, changes what is
embedded and embeds it again. Status and the Pipeline page say so while it
lasts. The recipe pass has nothing to go on without the tagger's model, and
keeps waiting.

**An attempts cap.** A stage that tries a reel and does not finish it, by
failing or by dying with it, counts the try. The reel waits a minute before
its second try and two before its third, since what failed may have been
passing: a file still being written, a GPU out of memory. After three it is
failed at that stage and not offered again, so a reel that crashes the worker
cannot crash it forever. A stage's version bump starts every count again, and
so does a retry: `--retry-failed`, or **Retry** on the Pipeline page. The
later stages then go ahead without what that stage would have made, as they do
for any failure.

A stage that cannot run at all is not the reels' fault and counts nothing: the
tagger with no Ollama, OCR with no tesseract, Whisper that will not load. The
worker gives those reels back, tries the stage again after a minute, then
longer each time up to an hour, and reports it at every check-in until it runs.

### Which stages run where

`HIKIDASHI_LOCAL_STAGES` is the stages a worker runs: `all`, `none`, or a list
such as `asr,ocr`. Under uv it defaults to `all`; each Docker image sets its
own (see [below](#in-docker)). `--stages` overrides it for one run. It is the operator's choice, not something detected: the `cpu` profile
works on any machine, slowly. Readiness does not care where a stage runs, so a
host that runs only the fetch leaves the rest waiting for whoever runs them.
A worker on another machine, such as a GPU desktop, claims through the server
over HTTP instead: see [remote workers](remote-workers.md).

### Idle, and woken

When a round finds nothing ready, the worker waits 5 seconds before looking
again, doubling up to a minute while the queue stays empty. An import wakes it
at once, by touching `data/run/worker.wake`. Whisper and the embedder are
loaded on first use and dropped after five minutes with no work for them
(`HIKIDASHI_UNLOAD_AFTER`), since each holds a gigabyte or more. Ollama
unloads its own models.

### In Docker

There are two images (#86), and Ollama is the upstream `ollama/ollama`:

| Image | Has | Runs by default | `HIKIDASHI_LOCAL_STAGES` |
|---|---|---|---|
| `hikidashi-server` | the web UI and API, yt-dlp, ffprobe, the embedder, sqlite-vec | `uvicorn` | `enrich,embed` |
| `hikidashi-worker` | faster-whisper, ffmpeg, tesseract; no web stack, no embedder | `hikidashi worker` | `asr,ocr,tag,recipe` |
| `hikidashi-worker-cuda` | the worker, plus cuBLAS and cuDNN | `hikidashi worker` | `asr,ocr,tag,recipe` |

Embed stays with the server because search embeds every query with the same
model; enrich writes the media the server serves. The worker's stages are
exactly those a [remote worker](remote-workers.md) may run, and tag and recipe
are small HTTP clients to Ollama. The server never calls Ollama.

`docker-compose.yml` runs both, with each worker a service of its own:

| Service | Image | Runs |
|---|---|---|
| `app` | server | the web UI, the API, imports, places, and the Retry button's fetch |
| `server-worker` | server | enrich and embed on new reels (`HIKIDASHI_SERVER_STAGES`) |
| `worker` | worker | asr, ocr, tag and recipe on new reels (`HIKIDASHI_LOCAL_STAGES`) |
| `ollama` | `ollama/ollama` | the models tag and recipe call (`--ai`) |

Apart from `app`, a yt-dlp hang, a backlog of embeddings or a crash
mid-transcription never takes the web UI down, and Whisper and Ollama never
compete with page loads. Each worker may use 2 cores (`HIKIDASHI_WORKER_CPUS`;
0 lifts the limit), so a small always-on box stays responsive, and runs at a
lower priority than the server under uv and Docker alike. Their names in
status are their hostnames, `hikidashi-server-worker` and `hikidashi-worker`
(`HIKIDASHI_SERVER_WORKER_NAME`, `HIKIDASHI_WORKER_NAME`); each worker on one
database needs its own. For the GPU, build with `HIKIDASHI_EXTRAS="--extra
cuda"` and uncomment the worker's `deploy` block.

A server whose heavy stages are all done elsewhere can leave the `worker`
container out: `COMPOSE_FILE=docker-compose.yml:docker-compose.server-only.yml`
in `.env` runs `app` and `server-worker` only, and never builds the worker
image. See [remote workers](remote-workers.md).

A stage listed for an image without its tools, such as `embed` on the worker,
is reported as unable to run, never failed, and its reels are left for a
worker that can.

### Where the data lives

Both services mount one folder at `/data`: the database, videos, thumbnails,
imports and model weights. It is `./data`, beside the repo, unless
`HIKIDASHI_DATA` names another. It must be on a local disk. SQLite locks its
file through the filesystem, and NFS, SMB and FUSE shares do not keep those
locks reliably, so a database on one can be corrupted, worst of all with the
server and the worker writing at once.

| Host | `HIKIDASHI_DATA` | Not |
|------|------------------|-----|
| Unraid | a pool: `/mnt/cache/appdata/hikidashi`, or `/mnt/<pool>/...` | `/mnt/user/...`, a FUSE share over the array and pools, even when it maps to the cache |
| TrueNAS SCALE | a dataset: `/mnt/<pool>/apps/hikidashi` | an SMB or NFS share mounted from another machine |
| A plain host | `./data` (the default), or any local disk | an NFS or SMB mount |

Videos are most of it, about 10 MB a reel, so give the folder room: 335 reels
came to 3.5 GB, and the weights to 1.5-3 GB. `./hikidashi import` and `export`
take paths under that folder and hand the container the same path under
`/data`; they ask Docker where it is, so a `HIKIDASHI_DATA` in `.env` is
followed.

The worker container reaches Ollama for tag and recipes at
`HIKIDASHI_OLLAMA_URL`: unset, the bundled `ollama` service (`./hikidashi start
--ai`). Compose reads it rather than `OLLAMA_URL`, which is uv's and often
`localhost`, the container itself. No server container has it. While that
Ollama is off, tag and recipes cannot run: they back off and report it, as
above, and catch up when it is back. The model they ask for follows the
worker's profile. To tag with a GPU desktop's Ollama, run a worker there: see
[remote workers](remote-workers.md).

### Seeing it

`./hikidashi status` lists each worker that has checked in, and per stage the
reels that are ready, waiting on an earlier stage or a pause between tries,
being worked on, and failed:

```
  worker      hikidashi-worker  running · asr, ocr, tag, recipe, embed · checked in 12 s ago, transcribing
                                  tag cannot run: no Ollama at http://ollama:11434
              desktop           not responding: last checked in 25 min ago, and never said it was stopping
              laptop            stopped 24 h ago

  jobs         ready  waiting  working  failed
    enrich         0        0        0       0
    asr            3        0        4       1
```

A worker that is off said so when it stopped. One that stopped checking in
without a word crashed, was killed, or lost its host, and shows as not
responding after two minutes. The web app's **Pipeline** page, for admins and
linked from the sidebar, shows the same, with the reels each stage gave up on
and why. `./hikidashi logs worker`, or the logs page, has the worker's log;
under Docker, `logs server-worker` has the server-worker's, which writes
`server-worker.log` so the two never rotate one file between them.

## Running the stages by hand

Every stage is also a command, which runs it once over everything pending.
These ignore the worker's leases and cap: a reel the worker gave up on is
tried again, and one it holds may be done twice, which costs time and nothing
else.

```bash
./hikidashi import data/imports/instagram-export.zip   # 1. import
./hikidashi enrich --media                             # 2. enrich
./hikidashi transcribe                                 # 3. transcribe
./hikidashi ocr                                        # 4. read on-screen text
./hikidashi tag --llm                                  # 5. tag
./hikidashi recipe                                     # 6. find recipes
./hikidashi embed                                      # 7. embed
./hikidashi places                                     # 8. find and map places
```

Or all of them in order, stopping at the first failure:

```bash
./hikidashi pipeline data/imports/instagram-export.zip   # import, then 2-8
./hikidashi pipeline                                     # 2-8 only, e.g. from cron
```

Under Docker, `./hikidashi` runs each where its tools are: transcribe, ocr,
tag and recipe in the `worker` container, the rest in `app`, and `pipeline` in
three parts, enrich in `app`, 3-6 in `worker`, then 7-8 in `app`. Exit codes
come through, so `./hikidashi pipeline && ...` works. A lock
(`data/run/pipeline.lock`) stops a second pipeline from starting while one runs,
since every stage writes to the same SQLite file. The server keeps running
alongside. OCR is skipped, with a note, where `tesseract` is not installed.
`./hikidashi <command> --help` lists each command's flags.

## 1. Import

Accepts a ZIP or an already-unpacked directory, and reports which parse strategy
matched. Safe to run repeatedly — the same export twice changes zero rows:

```
import 1:  inserted 420, updated 0, unchanged 0,   collections 271 across 7
import 2:  inserted 0,   updated 0, unchanged 420, collections 0
```

## 2. Enrich

The export has no images in it, so a freshly imported grid shows coloured
placeholders. Fetching is what fills them in.

```bash
./hikidashi enrich                 # metadata, thumbnails, carousel slides
./hikidashi enrich --media         # and the mp4s, in the same pass
./hikidashi enrich --media-only    # mp4s (or slides) for items already fetched
./hikidashi enrich --retry-failed  # give permanent failures another go
```

Each item is one transaction, so the run can be killed at any point and resumed
by running it again. Failures are recorded with a stage, a short label and an
attempt count rather than stopping the run; permanent ones (deleted, private,
restricted) are not retried, transient ones get three attempts.

`--media-only` exists because an item that has been fetched is no longer behind
on `fetch_v`, so the ordinary queue will never offer it again. It selects on
`media_path IS NULL` instead and touches no other column, which means a photo
post with no video to download keeps everything it already had. A carousel of
photos has no mp4 either; for one fetched before slides were, it saves the
slides instead, and once it has them it is not offered again.

Three Instagram-specific details the fetcher handles:

- **Photo posts have no video formats**, and yt-dlp refuses the whole request
  for them by default. `--ignore-no-formats-error` is what lets the 85 photo
  posts contribute metadata and thumbnails like everything else.
- **Carousels report a child post's id**, not the one you asked for. The
  shortcode we already hold stays the key; the reported id is ignored. The
  thumbnail is the first slide's. When every slide is a photo, a second
  yt-dlp call without `--no-playlist` lists them all, and each slide's image
  is saved to `slides/<shard>/<shortcode>-<n>.jpg` and recorded in
  `item_slide`, for paging through in the detail sheet. All of them or none:
  a slide that will not download leaves the set for the next run.
- **Instagram reports no duration** — not at the top level, not on any format.
  It is read with `ffprobe` from the downloaded file instead, so `duration` is
  populated only once the mp4 exists.

## 3. Transcribe

Spoken words are the signal captions do not carry. A reel captioned
`the best one yet 🔥` might spend sixty seconds naming the restaurant, the dish
and the street. Transcripts go into the same FTS5 index as captions, so the
search box finds a reel by what was said in it.

```bash
./hikidashi transcribe                  # every video without a transcript
./hikidashi transcribe --limit 20       # try it on a few first
./hikidashi transcribe --profile cpu    # override HIKIDASHI_PROFILE
./hikidashi transcribe --force          # re-transcribe everything
```

ffmpeg decodes each mp4's audio to 16 kHz mono on a pipe, with no temp files,
and `faster-whisper` transcribes it with the **VAD filter on**. Whisper given a
music-only reel will write out lyrics nobody sang, and those would then show up
in search. With the filter on, a reel with no speech gets no transcript. It is
still stamped, so it is not queued again.

On the real library, 98 of 322 videos came back with no speech. A spot check
with the filter off shows what it prevented: `Thank you.` on silent travel
clips, `I'm going to be able to be` repeated over a Japanese house tour, and
pop lyrics pulled from a road-trip montage.

The filter still passes a second or so of music with a voice-like band, and
Whisper writes its stock phrases there. Its own guard keeps them, because
their no-speech probability is low. So a transcript is dropped afterwards when
the filter kept less than 2.5 s of speech, when it holds fewer than one word
for every two seconds of that speech, or when more than half its sentences are
stock phrases (`Thank you.`, `you`, `I don't know.`…). On the real library
that cleared 31 of 227 transcripts, all a scrap of a word or two (`Thank you.`
six times, `Oh`, `Yep!`, `same`). The shortest real sentence, 2.7 s, stayed.

As with enrichment, each item is one transaction. A run can be killed at any
point, including with `kill -9`, and every transcript that finished is already
committed. Ctrl-C exits at once rather than waiting for the reels still in
flight. An error (a corrupt file, the GPU running out of memory) is recorded
with `fail_stage = 'asr'` and retried on the next run. It never sets
`state = 'failed'`, because that state means the post itself is gone.

### Hardware profiles

`HIKIDASHI_PROFILE` picks the models. The resolved name, never `auto`, is
stamped into `asr_profile` and `tag_profile`, so every transcript and every set
of model tags records which model wrote it.

| Profile | Model | Precision | Workers | Speed on this library |
|---------|-------|-----------|---------|-----------------------|
| `cpu` | `distil-small.en` | `int8` | 4, sharing the cores | ~25× realtime on 28 threads |
| `cuda` | `distil-large-v3` | `float16` | 1 | ~50× realtime on an RTX 3080 |
| `auto` | `cuda` if CTranslate2 can see a GPU, else `cpu` | | | |

The tagger follows the same profile: `qwen2.5:3b` on `cpu`, `qwen2.5:7b` on
`cuda` (~5 GB at Q4, leaving room for Whisper on a 10 GB card). Ollama decides
for itself whether it has a GPU.

An explicit `cuda` with no GPU visible is an error, not a quiet fallback to a
run that takes much longer than expected. The GPU path needs cuBLAS and cuDNN:

```bash
uv sync --extra worker --extra cuda   # NVIDIA wheels; no system CUDA needed
```

They are loaded from the wheels at startup, so `LD_LIBRARY_PATH` needs no
changes. The model also runs once at load time, so a library that will not load
fails the run before it starts rather than marking every reel as failed. In
Docker, build the worker with `HIKIDASHI_EXTRAS="--extra cuda"` (the
`hikidashi-worker-cuda` image) and uncomment the worker's GPU `deploy` block.

Weights download once into `HIKIDASHI_MODELS` (default `data/models`): about
330 MB for `cpu` and 1.5 GB for `cuda`. Both models are English-only.

## 4. Read on-screen text

85 of the library's saves are photo posts and 98 of its reels have no speech.
None of them will ever have a transcript, and a silent reel is often silent
because its words are burned into the picture. OCR is how they get read.

```bash
./hikidashi ocr                  # every item with no other words
./hikidashi ocr --limit 20       # try it on a few first
./hikidashi ocr --force          # re-read everything
```

Only items with no speech are read: photo posts and reels whose transcription
found none. A reel still waiting for its transcript waits for OCR too. A photo
post is read from its thumbnail, which for a carousel is the first slide. So is
a reel whose video could not be downloaded. Any other reel is read from its
cover and five frames between 10% and 90% of its length. ffmpeg pipes each
frame straight into `tesseract`.

Tesseract reads anything, including a brick wall, so its output is filtered
rather than kept whole. Words under 60% confidence are dropped, and so are
lines with no word of three or more letters left in them. The same caption read
off five frames, a little differently each time, is kept once. On the library,
115 of 187 items came back with text, 558 lines in all, at about 0.2 s an item.

`tesseract` is CPU-only and uses no model download. It runs one process per
core, up to 8 (`--workers`). Each item is one transaction, and a run resumes
where it stopped. A read that fails records `fail_stage = 'ocr'` and is retried.
If tesseract or ffmpeg is missing, the run stops without marking anything.

New text clears the item's `tag_v`, so the next `./hikidashi tag --llm`
reads it again with the on-screen text added. Only items OCR added something to
are re-tagged. A thumbnail-only item whose video is downloaded later is read
again from the video.

What it changed on the library, from re-tagging the 115 items with text:

- Items with no tag from any source: 27 → 25.
- Categories changed on 20 items. The model added 3 right ones (Food for a pie
  shop, Sim racing for a rig parts list, Finance for an LLC asset structure)
  and 1 wrong one. It removed about 6 wrong ones, such as Finance on an F1
  clip and Design on an AE86, and about 3 that were arguably right.
- 106 summaries were rewritten. Most got more specific, such as "a map of
  western US cities" where it had said "a humorous caption about Utah".

These are spot checks. The calibration set (#6) is what will measure it.

Only English is installed in the worker image (`tesseract-ocr` brings `eng`),
so Japanese and other non-Latin text on screen is not read.

## 5. Tag

Tags are **ours**; collections are **Instagram's**. They live in separate tables
and are shown as separate filters, because they answer different questions.

```bash
./hikidashi tag                  # tag from hashtags, and clean them
./hikidashi tag --llm            # and ask the model
./hikidashi tag --force          # re-tag everything
./hikidashi tag --eval           # score both against the tags you set by hand
```

Each reel gets **one tag**, its primary category from a short fixed list, plus
its cleaned **hashtags** and the model's **keywords**, which carry everything
more specific: the dish, the place, the car model. All three are searchable.
[docs/tags.md](tags.md) has the reasoning and the audit behind the list.

The library's 420 saves carry **2,239 distinct hashtags, 80% of which appear
exactly once**, so promoting hashtags straight to tags would produce a filter
list longer than the library. The hashtag pass instead maps them onto the
category they point at most, and cleans them: normalised, engagement bait like
`#fyp` dropped, each once. The raw hashtags are kept as they were.

The hashtag pass is deterministic and offline. It reads hashtags only, so it
tops out where the hashtags do: 81 items have none at all, and many more use
ones too idiosyncratic to map. It only decides a reel's tag when the model
has not run or found nothing that fits.

### The model tagger

`--llm` sends each reel's caption, hashtags, transcript and on-screen text to a
local model in [Ollama](https://ollama.com) — a reel about pizza is Food whether
or not anyone hashtagged it. The reply is strict JSON: a one-sentence
**summary** (written to `item.summary`, so it is searchable), one **category**
with a high, medium or low **confidence**, and up to five free-text
**keywords**. Ollama constrains generation to a JSON schema whose category
field is an enum of the taxonomy, so an off-taxonomy category cannot be
produced at all; everything outside the taxonomy surfaces as keywords
instead.

```bash
./hikidashi start --ai        # or the worker's own: HIKIDASHI_OLLAMA_URL (Docker), OLLAMA_URL (uv)
./hikidashi tag --llm         # pulls the model on first use
```

Generated tokens are capped at 200, the prompt is not: reading is fast even on
CPU, and cutting a transcript would cut exactly the reels whose captions say
nothing. Each reel is one transaction stamping `tag_v` and `tag_profile`, so a
run resumes where it stopped. A reel whose transcript or on-screen text has not
been made yet waits for it. A bad reply records `fail_stage = 'tag'` and is
retried next run; losing Ollama stops the run instead of failing every reel
after it.

Two things keep a 7B model from guessing. Each category comes with a one-line
definition, because a bare name invites a literal reading ("Sim racing" took a
flight simulator, "Design" took anything built). And the schema offers `Other`
as a way out, which parsing then drops: without it, a constrained model nearly
always picks *something*. A caption with fewer than three real words and
nothing else to go on ("Find your @heaven 🌍") is not sent at all. Words read
off the screen count toward the three.

Re-running replaces only the `source = 'model'` row. The model's answer
outranks the hashtags', and yours outranks both: choosing a reel's tag in the
detail sheet makes it yours, so no re-run can change it.

### Which game

A reel the model filed under Gaming or Sim racing, or whose own text reads
like video games (`#pcgaming`, "gameplay", "PS5", "Minecraft"), is asked one
more question on the same model: is it about one video game, several or none,
and which, and does the text name any other. The title it gives is then
checked with nothing else to go on, which returns the game's official title
("Grand Turismo 4" is Gran Turismo 4) or says it cannot place it. Two short
requests, about 25 tokens each, and only for those reels: 39 of 420 here.

Whether the answer is applied is not the model's call. A 7B model says "high"
to nearly everything, so the server decides from evidence, with the reel's
text as it has it:

| Outcome | When | What it does |
|---|---|---|
| **sure** | one game, high, a title the check can place, no other game it can place named in the text, and the title (its main part, or its hashtag) in the caption, hashtags, on-screen text or transcript | `item.game`: shown on the reel, searchable, and a filter. A reel the others filed elsewhere is tagged Gaming |
| **guess** | one game, but short of that | `item.game_guess`: the detail sheet asks "Is this …?" |
| **none** | several, none, or a low guess | nothing |

Your answer is `user_item.game`: confirming the guess, another title, or no
game at all (`''`), and like your tag it is yours alone and no re-run changes
it. [docs/tags.md](tags.md#games) has the rule's calibration.

## 6. Recipes

```bash
./hikidashi recipe            # find recipes, read out ingredients and steps
./hikidashi recipe --force    # read everything again
./hikidashi recipe --label    # label reels by hand, for --eval
./hikidashi recipe --eval     # precision and recall against your labels
```

Many saved reels are recipes, with the useful part spread across the caption,
the voiceover and the text on screen. This pass decides which reels are
recipes and reads each one out as a structured recipe: title, servings, time,
ingredients with their amounts as written ("1½", "a splash", "to taste") and
numbered steps. It uses the tagger's model and Ollama, and runs after the
tagger, whose Food tag it reads.

Cheapest first:

1. **Candidate filter**, no model: tagged Food, or a quantity with a unit
   ("2 tbsp", "200g"), or the word for ingredients or recipe. Numbered steps
   alone are not enough; in this library they were travel and "top 10" lists.
2. **Check**: the dish the reel makes, whether it is something to eat or
   drink, and a yes or no with a confidence, capped at 60 tokens. Restaurant
   reviews, food tours, mukbangs, food sold at a stall and kitchen tips that
   make no dish (washing salad) should come back no. Naming the dish first
   gives a 3B model something to judge. A yes that names no dish is a no. A
   reel the tagger did not call Food needs a high confidence and a dish that
   is edible: without that, "Overview, Recipe, and Examples" made a recipe of
   transparent wood.
3. **Extraction**, only for a yes: strict JSON, capped at 3,000 tokens.

The model is not trusted about ingredients. It copies each one's words from the
text alongside its English name, and any ingredient whose words appear in no
source is dropped and recorded in `recipe.dropped`. That is what stops a model
adding the garlic every such dish has. `source_fields` (caption, transcript,
ocr) is worked out from where the words were found. A recipe that points
elsewhere ("link in bio", "comment RECIPE"), or has no ingredients or no steps,
is marked **incomplete** rather than filled in.

Corrections work like tags: the pass writes `source = 'model'` rows, and an
edit in the web UI writes a `source = 'user'` row beside it, which wins. A
re-run after a `RECIPE_V` bump replaces only the model's row and touches no
other stage. "Use the model's version" deletes yours. "Not a recipe" hides one
from the Recipes view and survives re-runs too.

`--label` walks every Food reel and every reel with recipe signals, asking
y/n, and saves to `data/recipe_labels.json`. That file sits next to the
database and is never committed, since it is built from your captions. `--eval`
then reports precision, recall, the misses by shortcode and any stored
ingredient the text does not name, which should be none.

## 7. Embed

```bash
./hikidashi embed            # vectors for semantic search
./hikidashi embed --force    # re-embed everything
```

Embeds each item's summary and keywords with
[embeddinggemma-300m](https://huggingface.co/google/embeddinggemma-300m) on
ONNX Runtime, so search finds a reel by what it is about as well as by the
words in it. The whole library takes about 25 seconds on CPU, and the weights
(1.2 GB) are downloaded once into `HIKIDASHI_MODELS`. Each batch is one
transaction. Re-running embeds only what changed: the stored digest covers the
embedded text, so a re-tagged summary re-embeds that reel and nothing else.

What gets embedded is the tagger's one-line English summary, not the caption.
Adding the caption made every model tested worse. Captions are long, full of
hashtags and often not in English, and they bury the line that says what the
reel is. Captions are still searched as keywords. An item the tagger has not
reached yet is embedded from its caption until its summary arrives. The worker
waits for the summary instead, unless no worker can tag
([Readiness](#the-worker)).

The model was picked by measuring it against the library, not by its size.
Over eight paraphrased queries, each with a known set of right answers, it found
0.76 of them in its top 10. bge-small-en-v1.5, the obvious choice, found 0.53,
and bge-large and mxbai-embed-large found 0.54–0.58.

How the vectors are combined with keyword search is covered in
[Design notes](design.md#search-is-keywords-and-meaning-blended).

## 8. Places

```bash
./hikidashi places             # read places from captions, then look them up
./hikidashi places --offline   # only read them; look nothing up
./hikidashi places --force     # read and look up everything again
```

Two passes. The first reads the places each caption names (📍 pins, street
addresses, `Where:` lines) into `item_place`. It is a few regular
expressions, free and offline, and it is all the **Places** filter needs.

The second puts them on the map. Each place is looked up on a
[Nominatim](https://nominatim.org) server, and the answer is cached by its
text, so a place twenty reels name is looked up once. Nothing is looked up
until `HIKIDASHI_GEOCODER` names a server: a self-hosted Nominatim, or
`public` for OpenStreetMap's own, which asks for no more than one request a
second (`HIKIDASHI_GEOCODER_DELAY`, 1 by default). Each answer commits as it
arrives, so an interrupted run resumes where it stopped.

**The server runs it too.** With a geocoder set, the server starts a locator
that does both passes whenever there is something new: at once after an
import through the web, and otherwise every `HIKIDASHI_LOCATE_EVERY` seconds
(300 by default; 0 turns it off). Lookups hold `data/run/places.lock`, so the
locator and `./hikidashi places` never ask the geocoder at once; the one that
finds it held leaves the work to the other. When the geocoder fails, the
locator waits longer each time, from a minute up to an hour. A reel with no
caption yet is not read until enrich brings one.

A caption's place is rarely a clean query, so each is tried a few ways, best
first: without @handles and asides, the street address alone, the address
without its suite, the address without its venue, and the venue alone. The
first that finds anything wins.

**How sure is it?** The score is the share of the candidates that lie near the
top one. "Portland, Maine" names one place and scores 1. "Cedar Park" names a
dozen across the US and scores 0.35: a pin there would be a guess, so it is
kept but left off the map, under 0.6. A country is no place for a pin, and
scores 0.2 at most.

**A second look.** A place can be sure of itself and still be wrong.
"Barnett Cave" is a cave in New Zealand and a farm in Tennessee, and only the
farm is in OSM under that name. When a reel's sure places agree on a region,
its other places are looked up again inside it: the unsure ones, and any that
landed far outside. "Carmel" in a list of California's coast moves from
Indiana to Carmel-by-the-Sea. An outlier with no match inside the region is
taken off the map rather than left on the wrong continent. A list of the best
of a whole country, whose places are spread too wide to agree on a region, is
left as it is.

On the real library: 38 reels name 110 places. 83 are on the map, 11 were
found but too unsurely to pin, and 16 were not found, most of them not places
at all ("Where to stay", "Christchurch → Twizel (3 nights)"). The second look
moved 12.
