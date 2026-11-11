"""Storing recipes, and reading back the one each reel shows.

Provenance works as it does for tags. The recipe pass writes `source='model'`
rows and only ever replaces those; a correction is a `source='user'` row beside
it, one per person who made one, which `now` prefers for that person. A
re-extract after a RECIPE_V bump therefore never touches what you fixed,
deleting your row brings the model's back, and nobody else sees your fix.

Saving a correction copies the whole recipe, not the fields you changed: a
recipe is read as a whole, and merging a new extraction into an old edit field
by field would produce a recipe nobody wrote.
"""

from __future__ import annotations

import collections
import json
import sqlite3
import time
from typing import Any

from app.library import LOCAL, mine

from .groups import group_named, groups_csv, groups_of, normalise
from .llm import Found, Ingredient, Recipe

MODEL = "model"
USER = "user"
NOBODY = 0  # recipe.user_id on the model's rows, which are everyone's


def now(user_id: int) -> str:
    """The recipe each reel shows this person, as a subquery: theirs if they
    corrected it, else the model's."""
    uid = int(user_id)
    return f"""(SELECT * FROM recipe r
                WHERE (r.source = '{USER}' AND r.user_id = {uid})
                   OR (r.source = '{MODEL}' AND NOT EXISTS (
                         SELECT 1 FROM recipe u WHERE u.shortcode = r.shortcode
                           AND u.source = '{USER}' AND u.user_id = {uid})))"""


# Joins a recipe row (rn) to its ingredients (ri).
_INGREDIENTS = """recipe_ingredient ri ON ri.shortcode = rn.shortcode
                  AND ri.source = rn.source AND ri.user_id = rn.user_id"""


def register(conn: sqlite3.Connection) -> None:
    """Make `hk_groups(item)` and `hk_norm(text)` callable from SQL here."""
    conn.create_function("hk_groups", 1, groups_csv, deterministic=True)
    conn.create_function("hk_norm", 1, lambda t: normalise(t or ""), deterministic=True)


def _write(
    conn: sqlite3.Connection,
    shortcode: str,
    source: str,
    user_id: int,
    recipe: Recipe,
    *,
    confidence: float | None = None,
    source_fields: list[str] | None = None,
    dropped: list[str] | None = None,
    hidden: bool = False,
) -> None:
    conn.execute(
        "DELETE FROM recipe WHERE shortcode = ? AND source = ? AND user_id = ?",
        (shortcode, source, user_id),
    )
    conn.execute(
        """INSERT INTO recipe (shortcode, source, user_id, title, servings, time_minutes,
                               steps, incomplete, hidden, confidence, source_fields,
                               dropped, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            shortcode,
            source,
            user_id,
            recipe.title,
            recipe.servings,
            recipe.time_minutes,
            json.dumps(recipe.steps),
            int(recipe.incomplete),
            int(hidden),
            confidence,
            json.dumps(source_fields) if source_fields is not None else None,
            json.dumps(dropped) if dropped else None,
            int(time.time()),
        ),
    )
    conn.executemany(
        """INSERT INTO recipe_ingredient (shortcode, source, user_id, position, item,
                                          quantity, unit, note, as_written)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                shortcode,
                source,
                user_id,
                i,
                ing.item,
                ing.quantity,
                ing.unit,
                ing.note,
                ing.as_written,
            )
            for i, ing in enumerate(recipe.ingredients)
        ],
    )


def set_model(conn: sqlite3.Connection, shortcode: str, found: Found | None) -> None:
    """Replace the model's recipe for this reel, or remove it for None."""
    if found is None:
        conn.execute("DELETE FROM recipe WHERE shortcode = ? AND source = ?", (shortcode, MODEL))
    else:
        _write(
            conn,
            shortcode,
            MODEL,
            NOBODY,
            found.recipe,
            confidence=found.confidence,
            source_fields=found.source_fields,
            dropped=found.dropped,
        )
    reindex(conn, shortcode, user_id=NOBODY)


def save_user(
    conn: sqlite3.Connection,
    shortcode: str,
    recipe: Recipe,
    *,
    hidden: bool = False,
    user_id: int = LOCAL,
) -> None:
    """Your version of this reel's recipe. `hidden`: it is not a recipe."""
    _write(conn, shortcode, USER, user_id, recipe, hidden=hidden)
    reindex(conn, shortcode, user_id=user_id)


def revert(conn: sqlite3.Connection, shortcode: str, *, user_id: int = LOCAL) -> None:
    """Drop your version, so the model's shows again."""
    conn.execute(
        "DELETE FROM recipe WHERE shortcode = ? AND source = ? AND user_id = ?",
        (shortcode, USER, user_id),
    )
    reindex(conn, shortcode, user_id=user_id)


def reindex(conn: sqlite3.Connection, shortcode: str, *, user_id: int = NOBODY) -> None:
    """Bring this person's row of recipe_fts in line with their recipe row:
    the model's for NOBODY, a correction for anyone else."""
    conn.execute("DELETE FROM recipe_fts WHERE shortcode = ? AND user_id = ?", (shortcode, user_id))
    source = MODEL if user_id == NOBODY else USER
    row = conn.execute(
        """SELECT title FROM recipe
           WHERE shortcode = ? AND source = ? AND user_id = ? AND hidden = 0""",
        (shortcode, source, user_id),
    ).fetchone()
    if row is None:
        return
    items = [
        r[0]
        for r in conn.execute(
            """SELECT item FROM recipe_ingredient
           WHERE shortcode = ? AND source = ? AND user_id = ? ORDER BY position""",
            (shortcode, source, user_id),
        )
    ]
    conn.execute(
        "INSERT INTO recipe_fts (shortcode, user_id, title, ingredients) VALUES (?, ?, ?, ?)",
        (shortcode, user_id, row["title"] or "", "\n".join(items)),
    )


def current(
    conn: sqlite3.Connection, shortcode: str, *, user_id: int = LOCAL
) -> dict[str, Any] | None:
    """The recipe this reel shows this person, for the API. None when there is none.

    A hidden one is returned, flagged, so the sheet can offer to undo it.
    """
    row = conn.execute(f"SELECT * FROM {now(user_id)} WHERE shortcode = ?", (shortcode,)).fetchone()
    if row is None:
        return None
    has_model = (
        row["source"] == MODEL
        or conn.execute(
            "SELECT 1 FROM recipe WHERE shortcode = ? AND source = ?", (shortcode, MODEL)
        ).fetchone()
        is not None
    )
    ingredients = [
        {
            **{k: r[k] for k in ("item", "quantity", "unit", "note")},
            "groups": sorted(groups_of(r["item"])),
        }
        for r in conn.execute(
            """SELECT * FROM recipe_ingredient
               WHERE shortcode = ? AND source = ? AND user_id = ?
               ORDER BY position""",
            (shortcode, row["source"], row["user_id"]),
        )
    ]
    steps = json.loads(row["steps"] or "[]")
    return {
        "title": row["title"],
        "servings": row["servings"],
        "time_minutes": row["time_minutes"],
        "ingredients": ingredients,
        "steps": steps,
        "incomplete": bool(row["incomplete"]),
        "hidden": bool(row["hidden"]),
        "confidence": row["confidence"],
        "source_fields": json.loads(row["source_fields"] or "[]"),
        "edited": row["source"] == USER,
        "has_model": has_model,
    }


def from_dict(data: dict[str, Any]) -> Recipe:
    """A recipe from the API's body: trimmed, empty rows dropped."""

    def text(v: object, limit: int = 200) -> str | None:
        return " ".join(v.split())[:limit] or None if isinstance(v, str) else None

    def count(v: object) -> int | None:
        return v if isinstance(v, int) and 0 < v < 10_000 else None

    ingredients = []
    for raw in data.get("ingredients") or []:
        item = text(raw.get("item"), 120)
        if item:
            ingredients.append(
                Ingredient(
                    item=item.lower(),
                    quantity=text(raw.get("quantity"), 40),
                    unit=text(raw.get("unit"), 40),
                    note=text(raw.get("note")),
                )
            )
    steps = [s for s in (text(x, 1000) for x in data.get("steps") or []) if s]
    return Recipe(
        title=text(data.get("title"), 120),
        servings=count(data.get("servings")),
        time_minutes=count(data.get("time_minutes")),
        ingredients=ingredients,
        steps=steps,
        incomplete=bool(data.get("incomplete")),
    )


# --- filtering ----------------------------------------------------------------


def visible(user_id: int) -> str:
    """The reels in this person's library that show them a recipe."""
    return f"SELECT shortcode FROM {now(user_id)} WHERE hidden = 0 AND {mine(user_id, 'shortcode')}"


def _mentions(value: str, i: int, params: dict[str, Any], user_id: int) -> str:
    """SQL true when an ingredient of the reel's recipe is in a group, or
    contains the words."""
    group = group_named(value)
    if group:
        params[f"g{i}"] = f",{group},"
        test = f"instr(hk_groups(ri.item), :g{i})"
    else:
        # Spaces around both sides make this a whole-word match.
        params[f"w{i}"] = f"% {normalise(value)} %"
        test = f"(' ' || hk_norm(ri.item) || ' ') LIKE :w{i}"
    return f"""EXISTS (SELECT 1 FROM {now(user_id)} rn JOIN {_INGREDIENTS}
                       WHERE rn.shortcode = item.shortcode AND {test})"""


def filters(
    has: list[str],
    without: list[str],
    max_time: int | None,
    params: dict[str, Any],
    *,
    user_id: int = LOCAL,
) -> list[str]:
    """WHERE clauses for the Recipes view: only recipes, then the filters.

    Needs `register` to have run on the connection.
    """
    where = [f"item.shortcode IN ({visible(user_id)})"]
    n = 0
    for value in has:
        if value.strip():
            where.append(_mentions(value, n, params, user_id))
            n += 1
    for value in without:
        if value.strip():
            where.append("NOT " + _mentions(value, n, params, user_id))
            n += 1
    if max_time:
        # Only recipes that say how long they take: "15 minutes or less" should
        # not answer with a braise that never mentioned time.
        where.append(f"""item.shortcode IN (SELECT shortcode FROM {now(user_id)}
                          WHERE hidden = 0 AND time_minutes <= :max_time)""")
        params["max_time"] = max_time
    return where


def card(
    conn: sqlite3.Connection, shortcodes: list[str], *, user_id: int = LOCAL
) -> dict[str, dict[str, Any]]:
    """What a grid card shows of each reel's recipe, for those that have one."""
    rows = conn.execute(
        f"""SELECT rn.shortcode, rn.title, rn.time_minutes, rn.incomplete,
                   (SELECT COUNT(*) FROM recipe_ingredient ri
                     WHERE ri.shortcode = rn.shortcode AND ri.source = rn.source
                       AND ri.user_id = rn.user_id) AS n
            FROM {now(user_id)} rn
            WHERE rn.hidden = 0 AND rn.shortcode IN (SELECT value FROM json_each(?))""",
        (json.dumps(shortcodes),),
    )
    return {
        r["shortcode"]: {
            "title": r["title"],
            "time_minutes": r["time_minutes"],
            "ingredients": r["n"],
            "incomplete": bool(r["incomplete"]),
        }
        for r in rows
    }


def same_key(item: str) -> str:
    """Close enough to count "garlic clove" and "garlic cloves" as one."""
    words = normalise(item).split()
    if words and len(words[-1]) > 3 and words[-1].endswith("s") and not words[-1].endswith("ss"):
        words[-1] = words[-1][:-1]
    return " ".join(words)


def facets(conn: sqlite3.Connection, *, top: int = 24, user_id: int = LOCAL) -> dict[str, Any]:
    """What the Recipes view can filter by, with how many of this person's
    recipes each matches."""
    total = conn.execute(f"SELECT COUNT(*) FROM ({visible(user_id)})").fetchone()[0]
    groups: collections.Counter[str] = collections.Counter()
    items: collections.Counter[str] = collections.Counter()
    display: dict[str, str] = {}
    per: dict[str, set[str]] = collections.defaultdict(set)
    for r in conn.execute(
        f"""SELECT rn.shortcode, ri.item FROM {now(user_id)} rn JOIN {_INGREDIENTS}
                WHERE rn.hidden = 0 AND {mine(user_id, "rn.shortcode")}"""
    ):
        per[r["shortcode"]].add(r["item"])
    for names in per.values():
        groups.update({g for item in names for g in groups_of(item)})
        keys: dict[str, str] = {}
        for item in names:
            keys.setdefault(same_key(item), item)
        items.update(keys.keys())
        for k, item in keys.items():
            # The shortest spelling seen reads best as a filter: "garlic", not
            # "garlic cloves, minced".
            if k not in display or len(item) < len(display[k]):
                display[k] = item
    timed = conn.execute(
        f"""SELECT SUM(time_minutes <= 15), SUM(time_minutes <= 30),
                   SUM(time_minutes <= 60)
            FROM {now(user_id)} WHERE hidden = 0 AND {mine(user_id, "shortcode")}"""
    ).fetchone()
    return {
        "total": total,
        "groups": [
            {"name": g, "count": groups[g]} for g in sorted(groups, key=lambda g: -groups[g])
        ],
        "ingredients": [
            {"name": display[k], "count": n} for k, n in items.most_common(top) if n > 1
        ],
        "times": [
            {"minutes": m, "count": c or 0} for m, c in zip((15, 30, 60), timed, strict=True)
        ],
    }


def total(conn: sqlite3.Connection, *, user_id: int = LOCAL) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM ({visible(user_id)})").fetchone()[0])
