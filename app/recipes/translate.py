"""A recipe in the language someone reads the app in (#114).

The recipe pass already writes the title, the steps and each ingredient's
name in English, whatever language the reel speaks, so an English reader needs
nothing more, except a note copied as written in a script they may not read.
Any other language gets the whole recipe translated: title, steps, and each
ingredient's name and note. Amounts and units stay as they are; the shopping
list and scaling read those.

The person's own AI model translates it (app/assistant.py), once per recipe as
shown and language: `recipe_translation` keeps it by a digest of the recipe,
so a correction is translated afresh and everyone reading the same recipe in
the same language shares one translation.

Stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from typing import Any

from app.chat import Asks

# The languages the app is in, by the code it uses.
LANGUAGES = {
    "en": "English",
    "ja": "Japanese",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
}

# Scripts an English reader may not read: CJK, kana, hangul, Cyrillic, Greek,
# Arabic, Hebrew, Thai, Devanagari.
FOREIGN = re.compile("[Ͱ-ϿЀ-ӿ֐-׿؀-ۿऀ-ॿ฀-๿぀-ヿ㐀-䶿一-鿿가-힯]")

PREDICT = 3000

SYSTEM = """\
You translate recipes. You are given a recipe as JSON and a language. Reply
with the same JSON, every text translated into that language: the title, each
step, and each ingredient's item and note. Keep the same number of
ingredients and steps, in the same order. Translate only; add nothing and
leave nothing out. Keep "" where the input has "". Reply with JSON only."""


def schema(ingredients: int, steps: int) -> dict[str, Any]:
    text = {"type": "string"}
    return {
        "type": "object",
        "properties": {
            "title": text,
            "ingredients": {
                "type": "array",
                "minItems": ingredients,
                "maxItems": ingredients,
                "items": {
                    "type": "object",
                    "properties": {"item": text, "note": text},
                    "required": ["item", "note"],
                },
            },
            "steps": {"type": "array", "minItems": steps, "maxItems": steps, "items": text},
        },
        "required": ["title", "ingredients", "steps"],
    }


def _texts(recipe: dict[str, Any]) -> dict[str, Any]:
    """What gets translated."""
    return {
        "title": recipe.get("title") or "",
        "ingredients": [
            {"item": i.get("item") or "", "note": i.get("note") or ""}
            for i in recipe.get("ingredients") or []
        ],
        "steps": list(recipe.get("steps") or []),
    }


def needed(recipe: dict[str, Any], lang: str) -> bool:
    """Whether a reader of `lang` needs this recipe translated."""
    if lang not in LANGUAGES:
        return False
    if lang != "en":
        return True
    return bool(FOREIGN.search(json.dumps(_texts(recipe), ensure_ascii=False)))


def digest(recipe: dict[str, Any]) -> str:
    whole = json.dumps(_texts(recipe), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(whole.encode("utf-8")).hexdigest()[:16]


def merge(recipe: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    """The recipe with its texts replaced by the translation's, where the
    translation has as many ingredients and steps; otherwise as it was."""
    out = dict(recipe)
    ingredients = recipe.get("ingredients") or []
    said = body.get("ingredients") or []
    if body.get("title"):
        out["title"] = body["title"]
    if len(said) == len(ingredients):
        out["ingredients"] = [
            {**i, "item": t.get("item") or i.get("item"), "note": t.get("note") or i.get("note")}
            for i, t in zip(ingredients, said, strict=True)
        ]
    if len(body.get("steps") or []) == len(recipe.get("steps") or []):
        out["steps"] = body["steps"]
    return out


def cached(conn: sqlite3.Connection, recipe: dict[str, Any], lang: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT body, model FROM recipe_translation WHERE digest = ? AND lang = ?",
        (digest(recipe), lang),
    ).fetchone()
    return {"body": json.loads(row["body"]), "model": row["model"]} if row else None


def translate(
    conn: sqlite3.Connection, recipe: dict[str, Any], lang: str, asks: Asks, label: str
) -> dict[str, Any]:
    """The translation, from the cache or asked of the model and kept.
    Raises what the model raises."""
    if hit := cached(conn, recipe, lang):
        return dict(hit["body"])
    texts = _texts(recipe)
    reply = asks.ask(
        SYSTEM,
        f"Language: {LANGUAGES[lang]}\n\n{json.dumps(texts, ensure_ascii=False)}",
        schema=schema(len(texts["ingredients"]), len(texts["steps"])),
        max_tokens=PREDICT,
        name="recipe_translation",
    )
    body = json.loads(reply)
    if not isinstance(body, dict):
        raise ValueError("the translation is not a JSON object")
    conn.execute(
        """INSERT OR REPLACE INTO recipe_translation (digest, lang, body, model, made_at)
           VALUES (?, ?, ?, ?, ?)""",
        (digest(recipe), lang, json.dumps(body, ensure_ascii=False), label, int(time.time())),
    )
    return body
