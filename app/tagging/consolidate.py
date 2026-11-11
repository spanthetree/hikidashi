"""The one-time move from many tags per item to one per source (#21).

Before, an item could carry up to five tags from three sources, and accepted
proposals (`texas`, `birria`, `corolla`) sat in the filter list beside `Food`
and `Travel`. After, each source holds at most one category per item, and
item.primary_tag is the one the item shows: yours, else the model's, else the
hashtag pass's.

Run once, by app/db.py, when it finds item_tag still keyed by tag. Nothing is
asked of the model, so the model's single choice here is a best guess from the
two it gave; bumping TAG_V has the tagger answer properly on its next run.

    user rows      kept. If you had several, one becomes the primary (a
                   category, preferring one another source agrees with) and the
                   rest become keywords
    model rows     one category kept, preferring one the hashtags agree with;
                   any accepted proposal becomes a keyword
    hashtag rows   recomputed by the new rules, with clean_hashtags beside them
    tags           only categories and names you gave a reel remain
"""

from __future__ import annotations

import collections
import json
import sqlite3
from collections.abc import Set as AbstractSet

from .service import HASHTAG, HASHTAG_V, MODEL, USER, ensure_taxonomy
from .taxonomy import NARROWER, classify, clean, names, primary


def _pick(candidates: AbstractSet[str], agree: AbstractSet[str | None]) -> str | None:
    """One of these, preferring one that agrees, then the narrower, then by name."""
    if not candidates:
        return None
    return sorted(candidates, key=lambda n: (n not in agree, n not in NARROWER, n))[0]


def consolidate(conn: sqlite3.Connection) -> dict[str, int]:
    """Rewrite item_tag one row per source. Call inside a transaction."""
    categories = set(names())
    old: dict[str, dict[str, list[tuple[str, float | None]]]] = collections.defaultdict(
        lambda: collections.defaultdict(list)
    )
    for r in conn.execute(
        """SELECT item_tag.shortcode, tag.name, item_tag.source, item_tag.confidence
               FROM item_tag JOIN tag ON tag.id = item_tag.tag_id"""
    ):
        old[r[0]][r[2]].append((r[1], r[3]))

    conn.execute("ALTER TABLE item_tag RENAME TO item_tag_old")
    conn.execute(
        """CREATE TABLE item_tag (
             shortcode  TEXT NOT NULL REFERENCES item(shortcode) ON DELETE CASCADE,
             tag_id     INTEGER NOT NULL REFERENCES tag(id) ON DELETE CASCADE,
             source     TEXT NOT NULL,
             confidence REAL,
             PRIMARY KEY (shortcode, source))"""
    )
    conn.execute("DROP TABLE item_tag_old")
    ensure_taxonomy(conn)

    def tag_id(name: str) -> int:
        conn.execute("INSERT OR IGNORE INTO tag (name, kind) VALUES (?, 'user')", (name,))
        return int(conn.execute("SELECT id FROM tag WHERE name = ?", (name,)).fetchone()[0])

    stats: collections.Counter[str] = collections.Counter()
    for r in conn.execute("SELECT shortcode, hashtags, keywords FROM item").fetchall():
        code = r["shortcode"]
        hashtags = json.loads(r["hashtags"] or "[]")
        keywords = json.loads(r["keywords"] or "[]")
        rows = old.get(code, {})
        hinted = classify(hashtags)
        from_hashtags = primary(hashtags)

        model_names = {n for n, _ in rows.get(MODEL, [])}
        model = _pick(model_names & categories, hinted)
        confidence = next((c for n, c in rows.get(MODEL, []) if n == model), None)

        user_names = {n for n, _ in rows.get(USER, [])}
        user = _pick(user_names & categories, hinted | {model}) or _pick(user_names, set())

        chosen = {user, model, from_hashtags}
        # What does not survive as a category stays findable as a keyword:
        # accepted proposals from any source, and any extra tags of yours.
        demoted = {
            n for src in rows.values() for n, _ in src if n not in categories and n not in chosen
        }
        # Compared as written, not normalised: "bucketlist" is one search
        # token and "bucket list" two, so having one does not find the other.
        for word in sorted(demoted, key=str.lower):
            if word.lower() not in keywords:
                keywords.append(word.lower())
                stats["demoted"] += 1

        for source, name, conf in (
            (USER, user, None),
            (MODEL, model, confidence),
            (HASHTAG, from_hashtags, None),
        ):
            if name:
                conn.execute(
                    """INSERT INTO item_tag (shortcode, tag_id, source, confidence)
                       VALUES (?, ?, ?, ?)""",
                    (code, tag_id(name), source, conf),
                )
        tag = user or model or from_hashtags
        stats["tagged" if tag else "untagged"] += 1
        stats["multi"] += sum(len(v) for v in rows.values()) > 1
        conn.execute(
            """UPDATE item SET primary_tag = ?, clean_hashtags = ?, keywords = ?,
                               hashtag_v = ? WHERE shortcode = ?""",
            (
                tag,
                json.dumps(clean(hashtags)) if r["hashtags"] is not None else None,
                json.dumps(keywords) if keywords else None,
                HASHTAG_V,
                code,
            ),
        )

    # Proposals, dismissals, accepted proposals and dropped categories go.
    stats["tags_removed"] = conn.execute(
        f"""DELETE FROM tag WHERE name NOT IN ({",".join("?" * len(categories))})
            AND id NOT IN (SELECT tag_id FROM item_tag)""",
        sorted(categories),
    ).rowcount
    conn.execute(
        f"""UPDATE tag SET kind = 'user'
            WHERE name NOT IN ({",".join("?" * len(categories))})""",
        sorted(categories),
    )
    return dict(stats)
