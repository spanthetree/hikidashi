"""Measuring the taggers against the tags you set by hand (#6).

Your labels are the tags chosen by hand (user_item.tag_id): setting a reel's
tag in the detail sheet is labelling it, and no pass ever overwrites one. Each automatic source
is scored on the labelled reels:

    answered   how many it gave any category for
    exact      its answer was exactly yours
    hit        yours was among its answers

Since #21 a source gives at most one answer, so exact and hit agree. They
differ when scoring a database from before it, where the model could give
two: pass that backup as `predictions` to compare old against new on the same
labels. "shown" is what the reel would show without your tag: the model's
answer, else the hashtags'.

Read only. Nothing here asks the model anything.
"""

from __future__ import annotations

import collections
import sqlite3
from dataclasses import dataclass, field

SOURCES = ("model", "hashtag", "shown")


@dataclass
class Score:
    labelled: int = 0
    answered: int = 0
    exact: int = 0
    hit: int = 0
    misses: list[tuple[str, str, str]] = field(default_factory=list)  # code, yours, its

    @property
    def precision(self) -> float | None:
        return self.exact / self.answered if self.answered else None

    @property
    def coverage(self) -> float | None:
        return self.answered / self.labelled if self.labelled else None


def labels(conn: sqlite3.Connection) -> dict[str, str]:
    """{shortcode: the tag you chose}. Where two people disagree, the earlier
    account's label is the one used."""
    return dict(
        conn.execute(
            """SELECT user_item.shortcode, tag.name FROM user_item
           JOIN tag ON tag.id = user_item.tag_id ORDER BY user_item.user_id DESC"""
        )
    )


def answers(conn: sqlite3.Connection) -> dict[str, dict[str, set[str]]]:
    """{shortcode: {source: names}}, from a database of either shape."""
    out: dict[str, dict[str, set[str]]] = collections.defaultdict(
        lambda: collections.defaultdict(set)
    )
    for code, name, source in conn.execute(
        """SELECT item_tag.shortcode, tag.name, item_tag.source FROM item_tag
               JOIN tag ON tag.id = item_tag.tag_id
               WHERE item_tag.source IN ('model', 'hashtag')"""
    ):
        out[code][source].add(name)
    for by_source in out.values():
        by_source["shown"] = by_source.get("model") or by_source.get("hashtag") or set()
    return out


def evaluate(
    conn: sqlite3.Connection, predictions: sqlite3.Connection | None = None
) -> dict[str, Score]:
    """Score each source on your labels in `conn`, reading its answers from
    `predictions`, which defaults to the same database."""
    truth = labels(conn)
    said = answers(predictions or conn)
    scores = {s: Score(labelled=len(truth)) for s in SOURCES}
    for code, yours in sorted(truth.items()):
        for source, score in scores.items():
            its = said.get(code, {}).get(source) or set()
            if not its:
                continue
            score.answered += 1
            score.exact += its == {yours}
            score.hit += yours in its
            if its != {yours}:
                score.misses.append((code, yours, ", ".join(sorted(its))))
    return scores


def _pct(x: float | None) -> str:
    return "   —" if x is None else f"{x:4.0%}"


def report(scores: dict[str, Score]) -> str:
    n = scores["shown"].labelled
    if not n:
        return (
            "  no labels yet: set a reel's tag yourself in its detail sheet,\n"
            "  and it counts as one (#6)"
        )
    lines = [
        f"  {n} reels tagged by hand\n",
        "  source    answered  precision  hit",
        *(
            f"  {s:<8}  {_pct(sc.coverage):>8}  {_pct(sc.precision):>9}  "
            f"{_pct(sc.hit / sc.answered if sc.answered else None)}"
            for s, sc in scores.items()
        ),
    ]
    misses = scores["shown"].misses
    if misses:
        lines.append(f"\n  shown, but not yours ({len(misses)})")
        lines += [f"    {code}  yours {yours}, shown {its}" for code, yours, its in misses]
    return "\n".join(lines)
