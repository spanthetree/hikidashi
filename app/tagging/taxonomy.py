"""The tag taxonomy, how hashtags map onto it, and which hashtags are worth keeping.

Tags are ours; collections are Instagram's. They are kept in separate tables for
that reason — a collection is something you made in the app, a tag is something
this system worked out.

Each item gets one tag, its primary category, from this short fixed list. The
library's 420 saves carry 2,239 distinct hashtags, 80% of which appear exactly
once, so hashtags are never promoted to tags: they are cleaned (`clean`) and
kept beside the tag, where they stay searchable and filterable without growing
the list. docs/tags.md records why each category is here.

`stems` match as substrings and must be distinctive enough to survive that:
"car" would match "cardio", so it is not a stem — "carspotting" is.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# name -> (exact hashtags, distinctive substrings)
TAXONOMY: dict[str, tuple[frozenset[str], tuple[str, ...]]] = {
    "Food": (
        frozenset(
            {
                "food",
                "foodie",
                "recipe",
                "recipes",
                "cooking",
                "cook",
                "dinner",
                "lunch",
                "breakfast",
                "brunch",
                "dessert",
                "baking",
                "mealprep",
                "yummy",
                "delicious",
                "tasty",
                "homemade",
                "chef",
                "eats",
            }
        ),
        (
            "recipe",
            "foodie",
            "foodblog",
            "instafood",
            "foodstagram",
            "easyrecipe",
            "easydinner",
            "comfortfood",
            "eeeeeats",
            "homecook",
            "mealprep",
            "airfryer",
            "sourdough",
            "pasta",
            "ramen",
            "bbq",
            "grill",
        ),
    ),
    "Travel": (
        frozenset(
            {
                "travel",
                "traveling",
                "travelling",
                "roadtrip",
                "vacation",
                "holiday",
                "wanderlust",
                "adventure",
                "hiking",
                "camping",
                "japan",
                "tokyo",
                "kyoto",
                "osaka",
                "italy",
                "europe",
                "iceland",
                "hawaii",
                "arizona",
                "austin",
                "nyc",
                "london",
                "paris",
            }
        ),
        (
            "travel",
            "roadtrip",
            "destination",
            "wanderlust",
            "nationalpark",
            "backpack",
            "itinerary",
            "staycation",
            "jetlag",
            "tourism",
        ),
    ),
    "Automotive": (
        frozenset(
            {
                "car",
                "cars",
                "jdm",
                "toyota",
                "honda",
                "nissan",
                "mazda",
                "bmw",
                "audi",
                "porsche",
                "drift",
                "drifting",
                "motorsport",
                "racing",
                "turbo",
                "engine",
                "garage",
                "ae86",
                "trueno",
                "levin",
                "hachiroku",
                "sc300",
                "soarer",
                "supra",
                "miata",
                "wrx",
                "sti",
                "gtr",
            }
        ),
        (
            "jdm",
            "carspotting",
            "carsofinstagram",
            "drift",
            "motorsport",
            "stance",
            "4age",
            "2jz",
            "1jz",
            "rotary",
            "trackday",
            "autocross",
            "carbuild",
            "enginesound",
            "exhaust",
        ),
    ),
    "Sim racing": (
        frozenset(
            {
                "simracing",
                "simrig",
                "simracer",
                "iracing",
                "assettocorsa",
                "granturismo",
                "forza",
                "rigbuild",
            }
        ),
        ("simracing", "simrig", "iracing", "assettocorsa", "racingwheel", "直drive", "directdrive"),
    ),
    "Gaming": (
        frozenset(
            {
                "gaming",
                "gamer",
                "games",
                "videogames",
                "pcgaming",
                "playstation",
                "xbox",
                "nintendo",
                "steam",
            }
        ),
        ("gaming", "gamer", "videogame", "pcbuild", "esports"),
    ),
    "Technology": (
        frozenset(
            {
                "tech",
                "ai",
                "coding",
                "programming",
                "developer",
                "software",
                "cybersecurity",
                "linux",
                "homelab",
                "selfhosted",
                "devops",
                "python",
                "javascript",
                "docker",
                "kubernetes",
            }
        ),
        (
            "techtip",
            "cybersec",
            "infosec",
            "homelab",
            "selfhost",
            "coding",
            "programming",
            "devops",
            "machinelearning",
            "chatgpt",
            "automation",
        ),
    ),
    "Home & DIY": (
        frozenset(
            {
                "diy",
                "woodworking",
                "homeimprovement",
                "renovation",
                "interiordesign",
                "homedecor",
                "furniture",
                "workshop",
                "tools",
                "handmade",
            }
        ),
        (
            "woodwork",
            "homeimprove",
            "renovat",
            "interiordesign",
            "homedecor",
            "diyproject",
            "3dprint",
        ),
    ),
    "Fitness": (
        frozenset(
            {
                "fitness",
                "gym",
                "workout",
                "training",
                "running",
                "cycling",
                "yoga",
                "strength",
                "calisthenics",
                "mobility",
            }
        ),
        ("workout", "fitnessmotivation", "gymlife", "homegym", "powerlifting", "marathon", "hyrox"),
    ),
    "Design": (
        frozenset(
            {
                "design",
                "architecture",
                "graphicdesign",
                "typography",
                "branding",
                "ux",
                "ui",
                "art",
                "illustration",
                "photography",
            }
        ),
        ("architect", "graphicdesign", "typograph", "branding", "productdesign", "photograph"),
    ),
    "Finance": (
        frozenset(
            {
                "finance",
                "investing",
                "money",
                "stocks",
                "crypto",
                "realestate",
                "budgeting",
                "personalfinance",
                "fire",
            }
        ),
        (
            "investing",
            "personalfinance",
            "sidehustle",
            "passiveincome",
            "realestate",
            "stockmarket",
        ),
    ),
    # The four below came from hand labels (#6, #145): reels that fitted none
    # of the ten. Plain #nature is left out, since travel reels use it as much
    # as wildlife ones; #film matches only whole, so #filmphotography stays
    # Design.
    "Movies & TV": (
        frozenset(
            {
                "movie",
                "movies",
                "film",
                "films",
                "cinema",
                "tv",
                "tvshow",
                "tvshows",
                "tvseries",
                "series",
                "netflix",
                "anime",
                "manga",
                "cinephile",
            }
        ),
        ("movie", "cinema", "cinephile", "tvshow", "tvseries", "netflix", "anime", "filmmak"),
    ),
    "Nature & science": (
        frozenset(
            {
                "wildlife",
                "animals",
                "science",
                "astronomy",
                "astrophotography",
                "space",
                "physics",
                "biology",
                "chemistry",
                "stargazing",
                "eclipse",
                "nasa",
            }
        ),
        (
            "wildlife",
            "naturephoto",
            "astronom",
            "astrophoto",
            "stargaz",
            "science",
            "physics",
            "biology",
            "marinelife",
            "birdwatch",
        ),
    ),
    "Family & wellbeing": (
        frozenset(
            {
                "family",
                "parenting",
                "motherhood",
                "fatherhood",
                "momlife",
                "dadlife",
                "mentalhealth",
                "selfcare",
                "mindfulness",
                "wellbeing",
                "relationships",
                "selflove",
                "motivation",
            }
        ),
        ("parenting", "motherhood", "fatherhood", "mentalhealth", "selfcare", "mindful", "wellbeing"),
    ),
    "Style & beauty": (
        frozenset(
            {
                "fashion",
                "style",
                "ootd",
                "outfit",
                "makeup",
                "beauty",
                "skincare",
                "hair",
                "hairstyle",
                "haircut",
                "grooming",
                "menswear",
                "streetwear",
            }
        ),
        ("fashion", "outfit", "makeup", "skincare", "hairstyl", "haircut", "menswear", "streetwear", "beauty"),
    ),
}

# Hashtags that carry no topic at all: reach-farming, not description. Dropped
# from the cleaned hashtags, so they are neither shown nor searched.
STOPTAGS = frozenset(
    {
        "viral",
        "reels",
        "reel",
        "fyp",
        "foryou",
        "foryoupage",
        "explore",
        "explorepage",
        "trending",
        "instagram",
        "instagood",
        "like",
        "follow",
        "followme",
        "love",
        "photooftheday",
        "video",
        "videos",
        "shorts",
        "tiktok",
        "pubity",
        "repost",
        "share",
        "comment",
        "tag",
        "tags",
        "new",
        "best",
        "top",
        "1",
        "2",
        "3",
        "amazing",
        "beautiful",
        "cute",
        "funny",
        "lol",
        "wow",
        "omg",
    }
)

_CLEAN = re.compile(r"[^a-z0-9]+")


def normalise(hashtag: str) -> str:
    """Lowercase and strip punctuation, so '#Easy_Recipe!' becomes 'easyrecipe'."""
    return _CLEAN.sub("", hashtag.lower())


def _matches(name: str, tag: str) -> bool:
    exact, stems = TAXONOMY[name]
    return tag in exact or any(stem in tag for stem in stems)


def classify(hashtags: Iterable[str]) -> set[str]:
    """Return every taxonomy tag these hashtags imply."""
    cleaned = {normalise(h) for h in hashtags}
    cleaned.discard("")
    return {name for name in TAXONOMY if any(_matches(name, t) for t in cleaned)}


# The narrower category wins a tie: a rig hashtagged #simracing #gaming is
# about sim racing, which is the reason that category exists.
NARROWER = ("Sim racing",)


def primary(hashtags: Iterable[str]) -> str | None:
    """The one category these hashtags point at most, or None.

    Each category scores one per distinct hashtag it matches, so a food reel
    with one stray #travel among #recipe #dinner #pasta is Food.
    """
    cleaned = {normalise(h) for h in hashtags}
    cleaned.discard("")
    scores = {name: sum(_matches(name, t) for t in cleaned) for name in TAXONOMY}
    best = sorted(
        (n for n in scores if scores[n]), key=lambda n: (-scores[n], n not in NARROWER, n)
    )
    return best[0] if best else None


def is_noise(hashtag: str) -> bool:
    """True for engagement-bait and tags too short to mean anything."""
    clean = normalise(hashtag)
    return (not clean) or clean in STOPTAGS or len(clean) < 3 or clean.isdigit()


def clean(hashtags: Iterable[str]) -> list[str]:
    """The hashtags worth showing and searching: normalised, noise dropped,
    each once, in the post's order. '#Birria', '#birria' and '#fyp' become
    ['birria']."""
    out: dict[str, None] = {}
    for h in hashtags:
        if not is_noise(h):
            out.setdefault(normalise(h))
    return list(out)


def names() -> list[str]:
    return sorted(TAXONOMY)
