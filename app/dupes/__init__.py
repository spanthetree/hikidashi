"""Reposts and duplicate saves (#58).

The same video is often saved more than once: reposted by another account,
cross-posted, re-uploaded with a new caption. Each copy gets a fingerprint of
what it shows, a difference hash (dHash) of three frames (one for a photo):
the frame shrunk to 9x8 grey pixels, one bit per pair of neighbours, so a
re-encode, a resize or a watermark in a corner moves few of its 64 bits.

Two reels are copies when every frame's hash is within NEAR bits of the
other's, their lengths agree, and, when both have a voiceover, enough of its
words are the same: a template that many accounts fill in (the same stock
clip, a different voiceover) looks the same and is not a copy. Copies are
grouped under the earliest saved: `item.dup_of` points at it. Someone saying
"not the same" (`item_split`) keeps two reels apart for good.

Fingerprinting runs in the embed stage, which is on the server with the media
(`phash_v`). Grouping is redone over the whole library after each batch: at
a few thousand reels that is a few thousand comparisons, by length.

Stdlib only, and ffmpeg.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from collections.abc import Callable
from pathlib import Path

# Bumping this fingerprints every reel again.
PHASH_V = 1

NEAR = 10  # bits of 64 a frame's hash may differ by; measured on the library
FRAMES = (0.2, 0.5, 0.8)  # where in a video, as a share of its length
LENGTH = 1.5  # seconds two copies' lengths may differ by
SAME_WORDS = 0.5  # share of their voiceovers' words two copies must share
MIN_WORDS = 8  # fewer than this is too little voiceover to go by


def dhash(gray: bytes) -> int:
    """A 9x8 frame of grey bytes as 64 bits: each pixel brighter than its
    right-hand neighbour or not."""
    if len(gray) != 72:
        raise ValueError(f"a 9x8 frame is 72 bytes, not {len(gray)}")
    bits = 0
    for y in range(8):
        row = gray[y * 9 : y * 9 + 9]
        for x in range(8):
            bits = bits << 1 | (row[x] > row[x + 1])
    return bits


def frame(path: Path, at: float | None = None, *, timeout: int = 60) -> int | None:
    """The hash of one frame, `at` seconds into a video, or of an image. None
    when ffmpeg finds no picture there."""
    seek = ["-ss", f"{at:.2f}"] if at else []
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                *seek,
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-vf",
                "scale=9:8:flags=area,format=gray",
                "-f",
                "rawvideo",
                "-",
            ],
            capture_output=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    return dhash(proc.stdout) if proc.returncode == 0 and len(proc.stdout) == 72 else None


def fingerprint(path: Path, duration: float | None) -> list[int]:
    """Three frames of a video, or the one of a photo or a video of unknown
    length. Empty when none could be read."""
    times = [duration * f for f in FRAMES] if duration else [None]
    hashes = [frame(path, t) for t in times]
    return [h for h in hashes if h is not None] if all(h is not None for h in hashes) else []


def pending(conn: sqlite3.Connection, shortcodes: list[str] | None = None) -> list[str]:
    """Reels with a picture on disk and no fingerprint at PHASH_V."""
    only = "AND shortcode IN (SELECT value FROM json_each(:only))" if shortcodes is not None else ""
    return [
        r[0]
        for r in conn.execute(
            f"""SELECT shortcode FROM item
                WHERE phash_v < :v AND state != 'failed'
                  AND (media_path IS NOT NULL OR thumb_path IS NOT NULL) {only}
                ORDER BY saved_at IS NULL, saved_at DESC""",
            {"v": PHASH_V, "only": json.dumps(shortcodes)},
        )
    ]


Hasher = Callable[[Path, "float | None"], list[int]]


def hash_all(
    conn: sqlite3.Connection,
    media_dir: Path,
    shortcodes: list[str] | None = None,
    *,
    hasher: Hasher = fingerprint,
) -> int:
    """Fingerprint every pending reel, then regroup the library. How many
    were fingerprinted."""
    codes = pending(conn, shortcodes)
    for code in codes:
        r = conn.execute(
            "SELECT media_path, thumb_path, duration FROM item WHERE shortcode = ?", (code,)
        ).fetchone()
        path = r["media_path"] or r["thumb_path"]
        hashes = hasher(media_dir / path, r["duration"] if r["media_path"] else None)
        conn.execute(
            "UPDATE item SET phash = ?, phash_v = ? WHERE shortcode = ?",
            (" ".join(f"{h:016x}" for h in hashes) or None, PHASH_V, code),
        )
    if codes:
        group(conn)
    return len(codes)


def _words(text: str | None) -> set[str]:
    return {w for w in (text or "").lower().split() if w.isalnum()}


def same(a: sqlite3.Row, b: sqlite3.Row) -> bool:
    """Whether two fingerprinted reels are copies of one video."""
    ha, hb = a["phash"].split(), b["phash"].split()
    if len(ha) != len(hb) or bool(a["media_path"]) != bool(b["media_path"]):
        return False
    if any((int(x, 16) ^ int(y, 16)).bit_count() > NEAR for x, y in zip(ha, hb, strict=True)):
        return False
    if a["duration"] and b["duration"] and abs(a["duration"] - b["duration"]) > LENGTH:
        return False
    wa, wb = _words(a["transcript"]), _words(b["transcript"])
    if len(wa) >= MIN_WORDS and len(wb) >= MIN_WORDS:
        return len(wa & wb) / len(wa | wb) >= SAME_WORDS
    return True


def group(conn: sqlite3.Connection) -> int:
    """Group every fingerprinted reel with its copies, under the earliest
    saved, and clear `dup_of` everywhere else. How many groups."""
    rows = conn.execute(
        """SELECT shortcode, phash, media_path, duration, transcript, saved_at FROM item
           WHERE phash IS NOT NULL ORDER BY duration IS NULL, duration"""
    ).fetchall()
    split = {tuple(r) for r in conn.execute("SELECT a, b FROM item_split")}
    parent = {r["shortcode"]: r["shortcode"] for r in rows}

    def root(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    # Sorted by length, so only the reels within LENGTH of each other are
    # compared; photos (no length) all with each other.
    for i, a in enumerate(rows):
        for b in rows[i + 1 :]:
            if a["duration"] and b["duration"] and b["duration"] - a["duration"] > LENGTH:
                break
            pair = tuple(sorted((a["shortcode"], b["shortcode"])))
            if pair not in split and same(a, b):
                parent[root(a["shortcode"])] = root(b["shortcode"])

    members: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        members.setdefault(root(r["shortcode"]), []).append(r)
    dup_of: dict[str, str | None] = {}
    groups = 0
    for found in members.values():
        first = min(found, key=lambda r: (r["saved_at"] or 0, r["shortcode"]))["shortcode"]
        groups += len(found) > 1
        for r in found:
            dup_of[r["shortcode"]] = None if r["shortcode"] == first or len(found) == 1 else first
    # One transaction, so a reader never sees the groups half redone.
    own = not conn.in_transaction
    if own:
        conn.execute("BEGIN")
    try:
        conn.execute("UPDATE item SET dup_of = NULL WHERE dup_of IS NOT NULL")
        conn.executemany(
            "UPDATE item SET dup_of = ? WHERE shortcode = ?",
            [(v, k) for k, v in dup_of.items() if v],
        )
        if own:
            conn.execute("COMMIT")
    except BaseException:
        if own:
            conn.execute("ROLLBACK")
        raise
    return groups


COLLAPSE = "collapse_copies"  # the setting: one card for a reel and its copies


def collapsing(conn: sqlite3.Connection, user_id: int) -> bool:
    row = conn.execute(
        "SELECT value FROM setting WHERE user_id = ? AND key = ?", (user_id, COLLAPSE)
    ).fetchone()
    return bool(row and json.loads(row[0]))


def set_collapsing(conn: sqlite3.Connection, user_id: int, on: bool) -> None:
    conn.execute(
        """INSERT INTO setting (user_id, key, value, updated_at, updated_by)
           VALUES (:u, :k, :v, strftime('%s', 'now'), :u)
           ON CONFLICT (ifnull(user_id, 0), key)
           DO UPDATE SET value = :v, updated_at = strftime('%s', 'now'), updated_by = :u""",
        {"u": user_id, "k": COLLAPSE, "v": json.dumps(on)},
    )


# SQL over `item` and `ui` (user_item) for the grid. The group a reel is in is
# named by its first copy: dup_of, or itself.
GROUP = "COALESCE(item.dup_of, item.shortcode)"
# How many of the group this person has, counting it; 1 for most reels.
COPIES = f"""(SELECT COUNT(*) FROM item o JOIN user_item u2
                ON u2.shortcode = o.shortcode AND u2.user_id = ui.user_id
              WHERE o.shortcode = {GROUP} OR o.dup_of = {GROUP})"""
# True for every reel but the first this person saved of its group.
LATER_COPY = f"""EXISTS (SELECT 1 FROM item o JOIN user_item u2
                  ON u2.shortcode = o.shortcode AND u2.user_id = ui.user_id
                WHERE (o.shortcode = {GROUP} OR o.dup_of = {GROUP})
                  AND o.shortcode != item.shortcode
                  AND (COALESCE(u2.saved_at, 0), o.shortcode)
                    < (COALESCE(ui.saved_at, 0), item.shortcode))"""


def split(conn: sqlite3.Connection, a: str, b: str) -> None:
    """Someone said `a` and `b` are not the same: never group them again."""
    x, y = sorted((a, b))
    conn.execute("INSERT OR IGNORE INTO item_split (a, b) VALUES (?, ?)", (x, y))
    group(conn)


def copies(conn: sqlite3.Connection, shortcode: str, user_id: int) -> list[str]:
    """The other copies of a reel in this person's library, earliest saved first."""
    return [
        r[0]
        for r in conn.execute(
            """SELECT o.shortcode FROM item me JOIN item o
                 ON COALESCE(o.dup_of, o.shortcode) = COALESCE(me.dup_of, me.shortcode)
                AND o.shortcode != me.shortcode
               JOIN user_item ui ON ui.shortcode = o.shortcode AND ui.user_id = ?
               WHERE me.shortcode = ? AND (me.dup_of IS NOT NULL OR o.dup_of IS NOT NULL)
               ORDER BY ui.saved_at""",
            (user_id, shortcode),
        )
    ]
