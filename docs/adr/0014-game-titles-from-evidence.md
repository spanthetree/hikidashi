# 0014. A reel's video game, decided from evidence

- **Status:** Accepted. Supersedes the precedence in [0006](0006-one-primary-tag-per-reel.md): a reel now shows yours, else the game pass's, else the model's, else the hashtags'
- **Date:** 2026-11-10
- **Issue:** #117; [tags.md](../tags.md#games), [pipeline.md](../pipeline.md#which-game)

## Context

A gaming reel is mostly about one game, and one category, Gaming, says
nothing about which. The ask was to name the game when over 95% sure, and
otherwise to ask the person who saved the reel. The tagger's model is a local
7B ([0009](0009-local-models-only.md)), and asked how sure it is, it says
"high" to nearly everything: its confidence is a ranking, not a probability.

## Decision

The tag stage asks the reels worth asking (filed under Gaming or Sim racing,
or whose text reads like video games) which game they show, and checks the
title with nothing else to go on. **The server decides** whether to apply it,
from evidence the model cannot talk its way past: one game, high confidence,
a title the check can place, no other game it can place named in the text,
and the title in the reel's own caption, hashtags, on-screen text or
transcript. Anything short of that is a suggestion, which the detail sheet
asks about. A person's answer is theirs alone (`user_item.game`), like their
tag, and outranks the pass.

A sure game on a reel the tagger filed elsewhere makes it a gaming reel, as a
`game` source in `item_tag` ranked above the model's. The reel still has one
category ([0006](0006-one-primary-tag-per-reel.md)); the game is beside it,
not a second tag.

It runs inside the tag stage, a second request for candidates, not as a
stage of its own.

## Alternatives

- **Trust the model's confidence.** On this library it would have applied a
  misspelt title and a game the reel only shared a hashtag with.
- **A games database** (IGDB, RAWG, Wikidata) for official titles. Better
  names, but every gaming reel's text, or its title, would leave the machine,
  and it needs a key or a dependency on a public service. Asking the same
  model for the official title, with nothing else to go on, got Gran Turismo 4
  right and refused to place what it did not know.
- **A stage of its own.** Its own version and queue, but a new name in
  `HIKIDASHI_LOCAL_STAGES`, every compose file and NAS template, the remote
  protocol's stage list and the diagrams, for two short requests on one reel
  in ten that the tagger has just read.
- **Games as tags.** Many tags per reel is what 0006 undid.

## Consequences

- `TAG_V` is 3: upgrading re-tags every reel once.
- 95% is a target, not a measurement. Six of six sure answers were right on
  39 candidates; the rule is strict so that its errors are questions.
- A remote worker sends the model's answer, not a verdict, and cannot make a
  title sure.
- A guess is not indexed for search: it is not yet the reel's game.
