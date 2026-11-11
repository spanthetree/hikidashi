# 0009. Local models only

- **Status:** Accepted
- **Date:** 2026-10-21
- **Issue:** #2 (transcripts), #3 (tagging), #4 (embeddings)

## Context

Every stage after the fetch is a model: speech to text, a summary and category,
recipe extraction, and vectors for search. Hosted APIs would do each of them
well, and fast.

## Decision

Every model runs on hardware you own: faster-whisper for transcripts,
tesseract for on-screen text, an LLM on Ollama (`qwen2.5:3b` on CPU, `7b` on
CUDA) for tags and recipes, and `embeddinggemma-300m` through fastembed for
vectors. `HIKIDASHI_PROFILE` picks models and precision to suit the machine.
No API keys, and no captions or transcripts leave the machine.

## Alternatives

- **Hosted APIs** (speech, LLM, embeddings). Better quality, and nothing to
  install. But they send everything you saved to a third party, cost money per
  reel and per search, and stop working when a key or a price changes. A
  library of your saves is personal data.
- **A hosted embedder only.** Every search query would leave the machine too.

## Consequences

- Quality is bounded by small models on CPU. For example, a query for a dish
  that no reel names needs world knowledge a 300M embedder has only weakly. Limits
  that need a GPU are tracked together in #64.
- Heavy stages are slow on a NAS, which is what remote workers are for
  ([0010](0010-always-on-workers-before-remote.md)).
- Model weights (1.5–3 GB) live in `data/models`, and the worker unloads them
  when idle.
- The only network calls are yt-dlp to Instagram, and the geocoder and map
  tiles if you configure them.
