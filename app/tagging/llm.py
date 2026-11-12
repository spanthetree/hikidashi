"""Asking a local LLM what a reel is about.

One request per reel to Ollama's /api/chat, over caption, hashtags and
transcript, answered as strict JSON:

    summary      one sentence, written into item.summary and so searchable
    category     one taxonomy name, or Other; the JSON schema makes anything
                 else unrepresentable, not merely discouraged
    confidence   high, medium or low, stored as a number beside the tag
    keywords     free text: the dish, the place, the car model. Everything
                 more specific than a category lives here, and is searchable

A reel filed under Gaming or Sim racing, or whose text reads like video games,
is then asked which game it shows: app/tagging/games.py.

The output is capped, not the prompt. Reading a prompt is fast even on CPU, and
cutting a transcript would cut exactly the reels whose captions say nothing.
Generating is what costs, a token at a time, so `num_predict` is held to what a
one-sentence summary and a handful of words need. `num_ctx` is raised instead,
because Ollama's default context silently drops the front of a long prompt.

Stdlib only: the client, shared with the recipe pass, is app/ollama.py.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app import ollama
from app.ollama import NUM_CTX
from app.ollama import OllamaError as OllamaError  # re-exported

from . import games
from .taxonomy import names

# Bumping this schedules every item for re-tagging by the model. Change it with
# the prompt, the schema or the parsing below.
TAG_V = 4  # 3: the game a gaming reel shows (#117); 4: four more categories (#145)

# Self-reported, so a ranking more than a probability: words, because a 7B
# model asked for a number answers 0.9 to nearly everything.
CONFIDENCE = {"high": 0.9, "medium": 0.6, "low": 0.3}

# The way out. With only taxonomy names to choose from, a schema-constrained 7B
# model nearly always picks one, so a quote about resilience came back as
# Finance, Fitness and Home & DIY. Naming "none of these" as a choice is what
# makes abstaining likely. Parsing drops it.
OTHER = "Other"
MAX_KEYWORDS = 5
NUM_PREDICT = 200  # a 25-word summary and five keywords is ~100 tokens of JSON

# What each category means. A bare name invites a literal reading: given only
# "Sim racing", a 7B model files a flight simulator under it, and "Design"
# collects anything that was built. One line each draws the boundary.
CATEGORY_NOTES: dict[str, str] = {
    "Automotive": "real cars and motorbikes: builds, repairs, driving, motorsport",
    "Design": "visual design as the subject: architecture, interiors, graphic "
    "design, typography, photography, art",
    "Finance": "money: investing, saving, budgeting, property, crypto",
    "Fitness": "exercise and training: workouts, running, yoga, mobility",
    "Food": "food and drink: recipes, cooking, restaurants, dishes",
    "Gaming": "video games, played or built: gameplay, mods, consoles, PC builds for games",
    "Home & DIY": "making and fixing things by hand: woodworking, renovation, "
    "3D printing, home projects",
    "Sim racing": "car racing simulators only: rigs, wheels, pedals, racing "
    "games played on them; not flight simulators",
    "Technology": "computing and software: programming, AI, gadgets, self-hosting",
    "Travel": "places to go: trips, destinations, hotels, sights",
    "Movies & TV": "films, series and anime: recommendations, reviews, scenes, how they were made",
    "Nature & science": "the natural world and how things work: wildlife, the night sky, "
    "weather, science explainers; not gardening, and not a trip somewhere",
    "Family & wellbeing": "people and the inner life: parenting, relationships, "
    "mental health, mindfulness, encouragement",
    "Style & beauty": "how people look: fashion, outfits, hair, makeup, skincare, grooming",
}

SYSTEM = """\
You file Instagram reels into a personal library. You are given a reel's caption,
hashtags, transcript and the text shown on screen, any of which may be missing,
noisy or in another language. Reply with JSON only.

- summary: one plain English sentence, under 25 words, saying what the reel
  shows or teaches, whatever language the reel is in. No hashtags, no emoji,
  no "this reel".
- category: the one category whose definition the reel's main subject fits
  best. A reel about pizza is Food whether or not anyone said "food". If none
  fits, or the text is too vague to tell what the reel shows, answer "Other":
  a wrong category is worse than a missing one.
- confidence: high if the category is plainly the reel's subject, medium if
  it is the best of several, low if it is a guess.
- keywords: up to 5 short, specific, lowercase English topics, such as a dish,
  a car model, a place or a technique. Not the category names, and not generic
  words like "video", "tips", "life" or "restaurant"."""


# This one reel could not be tagged. Recorded, and retried next run.
TagError = ollama.ReplyError


@dataclass
class Result:
    summary: str | None
    category: str | None = None
    confidence: float | None = None
    keywords: list[str] = field(default_factory=list)
    # What the model said of its video game, for reels worth asking; the
    # server decides from it, with the reel's text, what to keep.
    game: games.Answer | None = None


# Takes the user prompt, returns the model's JSON reply as text.
Generate = Callable[[str], str]


def schema(categories: list[str] | None = None) -> dict[str, Any]:
    """The JSON schema Ollama constrains generation to."""
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "category": {"type": "string", "enum": (categories or names()) + [OTHER]},
            "confidence": {"type": "string", "enum": list(CONFIDENCE)},
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": MAX_KEYWORDS,
            },
        },
        "required": ["summary", "category", "confidence", "keywords"],
    }


# Below this many words, a caption alone is too thin to tag: "Find your @happyplace"
# came back as "a beautiful car" every time. Such reels need OCR or vision.
MIN_WORDS = 3
_WORD = re.compile(r"(?<![@\w/.])[^\W\d_]{2,}")


def _words(text: str) -> int:
    """Words a reader could learn from: not @mentions, URLs, emoji or numbers."""
    return len(_WORD.findall(re.sub(r"\S*[@/]\S*", " ", text)))


def build_prompt(
    caption: str | None, hashtags: list[str], transcript: str | None, ocr_text: str | None = None
) -> str | None:
    """The user message for one reel. None when there is too little to read."""
    has_more = bool(hashtags) or bool(transcript and transcript.strip())
    if not has_more and _words(caption or "") + _words(ocr_text or "") < MIN_WORDS:
        return None
    parts = []
    if caption and caption.strip():
        parts.append(f"Caption:\n{caption.strip()}")
    if hashtags:
        parts.append("Hashtags: " + " ".join(f"#{h}" for h in hashtags))
    if transcript and transcript.strip():
        parts.append(f"Transcript:\n{transcript.strip()}")
    if ocr_text and ocr_text.strip():
        # Machine-read and partly misread, so it is labelled as a hint rather
        # than presented as the post's own words.
        parts.append(f"Text on screen (OCR, may contain errors):\n{ocr_text.strip()}")
    if not parts:
        return None
    parts.append(
        "Categories:\n"
        + "\n".join(f"- {n}: {CATEGORY_NOTES[n]}" for n in names())
        + f"\n- {OTHER}: none of the above, or too vague to tell"
    )
    # Said in the system prompt too, but a 7B model answers a Japanese caption
    # in Japanese unless it is the last thing it reads.
    parts.append("Write the summary and keywords in English.")
    return "\n\n".join(parts)


def parse(reply: str) -> Result:
    """Validate the model's reply. Raises TagError on anything malformed.

    The schema already shapes the output; this is the second line, for a model
    or server that ignores `format`, and it is lenient about case.
    """
    try:
        data = json.loads(reply)
    except json.JSONDecodeError as exc:
        raise TagError(f"reply is not JSON: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise TagError("reply is not a JSON object")

    by_lower = {n.lower(): n for n in names()}
    cat = data.get("category")
    keys = data.get("keywords") or []
    if not isinstance(keys, list):
        raise TagError("keywords must be a list")
    if cat is not None and not isinstance(cat, str):
        raise TagError("category must be a string")

    # Other, or a name the taxonomy no longer has, is an abstention.
    category = by_lower.get((cat or "").strip().lower())
    level = data.get("confidence")
    confidence = None
    if category and isinstance(level, str):
        confidence = CONFIDENCE.get(level.strip().lower())

    keywords: list[str] = []
    for k in keys:
        if not isinstance(k, str):
            continue
        k = " ".join(k.lower().lstrip("#").split())
        if k and k not in keywords and k not in by_lower:
            keywords.append(k)

    summary = data.get("summary")
    summary = " ".join(summary.split()) if isinstance(summary, str) else ""
    return Result(
        summary=summary or None,
        category=category,
        confidence=confidence,
        keywords=keywords[:MAX_KEYWORDS],
    )


def tag(
    generate: Generate,
    caption: str | None,
    hashtags: list[str],
    transcript: str | None,
    ocr_text: str | None = None,
) -> Result:
    """Tag one reel. A reel with no text at all is answered without the model.

    When `generate` can also ask about games (a `games.Model`, as the Ollama
    client is) and the reel is worth asking, it is asked which game it shows."""
    prompt = build_prompt(caption, hashtags, transcript, ocr_text)
    if prompt is None:
        return Result(summary=None)
    result = parse(generate(prompt))
    asker: object = generate  # a Generate may be a games.Model too, or not
    if isinstance(asker, games.Model) and games.is_candidate(
        result.category, caption, hashtags, transcript, ocr_text
    ):
        result.game = games.ask(asker, caption, hashtags, transcript, ocr_text)
    return result


class Ollama(games.Ollama):
    """A `Generate` backed by one model on an Ollama server. It asks about a
    reel's game too (app/tagging/games.py), on the same model."""

    def __init__(self, url: str, model: str, *, timeout: float = 300):
        super().__init__(url, model, timeout=timeout)
        self._schema = schema()

    def __call__(self, prompt: str) -> str:
        return self.chat(
            SYSTEM, prompt, schema=self._schema, num_predict=NUM_PREDICT, num_ctx=NUM_CTX
        )
