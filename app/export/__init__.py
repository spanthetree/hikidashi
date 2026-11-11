"""Taking a library elsewhere (#56). Stdlib only.

Everything an export writes is read once, here: one person's reels, each with
what they see of it, which is their tag, their collections and their recipe.
The formats are in the modules beside this one. `recipes` writes schema.org
JSON-LD and the bundles Mealie, Tandoor and Paprika import, `markdown` an
Obsidian vault, and `table` plain JSON and CSV.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from typing import Any

from app.library import SHOWN_TAG
from app.places import Place
from app.places.geocoder import CONFIDENT
from app.recipes import service as recipe_store

# One person's reels, newest save first, with what they see of each.
_REELS = f"""
    SELECT item.*, ui.saved_at AS my_saved_at, {SHOWN_TAG} AS shown_tag,
           (SELECT json_group_array(name) FROM
              (SELECT name FROM collection
                WHERE shortcode = item.shortcode AND user_id = :me ORDER BY name)) AS colls,
           (SELECT json_group_array(path) FROM
              (SELECT path FROM item_slide
                WHERE shortcode = item.shortcode ORDER BY position)) AS slide_paths
    FROM item JOIN user_item ui ON ui.shortcode = item.shortcode AND ui.user_id = :me
    WHERE :one IS NULL OR item.shortcode = :one
    ORDER BY ui.saved_at IS NULL, ui.saved_at DESC, item.shortcode"""


def library(
    conn: sqlite3.Connection, user_id: int, shortcode: str | None = None
) -> list[dict[str, Any]]:
    """Every reel in this person's library, as the exports write it. With a
    shortcode, only that one, or none when it is not in their library."""
    places = _places(conn, user_id)
    out = []
    for r in conn.execute(_REELS, {"me": user_id, "one": shortcode}).fetchall():
        recipe = recipe_store.current(conn, r["shortcode"], user_id=user_id)
        out.append(
            {
                "shortcode": r["shortcode"],
                "permalink": r["permalink"],
                # The link says which: /reel/ is a video, /p/ a photo or carousel.
                "kind": "reel" if "/reel/" in r["permalink"] else "post",
                "author": r["author"],
                "author_name": r["author_name"],
                "saved_at": r["my_saved_at"],
                "tag": r["shown_tag"],
                "hashtags": json.loads(r["clean_hashtags"] or "[]"),
                "collections": json.loads(r["colls"] or "[]"),
                "caption": r["caption"],
                "summary": r["summary"],
                "keywords": json.loads(r["keywords"] or "[]"),
                "transcript": r["transcript"],
                "ocr_text": r["ocr_text"],
                "duration": r["duration"],
                "like_count": r["like_count"],
                "state": r["state"],
                "places": places.get(r["shortcode"], []),
                # A recipe they marked "not a recipe" is not one here either.
                "recipe": recipe if recipe and not recipe["hidden"] else None,
                # Relative to HIKIDASHI_MEDIA, for whatever copies the files.
                "thumb_path": r["thumb_path"],
                "media_path": r["media_path"],
                "slides": json.loads(r["slide_paths"] or "[]"),
            }
        )
    return out


def _places(conn: sqlite3.Connection, user_id: int) -> dict[str, list[dict[str, Any]]]:
    """Each reel's places in caption order, with where each is when that is
    known and sure enough to show, as the map has it."""
    out: dict[str, list[dict[str, Any]]] = {}
    for r in conn.execute(
        f"""SELECT p.shortcode, p.text, g.lat, g.lng FROM item_place p
            JOIN user_item ui ON ui.shortcode = p.shortcode AND ui.user_id = ?
            LEFT JOIN geocode g ON g.text = p.text AND g.lat IS NOT NULL
                               AND g.confidence >= {CONFIDENT}
            ORDER BY p.shortcode, p.seq""",
        (user_id,),
    ):
        out.setdefault(r["shortcode"], []).append(
            {**Place(r["text"]).as_dict(), "lat": r["lat"], "lng": r["lng"]}
        )
    return out


def recipes_of(reels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in reels if r["recipe"]]


def iso_date(seconds: int | None) -> str | None:
    """A save time as the day it was, UTC: 2025-10-09."""
    return time.strftime("%Y-%m-%d", time.gmtime(seconds)) if seconds is not None else None


def iso_time(seconds: int | None) -> str | None:
    """A save time to the second, UTC: 2025-10-09T08:26:40Z."""
    return (
        time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds)) if seconds is not None else None
    )


def ingredient_line(ing: dict[str, Any]) -> str:
    """One ingredient as a recipe card writes it: "50 g pecorino, finely grated"."""
    line = " ".join(p for p in (ing.get("quantity"), ing.get("unit"), ing["item"]) if p)
    return f"{line}, {ing['note']}" if ing.get("note") else line


def first_line(text: str | None, limit: int = 80) -> str | None:
    """A caption's opening words, as a title for a reel that has none."""
    for line in (text or "").splitlines():
        line = re.sub(r"\s+", " ", re.sub(r"#\w+", "", line)).strip(" -–—:|·•")
        if line:
            return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"
    return None


def title(reel: dict[str, Any]) -> str:
    """What a reel is called in an export: its recipe's title, the caption's
    first line, the tagger's summary, or failing all of those, who posted it."""
    recipe = reel["recipe"] or {}
    return (
        recipe.get("title")
        or first_line(reel["caption"])
        or first_line(reel["summary"])
        or (f"A reel by @{reel['author']}" if reel["author"] else reel["shortcode"])
    )
