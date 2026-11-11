"""The one-time move from one library to one per person (#12).

Before, the database was one person's: item.saved_at was when they saved a
reel, item_tag's 'user' rows were the tags they chose, and the collections and
recipe corrections were theirs. After, all of that belongs to user 1, the
library's owner, and `item` is a cache any number of people can share.

Run once, by app/db.py, when it finds `item` but no `user_item`.

    user 1        created as `local`: an admin, with no password
    user_item     one row per item, with its saved_at and first_seen_in, and
                  the tag you chose, moved out of item_tag
    item_tag      'user' rows gone, and item.primary_tag recomputed without them
    collection    rebuilt with user_id 1
    recipe        rebuilt with user_id: 0 on the model's rows, 1 on yours
    recipe_fts    rebuilt with one row per recipe row, not one per reel
    recipe_now    dropped: app/recipes/service.py asks per person instead
"""

from __future__ import annotations

import sqlite3

from app.db import statement

from . import LOCAL, ensure_local

_RECIPE = (
    "shortcode, source, {user}, title, servings, time_minutes, steps, incomplete, "
    "hidden, confidence, source_fields, dropped, updated_at"
)
_INGREDIENT = "shortcode, source, {user}, position, item, quantity, unit, note, as_written"
# Your corrections are user 1's; the model's rows belong to no one.
_OWNER = f"CASE source WHEN 'user' THEN {LOCAL} ELSE 0 END"


def _exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone() is not None
    )


def _rebuild(conn: sqlite3.Connection, table: str, columns: str, select: str) -> None:
    """Recreate `table` as schema.sql has it now, copying its rows across."""
    if not _exists(conn, table):
        conn.execute(statement(table))
        return
    conn.execute(f"ALTER TABLE {table} RENAME TO {table}_old")
    conn.execute(statement(table))
    conn.execute(f"INSERT INTO {table} ({columns}) SELECT {select} FROM {table}_old")


def separate(conn: sqlite3.Connection) -> None:
    """Give the whole library to user 1. Call inside a transaction."""
    conn.execute(statement("user"))
    conn.execute(statement("user_item"))
    ensure_local(conn)

    yours = (
        (
            "(SELECT tag_id FROM item_tag WHERE item_tag.shortcode = item.shortcode "
            "AND source = 'user')"
        )
        if _exists(conn, "item_tag")
        else "NULL"
    )
    conn.execute(
        f"""INSERT INTO user_item (user_id, shortcode, saved_at, first_seen_in, tag_id)
            SELECT {LOCAL}, shortcode, saved_at, first_seen_in, {yours} FROM item"""
    )

    if _exists(conn, "item_tag"):
        # schema.sql recreates these without the 'user' branch.
        for trigger in ("item_tag_ai", "item_tag_ad", "item_tag_au"):
            conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        conn.execute("DELETE FROM item_tag WHERE source = 'user'")
        shared = """(SELECT tag.name FROM item_tag JOIN tag ON tag.id = item_tag.tag_id
                     WHERE item_tag.shortcode = item.shortcode
                     ORDER BY item_tag.source = 'model' DESC LIMIT 1)"""
        conn.execute(f"UPDATE item SET primary_tag = {shared} WHERE primary_tag IS NOT {shared}")

    _rebuild(conn, "collection", "user_id, name, shortcode", f"{LOCAL}, name, shortcode")
    conn.execute("DROP TABLE IF EXISTS collection_old")

    # The view reads recipe, which is about to be renamed out from under it.
    conn.execute("DROP VIEW IF EXISTS recipe_now")
    _rebuild(conn, "recipe", _RECIPE.format(user="user_id"), _RECIPE.format(user=_OWNER))
    _rebuild(
        conn,
        "recipe_ingredient",
        _INGREDIENT.format(user="user_id"),
        _INGREDIENT.format(user=_OWNER),
    )
    conn.execute("DROP TABLE IF EXISTS recipe_ingredient_old")
    conn.execute("DROP TABLE IF EXISTS recipe_old")

    conn.execute("DROP TABLE IF EXISTS recipe_fts")
    conn.execute(statement("recipe_fts"))
    from app.recipes.service import reindex

    for code, user_id in conn.execute("SELECT shortcode, user_id FROM recipe").fetchall():
        reindex(conn, code, user_id=user_id)
