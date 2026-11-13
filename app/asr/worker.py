"""The transcription worker.

Pulls every item that has a video on disk and no transcript at the current
`asr_v`, transcribes it, and commits that one row. Like enrichment, each item is
its own transaction, so a run can be killed at any point and resumed by running
it again: finished transcripts are already committed, unfinished ones were never
started as far as the database knows.

Outcomes:

    speech      transcript written, asr_v and asr_profile stamped
    no speech   the VAD found nothing; transcript stays NULL, still stamped
    no audio    the file has no audio track; same as no speech
    error       fail_stage = 'asr' with a reason, NOT stamped, retried next run

An ASR error never sets `state = 'failed'`. That state means the post itself is
unreachable, and a reel whose transcript failed is still a perfectly good reel.

A new transcript also clears `tag_v` and `recipe_v`, as new OCR text does, so a
reel tagged without its words (its transcription had failed, say) is read again
once they arrive. The same transcript again changes nothing.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from app.db import Row

from .transcriber import ASR_V, AudioError, NoAudio, Said, extract_audio

# Takes 16 kHz mono s16le PCM, returns the transcript or None for no speech:
# a Said when the model says when each part was said, else the text.
Transcribe = Callable[[bytes], "Said | str | None"]
Heard = Said | str | None


@dataclass
class Progress:
    total: int = 0
    transcribed: int = 0
    silent: int = 0
    failed: int = 0
    seconds: int = 0  # of audio, from item.duration

    def line(self) -> str:
        done = self.transcribed + self.silent + self.failed
        return (
            f"{done}/{self.total}  transcribed {self.transcribed}"
            f"  no speech {self.silent}  failed {self.failed}"
            f"  {self.seconds / 60:,.0f} min of audio"
        )


def pending(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
    force: bool = False,
    shortcodes: list[str] | None = None,
) -> list[sqlite3.Row]:
    """Items with a video and no transcript at the current asr_v, newest first.
    `shortcodes` narrows it to those."""
    clause = "" if force else "AND asr_v < :v"
    if shortcodes is not None:
        clause += " AND shortcode IN (SELECT value FROM json_each(:only))"
    sql = f"""SELECT shortcode, media_path, duration FROM item
              WHERE media_path IS NOT NULL AND state != 'failed' {clause}
              ORDER BY saved_at IS NULL, saved_at DESC"""
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, {"v": ASR_V, "only": json.dumps(shortcodes)}).fetchall()


def record(
    conn: sqlite3.Connection,
    lock: threading.Lock,
    shortcode: str,
    heard: Heard,
    profile: str,
    language: str | None = None,
) -> None:
    """`language`: what a multilingual hosted model heard (#116); None from
    the local English-only models. A Said's segments are kept, for jumping to
    the moment a search matched (#44)."""
    transcript = heard.text if isinstance(heard, Said) else heard
    segments = (
        json.dumps(heard.segments, ensure_ascii=False)
        if isinstance(heard, Said) and heard.segments
        else None
    )
    with lock:
        conn.execute(
            """UPDATE item SET
                 -- Read against the old row: re-tag only if the words changed,
                 -- as OCR does. A reel tagged while its transcript had failed
                 -- is read again once one arrives, and so is one whose
                 -- transcript is now gone: it was tagged from noise (#95).
                 tag_v       = CASE WHEN :transcript IS NOT transcript
                                    THEN 0 ELSE tag_v END,
                 recipe_v    = CASE WHEN :transcript IS NOT transcript
                                    THEN 0 ELSE recipe_v END,
                 transcript  = :transcript,
                 transcript_segments = :segments,
                 asr_v       = :v,
                 asr_profile = :profile,
                 asr_language = :language,
                 -- Only move forward: a later stage's state is not undone.
                 state       = CASE WHEN state = 'fetched' THEN 'transcribed'
                                    ELSE state END,
                 fail_stage  = CASE WHEN fail_stage = 'asr' THEN NULL ELSE fail_stage END,
                 fail_reason = CASE WHEN fail_stage = 'asr' THEN NULL ELSE fail_reason END
               WHERE shortcode = :shortcode""",
            {
                "transcript": transcript,
                "segments": segments,
                "v": ASR_V,
                "profile": profile,
                "language": language,
                "shortcode": shortcode,
            },
        )


def record_failure(
    conn: sqlite3.Connection, lock: threading.Lock, shortcode: str, reason: str
) -> None:
    with lock:
        conn.execute(
            """UPDATE item SET fail_stage = 'asr', fail_reason = :reason
               WHERE shortcode = :shortcode""",
            {"reason": reason[:200], "shortcode": shortcode},
        )


def transcribe_one(row: Row, media_dir: Path, transcribe: Transcribe) -> tuple[Heard, str | None]:
    """One reel's transcript, or None for no speech; or the reason it failed.
    Reads only the media file and `row`, and writes nothing, so a worker on
    another machine runs it on a downloaded copy (#35)."""
    try:
        return transcribe(extract_audio(media_dir / row["media_path"])), None
    except NoAudio:
        return None, None  # a silent clip, not a failure
    except AudioError as exc:
        return None, str(exc)
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def transcribe_all(
    conn: sqlite3.Connection,
    media_dir: Path,
    transcribe: Transcribe,
    *,
    profile: str,
    workers: int = 1,
    limit: int | None = None,
    force: bool = False,
    shortcodes: list[str] | None = None,
    on_progress: Callable[[Progress], None] | None = None,
) -> Progress:
    """Transcribe every pending item, or every one of `shortcodes` that is
    pending. Safe to interrupt and re-run."""
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
        text, reason = transcribe_one(row, media_dir, transcribe)
        if reason:
            record_failure(conn, lock, shortcode, reason)
            with lock:
                progress.failed += 1
            if on_progress:
                on_progress(progress)
            return

        record(conn, lock, shortcode, text, profile)
        with lock:
            if text:
                progress.transcribed += 1
            else:
                progress.silent += 1
            progress.seconds += row["duration"] or 0
        if on_progress:
            on_progress(progress)

    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    try:
        for f in [pool.submit(handle, row) for row in items]:
            f.result()
    except BaseException:
        # Ctrl-C: drop everything queued. Items already mid-transcription finish
        # and commit; nothing half-done is ever written.
        stop.set()
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown()
    return progress


def summary(conn: sqlite3.Connection) -> dict[str, int]:
    row = conn.execute(
        """SELECT SUM(media_path IS NOT NULL AND state != 'failed') AS media,
                  SUM(asr_v >= :v AND transcript IS NOT NULL) AS transcribed,
                  SUM(asr_v >= :v AND transcript IS NULL) AS silent,
                  SUM(fail_stage = 'asr') AS failed,
                  SUM(media_path IS NOT NULL AND state != 'failed'
                      AND asr_v < :v) AS waiting
           FROM item""",
        {"v": ASR_V},
    ).fetchone()
    return {k: (row[k] or 0) for k in row.keys()}
