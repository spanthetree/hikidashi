"""Asking a local LLM whether a reel is a recipe, and if so, for the recipe.

Two requests, not one. The check is a yes or no with a confidence, capped at a
few dozen tokens; only a yes pays for the extraction, which can run to a
thousand. Most of the cost is output, so restaurant reviews and mukbangs, a
third of the Food reels, stay cheap. And a schema that must always hold a recipe
invites the model to write one.

Nothing the model says about ingredients is taken on trust. It copies each
one's words out of the text (`as_written`) next to its English name, and
`ground` drops any whose words appear in no source: that is what keeps the
model from adding the garlic every such dish has. `source_fields` is worked out
the same way, from where the words were found, rather than asked for, and so is
`incomplete` (see `read`).

Empty strings and zeros stand for "not given" in the schema. Every JSON schema
implementation Ollama has shipped handles those; not all handle nullable types.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from app import ollama
from app.chat import Chat

from .detect import sends_elsewhere
from .groups import normalise

# Bumping this schedules every item for the recipe pass again, recipes or not.
# Change it with a prompt, a schema, the parsing or the candidate filter.
# 6: each recipe is translated into the app's languages as it is read (#114).
RECIPE_V = 6

# {"dish": "southern indian beef curry", "edible": true, "is_recipe": true, "confidence": "high"}
CHECK_PREDICT = 60
EXTRACT_PREDICT = 3000  # a cap, not a cost: 25 ingredients ran past 1,500
CONFIDENCE = {"high": 0.9, "medium": 0.6, "low": 0.3}
MIN_CONFIDENCE = CONFIDENCE["medium"]
# A reel the tagger did not call Food is taken on a firm yes only: asked about
# anything with the word "recipe" in it, a 3B model found one in a
# materials-science video (#97).
MIN_CONFIDENCE_NOT_FOOD = CONFIDENCE["high"]
MAX_INGREDIENTS = 40
MAX_STEPS = 30

RecipeError = ollama.ReplyError

CHECK_SYSTEM = """\
You sort saved Instagram reels. You are given a reel's caption, hashtags,
transcript and the text shown on screen, any of which may be missing, noisy or
in another language. Reply with JSON only.

- dish: what the reel shows or tells how to make, in a few English words; "" if
  it makes nothing.
- edible: true when that is something to eat or drink. false for anything
  else, however it is made: wood, soap, paint, a craft.
- is_recipe: true when the reel shows or tells how to make one particular dish,
  drink or sauce, so that someone could cook it, even if the exact amounts are
  somewhere else ("link in bio"). false for food the viewer is not shown how to
  make: restaurant visits and reviews, food sold at a shop, stall, market or
  festival, eating videos, food tours, "best tacos in town", product reviews,
  diet advice, kitchen gadgets, lists of dishes. false for kitchen tips that
  make no dish: washing, storing or buying food, food safety.
- confidence: high, medium or low."""

EXTRACT_SYSTEM = """\
You copy the recipe out of an Instagram reel into a structured form. You are
given its caption, transcript and the text shown on screen. Use only what that
text says. Reply with JSON only.

- title: the name of the dish in a few plain English words.
- servings: how many it serves, if the text says; otherwise 0.
- time_minutes: the total time in minutes, if the text says; otherwise 0. Do not
  estimate.
- ingredients: each ingredient the text names, once, in the order it names them.
  - item: its name in English, lowercase, without the amount.
  - as_written: the words the text uses for it, copied exactly, in its language.
  - quantity: the amount exactly as written, including words like "a splash" or
    "to taste". "" if none is given.
  - unit: as written, like "g", "tbsp", "cups", "cloves". "" if none.
  - note: preparation or a substitute, as written. "" if none.
  Never add an ingredient the text does not name, even one the dish usually has.
  If the text names none, the list is empty.
- steps: the method in order, as short instructions in English. Only the steps
  the text gives; [] if it gives none."""


def check_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "dish": {"type": "string"},
            "edible": {"type": "boolean"},
            "is_recipe": {"type": "boolean"},
            "confidence": {"type": "string", "enum": list(CONFIDENCE)},
        },
        "required": ["dish", "edible", "is_recipe", "confidence"],
    }


def extract_schema() -> dict[str, Any]:
    text = {"type": "string"}
    return {
        "type": "object",
        "properties": {
            "title": text,
            "servings": {"type": "integer"},
            "time_minutes": {"type": "integer"},
            "ingredients": {
                "type": "array",
                "maxItems": MAX_INGREDIENTS,
                "items": {
                    "type": "object",
                    "properties": {
                        "item": text,
                        "as_written": text,
                        "quantity": text,
                        "unit": text,
                        "note": text,
                    },
                    "required": ["item", "as_written", "quantity", "unit", "note"],
                },
            },
            "steps": {"type": "array", "items": text, "maxItems": MAX_STEPS},
        },
        "required": ["title", "servings", "time_minutes", "ingredients", "steps"],
    }


@dataclass
class Ingredient:
    item: str
    quantity: str | None = None
    unit: str | None = None
    note: str | None = None
    as_written: str | None = None


@dataclass
class Recipe:
    title: str | None
    servings: int | None = None
    time_minutes: int | None = None
    ingredients: list[Ingredient] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    incomplete: bool = False


@dataclass
class Found:
    """A recipe, checked against its sources."""

    recipe: Recipe
    confidence: float
    source_fields: list[str]
    dropped: list[str]  # ingredients whose words no source contains
    # The recipe in the app's other languages (app/recipes/translate.py).
    translations: dict[str, Any] = field(default_factory=dict)


class Model(Protocol):
    def check(self, prompt: str) -> str: ...
    def extract(self, prompt: str) -> str: ...


def build_prompt(
    caption: str | None, hashtags: list[str], transcript: str | None, ocr_text: str | None
) -> str | None:
    """The user message for both requests. None when there is nothing to read."""
    parts = []
    if caption and caption.strip():
        parts.append(f"Caption:\n{caption.strip()}")
    if transcript and transcript.strip():
        parts.append(f"Transcript:\n{transcript.strip()}")
    if ocr_text and ocr_text.strip():
        parts.append(f"Text on screen (OCR, may contain errors):\n{ocr_text.strip()}")
    if not parts:
        return None
    if hashtags:
        parts.insert(1, "Hashtags: " + " ".join(f"#{h}" for h in hashtags))
    return "\n\n".join(parts)


def _object(reply: str) -> dict[str, Any]:
    try:
        data = json.loads(reply)
    except json.JSONDecodeError as exc:
        raise RecipeError(f"reply is not JSON: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise RecipeError("reply is not a JSON object")
    return data


def _text(value: object, limit: int = 200) -> str | None:
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())[:limit]
    return value or None


def _count(value: Any) -> int | None:
    # A model that ignores the schema may send "4" or 4.0.
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return None
    return n if 0 < n < 10_000 else None


_LETTERS = r"[^\W\d_]"


def _amount(quantity: str | None, unit: str | None) -> tuple[str | None, str | None]:
    """Say the unit once. Given "200g", a model writes "g" again as the unit,
    and "2 tsp" comes back as quantity "2 tsp", unit "tsp"."""
    if quantity and unit:
        words = re.findall(rf"{_LETTERS}+", unit)
        if words and all(
            re.search(rf"(?<!{_LETTERS}){re.escape(w)}(?!{_LETTERS})", quantity, re.I)
            for w in words
        ):
            unit = None
    return quantity, unit


def _note(value: object) -> str | None:
    note = _text(value)
    if note and note.startswith("(") and note.endswith(")"):
        note = note[1:-1].strip() or None
    return note


def parse_check(reply: str, *, food: bool) -> float:
    """The confidence that this is a recipe: 0 for a no, or for a yes too weak
    to keep. `food` is whether the tagger called the reel Food."""
    data = _object(reply)
    if not isinstance(data.get("is_recipe"), bool):
        raise RecipeError("is_recipe must be true or false")
    # A yes that names nothing made is a no, and so is one for something
    # nobody eats, unless the tagger called it Food: a 3B model says pizza
    # dough is not edible.
    if not data["is_recipe"] or not _text(data.get("dish")):
        return 0.0
    if not food and data.get("edible") is False:
        return 0.0
    confidence = CONFIDENCE.get(str(data.get("confidence", "")).lower(), CONFIDENCE["low"])
    return (
        confidence if confidence >= (MIN_CONFIDENCE if food else MIN_CONFIDENCE_NOT_FOOD) else 0.0
    )


# What a model writes when the text names no dish.
_NO_TITLE = frozenset(
    {"no title provided", "no title", "untitled", "unknown", "n/a", "none", "recipe"}
)


def parse_recipe(reply: str) -> Recipe:
    data = _object(reply)
    raw_ingredients = data.get("ingredients") or []
    raw_steps = data.get("steps") or []
    if not isinstance(raw_ingredients, list) or not isinstance(raw_steps, list):
        raise RecipeError("ingredients and steps must be lists")

    ingredients: list[Ingredient] = []
    seen: set[str] = set()
    for raw in raw_ingredients[:MAX_INGREDIENTS]:
        if not isinstance(raw, dict):
            continue
        item = _text(raw.get("item"), 120)
        if not item or item.lower() in seen:
            continue
        seen.add(item.lower())
        quantity, unit = _amount(_text(raw.get("quantity"), 40), _text(raw.get("unit"), 40))
        ingredients.append(
            Ingredient(
                item=item.lower(),
                quantity=quantity,
                unit=unit,
                note=_note(raw.get("note")),
                as_written=_text(raw.get("as_written"), 120),
            )
        )

    steps = [s for s in (_text(x, 1000) for x in raw_steps[:MAX_STEPS]) if s]
    title = _text(data.get("title"), 120)
    if title and title.lower().strip(".") in _NO_TITLE:
        title = None
    return Recipe(
        title=title,
        servings=_count(data.get("servings")),
        time_minutes=_count(data.get("time_minutes")),
        ingredients=ingredients,
        steps=steps,
    )


# Words that say nothing about which ingredient it is, so a source need not
# contain them: "a handful of fresh basil" is grounded by "basil".
_FILLER = frozenset(
    """a an the of and or some fresh freshly chopped sliced diced
    minced grated large small medium your to taste for optional""".split()
)


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("oes", "ches", "shes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


class Sources:
    """The texts a recipe may come from, prepared for looking words up."""

    def __init__(self, **texts: str | None):
        self.texts = {k: normalise(v) for k, v in texts.items() if v and v.strip()}
        self.stems = {k: {_stem(w) for w in t.split()} for k, t in self.texts.items()}

    def where(self, phrase: str | None) -> list[str]:
        """The sources that name this phrase."""
        p = normalise(phrase or "")
        if not p:
            return []
        if not p.isascii():
            # No spaces to split on in Japanese or Thai; a substring is the test.
            return [k for k, t in self.texts.items() if p in t]
        words = [_stem(w) for w in p.split() if w not in _FILLER] or [_stem(w) for w in p.split()]
        return [k for k, stems in self.stems.items() if all(w in stems for w in words)]


def ground(recipe: Recipe, sources: Sources) -> tuple[list[str], list[str]]:
    """Drop the ingredients no source names. Returns (source fields, dropped).

    Either the model's copy of the words or its English name will do: copying
    can fail on a misread word, and in an English caption the two are the same.
    """
    kept, dropped, fields = [], [], set()
    for ing in recipe.ingredients:
        where = sources.where(ing.as_written) or sources.where(ing.item)
        if where:
            kept.append(ing)
            fields.update(where)
        else:
            dropped.append(ing.item)
    recipe.ingredients = kept
    order = ["caption", "transcript", "ocr"]
    return [f for f in order if f in fields], dropped


def read(
    model: Model,
    caption: str | None,
    hashtags: list[str],
    transcript: str | None,
    ocr_text: str | None,
    *,
    food: bool,
) -> Found | None:
    """The reel's recipe, or None when it is not one. `food`: tagged Food."""
    prompt = build_prompt(caption, hashtags, transcript, ocr_text)
    if prompt is None:
        return None
    confidence = parse_check(model.check(prompt), food=food)
    if not confidence:
        return None
    recipe = parse_recipe(model.extract(prompt))
    fields, dropped = ground(recipe, Sources(caption=caption, transcript=transcript, ocr=ocr_text))
    # Not asked of the model, which flagged complete recipes: incomplete is
    # nothing to cook from, or a pointer to the rest somewhere else.
    recipe.incomplete = (
        not recipe.ingredients or not recipe.steps or sends_elsewhere(caption, ocr_text)
    )
    return Found(recipe=recipe, confidence=confidence, source_fields=fields, dropped=dropped)


class Reader:
    """A `Model` on any chat backend with `ask` (app/chat.py)."""

    def ask(
        self, system: str, prompt: str, *, schema: dict[str, Any], max_tokens: int, name: str
    ) -> str:
        raise NotImplementedError

    def check(self, prompt: str) -> str:
        return self.ask(
            CHECK_SYSTEM, prompt, schema=_CHECK, max_tokens=CHECK_PREDICT, name="recipe_check"
        )

    def extract(self, prompt: str) -> str:
        return self.ask(
            EXTRACT_SYSTEM, prompt, schema=_EXTRACT, max_tokens=EXTRACT_PREDICT, name="recipe"
        )


class Ollama(ollama.Client, Reader):
    """The recipe pass on one model on an Ollama server."""


class Hosted(Chat, Reader):
    """The recipe pass on a hosted provider's model (#116)."""


_CHECK, _EXTRACT = check_schema(), extract_schema()
