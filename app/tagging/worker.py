"""The LLM tagging worker.

Pulls every item behind the current `tag_v`, asks the model about it, and
commits that one item in a single transaction: its `source='model'` category,
its summary and keywords, and the `tag_v` / `tag_profile` stamp. A run can be killed
at any point and resumed by running it again.

Outcomes:

    tagged      one taxonomy category, with a confidence, stamped; and for a
                gaming reel, the game it shows or a guess at it (games.py)
    untagged    the model found no category that clearly fits; still stamped,
                with its summary and keywords, and not asked again
    error       fail_stage = 'tag' with a reason, NOT stamped, retried next run

A reel whose transcript or on-screen text is still to come waits for it, so it
is read once with everything rather than twice. One whose transcription or OCR
failed does not wait.

Only the `source='model'` row is ever replaced. The hashtag pass's answer and
yours survive, and yours still wins.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from app.asr.transcriber import ASR_V
from app.db import Row
from app.ocr.reader import OCR_V
from app.ocr.worker import NEEDS_OCR

from . import games, llm
from .service import GAME, MODEL, ensure_taxonomy, set_tag


@dataclass
class Progress:
    total: int = 0
    tagged: int = 0
    untagged: int = 0
    failed: int = 0
    games: int = 0  # sure of the video game it shows
    guesses: int = 0  # a game to ask its savers about

    def line(self) -> str:
        done = self.tagged + self.untagged + self.failed
        line = (
            f"{done}/{self.total}  tagged {self.tagged}"
            f"  untagged {self.untagged}  failed {self.failed}"
        )
        if self.games or self.guesses:
            line += f"  games {self.games} (+{self.guesses} to confirm)"
        return line


_WAITING_FOR_ASR = """media_path IS NOT NULL AND asr_v < :asr
                      AND fail_stage IS NOT 'asr'"""
_WAITING_FOR_OCR = f"""{NEEDS_OCR} AND ocr_v < :ocr
                       AND fail_stage IS NOT 'ocr'"""
WAITING = f"(({_WAITING_FOR_ASR}) OR ({_WAITING_FOR_OCR}))"


def pending(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
    force: bool = False,
    shortcodes: list[str] | None = None,
) -> list[sqlite3.Row]:
    """Items behind the current tag_v, newest first. `shortcodes` narrows it
    to those."""
    clause = "" if force else "AND tag_v < :v"
    if shortcodes is not None:
        clause += " AND shortcode IN (SELECT value FROM json_each(:only))"
    sql = f"""SELECT shortcode, caption, hashtags, transcript, ocr_text FROM item
              WHERE state != 'failed' AND NOT {WAITING} {clause}
              ORDER BY saved_at IS NULL, saved_at DESC"""
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(
        sql, {"v": llm.TAG_V, "asr": ASR_V, "ocr": OCR_V, "only": json.dumps(shortcodes)}
    ).fetchall()


def decide(conn: sqlite3.Connection, shortcode: str, answer: games.Answer | None) -> games.Decision:
    """Whether to apply the game the model named, from the reel's own text as
    the server has it, not as a remote worker says it was."""
    row = conn.execute(
        "SELECT caption, hashtags, transcript, ocr_text FROM item WHERE shortcode = ?",
        (shortcode,),
    ).fetchone()
    if row is None:
        return games.NO_GAME
    return games.decide(
        answer,
        row["caption"],
        json.loads(row["hashtags"] or "[]"),
        row["transcript"],
        row["ocr_text"],
    )


def record(
    conn: sqlite3.Connection, shortcode: str, result: llm.Result, profile: str
) -> games.Decision:
    """Write one reel's answer in a single transaction. Returns what was
    decided about its game."""
    conn.execute("BEGIN")
    try:
        set_tag(conn, shortcode, result.category, MODEL, confidence=result.confidence)
        game = decide(conn, shortcode, result.game)
        # A sure game on a reel filed under something else is a gaming reel.
        shown = conn.execute(
            """SELECT tag.name FROM item_tag JOIN tag ON tag.id = item_tag.tag_id
               WHERE shortcode = ? AND source != ?
               ORDER BY source = 'model' DESC LIMIT 1""",
            (shortcode, GAME),
        ).fetchone()
        filed = shown[0] if shown else None
        set_tag(
            conn,
            shortcode,
            games.GAMING if game.sure and filed not in games.CATEGORIES else None,
            GAME,
        )
        conn.execute(
            """UPDATE item SET
                 game        = :game,
                 game_guess  = :guess,
                 summary     = :summary,
                 keywords    = :keywords,
                 tag_v       = :v,
                 tag_profile = :profile,
                 -- Only move forward: a later stage's state is not undone.
                 state       = CASE WHEN state IN ('fetched', 'transcribed')
                                    THEN 'tagged' ELSE state END,
                 fail_stage  = CASE WHEN fail_stage = 'tag' THEN NULL ELSE fail_stage END,
                 fail_reason = CASE WHEN fail_stage = 'tag' THEN NULL ELSE fail_reason END
               WHERE shortcode = :shortcode""",
            {
                "game": game.title if game.sure else None,
                "guess": game.guess,
                "summary": result.summary,
                "keywords": json.dumps(result.keywords) if result.keywords else None,
                "v": llm.TAG_V,
                "profile": profile,
                "shortcode": shortcode,
            },
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return game


def record_failure(conn: sqlite3.Connection, shortcode: str, reason: str) -> None:
    conn.execute(
        """UPDATE item SET fail_stage = 'tag', fail_reason = :reason
           WHERE shortcode = :shortcode""",
        {"reason": reason[:200], "shortcode": shortcode},
    )


def tag_one(row: Row, generate: llm.Generate) -> llm.Result:
    """One item's tag, summary and keywords, and what the model said of its
    game when it is a gaming reel, from `row` alone; writes nothing,
    so a worker on another machine runs it (#35). Raises llm.TagError, or
    OllamaError when the server cannot be used."""
    return llm.tag(
        generate,
        row["caption"],
        json.loads(row["hashtags"] or "[]"),
        row["transcript"],
        row["ocr_text"],
    )


def tag_all(
    conn: sqlite3.Connection,
    generate: llm.Generate,
    *,
    profile: str,
    limit: int | None = None,
    force: bool = False,
    shortcodes: list[str] | None = None,
    on_progress: Callable[[Progress], None] | None = None,
) -> Progress:
    """Tag every pending item, or every one of `shortcodes` that is pending.
    Safe to interrupt and re-run.

    One at a time: Ollama queues concurrent requests for a model anyway, and on
    CPU they would only split the same cores. Raises llm.OllamaError, having
    written nothing for the item in hand, if the server cannot be used.
    """
    ensure_taxonomy(conn)
    items = pending(conn, limit=limit, force=force, shortcodes=shortcodes)
    progress = Progress(total=len(items))

    for row in items:
        shortcode = row["shortcode"]
        try:
            result = tag_one(row, generate)
        except llm.TagError as exc:
            record_failure(conn, shortcode, str(exc))
            progress.failed += 1
        else:
            game = record(conn, shortcode, result, profile)
            if result.category or game.sure:
                progress.tagged += 1
            else:
                progress.untagged += 1
            progress.games += game.sure
            progress.guesses += game.guess is not None
        if on_progress:
            on_progress(progress)
    return progress


def summary(conn: sqlite3.Connection) -> dict[str, int]:
    row = conn.execute(
        f"""SELECT SUM(tag_v >= :v) AS done,
                   -- From any source: the number this tagger exists to shrink.
                   SUM(state != 'failed' AND primary_tag IS NULL) AS untagged,
                   SUM(fail_stage = 'tag') AS failed,
                   SUM(state != 'failed' AND tag_v < :v
                       AND {_WAITING_FOR_ASR}) AS waiting_asr,
                   SUM(state != 'failed' AND tag_v < :v
                       AND {_WAITING_FOR_OCR}) AS waiting_ocr,
                   SUM(state != 'failed' AND tag_v < :v
                       AND NOT {WAITING}) AS waiting
            FROM item""",
        {"v": llm.TAG_V, "asr": ASR_V, "ocr": OCR_V},
    ).fetchone()
    return {k: (row[k] or 0) for k in row.keys()}
