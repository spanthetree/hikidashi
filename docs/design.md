# Design notes

The principles behind Hikidashi, and where things live in the code. The
system as a whole, with diagrams, is in [architecture.md](architecture.md); how
it behaves at runtime in [operation.md](operation.md); and each decision argued
out in an issue has a [decision record](adr/README.md).
[Back to the README](../README.md)

## Principles

**The shortcode is the primary key.** Parsed out of the permalink, it is what
makes dedupe, incremental import, retrying a failed stage and keeping your
hand-written tags all work.

**An import only ever adds or widens.** It never overwrites enrichment, never
resets a stage version, never deletes. Re-importing is always safe.

**Stage versions (`fetch_v`, `asr_v`, `tag_v`, …)** record which build produced
each column, so a second run costs only what actually changed. Improving the
tagging prompt re-tags from stored text without re-downloading anything. A
paired `asr_profile` / `tag_profile` records *which* model did it, because
`asr_v = 2` alone cannot tell you whether a transcript came from the small model
or the large one.

**A stage is a query, and the worker only leases from it.** What a stage has
left to do is what is behind its version stamp, which is why each can be run
by hand at any time. The always-on worker (`app/jobs`) adds only leases, so
two workers never hold one reel and a dead one's reels come back; readiness,
so a reel waits for the stages before it; and an attempts cap, so a reel that
crashes the worker is given up on. It reaches the database through a small
job source, which is local SQLite today and will be HTTP for workers on other
hosts ([#35](https://github.com/spanthetree/hikidashi/issues/35)), so the loop is the same either way.

**One tag per reel, with provenance.** `item_tag` holds one answer per source
(`hashtag`, `model`, `game`), and yours is in `user_item`, so re-running any
one source replaces only its own row, and the reel shows yours, else the game
pass's, else the model's, else the hashtags'. A gaming reel's video game is
kept beside its tag, applied only when the reel's own text names it
([ADR 0014](adr/0014-game-titles-from-evidence.md)).
Hashtags and keywords carry everything more specific than a category, and are
searched rather than listed. Your corrections survive every re-run, which is
also what makes them a usable evaluation set. [docs/tags.md](tags.md) has the
audit behind the category list.

**A reel is shared; a save is yours.** `item` and everything derived from it
(media, transcript, OCR, the model's tag and recipe, vectors) depend only on
the shortcode, so they are a cache shared by everyone who saved the reel and
made once. What belongs to a person is the save itself and what they did with
it: `user_item` (when they saved it, the tag they chose), their collections,
imports and recipe corrections. Every read goes through `user_item`
(`library.mine`), inside each search list rather than after it, so a reel
nobody else saved is invisible to everyone else down to whether it exists:
a 404, never a 403. The last person to remove a reel deletes it and its media.
User 1 always exists and owns the library from before accounts; with
`HIKIDASHI_AUTH=none` every request is user 1, so there is one code path.

**The raw records are kept verbatim** — `item.raw_meta` for the export record
and `item.fetch_meta` for the yt-dlp response. A few KB per reel, and the
difference between "I also want the brand-partner field" costing a SQL query
rather than another rate-limited crawl.

**The parser is deliberately tolerant.** Meta has already reshaped this file
once, so three strategies run in order — the current `label_values` shape, the
older documented shape, then a generic permalink sweep. The next reshape
degrades the import instead of breaking it.

**Captions make search work on day one.** Because the export carries caption
text, FTS5 is populated at import and the search box is useful before any
network fetch or model has run. Transcripts and on-screen text share the same
index once those stages run.

## Search is keywords and meaning, blended

FTS5 alone goes a long way, because Instagram captions are long and full of
hashtags. But it cannot find "a vegetarian sandwich" in a reel captioned
*Portobello banh mi*. Embeddings can, and are vague about exact wording, so
both run (`app/search/`):

1. Items containing the query as an exact phrase come first.
2. Everything else is ranked by reciprocal rank fusion of three lists: reels
   with every word (bm25); reels close in meaning, by the summary's vector or
   by their transcript's closest 60-word chunk, whichever is closer; and, when
   no reel has every word, reels with every word that says what you are after
   (no stopwords, nothing in a tenth of the library), at half weight. Fusion
   uses only ranks, so bm25 and cosine scores never need a common scale, and a
   reel several lists found beats one only one found.

A vector hit needs a cosine similarity of at least 0.27 (0.5 for a chunk), and
at most 24 are kept. Every item is a little similar to every query, so without
those limits any search would return the whole library. Searching sorts by
**Best match** until you pick another order. Without `./hikidashi embed`, or
while the model cannot load, search is keywords only.

The scan is brute force: `vec_distance_cosine` from
[sqlite-vec](https://github.com/asg017/sqlite-vec) over plain tables of
float32 blobs, 1,427 vectors for 420 items, in about a millisecond. A vector
index would only add something that can go stale.

Search quality is measured, not eyeballed: `python -m app.search.eval` scores
73 graded queries, and `--against <git ref>` scores an older search beside
it. [search.md](search.md) has the numbers, and what was tried and rejected
(cross-encoder reranking, LLM query and document expansion). The
limit is still the model's world knowledge: asking for the best of a dish in
its home city puts the first matching reel 5th, because to a 300M-parameter
model "best" plus a place reads as a restaurant review, and a dish's name only
weakly means the food when no reel says it.

## Finding places in captions

Calibrated against a real library of 420 saves rather than guessed at. Of 420 captions, 59
use a 📍 pin, 22 contain a street address, and 2 use a `Where:` label. The pin
is the strongest signal and usually carries a venue name as well as an address —
`📍Corner Noodle Bar — 12 Example St, Portland, OR 97201` — which makes a
far better Maps query than the street line alone, so the whole string is kept.

The bar is deliberately high: a candidate must look like a place, not merely
contain a number. A description after a venue name is dropped, and itinerary
markers like `STOP 1` are rejected.

## Project layout

```
hikidashi           ./hikidashi: server start/stop/status, uv or Docker
app/
  schema.sql        tables, indexes, FTS5 and its sync triggers
  cli.py            the `hikidashi` command: stages, pipeline, status
  config.py         settings from the environment
  db.py             connection, schema application, column migrations
  places/
    __init__.py     addresses and venues in captions -> Google Maps
    geocoder.py     Nominatim, query variants, confidence, a reel's region
    worker.py       item_place and the geocode cache; the second look
    service.py      the server's locator, and the lock it shares with the CLI
    __main__.py     `hikidashi places`
  export/
    __init__.py     one person's library, read once for every format
    recipes.py      schema.org JSON-LD, Nextcloud Cookbook and Paprika bundles
    markdown.py     the Obsidian vault, kept in step below each note's marker
    table.py        plain JSON and CSV
    __main__.py     `hikidashi export`
  library/
    __init__.py     whose reels are whose: user_item, removing a reel
    migrate.py      the one-time move to one library per person (#12)
  accounts/
    __init__.py     users, argon2 passwords, sessions, invites, API and worker tokens
    __main__.py     `hikidashi user`: add, passwd, rename, list, invite
  tokens/
    __main__.py     `hikidashi token`: make, list, revoke
  profiles.py       HIKIDASHI_PROFILE -> concrete model, precision, workers
  importer/
    shortcode.py    permalink -> shortcode
    parser.py       export JSON -> records, tolerantly
    service.py      idempotent upsert, import_run bookkeeping
  enrich/
    fetcher.py      yt-dlp wrapper, failure classification, ffprobe
    worker.py       resumable job loop, state transitions
  asr/
    transcriber.py  ffmpeg audio extraction, faster-whisper with VAD
    worker.py       resumable transcription loop
  ocr/
    reader.py       ffmpeg frames, tesseract, confidence filter, line dedupe
    worker.py       resumable OCR loop for items with no speech
  ollama.py         the Ollama client the tagger and the recipe pass share
  search/
    __init__.py     FTS5, vector and transcript-chunk hits, fused into one ranking
    eval.py         the graded query set, scored: recall@10, MRR, nDCG@10
  logs.py           the server and worker log files behind /logs, and reading their tail
  jobs/
    __init__.py     the stages by name, HIKIDASHI_LOCAL_STAGES, the wake file
    queue.py        leases, readiness, the attempts cap, check-ins; LocalJobs
    protocol.py     what a remote worker is sent, and how its results are checked
    remote.py       HttpJobs and RemoteRunners: the worker on another machine
    worker.py       the loop: a batch per stage, idle backoff, models unloaded
    __main__.py     `hikidashi worker`
  embed/
    embedder.py     what gets embedded (summary, transcript chunks), the model wrapper
    worker.py       resumable embedding loop, staleness by digest
  tagging/
    taxonomy.py     the categories, hashtag mapping rules, hashtag cleaning
    service.py      one answer per source, and which one a reel shows
    llm.py          prompt, JSON schema, reply validation
    worker.py       resumable model tagging loop
    evaluate.py     the taggers scored against the tags you set by hand
    consolidate.py  the one-time move to one tag per reel (#21)
  recipes/
    detect.py       the candidate filter: Food tag or recipe signals
    groups.py       hand-written ingredient groups: Dairy, Meat, Gluten, …
    llm.py          check and extract prompts, schemas, grounding
    service.py      model and user recipes, search index, filters, facets
    worker.py       resumable recipe loop
    evaluate.py     hand labels, precision and recall
  api/main.py       FastAPI, thin: who is asking, then their library
  spa.py            which built file, or index.html, answers a front-end URL
  web/              gitignored: the built front end, from frontend/
frontend/           Svelte 5 + SvelteKit, built with adapter-static
  src/routes/
    (library)/      grid, search, filters, ?view=recipes; /item/[shortcode] opens the sheet
    logs/           the server log, followed live
  src/lib/
    components/     card, detail sheet, recipe panel, sidebar, top bar
    library.svelte.ts  the grid's items and paging
    filters.ts      search, filters and sort in the query string
    motion.svelte.ts   every transition, and the reduced-motion fallbacks
    theme.svelte.ts    the chosen theme, light/dark mode and pixel art, set on <html>
  src/app.css       design tokens: the Instagram theme, and every theme's defaults
  src/themes/       the other themes, then pixel art over any of them
  static/icon.svg   favicon and header mark
  scripts/record-demo.mjs  films the README animation and the tour
docs/               these notes, architecture.md, operation.md
  adr/              the decision records
  diagrams/         animated SVGs, written by scripts/make_diagrams.py
  demo/             the README animation, the tour, the worker recording, credits
scripts/
  demo_library.py   builds the demo library the recordings are made on
  make_diagrams.py  the ER and stage diagrams from code, and the animated SVGs
tests/              stdlib unittest
data/               gitignored: database, media, imported ZIPs, model weights
  media/thumbs/     fetched thumbnails, sharded by shortcode
  media/video/      original mp4s, never re-encoded
```
