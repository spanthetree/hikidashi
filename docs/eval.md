# Evals by release

How well search and tagging do, scored the same way at every release
([#57](https://github.com/spanthetree/hikidashi/issues/57)).
[Back to the README](../README.md) · [Search quality](search.md) · [Testing](testing.md)

![Search and tagging scores on the demo library, one point a release](diagrams/eval-history.svg)

<!-- make_diagrams: eval-history -->
| version | date | blended nDCG@10 | keyword nDCG@10 | negatives quiet | tag precision | what moved it |
|---|---|---|---|---|---|---|
| 0.23.0 | 2026-11-12 | 0.995 | 0.749 | 4/4 | 0.923 |  |
| 0.22.0 | 2026-11-12 | 0.995 | 0.749 | 4/4 | 0.923 | six more categories (#145): 2 demo reels relabelled; model tags from before them |
| 0.21.0 | 2026-11-12 | 0.995 | 0.749 | 4/4 | 0.921 | first row: 30 demo queries, 41 labelled reels |
<!-- /make_diagrams -->

## What is scored

Everything here runs on the demo library (`scripts/demo_library.py`), 41
reels made from Wikimedia Commons media, so anyone can rerun it:

- **Search:** 30 graded queries (`tests/search_eval.json`) of six kinds:
  phrase, keyword, paraphrase, place, non-English and voiceover, plus four
  negatives with no good answer. Scored as nDCG@10, MRR and recall@10, the
  same way as on a real library ([Search quality](search.md#measuring-it)).
  "Blended" is what the app searches with; "keyword" is what it falls back to
  before `./hikidashi embed` has run. A negative counts as quiet when it returns
  three reels or fewer.
- **Tagging:** the tag each reel shows, against hand labels
  (`tests/tag_labels.json`,
  [#6](https://github.com/spanthetree/hikidashi/issues/6)). Precision is the
  share of its answers that are right, counting an answer on a reel where no
  category fits as wrong. Five reels fit none.

The library is stored in the repository as `tests/eval/demo.sqlite3`, with
the query vectors beside it, so the numbers come out the same on any machine
with no model installed.

The demo set is small and mostly easy: blended search finds nearly every
reel. Read it as a guard against getting worse, not as a measure of how good
search is on a library of hundreds of reels. [Search quality](search.md) has
that, from a real library's 73 queries. Tagging changes only when the demo
library is built again, since that is when the model runs.

## Running it

```bash
uv run python scripts/evals.py           # today's scores, against the last release
uv run python scripts/evals.py --check   # and exit 1 if one fell too far
```

CI runs `--check` on every pull request and puts the table in the job
summary. It fails when, since the last release:

- blended or keyword nDCG@10 falls by more than 0.02
- any kind of query falls by more than 0.05 (one query in a kind of three to
  seven moving one place)
- fewer negatives stay quiet
- tag precision falls by more than 0.05

A change that should move the numbers says so in its pull request, and its
release records the new ones. The release commit runs
`uv run python scripts/evals.py --record --note "what moved it"`, then
`uv run python scripts/make_diagrams.py`, which redraws the chart and the table
above from `docs/eval/history.json` (CONTRIBUTING.md).

After the demo library is built again, or the query set changes, make the
fixture again: `uv run python scripts/demo_library.py fixture`.

## Scoring your own library

Your own set and labels stay out of the repository:

- **Search:** put graded queries at `data/search_eval.json` (git-ignored), in
  the format of `tests/search_eval.json`, and run
  `uv run python -m app.search.eval`.
- **Tagging:** set a reel's tag in its detail sheet, and it counts as a label.
  `uv run python -m app.tagging --pick 50` lists reels worth labelling: some
  of every tag, and the awkward ones (photo posts, no hashtags, captions not
  in English, the model and the hashtags disagreeing). Then
  `uv run python -m app.tagging --eval` scores the model and the hashtags
  against them.
