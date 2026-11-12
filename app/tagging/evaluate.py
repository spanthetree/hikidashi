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

Labels can also come from a file, {shortcode: tag}, where null says no
category fits: a source that answers one of those is wrong, and one that
leaves it blank is right ("abstained"). tests/tag_labels.json labels the demo
library that way, for CI (#57); the detail sheet cannot say "none".

`pick` chooses reels worth labelling by hand: some of every tag, and the
awkward ones (no hashtags, a photo post, a caption not in English, the
sources disagreeing, nothing shown).

Read only. Nothing here asks the model anything.
"""

from __future__ import annotations

import collections
import json
import random
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SOURCES = ("model", "hashtag", "shown")


NONE = "none"  # how a "no category fits" label is shown


@dataclass
class Score:
    labelled: int = 0  # reels labelled with a tag
    answered: int = 0  # of those, how many this source gave a tag for
    exact: int = 0
    hit: int = 0
    unfit: int = 0  # reels labelled "no category fits"
    abstained: int = 0  # of those, how many this source left blank
    misses: list[tuple[str, str, str]] = field(default_factory=list)  # code, yours, its

    @property
    def precision(self) -> float | None:
        """Right answers over every answer, including those where none fits."""
        given = self.answered + self.unfit - self.abstained
        return self.exact / given if given else None

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


def labels_from(path: Path) -> dict[str, str | None]:
    """{shortcode: tag, or None where no category fits}, from a file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, str | None] = data["labels"]
    return out


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
    conn: sqlite3.Connection,
    predictions: sqlite3.Connection | None = None,
    truth: dict[str, str | None] | None = None,
) -> dict[str, Score]:
    """Score each source on your labels in `conn` (or on `truth`), reading its
    answers from `predictions`, which defaults to the same database."""
    gold: dict[str, str | None] = dict(labels(conn)) if truth is None else truth
    said = answers(predictions or conn)
    tagged = len([t for t in gold.values() if t is not None])
    scores = {s: Score(labelled=tagged, unfit=len(gold) - tagged) for s in SOURCES}
    for code, yours in sorted(gold.items(), key=lambda kv: kv[0]):
        for source, score in scores.items():
            its = said.get(code, {}).get(source) or set()
            if yours is None:
                if its:
                    score.misses.append((code, NONE, ", ".join(sorted(its))))
                else:
                    score.abstained += 1
                continue
            if not its:
                continue
            score.answered += 1
            score.exact += its == {yours}
            score.hit += yours in its
            if its != {yours}:
                score.misses.append((code, yours, ", ".join(sorted(its))))
    return scores


def summary(scores: dict[str, Score]) -> dict[str, Any]:
    """The scores as numbers, for scripts/evals.py."""
    return {
        source: {
            "labelled": sc.labelled,
            "unfit": sc.unfit,
            "coverage": sc.coverage,
            "precision": sc.precision,
            "abstained": sc.abstained,
        }
        for source, sc in scores.items()
    }


def pick(conn: sqlite3.Connection, n: int = 50, seed: int = 6) -> list[tuple[str, str, str]]:
    """About `n` reels worth labelling, as (shortcode, shown tag, why): the
    awkward ones first, then the rest spread over what each reel shows.
    Reels you have already labelled are left out."""
    said = answers(conn)
    done = set(labels(conn))
    rows = conn.execute(
        # A photo post's permalink is /p/, a reel's /reel/.
        """SELECT shortcode, clean_hashtags, caption, permalink LIKE '%/p/%'
           FROM item WHERE state != 'failed' ORDER BY shortcode"""
    ).fetchall()
    rng = random.Random(seed)
    awkward: dict[str, list[str]] = collections.defaultdict(list)
    by_tag: dict[str, list[str]] = collections.defaultdict(list)
    shown: dict[str, str] = {}
    for code, hashtags, caption, still in rows:
        if code in done:
            continue
        by_source = said.get(code, {})
        shown[code] = ", ".join(sorted(by_source.get("shown") or ())) or NONE
        model, hashtag = by_source.get("model"), by_source.get("hashtag")
        caption = caption or ""
        letters = [c for c in caption if c.isalpha()]
        if not (hashtags or "").strip(" []"):
            awkward["no hashtags"].append(code)
        if still:
            awkward["a photo post"].append(code)
        if letters and sum(not c.isascii() for c in letters) > len(letters) / 3:
            awkward["not in English"].append(code)
        if model and hashtag and model != hashtag:
            awkward["model and hashtags disagree"].append(code)
        if shown[code] == NONE:
            awkward["nothing shown"].append(code)
        by_tag[shown[code]].append(code)

    chosen: dict[str, str] = {}
    for why, codes in sorted(awkward.items()):
        for code in rng.sample(codes, min(len(codes), max(1, n // 10))):
            if len(chosen) < n:
                chosen.setdefault(code, why)
    # Then round-robin over the tags, so a rare one is not crowded out.
    pools = {tag: rng.sample(codes, len(codes)) for tag, codes in sorted(by_tag.items())}
    while len(chosen) < n and any(pools.values()):
        for tag, codes in pools.items():
            while codes and codes[-1] in chosen:
                codes.pop()
            if codes and len(chosen) < n:
                chosen[codes.pop()] = f"shows {tag}"
    return [(code, shown[code], why) for code, why in chosen.items()]


def _pct(x: float | None) -> str:
    return "   —" if x is None else f"{x:4.0%}"


def report(scores: dict[str, Score]) -> str:
    n = scores["shown"].labelled
    unfit = scores["shown"].unfit
    if not n and not unfit:
        return (
            "  no labels yet: set a reel's tag yourself in its detail sheet,\n"
            "  and it counts as one (#6)"
        )
    lines = [
        f"  {n} reels tagged by hand"
        + (f", {unfit} where no category fits" if unfit else "")
        + "\n",
        "  source    answered  precision  hit" + ("   abstained" if unfit else ""),
        *(
            f"  {s:<8}  {_pct(sc.coverage):>8}  {_pct(sc.precision):>9}  "
            f"{_pct(sc.hit / sc.answered if sc.answered else None)}"
            + (f"   {sc.abstained:>4}/{sc.unfit}" if unfit else "")
            for s, sc in scores.items()
        ),
    ]
    misses = scores["shown"].misses
    if misses:
        lines.append(f"\n  shown, but not yours ({len(misses)})")
        lines += [f"    {code}  yours {yours}, shown {its}" for code, yours, its in misses]
    return "\n".join(lines)
