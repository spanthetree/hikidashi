"""Idempotent import of an Instagram export.

The contract phase 00 is judged on: importing the same export twice changes
zero rows. An import only ever adds reels or widens what we know about one; it
never overwrites enrichment, never resets a stage version, never deletes.

An export is one person's. Its reels go into the shared `item` cache, and into
that person's library (`user_item`) with their own save times and collections.
The counts are of their library: a reel someone else saved first is still
`inserted` for the person importing it now.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.library import LOCAL

from .parser import SavedPost, parse_collections, parse_saved_posts
from .shortcode import canonical_permalink

POSTS_FILE = "saved_posts.json"
COLLECTIONS_FILE = "saved_collections.json"


class ImportError_(Exception):
    """Raised when an export cannot be read at all."""


@dataclass
class ImportStats:
    run_id: int
    source_name: str
    strategy: str = ""
    seen: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    unparsed: int = 0
    collections: int = 0
    collection_names: list[str] = field(default_factory=list)
    already_imported: bool = False

    def as_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["collection_names"] = sorted(self.collection_names)
        return d


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_from_zip(zf: zipfile.ZipFile, filename: str) -> Any | None:
    for name in zf.namelist():
        if name.lower().endswith(filename) and not name.startswith("__MACOSX"):
            with zf.open(name) as fh:
                return json.loads(fh.read().decode("utf-8"))
    return None


def _load_from_dir(root: Path, filename: str) -> Any | None:
    matches = sorted(root.rglob(f"*{filename}"))
    if not matches:
        return None
    return json.loads(matches[0].read_text(encoding="utf-8"))


def read_export(source: Path) -> tuple[Any | None, Any | None]:
    """Return (saved_posts, saved_collections) from a ZIP or an unpacked dir."""
    if source.is_dir():
        return _load_from_dir(source, POSTS_FILE), _load_from_dir(source, COLLECTIONS_FILE)

    if not zipfile.is_zipfile(source):
        raise ImportError_(f"{source} is neither a directory nor a ZIP archive")

    with zipfile.ZipFile(source) as zf:
        return _load_from_zip(zf, POSTS_FILE), _load_from_zip(zf, COLLECTIONS_FILE)


# An insert, or an update that only ever fills gaps and pulls saved_at earlier.
# The WHERE clause is what makes a repeat import a true no-op: with nothing to
# widen, the UPDATE does not fire and rowcount is 0.
_UPSERT = """
INSERT INTO item (shortcode, permalink, author, author_name, saved_at, fbid,
                  caption, hashtags, raw_meta, first_seen_in, state)
VALUES (:shortcode, :permalink, :author, :author_name, :saved_at, :fbid,
        :caption, :hashtags, :raw_meta, :run_id, 'imported')
ON CONFLICT(shortcode) DO UPDATE SET
  saved_at    = CASE
      WHEN excluded.saved_at IS NOT NULL
       AND (item.saved_at IS NULL OR excluded.saved_at < item.saved_at)
      THEN excluded.saved_at ELSE item.saved_at END,
  permalink   = COALESCE(item.permalink,   excluded.permalink),
  author      = COALESCE(item.author,      excluded.author),
  author_name = COALESCE(item.author_name, excluded.author_name),
  fbid        = COALESCE(item.fbid,        excluded.fbid),
  caption     = COALESCE(item.caption,     excluded.caption),
  hashtags    = COALESCE(item.hashtags,    excluded.hashtags),
  raw_meta    = COALESCE(item.raw_meta,    excluded.raw_meta)
WHERE (excluded.saved_at IS NOT NULL
       AND (item.saved_at IS NULL OR excluded.saved_at < item.saved_at))
   OR (item.permalink   IS NULL AND excluded.permalink   IS NOT NULL)
   OR (item.author      IS NULL AND excluded.author      IS NOT NULL)
   OR (item.author_name IS NULL AND excluded.author_name IS NOT NULL)
   OR (item.fbid        IS NULL AND excluded.fbid        IS NOT NULL)
   OR (item.caption     IS NULL AND excluded.caption     IS NOT NULL)
   OR (item.hashtags    IS NULL AND excluded.hashtags    IS NOT NULL)
   OR (item.raw_meta    IS NULL AND excluded.raw_meta    IS NOT NULL)
"""

# The same, for one person's save of it: in, or saved earlier than we knew.
_SAVE = """
INSERT INTO user_item (user_id, shortcode, saved_at, first_seen_in)
VALUES (:user_id, :shortcode, :saved_at, :run_id)
ON CONFLICT(user_id, shortcode) DO UPDATE SET saved_at = excluded.saved_at
WHERE excluded.saved_at IS NOT NULL
  AND (user_item.saved_at IS NULL OR excluded.saved_at < user_item.saved_at)
"""


def import_export(conn: sqlite3.Connection, source: Path, *, user_id: int = LOCAL) -> ImportStats:
    """Import one person's export. Safe to run repeatedly on the same file."""
    source = Path(source)
    digest = _sha256(source) if source.is_file() else "directory"
    started = int(time.time())

    cur = conn.cursor()
    cur.execute(
        """INSERT INTO import_run (user_id, source_name, source_sha256, started_at)
           VALUES (?,?,?,?)""",
        (user_id, source.name, digest, started),
    )
    run_id = cur.lastrowid
    assert run_id is not None, "just inserted"
    stats = ImportStats(run_id=run_id, source_name=source.name)

    if digest != "directory":
        prior = cur.execute(
            """SELECT COUNT(*) FROM import_run
               WHERE user_id = ? AND source_sha256 = ? AND id != ?""",
            (user_id, digest, run_id),
        ).fetchone()[0]
        stats.already_imported = prior > 0

    posts_raw, collections_raw = read_export(source)
    if posts_raw is None:
        raise ImportError_(f"no {POSTS_FILE} found in {source}")

    parsed = parse_saved_posts(posts_raw)
    stats.strategy = parsed.strategy
    stats.unparsed = parsed.unparsed
    stats.seen = len(parsed.posts)

    existing = {
        r[0] for r in cur.execute("SELECT shortcode FROM user_item WHERE user_id = ?", (user_id,))
    }

    def bind(post: SavedPost) -> dict[str, Any]:
        return {
            "shortcode": post.shortcode,
            "permalink": post.permalink,
            "author": post.author,
            "author_name": post.author_name,
            "saved_at": post.saved_at,
            "fbid": post.fbid,
            "caption": post.caption,
            "hashtags": json.dumps(post.hashtags, ensure_ascii=False) if post.hashtags else None,
            "raw_meta": post.raw_json(),
            "run_id": run_id,
            "user_id": user_id,
        }

    # A reel listed in saved_posts.json and embedded in a collection is upserted
    # twice. Counting calls would double-count it, so track one outcome per
    # shortcode and total them at the end.
    outcome: dict[str, str] = {}

    def upsert(post: SavedPost) -> None:
        is_new = post.shortcode not in existing
        row = bind(post)
        cur.execute(_UPSERT, row)
        changed = bool(cur.rowcount)
        cur.execute(_SAVE, row)
        changed |= bool(cur.rowcount)

        if is_new:
            existing.add(post.shortcode)
            outcome[post.shortcode] = "inserted"
        elif outcome.get(post.shortcode) == "inserted":
            pass  # already new this run; widening is not an update
        elif changed:
            outcome[post.shortcode] = "updated"
        else:
            outcome.setdefault(post.shortcode, "unchanged")

    conn.execute("BEGIN")
    try:
        for post in parsed.posts:
            upsert(post)

        if collections_raw is not None:
            pairs, names, embedded = parse_collections(collections_raw)
            stats.collection_names = sorted(names)

            # Collections embed the full record, so a reel missing from
            # saved_posts.json still arrives with its caption and creator.
            for post in embedded:
                upsert(post)

            # Anything left is a bare URL: adopt it so the foreign key holds.
            for _, code in pairs:
                if code not in existing:
                    upsert(SavedPost(shortcode=code, permalink=canonical_permalink(code)))

            for name, code in pairs:
                cur.execute(
                    """INSERT OR IGNORE INTO collection (user_id, name, shortcode)
                       VALUES (?,?,?)""",
                    (user_id, name, code),
                )
                stats.collections += cur.rowcount

        stats.inserted = sum(1 for v in outcome.values() if v == "inserted")
        stats.updated = sum(1 for v in outcome.values() if v == "updated")
        stats.unchanged = sum(1 for v in outcome.values() if v == "unchanged")

        cur.execute(
            """UPDATE import_run SET finished_at=?, parser=?, seen=?, inserted=?,
                      updated=?, unparsed=?, collections=? WHERE id=?""",
            (
                int(time.time()),
                stats.strategy,
                stats.seen,
                stats.inserted,
                stats.updated,
                stats.unparsed,
                stats.collections,
                run_id,
            ),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    return stats
