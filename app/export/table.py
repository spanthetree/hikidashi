"""The library as plain JSON or CSV, for scripting.

JSON is a list with one object per reel, nested as it comes: hashtags and
collections are lists, a recipe is an object. CSV has one row per reel and
flattens the same: lists joined with "; ", a recipe's ingredients and steps
one to a line within their cell.
"""

from __future__ import annotations

import csv
import json
from typing import IO, Any

from . import ingredient_line, iso_time, title


def record(reel: dict[str, Any]) -> dict[str, Any]:
    """One reel as the JSON has it: what it is and what you made of it, without
    the paths it has on this server."""
    recipe = reel["recipe"]
    return {
        "shortcode": reel["shortcode"],
        "permalink": reel["permalink"],
        "type": reel["kind"],
        "title": title(reel),
        "author": reel["author"],
        "author_name": reel["author_name"],
        "saved_at": iso_time(reel["saved_at"]),
        "tag": reel["tag"],
        "hashtags": reel["hashtags"],
        "collections": reel["collections"],
        "summary": reel["summary"],
        "keywords": reel["keywords"],
        "caption": reel["caption"],
        "transcript": reel["transcript"],
        "ocr_text": reel["ocr_text"],
        "duration": reel["duration"],
        "like_count": reel["like_count"],
        "places": [{k: p[k] for k in ("text", "lat", "lng")} for p in reel["places"]],
        "recipe": recipe
        and {
            "title": recipe["title"],
            "servings": recipe["servings"],
            "time_minutes": recipe["time_minutes"],
            "ingredients": [
                {k: i[k] for k in ("item", "quantity", "unit", "note")}
                for i in recipe["ingredients"]
            ],
            "steps": recipe["steps"],
            "incomplete": recipe["incomplete"],
            "edited": recipe["edited"],
        },
    }


def write_json(reels: list[dict[str, Any]], out: IO[str]) -> None:
    json.dump([record(r) for r in reels], out, ensure_ascii=False, indent=2)
    out.write("\n")


COLUMNS = (
    "shortcode",
    "permalink",
    "type",
    "title",
    "author",
    "author_name",
    "saved_at",
    "tag",
    "hashtags",
    "collections",
    "summary",
    "keywords",
    "caption",
    "transcript",
    "ocr_text",
    "duration",
    "like_count",
    "places",
    "recipe_title",
    "recipe_servings",
    "recipe_minutes",
    "recipe_ingredients",
    "recipe_steps",
)


def row(reel: dict[str, Any]) -> dict[str, Any]:
    flat = record(reel)
    recipe = reel["recipe"] or {}
    for key in ("hashtags", "collections", "keywords"):
        flat[key] = "; ".join(flat[key])
    flat["places"] = "; ".join(p["text"] for p in reel["places"])
    flat["recipe_title"] = recipe.get("title")
    flat["recipe_servings"] = recipe.get("servings")
    flat["recipe_minutes"] = recipe.get("time_minutes")
    flat["recipe_ingredients"] = "\n".join(
        ingredient_line(i) for i in recipe.get("ingredients", [])
    )
    flat["recipe_steps"] = "\n".join(recipe.get("steps", []))
    return {k: flat.get(k) for k in COLUMNS}


def write_csv(reels: list[dict[str, Any]], out: IO[str]) -> None:
    w = csv.DictWriter(out, fieldnames=COLUMNS)
    w.writeheader()
    w.writerows(row(r) for r in reels)
