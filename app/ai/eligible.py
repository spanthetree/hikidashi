"""Which reels an API may take: the loose rule (#125).

A reel's transcript, on-screen text, tags and recipe are stored once, on the
item, for everyone who saved it. So a reel is processed once, by one API or
by workers, and its savers all get the result.

    a user's API     the reels that user saved, while their switch is on
                     and their choice is "mine"
    the server's     Just me: the reels of the admin who turned it on.
                     All users: reels where at least one saver relies on it,
                     their choice being "server" or the server default.
                     It waits GRACE seconds after it first finds a reel
                     ready, so a saver's own API, offered it first, pays for
                     its own reels where it can.

A "workers only" user's reels are never taken on their account; a reel
another saver's API may take is taken, with nothing in the request saying
who else saved it.

Ready: fetched, and some worker stage (asr, ocr, tag, recipe) still to do.
Not: a reel a worker holds a lease on for one of them (all or nothing: the
API takes a reel's every remaining stage, or none), one at the API error cap,
or one left to workers when the switch went on for new reels only.

Stdlib only.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable

from app.jobs.protocol import REMOTE
from app.jobs.queue import REGISTRY

from .config import Api, use

GRACE = 60  # seconds the server's API leaves a ready reel to its savers' own
MAX_ERRORS = 3  # API errors on a reel before an API leaves it to workers


def ready(conn: sqlite3.Connection) -> list[str]:
    """Fetched reels with a worker stage still to do, newest saves first."""
    todo: set[str] = set()
    for stage in REMOTE:
        todo |= set(REGISTRY[stage].pending(conn, None))
    todo -= set(REGISTRY["enrich"].pending(conn, None))
    if not todo:
        return []
    rows = conn.execute(
        """SELECT shortcode FROM item WHERE shortcode IN (SELECT value FROM json_each(?))
           ORDER BY saved_at IS NULL, saved_at DESC, shortcode""",
        (json.dumps(sorted(todo)),),
    )
    return [r[0] for r in rows]


def savers(conn: sqlite3.Connection, codes: Iterable[str]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for r in conn.execute(
        """SELECT shortcode, user_id FROM user_item
           WHERE shortcode IN (SELECT value FROM json_each(?))""",
        (json.dumps(list(codes)),),
    ):
        out.setdefault(r[0], []).append(r[1])
    return out


def relies_on_server(conn: sqlite3.Connection, user_id: int) -> bool:
    return use(conn, user_id) in ("server", "default")


def covered(conn: sqlite3.Connection, api: Api, codes: list[str]) -> list[str]:
    """Of `codes`, those `api` may take by the loose rule, switch aside."""
    by = savers(conn, codes)
    if api.owner is not None:
        if use(conn, api.owner) != "mine":
            return []
        return [c for c in codes if api.owner in by.get(c, [])]
    if api.switch.scope == "me":
        return [c for c in codes if api.switch.by in by.get(c, [])]
    relies: dict[int, bool] = {}

    def rely(u: int) -> bool:
        if u not in relies:
            relies[u] = relies_on_server(conn, u)
        return relies[u]

    return [c for c in codes if any(rely(u) for u in by.get(c, []))]


def leased(conn: sqlite3.Connection, codes: list[str]) -> set[str]:
    """Reels someone holds a live lease on for a worker stage."""
    return {
        r[0]
        for r in conn.execute(
            f"""SELECT DISTINCT shortcode FROM job_lease
                WHERE stage IN ({",".join("?" * len(REMOTE))})
                  AND shortcode IN (SELECT value FROM json_each(?))""",
            (*REMOTE, json.dumps(codes)),
        )
    }


def given_up(conn: sqlite3.Connection, api: str, *, cap: int = MAX_ERRORS) -> set[str]:
    """Reels this API left to workers: at the error cap, or the backlog."""
    return {
        r[0]
        for r in conn.execute(
            "SELECT shortcode FROM api_reel WHERE api = ? AND (errors >= ? OR left)",
            (api, cap),
        )
    }


def waiting(conn: sqlite3.Connection, api: Api, *, name: str | None = None) -> list[str]:
    """Reels `api` would take if its switch were on now: for the backlog
    shown when it is turned on. Leased reels and its given-up ones (under
    `name`, its own by default) are out; the grace period is not applied."""
    codes = covered(conn, api, ready(conn))
    skip = given_up(conn, name or api.name) | leased(conn, codes)
    return [c for c in codes if c not in skip]


def mark_seen(conn: sqlite3.Connection, api: str, codes: list[str], now: int) -> None:
    conn.executemany(
        """INSERT INTO api_reel (api, shortcode, seen_at) VALUES (?, ?, ?)
           ON CONFLICT (api, shortcode) DO UPDATE SET
             seen_at = COALESCE(api_reel.seen_at, excluded.seen_at)""",
        [(api, c, now) for c in codes],
    )


def after_grace(
    conn: sqlite3.Connection, api: str, codes: list[str], now: int, grace: int
) -> list[str]:
    """Of `codes`, those `api` first found ready at least `grace` seconds ago."""
    seen = {
        r[0]: r[1]
        for r in conn.execute(
            """SELECT shortcode, seen_at FROM api_reel
               WHERE api = ? AND shortcode IN (SELECT value FROM json_each(?))""",
            (api, json.dumps(codes)),
        )
    }
    return [c for c in codes if seen.get(c) is not None and now - seen[c] >= grace]


def leave(conn: sqlite3.Connection, api: str, codes: list[str]) -> None:
    """Leave these reels to workers: the backlog, on a switch turned on for
    new reels only."""
    conn.executemany(
        """INSERT INTO api_reel (api, shortcode, left) VALUES (?, ?, 1)
           ON CONFLICT (api, shortcode) DO UPDATE SET left = 1""",
        [(api, c) for c in codes],
    )


def count_error(conn: sqlite3.Connection, api: str, code: str, reason: str, now: int) -> int:
    """An API error on a reel. Returns how many it has had."""
    conn.execute(
        """INSERT INTO api_reel (api, shortcode, errors, reason, last_at) VALUES (?, ?, 1, ?, ?)
           ON CONFLICT (api, shortcode) DO UPDATE SET
             errors = api_reel.errors + 1, reason = excluded.reason, last_at = excluded.last_at""",
        (api, code, reason[:200], now),
    )
    row = conn.execute(
        "SELECT errors FROM api_reel WHERE api = ? AND shortcode = ?", (api, code)
    ).fetchone()
    return int(row[0])
