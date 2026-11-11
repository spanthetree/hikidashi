"""Whose reels are whose. Stdlib only.

Every reel is one row in `item`, shared by everyone who saved it: fetching,
transcribing and tagging depend only on the shortcode, so they are done once. A
person's library is their rows in `user_item`. Anything a request reads about a
reel goes through `mine`, so a reel nobody else saved stays invisible to
everyone else, down to whether it exists.

Taking a reel out of your library leaves it for whoever else saved it. The last
person to remove it removes it from the server, media and all.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

# The library's owner from before there were accounts. Always exists; with
# HIKIDASHI_AUTH=none, every request is this user.
LOCAL = 1

# The tag a reel shows the person who saved it: theirs, else the model's or the
# hashtags' (item.primary_tag). Needs `ui`, their user_item row, and `item`.
SHOWN_TAG = "COALESCE((SELECT name FROM tag WHERE tag.id = ui.tag_id), item.primary_tag)"
# The game a reel shows this person: theirs ('' when they said none), else the
# one the game pass is sure of. A guess is never shown as the game.
SHOWN_GAME = "(CASE WHEN ui.game IS NULL THEN item.game ELSE NULLIF(ui.game, '') END)"
# The guess to ask them about: only while neither they nor the pass has an answer.
ASK_GAME = "(CASE WHEN ui.game IS NULL AND item.game IS NULL THEN item.game_guess END)"


def ensure_local(conn: sqlite3.Connection) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO user (id, username, is_admin, created_at)
           VALUES (?, 'local', 1, ?)""",
        (LOCAL, int(time.time())),
    )


def mine(user_id: int, column: str = "item.shortcode") -> str:
    """SQL that is true when `column` is a reel in this person's library."""
    return f"{column} IN (SELECT shortcode FROM user_item WHERE user_id = {int(user_id)})"


def has(conn: sqlite3.Connection, user_id: int, shortcode: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM user_item WHERE user_id = ? AND shortcode = ?", (user_id, shortcode)
        ).fetchone()
        is not None
    )


@dataclass
class Removed:
    purged: bool = False  # nobody has it now, so the reel went too
    files: list[str] = field(default_factory=list)  # media to delete, relative


def remove(conn: sqlite3.Connection, user_id: int, shortcode: str) -> Removed | None:
    """Take a reel out of this person's library, with everything of theirs on
    it: collections, their tag, their recipe, its place on their shopping
    list. None if it was not in it.

    When nobody else has it saved, the reel goes too. Its media files are
    returned rather than deleted, so the caller can delete them once this has
    committed. Call inside a transaction.
    """
    if not has(conn, user_id, shortcode):
        return None
    from app.tagging.service import prune_tags

    conn.execute("DELETE FROM collection WHERE user_id = ? AND shortcode = ?", (user_id, shortcode))
    conn.execute(
        "DELETE FROM recipe WHERE shortcode = ? AND source = 'user' AND user_id = ?",
        (shortcode, user_id),
    )
    conn.execute("DELETE FROM recipe_fts WHERE shortcode = ? AND user_id = ?", (shortcode, user_id))
    conn.execute(
        "DELETE FROM shopping_recipe WHERE user_id = ? AND shortcode = ?", (user_id, shortcode)
    )
    conn.execute("DELETE FROM user_item WHERE user_id = ? AND shortcode = ?", (user_id, shortcode))

    removed = Removed()
    if not conn.execute(
        "SELECT 1 FROM user_item WHERE shortcode = ? LIMIT 1", (shortcode,)
    ).fetchone():
        row = conn.execute(
            "SELECT thumb_path, media_path FROM item WHERE shortcode = ?", (shortcode,)
        ).fetchone()
        slides = [
            r[0]
            for r in conn.execute("SELECT path FROM item_slide WHERE shortcode = ?", (shortcode,))
        ]
        # Tags, recipes, vectors and the search index follow by cascade and
        # trigger; recipe_fts is a plain FTS table and has neither.
        conn.execute("DELETE FROM recipe_fts WHERE shortcode = ?", (shortcode,))
        conn.execute("DELETE FROM item WHERE shortcode = ?", (shortcode,))
        removed = Removed(purged=True, files=[p for p in row if p] + slides)
    prune_tags(conn)
    return removed


def delete_files(media_dir: Path, files: list[str]) -> None:
    """Delete media left behind by `remove`. Never outside `media_dir`."""
    root = media_dir.resolve()
    for rel in files:
        path = (root / rel).resolve()
        if path.is_relative_to(root):
            path.unlink(missing_ok=True)
