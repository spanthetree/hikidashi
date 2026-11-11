# 0015. Filters intersect

- **Status:** Accepted
- **Date:** 2026-11-10
- **Issue:** #117

## Context

Tag, collection and hashtag were one axis at a time: choosing one cleared the
others, so a filter could never silently intersect into an empty grid. The ask
was to choose several and see the reels that have them all. With one tag per
reel ([0006](0006-one-primary-tag-per-reel.md)), two tags at once always find
nothing.

## Decision

Every filter narrows what is already chosen: tag, game, collection and
hashtags, with search and media as before. A reel has one tag, one game and
here one collection, so a second of one of those **replaces** the first; it
has many hashtags, so those **add up**. Choosing an active one takes it off,
and each shows as its own pill. The API takes `tag`, `game` and `hashtag`
repeated and ANDs them, so a link from before reads the same.

## Alternatives

- **Many tags per reel**, so that tags themselves intersect. It reverses
  0006, for a filter that reels' hashtags and game already give.
- **Any of the chosen (OR).** It widens rather than narrows, which is not
  what was asked, and "Food or Travel" is rarely a question.
- **Accumulate tags too.** Each pair would be an empty grid.

## Consequences

- "Gaming + Minecraft", "Food + #ramen + #tokyo" and "Cars + #jdm" are views.
- A combination can still be empty, as a search can: the count says so.
