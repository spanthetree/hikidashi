# 0006. One primary tag per reel

- **Status:** Accepted. Its order of sources **superseded by** [0014](0014-game-titles-from-evidence.md), which puts the game pass's answer before the model's
- **Date:** 2026-10-22
- **Issue:** #21; [tags.md](../tags.md)

## Context

By #21 the tag list had drifted. It had 41 tags: the 12 categories, plus 29
accepted proposals that mixed places (`texas`), dishes (`birria`), car models
(`corolla`) and vague words (`views`). Reels carried up to five tags, 96 had
hashtag and model tags that disagreed, and as filters `texas` sat beside
`Travel` as if they were the same kind of thing.

## Decision

Each reel has **one primary tag**, its single most relevant category, or none.
Everything more specific is a cleaned hashtag or a model keyword, and is
searched rather than listed. `item_tag` holds one answer per source
(`hashtag`, `model`), and a person's own choice is theirs alone in
`user_item.tag_id`. A reel shows yours, else the model's, else the hashtags'.

## Alternatives

- **Many tags per reel.** It is what had drifted. Filters stop meaning
  anything once they overlap, and the model and the hashtags disagree on
  which ones apply.
- **Proposal tags as categories.** Places, dishes and models are better found
  by search, which already indexes hashtags and keywords.

## Consequences

- The sidebar, cards and the Recipes page key off one category.
- Re-running a source replaces only its own row, and your corrections survive
  every re-run, which also makes them a usable evaluation set
  (`app/tagging/evaluate.py`).
- `item.primary_tag` is kept by triggers so FTS5 can index it.
