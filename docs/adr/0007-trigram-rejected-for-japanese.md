# 0007. Trigram rejected for Japanese

- **Status:** Accepted
- **Date:** 2026-10-22
- **Issue:** #33; [search.md](../search.md#japanese-33)

## Context

A Japanese UI invites Japanese queries. FTS5's `unicode61` tokenizer makes one
token of a whole run of unspaced script, so `紅葉` never matches a caption that
says `紅葉スポット`.

## Decision

Match words in unspaced scripts (kana, kanji, hangul) as substrings with
`LIKE`, as a tier of the keyword list, and rely on the multilingual embedder for
meaning. No second index.

## Alternatives

- **FTS5 `trigram`**, as a second index over caption, transcript and OCR text.
  Measured: non-English blended nDCG@10 0.88 with either, keyword 0.78 (LIKE)
  against 0.77 (trigram), for +1.8 MB of index. It can't match the two-character
  words that matter (`桜`, `京都`), so LIKE was needed anyway. And it still finds
  `京都` (Kyoto) inside `東京都` (Tokyo Metropolis).
- **A morphological tokenizer** (MeCab, Sudachi). It would tell `京都` from
  `東京都`, at the cost of a new dependency and an index of its own, for a
  library with 34 reels in Japanese.

## Consequences

- Keyword search over Japanese is substring matching, and `京都` matches Tokyo
  addresses. The vectors put real matches in the top ten for every other
  Japanese query in the eval set.
- Revisit with a morphological tokenizer if Japanese reels become a large share
  of a library.
