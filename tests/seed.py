"""A small, deterministic library to test against: two people, a dozen reels.

Shared by the unit tests, the Playwright browser tests (frontend/e2e) and
manual demos, in place of setup written out again in each test file. It goes
through the real import, the hashtag pass and the recipe store, and writes the
columns the fetch, transcribe and tag stages would have, so nothing needs
yt-dlp, Whisper, Ollama or an embedding model. Search runs in its keyword-only
fallback.

    python3 -m tests.seed DIR      # DIR/hikidashi.sqlite3, DIR/media, DIR/exports

Alice is user 1, the admin; Bob was invited. They share one reel (SHARED) and
have their own besides, so a test can check that neither sees the other's.
`exports/upload.zip` holds reels nobody has yet, for importing through the UI.
"""

from __future__ import annotations

import json
import shutil
import struct
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app import accounts
from app.db import open_db
from app.importer.service import import_export
from app.recipes import service as recipes
from app.recipes.llm import Found, Ingredient, Recipe
from app.tagging.service import apply_hashtags
from tests.fixtures import post_record, saved_collections, saved_posts, write_zip

ASSETS = Path(__file__).resolve().parent / "assets"
R = "https://www.instagram.com/reel/"
SAVED = 1_760_000_000  # 2025-10-09; each reel is saved a day after the last

ALICE = ("alice", "alice-password")
BOB = ("bob", "bob-password")


@dataclass(frozen=True)
class Reel:
    code: str
    author: str
    caption: str
    hashtags: tuple[str, ...] = ()
    collections: tuple[str, ...] = ()
    transcript: str | None = None
    fetched: bool = True  # a thumbnail and a video, as the fetch would leave
    failed: str | None = None  # why the fetch failed, for a reel that did


# Captions are distinct enough that a search or a page can be checked for one
# person's reels and not the other's.
SHARED = Reel(
    "SHAREDreel01",
    "chef.marco",
    "Weeknight carbonara, no cream",
    ("pasta", "recipe"),
    ("Dinners",),
    transcript="Crack three eggs into a bowl with the pecorino.",
)
ALICE_REELS = (
    SHARED,
    Reel("ALICEramen01", "noodle.lab", "Ten minute miso ramen", ("ramen", "dinner"), ("Dinners",)),
    Reel(
        "ALICEtokyo01",
        "wander.jp",
        "A morning in Tokyo's back streets",
        ("tokyo", "travel"),
        ("Japan trip",),
        transcript="We start at Yanaka Ginza before the shops open.",
    ),
    Reel("ALICEkyoto01", "wander.jp", "Fushimi Inari at dawn", ("kyoto",), ("Japan trip",)),
    Reel("ALICEdrift01", "touge.daily", "AE86 on a mountain pass", ("ae86", "drift")),
    Reel(
        "ALICEsimrc01", "rig.builder", "Building a direct drive sim rig", ("simracer", "rigbuild")
    ),
    Reel("ALICEgym0001", "lift.well", "Three mobility drills before squats", ("mobility", "gym")),
    Reel("ALICEdiy0001", "make.room", "A floating shelf from one plank", ("diy", "woodworking")),
    Reel(
        "ALICEcode001", "dev.notes", "Docker volumes, explained in a minute", ("docker", "coding")
    ),
    Reel("ALICEnotag01", "someone", "No hashtags on this one at all"),
    Reel(
        "ALICEfail001",
        "gone.account",
        "This reel was taken down",
        ("travel",),
        fetched=False,
        failed="Video unavailable",
    ),
)
BOB_REELS = (
    SHARED,
    Reel("BOBbudget001", "money.mia", "Bob's zero-based budget template", ("budgeting", "finance")),
    Reel("BOBgaming01", "pixel.pete", "Bob's favourite speedrun route", ("gaming", "nintendo")),
)
# In exports/upload.zip, for importing through the upload button.
UPLOAD_REELS = (
    Reel(
        "UPLOADnew001",
        "fresh.find",
        "Crispy chickpeas in the air fryer",
        ("airfryer",),
        fetched=False,
    ),
    Reel("UPLOADnew002", "fresh.find", "Sourdough starter, day one", ("sourdough",), fetched=False),
)

CARBONARA = Found(
    recipe=Recipe(
        title="Carbonara",
        servings=2,
        time_minutes=20,
        ingredients=[
            Ingredient("spaghetti", "200", "g", as_written="200g spaghetti"),
            Ingredient("eggs", "3", as_written="three eggs"),
            Ingredient("pecorino", "50", "g", "finely grated", "pecorino"),
            Ingredient("guanciale", "100", "g", as_written="guanciale"),
        ],
        steps=[
            "Boil the pasta.",
            "Crisp the guanciale.",
            "Off the heat, toss with the eggs and cheese.",
        ],
    ),
    confidence=0.9,
    source_fields=["caption", "transcript"],
    dropped=[],
)


@dataclass
class Seed:
    root: Path
    db: Path
    media: Path
    upload: Path  # an export to import through the UI
    users: dict[str, int] = field(default_factory=dict)


def _png(path: Path, rgb: tuple[int, int, int], size: tuple[int, int] = (36, 64)) -> None:
    """A solid-colour PNG, written with the stdlib."""
    w, h = size
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _colour(code: str) -> tuple[int, int, int]:
    n = zlib.crc32(code.encode())
    return (n & 0xFF, (n >> 8) & 0xFF, (n >> 16) & 0xFF)


def export(path: Path, reels: tuple[Reel, ...]) -> Path:
    """An Instagram export ZIP of these reels, saved a day apart, newest first."""
    records = {
        r.code: post_record(
            R + r.code + "/",
            timestamp=SAVED + i * 86400,
            caption=r.caption,
            username=r.author,
            hashtags=r.hashtags,
        )
        for i, r in enumerate(reels)
    }
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in reels:
        for name in r.collections:
            groups.setdefault(name, []).append(records[r.code])
    path.parent.mkdir(parents=True, exist_ok=True)
    return write_zip(
        path, saved_posts(list(records.values())), saved_collections(groups) if groups else None
    )


def _fetched(conn, media: Path, reel: Reel) -> None:
    """What the fetch, transcribe and tag stages would have written."""
    if reel.failed:
        conn.execute(
            """UPDATE item SET state = 'failed', fail_stage = 'fetch',
                          fail_reason = ?, attempts = 1 WHERE shortcode = ?""",
            (reel.failed, reel.code),
        )
        return
    if not reel.fetched:
        return
    shard = reel.code[:2].lower()
    thumb, video = f"thumbs/{shard}/{reel.code}.png", f"media/{shard}/{reel.code}.mp4"
    _png(media / thumb, _colour(reel.code))
    (media / video).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ASSETS / "reel.mp4", media / video)
    conn.execute(
        """UPDATE item SET state = ?, thumb_path = ?, media_path = ?, bytes = ?,
             duration = 2, width = 90, height = 160, transcript = ?
           WHERE shortcode = ?""",
        (
            "transcribed" if reel.transcript else "fetched",
            thumb,
            video,
            (media / video).stat().st_size,
            reel.transcript,
            reel.code,
        ),
    )


def build(root: Path, *, people: bool = True) -> Seed:
    """The library, in `root`, which should be empty. people=False leaves a new
    library nobody has claimed yet, as a first run sees it."""
    root = Path(root)
    seed = Seed(
        root=root,
        db=root / "hikidashi.sqlite3",
        media=root / "media",
        upload=export(root / "exports" / "upload.zip", UPLOAD_REELS),
    )
    seed.media.mkdir(parents=True, exist_ok=True)
    conn = open_db(seed.db)
    try:
        if not people:
            return seed
        alice = accounts.claim(conn, *ALICE)
        bob = accounts.create(conn, *BOB)
        seed.users = {alice.username: alice.id, bob.username: bob.id}
        for user, reels in ((alice, ALICE_REELS), (bob, BOB_REELS)):
            import_export(
                conn, export(root / "exports" / f"{user.username}.zip", reels), user_id=user.id
            )
        apply_hashtags(conn)
        for reel in {r.code: r for r in ALICE_REELS + BOB_REELS}.values():
            _fetched(conn, seed.media, reel)
        recipes.register(conn)
        recipes.set_model(conn, SHARED.code, CARBONARA)
    finally:
        conn.close()
    return seed


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python3 -m tests.seed DIR", file=sys.stderr)
        return 2
    root = Path(argv[0])
    if root.exists() and any(root.iterdir()):
        print(f"{root} is not empty", file=sys.stderr)
        return 1
    seed = build(root)
    print(
        json.dumps(
            {
                "db": str(seed.db),
                "media": str(seed.media),
                "upload": str(seed.upload),
                "users": seed.users,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
