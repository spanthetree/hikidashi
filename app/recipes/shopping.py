"""A shopping list for several recipes at once (#48).

What is stored is which recipes are on your list and which lines you ticked.
The lines are worked out on every read from the recipes as they are now: your
correction if you made one, else the model's. Fixing a recipe fixes the list,
and a recipe you take out of your library leaves it.

Ingredients merge when their names do, the way the Recipes filters count them
(`same_key`: "garlic clove" and "garlic cloves" are one line). Amounts add up
when their units agree, kilograms and grams included (amounts.py), and the
rest are listed as written beside them: "500 g + 1 handful". Each line goes
in an aisle (aisles.py), and pantry staples are flagged so the list can fold
them away.

A tick belongs to a line's key, so it survives the list being read again. When
a recipe is added that needs more of something already ticked, the tick comes
off: you have the onion for one recipe, not for two.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from app.library import LOCAL, mine

from .aisles import ORDER, aisle_of, is_staple
from .amounts import Amount, amount, base, dimension, written
from .service import now, same_key


@dataclass
class Line:
    key: str
    name: str
    amounts: dict[str | None, Amount] = field(default_factory=dict)  # by dimension
    loose: list[str] = field(default_factory=list)  # as written
    recipes: list[str] = field(default_factory=list)

    def add(self, item: str, quantity: str | None, unit: str | None, shortcode: str) -> None:
        # The shortest spelling reads best: "garlic", not "garlic cloves, minced".
        if len(item) < len(self.name):
            self.name = item
        if shortcode not in self.recipes:
            self.recipes.append(shortcode)
        a = amount(quantity, unit)
        if a is None:
            text = " ".join(x for x in (quantity, unit) if x)
            if text and text not in self.loose:
                self.loose.append(text)
            return
        a, d = base(a), dimension(a.unit)
        had = self.amounts.get(d)
        if had is None:
            self.amounts[d] = a
        else:
            high = (
                None
                if had.high is None and a.high is None
                else (had.high or had.low) + (a.high or a.low)
            )
            self.amounts[d] = Amount(had.low + a.low, high, d, "")

    def shown(self) -> list[str]:
        return [written(a) for a in self.amounts.values()] + self.loose


def _on_list(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    """The recipes on this person's list that they can still see, oldest first."""
    return conn.execute(
        f"""SELECT rn.shortcode, rn.source, rn.user_id, rn.title, rn.servings,
                   item.thumb_path
            FROM shopping_recipe s
            JOIN {now(user_id)} rn ON rn.shortcode = s.shortcode
            JOIN item ON item.shortcode = s.shortcode
            WHERE s.user_id = ? AND rn.hidden = 0 AND {mine(user_id, "s.shortcode")}
            ORDER BY s.added_at, s.shortcode""",
        (user_id,),
    ).fetchall()


def _ingredients(
    conn: sqlite3.Connection, recipes: list[sqlite3.Row]
) -> Iterator[tuple[str, sqlite3.Row]]:
    for r in recipes:
        for i in conn.execute(
            """SELECT item, quantity, unit FROM recipe_ingredient
                   WHERE shortcode = ? AND source = ? AND user_id = ? ORDER BY position""",
            (r["shortcode"], r["source"], r["user_id"]),
        ):
            yield r["shortcode"], i


def lines(conn: sqlite3.Connection, recipes: list[sqlite3.Row]) -> list[Line]:
    """The merged list for these recipes, by aisle, then name."""
    merged: dict[str, Line] = {}
    for shortcode, i in _ingredients(conn, recipes):
        key = same_key(i["item"])
        if not key:
            continue
        line = merged.setdefault(key, Line(key, i["item"]))
        line.add(i["item"], i["quantity"], i["unit"], shortcode)
    return sorted(merged.values(), key=lambda ln: (ORDER.index(aisle_of(ln.name)), ln.name))


def _ticked(conn: sqlite3.Connection, user_id: int) -> set[str]:
    return {
        r[0] for r in conn.execute("SELECT line FROM shopping_tick WHERE user_id = ?", (user_id,))
    }


def view(conn: sqlite3.Connection, *, user_id: int = LOCAL) -> dict[str, Any]:
    """This person's list, for the API."""
    recipes = _on_list(conn, user_id)
    ticked = _ticked(conn, user_id)
    return {
        "recipes": [
            {
                "shortcode": r["shortcode"],
                "title": r["title"],
                "servings": r["servings"],
                "thumb": f"/media/{r['thumb_path']}" if r["thumb_path"] else None,
            }
            for r in recipes
        ],
        "lines": [
            {
                "key": ln.key,
                "name": ln.name,
                "aisle": aisle_of(ln.name),
                "staple": is_staple(ln.name),
                "amounts": ln.shown(),
                "recipes": ln.recipes,
                "ticked": ln.key in ticked,
            }
            for ln in lines(conn, recipes)
        ],
    }


def add(conn: sqlite3.Connection, shortcodes: list[str], *, user_id: int = LOCAL) -> int:
    """Put these recipes on the list. Reels without a recipe you can see, or
    already on it, are skipped. Returns how many were added."""
    candidates = conn.execute(
        f"""SELECT rn.* FROM {now(user_id)} rn
            WHERE rn.hidden = 0 AND {mine(user_id, "rn.shortcode")}
              AND rn.shortcode IN (SELECT value FROM json_each(?))
              AND rn.shortcode NOT IN (SELECT shortcode FROM shopping_recipe
                                       WHERE user_id = ?)""",
        (json.dumps(list(dict.fromkeys(shortcodes))), user_id),
    ).fetchall()
    stamp = int(time.time())
    conn.executemany(
        "INSERT INTO shopping_recipe (user_id, shortcode, added_at) VALUES (?, ?, ?)",
        [(user_id, r["shortcode"], stamp) for r in candidates],
    )
    # More of something ticked is needed now: untick it.
    needed = {same_key(i["item"]) for _, i in _ingredients(conn, candidates)}
    conn.executemany(
        "DELETE FROM shopping_tick WHERE user_id = ? AND line = ?", [(user_id, k) for k in needed]
    )
    return len(candidates)


def drop(conn: sqlite3.Connection, shortcode: str, *, user_id: int = LOCAL) -> None:
    """Take a recipe off the list, and the ticks of lines that went with it."""
    conn.execute(
        "DELETE FROM shopping_recipe WHERE user_id = ? AND shortcode = ?", (user_id, shortcode)
    )
    left = {ln.key for ln in lines(conn, _on_list(conn, user_id))}
    conn.executemany(
        "DELETE FROM shopping_tick WHERE user_id = ? AND line = ?",
        [(user_id, k) for k in _ticked(conn, user_id) - left],
    )


def clear(conn: sqlite3.Connection, *, user_id: int = LOCAL) -> None:
    conn.execute("DELETE FROM shopping_recipe WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM shopping_tick WHERE user_id = ?", (user_id,))


def tick(conn: sqlite3.Connection, line: str, ticked: bool, *, user_id: int = LOCAL) -> None:
    if ticked:
        conn.execute(
            "INSERT OR IGNORE INTO shopping_tick (user_id, line) VALUES (?, ?)", (user_id, line)
        )
    else:
        conn.execute("DELETE FROM shopping_tick WHERE user_id = ? AND line = ?", (user_id, line))


def listed(conn: sqlite3.Connection, *, user_id: int = LOCAL) -> set[str]:
    """The reels whose recipes are on this person's list."""
    return {r["shortcode"] for r in _on_list(conn, user_id)}
