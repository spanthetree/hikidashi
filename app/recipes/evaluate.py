"""Measuring the recipe pass against reels you have labelled by hand.

Labels are `{shortcode: true | false}` in `data/recipe_labels.json`, next to
the database, and never in git: they come with your captions. `python -m
app.recipes --label` walks the reels worth labelling (everything tagged Food or
with recipe signals) and asks about each.

The report reads what the pass stored, so it measures the pipeline as it ran,
candidate filter included, without asking the model anything. It also checks
every stored ingredient against the reel's text again: grounding should have
dropped any the text does not name, so that count is meant to be zero.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .detect import FOOD, signals
from .llm import RECIPE_V, Sources


def load(path: Path) -> dict[str, bool]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return {k: bool(v) for k, v in data.items()}


def save(path: Path, labels: dict[str, bool]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(dict(sorted(labels.items())), indent=1), encoding="utf-8")
    tmp.replace(path)


def to_label(conn: sqlite3.Connection, labels: dict[str, bool]) -> list[sqlite3.Row]:
    """Food reels and reels with recipe signals that have no label yet."""
    rows = conn.execute(
        """SELECT shortcode, permalink, author, caption, transcript, ocr_text,
                  primary_tag = ? AS food
           FROM item WHERE state != 'failed'
           ORDER BY saved_at IS NULL, saved_at DESC""",
        (FOOD,),
    ).fetchall()
    return [
        r
        for r in rows
        if r["shortcode"] not in labels
        and (r["food"] or signals(r["caption"], r["transcript"], r["ocr_text"]))
    ]


@dataclass
class Report:
    labelled: int = 0
    unread: int = 0  # labelled, but the pass has not run on it
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    false_pos: list[str] = field(default_factory=list)
    false_neg: list[str] = field(default_factory=list)
    ingredients: int = 0
    ungrounded: list[tuple[str, str]] = field(default_factory=list)
    dropped: int = 0

    @property
    def precision(self) -> float | None:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else None

    @property
    def recall(self) -> float | None:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else None


def evaluate(conn: sqlite3.Connection, labels: dict[str, bool]) -> Report:
    """How the model's output compares with your labels. Your own corrections
    are not counted: this measures the model."""
    report = Report()
    for code, truth in sorted(labels.items()):
        item = conn.execute(
            "SELECT caption, transcript, ocr_text, recipe_v FROM item WHERE shortcode = ?", (code,)
        ).fetchone()
        if item is None:
            continue
        report.labelled += 1
        if item["recipe_v"] < RECIPE_V:
            report.unread += 1
            continue
        recipe = conn.execute(
            "SELECT dropped FROM recipe WHERE shortcode = ? AND source = 'model'", (code,)
        ).fetchone()
        said = recipe is not None
        if said and truth:
            report.tp += 1
        elif said:
            report.fp += 1
            report.false_pos.append(code)
        elif truth:
            report.fn += 1
            report.false_neg.append(code)
        else:
            report.tn += 1
        if not said:
            continue

        report.dropped += len(json.loads(recipe["dropped"] or "[]"))
        sources = Sources(
            caption=item["caption"], transcript=item["transcript"], ocr=item["ocr_text"]
        )
        for ing in conn.execute(
            """SELECT item, as_written FROM recipe_ingredient
                   WHERE shortcode = ? AND source = 'model'""",
            (code,),
        ):
            report.ingredients += 1
            if not (sources.where(ing["as_written"]) or sources.where(ing["item"])):
                report.ungrounded.append((code, ing["item"]))
    return report
