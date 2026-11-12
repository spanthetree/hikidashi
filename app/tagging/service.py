"""Applying tags to items.

Each item has one tag, its primary category, and up to three answers for it,
kept apart so each can be recomputed without touching the others:

    hashtag   derived from the post's own hashtags, by the taxonomy rules
    model     written by the LLM tagger (app/tagging/worker.py)
    game      Gaming, when the game pass is sure of a video game on a reel
              the others filed elsewhere (app/tagging/games.py)
    user      yours, and never overwritten by anything

The first two are `item_tag` rows, shared by everyone who saved the reel, and
triggers in schema.sql keep the game pass's, else the model's, else the
hashtag pass's, in item.primary_tag. Yours is user_item.tag_id: only you see it, over the shared
one (app.library.SHOWN_TAG).

Re-running a tagger replaces only its own row. Your choice survives every
re-run, which is also what makes it a usable evaluation set.
"""

from __future__ import annotations

import collections
import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from app.library import ASK_GAME, LOCAL, SHOWN_GAME, SHOWN_TAG

from .taxonomy import clean, names, primary

# Bumping this schedules every item for re-tagging by the hashtag pass. The LLM
# tagger has its own, TAG_V in app/tagging/llm.py, on the tag_v column.
HASHTAG_V = 3  # 3: six more categories (#145)

HASHTAG = "hashtag"
MODEL = "model"
GAME = "game"
USER = "user"

# Whose answer the item shows, first to last. Mirrors the triggers in schema.sql.
PRECEDENCE = (USER, GAME, MODEL, HASHTAG)


@dataclass
class TagStats:
    items: int = 0
    tagged: int = 0
    untagged: int = 0
    per_tag: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "items": self.items,
            "tagged": self.tagged,
            "untagged": self.untagged,
            "per_tag": dict(sorted(self.per_tag.items(), key=lambda kv: -kv[1])),
        }


def ensure_taxonomy(conn: sqlite3.Connection) -> int:
    """Make sure every taxonomy tag exists. Returns how many were created."""
    before = int(conn.execute("SELECT COUNT(*) FROM tag").fetchone()[0])
    conn.executemany(
        "INSERT OR IGNORE INTO tag (name, kind) VALUES (?, 'taxonomy')",
        [(n,) for n in names()],
    )
    # A name you used before it joined the taxonomy is a category now.
    conn.executemany("UPDATE tag SET kind = 'taxonomy' WHERE name = ?", [(n,) for n in names()])
    return int(conn.execute("SELECT COUNT(*) FROM tag").fetchone()[0]) - before


def _tag_id(conn: sqlite3.Connection, name: str, kind: str = "user") -> int:
    conn.execute("INSERT OR IGNORE INTO tag (name, kind) VALUES (?, ?)", (name, kind))
    return int(conn.execute("SELECT id FROM tag WHERE name = ?", (name,)).fetchone()[0])


def set_tag(
    conn: sqlite3.Connection,
    shortcode: str,
    name: str | None,
    source: str,
    *,
    confidence: float | None = None,
) -> None:
    """Replace this source's answer for one item; None withdraws it. The other
    sources' answers are untouched."""
    if name is None:
        conn.execute("DELETE FROM item_tag WHERE shortcode = ? AND source = ?", (shortcode, source))
        return
    conn.execute(
        """INSERT INTO item_tag (shortcode, tag_id, source, confidence)
           VALUES (?, ?, ?, ?)
           ON CONFLICT (shortcode, source)
           DO UPDATE SET tag_id = excluded.tag_id, confidence = excluded.confidence""",
        (
            shortcode,
            _tag_id(conn, name, "taxonomy" if name in names() else "user"),
            source,
            confidence,
        ),
    )


def set_user_tag(
    conn: sqlite3.Connection, shortcode: str, name: str, *, user_id: int = LOCAL
) -> str:
    """Choose this item's tag yourself. No automatic pass ever overrides it.

    Usually one of the categories. Any other name works and becomes a filter of
    its own, which is how a new top-level category starts; it should be rare.
    Typing "food" reuses the existing "Food" rather than starting a second tag
    that differs only in case. Returns the name the tag was stored under.
    """
    row = conn.execute(
        "SELECT name FROM tag WHERE name = ? COLLATE NOCASE ORDER BY name = ? DESC", (name, name)
    ).fetchone()
    name = row[0] if row else name
    conn.execute(
        "UPDATE user_item SET tag_id = ? WHERE user_id = ? AND shortcode = ?",
        (_tag_id(conn, name, "taxonomy" if name in names() else "user"), user_id, shortcode),
    )
    return name


def clear_user_tag(conn: sqlite3.Connection, shortcode: str, *, user_id: int = LOCAL) -> None:
    """Drop your choice, so the item shows the automatic one again."""
    conn.execute(
        "UPDATE user_item SET tag_id = NULL WHERE user_id = ? AND shortcode = ?",
        (user_id, shortcode),
    )
    prune_tags(conn)


def prune_tags(conn: sqlite3.Connection) -> None:
    """Delete names that nobody gives any reel. One only you used, now on no
    reel, would otherwise linger as an empty filter."""
    conn.execute(
        """DELETE FROM tag WHERE kind = 'user'
           AND id NOT IN (SELECT tag_id FROM item_tag)
           AND id NOT IN (SELECT tag_id FROM user_item WHERE tag_id IS NOT NULL)"""
    )


def apply_hashtags(
    conn: sqlite3.Connection, *, limit: int | None = None, force: bool = False
) -> TagStats:
    """Derive each item's category from its own hashtags, and clean them.

    Cheap, deterministic and offline — no model involved. Runs over items whose
    hashtag_v is behind, or everything with force=True.
    """
    ensure_taxonomy(conn)

    where = "" if force else "WHERE hashtag_v < :v"
    sql = f"SELECT shortcode, hashtags FROM item {where} ORDER BY saved_at DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql, {"v": HASHTAG_V}).fetchall()

    stats = TagStats(items=len(rows))
    counts: collections.Counter[str] = collections.Counter()

    for row in rows:
        hashtags = json.loads(row["hashtags"] or "[]")
        tag = primary(hashtags)
        set_tag(conn, row["shortcode"], tag, HASHTAG)
        if tag:
            stats.tagged += 1
            counts[tag] += 1
        else:
            stats.untagged += 1
        conn.execute(
            "UPDATE item SET clean_hashtags = ?, hashtag_v = ? WHERE shortcode = ?",
            (
                json.dumps(clean(hashtags)) if row["hashtags"] is not None else None,
                HASHTAG_V,
                row["shortcode"],
            ),
        )

    stats.per_tag = dict(counts)
    return stats


def item_tags(
    conn: sqlite3.Connection, shortcode: str, *, user_id: int | None = LOCAL
) -> list[dict[str, Any]]:
    """Every source's answer for one item, the one it shows first. Yours is
    this person's; None leaves it out."""
    rows = [
        dict(r)
        for r in conn.execute(
            """SELECT tag.name, tag.kind, item_tag.source, item_tag.confidence
           FROM item_tag JOIN tag ON tag.id = item_tag.tag_id
           WHERE item_tag.shortcode = ?
           UNION ALL
           SELECT tag.name, tag.kind, 'user', NULL
           FROM user_item JOIN tag ON tag.id = user_item.tag_id
           WHERE user_item.shortcode = ? AND user_item.user_id = ?""",
            (shortcode, shortcode, user_id),
        )
    ]
    return sorted(rows, key=lambda r: PRECEDENCE.index(r["source"]))


def tag_counts(conn: sqlite3.Connection, *, user_id: int | None = LOCAL) -> list[dict[str, Any]]:
    """Every category, and any name you gave a reel, with how many of this
    person's reels show it. None counts the shared answers on every reel.

    Someone else's own names are left out: they are theirs, and on none of
    your reels.
    """
    if user_id is None:
        shown = "SELECT item.primary_tag AS name FROM item"
    else:
        shown = f"""SELECT {SHOWN_TAG} AS name FROM user_item ui
                    JOIN item ON item.shortcode = ui.shortcode
                    WHERE ui.user_id = {int(user_id)}"""
    return [
        dict(r)
        for r in conn.execute(
            f"""SELECT tag.name, tag.kind, COUNT(shown.name) AS count
            FROM tag LEFT JOIN ({shown}) shown ON shown.name = tag.name
            GROUP BY tag.id
            HAVING tag.kind = 'taxonomy' OR count > 0
            ORDER BY count DESC, tag.name"""
        )
    ]


# --- the game a reel shows (#117) --------------------------------------------

MAX_GAME = 120


def set_user_game(
    conn: sqlite3.Connection, shortcode: str, name: str | None, *, user_id: int = LOCAL
) -> None:
    """Say which game this reel shows, or None for no game at all. Confirming
    the suggestion is naming it. Yours alone, and no pass overrides it."""
    name = " ".join((name or "").split())[:MAX_GAME]
    conn.execute(
        "UPDATE user_item SET game = ? WHERE user_id = ? AND shortcode = ?",
        (name, user_id, shortcode),
    )


def clear_user_game(conn: sqlite3.Connection, shortcode: str, *, user_id: int = LOCAL) -> None:
    """Drop your answer, so the reel shows the game pass's again."""
    conn.execute(
        "UPDATE user_item SET game = NULL WHERE user_id = ? AND shortcode = ?",
        (user_id, shortcode),
    )


def item_game(conn: sqlite3.Connection, shortcode: str, *, user_id: int = LOCAL) -> dict[str, Any]:
    """The game this person sees on a reel: `game` (or None), whether it is
    `yours`, and `guess`, a title to ask them to confirm (or None)."""
    row = conn.execute(
        f"""SELECT {SHOWN_GAME} AS game, ui.game IS NOT NULL AS yours, {ASK_GAME} AS guess
            FROM user_item ui JOIN item ON item.shortcode = ui.shortcode
            WHERE ui.user_id = ? AND ui.shortcode = ?""",
        (user_id, shortcode),
    ).fetchone()
    if row is None:
        return {"game": None, "yours": False, "guess": None}
    return {"game": row["game"], "yours": bool(row["yours"]), "guess": row["guess"]}
