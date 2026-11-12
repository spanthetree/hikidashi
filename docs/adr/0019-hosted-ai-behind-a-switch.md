# 0019. Hosted AI, behind an on/off switch

- **Status:** Accepted; supersedes [0009](0009-local-models-only.md)
- **Date:** 2026-11-12
- **Issue:** #116 (the provider layer and presets), #125 (the switch)

## Context

[0009](0009-local-models-only.md) kept every model on hardware you own. That
still holds for most libraries, but some can't get good results that way:

- A NAS with no GPU and no desktop running a remote worker transcribes with
  `distil-small.en` and tags with `qwen2.5:3b`. Hosted Whisper large and a
  small hosted chat model do much better, in a fraction of the time.
- Both local Whisper models are English-only. A Japanese, Spanish or Italian
  reel gets an English transcript, or nothing.
- Some providers have free tiers (Groq for transcription, Gemini and others
  for chat), so a library can cost nothing.

But a hosted API sends your saves to a third party, costs money, and can be
switched off at the provider's end. And a reel is stored once for everyone who
saved it, so one person's choice touches reels others saved too.

## Decision

**Workers by default. Hosted only while someone has switched an API on.**

- **One choice each.** A user picks *System default* (whatever the admin
  chose), *Hosted AI* (their own provider and key) or *Workers only*. The
  admin's own choice, Hosted AI or Workers only, is the system default: the
  server's API, for everyone who keeps the default. Hosted AI, once set up,
  is an on/off switch. On, new reels go to
  the API as they're ready. Off, nothing is sent, and workers get everything,
  as before. Turning it off is the stop button: in-flight requests finish,
  and what's not sent goes back to workers. Env makes the server's API
  available but never switches it on.
- **All or nothing per reel.** The API that takes a reel does every worker
  stage it still needs (transcription, on-screen text, tags with the game
  pass, recipe), holding a lease on each, so no worker takes a later stage.
  Local and API stages are never mixed on a reel.
- **Providers can be mixed per stage,** e.g. Groq for transcription and
  OpenAI for the rest, but all four must be set before the switch can go on.
- **The loose rule for shared reels.** The server's API takes a reel if at
  least one saver keeps the system default. A user's own API is offered
  their reels first: the server's waits a minute. "Workers only" users have
  nothing sent on their account. But a reel someone else also saved may be
  processed by that person's API, and nothing in the request identifies the
  "workers only" saver.
- **One OpenAI-compatible client** (`app/providers`, stdlib only), with a
  preset per provider. The server image runs it with no faster-whisper,
  tesseract or Ollama.
- **Keys are encrypted at rest** (Fernet, `cryptography`), under
  `HIKIDASHI_SECRET_KEY` or `data/secret.key`. They're write-only (shown as
  `sk-…abcd`), never in a log, a `repr`, an error or an API response, and
  never sent to a worker. A hosted worker on another machine
  (`hikidashi worker --backend hosted`) uses its own env key only.
- **The switch turns itself off,** with the reason shown, when the key is
  rejected, the account's quota is used up, or the monthly budget (required
  before it can go on) is reached. A rate limit, a free tier's daily cap or an
  outage only pauses it. A failure on one reel is an API error, counted apart
  from workers' attempts. It never fails the reel, and after three the API
  leaves it to workers.

**What leaves, and to whom,** while a switch is on, to the providers set for
each stage:

| Stage | What is sent |
|---|---|
| transcription | the reel's audio, as 16 kHz mono FLAC |
| on-screen text | the cover and five frames, as 768 px JPEGs |
| tags, game pass, recipes | the caption, hashtags, transcript and on-screen text |

Each preset's free tier and data terms are in
[operation.md](../operation.md#hosted-ai), checked and dated. Gemini's free
tier, for one, lets Google use what is sent to improve its products.

**Embeddings stay local**
([0003](0003-embedding-is-server-only.md)). Gemini, Mistral, Jina, Voyage and
Cloudflare all have free embedding tiers. But vectors from different models
can't be compared, so every reel would have to be embedded again. And a
hosted embedder would send **every search query** out, so search would stop
working when the provider is down or the quota is used up. Not built. Measure
it against `tests/search_eval.json` before revisiting.

## Alternatives

- **Manual runs with recall** (send a batch, pull results back): complex,
  racy, and a click after every import.
- **Mixing local and API stages per reel** (Groq for transcripts, local Ollama
  for tags): a reel stalls when a local stage waits on a worker that never
  comes, and one reel's output comes from two places.
- **Batch APIs:** half the price for chat, but a day's delay per stage,
  orphaned batches after a crash, and transcription can't be batched. The
  chat stages cost little enough as it is.
- **Per-user transcripts and tags,** so one saver's choice never touches
  another's copy: a schema rewrite for little gain.
- **Claude as a provider:** the Claude API has no speech-to-text, so it can't
  cover all four stages, and it would need its own adapter.
- **Deepgram, AssemblyAI and ElevenLabs adapters** for transcription: Groq's
  free Whisper does the same through the one client.

## Consequences

- New tables: `setting`, `ai_audit`, `api_reel`, `hosted_usage`; new item
  columns `asr_language` and `ocr_profile`. A hosted stage stamps its
  profile as `provider:model`, so reels made by an older model can be queued
  again (`/api/admin/ai/requeue`).
- The remote worker protocol is version 2: a hosted worker claims a reel for
  all its stages at once. Both sides must run the same release, as before.
- **The music guard is weaker on some models.** Local Whisper's VAD keeps
  music-only reels empty. Hosted, the guard needs `verbose_json` segments with
  `no_speech_prob`. Groq's Whisper models give them; OpenAI's `gpt-transcribe`,
  its default since whisper-1 shuts down on 2027-02-26, does not. On such a
  model only `is_noise` runs over the whole clip, and a song may get lyrics.
  The settings page warns. To measure on the music-only reels: #116's
  deferred checks.
- A backup of `data/` holds both the database and `secret.key`, so it can
  read the keys. A copy of the database alone can't.
- Prices and model names are in code, dated. They go stale: the estimates
  and the budget are only as good as the price table.
