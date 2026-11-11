"""Which video game a gaming reel shows (#117).

Two short requests, after the tagger's, for the reels worth asking: those it
filed under Gaming or Sim racing, and those whose own text reads like video
games whatever it filed them under. The model answers whether the reel is
about one game, several or none, and names the one and any others the text
names. The second asks, with nothing but that name, for the game's official
title: a caption's "Grand Turismo 4" is Gran Turismo 4, and a title the
model cannot place is not one it may apply.

Whether that name is applied is decided here, not by the model. A 7B model
asked how sure it is answers "high" to nearly everything, so its word is one
half of the rule and the reel's own text is the other:

    sure     one game, high confidence, a title the model knows when asked
             again with nothing else to go on, no other game it knows named
             in the reel's text, and that title (or its main part, or its hashtag) in the caption,
             hashtags, on-screen text or transcript. Applied: the reel shows
             it and search finds it. A reel the tagger filed elsewhere is
             tagged Gaming.
    guess    one game, but not both of those. Kept as a suggestion for the
             person who saved the reel to confirm, correct or turn down.
    none     several games, none, or a low-confidence guess. Nothing kept.

The rule was set on a library's gaming reels, by hand: see docs/tags.md#games.

Stdlib only, like the rest of the tagger's core, so a remote worker runs the
request and the server, which has the text, makes the decision.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Any, Protocol, runtime_checkable

from app import ollama

# The categories whose reels are always asked about.
CATEGORIES = ("Gaming", "Sim racing")
# What a sure game on a reel filed under anything else is tagged instead.
GAMING = "Gaming"

ONE, SEVERAL, NONE = "one", "several", "none"
LEVELS = ("high", "medium", "low")
NUM_PREDICT = 120  # kind, title, confidence and a few other titles: ~60 tokens
MAX_OTHERS = 5
CHECK_PREDICT = 50
MAX_TITLE = 120

# Words that say a reel is about video games, whatever it was filed under.
# In a hashtag a cue may be part of the word, so #pcgaming counts.
# Deliberately narrow: "game" alone is football, board games and game night,
# and "mod" is half of the car reels. A cue only earns the question; the model
# still answers "none" for a gaming table.
_CUE = (
    r"(?:video ?games?|gaming|gamers?|gameplay|playthrough|let'?s play|speedrun\w*"
    r"|e-?sports?|nintendo|playstation|ps[345]|xbox|steam ?deck|gamedev|indie ?games?"
    r"|dlc|npcs?|minecraft|fortnite|roblox)"
)
CUES = re.compile(rf"\b{_CUE}\b", re.IGNORECASE)
_TAG_CUES = re.compile(_CUE, re.IGNORECASE)

SYSTEM = """\
You identify the video game an Instagram reel shows, from its caption, hashtags,
transcript and on-screen text, any of which may be missing, noisy or in another
language. Reply with JSON only.

- kind: "one" if the reel is mainly about one specific video game: playing
  it, a mod or port of it, news or a review of it. "several" if it covers more
  than one: a list, a compilation, an emulator or a website for many games.
  "none" if it is not about a video game: board games, tabletop role-playing,
  hardware or setups with no game in focus, real cars.
- title: when kind is "one", the game's official title as its publisher
  writes it, with its number or subtitle, such as "Cyberpunk 2077" or "The
  Legend of Zelda: Ocarina of Time". For a mod, remaster or fan port, the game
  it is built on. Otherwise "".
- others: the official titles of any other video games the text names, even
  in passing or in a hashtag. [] if none.
- confidence: high if the text names the game, medium if you recognise it
  from what the text describes, low if it is a guess."""


# This one reel's answer could not be read. Like a tagging failure, retried.
GameError = ollama.ReplyError


@dataclass(frozen=True)
class Answer:
    """What the model said, before the reel's text has been checked."""

    kind: str
    title: str | None
    confidence: str
    others: tuple[str, ...] = ()
    # What the check made of the title: its official form, or None when the
    # model, asked with nothing else, could not place it (or was not asked).
    official: str | None = None


@dataclass(frozen=True)
class Decision:
    title: str | None
    sure: bool

    @property
    def guess(self) -> str | None:
        return None if self.sure else self.title


NO_GAME = Decision(None, False)


def schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": [ONE, SEVERAL, NONE]},
            "title": {"type": "string"},
            "others": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_OTHERS},
            "confidence": {"type": "string", "enum": list(LEVELS)},
        },
        "required": ["kind", "title", "others", "confidence"],
    }


CHECK_SYSTEM = """\
You check video game titles. Given a title as someone wrote it, reply with JSON
only: known is true if it names a released or announced video game you know,
and title is that game's exact official title, with spelling, capitals,
punctuation and number corrected. If you do not know the game, or the title
could be several, known is false and title is ""."""


def check_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"known": {"type": "boolean"}, "title": {"type": "string"}},
        "required": ["known", "title"],
    }


def _texts(
    caption: str | None, hashtags: list[str], transcript: str | None, ocr_text: str | None
) -> list[str]:
    return [t for t in (caption, " ".join(hashtags), ocr_text, transcript) if t and t.strip()]


def is_candidate(
    category: str | None,
    caption: str | None,
    hashtags: list[str],
    transcript: str | None,
    ocr_text: str | None,
) -> bool:
    """Whether a reel is worth asking about: filed as a game, or reads like one."""
    if category in CATEGORIES:
        return True
    if any(_TAG_CUES.search(h) for h in hashtags):
        return True
    return any(CUES.search(t) for t in _texts(caption, [], transcript, ocr_text))


def build_prompt(
    caption: str | None, hashtags: list[str], transcript: str | None, ocr_text: str | None
) -> str:
    parts = []
    if caption and caption.strip():
        parts.append(f"Caption:\n{caption.strip()}")
    if hashtags:
        parts.append("Hashtags: " + " ".join(f"#{h}" for h in hashtags))
    if transcript and transcript.strip():
        parts.append(f"Transcript:\n{transcript.strip()}")
    if ocr_text and ocr_text.strip():
        parts.append(f"Text on screen (OCR, may contain errors):\n{ocr_text.strip()}")
    parts.append("Which video game is this reel about?")
    return "\n\n".join(parts)


def _json(reply: str) -> dict[str, Any]:
    try:
        data = json.loads(reply)
    except json.JSONDecodeError as exc:
        raise GameError(f"game reply is not JSON: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise GameError("game reply is not a JSON object")
    return data


def _title(v: Any) -> str | None:
    if not isinstance(v, str):
        return None
    return " ".join(v.split())[:MAX_TITLE] or None


def parse(reply: str) -> Answer:
    """Validate the model's reply. Raises GameError on anything malformed."""
    data = _json(reply)
    kind, title, level = data.get("kind"), data.get("title"), data.get("confidence")
    if not all(isinstance(v, str) for v in (kind, title, level)):
        raise GameError("game reply needs kind, title and confidence as text")
    assert isinstance(kind, str) and isinstance(level, str)
    kind, level = kind.strip().lower(), level.strip().lower()
    if kind not in (ONE, SEVERAL, NONE) or level not in LEVELS:
        raise GameError(f"game reply has kind {kind!r}, confidence {level!r}")
    raw = data.get("others") or []
    if not isinstance(raw, list):
        raise GameError("game reply's others must be a list")
    name = _title(title)
    others: list[str] = []
    for o in raw:
        t = _title(o)
        if t and t not in others and (not name or _fold(t) != _fold(name)):
            others.append(t)
    if kind != ONE or not name:
        return Answer(NONE if kind == ONE else kind, None, level)
    return Answer(ONE, name, level, tuple(others[:MAX_OTHERS]))


def parse_check(reply: str) -> str | None:
    """The official title the check gave, or None for a game it cannot place."""
    data = _json(reply)
    if data.get("known") is not True:
        return None
    return _title(data.get("title"))


@runtime_checkable
class Model(Protocol):
    """The two requests: what game a reel shows, and a title's official form.
    Each returns the model's JSON reply as text. The tagger's client has both,
    so whatever tags a reel can also ask about its game."""

    def game(self, prompt: str) -> str: ...

    def check(self, title: str) -> str: ...


def ask(
    model: Model,
    caption: str | None,
    hashtags: list[str],
    transcript: str | None,
    ocr_text: str | None,
) -> Answer:
    """Ask about one reel, and check the title it names. Writes nothing."""
    answer = parse(model.game(build_prompt(caption, hashtags, transcript, ocr_text)))
    if answer.title is None or answer.confidence == "low":
        return answer
    official = parse_check(model.check(answer.title))
    # The others the model lists are a 7B model's too: a car make, the same
    # game spelt another way, a game the text never mentions. Only one the
    # check can place, and that is not this game, is kept for decide().
    same = {_compact(t) for t in (answer.title, official) if t}
    others: list[str] = []
    for o in answer.others:
        if _compact(o) in same:
            continue
        checked = parse_check(model.check(o))
        if checked and _compact(checked) not in same and checked not in others:
            others.append(checked)
    return replace(answer, official=official, others=tuple(others))


# --- the decision -----------------------------------------------------------

_SPLIT = re.compile(r"\s*(?::|\s[-–—]\s)\s*")


def _fold(s: str) -> str:
    """Lowercase letters and digits, one space apart: "Gran Turismo® 4" is
    "gran turismo 4", and so is the same in a hashtag-less caption."""
    s = unicodedata.normalize("NFKD", s).replace("&", " and ")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(re.sub(r"[\W_]+", " ", s.lower()).split())


def _compact(s: str) -> str:
    return _fold(s).replace(" ", "")


def forms(title: str) -> list[str]:
    """The ways a reel names a game, folded: the whole title, and its main part
    before a subtitle. "The Legend of Zelda: Ocarina of Time" is named by
    either half; "Deadstick - Bush Flight Simulator" by "Deadstick"."""
    out: list[str] = []
    pieces = [title, *_SPLIT.split(title)]
    for piece in pieces:
        f = _fold(piece)
        for g in (f, f.removeprefix("the ")):
            if len(g.replace(" ", "")) >= 4 and g not in out:
                out.append(g)
    return out


def grounded(
    title: str,
    caption: str | None,
    hashtags: list[str],
    transcript: str | None,
    ocr_text: str | None,
) -> bool:
    """Whether the reel's own text names this game.

    A form of two or more words is enough anywhere in the text. One word must
    be a hashtag, or the start of one ("#minecraftbuilds"), or be written as
    a name, capitalised or in capitals: a game called Control is named by
    "Control" in a caption, not by "take control of your budget".
    """
    texts = _texts(caption, [], transcript, ocr_text)
    folded = " " + " ".join(_fold(t) for t in texts) + " "
    tags = [_fold(h).replace(" ", "") for h in hashtags]
    for f in forms(title):
        compact = f.replace(" ", "")
        if any(t == compact or (len(compact) >= 6 and t.startswith(compact)) for t in tags):
            return True
        if " " in f:
            if f" {f} " in folded:
                return True
            continue
        for t in texts:
            for m in re.finditer(rf"(?<!\w){re.escape(f)}(?!\w)", t, re.IGNORECASE):
                word = m.group(0)
                if word[0].isupper() or word.isdigit():
                    return True
    return False


def decide(
    answer: Answer | None,
    caption: str | None,
    hashtags: list[str],
    transcript: str | None,
    ocr_text: str | None,
) -> Decision:
    """Sure, a guess to confirm, or no game. See the module docstring.

    The title kept is the official one when the check gave it. The reel may
    name the game either way: its own misspelling is still its own words, and
    the check is what says which game they mean."""
    if answer is None or answer.kind != ONE or not answer.title or answer.confidence == "low":
        return NO_GAME
    title = answer.official or answer.title
    sure = (
        answer.confidence == "high"
        and answer.official is not None
        and not any(grounded(o, caption, hashtags, transcript, ocr_text) for o in answer.others)
        and any(
            grounded(t, caption, hashtags, transcript, ocr_text)
            for t in dict.fromkeys((answer.official, answer.title))
        )
    )
    return Decision(title, sure)


class Ollama(ollama.Client):
    """The `Model` on an Ollama server. The tagger's client is one."""

    def game(self, prompt: str) -> str:
        return self.chat(SYSTEM, prompt, schema=_SCHEMA, num_predict=NUM_PREDICT)

    def check(self, title: str) -> str:
        return self.chat(
            CHECK_SYSTEM, f"Title: {title}", schema=_CHECK_SCHEMA, num_predict=CHECK_PREDICT
        )


_SCHEMA, _CHECK_SCHEMA = schema(), check_schema()
