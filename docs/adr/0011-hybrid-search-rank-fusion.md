# 0011. Hybrid search by rank fusion, no reranker

- **Status:** Accepted
- **Date:** 2026-10-21 (#4), measured and tuned since (#20, open)
- **Issue:** #4, #20; [search.md](../search.md), [operation.md](../operation.md#a-search-request)

## Context

FTS5 keyword search is exact and explainable, but it can't find "warm broth for a
cold night" in a reel captioned *Loaded Potato Soup*. Embeddings can, and are
vague about exact wording. Their scores (bm25, cosine) are on scales that
don't compare.

## Decision

Run both, and blend them by **reciprocal rank fusion**. Each list scores a reel
`weight / (60 + rank)`, and the scores are summed: keyword (every word, weight
1), semantic (summary vector at cosine ≥ 0.27, top 24, or a transcript chunk at
≥ 0.5; weight 1), and, only when no reel has every word, distinctive content
words (weight 0.5). Then reels containing the query as an exact phrase are
pinned above the rest. **No reranking step.** Every constant is set against 73
graded queries (`python -m app.search.eval`).

## Alternatives

- **Score blending** (normalise bm25 and cosine, add). It needs a common scale
  that doesn't exist, and re-tuning whenever either changes. Rank fusion uses
  ranks only, and a reel two lists found naturally beats one only one found.
- **A cross-encoder reranker** on the fused top 30. Six models measured: at
  most +0.02 nDCG@10, with paraphrase queries getting worse, at 10–250× the
  latency budget (p95 0.7–5.3 s against 20 ms). None fixed the hard query.
- **LLM query expansion** (HyDE). It works for the hard query, but takes
  400–900 ms per search on CPU, and the model makes things up.
- **LLM document expansion** (doc2query). 28 minutes over the library for no
  gain, and voiceover queries got worse.

## Consequences

- Search runs in about 20 ms, on the server, with no model besides the
  embedder.
- Without the embedder, search falls back to keywords alone, with a looser
  content-word match.
- Queries that need world knowledge stay weak until a GPU makes query rewriting
  affordable (#64).
