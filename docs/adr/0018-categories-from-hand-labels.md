# 0018. Categories come from hand labels

- **Status:** Accepted
- **Date:** 2026-11-12
- **Issue:** #145; [tags.md](../tags.md#four-more-categories)

## Context

The #21 audit cut the list to ten categories. It removed Music and Pets
because almost nothing carried them, judging by the tags the model and the
hashtags had given. That method has a blind spot. A kind of reel that no
category covers gets no tag at all, so it never shows up as a count.

Hand-labelling 50 reels for #6 found that gap. Seven of the 50 fitted none of
the ten, and five of those were films, series or anime. Of the 19 untagged
reels in the library with a summary, 16 fell into four groups:

| group | untagged reels |
|---|---|
| films, series, anime | 6 |
| parenting, relationships, mental health | 5 |
| the night sky, wildlife, landscapes | 3 |
| hair and clothes | 2 |

## Decision

Add **Movies & TV**, **Nature & science**, **Family & wellbeing** and
**Style & beauty**, each with a note for the model that draws its boundary.
A category earns its place when hand labels show reels that belong in it and
have nowhere else to go, not when the existing tags happen to use it.

Each new category's hashtag stems must not take reels from the ten already
there. Checked on the library before merging, they move one reel (a film list
from Design to Movies & TV, which is right) and tag twelve that had none.

## Alternatives

- **Keep ten, and let "none" stand.** Then a quarter of what the labels found
  stays unfiltered, and the Movies & TV reels are the biggest single group of
  untagged reels.
- **One broad "Culture" or "Lifestyle" category.** It would hold films, poems,
  parenting and hairstyles together, which is the same drift into meaningless
  filters that #21 undid.
- **Bring back Music and Pets.** The labels did not show them: no labelled reel
  was about music, and the one pet reel is better kept as "none" than filed
  as Nature & science.

## Consequences

- `TAG_V` 4 and `HASHTAG_V` 3: every library is re-tagged with the new list.
  Your own tags are untouched.
- More categories give the model more ways to be wrong on a borderline reel.
  The hand labels measure it: `python -m app.tagging --eval` before and after
  the re-tag.
- The next category to add, or remove, should be argued the same way: from
  labels, with the counts.
