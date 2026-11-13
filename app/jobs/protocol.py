"""What a worker on another machine and the server send each other (#35).

The server stays the only writer of the database. A remote worker claims
reels over HTTP, and the server answers with each reel's row: the columns the
stage reads, and the paths of the media files it needs, which the worker
downloads. The worker runs the stage's own per-reel function on them
(`transcribe_one`, `read`, `tag_one`, `read_one`) and sends back one result
per reel: what the stage produced, or why it failed. The server checks each
result's shape and records it with the stage's own `record` and
`record_failure`, exactly as a local worker would have, then releases the
leases as the local job source does.

    claim     {stage, limit}  ->  {jobs: [row, ...]}
    results   {stage, profile, results: [{shortcode, <what>} | {shortcode, error}]}

A hosted worker (protocol 2, app/ai/remote.py) instead claims reels for all
their remaining stages at once, asks for each stage's rows as it gets there,
and sends its results with `languages` and `usage`.

`what` is per stage: `transcript` (asr), `text` (ocr), `tag` (tag), `recipe`
(recipe), each possibly null for "nothing there". An asr result may also carry
`segments`, [[start_ms, end_ms, text], ...], when the model timed them (#44).

Every request carries `protocol` and the worker's stage `versions`. A worker
whose stage version differs from the server's would stamp work the server
considers stale, or new, so it is refused rather than recorded: both sides
must run the same release. Enrich and embed stay on the server: enrich writes
media files, and embed must use the same model as search.

Stdlib only, like the stages' cores.
"""

from __future__ import annotations

import math
import re
import sqlite3
import threading
from collections.abc import Callable
from typing import Any

from app.asr import worker as asr
from app.asr.transcriber import Said
from app.ocr import worker as ocr
from app.recipes import llm as recipe_llm
from app.recipes import worker as recipe
from app.tagging import games
from app.tagging import llm as tag_llm
from app.tagging import worker as tag
from app.tagging.service import ensure_taxonomy
from app.tagging.taxonomy import names

from .queue import REGISTRY

PROTOCOL = 2  # 2: a hosted worker claims a reel for all its stages (#125)

# The stages a remote worker may claim, in pipeline order.
REMOTE = ("asr", "ocr", "tag", "recipe")

# Each stage's rows, for the reels it claimed.
ROWS: dict[str, Callable[..., list[sqlite3.Row]]] = {
    "asr": asr.pending,
    "ocr": ocr.pending,
    "tag": tag.pending,
    "recipe": recipe.pending,
}

# The row columns that name media files the stage reads.
MEDIA = {"asr": ("media_path",), "ocr": ("media_path", "thumb_path"), "tag": (), "recipe": ()}

# What each stage's result calls what it produced.
FIELD = {"asr": "transcript", "ocr": "text", "tag": "tag", "recipe": "recipe"}

REASON = 200  # the most of a failure's reason kept, as the stages keep
TEXT = 200_000  # the longest transcript or OCR text accepted, in characters


# A hosted model's profile: provider:model, as groq:whisper-large-v3-turbo.
HOSTED_PROFILE = re.compile(r"^[a-z0-9_-]{1,40}:[\w./:@-]{1,200}$")


class Refused(ValueError):
    """A request the server will not act on: the reason is the reply."""


def versions() -> dict[str, int]:
    """This release's version of each remote stage."""
    return {s: REGISTRY[s].version for s in REMOTE}


def check(stage: str, protocol: Any, theirs: Any) -> None:
    """Refuse a stage this server does not hand out, a protocol it does not
    speak, or a worker on a different version of the stage."""
    if stage not in REMOTE:
        raise Refused(
            f"{stage!r} does not run remotely; a remote worker runs some of {', '.join(REMOTE)}"
        )
    if protocol != PROTOCOL:
        raise Refused(f"worker protocol {protocol!r}, server {PROTOCOL}: update one to match")
    mine = REGISTRY[stage].version
    got = theirs.get(stage) if isinstance(theirs, dict) else None
    if got != mine:
        raise Refused(
            f"{stage} is at version {mine} here and {got} on the worker: "
            "run the same Hikidashi release on both"
        )


def rows(conn: sqlite3.Connection, stage: str, codes: list[str]) -> list[dict[str, Any]]:
    """The claimed reels' rows, as the worker is sent them."""
    if not codes:
        return []
    return [dict(r) for r in ROWS[stage](conn, shortcodes=codes)]


def media(row: dict[str, Any], stage: str) -> list[str]:
    """The media files, relative to the media folder, a stage reads for a row."""
    return [row[c] for c in MEDIA[stage] if row.get(c)]


# --- results: built on the worker --------------------------------------------


def ok(stage: str, shortcode: str, produced: Any) -> dict[str, Any]:
    """A reel the stage finished. `produced` is the stage's own value: a
    transcript or text (or None), a tagging Result, a recipe Found (or None)."""
    if stage == "tag":
        produced = _from_tag(produced)
    elif stage == "recipe":
        produced = None if produced is None else _from_found(produced)
    elif isinstance(produced, Said):
        return {
            "shortcode": shortcode,
            "transcript": produced.text,
            "segments": [list(seg) for seg in produced.segments],
        }
    return {"shortcode": shortcode, FIELD[stage]: produced}


def failed(shortcode: str, reason: str) -> dict[str, Any]:
    return {"shortcode": shortcode, "error": reason[:REASON]}


def _from_tag(r: tag_llm.Result) -> dict[str, Any]:
    g = r.game
    return {
        "summary": r.summary,
        "category": r.category,
        "confidence": r.confidence,
        "keywords": list(r.keywords),
        # What the model said, not what to apply: the server decides that
        # from the reel's text as it has it (app/tagging/worker.py).
        "game": None
        if g is None
        else {
            "kind": g.kind,
            "title": g.title,
            "confidence": g.confidence,
            "others": list(g.others),
            "official": g.official,
        },
    }


def _from_found(f: recipe_llm.Found) -> dict[str, Any]:
    r = f.recipe
    return {
        "recipe": {
            "title": r.title,
            "servings": r.servings,
            "time_minutes": r.time_minutes,
            "ingredients": [
                {
                    "item": i.item,
                    "quantity": i.quantity,
                    "unit": i.unit,
                    "note": i.note,
                    "as_written": i.as_written,
                }
                for i in r.ingredients
            ],
            "steps": list(r.steps),
            "incomplete": r.incomplete,
        },
        "confidence": f.confidence,
        "source_fields": list(f.source_fields),
        "dropped": list(f.dropped),
    }


# --- results: checked and recorded on the server -----------------------------


def _str(v: Any, what: str, limit: int, *, null: bool = True) -> str | None:
    if v is None and null:
        return None
    if not isinstance(v, str) or len(v) > limit:
        raise Refused(f"{what} must be text of at most {limit:,} characters")
    return v


def _int(v: Any, what: str) -> int | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise Refused(f"{what} must be a whole number")
    return v


def _unit(v: Any, what: str, *, null: bool = True) -> float | None:
    if v is None and null:
        return None
    if isinstance(v, bool) or not isinstance(v, int | float) or not 0 <= v <= 1:
        raise Refused(f"{what} must be a number from 0 to 1")
    if math.isnan(v):
        raise Refused(f"{what} must be a number from 0 to 1")
    return float(v)


def _strs(v: Any, what: str, limit: int, most: int) -> list[str]:
    if not isinstance(v, list) or len(v) > most:
        raise Refused(f"{what} must be a list of at most {most}")
    return [s for s in (_str(x, what, limit, null=False) for x in v) if s is not None]


def _obj(v: Any, what: str) -> dict[str, Any]:
    if not isinstance(v, dict):
        raise Refused(f"{what} must be an object")
    return v


def _to_tag(v: Any) -> tag_llm.Result:
    d = _obj(v, "tag")
    # As the tagger's own parse: a name the taxonomy does not have is no tag.
    category = _str(d.get("category"), "tag.category", 100)
    category = category if category in names() else None
    return tag_llm.Result(
        summary=_str(d.get("summary"), "tag.summary", 2_000),
        category=category,
        confidence=_unit(d.get("confidence"), "tag.confidence") if category else None,
        keywords=_strs(d.get("keywords", []), "tag.keywords", 100, tag_llm.MAX_KEYWORDS),
        game=_to_game(d.get("game")),
    )


def _to_game(v: Any) -> games.Answer | None:
    if v is None:
        return None
    d = _obj(v, "tag.game")
    kind = d.get("kind")
    level = d.get("confidence")
    if kind not in (games.ONE, games.SEVERAL, games.NONE) or level not in games.LEVELS:
        raise Refused("tag.game needs a kind of one, several or none, and a confidence")
    title = _str(d.get("title"), "tag.game.title", games.MAX_TITLE)
    return games.Answer(
        kind=kind,
        title=title if kind == games.ONE else None,
        confidence=level,
        others=tuple(
            _strs(d.get("others", []), "tag.game.others", games.MAX_TITLE, games.MAX_OTHERS)
        ),
        official=_str(d.get("official"), "tag.game.official", games.MAX_TITLE),
    )


def _to_found(v: Any) -> recipe_llm.Found | None:
    if v is None:
        return None
    d = _obj(v, "recipe")
    r = _obj(d.get("recipe"), "recipe.recipe")
    ingredients = r.get("ingredients", [])
    if not isinstance(ingredients, list) or len(ingredients) > recipe_llm.MAX_INGREDIENTS:
        raise Refused(f"recipe ingredients must be a list of at most {recipe_llm.MAX_INGREDIENTS}")
    out = []
    for i in ingredients:
        i = _obj(i, "recipe ingredient")
        item = _str(i.get("item"), "ingredient item", 500, null=False)
        assert item is not None
        out.append(
            recipe_llm.Ingredient(
                item=item,
                quantity=_str(i.get("quantity"), "ingredient quantity", 100),
                unit=_str(i.get("unit"), "ingredient unit", 100),
                note=_str(i.get("note"), "ingredient note", 500),
                as_written=_str(i.get("as_written"), "ingredient as written", 1_000),
            )
        )
    incomplete = r.get("incomplete", False)
    if not isinstance(incomplete, bool):
        raise Refused("recipe.incomplete must be true or false")
    return recipe_llm.Found(
        recipe=recipe_llm.Recipe(
            title=_str(r.get("title"), "recipe title", 500),
            servings=_int(r.get("servings"), "recipe servings"),
            time_minutes=_int(r.get("time_minutes"), "recipe time"),
            ingredients=out,
            steps=_strs(r.get("steps", []), "recipe steps", 5_000, recipe_llm.MAX_STEPS),
            incomplete=incomplete,
        ),
        confidence=_unit(d.get("confidence"), "recipe confidence", null=False) or 0.0,
        source_fields=_strs(d.get("source_fields", []), "recipe source fields", 50, 10),
        dropped=_strs(d.get("dropped", []), "recipe dropped", 500, recipe_llm.MAX_INGREDIENTS),
    )


def _segments(v: Any) -> tuple[tuple[int, int, str], ...]:
    """An asr result's [[start_ms, end_ms, text], ...]."""
    if not isinstance(v, list) or len(v) > TEXT // 10:
        raise Refused("segments must be a list")
    out = []
    for seg in v:
        if not isinstance(seg, list) or len(seg) != 3:
            raise Refused("a segment is [start_ms, end_ms, text]")
        start, end = _int(seg[0], "segment start"), _int(seg[1], "segment end")
        text = _str(seg[2], "segment text", TEXT, null=False)
        assert start is not None and end is not None and text is not None
        out.append((start, end, text))
    return tuple(out)


def parse(stage: str, results: Any) -> list[tuple[str, Any, str | None]]:
    """Check every result before any is recorded: (shortcode, what it
    produced, None) or (shortcode, None, why it failed). Raises Refused."""
    if not isinstance(results, list):
        raise Refused("results must be a list")
    out: list[tuple[str, Any, str | None]] = []
    for r in results:
        r = _obj(r, "a result")
        code = _str(r.get("shortcode"), "shortcode", 64, null=False)
        assert code is not None
        if "error" in r:
            why = _str(r["error"], "error", 10_000, null=False) or "failed"
            out.append((code, None, why[:REASON] or "failed"))
            continue
        name = FIELD[stage]
        if name not in r:
            raise Refused(f"a {stage} result has {name} or error")
        v = r[name]
        if stage in ("asr", "ocr"):
            produced: Any = _str(v, name, TEXT)
            if stage == "asr" and produced and r.get("segments"):
                produced = Said(produced, _segments(r["segments"]))
        elif stage == "tag":
            produced = _to_tag(v)
        else:
            produced = _to_found(v)
        out.append((code, produced, None))
    return out


def record_one(
    conn: sqlite3.Connection,
    stage: str,
    code: str,
    produced: Any,
    why: str | None,
    *,
    profile: str,
    lock: threading.Lock | None = None,
    language: str | None = None,
) -> None:
    """Record one reel's result for one stage with the stage's own function:
    what it produced, or (with `why`) that it failed. `language`: what a
    hosted ASR model heard (#116)."""
    lock = lock or threading.Lock()
    if stage == "asr":
        if why is None:
            asr.record(conn, lock, code, produced, profile, language)
        else:
            asr.record_failure(conn, lock, code, why)
    elif stage == "ocr":
        if why is None:
            # A hosted model's provider:model; tesseract's profile is the hardware's.
            ocr.record(conn, lock, code, produced, profile if ":" in profile else None)
        else:
            ocr.record_failure(conn, lock, code, why)
    elif stage == "tag":
        if why is None:
            tag.record(conn, code, produced, profile)
        else:
            tag.record_failure(conn, code, why)
    elif why is None:
        recipe.record(conn, code, produced, profile)
    else:
        recipe.record_failure(conn, code, why)


def record(
    conn: sqlite3.Connection,
    stage: str,
    parsed: list[tuple[str, Any, str | None]],
    *,
    profile: str,
    held: set[str],
    languages: dict[str, str | None] | None = None,
) -> list[str]:
    """Record the results for the reels this worker holds, with the stage's
    own functions. A result for a reel it no longer holds (its lease ran out
    and another worker took it) is dropped. Returns those recorded."""
    lock = threading.Lock()
    if stage == "tag":
        ensure_taxonomy(conn)
    done = []
    for code, produced, why in parsed:
        if code not in held:
            continue
        record_one(
            conn,
            stage,
            code,
            produced,
            why,
            profile=profile,
            lock=lock,
            language=(languages or {}).get(code),
        )
        done.append(code)
    return done
