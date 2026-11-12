# 0018. Categories come from hand labels

- **Status:** Accepted
- **Date:** 2026-11-12
- **Issue:** #145; [tags.md](../tags.md#six-more-categories)

## Context

The #21 audit cut the list to ten categories. It removed Music and Pets
because almost nothing carried them, judging by the tags the model and the
hashtags had given. That method has a blind spot. A kind of reel that no
category covers gets no tag at all, so it never shows up as a count.

Hand-labelling 50 reels for #6 found that gap. Seven of the 50 fitted none of
the ten, and five of those were films, series or anime. Re-tagging the library
with the model then left 36 of its 411 reels with no category. Read one by
one, all but a handful were about something real: films, parenting and
relationships, wildlife and the sky, clothes and hair, where to live, and
shops worth visiting.

## Decision

Every reel with something to go on should have a category. "Other" is kept
only for reels too thin to tell what they show, such as a two-word caption
with no speech or on-screen text.

Six categories were added, each with a note for the model that draws its
boundary:

- Movies & TV
- Nature & science
- Family & wellbeing
- Style & beauty
- Home & living
- Shopping

A category earns its place when hand labels or the untagged reels show reels
that belong in it and have nowhere else to go, not when the existing tags
happen to use it.

Each new category's hashtag stems must not take reels from the categories
already there. Before merging they were checked on the library: they move two
reels, a film list from Design and a fake-reviews tool for Amazon shopping
from Technology, and tag fifteen that had none.

## Measured

On the 50 hand labels, with qwen2.5:7b re-tagging the whole library each
time:

| list | right | answered | library left without a category |
|---|---|---|---|
| the ten | 31/50 | 40/50 | 36 reels |
| the sixteen | 36/50 | 49/50 | 5 reels (4 too thin to tell) |

The model's answers vary by about 7% of reels from one run to the next. A
difference of one or two labels is noise, and four runs with the new list
scored 34 to 36.

The cost is at Travel's edges. A landscape can drift to Nature & science and
a city's shops to Shopping, so each note says where the line is: a view or a
place is Travel. Food at a destination goes to Food, as it did before.

## Alternatives

- **Keep ten, and let "none" stand.** Then 36 reels stay unfiltered, the
  films being the largest group.
- **One broad "Culture" or "Lifestyle" category.** It would hold films, poems,
  parenting and hairstyles together, which is the same drift into meaningless
  filters that #21 undid.
- **Bring back Music and Pets.** Neither the labels nor the untagged reels
  showed them. A pet video still fits nothing, and is better as Other than
  filed as wildlife.
- **Force a category on every reel.** For a two-word caption the model can
  only guess, and a wrong category is worse than none ([ADR 0006](0006-one-primary-tag-per-reel.md)).

## Consequences

- `TAG_V` 4 and `HASHTAG_V` 3: every library is re-tagged with the new list.
  Tags you set yourself are kept.
- More categories give the model more ways to be wrong on a borderline reel.
  The hand labels measure that: run `python -m app.tagging --eval` after a
  re-tag.
- The next category to add or remove should be argued the same way: from
  labels and untagged reels, with the counts.
