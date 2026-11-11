"""The recipe worker.

Pulls every item behind the current `recipe_v`, decides whether it is a recipe
and if so extracts it, and commits that one item in a single transaction: its
`source='model'` recipe, its search index row and the `recipe_v` /
`recipe_profile` stamp. A run can be killed at any point and resumed.

Outcomes:

    recipe      a model recipe written, stamped
    not one     not a candidate, or the model said no; any old model recipe
                removed, stamped, and not asked again
    error       fail_stage = 'recipe' with a reason, NOT stamped, retried next run

A reel waits for its transcript, its on-screen text and the tagger, whose Food
tag is half the candidate filter, so it is read once with everything.

Only `source='model'` rows are ever replaced. Your corrections survive.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from app.asr.transcriber import ASR_V
from app.db import Row
from app.ocr.reader import OCR_V
from app.tagging.llm import TAG_V
from app.tagging.worker import WAITING

from . import llm, service
from .detect import FOOD, is_candidate


@dataclass
class Progress:
    total: int = 0
    recipes: int = 0
    not_recipes: int = 0
    skipped: int = 0  # not candidates: no model request was made
    failed: int = 0

    def line(self) -> str:
        done = self.recipes + self.not_recipes + self.skipped + self.failed
        return (
            f"{done}/{self.total}  recipes {self.recipes}"
            f"  not recipes {self.not_recipes + self.skipped}  failed {self.failed}"
        )


_WAITING_FOR_TAG = "tag_v < :tag AND fail_stage IS NOT 'tag'"
_PARAMS = {"v": llm.RECIPE_V, "asr": ASR_V, "ocr": OCR_V, "tag": TAG_V}


def pending(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
    force: bool = False,
    shortcodes: list[str] | None = None,
) -> list[sqlite3.Row]:
    """Items behind the current recipe_v, newest first. `shortcodes` narrows
    it to those."""
    clause = "" if force else "AND recipe_v < :v"
    if shortcodes is not None:
        clause += " AND shortcode IN (SELECT value FROM json_each(:only))"
    sql = f"""SELECT shortcode, caption, hashtags, transcript, ocr_text, primary_tag
              FROM item
              WHERE state != 'failed' AND NOT {WAITING}
                AND NOT ({_WAITING_FOR_TAG}) {clause}
              ORDER BY saved_at IS NULL, saved_at DESC"""
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, {**_PARAMS, "only": json.dumps(shortcodes)}).fetchall()


def record(conn: sqlite3.Connection, shortcode: str, found: llm.Found | None, profile: str) -> None:
    conn.execute("BEGIN")
    try:
        service.set_model(conn, shortcode, found)
        conn.execute(
            """UPDATE item SET
                 recipe_v       = :v,
                 recipe_profile = :profile,
                 fail_stage  = CASE WHEN fail_stage = 'recipe' THEN NULL ELSE fail_stage END,
                 fail_reason = CASE WHEN fail_stage = 'recipe' THEN NULL ELSE fail_reason END
               WHERE shortcode = :shortcode""",
            {"v": llm.RECIPE_V, "profile": profile, "shortcode": shortcode},
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def record_failure(conn: sqlite3.Connection, shortcode: str, reason: str) -> None:
    conn.execute(
        """UPDATE item SET fail_stage = 'recipe', fail_reason = :reason
           WHERE shortcode = :shortcode""",
        {"reason": reason[:200], "shortcode": shortcode},
    )


def candidate(row: Row) -> bool:
    """Whether the item is worth asking the model about at all."""
    return is_candidate(row["primary_tag"], row["caption"], row["transcript"], row["ocr_text"])


def read_one(row: Row, model: llm.Model) -> llm.Found | None:
    """One candidate's recipe, or None, from `row` alone; writes nothing, so a
    worker on another machine runs it (#35). Raises llm.RecipeError, or
    OllamaError when the server cannot be used."""
    return llm.read(
        model,
        row["caption"],
        json.loads(row["hashtags"] or "[]"),
        row["transcript"],
        row["ocr_text"],
        food=row["primary_tag"] == FOOD,
    )


def read_all(
    conn: sqlite3.Connection,
    model: llm.Model,
    *,
    profile: str,
    limit: int | None = None,
    force: bool = False,
    shortcodes: list[str] | None = None,
    on_progress: Callable[[Progress], None] | None = None,
) -> Progress:
    """Run the recipe pass over every pending item, or every one of
    `shortcodes` that is pending. Safe to interrupt and re-run.

    Raises ollama.OllamaError, having written nothing for the item in hand, if
    the server cannot be used.
    """
    items = pending(conn, limit=limit, force=force, shortcodes=shortcodes)
    progress = Progress(total=len(items))

    for row in items:
        shortcode = row["shortcode"]
        if not candidate(row):
            record(conn, shortcode, None, profile)
            progress.skipped += 1
        else:
            try:
                found = read_one(row, model)
            except llm.RecipeError as exc:
                record_failure(conn, shortcode, str(exc))
                progress.failed += 1
            else:
                record(conn, shortcode, found, profile)
                if found:
                    progress.recipes += 1
                else:
                    progress.not_recipes += 1
        if on_progress:
            on_progress(progress)
    return progress


def summary(conn: sqlite3.Connection) -> dict[str, int]:
    row = conn.execute(
        f"""SELECT SUM(recipe_v >= :v) AS done,
                   SUM(fail_stage = 'recipe') AS failed,
                   SUM(state != 'failed' AND recipe_v < :v
                       AND ({WAITING} OR ({_WAITING_FOR_TAG}))) AS waiting_upstream,
                   SUM(state != 'failed' AND recipe_v < :v AND NOT {WAITING}
                       AND NOT ({_WAITING_FOR_TAG})) AS waiting
            FROM item""",
        _PARAMS,
    ).fetchone()
    out = {k: (row[k] or 0) for k in row.keys()}
    # Across everyone: the model's recipes, and how many corrections people made.
    counts = conn.execute(
        """SELECT SUM(source = 'model') AS recipes,
                  SUM(source = 'model' AND incomplete) AS incomplete,
                  SUM(source = 'user') AS edited
           FROM recipe WHERE hidden = 0"""
    ).fetchone()
    out.update({k: (counts[k] or 0) for k in counts.keys()})
    return out
