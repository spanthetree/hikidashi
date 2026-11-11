"""Recipes as schema.org `Recipe` JSON-LD, and the bundles recipe managers import.

JSON-LD is what recipe sites embed, so it is what most recipe managers read
from a page. They do not all import a file of it, though, so two bundles carry
the same recipes in the shape each tool already imports:

- **nextcloud**: a ZIP of `<name>/recipe.json` and `<name>/full.jpg` folders,
  as Nextcloud Cookbook keeps them. recipe.json is schema.org JSON-LD. Mealie
  (Settings > Migrations > Nextcloud Cookbook) and Tandoor (Import > Nextcloud
  Cookbook) both read it.
- **paprika**: a `.paprikarecipes` file, a ZIP of gzipped JSON, which is what
  Paprika exports and imports. Mealie and Tandoor read it too.

What the importers need is not always what schema.org says, and both bundles
are written to what their code reads (mealie/services/migrations/, Tandoor's
cookbook/integration/). The notes on each function say what that means.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import re
import uuid
import zipfile
from pathlib import Path
from typing import IO, Any

from . import first_line, ingredient_line, iso_date, title

CONTEXT = "https://schema.org"


def duration(minutes: int | None = None, *, seconds: int | None = None) -> str | None:
    """ISO 8601, as schema.org times are written: PT1H30M, PT45S."""
    total = (minutes or 0) * 60 + (seconds or 0)
    if total <= 0:
        return None
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return "PT" + "".join(f"{n}{u}" for n, u in ((h, "H"), (m, "M"), (s, "S")) if n)


def servings(n: int) -> str:
    return f"{n} serving{'' if n == 1 else 's'}"


def _url(base_url: str | None, path: str | None) -> str | None:
    return f"{base_url.rstrip('/')}/media/{path}" if base_url and path else None


def jsonld(reel: dict[str, Any], *, base_url: str | None = None) -> dict[str, Any]:
    """One reel's recipe as a schema.org Recipe. `base_url` is where this
    server is reached, for the thumbnail's and video's links; without it they
    are left out, since a path alone points nowhere once exported."""
    recipe = reel["recipe"]
    name = title(reel)
    doc: dict[str, Any] = {
        "@context": CONTEXT,
        "@type": "Recipe",
        "name": name,
        # Tandoor's importer reads description without checking it is there.
        "description": reel["summary"] or first_line(reel["caption"], 300) or "",
        "url": reel["permalink"],
        "identifier": reel["shortcode"],
    }
    if reel["author"]:
        doc["author"] = {
            "@type": "Person",
            "name": reel["author_name"] or f"@{reel['author']}",
            "alternateName": f"@{reel['author']}",
            "url": f"https://www.instagram.com/{reel['author']}/",
        }
    image = _url(base_url, reel["thumb_path"])
    if image:
        doc["image"] = [image]
    if recipe["time_minutes"]:
        doc["totalTime"] = duration(recipe["time_minutes"])
    if recipe["servings"]:
        # As words, not a bare number: Mealie reads "4" as four of something,
        # and only "4 servings" as how many it serves.
        doc["recipeYield"] = servings(recipe["servings"])
    doc["recipeIngredient"] = [ingredient_line(i) for i in recipe["ingredients"]]
    # A name on every step: Tandoor reads step["name"] whenever a step is an
    # object, and fails the whole recipe when it is missing.
    doc["recipeInstructions"] = [
        {"@type": "HowToStep", "name": f"Step {n}", "text": text}
        for n, text in enumerate(recipe["steps"], 1)
    ]
    # No keywords: Mealie and Tandoor make a tag of each one, and a reel's
    # hashtags would bury a library's own tags under hundreds of these.
    if reel["kind"] == "reel":
        video: dict[str, Any] = {
            "@type": "VideoObject",
            "name": name,
            "description": doc["description"] or name,
            "embedUrl": reel["permalink"],
        }
        if image:
            video["thumbnailUrl"] = [image]
        if reel["duration"]:
            video["duration"] = duration(seconds=reel["duration"])
        content = _url(base_url, reel["media_path"])
        if content:
            video["contentUrl"] = content
        doc["video"] = video
    return doc


def graph(reels: list[dict[str, Any]], *, base_url: str | None = None) -> dict[str, Any]:
    """Every recipe in one JSON-LD document."""
    docs = [jsonld(r, base_url=base_url) for r in reels]
    for d in docs:
        del d["@context"]
    return {"@context": CONTEXT, "@graph": docs}


def script_tag(doc: dict[str, Any]) -> str:
    """The JSON-LD as a page embeds it. `<` is escaped, so no text in a recipe
    can close the script element early."""
    body = json.dumps(doc, ensure_ascii=False).replace("<", "\\u003c")
    return f'<script type="application/ld+json">{body}</script>'


# --- file names -----------------------------------------------------------------


def slug(text: str, limit: int = 60) -> str:
    """A file name's worth of a title: "Weeknight carbonara" -> weeknight-carbonara."""
    words = re.findall(r"\w+", text.lower())
    return "-".join(words)[:limit].strip("-") or "recipe"


# Tandoor finds a recipe's photo with re.match(f"^(.)+{name}/full.jpg$"), the
# name unescaped, so these in a name would lose its photo; "/" would make a
# folder. Mealie keys recipes by a slug of the name, so two recipes called the
# same would be one.
_UNSAFE = re.compile(r"[()\[\]{}+*?|^$\\/]")


def _names(reels: list[dict[str, Any]]) -> list[str]:
    """A distinct, folder-safe name for each recipe."""
    seen: set[str] = set()
    names = []
    for reel in reels:
        base = " ".join(_UNSAFE.sub(" ", title(reel)).split()) or reel["shortcode"]
        name = base
        if name.lower() in seen and reel["author"]:
            name = f"{base} by {reel['author']}"
        n = 2
        while name.lower() in seen:
            name, n = f"{base} {n}", n + 1
        seen.add(name.lower())
        names.append(name)
    return names


def _ext(reel: dict[str, Any]) -> str:
    return Path(reel["thumb_path"] or "").suffix.lower() or ".jpg"


def _thumb(media_dir: Path, reel: dict[str, Any]) -> bytes | None:
    if not reel["thumb_path"]:
        return None
    root = media_dir.resolve()
    path = (root / reel["thumb_path"]).resolve()
    return path.read_bytes() if path.is_relative_to(root) and path.is_file() else None


# --- bundles ----------------------------------------------------------------------


def write_files(reels: list[dict[str, Any]], out: Path, *, base_url: str | None = None) -> int:
    """One `<name>.json` per recipe in a folder. Returns how many."""
    out.mkdir(parents=True, exist_ok=True)
    for reel in reels:
        doc = jsonld(reel, base_url=base_url)
        path = out / f"{slug(title(reel))}-{reel['shortcode']}.json"
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return len(reels)


def nextcloud(
    reels: list[dict[str, Any]], media_dir: Path, out: IO[bytes], *, base_url: str | None = None
) -> int:
    """A Nextcloud Cookbook ZIP, for Mealie and Tandoor. Returns how many recipes."""
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for reel, name in zip(reels, _names(reels), strict=True):
            doc = jsonld(reel, base_url=base_url)
            doc["name"] = name
            # Tandoor stores recipeYield as the servings, a whole number a recipe
            # must have, so here it is one, as in Nextcloud Cookbook itself.
            doc["recipeYield"] = reel["recipe"]["servings"] or 1
            # In a folder, not at the ZIP's root: Tandoor's photo pattern needs
            # something before the name.
            z.writestr(f"recipes/{name}/recipe.json", json.dumps(doc, ensure_ascii=False, indent=2))
            photo = _thumb(media_dir, reel)
            if photo:
                # Thumbnails are JPEGs, which is all Tandoor looks for.
                z.writestr(f"recipes/{name}/full{_ext(reel)}", photo)
    return len(reels)


def paprika_recipe(reel: dict[str, Any], name: str, photo: bytes | None) -> dict[str, Any]:
    """One recipe as Paprika writes it. Every key is there, as strings where
    Paprika has strings: Tandoor reads notes, nutritional_info and source
    without checking for them, and calls .strip() on the times."""
    recipe = reel["recipe"]
    uid = str(uuid.uuid5(uuid.NAMESPACE_URL, reel["permalink"])).upper()
    minutes = recipe["time_minutes"]
    out: dict[str, Any] = {
        "uid": uid,
        "name": name,
        "description": reel["summary"] or "",
        "ingredients": "\n".join(ingredient_line(i) for i in recipe["ingredients"]),
        # Blank lines between steps: Mealie splits directions on them.
        "directions": "\n\n".join(recipe["steps"]),
        "notes": (
            "The rest of this recipe is elsewhere: the caption says where."
            if recipe["incomplete"]
            else ""
        ),
        "nutritional_info": "",
        "servings": servings(recipe["servings"]) if recipe["servings"] else "",
        "prep_time": "",
        "cook_time": "",
        "total_time": f"{minutes} min" if minutes else "",
        "difficulty": "",
        "rating": 0,
        "source": f"@{reel['author']} on Instagram" if reel["author"] else "Instagram",
        "source_url": reel["permalink"],
        "image_url": "",
        "categories": list(reel["collections"]),
        "created": f"{iso_date(reel['saved_at']) or '1970-01-01'} 00:00:00",
        "photo": f"{uid}{_ext(reel)}" if photo else None,
        "photo_data": base64.b64encode(photo).decode("ascii") if photo else None,
        "photo_hash": hashlib.sha256(photo).hexdigest().upper() if photo else None,
        "photo_large": None,
        "photos": [],
        "scale": None,
        "in_trash": False,
        "is_pinned": False,
        "on_favorites": False,
        "on_grocery_list": None,
    }
    out["hash"] = hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest().upper()
    return out


def paprika(reels: list[dict[str, Any]], media_dir: Path, out: IO[bytes]) -> int:
    """A .paprikarecipes file: a ZIP with one gzipped JSON recipe per entry."""
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as z:
        for reel, name in zip(reels, _names(reels), strict=True):
            doc = paprika_recipe(reel, name, _thumb(media_dir, reel))
            # mtime=0, so the same library writes the same bytes.
            data = gzip.compress(json.dumps(doc, ensure_ascii=False).encode("utf-8"), mtime=0)
            z.writestr(f"{name}.paprikarecipe", data)
    return len(reels)
