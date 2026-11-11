"""Ingredient groups, by hand: what "without dairy" or "has chicken" means.

A filter on the ingredient text alone cannot answer "without dairy": no recipe
lists "dairy", and "butter" is dairy while "peanut butter" is not. So each group
is a list of words that put an ingredient in it, and a list of phrases that
keep it out, checked first. They are per group: "peanut butter" is kept out of
Dairy and still counts under Nuts, and "vegan butter" is kept out of Dairy
whole, not just the word "vegan".

Words match whole, with an optional plural: "egg" finds "eggs" and not
"eggplant". Matching happens at query time (the API registers `groups_csv` as
a SQLite function), so editing these lists needs no re-run of anything.

This is a browsing aid, not allergen advice. It knows the common ingredients by
name, not what is in a brand of stock cube.
"""

from __future__ import annotations

import functools
import re
import unicodedata

# name -> (words that put an ingredient in the group, phrases that take it out)
GROUPS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "Dairy": (
        (
            "milk",
            "buttermilk",
            "butter",
            "cream",
            "cheese",
            "yogurt",
            "yoghurt",
            "ghee",
            "parmesan",
            "parmigiano",
            "pecorino",
            "mozzarella",
            "burrata",
            "feta",
            "ricotta",
            "mascarpone",
            "paneer",
            "halloumi",
            "cheddar",
            "gruyere",
            "brie",
            "camembert",
            "gouda",
            "creme fraiche",
            "kefir",
            "labneh",
            "custard",
            "whey",
            "quark",
            "queso",
            "skyr",
        ),
        (
            "coconut milk",
            "coconut cream",
            "coconut yogurt",
            "coconut yoghurt",
            "oat milk",
            "almond milk",
            "soy milk",
            "soya milk",
            "rice milk",
            "cashew milk",
            "plant milk",
            "plant based",
            "vegan",
            "dairy free",
            "peanut butter",
            "almond butter",
            "cashew butter",
            "nut butter",
            "cocoa butter",
            "apple butter",
            "butter bean",
            "cream of tartar",
        ),
    ),
    "Eggs": (
        ("egg", "yolk", "mayonnaise", "mayo", "meringue", "aioli"),
        ("egg free", "vegan mayo", "vegan mayonnaise", "eggless"),
    ),
    "Gluten": (
        (
            "flour",
            "bread",
            "breadcrumb",
            "panko",
            "pasta",
            "spaghetti",
            "linguine",
            "penne",
            "fettuccine",
            "tagliatelle",
            "rigatoni",
            "lasagna",
            "lasagne",
            "macaroni",
            "orzo",
            "couscous",
            "noodle",
            "udon",
            "ramen",
            "wheat",
            "barley",
            "rye",
            "semolina",
            "bulgur",
            "farro",
            "seitan",
            "tortilla",
            "pita",
            "pitta",
            "naan",
            "baguette",
            "brioche",
            "croissant",
            "dough",
            "pastry",
            "gnocchi",
            "soy sauce",
            "beer",
            "malt",
            "cracker",
            "biscuit",
        ),
        (
            "rice flour",
            "almond flour",
            "coconut flour",
            "corn flour",
            "cornflour",
            "chickpea flour",
            "gram flour",
            "buckwheat flour",
            "tapioca flour",
            "potato flour",
            "gluten free",
            "rice noodle",
            "glass noodle",
            "rice paper",
            "corn tortilla",
            "tamari",
            "buckwheat noodle",
        ),
    ),
    "Meat": (
        (
            "beef",
            "pork",
            "lamb",
            "mutton",
            "veal",
            "bacon",
            "ham",
            "sausage",
            "chorizo",
            "salami",
            "pepperoni",
            "prosciutto",
            "pancetta",
            "guanciale",
            "mince",
            "steak",
            "brisket",
            "short rib",
            "oxtail",
            "venison",
            "goat",
            "meatball",
            "burger",
            "lard",
            "mortadella",
            "kielbasa",
            "hot dog",
            "nduja",
            "speck",
            "jamon",
        ),
        (
            "plant based",
            "vegan",
            "vegetarian",
            "veggie",
            "meat free",
            "goat cheese",
            "goats cheese",
            "goat s cheese",
            "lamb s lettuce",
            "beyond meat",
            "impossible",
        ),
    ),
    "Poultry": (
        ("chicken", "turkey", "duck", "goose", "quail"),
        ("vegan chicken", "chicken free", "plant based"),
    ),
    "Fish & seafood": (
        (
            "fish",
            "salmon",
            "tuna",
            "cod",
            "haddock",
            "trout",
            "mackerel",
            "sardine",
            "anchovy",
            "anchovies",
            "halibut",
            "sea bass",
            "tilapia",
            "snapper",
            "prawn",
            "shrimp",
            "crab",
            "lobster",
            "mussel",
            "clam",
            "oyster",
            "scallop",
            "squid",
            "calamari",
            "octopus",
            "bonito",
            "katsuobushi",
            "dashi",
            "roe",
            "caviar",
            "eel",
            "worcestershire",
            "seafood",
        ),
        (
            "vegan fish sauce",
            "fish free",
            "oyster mushroom",
            "mushroom oyster sauce",
            "vegetarian oyster sauce",
            "kombu dashi",
        ),
    ),
    "Nuts": (
        (
            "nut",
            "almond",
            "walnut",
            "pecan",
            "cashew",
            "pistachio",
            "hazelnut",
            "macadamia",
            "brazil nut",
            "pine nut",
            "peanut",
            "praline",
            "marzipan",
            "nutella",
            "frangipane",
        ),
        ("nut free", "water chestnut"),
    ),
}

NAMES = tuple(GROUPS)


def normalise(text: str) -> str:
    """Lowercase, accents and punctuation gone, single spaces: "Crème-fraîche"
    becomes "creme fraiche"."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^\w]+", " ", text).split())


def _words(terms: tuple[str, ...]) -> re.Pattern[str]:
    alts = "|".join(sorted((re.escape(t) for t in terms), key=len, reverse=True))
    return re.compile(rf"\b(?:{alts})(?:e?s)?\b")


_PATTERNS = {name: (_words(words), _words(out)) for name, (words, out) in GROUPS.items()}


@functools.lru_cache(maxsize=4096)
def groups_of(item: str) -> frozenset[str]:
    """Every group this ingredient belongs to."""
    text = normalise(item)
    found = set()
    for name, (words, out) in _PATTERNS.items():
        if not out.search(text) and words.search(text):
            found.add(name)
    return frozenset(found)


def groups_csv(item: str | None) -> str:
    """For SQL: ",Dairy,Eggs," so `instr(x, ',Dairy,')` tests membership."""
    return "," + ",".join(sorted(groups_of(item or ""))) + ","


def group_named(value: str) -> str | None:
    """The group a filter value names, ignoring case, or None."""
    return next((n for n in NAMES if n.lower() == value.strip().lower()), None)
