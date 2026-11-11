"""The enrichment worker.

Pulls items that have not been fetched at the current `fetch_v`, fetches each,
and records the outcome. Every unit of work is one item and one transaction, so
the process can be killed at any point and resumed without losing progress or
repeating finished work.

Failures are data, not exceptions: a private or deleted reel lands in `failed`
with a reason and an attempt count, and stays in the library as a metadata-only
row. The save is still a record.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .fetcher import (
    FETCH_V,
    Fetched,
    FetchError,
    download_media,
    download_thumbnail,
    is_carousel,
    probe,
    probe_duration,
    probe_slides,
    shard,
)

# Permanent failures are not worth retrying; transient ones get a few goes.
MAX_ATTEMPTS = 3


@dataclass
class Progress:
    total: int = 0
    fetched: int = 0
    failed: int = 0
    skipped: int = 0
    thumbs: int = 0
    slides: int = 0
    media: int = 0
    bytes: int = 0

    def line(self) -> str:
        done = self.fetched + self.failed + self.skipped
        mb = self.bytes / 1_048_576
        return (
            f"{done}/{self.total}  fetched {self.fetched}  failed {self.failed}"
            f"  thumbs {self.thumbs}  slides {self.slides}  media {self.media}"
            f"  {mb:,.0f} MB"
        )


def pending(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
    retry_failed: bool = False,
    shortcodes: list[str] | None = None,
) -> list[sqlite3.Row]:
    """Items not yet fetched at the current fetch_v, oldest saves first.
    `shortcodes` narrows it to those."""
    # A permanent failure stamps fetch_v so it stops coming back round; asking
    # for a retry has to reach past that stamp, not just look for stale rows.
    clause = (
        "(fetch_v < :v OR state = 'failed')"
        if retry_failed
        else "fetch_v < :v AND (state != 'failed' OR attempts < :max)"
    )
    if shortcodes is not None:
        clause += " AND shortcode IN (SELECT value FROM json_each(:only))"
    sql = f"""SELECT shortcode, permalink, attempts FROM item
              WHERE {clause}
              ORDER BY saved_at IS NULL, saved_at DESC"""
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(
        sql, {"v": FETCH_V, "max": MAX_ATTEMPTS, "only": json.dumps(shortcodes)}
    ).fetchall()


def _record_success(
    conn: sqlite3.Connection,
    lock: threading.Lock,
    shortcode: str,
    got: Fetched,
    thumb_rel: str | None,
    media_rel: str | None,
    size: int | None,
) -> None:
    """Fill gaps and set the fetched columns. Never overwrites hand-written data.

    The export is the earlier, more trustworthy source for author and caption,
    so COALESCE keeps what is already there and only fills what is missing.
    """
    with lock:
        conn.execute(
            """UPDATE item SET
                 author        = COALESCE(author, :author),
                 author_name   = COALESCE(author_name, :author_name),
                 caption       = COALESCE(caption, :caption),
                 duration      = :duration,
                 width         = :width,
                 height        = :height,
                 like_count    = :like_count,
                 comment_count = :comment_count,
                 thumb_path    = COALESCE(:thumb, thumb_path),
                 media_path    = COALESCE(:media, media_path),
                 bytes         = COALESCE(:bytes, bytes),
                 fetch_meta    = :fetch_meta,
                 fetch_v       = :v,
                 state         = 'fetched',
                 fail_stage    = NULL,
                 fail_reason   = NULL
               WHERE shortcode = :shortcode""",
            {
                "author": got.author,
                "author_name": got.author_name,
                "caption": got.caption,
                "duration": got.duration,
                "width": got.width,
                "height": got.height,
                "like_count": got.like_count,
                "comment_count": got.comment_count,
                "thumb": thumb_rel,
                "media": media_rel,
                "bytes": size,
                "fetch_meta": got.raw_json(),
                "v": FETCH_V,
                "shortcode": shortcode,
            },
        )


def save_slides(
    conn: sqlite3.Connection, lock: threading.Lock, media_dir: Path, shortcode: str, url: str
) -> int:
    """Download every slide of a carousel of photos and record them, returning
    how many. Zero for anything else, or if one would not download: half a
    carousel would page through to a gap, and the next run can try again."""
    try:
        urls = probe_slides(url)
    except FetchError:
        return 0
    saved: list[str] = []
    for i, src in enumerate(urls):
        rel = f"slides/{shard(shortcode)}/{shortcode}-{i + 1}.jpg"
        try:
            download_thumbnail(src, media_dir / rel)
        except FetchError:
            for done in saved:
                (media_dir / done).unlink(missing_ok=True)
            return 0
        saved.append(rel)
    if not saved:
        return 0
    with lock:
        conn.execute("DELETE FROM item_slide WHERE shortcode = ?", (shortcode,))
        conn.executemany(
            "INSERT INTO item_slide (shortcode, position, path) VALUES (?, ?, ?)",
            [(shortcode, i, rel) for i, rel in enumerate(saved)],
        )
    return len(saved)


def _record_failure(
    conn: sqlite3.Connection, lock: threading.Lock, shortcode: str, reason: str, *, permanent: bool
) -> None:
    with lock:
        conn.execute(
            """UPDATE item SET
                 state       = 'failed',
                 fail_stage  = 'fetch',
                 fail_reason = :reason,
                 attempts    = attempts + 1,
                 fetch_v     = CASE WHEN :permanent THEN :v ELSE fetch_v END
               WHERE shortcode = :shortcode""",
            {
                "reason": reason[:200],
                "permanent": 1 if permanent else 0,
                "v": FETCH_V,
                "shortcode": shortcode,
            },
        )


def enrich(
    conn: sqlite3.Connection,
    media_dir: Path,
    *,
    limit: int | None = None,
    concurrency: int = 3,
    with_media: bool = False,
    retry_failed: bool = False,
    shortcodes: list[str] | None = None,
    delay: float = 0.4,
    on_progress: Callable[[Progress], None] | None = None,
) -> Progress:
    """Fetch every pending item, or every one of `shortcodes` that is pending.
    Safe to interrupt and re-run."""
    items = pending(conn, limit=limit, retry_failed=retry_failed, shortcodes=shortcodes)
    progress = Progress(total=len(items))
    if not items:
        return progress

    lock = threading.Lock()
    throttle = threading.Semaphore(max(1, concurrency))

    def handle(row: sqlite3.Row) -> None:
        shortcode, url = row["shortcode"], row["permalink"]
        with throttle:
            time.sleep(delay)  # be a polite client, not a scraper
            try:
                got = probe(url)
            except FetchError as exc:
                _record_failure(conn, lock, shortcode, exc.reason, permanent=exc.permanent)
                with lock:
                    progress.failed += 1
                return
            except Exception as exc:
                _record_failure(
                    conn, lock, shortcode, f"{type(exc).__name__}: {exc}", permanent=False
                )
                with lock:
                    progress.failed += 1
                return

            thumb_rel = media_rel = None
            size = None

            if got.thumbnail_url:
                rel = f"thumbs/{shard(shortcode)}/{shortcode}.jpg"
                try:
                    download_thumbnail(got.thumbnail_url, media_dir / rel)
                    thumb_rel = rel
                    with lock:
                        progress.thumbs += 1
                except FetchError:
                    pass  # metadata is still worth keeping without art

            if with_media:
                rel = f"video/{shard(shortcode)}/{shortcode}.mp4"
                try:
                    size = download_media(url, media_dir / rel)
                    media_rel = rel
                    # Instagram reports no duration, so take it from the file.
                    got.duration = probe_duration(media_dir / rel) or got.duration
                    with lock:
                        progress.media += 1
                        progress.bytes += size
                except FetchError:
                    pass  # a missing video does not undo a good fetch

            _record_success(conn, lock, shortcode, got, thumb_rel, media_rel, size)
            slides = (
                save_slides(conn, lock, media_dir, shortcode, url) if is_carousel(got.raw) else 0
            )
            with lock:
                progress.fetched += 1
                progress.slides += slides

        if on_progress:
            on_progress(progress)

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        list(pool.map(handle, items))

    return progress


def pending_media(conn: sqlite3.Connection, *, limit: int | None = None) -> list[sqlite3.Row]:
    """Fetched items that still have no video file, nor slides.

    Separate from `pending` on purpose: once an item has been fetched, it is no
    longer behind on fetch_v, so a later --media run would otherwise find
    nothing to do. Photo posts are included — they simply fail the download and
    keep everything else they already have — except carousels whose slides are
    saved, which have all there is. `carousel` marks the ones to page through.
    """
    sql = """SELECT shortcode, permalink, attempts,
                    COALESCE(json_extract(fetch_meta, '$.playlist_count'), 0) > 1
                    AND COALESCE(json_array_length(fetch_meta, '$.formats'), 0) = 0
                      AS carousel
             FROM item
             WHERE media_path IS NULL AND state != 'failed'
               AND NOT EXISTS (SELECT 1 FROM item_slide s
                               WHERE s.shortcode = item.shortcode)
             ORDER BY saved_at IS NULL, saved_at DESC"""
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


def fetch_media(
    conn: sqlite3.Connection,
    media_dir: Path,
    *,
    limit: int | None = None,
    concurrency: int = 2,
    delay: float = 0.4,
    on_progress: Callable[[Progress], None] | None = None,
) -> Progress:
    """Download the original mp4 for items that do not have one yet.

    Metadata is left alone: this only ever adds media_path, bytes and duration,
    so a failure here costs nothing that was already collected. It does clear
    ocr_v: an item read from its thumbnail alone has more to read now. A
    carousel of photos has no mp4 to download; its slides are saved instead.
    """
    items = pending_media(conn, limit=limit)
    progress = Progress(total=len(items))
    if not items:
        return progress

    lock = threading.Lock()
    throttle = threading.Semaphore(max(1, concurrency))

    def handle(row: sqlite3.Row) -> None:
        shortcode, url = row["shortcode"], row["permalink"]
        rel = f"video/{shard(shortcode)}/{shortcode}.mp4"
        with throttle:
            time.sleep(delay)
            if row["carousel"]:
                slides = save_slides(conn, lock, media_dir, shortcode, url)
                if slides:
                    with lock:
                        progress.slides += slides
                        progress.fetched += 1
                    if on_progress:
                        on_progress(progress)
                    return
            try:
                size = download_media(url, media_dir / rel)
            except (FetchError, Exception) as exc:
                # A photo post has no video. That is not a failure of the item.
                with lock:
                    progress.skipped += 1
                    del exc
                return

            seconds = probe_duration(media_dir / rel)
            with lock:
                conn.execute(
                    """UPDATE item SET media_path = ?, bytes = ?,
                              duration = COALESCE(?, duration), ocr_v = 0
                       WHERE shortcode = ?""",
                    (rel, size, seconds, shortcode),
                )
                progress.media += 1
                progress.fetched += 1
                progress.bytes += size

        if on_progress:
            on_progress(progress)

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        list(pool.map(handle, items))

    return progress


def summary(conn: sqlite3.Connection) -> dict[str, int]:
    row = conn.execute(
        """SELECT COUNT(*) AS total,
                  SUM(thumb_path IS NOT NULL) AS thumbs,
                  SUM(media_path IS NOT NULL) AS media,
                  SUM(state = 'fetched') AS fetched,
                  SUM(state = 'failed') AS failed,
                  SUM(state = 'imported') AS waiting,
                  COALESCE(SUM(bytes), 0) AS bytes
           FROM item"""
    ).fetchone()
    return {k: (row[k] or 0) for k in row.keys()}


def failures(conn: sqlite3.Connection, *, limit: int = 20) -> dict[str, Any]:
    """The reels whose fetch failed: how many, how many of those `retry` would
    offer again, and the most recently saved `limit` of them, with why.
    `permanent` ones fail the same way every time (restricted, deleted, login
    only); the rest are `retrying` while they have tries left, then wait for
    someone to ask again."""
    where = "state = 'failed' AND fail_stage = 'fetch'"
    counts = conn.execute(
        f"""SELECT COUNT(*) AS failed,
                   COALESCE(SUM(fetch_v < :v AND attempts >= :max), 0) AS retryable
            FROM item WHERE {where}""",
        {"v": FETCH_V, "max": MAX_ATTEMPTS},
    ).fetchone()
    rows = conn.execute(
        f"""SELECT shortcode, permalink, fail_reason AS reason, attempts,
                   fetch_v >= :v AS permanent,
                   fetch_v < :v AND attempts < :max AS retrying
            FROM item WHERE {where}
            ORDER BY saved_at IS NULL, saved_at DESC, shortcode
            LIMIT :limit""",
        {"v": FETCH_V, "max": MAX_ATTEMPTS, "limit": limit},
    ).fetchall()
    return {
        **dict(counts),
        "failures": [
            {**dict(r), "permanent": bool(r["permanent"]), "retrying": bool(r["retrying"])}
            for r in rows
        ],
    }


def retry(conn: sqlite3.Connection) -> int:
    """Give the fetches that ran out of tries another `MAX_ATTEMPTS`. Not the
    permanent ones: retrying them cannot help. Returns how many."""
    return conn.execute(
        """UPDATE item SET attempts = 0
           WHERE state = 'failed' AND fail_stage = 'fetch'
             AND fetch_v < :v AND attempts >= :max""",
        {"v": FETCH_V, "max": MAX_ATTEMPTS},
    ).rowcount
