"""Reading numbers out of recipe text: how much of an ingredient.

Quantities are stored as written ("1½", "2 tbsp", "a half cup", "1 cup milk"),
since that is what a reader trusts. The shopping list adds them up, so it
needs the number underneath. `amount` finds it at the start of the quantity,
with the unit after it (or in the unit column), and gives back the words it
read.

What does not parse is left alone. "A splash" stays "a splash", listed as it
is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_FRACTIONS = {
    "½": 0.5,
    "⅓": 1 / 3,
    "⅔": 2 / 3,
    "¼": 0.25,
    "¾": 0.75,
    "⅕": 0.2,
    "⅖": 0.4,
    "⅗": 0.6,
    "⅘": 0.8,
    "⅙": 1 / 6,
    "⅚": 5 / 6,
    "⅛": 0.125,
    "⅜": 0.375,
    "⅝": 0.625,
    "⅞": 0.875,
}

_WORDS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "fifteen": 15,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "forty-five": 45,
    "sixty": 60,
    "ninety": 90,
    "dozen": 12,
    "half": 0.5,
    "quarter": 0.25,
}

_UNI = "".join(_FRACTIONS)
_DIGITS = rf"(?:\d+(?:\.\d+)?\s*[{_UNI}]|\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?|[{_UNI}])"
_WORD = r"(?:" + "|".join(sorted((re.escape(w) for w in _WORDS), key=len, reverse=True)) + r")\b"
# "one and a half", "a half", "half a", "two", "1 1/2", "1½"
_NUMBER = (
    rf"(?:{_DIGITS}|{_WORD}(?:\s+and\s+a\s+(?:half|quarter))?(?:\s+(?:half|quarter)\b)?"
    rf"(?:\s+a\b|\s+an\b)?)"
)
_RANGE = rf"(?P<low>{_NUMBER})(?:\s*(?:-|–|—|~|〜|to|or)\s*(?P<high>{_NUMBER}))?"


def number(text: str) -> float | None:
    """ "1½" is 1.5, "1 1/2" too, "a half" 0.5, "one and a half" 1.5."""
    t = " ".join(text.lower().split())
    t = re.sub(r"\s+(?:a|an)$", "", t)  # "half a", "half an"
    if m := re.fullmatch(r"(\d+(?:\.\d+)?)?\s*([" + _UNI + r"])", t):
        return float(m[1] or 0) + _FRACTIONS[m[2]]
    if m := re.fullmatch(r"(?:(\d+)\s+)?(\d+)/(\d+)", t):
        return float(m[1] or 0) + int(m[2]) / int(m[3]) if int(m[3]) else None
    if re.fullmatch(r"\d+(?:\.\d+)?", t):
        return float(t)
    words = t.replace(" and ", " ").split()
    if words and all(w in _WORDS for w in words):
        if len(words) == 1:
            return float(_WORDS[words[0]])
        # "a half", "a quarter": a fraction of one.
        if words[0] in ("a", "an") and len(words) == 2:
            return float(_WORDS[words[1]])
        # "one and a half", "two and a quarter".
        if len(words) == 3 and words[1] in ("a", "an"):
            return _WORDS[words[0]] + _WORDS[words[2]]
    return None


# Each unit the quantities use, by every spelling found in the data and a few
# more. Case matters for one pair only: "T" is a tablespoon and "t" a teaspoon.
_UNITS: dict[str, tuple[str, ...]] = {
    "g": ("g", "gr", "gram", "grams", "gramme", "grammes"),
    "kg": ("kg", "kgs", "kilo", "kilos", "kilogram", "kilograms"),
    "ml": ("ml", "milliliter", "milliliters", "millilitre", "millilitres"),
    "l": ("l", "liter", "liters", "litre", "litres"),
    "tsp": ("tsp", "tsps", "teaspoon", "teaspoons", "t", "小さじ"),
    "tbsp": ("tbsp", "tbsps", "tbs", "tbl", "tablespoon", "tablespoons", "T", "大さじ"),
    "cup": ("cup", "cups", "c", "カップ"),
    "oz": ("oz", "ounce", "ounces"),
    "lb": ("lb", "lbs", "pound", "pounds"),
    "clove": ("clove", "cloves"),
    "can": ("can", "cans", "tin", "tins"),
    "pinch": ("pinch", "pinches"),
    "slice": ("slice", "slices"),
    "piece": ("piece", "pieces"),
    "bunch": ("bunch", "bunches"),
    "handful": ("handful", "handfuls"),
    "stick": ("stick", "sticks"),
    "sprig": ("sprig", "sprigs"),
    "cube": ("cube", "cubes"),
    "packet": ("packet", "packets", "pack", "packs", "package", "packages"),
    "head": ("head", "heads"),
    "stalk": ("stalk", "stalks"),
    "sheet": ("sheet", "sheets"),
    "large": ("large",),
    "medium": ("medium",),
    "small": ("small",),
}
_UNIT_OF = {
    spelling if spelling in ("T", "t") else spelling.lower(): unit
    for unit, spellings in _UNITS.items()
    for spelling in spellings
}

# Units that are one measure in another: summed in the first, then shown in
# whichever reads best.
_BASE = {"kg": ("g", 1000), "l": ("ml", 1000), "lb": ("oz", 16)}

_PLURAL = {
    "cup": "cups",
    "clove": "cloves",
    "can": "cans",
    "pinch": "pinches",
    "slice": "slices",
    "piece": "pieces",
    "bunch": "bunches",
    "handful": "handfuls",
    "stick": "sticks",
    "sprig": "sprigs",
    "cube": "cubes",
    "packet": "packets",
    "head": "heads",
    "stalk": "stalks",
    "sheet": "sheets",
}


def unit_named(word: str | None) -> str | None:
    """The unit a word names, or None: "Tbsp" and "tablespoons" are "tbsp"."""
    if not word:
        return None
    w = word.strip().rstrip(".")
    return _UNIT_OF.get(w) if w in ("T", "t") else _UNIT_OF.get(w.lower())


@dataclass(frozen=True)
class Amount:
    low: float
    high: float | None  # "1-2": 1 and 2; None when it is one number
    unit: str | None  # a key of _UNITS, or None for a count
    text: str  # the words read as the number: "1½", "a half"

    def times(self, k: float) -> Amount:
        return Amount(
            self.low * k, None if self.high is None else self.high * k, self.unit, self.text
        )


_JP_FIRST = re.compile(rf"^\s*(大さじ|小さじ|カップ)\s*(?P<n>{_DIGITS})")
_LEAD = re.compile(rf"^\s*{_RANGE}(?=$|[\s(a-zA-Z]|[^\x00-\x7f])", re.IGNORECASE)
_UNIT_AFTER = re.compile(r"^\s*([A-Za-z]+\.?|大さじ|小さじ|カップ)(?=$|[^A-Za-z])")


def amount(quantity: str | None, unit: str | None = None) -> Amount | None:
    """The number at the start of a quantity, and its unit. None when it has
    no number to read: "to taste", "a splash", "juice of 1"."""
    if not quantity:
        return None
    if m := _JP_FIRST.match(quantity):
        n = number(m["n"])
        return Amount(n, None, unit_named(m[1]), m["n"]) if n is not None else None
    m = _LEAD.match(quantity)
    if not m:
        return None
    low = number(m["low"])
    high = number(m["high"]) if m["high"] else None
    if low is None or (m["high"] and high is None):
        return None
    rest = quantity[m.end() :]
    found = None
    if after := _UNIT_AFTER.match(rest):
        found = unit_named(after[1])
    # "a" and "an" are numbers only before a unit: "a pinch", not "a splash".
    if m["low"].strip().lower() in ("a", "an") and not high and not found:
        return None
    found = found or unit_named(unit)
    return Amount(low, high, found, m[0].strip())


def base(a: Amount) -> Amount:
    """In the unit sums are made in: kilograms as grams, pounds as ounces."""
    if a.unit in _BASE:
        to, k = _BASE[a.unit]
        return Amount(a.low * k, None if a.high is None else a.high * k, to, a.text)
    return a


def dimension(unit: str | None) -> str | None:
    """What two amounts must share to be added up."""
    return _BASE[unit][0] if unit in _BASE else unit


def figure(x: float) -> str:
    """1.5 as "1½", 0.333 as "⅓", 2.25 as "2¼"; else at most two decimals."""
    whole = int(x)
    part = x - whole
    for glyph, value in _FRACTIONS.items():
        if glyph in "½⅓⅔¼¾⅛" and abs(part - value) < 0.02:
            return f"{whole or ''}{glyph}"
    if part > 0.98:
        return str(whole + 1)
    if part < 0.02:
        return str(whole)
    return f"{x:.2f}".rstrip("0").rstrip(".")


def written(a: Amount) -> str:
    """An amount to read: "1½ cups", "500 g", "1.2 kg", "2-3"."""
    a = base(a) if a.unit in _BASE else a
    low, high, unit = a.low, a.high, a.unit
    for small, (big, k) in (("g", ("kg", 1000)), ("ml", ("l", 1000)), ("oz", ("lb", 16))):
        if unit == small and low >= k:
            low, high, unit = low / k, None if high is None else high / k, big
    n = figure(low) + (f"-{figure(high)}" if high is not None else "")
    if unit is None:
        return n
    plural = (high or low) > 1 and unit in _PLURAL
    return f"{n} {_PLURAL[unit] if plural else unit}"
