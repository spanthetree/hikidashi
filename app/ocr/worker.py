"""The OCR worker.

Reads the items that have no other source of words: photo posts, which have no
audio, and reels whose transcription found no speech. A reel with speech is
not read; neither is a reel whose transcript is still to come, since that is
not yet known.

Like the other stages, each item is its own transaction, so a run can be killed
at any point and resumed by running it again.

Outcomes:

    text        ocr_text written, ocr_v stamped
    no text     nothing legible; ocr_text stays NULL, still stamped
    error       fail_stage = 'ocr' with a reason, NOT stamped, retried next run

New text also clears `tag_v` and `recipe_v`, so the tagger and the recipe pass
read the item again with it. Its other signals have not changed, so only items
OCR actually added to are read again.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from app.asr.transcriber import ASR_V
from app.db import Row

from .reader import OCR_V, OcrError, read_image, read_video

# An item OCR is for: nothing else on it will ever produce words. A video
# counts once its transcription has finished and found none.
NEEDS_OCR = """state != 'failed' AND transcript IS NULL
               AND ((media_path IS NOT NULL AND asr_v >= :asr)
                    OR (media_path IS NULL AND thumb_path IS NOT NULL))"""


@dataclass
class Progress:
    total: int = 0
    text: int = 0
    empty: int = 0
    failed: int = 0
    lines: int = 0

    def line(self) -> str:
        done = self.text + self.empty + self.failed
        return (
            f"{done}/{self.total}  with text {self.text}"
            f"  no text {self.empty}  failed {self.failed}"
        )


def pending(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
    force: bool = False,
    shortcodes: list[str] | None = None,
) -> list[sqlite3.Row]:
    """Items OCR is for, behind the current ocr_v, newest first. `shortcodes`
    narrows it to those."""
    clause = "" if force else "AND ocr_v < :v"
    if shortcodes is not None:
        clause += " AND shortcode IN (SELECT value FROM json_each(:only))"
    sql = f"""SELECT shortcode, media_path, thumb_path, duration FROM item
              WHERE {NEEDS_OCR} {clause}
              ORDER BY saved_at IS NULL, saved_at DESC"""
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, {"v": OCR_V, "asr": ASR_V, "only": json.dumps(shortcodes)}).fetchall()


def record(
    conn: sqlite3.Connection,
    lock: threading.Lock,
    shortcode: str,
    text: str | None,
    profile: str | None = None,
) -> None:
    """`profile`: the hosted provider:model that read it; None for tesseract."""
    with lock:
        conn.execute(
            """UPDATE item SET
                 -- Read against the old row: re-tag only if the text is new.
                 tag_v       = CASE WHEN :text IS NOT NULL AND :text IS NOT ocr_text
                                    THEN 0 ELSE tag_v END,
                 recipe_v    = CASE WHEN :text IS NOT NULL AND :text IS NOT ocr_text
                                    THEN 0 ELSE recipe_v END,
                 ocr_text    = :text,
                 ocr_v       = :v,
                 ocr_profile = :profile,
                 fail_stage  = CASE WHEN fail_stage = 'ocr' THEN NULL ELSE fail_stage END,
                 fail_reason = CASE WHEN fail_stage = 'ocr' THEN NULL ELSE fail_reason END
               WHERE shortcode = :shortcode""",
            {"text": text, "v": OCR_V, "profile": profile, "shortcode": shortcode},
        )


def record_failure(
    conn: sqlite3.Connection, lock: threading.Lock, shortcode: str, reason: str
) -> None:
    with lock:
        conn.execute(
            """UPDATE item SET fail_stage = 'ocr', fail_reason = :reason
               WHERE shortcode = :shortcode""",
            {"reason": reason[:200], "shortcode": shortcode},
        )


def read(row: Row, media_dir: Path) -> str | None:
    """One item's text, or None. Reads only the media files and `row`, and
    writes nothing, so a worker on another machine runs it on downloaded
    copies (#35). Raises OcrError, or OcrUnavailable with no tesseract."""
    thumb = media_dir / row["thumb_path"] if row["thumb_path"] else None
    if row["media_path"]:
        return read_video(media_dir / row["media_path"], row["duration"], thumb)
    return read_image(thumb) if thumb else None


def ocr_all(
    conn: sqlite3.Connection,
    media_dir: Path,
    *,
    workers: int = 1,
    limit: int | None = None,
    force: bool = False,
    reader: Callable[[Row, Path], str | None] = read,
    shortcodes: list[str] | None = None,
    on_progress: Callable[[Progress], None] | None = None,
) -> Progress:
    """Read every pending item, or every one of `shortcodes` that is pending.
    Safe to interrupt and re-run.

    Raises reader.OcrUnavailable, having written nothing for the item in hand,
    if tesseract or ffmpeg cannot be run.
    """
    items = pending(conn, limit=limit, force=force, shortcodes=shortcodes)
    progress = Progress(total=len(items))
    if not items:
        return progress

    lock = threading.Lock()
    stop = threading.Event()

    def handle(row: sqlite3.Row) -> None:
        if stop.is_set():
            return
        shortcode = row["shortcode"]
        try:
            text = reader(row, media_dir)
        except OcrError as exc:
            record_failure(conn, lock, shortcode, str(exc))
            with lock:
                progress.failed += 1
        else:
            record(conn, lock, shortcode, text)
            with lock:
                if text:
                    progress.text += 1
                    progress.lines += text.count("\n") + 1
                else:
                    progress.empty += 1
        if on_progress:
            on_progress(progress)

    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    try:
        for f in [pool.submit(handle, row) for row in items]:
            f.result()
    except BaseException:
        # Ctrl-C, or tesseract missing: drop everything queued.
        stop.set()
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown()
    return progress


def summary(conn: sqlite3.Connection) -> dict[str, int]:
    row = conn.execute(
        f"""SELECT SUM({NEEDS_OCR}) AS needed,
                   SUM({NEEDS_OCR} AND ocr_v >= :v AND ocr_text IS NOT NULL) AS text,
                   SUM({NEEDS_OCR} AND ocr_v >= :v AND ocr_text IS NULL) AS empty,
                   SUM(fail_stage = 'ocr') AS failed,
                   SUM({NEEDS_OCR} AND ocr_v < :v) AS waiting
            FROM item""",
        {"v": OCR_V, "asr": ASR_V},
    ).fetchone()
    return {k: (row[k] or 0) for k in row.keys()}
