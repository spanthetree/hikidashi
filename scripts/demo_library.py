#!/usr/bin/env python3
"""Build the demo library the README recording and the docs are made from.

    uv run python scripts/demo_library.py sources   # download and cut the media
    uv run python scripts/demo_library.py build     # a fresh library from them
    uv run python scripts/demo_library.py serve     # the server, on 127.0.0.1:8090
    uv run python scripts/demo_library.py worker    # the worker beside it
    uv run python scripts/demo_library.py remote    # a remote worker for it
    uv run python scripts/demo_library.py fixture   # tests/eval/, for CI's evals

`worker` runs enrich and embed next to the server, and `remote` runs the rest
over HTTP, as a GPU desktop would for a NAS. `cast` is `remote --once`
recorded, line by line with its timing, to docs/demo/remote-worker.cast (an
asciicast, so `asciinema play` replays it); make_diagrams.py draws it as the
terminal in docs/remote-workers.md. `credits` rewrites docs/demo/CREDITS.md.

`fixture` copies the built library into tests/eval/ for scripts/evals.py
(#57): the database without its sign-ins, and a vector for every query in
tests/search_eval.json. CI then scores search and tagging on it with no
model. Make it again after a build, or when the query set changes.

Every picture and clip is from Wikimedia Commons under CC0, public domain or
CC BY, never share-alike: the recordings are made from them, and share-alike
would bind the recordings too. Each caption ends with its credit.

The library goes through the real import, and the real workers transcribe,
read, tag, find recipes and embed it. Only the fetch is skipped: these are
not on Instagram, so the build writes what the fetch would have. Places are
looked up on a stub geocoder in this script that knows the places the
captions name, so the map fills in without asking a public server.

It lives in DEMO_ROOT (default ~/.cache/hikidashi-demo), never in data/, and
listens on 127.0.0.1 only, so it cannot meet a running Hikidashi. Models are
read from data/models, so nothing is downloaded twice.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEMO = Path(os.environ.get("DEMO_ROOT") or Path.home() / ".cache" / "hikidashi-demo")
SOURCES = DEMO / "sources"  # downloads and cuts, kept between builds
LIB = DEMO / "library"  # the library, made again by each build
CREDITS = ROOT / "docs" / "demo" / "CREDITS.md"
CAST = ROOT / "docs" / "demo" / "remote-worker.cast"
FIXTURE = ROOT / "tests" / "eval"

PORT = 8090
GEOCODER_PORT = 8091
OWNER = "spanthetree"
API = "https://commons.wikimedia.org/w/api.php"
UA = "Hikidashi demo builder (https://github.com/spanthetree/hikidashi)"
FREE = re.compile(r"CC0|Public domain|CC BY \d\.\d")
NEWEST = 1_777_000_000  # the newest save; each one below is a day older
SHORT = 720  # a clip's short side
SLIDE_W, SLIDE_H = 1080, 1350  # photos, 4:5
TRANSCODES = ("720p.vp9.webm", "720p.webm", "480p.vp9.webm", "480p.webm")


@dataclass(frozen=True)
class Demo:
    code: str
    files: tuple[str, ...]  # Commons titles; several photos make a carousel
    caption: str
    collections: tuple[str, ...] = ()
    start: float = 0  # seconds into a clip
    length: float = 40  # the most of it that plays
    thumb: float = 0  # where to look for the thumbnail, seconds into the cut
    place: tuple[float, float, str] | None = None  # the 📍 line's lat, lng and kind

    @property
    def video(self) -> bool:
        return self.files[0].lower().endswith((".webm", ".ogv"))


def _d(code: str, files: str | tuple[str, ...], caption: str, *more: str, **kw: Any) -> Demo:
    return Demo(code, (files,) if isinstance(files, str) else files, caption, more, **kw)


# Newest save first. Captions are written as their creators might have: the
# hashtags become tags, a 📍 line a place on the map, and ingredients and
# steps, whether said, written or shown, a recipe.
REELS: tuple[Demo, ...] = (
    _d(
        "DQpRoastAp1",
        "Test Kitchen- Pork Roast with Fresh Applesauce -shorts.webm",
        "Pork roast with fresh applesauce 🍎 The sauce cooks while the roast rests.\n\n"
        "#sundayroast #pork #applesauce #recipe",
        "Dinners",
    ),
    _d(
        "DQfushimi01",
        (
            "20181110 Fushimi Inari shrine 2.jpg",
            "20181110 Fushimi Inari shrine 3.jpg",
            "20181110 Fushimi Inari shrine 4.jpg",
        ),
        "Fushimi Inari before the crowds ⛩️ Thousands of torii and nobody else on the path "
        "at 7am. Go early, then keep climbing past the first viewpoint.\n"
        "📍Fushimi Inari Taisha, Kyoto\n\n#kyoto #japan #travel #伏見稲荷",
        "Japan trip",
        place=(34.9671, 135.7727, "shrine"),
    ),
    _d(
        "DQcrabdip01",
        "Baked Crab Dip with Crostini, by America's Test Kitchen.webm",
        "Baked crab dip with crostini 🦀 Party food that disappears first.\n\n"
        "#crabdip #appetizer #partyfood #recipe",
        "Party food",
    ),
    _d(
        "DQshioramen",
        ("Shio Ramen 001.jpg", "Ramen and Chahan 003.jpg"),
        "塩ラーメンと半チャーハン 🍜 Shio ramen and half a plate of fried rice, the lunch set "
        "for 950 yen.\n📍Shinjuku, Tokyo\n\n#ramen #tokyo #塩ラーメン #japanesefood",
        "Japan trip",
        place=(35.6938, 139.7034, "suburb"),
    ),
    _d(
        "DQlathefin1",
        "Lathe in Finland.webm",
        "Roughing a blank on the lathe 🪵 Sharp gouge, light cuts, let the tool do it.\n\n"
        "#woodturning #woodworking #lathe",
        "Workshop",
    ),
    _d(
        "DQkittens01",
        "Three orange kittens following their mother.webm",
        "Three orange kittens following mum around the yard 🧡\n\n#kittens #cats #catsofinstagram",
    ),
    _d(
        "DQmilanese1",
        "Chicken Milanese Parmesan 2019.webm",
        "Chicken Milanese with parmesan in the crumb. Pound it thin, fry it fast.\n\n"
        "#chicken #milanese #dinner #recipe",
        "Dinners",
        length=45,
    ),
    _d(
        "DQsourdough",
        (
            "Sourdough in proofing phase.jpg",
            "Loaf of sourdough bread cooling.jpg",
            "Sliced loaf of sourdough bread.jpg",
        ),
        "Sourdough, proof to slice 🍞\n\n500 g bread flour\n350 g water\n100 g active starter\n"
        "10 g salt\n\nMix, rest an hour, then fold every 30 minutes for 2 hours. Bulk until "
        "doubled, shape, and cold proof overnight. Bake at 250°C in a lidded pot for 20 "
        "minutes, then 25 more with the lid off.\n\n#sourdough #bread #baking #recipe",
        "Weekend bakes",
    ),
    _d(
        "DQtofino001",
        "Pebble of the morning, on Long Beach in late summer, near Tofino.webm",
        "Morning on Long Beach. Pebbles, fog and nobody around 🌊\n📍Long Beach, Tofino\n\n"
        "#tofino #beach #britishcolumbia #travel",
        "Trips",
        length=30,
        place=(49.0667, -125.7330, "beach"),
    ),
    _d(
        "DQgreenbean",
        "Mediterranean Braised Green Beans with Mint and Feta, by America's Test Kitchen.webm",
        "Braised green beans with mint and feta. A side that steals the show 🌿\n\n"
        "#greenbeans #sidedish #vegetarian #recipe",
        "Dinners",
    ),
    _d(
        "DQcafemenu1",
        "Cafe menu (Unsplash).jpg",
        "The board at our new favourite coffee spot ☕ Saving it for next time.\n\n"
        "#coffee #cafe #menu",
    ),
    _d(
        "DQmykonos01",
        "MYKONOS , a walking tour of this Greek island (in 4K).webm",
        "Little Venice to the windmills, Mykonos 🇬🇷 Get lost in the lanes, it's the point.\n"
        "📍Mykonos, Greece\n\n#mykonos #greece #travel #walkingtour",
        "Trips",
        start=300,
        length=30,
        place=(37.4467, 25.3289, "island"),
    ),
    _d(
        "DQonigiri01",
        "Just Inc. Recipe Video Sample - Onigiri Rice Balls (2016).webm",
        "Onigiri for the train 🍙 Salted hands, warm rice, a filling in the middle.\n\n"
        "#onigiri #japanesefood #bento #recipe",
        "Lunches",
    ),
    _d(
        "DQtranspwd1",
        "DIY Transparent Wood - Overview, Recipe, and Examples.webm",
        "Transparent wood: strip the lignin, fill it with epoxy, and you can read through "
        "a plank 🤯\n\n#diy #woodworking #science #materials",
        "Workshop",
        start=5,
        length=60,
    ),
    _d(
        "DQcalamari1",
        "Cooking Calimari at the Gilroy Garlic Festival.webm",
        "Garlic calamari at the Gilroy Garlic Festival 🦑🧄\n📍Gilroy, California\n\n"
        "#gilroy #garlic #streetfood #festival",
        "Trips",
        length=40,
        place=(37.0058, -121.5683, "town"),
    ),
    _d(
        "DQredvelvt1",
        "Original Red Velvet Recipe Card.jpg",
        "Found the original red velvet cake recipe card in a box of old cookbooks 🎂\n\n"
        "#redvelvet #cake #baking #vintage",
        "Weekend bakes",
    ),
    _d(
        "DQporkbelly",
        "Test Kitchen- Pork Belly Sandwich.webm",
        "Crispy pork belly sandwich. Worth every minute of the braise 🥪\n\n"
        "#porkbelly #sandwich #lunch #recipe",
        "Lunches",
    ),
    _d(
        "DQlisbon001",
        (
            "Tram on Largo de Santo António da Sé, Lisbon, 2010.jpg",
            "Trams in Lisbon 2006-01-01.jpg",
        ),
        "Tram 28 rattling past the Sé 🚋 Ride it early, before the queue at Martim Moniz.\n"
        "📍Largo de Santo António da Sé, Lisbon\n\n#lisbon #portugal #tram28 #travel",
        "Trips",
        place=(38.7099, -9.1334, "square"),
    ),
    _d(
        "DQberrycake",
        "Easy Berry Cake with Lemon Whipped Cream.webm",
        "Easy berry cake with lemon whipped cream 🍓 The library's cooking class, at home.\n\n"
        "#cake #berries #baking #recipe",
        "Weekend bakes",
        start=20,
        length=50,
    ),
    _d(
        "DQcpudies01",
        "How-to expose flip chip CPU dies.webm",
        "How to get at the die of a flip chip CPU without wrecking it. Heat, patience and a "
        "razor blade.\n\n#hardware #electronics #macro #cpu",
        "Workshop",
        length=45,
    ),
    _d(
        "DQkaohsiun1",
        "高雄哈囉市場 4.webm",
        "高雄的哈囉市場，早餐吃什麼都好吃 🥟 Breakfast at Hello Market in Kaohsiung.\n"
        "📍哈囉市場, 高雄\n\n#kaohsiung #taiwan #streetfood #哈囉市場",
        "Trips",
        place=(22.6436, 120.2816, "marketplace"),
    ),
    _d(
        "DQturkey001",
        "One-Pot Turkey Breast with Gravy, by America's Test Kitchen 2020.webm",
        "One-pot turkey breast with gravy. Thanksgiving for four, one pot to wash 🦃\n\n"
        "#turkey #thanksgiving #onepot #recipe",
        "Dinners",
    ),
    _d(
        "DQyosemit01",
        "Yosemite meadows 2004-09-04.jpg",
        "Yosemite Valley meadows in September. Quiet, golden, and the cliffs all round.\n"
        "📍Yosemite Valley, California\n\n#yosemite #nationalpark #hiking #travel",
        "Trips",
        place=(37.7456, -119.5936, "valley"),
    ),
    _d(
        "DQleafyveg1",
        "Ask The Test Kitchen- How Should I Wash Leafy Vegetables-.webm",
        "Do you need to wash bagged salad? The test kitchen answers 🥬\n\n"
        "#kitchentips #salad #foodsafety",
    ),
    _d(
        "DQcaminand1",
        "Caminandes- Llama Drama - Short Movie.ogv",
        "Caminandes: Llama Drama. A llama versus a fence, made in Blender 🦙\n\n"
        "#blender #animation #shortfilm #3d",
        length=60,
    ),
    _d(
        "DQpitaland1",
        "Pitas leaving the oven at Pitaland, Pittsburgh.webm",
        "Fresh pitas sliding out of the oven at Pitaland ♨️ Buy them warm, eat one in the car.\n"
        "📍Pitaland, Pittsburgh\n\n#pittsburgh #pita #bakery",
        "Trips",
        place=(40.3926, -80.0444, "bakery"),
    ),
    _d(
        "DQbenchtl01",
        ("Legvise.JPG", "Benchhook.JPG"),
        "Two bench helpers I use every day: a leg vise and a bench hook. Both made from "
        "offcuts.\n\n#woodworking #workbench #handtools",
        "Workshop",
    ),
    _d(
        "DQblackhol1",
        "Zoom in onto the stellar black hole NGC 300 X-1 (ESO 1004b).webm",
        "Zooming in on a stellar black hole in the galaxy NGC 300, six million light years "
        "away 🌌\n\n#space #astronomy #blackhole",
        thumb=30,
    ),
    _d(
        "DQcurry0001",
        "Southern Indian Curry featuring Oklahoma Beef Sirloin.webm",
        "Southern Indian beef curry with coconut and curry leaves 🍛\n\n"
        "#curry #indianfood #beef #recipe",
        "Dinners",
        length=50,
    ),
    _d(
        "DQkyotost01",
        "August 2007 street in Kyoto.jpg",
        "A quiet street in Higashiyama after the rain.\n📍Higashiyama, Kyoto\n\n"
        "#kyoto #japan #streetphotography",
        "Japan trip",
        place=(34.9966, 135.7809, "suburb"),
    ),
    _d(
        "DQcatpigeo1",
        "Cat chasing a pigeon.webm",
        "No pigeons were caught in the making of this video 🐈🐦\n\n#cats #funnycats",
        length=30,
    ),
    _d(
        "DQcroissnt1",
        "Vegan Pastry - Voted Best Croissant in Miami.webm",
        "Voted the best croissant in Miami, and it's vegan 🥐\n📍Miami, Florida\n\n"
        "#miami #vegan #croissant #bakery",
        "Weekend bakes",
        length=45,
        place=(25.7617, -80.1918, "city"),
    ),
    _d(
        "DQlatteart1",
        "Latte art on cappuccino (Unsplash).jpg",
        "Cappuccino with a proper rosetta ☕\n\n#latteart #coffee #barista",
    ),
    _d(
        "DQpasta0001",
        "Cooking with Laura Homemade Pasta.webm",
        "Homemade pasta with just flour and eggs 🍝 Two cups of flour, three eggs, a pinch "
        "of salt. Knead ten minutes, rest thirty, roll it thin.\n\n"
        "#pasta #homemade #italianfood #recipe",
        "Dinners",
        start=60,
        length=60,
    ),
    _d(
        "DQchicknrc1",
        "Cooking Chicken and Rice.webm",
        "One-pan chicken and rice, the weeknight standby.\n\n#chicken #onepan #dinner",
        "Dinners",
    ),
    _d(
        "DQcoffeesr1",
        "Color sorter coffeebeans.webm",
        "A colour sorter flicking bad coffee beans out of the stream, one puff of air each ☕\n\n"
        "#coffee #engineering #satisfying",
    ),
    _d(
        "DQblackhd01",
        "Black Head Beach, New South Wales.webm",
        "Black Head Beach at first light 🌅\n📍Black Head Beach, New South Wales\n\n"
        "#australia #nsw #beach #travel",
        "Trips",
        place=(-32.0698, 152.5419, "beach"),
    ),
    _d(
        "DQsealion01",
        "Sea Lion Walking on the Beach.webm",
        "A sea lion out for a walk on the beach 🦭\n\n#sealion #wildlife #nature",
    ),
    _d(
        "DQspytrail1",
        "Washington Spy Trail sign, Flower Hill.jpg",
        "Following the Washington Spy Trail across Long Island. Revolutionary War history at "
        "every marker.\n📍Flower Hill, New York\n\n#hiking #history #longisland",
        "Trips",
        place=(40.8076, -73.6812, "village"),
    ),
    _d(
        "DQcompost01",
        "Steaming compost in Nymans Garden.webm",
        "A compost heap steaming on a cold morning at Nymans. Hot composting works 🌱\n"
        "📍Nymans, West Sussex\n\n#gardening #compost #nymans",
        place=(51.0525, -0.2003, "garden"),
    ),
    _d(
        "DQbanhmi001",
        "Just Inc. Recipe Video Sample - Portobello Banh Mi (2016).webm",
        "Portobello banh mi. Pickled carrot, lots of herbs, and the mushroom does the rest 🥖\n\n"
        "#banhmi #vegetarian #sandwich #recipe",
        "Lunches",
    ),
)


# ---------- sources ----------


def _commons(titles: list[str]) -> dict[str, dict[str, Any]]:
    """Each title's licence, author, page, and the URL to download."""
    found: dict[str, dict[str, Any]] = {}
    for i in range(0, len(titles), 20):
        params = {
            "action": "query",
            "titles": "|".join("File:" + t for t in titles[i : i + 20]),
            "prop": "imageinfo|videoinfo",
            "iiprop": "url|extmetadata",
            "iiurlwidth": 1600,
            "iiextmetadatafilter": "LicenseShortName|Artist",
            "viprop": "derivatives",
            "format": "json",
            "formatversion": "2",
        }
        req = urllib.request.Request(
            API + "?" + urllib.parse.urlencode(params), headers={"User-Agent": UA}
        )
        with urllib.request.urlopen(req, timeout=60) as res:
            pages = json.load(res)["query"]["pages"]
        for page in pages:
            title = page["title"].removeprefix("File:")
            if page.get("missing"):
                raise SystemExit(f"not on Commons: {title}")
            info = page["imageinfo"][0]
            meta = info.get("extmetadata", {})
            licence = meta.get("LicenseShortName", {}).get("value", "")
            if not FREE.fullmatch(licence):
                raise SystemExit(f"{title}: {licence!r} is not CC0, public domain or CC BY")
            artist = html.unescape(re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "")))
            url = info.get("thumburl") or info["url"]
            derivatives = {
                d.get("transcodekey"): d["src"]
                for d in page.get("videoinfo", [{}])[0].get("derivatives", [])
            }
            for key in TRANSCODES:
                if key in derivatives:
                    url = derivatives[key]
                    break
            else:
                if "videoinfo" in page:
                    url = info["url"]
            found[title] = {
                "licence": licence,
                "artist": " ".join(artist.split()) or "unknown",
                "page": info["descriptionurl"],
                "url": url,
            }
    return found


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def _cut(demo: Demo, src: Path, out: Path) -> None:
    """The clip, 720 pixels on its short side and at 30 fps, and a thumbnail:
    the most typical frame of six seconds from `thumb`. The UI crops both, as it
    crops Instagram's."""
    short = f"scale='if(gt(iw,ih),-2,{SHORT})':'if(gt(iw,ih),{SHORT},-2)'"
    _ffmpeg(
        *("-ss", str(demo.start), "-t", str(demo.length), "-i", str(src)),
        *("-vf", f"{short},fps=30,format=yuv420p", "-map", "0:v", "-map", "0:a?"),
        *("-c:v", "libx264", "-crf", "26", "-preset", "slow", "-c:a", "aac", "-b:a", "96k"),
        *("-movflags", "+faststart", str(out)),
    )
    _ffmpeg(
        *("-ss", str(demo.thumb), "-t", "6", "-i", str(out), "-vf", "thumbnail=n=150"),
        "-frames:v",
        "1",
        *("-q:v", "3", str(out.with_suffix(".jpg"))),
    )


def download(url: str, out: Path) -> Path:
    """`url` to `out`, once. Commons asks for a slower pace with a 429: wait
    and try again, longer each time."""
    if out.exists():
        return out
    for wait in (30, 60, 120, 240, 0):
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=300) as res:
                part = out.with_suffix(".part")
                with part.open("wb") as f:
                    shutil.copyfileobj(res, f)
            part.rename(out)
            time.sleep(1)  # one file at a time, a second apart
            return out
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or not wait:
                raise
            print(f"  Commons asks us to slow down; trying again in {wait} s", flush=True)
            time.sleep(wait)
    raise AssertionError("unreachable")


def _photo(src: Path, out: Path) -> None:
    """One slide, cropped to 4:5."""
    _ffmpeg(
        *("-i", str(src), "-vf"),
        f"scale={SLIDE_W}:{SLIDE_H}:force_original_aspect_ratio=increase,crop={SLIDE_W}:{SLIDE_H}",
        *("-q:v", "3", str(out)),
    )


def sources(force: bool = False) -> None:
    titles = [t for d in REELS for t in d.files]
    meta = _commons(titles)
    SOURCES.mkdir(parents=True, exist_ok=True)
    (SOURCES / "commons.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    for demo in REELS:
        folder = SOURCES / demo.code
        folder.mkdir(exist_ok=True)
        if demo.video:
            out = folder / "reel.mp4"
            if force or not out.exists():
                print(f"  cutting {demo.code}  {demo.files[0]}", flush=True)
                url = meta[demo.files[0]]["url"]
                ext = Path(urllib.parse.urlparse(url).path).suffix or ".webm"
                _cut(demo, download(url, folder / f"source{ext}"), out)
        else:
            for i, title in enumerate(demo.files, 1):
                out = folder / f"slide-{i}.jpg"
                if force or not out.exists():
                    print(f"  fetching {demo.code}  {title}", flush=True)
                    _photo(download(meta[title]["url"], folder / f"source-{i}.img"), out)
    write_credits(meta)


def _meta() -> dict[str, dict[str, Any]]:
    path = SOURCES / "commons.json"
    if not path.exists():
        raise SystemExit("no sources yet: run `scripts/demo_library.py sources` first")
    meta: dict[str, dict[str, Any]] = json.loads(path.read_text())
    return meta


def credit(demo: Demo, meta: dict[str, dict[str, Any]]) -> str:
    """The caption's last line: who made it, and under what licence."""
    seen: list[str] = []
    for title in demo.files:
        m = meta[title]
        line = f"{m['artist']}, {m['licence']}"
        if line not in seen:
            seen.append(line)
    mark = "🎥" if demo.video else "📷"
    return f"{mark} {'; '.join(seen)}, via Wikimedia Commons"


def write_credits(meta: dict[str, dict[str, Any]]) -> None:
    rows = [
        "# Demo library credits",
        "",
        "The README animation and the recordings in these docs show a demo library",
        "built by `scripts/demo_library.py` from these files on Wikimedia Commons. Each",
        "is CC0, in the public domain, or CC BY. The clips were trimmed, scaled to 720",
        "pixels on their short side and re-encoded, and the photos cropped to 4:5. The",
        "captions were written for the demo, and each ends with the credit below.",
        "",
        "| Reel | File | Author | Licence |",
        "| --- | --- | --- | --- |",
    ]
    for demo in REELS:
        for title in demo.files:
            m = meta[title]
            name = title.replace("|", "\\|")
            rows.append(
                f"| `{demo.code}` | [{name}]({m['page']}) | {m['artist']} | {m['licence']} |"
            )
    CREDITS.parent.mkdir(parents=True, exist_ok=True)
    CREDITS.write_text("\n".join(rows) + "\n")
    print(f"  wrote {CREDITS.relative_to(ROOT)}")


# ---------- the library ----------


def environment() -> dict[str, str]:
    """HIKIDASHI_* for the demo: its own files, port and stub geocoder."""
    env = {
        "HIKIDASHI_DB": str(LIB / "hikidashi.sqlite3"),
        "HIKIDASHI_MEDIA": str(LIB / "media"),
        "HIKIDASHI_IMPORTS": str(LIB / "imports"),
        "HIKIDASHI_LOGS": str(LIB / "logs"),
        "HIKIDASHI_RUN": str(LIB / "run"),
        "HIKIDASHI_CACHE": str(LIB / "cache"),
        "HIKIDASHI_MODELS": os.environ.get("HIKIDASHI_MODELS", str(ROOT / "data" / "models")),
        "HIKIDASHI_HOST": "127.0.0.1",
        "HIKIDASHI_PORT": str(PORT),
        "HIKIDASHI_AUTH": "local",
        "HIKIDASHI_ORIGIN": f"http://127.0.0.1:{PORT}",
        "HIKIDASHI_PROFILE": os.environ.get("HIKIDASHI_PROFILE", "cpu"),
        "HIKIDASHI_GEOCODER": f"http://127.0.0.1:{GEOCODER_PORT}",
        "HIKIDASHI_GEOCODER_DELAY": "0",
        "HIKIDASHI_LOCATE_EVERY": "15",
        "HIKIDASHI_WORKER_NAME": "nas",
    }
    return env


def desktop() -> dict[str, str]:
    """What the "desktop" worker adds: its name, and the worker token `build`
    made for it, which the server took as an admin's (#103)."""
    return {
        "HIKIDASHI_WORKER_NAME": "desktop",
        "HIKIDASHI_WORKER_TOKEN": (DEMO / "worker-token").read_text().strip(),
    }


def build() -> None:
    meta = _meta()
    os.environ.update(environment())
    from app import accounts
    from app.db import open_db
    from app.enrich.fetcher import FETCH_V, shard
    from app.importer.service import import_export
    from app.tagging.service import apply_hashtags
    from tests.fixtures import post_record, saved_collections, saved_posts, write_zip

    if LIB.exists():
        shutil.rmtree(LIB)
    media = LIB / "media"
    media.mkdir(parents=True)
    password = secrets.token_urlsafe(12)
    (DEMO / "password").write_text(password + "\n")

    records: dict[str, dict[str, Any]] = {}
    groups: dict[str, list[dict[str, Any]]] = {}
    for i, demo in enumerate(REELS):
        kind = "reel" if demo.video else "p"
        records[demo.code] = post_record(
            f"https://www.instagram.com/{kind}/{demo.code}/",
            timestamp=NEWEST - i * 86_400 - (i * 7919) % 30_000,
            caption=demo.caption + "\n\n" + credit(demo, meta),
            username=OWNER,
            hashtags=tuple(re.findall(r"#(\w+)", demo.caption)),
        )
        for name in demo.collections:
            groups.setdefault(name, []).append(records[demo.code])
    export = write_zip(
        DEMO / "export.zip", saved_posts(list(records.values())), saved_collections(groups)
    )

    conn = open_db(LIB / "hikidashi.sqlite3")
    try:
        owner = accounts.claim(conn, OWNER, password)
        token, _ = accounts.make_worker_token(conn, owner, "desktop")
        (DEMO / "worker-token").write_text(token + "\n")
        import_export(conn, export, user_id=owner.id)
        apply_hashtags(conn)
        for i, demo in enumerate(REELS):
            _fetched(conn, media, demo, i, FETCH_V, shard(demo.code))
    finally:
        conn.close()
    print(f"  {len(REELS)} reels in {LIB}; sign in as {OWNER} / {password}")


def _fetched(conn: Any, media: Path, demo: Demo, i: int, version: int, sub: str) -> None:
    """What the fetch would have written: files, sizes, and the counts."""
    src = SOURCES / demo.code
    thumb = f"thumbs/{sub}/{demo.code}.jpg"
    (media / thumb).parent.mkdir(parents=True, exist_ok=True)
    video, size, duration = None, None, None
    if demo.video:
        video = f"media/{sub}/{demo.code}.mp4"
        (media / video).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src / "reel.mp4", media / video)
        shutil.copyfile(src / "reel.jpg", media / thumb)
        size = (media / video).stat().st_size
        duration = _duration(media / video)
        width, height = _size(media / video)
    else:
        shutil.copyfile(src / "slide-1.jpg", media / thumb)
        width, height = SLIDE_W, SLIDE_H
        slides = []
        for n in range(len(demo.files)):
            rel = f"slides/{sub}/{demo.code}-{n + 1}.jpg"
            (media / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src / f"slide-{n + 1}.jpg", media / rel)
            slides.append((demo.code, n, rel))
        if len(slides) > 1:
            conn.executemany(
                "INSERT INTO item_slide (shortcode, position, path) VALUES (?, ?, ?)", slides
            )
    conn.execute(
        """UPDATE item SET duration = ?, width = ?, height = ?,
             like_count = ?, comment_count = ?, thumb_path = ?, media_path = ?, bytes = ?,
             fetch_v = ?, state = 'fetched', fail_stage = NULL, fail_reason = NULL
           WHERE shortcode = ?""",
        (
            duration,
            width,
            height,
            1200 + (i * 7351) % 48_000,
            12 + (i * 37) % 400,
            thumb,
            video,
            size,
            version,
            demo.code,
        ),
    )
    conn.commit()


def _size(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        [
            *("ffprobe", "-v", "error", "-select_streams", "v:0"),
            *("-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    w, h = out.stdout.strip().split(",")
    return int(w), int(h)


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return round(float(out.stdout))  # whole seconds, as the fetch writes them


# ---------- running it ----------


def _places() -> list[tuple[str, float, float, str]]:
    found = []
    for demo in REELS:
        pin = re.search(r"📍\s*([^\n]+)", demo.caption)
        if demo.place and pin:
            found.append((pin.group(1).strip(), *demo.place))
    return found


class _Geocoder(BaseHTTPRequestHandler):
    """Nominatim's /search, for the places the captions name and no others."""

    places = _places()

    def do_GET(self) -> None:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("q", [""])[0]
        key = q.casefold()
        hits = [
            {
                "lat": str(lat),
                "lon": str(lng),
                "display_name": name,
                "type": kind,
                "importance": 0.6,
                "place_rank": 18,
            }
            for name, lat, lng, kind in self.places
            if key and (key in name.casefold() or name.casefold() in key)
        ][:1]
        body = json.dumps(hits).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        pass


def serve() -> int:
    geocoder = ThreadingHTTPServer(("127.0.0.1", GEOCODER_PORT), _Geocoder)
    threading.Thread(target=geocoder.serve_forever, daemon=True).start()
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.api.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(PORT),
    ]
    try:
        return subprocess.run(cmd, env=os.environ | environment(), cwd=ROOT).returncode
    except KeyboardInterrupt:
        return 0
    finally:
        geocoder.shutdown()


def worker(remote: bool, extra: list[str]) -> int:
    env = os.environ | environment()
    if remote:
        env |= desktop()
        args = ["--server", f"http://127.0.0.1:{PORT}", "--log", "remote.log"]
    else:
        args = ["--stages", "enrich,embed"]
    cmd = [sys.executable, "-m", "app.jobs", *args, *extra]
    try:
        return subprocess.run(cmd, env=env, cwd=ROOT).returncode
    except KeyboardInterrupt:
        return 0


def cast() -> int:
    """`remote --once`, recorded as an asciicast: one event a line, as it came."""
    env = os.environ | environment() | desktop() | {"COLUMNS": "100"}
    shown = f"hikidashi worker --server http://127.0.0.1:{PORT}"
    cmd = [sys.executable, "-u", "-m", "app.jobs", "--server", f"http://127.0.0.1:{PORT}"]
    cmd += ["--log", "remote.log", "--once"]
    header = {"version": 2, "width": 100, "height": 24, "title": shown, "env": {"TERM": "xterm"}}
    events: list[list[Any]] = [[0.0, "o", f"$ {shown} --once\r\n"]]
    start = time.monotonic()
    proc = subprocess.Popen(
        cmd, env=env, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line, end="", flush=True)
        events.append([round(time.monotonic() - start + 0.6, 3), "o", line.rstrip("\n") + "\r\n"])
    code = proc.wait()
    CAST.parent.mkdir(parents=True, exist_ok=True)
    with CAST.open("w") as f:
        f.write(json.dumps(header) + "\n")
        for event in events:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    print(f"  wrote {CAST.relative_to(ROOT)}: {len(events)} lines")
    return code


def fixture() -> None:
    """The built library as CI's eval fixture: tests/eval/demo.sqlite3, with
    no sessions, tokens, password or job history, and the query vectors."""
    import sqlite3

    from app.embed.embedder import Embedder
    from app.search.eval import write_vectors

    env = environment()
    FIXTURE.mkdir(parents=True, exist_ok=True)
    db = FIXTURE / "demo.sqlite3"
    db.unlink(missing_ok=True)
    src = sqlite3.connect(f"file:{env['HIKIDASHI_DB']}?mode=ro", uri=True)
    out = sqlite3.connect(db)
    src.backup(out)
    src.close()
    for table in ("session", "invite", "api_token", "job_lease", "job_attempt", "job_worker"):
        out.execute(f"DELETE FROM {table}")
    out.execute("UPDATE user SET password_hash = NULL")
    out.commit()
    out.execute("PRAGMA journal_mode = DELETE")
    out.execute("VACUUM")
    out.close()
    print(f"wrote {db.relative_to(ROOT)} ({db.stat().st_size / 1024:.0f} KB)")

    queries = [
        q["q"] for q in json.loads((ROOT / "tests" / "search_eval.json").read_text())["queries"]
    ]
    vectors = FIXTURE / "query_vectors.json"
    write_vectors(vectors, queries, Embedder(Path(env["HIKIDASHI_MODELS"])).query)
    print(f"wrote {vectors.relative_to(ROOT)} ({len(queries)} queries)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "command",
        choices=("sources", "build", "serve", "worker", "remote", "cast", "credits", "fixture"),
    )
    ap.add_argument("--force", action="store_true", help="sources: cut every file again")
    args, extra = ap.parse_known_args(argv)
    if args.command == "sources":
        sources(args.force)
    elif args.command == "credits":
        write_credits(_meta())
    elif args.command == "build":
        build()
    elif args.command == "fixture":
        fixture()
    elif args.command == "serve":
        return serve()
    elif args.command == "cast":
        return cast()
    else:
        return worker(args.command == "remote", extra)
    return 0


if __name__ == "__main__":
    sys.exit(main())
