# Search quality

How search is measured, what it scores, and what was tried (#20).
[Back to the README](../README.md) · [Design](design.md)

## Measuring it

The numbers below come from 73 graded queries over a real library, each with
the reels it should return, graded 2 (the reel the query is after) or 1 (fair
to show). That set lists what someone saved, so it stays out of the repo: put
your own at `data/search_eval.json` (git-ignored), and the eval reads it in
place of `tests/search_eval.json`, 22 queries over the demo library
(`scripts/demo_library.py`) in the same format. The real set covers eight
kinds of query. The examples below are of the same kind, but they are not the
real queries, which would say what is in the library:

| kind | n | example |
|------|---|---------|
| phrase | 7 | `one pot to wash`, words from a caption |
| keyword | 7 | `sourdough`, `lathe` |
| paraphrase | 15 | `a quick weeknight dinner with chicken`, `making bread at home` |
| voiceover | 9 | `the one where he says he's been baking for ten years` |
| place | 8 | `cheap eats in Tokyo`, `things to see in Kyoto` |
| creator | 6 | a handle typed as words, or a creator described rather than named |
| non-english | 13 | `塩ラーメン`, `伏見稲荷`, a Spanish recipe search |
| negative | 8 | `knitting a sweater`, `car engine repair`: no good answer exists |

```bash
uv run python -m app.search.eval             # keyword-only and blended
uv run python -m app.search.eval --queries   # plus where every expected reel ranked
uv run python -m app.search.eval --against 83c01ae   # and search as it was before #20
```

`--against REF` loads `rank()` from a git ref and scores it beside today's
code on the same library and query set, then lists each query whose score
moved. The library changes as reels are added and re-summarised, so a number
recorded weeks ago is not a baseline: rerun the old code instead.

It prints recall@10, MRR and nDCG@10 (gain 2^grade − 1) overall and per kind,
how many results the negatives return, and search latency including the query
embedding. Two modes run side by side: `keyword` (FTS5 and the LIKE fallbacks)
and `blended` (what the app does). A negative counts as quiet when it returns
three reels or fewer. Shortcodes the library does not hold are reported and
skipped, so the set survives a re-import.

The set is small: one query moves a six-query kind by up to 0.17. Read a change
of 0.01 or 0.02 in a kind as noise unless the per-query table shows why.

## Results

Measured 2026-10-24 on the 420-item library, CPU, embeddinggemma-300m, with
`--against 83c01ae`: search as it was before #20 and as it is now, on the same
library and the same 73 queries.

```
                             keyword@83c01ae                   keyword           blended@83c01ae                   blended
                          recall  MRR   nDCG        recall  MRR   nDCG        recall  MRR   nDCG        recall  MRR   nDCG
  all                      0.39  0.51   0.43         0.69  0.83   0.73         0.89  0.90   0.86         0.93  0.96   0.92
  creator (6)              0.53  0.67   0.55         0.70  0.83   0.71         0.88  1.00   0.88         0.92  1.00   0.91
  keyword (7)              0.94  1.00   0.94         0.94  1.00   0.94         0.99  1.00   0.98         0.99  1.00   0.98
  non-english (13)         0.20  0.46   0.29         0.58  0.73   0.63         0.77  0.86   0.78         0.86  0.93   0.88
  paraphrase (15)          0.14  0.20   0.17         0.47  0.73   0.56         0.90  0.90   0.86         0.90  0.90   0.86
  phrase (7)               1.00  1.00   1.00         1.00  1.00   1.00         1.00  1.00   1.00         1.00  1.00   1.00
  place (8)                0.36  0.62   0.44         0.42  0.75   0.53         0.91  1.00   0.88         0.91  1.00   0.88
  voiceover (9)            0.11  0.11   0.11         1.00  0.93   0.94         0.89  0.66   0.71         1.00  1.00   1.00

  negatives          8/8 quiet, 0.0 hits avg   8/8 quiet, 0.0 hits avg   5/8 quiet, 4.5 hits avg   5/8 quiet, 4.5 hits avg
  latency                 p50 2 ms, p95 3 ms        p50 3 ms, p95 4 ms      p50 18 ms, p95 26 ms      p50 21 ms, p95 25 ms
```

Blended nDCG@10 went from 0.86 to 0.92 and MRR from 0.90 to 0.96, and no kind
of query went down. Voiceover half-memories gained the most (0.71 to 1.00:
every one now has its reel first), then non-English (0.78 to 0.88). Exact
phrases still rank first, the negatives are as quiet as before, and p95 is
25 ms. Keyword-only search, all you get before `./hikidashi embed`, went from
0.43 to 0.73. The chunk vectors add about 3 MB to the database.

At three decimals paraphrase is 0.859 against 0.861: in one paraphrase query,
a reel whose voiceover is about something close to the query now ranks 4th,
one above a fifth expected reel.

What did not improve:

- **A "best dish in its home city" query puts its first reel at 5th**, not in
  the top 3. Its reels show the food, but none names the dish or the city.
  Nothing that fits the latency budget on CPU fixes it; see below, and
  [#64](https://github.com/spanthetree/hikidashi/issues/64).
- **`京都`, 0.22 to 0.18.** Kyoto is a substring of 東京都, Tokyo (see
  [Japanese](#japanese-33)).
- **"cheap eats in Tokyo" is weak before and after** (0.28): two of its six
  reels are not in the top 30.

An earlier version of this page had a baseline recorded on 2026-10-22, which
no longer reproduced two days later: re-summarised reels move the numbers
(voiceover measured 0.81 then, 0.71 now on the same code). Hence
`--against`.

## What changed

1. **Sentences match on their content words** (`content_word_hits`). Keyword
   search ANDs every token, so a sentence almost never matched and the blend
   fell back to vectors alone. When no reel has every word, the search ANDs the
   words that are neither stopwords ("that", "video", "says") nor in more than
   10% of the library ("best" is in 22%), fused at half the weight of a full
   match. Without vectors, any two of those words will do.
2. **Unspaced scripts match inside a run.** FTS5's unicode61 tokenizer makes a
   whole run of Japanese one token, so 紅葉 never matched 紅葉スポット. Words in
   CJK and kana are matched as substrings of caption, transcript and OCR text.
3. **Creators typed with spaces.** "tokyo supercars" matches @tokyosupercars_example.
   Before, the LIKE over handles took the whole query, spaces included.
4. **Transcripts are embedded a stretch at a time** (`item_chunk_vec`): 60
   words, overlapping by 20, from the transcript and the OCR text. 1,007 chunks
   for 420 reels. A reel's closest chunk, if at least 0.5 similar, competes
   with its summary for its place on the semantic list, at 0.1 less than its
   similarity. The summary says what a reel is about and drops what was said,
   and this is what finds a half-remembered line from a voiceover. `EMBED_V`
   is 2, so the first `./hikidashi embed` after upgrading re-embeds everything
   (about 90 s on CPU).
5. **Every hit says why it matched.** `/api/items?q=` returns `match` per item:
   `{"match": ["keyword", "semantic", "transcript"], "similarity": 0.52}`.
   Cards show it as a badge under `npm run dev`, or on the built app with
   `localStorage.explain = "1"`.
6. **A reel's game is indexed** (#117). `item_fts` gained `game`, the title
   the tagger is sure a reel shows, so "gran turismo" finds a reel whose
   caption spells it "Grand Turismo". A game you named matches with your tags
   in the second tier. On the 73 queries, against the code before it, nothing
   moved: 0.92 nDCG blended, 0.72 keyword, the same in every kind. None of the
   set's queries is about a game.

### Getting there

The first version (0.2.0) scored 0.90, but paraphrase and place went down
(0.86 to 0.84, 0.88 to 0.86) and the negatives got noisy (3/8 quiet, 11.5
results on average). Two things caused it:

| change | blended nDCG@10 | paraphrase | place | voiceover | negatives quiet |
|--------|----:|----:|----:|----:|----:|
| 0.2.0: any word ORed at 0.25, chunks fused at 0.5 | 0.90 | 0.84 | 0.86 | 0.96 | 3/8 |
| any two words | 0.91 | 0.85 | 0.87 | 1.00 | 5/8 |
| ...and chunks on the semantic list, less 0.1 | 0.91 | 0.85 | 0.88 | 1.00 | 5/8 |
| ...and every content word (now) | 0.92 | 0.86 | 0.88 | 1.00 | 5/8 |
| every content word, any two when none has all | 0.91 | 0.86 | 0.88 | 1.00 | 5/8 |
| every content word, any one when none has all | 0.91 | 0.83 | 0.87 | 1.00 | 3/8 |

The pre-#20 code scores paraphrase 0.861; "now" is 0.859, and any-two as the
fallback 0.855. Without vectors that fallback is what the app uses.

- **One word was a match.** ORed, a query about grooming a pet found 24 reels
  for one common adjective in it, and a query about board games with children
  pushed its fourth reel from 3rd to 11th with 48 reels about games. Lowering
  the weight instead cost voiceover (0.15: 0.93), and keeping only the hits the vectors
  also found changed nothing: the noise was on both lists.
- **A voiceover counted twice.** As a list of its own, a reel's best chunk
  added to its summary's rank, so in one food-and-place query two reels with a
  voiceover beat the closest summary match (0.65), which has none. On one list,
  an offset of 0.1 to 0.15 scores the same; 0.2 costs voiceover (0.93).

Every content-word weight from 0.25 to 0.75 scores the same; 0.5 is the
middle. So does every RRF `K` from 30 to 90, and a chunk floor of 0.45. A
floor of 0.55 loses voiceover reels (0.93) and creator (0.88). A chunk
offset of 0.2 is the one setting that puts paraphrase and creator exactly
back at their pre-#20 scores (0.861 and 0.879), but overall drops to 0.915,
against 0.918 at 0.1.

## What was tried and rejected

Measured on 2026-10-22 against the 0.2.0 search and the 69 queries of then,
so compare each with its own "none" row rather than with the tables above.

**Cross-encoder reranking** of the fused top 30, through fastembed:

| model | nDCG@10 | p50 | p95 |
|-------|---------|-----|-----|
| none (blended) | 0.902 | 17 ms | 20 ms |
| ms-marco-MiniLM-L-6-v2 | 0.918 | 238 ms | 692 ms |
| ms-marco-MiniLM-L-12-v2 | 0.913 | 463 ms | 1,380 ms |
| jina-reranker-v1-turbo-en | 0.903 | 355 ms | 1,057 ms |
| bge-reranker-base | 0.916 | 1,435 ms | 3,569 ms |
| jina-reranker-v2-base-multilingual | 0.918 | 1,689 ms | 4,163 ms |
| bge-reranker-v2-m3-int8 | 0.923 | 2,189 ms | 5,344 ms |

At most +0.02, with paraphrase getting worse, at 10 to 250 times the latency
budget. None of them fixes the dish query either. Given 60 candidates that
include its four reels, every model ranks them 19th or lower and puts other
food reels with a city in them on top: like the bi-encoder, they read "best X
in Y" as a restaurant review.

**Tuning the cut-offs.** Sweeping `MIN_SIMILARITY` (0.22 to 0.40),
`MAX_VECTOR_HITS` (12 to 40) and the RRF `K` (10 to 60) moved nDCG by at most
±0.01. 0.36 made negatives quieter, but it also cuts the dish query's reels,
which sit at 0.27 to 0.30. Left at 0.27 / 24 / 60.

**Query expansion with the local LLM** (HyDE: write the summary of a reel that
would answer the query, embed that). For the dish query, qwen2.5:3b writes a
summary that names the food, which would work, but takes 400 to 900 ms per
search on this box and makes things up (a recipe no creator in the library
posted). Over budget.

**Document expansion** (doc2query: the LLM writes eight searches per reel at
index time, embedded with the summary). 28 minutes over the library and no gain
(0.898 to 0.904 at best), with voiceover worse (0.88 to 0.79). A 3B model
restates the reel's title rather than adding what someone would search for,
and never wrote the dish's name for any of its reels.

**What would fix the dish query:** the link from the dish's name to the food
is world knowledge that a 300M embedder has only weakly: the dish's name with
the food's puts all four reels in the top five, the dish's name with the city
puts none in the top ten. None of the four reels names the dish or the city
anywhere, so nothing added to them at index time can help: the query has to
learn what the dish is. Accepted as a limit of running without a GPU: query
rewriting works but is too slow on CPU. It is tracked, with the reranker and
doc2query results above, in
[#64](https://github.com/spanthetree/hikidashi/issues/64), to re-measure once
a GPU is added.

## Japanese (#33)

A Japanese UI invites Japanese queries, so four more went into the set: `カフェ`,
`桜`, `カレー` and `京都`. They are in the results tables above; the
per-query numbers here are from the 0.2.0 search.

| query | blended nDCG@10 | |
|-------|----------------:|-|
| `カフェ` | 0.97 | substring hits in Japanese captions, plus an English-only cafe reel from the vectors |
| `桜` | 0.93 | three reels with 桜 in the caption, and two English-only cherry blossom reels from the vectors |
| `カレー` | 0.83 | the curry recipe first; a curry inside a reel about something else is missed |
| `京都` | 0.18 | see below |

embeddinggemma is multilingual, so a Japanese query finds English reels on the
same subject without any translation step: that is how `桜` finds cherry blossom
reels whose text is all in English.

**Substring matching cannot tell words apart.** `京都` (Kyoto) is a substring of
`東京都` (Tokyo Metropolis), so it matches three Tokyo restaurant addresses,
two of them in the top five. Of the two reels fair to show for Kyoto, one ranks
9th and the other not at all. Telling the words apart needs a morphological
tokenizer (MeCab, Sudachi) at index time, which is a new dependency and an
index of its own. Not done: the library has 34 reels with Japanese text, and the
vectors put real matches in the top ten for every other query.

**Recipe titles and ingredients** are matched as substrings too. A recipe you
retitled in Japanese is only in `recipe_fts`, which holds a whole title as one
token.

### Trigram, tried and rejected

FTS5's `trigram` tokenizer indexes every three-character window, so it finds
`ラーメン` inside `東京で一番美味しいラーメン` and ranks the hits by bm25 rather
than by date. Tried as a second index over caption, transcript and OCR text,
in place of the LIKE tier for words of three characters or more (LIKE stays for
`桜` and `京都`, which trigram cannot match):

| | LIKE (kept) | trigram |
|-|------------:|--------:|
| non-English, blended nDCG@10 | 0.88 | 0.88 |
| non-English, keyword nDCG@10 | 0.78 | 0.77 |
| index size | 0.58 MB (`item_fts`) | +1.8 MB |

No query moved in blended search, and trigram finds `京都` in `東京都` just the
same. With LIKE, keyword search takes 2 ms at the median on this library.
