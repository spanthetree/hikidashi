"""Which reels are worth asking the model about. No model involved.

A reel is a candidate when its tag is Food, or its words look
like a recipe: a quantity with a unit ("2 tbsp", "200g") or the word for
ingredients or recipe. Either is enough. Some recipes have no Food tag, and a
Food reel with no signals may still be a recipe told in passing.

Numbered steps are reported but are not enough alone: in this library the
reels with numbered lists and no other sign were eleven travel, TV and "top
10" lists, and no recipes.

Being a candidate only earns a model check, so this errs wide: "method" in a
woodworking reel costs one short request answered `false`.
"""

from __future__ import annotations

import re

FOOD = "Food"

_AMOUNT = r"(?:\d+(?:[.,/]\d+)?\s?[½¼¾⅓⅔⅛]?|[½¼¾⅓⅔⅛])"
QUANTITY = re.compile(
    rf"(?<![\w.]){_AMOUNT}\s?"
    # Short units are lowercase only, so "5G" and "4K" are not grams and kelvin.
    r"(?:(?:g|kg|mg|ml|l|oz|lbs?)\b"
    r"|(?i:tbsps?|tsps?|tablespoons?|teaspoons?|cups?|grams?|ounces?|pounds?"
    r"|cloves?|pinch(?:es)?|cans?|tins?)\b)"
)
WORDS = re.compile(
    r"\b(?:ingredients?|recipes?|method|receta|ingredientes|recette|ricetta"
    r"|ingredienti|rezept|zutaten)\b",
    re.I,
)
STEPS = re.compile(r"(?m)^\s*(?:step\s*)?(?:[1-9]|1\d)[.)]\s+\S|\bstep\s*[1-9]\b", re.I)

# The recipe is somewhere else. Decided here and not by the model: asked, a 7B
# model flagged a complete cheesecake, and read "comment and make me go viral"
# as a pointer. These are the phrasings the library actually uses.
ELSEWHERE = re.compile(
    r"link(?:ed)? in (?:my |the |our )?(?:@\w+ )?(?:bio|profile)"
    r"|recipe (?:is )?(?:on|at|in) (?:my|the|our) (?:blog|website|site|bio|profile)"
    r"|full recipe (?:is )?(?:on|at|in|below|linked|here)"
    r"|(?:recipe|found here).{0,40}https?://|https?://\S*recipe"
    r"|comment\b.{0,30}\b(?:recipe|link)\b|\bdm\b.{0,20}\brecipe\b",
    re.I | re.S,
)
# 'Comment "BIRRIA" to have it sent': a keyword in capitals, then a request.
KEYWORD = re.compile(r"\bcomment\s+[\"“'‘]?[A-Z]{3,}[\"”'’]?\s+(?:to|and|for)\b")


def signals(*texts: str | None) -> list[str]:
    """The recipe signals in these texts, by name."""
    text = "\n".join(t for t in texts if t)
    found = []
    if QUANTITY.search(text):
        found.append("quantity")
    if WORDS.search(text):
        found.append("words")
    if STEPS.search(text):
        found.append("steps")
    return found


def is_candidate(
    tag: str | None, caption: str | None, transcript: str | None, ocr_text: str | None
) -> bool:
    if not any(t and t.strip() for t in (caption, transcript, ocr_text)):
        return False
    return tag == FOOD or bool(set(signals(caption, transcript, ocr_text)) - {"steps"})


def sends_elsewhere(*texts: str | None) -> bool:
    return any(ELSEWHERE.search(t) or KEYWORD.search(t) for t in texts if t)
