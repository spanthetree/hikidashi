# Tags

What a tag is, what was decided about each one in the October 2026 audit
([#21](https://github.com/spanthetree/hikidashi/issues/21)), and how the result is measured.
[Back to the README](../README.md)

## The model

Each reel has:

1. **One tag**, its primary category, from a short fixed list, or none when
   nothing fits. This is what the sidebar, the card chip and the Recipes
   candidate filter use.
2. **Its cleaned hashtags**: the post's own, normalised (`#Easy_Recipe` →
   `easyrecipe`), with engagement bait (`#fyp`, `#reels`) dropped and each kept
   once. Shown on cards and in the detail sheet, and filterable. The raw
   hashtags stay in `item.hashtags`, untouched.
3. **Keywords**: the model's free text — dishes, places, car models. This is
   where everything more specific than a category lives.

All three are in the full-text index, so a search for `birria`, `jdm` or
`food` finds the reel whichever of them says it.

### Where the tag comes from

Up to four sources answer: yours in `user_item`, the rest one row each in
`item_tag`:

| source  | written by                         | re-run by                 |
|---------|------------------------------------|---------------------------|
| user    | you, in the detail sheet           | nothing, ever             |
| game    | the tagger, Gaming for a [sure game](#games) on a reel filed elsewhere | a `TAG_V` bump, `--force` |
| model   | `./hikidashi tag --llm`            | a `TAG_V` bump, `--force` |
| hashtag | `./hikidashi tag`                  | a `HASHTAG_V` bump        |

The reel shows yours, else the game pass's, else the model's, else the
hashtags'. So the hashtags
only decide when the model has not run or answered `Other`, and when the two
disagree the model wins. Triggers keep the answer in `item.primary_tag`.

Each source re-running replaces only its own row, so the model and the
hashtags can be compared with each other, and both with you.

## The audit

The library on 22 October 2026, before the change: 420 items and 41 tags, all
`kind='taxonomy'`, which means 12 categories and 29 accepted proposals. 183
items had one tag, 134 had two, 70 had three to five, and 33 had none.

### Categories

| tag        | items | decision | why |
|------------|------:|----------|-----|
| Travel     | 154 | keep | the largest group |
| Food       | 107 | keep | also the Recipes candidate filter ([#13](https://github.com/spanthetree/hikidashi/issues/13)) |
| Automotive |  68 | keep | |
| Technology |  51 | keep | absorbs the `websites` and `hacking` proposals |
| Design     |  34 | keep | |
| Gaming     |  32 | keep | |
| Sim racing |  13 | keep | see below |
| Home & DIY |   9 | keep | |
| Finance    |   6 | keep | small, but nothing else covers it |
| Fitness    |   5 | keep, watch | 3 of the 5 were high-protein recipes, which are Food now; 2 remain |
| Music      |   1 | **remove** | the one item is a car video set to synth music: Automotive |
| Pets       |   0 | **remove** | tagged nothing |

**Sim racing stays separate** rather than folding into Gaming or Automotive.
Its 13 reels are a tight group (rigs, wheels, shifters, pedals), and under one
tag per reel they would otherwise be split: hashtagged `#simracing #gaming`,
or a rig built around a real car's seat, they would land in either Gaming or
Automotive almost at random. When hashtags tie, the hashtag pass now prefers
the narrower category, so `#simracing #gaming` is Sim racing.

The model is the weak point here. It chose Sim racing for only 2 of the 13,
once for a flight simulator. So after the migration, where the model outranks
the hashtags, only 5 reels show Sim racing. The other 8 show what the old
model said, mostly Automotive. The next `./hikidashi tag --llm` re-tags every
reel (`TAG_V` 2), with one category and its definition. Check the result
with `--eval` once [#6](https://github.com/spanthetree/hikidashi/issues/6) has labels. If the model still misfiles sim rigs, fold
the category.

A removed category is gone from both passes and the model's choices. An old
model answer naming one became a keyword.

### Former proposals

Every accepted proposal is **demoted to a keyword** on the reels it tagged,
whichever source had attached it. None became a category. They fell into four
kinds, none of them a category:

| kind     | what they were |
|----------|----------------|
| places   | 5 countries, states and cities |
| food     | 11 dishes and ingredients |
| cars     | 4 models and kinds of driving |
| other    | 9 travel and general words |

All 29 still find every reel they tagged, as a search (keywords only, no
vectors), checked on a copy of the library after migrating. The one that
nearly did not was a two-word tag the model had written as one word
(`bucketlist` for `bucket list`), which is a single search token, so the
migration adds the spaced form too.

Keywords are model output, and the next `tag --llm` rewrites them. For 186 of
the 195 reels that does not matter, because the caption, transcript, summary
or a hashtag also says the word. The other 9 are found only through the
keyword: one two-word tag on 3 of them, and six other tags on one each. Those stay findable only if the
model names them again, or semantic search places them close enough. Search
for them again after the next model run.

Proposals that were never accepted, and dismissed ones, were deleted.
Proposing is gone. A new category is either a code change to
`app/tagging/taxonomy.py`, which should be rare, or a name you type as a
reel's tag, which becomes a filter of its own.

### After

| tag        | items |
|------------|------:|
| Travel     | 126 |
| Food       | 105 |
| Automotive |  51 |
| Technology |  42 |
| Design     |  24 |
| Gaming     |  20 |
| Finance    |   6 |
| Home & DIY |   5 |
| Sim racing |   5 |
| Fitness    |   2 |
| none       |  34 |

The model and the hashtags disagree on 34 reels. The model's row is missing
on many reels where the two agreed, because the old schema allowed only one
row per tag per item and the hashtag pass usually wrote it first. The next
model run fills those in.

## Six more categories

November 2026, [#145](https://github.com/spanthetree/hikidashi/issues/145),
[ADR 0018](adr/0018-categories-from-hand-labels.md). Hand-labelling 50 reels
for [#6](https://github.com/spanthetree/hikidashi/issues/6) found 7 that fitted
none of the ten, 5 of them films, series or anime. Re-tagging the library left
36 reels with no category, and nearly all of them were about something real.
Every reel with something to go on should have a category, so six were added:

| tag | what it covers | where the line is |
|---|---|---|
| Movies & TV | films, series and anime: recommendations, reviews, scenes, how they were made | |
| Nature & science | wildlife, the night sky, weather, science explainers | a view, a landscape or a place is Travel; gardening is Home & DIY |
| Family & wellbeing | parenting, relationships, mental health, mindfulness, encouragement | |
| Style & beauty | fashion, outfits, hair, makeup, skincare, grooming | |
| Home & living | neighbourhoods, moving to a city, renting or buying a home to live in | fixing a home is Home & DIY; property as money is Finance |
| Shopping | stores worth visiting, collectibles, gift ideas, buying guides, hauls | |

Travel's note now names attractions and the outdoors (hikes, beaches, national
parks, scenery and views), and Technology's names useful websites and apps.
"Other" stays for reels too thin to tell what they show.

Their hashtag stems were checked not to take reels from the others:

- Plain `#nature` is left out, because travel reels use it as much as wildlife
  ones.
- `#film` matches only whole, so `#filmphotography` stays Design.
- `#shop` matches only whole, so `#workshop` stays Home & DIY.
- `#realestate` stays Finance; a `#…realtor` showing a city to live in is
  Home & living.

On the library the stems moved two reels and tagged fifteen that had none.

### Measured

qwen2.5:7b re-tagged the whole library with each list, scored against the
50 hand labels:

| list | right | answered | left without a category |
|---|---|---|---|
| the ten | 31/50 | 40/50 | 36 |
| the sixteen | 36/50 | 49/50 | 5, of which 4 are too thin to tell |

Two runs of the same list differ on about 7% of reels, so read a difference
of one or two labels as noise. Most of the misses left are at Travel's edges:
food at a destination goes to Food, and a landscape now and then goes to
Nature & science. Sim racing and Gaming still swap now and then, as before.

`TAG_V` 4 and `HASHTAG_V` 3 re-tag every library with the new list.

## Games

The one category stays one ([#21](https://github.com/spanthetree/hikidashi/issues/21)), but a gaming reel is mostly about one game,
which is worth knowing and finding. Since [#117](https://github.com/spanthetree/hikidashi/issues/117) the tagger asks those reels
which game they show; [the pipeline](pipeline.md#which-game) has the questions
and the rule. The rule was set on a real library's 39 candidates (32 filed under
Gaming or Sim racing, 7 by their text), each read by hand:

| | Reels | Right |
|---|---:|---:|
| sure, applied | 6 | 6: Minecraft ×2, The Legend of Zelda: Ocarina of Time, Gran Turismo 4, Assetto Corsa ×2 |
| guess, to confirm | 7 | 6: Cyberpunk 2077, Starfield, REPLACED, Starpath, Deadstick, Assetto Corsa. Not "Exodos", a DOS collection |
| no game | 26 | D&D, board games, rigs and shifters, game lists, a browser emulator; one miss, a World of Tanks reel the model would not name |

Each part of the rule is there for a reel that broke it without it:

- **The title in the reel's text.** Without it, a reel whose caption says only
  "#cyberpunk" was a sure Cyberpunk 2077. One word must be written as a name
  (Control, not "take control") or be a hashtag.
- **No other game named.** A rig reel tagged #iracing and #assettocorsa was a
  sure Assetto Corsa. The model's list of others is itself a 7B model's (a car
  make, the same game spelt as a hashtag, a game nowhere in the text), so an
  other counts only when the check places it and the text names it.
- **The check.** The caption's "Grand Turismo 4" was in the text, so it was
  grounded, and wrong. Asked alone, the model corrects it; and a title it
  cannot place (a DOS collection called "Exodos", or an indie newer than the
  model, like Starpath) is never applied, only asked about.

Six of six is not a measured 95%: it is what a small library gives. The rule is
strict so that it errs towards asking. Every miss above is a guess or nothing,
and a guess costs one click.

## Measuring it

```bash
./hikidashi tag --eval                         # your library
./hikidashi tag --eval --db backup.sqlite3     # another one's answers, today's labels
```

Your labels are the tags you set by hand. Choosing a reel's tag in the
detail sheet labels it, even when you choose the one it already shows. The
report gives, for the model, the hashtags and what the reel shows without
you, how many labelled reels each answered and how often it was right.

**Not measured yet.** The library has no hand-set tags, so there is no
precision to report. The calibration set is [#6](https://github.com/spanthetree/hikidashi/issues/6): label about 50 reels across
the categories in the detail sheet, then run `--eval`. To compare the old
tagger, keep a copy of the database from before upgrading and pass it as
`--db`. Its multi-tag answers score under **hit**, meaning your tag was among
them, as well as **exact**.
