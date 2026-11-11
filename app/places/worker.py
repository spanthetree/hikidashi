"""The places worker. Two passes, both resumable.

Read: every reel behind the current `place_v` has its caption's places written
to item_place, replacing any older reading, and is stamped. Free and offline:
it is a few regular expressions, so it runs whether or not there is a geocoder.
A reel with no caption yet waits: enrich may fetch one, and it is read then.

Look up: every place text with no geocode row, or one behind `geo_v`, is asked
of the geocoder and the answer stored, found or not. Texts are shared, so a
place many reels name is looked up once. Each answer commits as it arrives, so
a run can be killed and resumed, and the public server's one request a second
is never spent twice on the same place.

Outcomes of a lookup:

    located     lat and lng stored with a confidence; on the map if confident
    not found   a row with no lat, so it is not asked again until geo_v moves
    error       nothing stored; the run stops, and the next resumes from it

Refine: a reel whose sure places agree on a region has its other places looked
at again, inside that region: the unsure ones, and any that landed far outside
it. Found there, a place moves. Not found, an unsure place stays unsure and an
outlier is made unsure, which takes it off the map. Either way `near` records
the region, so it is not asked twice.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import PLACE_V, extract
from .geocoder import CONFIDENT, GEO_V, Located, Region, locate

# What an outlier with nowhere better to go is demoted to: under CONFIDENT.
DOUBTED = 0.3


@dataclass
class Progress:
    total: int = 0
    located: int = 0
    approximate: int = 0  # found, but not confidently enough for the map
    missing: int = 0

    def line(self) -> str:
        done = self.located + self.approximate + self.missing
        return (
            f"{done}/{self.total}  located {self.located}"
            f"  approximate {self.approximate}  not found {self.missing}"
        )


def unread(conn: sqlite3.Connection, *, force: bool = False) -> list[sqlite3.Row]:
    clause = "" if force else "AND place_v < :v"
    return conn.execute(
        f"SELECT shortcode, caption FROM item WHERE caption IS NOT NULL {clause}", {"v": PLACE_V}
    ).fetchall()


def read_all(conn: sqlite3.Connection, *, force: bool = False) -> int:
    """Read the places out of every caption behind place_v. Returns how many
    reels were read."""
    rows = unread(conn, force=force)
    conn.execute("BEGIN")
    try:
        for row in rows:
            conn.execute("DELETE FROM item_place WHERE shortcode = ?", (row["shortcode"],))
            conn.executemany(
                "INSERT INTO item_place (shortcode, seq, text) VALUES (?, ?, ?)",
                [(row["shortcode"], i, p.text) for i, p in enumerate(extract(row["caption"]))],
            )
            conn.execute(
                "UPDATE item SET place_v = ? WHERE shortcode = ?", (PLACE_V, row["shortcode"])
            )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return len(rows)


def pending(
    conn: sqlite3.Connection, *, limit: int | None = None, force: bool = False
) -> list[str]:
    """Place texts to look up, the most-named first."""
    clause = "" if force else "WHERE g.text IS NULL OR g.geo_v < :v"
    sql = f"""SELECT p.text FROM item_place p LEFT JOIN geocode g ON g.text = p.text
              {clause} GROUP BY p.text ORDER BY COUNT(*) DESC, p.text"""
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [r[0] for r in conn.execute(sql, {"v": GEO_V})]


def store(
    conn: sqlite3.Connection, text: str, found: Located | None, near: str | None = None
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO geocode
             (text, lat, lng, label, kind, confidence, query, near, geo_v, looked_up_at)
           VALUES (:text, :lat, :lng, :label, :kind, :confidence, :query, :near, :v, :now)""",
        {
            "text": text,
            "near": near,
            "v": GEO_V,
            "now": int(time.time()),
            **(
                {
                    "lat": found.lat,
                    "lng": found.lng,
                    "label": found.label,
                    "kind": found.kind,
                    "confidence": found.confidence,
                    "query": found.query,
                }
                if found
                else dict.fromkeys(("lat", "lng", "label", "kind", "confidence", "query"))
            ),
        },
    )


def locate_all(
    conn: sqlite3.Connection,
    search: Callable[[str], list[dict[str, Any]]],
    *,
    limit: int | None = None,
    force: bool = False,
    on_progress: Callable[[Progress], None] | None = None,
) -> Progress:
    """Look up every pending place. Raises geocoder.GeocodeError, having
    stored nothing for the place in hand, when the server cannot be asked."""
    texts = pending(conn, limit=limit, force=force)
    progress = Progress(total=len(texts))
    for text in texts:
        found = locate(text, search)
        store(conn, text, found)
        if found is None:
            progress.missing += 1
        elif found.confident:
            progress.located += 1
        else:
            progress.approximate += 1
        if on_progress:
            on_progress(progress)
    return progress


@dataclass
class Refined:
    moved: int = 0  # found again inside its reel's region
    doubted: int = 0  # an outlier with no match there: off the map
    kept: int = 0  # unsure, and still unsure


def refine(conn: sqlite3.Connection, search: Callable[..., list[dict[str, Any]]]) -> Refined:
    """Look again at the places that disagree with the rest of their reel.
    See the module docstring. Raises GeocodeError as locate_all does."""
    reels: dict[str, list[sqlite3.Row]] = {}
    for r in conn.execute(
        """SELECT p.shortcode, p.text, g.lat, g.lng, g.label, g.kind,
                      g.confidence, g.query, g.near
               FROM item_place p JOIN geocode g ON g.text = p.text
               WHERE g.lat IS NOT NULL ORDER BY p.shortcode, p.seq"""
    ):
        reels.setdefault(r["shortcode"], []).append(r)

    out = Refined()
    for places in reels.values():
        region = Region.of([(p["lat"], p["lng"]) for p in places if p["confidence"] >= CONFIDENT])
        if region is None:
            continue
        for p in places:
            sure, inside = p["confidence"] >= CONFIDENT, region.holds(p["lat"], p["lng"])
            if p["near"] or (sure and inside):
                continue
            found = locate(p["text"], search, region)
            if found:
                store(conn, p["text"], found, near=region.box)
                out.moved += 1
                continue
            as_was = Located(
                p["lat"],
                p["lng"],
                p["label"],
                p["kind"],
                p["confidence"] if inside else min(p["confidence"], DOUBTED),
                p["query"],
            )
            store(conn, p["text"], as_was, near=region.box)
            if inside or not sure:
                out.kept += 1
            else:
                out.doubted += 1
    return out


def summary(conn: sqlite3.Connection) -> dict[str, int]:
    row = conn.execute(
        """SELECT (SELECT COUNT(*) FROM item
                   WHERE caption IS NOT NULL AND place_v < :pv) AS unread,
                  (SELECT COUNT(DISTINCT shortcode) FROM item_place) AS reels,
                  COUNT(DISTINCT p.text) AS places,
                  COUNT(DISTINCT CASE WHEN g.confidence >= :c THEN p.text END) AS located,
                  COUNT(DISTINCT CASE WHEN g.confidence < :c THEN p.text END) AS approximate,
                  COUNT(DISTINCT CASE WHEN g.text IS NOT NULL AND g.lat IS NULL
                                      THEN p.text END) AS missing,
                  COUNT(DISTINCT CASE WHEN g.text IS NULL OR g.geo_v < :gv
                                      THEN p.text END) AS waiting
           FROM item_place p LEFT JOIN geocode g ON g.text = p.text""",
        {"pv": PLACE_V, "gv": GEO_V, "c": CONFIDENT},
    ).fetchone()
    return {k: (row[k] or 0) for k in row.keys()}
