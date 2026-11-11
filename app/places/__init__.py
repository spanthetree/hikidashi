"""Pulling places out of Instagram captions.

Calibrated against the real library rather than guessed at: of 420 captions,
59 use a 📍 pin, 22 contain a street address, and 2 use a `Where:` label. The
pin is by far the strongest signal and usually carries a venue name as well as
an address — "📍Mabel's Deli — 118 Water St, New York, NY 10005" —
which makes a much better Maps query than the street line alone.

Nothing here is clever enough to be certain, so the bar is deliberately high:
a candidate must look like a place, not merely contain a number.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus

MAPS = "https://www.google.com/maps/search/?api=1&query="

# Which build of the extractor read item_place out of a caption. Bumping it
# reads every caption again, and looks up whatever new places that finds.
PLACE_V = 1

MAX_PER_CAPTION = 12
MIN_LENGTH = 6
MAX_LENGTH = 90

# Pin characters, spelled out rather than put in a character class: 🗺️ carries a
# U+FE0F variation selector, and inside a class that selector matches on its own,
# which drags in every other emoji in the caption.
_PINS = "\U0001f4cd\U0001f3e0\U0001f3e1\U0001f5fa"

# "📍Venue — 123 Main St, City, ST 10001", up to the next pin or line break.
_PIN = re.compile(f"[{_PINS}]\\s*([^\\n{_PINS}]{{3,120}})")

# "Where: ...", "Location — ...", "Address: ..."
_LABEL = re.compile(
    r"(?:^|\n)\s*(?:where|location|address|find (?:it|them|us))\s*[:\-–—]\s*([^\n]{3,120})",
    re.IGNORECASE,
)

# A street address, extended through suite, city, state and ZIP when present.
_STREET = re.compile(
    r"""\b\d{1,6}\s
        (?:[A-Za-z0-9.'\-]+\s){0,4}
        (?:St|Street|Rd|Road|Ave|Avenue|Blvd|Boulevard|Dr|Drive|Ln|Lane|Way|
           Hwy|Highway|Pkwy|Parkway|Ct|Court|Pl|Place|Ter|Terrace|Cir|Circle|
           Sq|Square)\b\.?
        (?:,?\s*(?:Ste|Suite|Unit|Apt|\#)\.?\s*[\w\-]+)?
        (?:,\s*[A-Za-z][A-Za-z\ .'\-]{1,30})?
        (?:,\s*[A-Z]{2}\b)?
        (?:\s*\d{5}(?:-\d{4})?)?""",
    re.VERBOSE,
)

# Leading noise on a labelled line: "@handle // ", "- ", "— "
_LEAD = re.compile(r"^(?:@[\w.]+\s*(?://|[-–—])?\s*)+|^[\s\-–—:•·|]+")
_TRAIL = re.compile(r"[\s\-–—:•·|,]+$")
# Where a venue name is followed by a description, the name is the place.
_SPLIT = re.compile(r"\s+[–—]\s+|\s+-\s+|:\s+")
# Flags and decoration cling to the end of these lines.
_EMOJI_TAIL = re.compile(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D\u20E3]+\s*$")
_HASHTAG = re.compile(r"#\w+")
_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class Place:
    text: str

    @property
    def url(self) -> str:
        return MAPS + quote_plus(self.text)

    def as_dict(self) -> dict[str, Any]:
        return {"text": self.text, "url": self.url}


def _looks_like_a_place(text: str) -> bool:
    """A place, not a sentence that happened to follow a pin.

    One of: it contains a street address; or it is a short comma-separated
    name like "Monument Valley, Arizona"; or it is just a few capitalised
    words. Prose fails all three.
    """
    if _STREET.search(text):
        return True
    # "STOP 1", "DAY 2" — an itinerary marker, not somewhere you can go.
    if re.fullmatch(r"[A-Za-z]{2,10}\s*\d{1,3}", text):
        return False
    words = text.split()
    if "," in text and len(words) <= 10:
        return True
    return len(words) <= 6 and text[:1].isupper()


def _tidy(raw: str, *, strict: bool) -> str | None:
    text = _WS.sub(" ", raw).strip()
    text = _HASHTAG.sub("", text)  # hashtags are never part of an address
    text = _LEAD.sub("", text)

    # "Cedar Hollow Canyon — red rock walls, a long hike + ..." is a place with a
    # description attached. Keep the name. An address keeps its whole line,
    # because "Pearl & Sons — 140 Water St" is better than either half.
    if not _STREET.search(text):
        text = _SPLIT.split(text, 1)[0]

    text = _EMOJI_TAIL.sub("", text)
    text = _TRAIL.sub("", text)
    text = _WS.sub(" ", text).strip()

    if not (MIN_LENGTH <= len(text) <= MAX_LENGTH):
        return None
    if not re.search(r"[A-Za-z]{3}", text):
        return None
    # A line that is only a handle or only a URL is not a place.
    if re.fullmatch(r"@?[\w.]+", text) or text.lower().startswith(("http", "www.")):
        return None
    if strict and not _looks_like_a_place(text):
        return None
    return text


def extract(caption: str | None) -> list[Place]:
    """Return the places mentioned in a caption, best candidates first."""
    if not caption:
        return []

    # (raw text, strict) — pin and label lines are free-form and need the
    # quality gate; a street-address match has already proved itself.
    candidates: list[tuple[str, bool]] = []
    for rx in (_PIN, _LABEL):
        candidates.extend((m, True) for m in rx.findall(caption))
    candidates.extend((m.group(0), False) for m in _STREET.finditer(caption))

    cleaned: list[str] = []
    for raw, strict in candidates:
        text = _tidy(raw, strict=strict)
        if text:
            cleaned.append(text)

    # A pin line usually contains the street address the street rule also found.
    # Keep the richer one: drop any candidate wholly inside another.
    kept: list[str] = []
    for text in sorted(cleaned, key=len, reverse=True):
        lowered = text.lower()
        if any(lowered in k.lower() for k in kept):
            continue
        kept.append(text)

    # Restore the order they appear in the caption, so the list reads naturally.
    kept.sort(key=lambda t: caption.find(t) if t in caption else len(caption))
    return [Place(t) for t in kept[:MAX_PER_CAPTION]]


def as_dicts(caption: str | None) -> list[dict[str, Any]]:
    return [p.as_dict() for p in extract(caption)]
