# Taking your library elsewhere

Hikidashi can write your library, or just its recipes, in formats other tools
read. Every export reads one person's library as they see it: their tag on
each reel, their collections, and their corrected recipes. A recipe marked
**Not a recipe** is left out.

In the web app there are three ways in:

- **Your account** has an export form: pick a format, what to export (the whole
  library, only reels with recipes, a collection or a tag), and for the
  Markdown vault, whether to put thumbnails and videos in it. An admin can
  also pick whose library to export.
- **Export**, above the grid, exports what the grid shows: a collection, a
  tag, a search, the Recipes view with its ingredient filters, Places.
- **Export** on a reel's sheet exports that reel: its recipe in each recipe
  format, its Markdown note, or its JSON, and **Copy as Markdown** puts the
  note on the clipboard.

`./hikidashi export` writes the same files on the server, and it is the only
one that can bring an Obsidian vault up to date in place.

```bash
./hikidashi export recipes data/exports/recipes             # a .json file per recipe
./hikidashi export recipes data/exports/recipes.json        # all of them in one file
./hikidashi export recipes --format nextcloud data/exports  # for Mealie or Tandoor
./hikidashi export recipes --format paprika data/exports    # for Paprika
./hikidashi export markdown --thumbs data/exports/vault     # an Obsidian vault
./hikidashi export json data/exports/library.json           # every reel, for scripts
./hikidashi export csv data/exports/library.csv             # the same, for spreadsheets
```

Each takes `--user NAME` for someone else's library; the default is the
library's owner. In the web app, only an admin can export someone else's. Under Docker, the output must be under the data folder
(`./data`, or `HIKIDASHI_DATA`), which the container sees as `/data`, and its
files belong to root.

## Recipes

### schema.org JSON-LD

Each recipe is a [schema.org `Recipe`](https://schema.org/Recipe), the format
recipe sites embed in their pages:

| Field | From |
|-------|------|
| `name` | the recipe's title, else the caption's first line |
| `description` | the tagger's one-line summary |
| `url`, `identifier` | the post on Instagram, and its shortcode |
| `author` | who posted it, with a link to their profile |
| `totalTime` | the recipe's time, as ISO 8601: `PT45M` |
| `recipeYield` | its servings: `"4 servings"` |
| `recipeIngredient` | a line each: `"50 g pecorino, finely grated"` |
| `recipeInstructions` | a `HowToStep` each |
| `image`, `video` | the thumbnail, and the reel as a `VideoObject` |

`image` and the video's `contentUrl` link to this server, so a file written on
the command line has them only when `HIKIDASHI_ORIGIN` says where the server is.
They need you signed in, so they work for you and not for others.

The recipe sheet embeds the same JSON-LD in the page while it is open, so a
browser extension or recipe manager that reads recipes off the page can pick it
up.

### For Mealie and Tandoor

`--format nextcloud`, or **For Mealie or Tandoor**, writes a
[Nextcloud Cookbook](https://github.com/nextcloud/cookbook) ZIP: a folder per
recipe, holding its JSON-LD as `recipe.json` and its thumbnail as `full.jpg`.

- **Mealie**: on its *Data Migrations* page, choose *Nextcloud Cookbook* and
  upload the ZIP. Use Migrations, not Backups: Backups only restores a backup
  Mealie made itself, and fails on this ZIP with "something went wrong".
- **Tandoor**: on its *Import* page, choose *Nextcloud Cookbook* and upload
  the ZIP.

### For Paprika

`--format paprika`, or **For Paprika**, writes a `.paprikarecipes` file, the
format Paprika exports and imports: a ZIP of gzipped JSON, one recipe each,
with its photo inside. Import it into Paprika as you would a file another
Paprika exported. Mealie and Tandoor import it too, choosing *Paprika* where
they ask what the file is: in Mealie, on the *Data Migrations* page, as for
the ZIP.

### What was checked, and what differs

Both bundles, from a library of 74 recipes, were imported into Mealie and
Tandoor, each run fresh in Docker (October 2026). All 74 came in each time,
with photos, ingredients and steps, and the Instagram link as the source.
Paprika itself was not tried: it is a paid app, and the file follows the
format its own exports use, which Mealie's and Tandoor's importers also read.

The importers do not all read the same fields, so each bundle is written to
what its importers' code reads:

- **Servings.** Tandoor's Nextcloud importer needs `recipeYield` to be a whole
  number, and Mealie counts only text like "4 servings" as servings. The
  Nextcloud bundle has the number, and 1 for a recipe that doesn't say; Mealie
  reads that as a yield of 1 rather than servings. The Paprika file has the
  words, and both read the servings from it.
- **Time.** Mealie keeps the Nextcloud bundle's time as a duration, and the
  Paprika file's ("45 min") as text. Tandoor's Paprika importer ignores total
  time, and its Nextcloud importer reads only prep and cook time, which
  Hikidashi doesn't have.
- **Names.** Tandoor finds a recipe's photo by its name, so the bundles drop
  characters like `()` and `/` from names, and Mealie keys recipes by name, so
  two recipes called the same get "by @author" or a number after it.
- **Steps.** Tandoor puts a Paprika recipe's steps in one step, and adds the
  source link to its end.
- **Tags.** Neither bundle carries hashtags. Mealie and Tandoor make a tag of
  every keyword, so a reel's hashtags became hundreds of tags. The Paprika
  file's categories are your collections.
- **Pasted JSON-LD.** Mealie's *Create recipe → Import from HTML or JSON*
  takes one recipe's JSON-LD as it is, servings and all.

## Markdown, for Obsidian

`./hikidashi export markdown OUT` writes a folder you can open as an
[Obsidian](https://obsidian.md) vault, or read anywhere Markdown is read:

```
reels/<shortcode>.md      one note per reel
tags/<tag>.md             a note for each tag, author and collection, listing
authors/@<handle>.md        its reels, which each reel links back to: the
collections/<name>.md       graph shows how your saves connect
media/                    thumbnails and videos, if asked for
Hikidashi.md              where to start
```

A reel's note starts with YAML properties: `shortcode`, `aliases` (its title),
`type` (reel or post), `author`, `tag`, `hashtags`, `collections`, `saved_at`,
`permalink`, `places` and `recipe`. Then its links, and its summary, recipe
(ingredients as a checklist, then numbered steps), places, caption, transcript
and on-screen text, each under a heading.

- `--thumbs` copies thumbnails, and every slide of a carousel, into `media/`
  and shows them in the notes.
- `--with-media` copies videos too, and shows the video in its note.
- `--link` makes symlinks to the files in `HIKIDASHI_MEDIA` instead of copies.
  Under Docker they point into the container, so copy there.

**Run it again** on the same folder to bring the vault up to date. Every note
ends in a marker line:

```
%% Hikidashi writes everything above this line. Your notes go below it, and are kept. %%
```

A re-run rewrites what is above the marker and keeps what is below it, and
leaves a note that has not changed untouched, so sync tools see only what did.
Notes are named for the reel's shortcode, which never changes, so links you
make to them keep working. A note whose marker you deleted is left alone, and
so is the note of a reel no longer in your library: both are reported.

A download from the web app is a fresh vault each time, as a ZIP, with or
without images and videos. With images it can be large, hundreds of megabytes
for a few hundred reels, and with videos, gigabytes.

## JSON and CSV

`export json` writes a list with one object per reel: `shortcode`,
`permalink`, `type`, `title`, `author`, `author_name`, `saved_at` (ISO 8601,
UTC), `tag`, `hashtags`, `collections`, `summary`, `keywords`, `caption`,
`transcript`, `ocr_text`, `duration`, `like_count`, `places` (with `lat` and
`lng` when they are on the map), and `recipe` (null, or its title, servings,
time, ingredients and steps). `-` as the file writes to stdout:

```bash
uv run hikidashi export json - | jq -r '.[] | select(.tag == "Travel") | .permalink'
```

Over HTTP, every export is `GET /api/export/<kind>`, with the grid's filters
(`collection`, `tag`, `hashtag`, `q`, `media`, `recipes`, `places`, `has`,
`without`, `max_time`), `shortcode` for one reel, `images` and `videos` for the
vault, and for an admin, `user`. The kinds are `recipes.json`,
`recipes-nextcloud.zip`, `recipes.paprikarecipes`, `markdown.zip`, `note.md`
(one reel's note), `library.json` and `library.csv`.

`export csv` flattens the same to one row per reel: lists are joined with
`; `, and a recipe's ingredients and steps are one to a line within their cell.
