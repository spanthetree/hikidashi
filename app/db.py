"""SQLite access. Stdlib only, so the core is testable with no install step."""

from __future__ import annotations

import fcntl
import re
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# A row read here, or the same columns as a dict: what a worker on another
# machine is sent instead (#35).
Row = sqlite3.Row | Mapping[str, Any]

SCHEMA = Path(__file__).resolve().parent / "schema.sql"


def connect(db_path: Path | str) -> sqlite3.Connection:
    # check_same_thread=False lets the enrichment worker's thread pool write
    # through one connection. That is only safe because every writer holds a
    # lock (see app/enrich/worker.py); do not write from a thread without one.
    # isolation_level=None means autocommit, which is what makes a long
    # enrichment run resumable: each item commits as it finishes.
    conn = sqlite3.connect(str(db_path), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


# Columns added after the first release. CREATE TABLE IF NOT EXISTS will not add
# them to a database that already holds rows, so they are applied explicitly.
# Append only — never reorder or remove, and never with a non-constant default.
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("item", "duration", "INTEGER"),
    ("item", "width", "INTEGER"),
    ("item", "height", "INTEGER"),
    ("item", "like_count", "INTEGER"),
    ("item", "comment_count", "INTEGER"),
    ("item", "fetch_meta", "TEXT"),
    ("item", "keywords", "TEXT"),
    ("item", "hashtag_v", "INTEGER NOT NULL DEFAULT 0"),
    ("item", "recipe_v", "INTEGER NOT NULL DEFAULT 0"),
    ("item", "recipe_profile", "TEXT"),
    ("item", "primary_tag", "TEXT"),
    ("item", "clean_hashtags", "TEXT"),
    ("import_run", "user_id", "INTEGER NOT NULL DEFAULT 1"),
    ("item", "place_v", "INTEGER NOT NULL DEFAULT 0"),
    ("item", "game", "TEXT"),
    ("item", "game_guess", "TEXT"),
    ("user_item", "game", "TEXT"),
    ("item", "asr_language", "TEXT"),
    ("item", "ocr_profile", "TEXT"),
)

# Data that moves when a column first appears. Runs once, in the same call that
# adds the column, so it can never run twice.
_BACKFILLS: dict[str, str] = {
    # tag_v was the hashtag pass's until phase 03 gave it to the LLM tagger. No
    # model had run, so every stamp in it belongs to the hashtag pass.
    "item.hashtag_v": "UPDATE item SET hashtag_v = tag_v, tag_v = 0",
}


# What item_fts indexes. A virtual table cannot gain a column, so a database
# whose index is narrower has it dropped here, recreated by schema.sql, and
# rebuilt from item by init().
FTS_COLUMNS = (
    "caption",
    "transcript",
    "ocr_text",
    "summary",
    "primary_tag",
    "clean_hashtags",
    "keywords",
    "game",
)


def statement(name: str) -> str:
    """schema.sql's CREATE statement for one table, index or trigger, so that a
    migration rebuilding it builds what a new database would have."""
    pending = ""
    for line in SCHEMA.read_text(encoding="utf-8").splitlines(keepends=True):
        if line.lstrip().startswith("--") or not (pending or line.strip()):
            continue
        pending += line
        if sqlite3.complete_statement(pending):
            found = re.match(
                r"CREATE\s+(?:VIRTUAL\s+)?\w+\s+IF NOT EXISTS\s+(\w+)", pending.strip()
            )
            if found and found[1] == name:
                return pending.strip()
            pending = ""
    raise KeyError(f"{name} is not in {SCHEMA.name}")


def _columns(conn: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return conn.execute(f"PRAGMA table_info({table})").fetchall()


def _narrow_fts(conn: sqlite3.Connection) -> bool:
    """Drop item_fts and its triggers if it indexes fewer columns than it should."""
    have = tuple(r[1] for r in _columns(conn, "item_fts"))
    if not have or have == FTS_COLUMNS:
        return False
    for trigger in ("item_ai", "item_ad", "item_au"):
        conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    conn.execute("DROP TABLE item_fts")
    return True


def _tag_order(conn: sqlite3.Connection) -> bool:
    """Drop the item_tag triggers if they predate the game pass's answer
    (#117), so schema.sql recreates them. No row is from it yet, so no
    primary_tag changes."""
    sql = [
        r[0]
        for r in conn.execute(
            """SELECT sql FROM sqlite_master WHERE type = 'trigger'
               AND name IN ('item_tag_ai', 'item_tag_ad', 'item_tag_au')"""
        )
    ]
    if not sql or all("'game'" in s for s in sql):
        return False
    for trigger in ("item_tag_ai", "item_tag_ad", "item_tag_au"):
        conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    return True


def _multi_tag(conn: sqlite3.Connection) -> bool:
    """Collapse item_tag from many tags per item to one per source (#21)."""
    pk = {r[1]: r[5] for r in _columns(conn, "item_tag")}
    if not pk or not pk.get("tag_id"):
        return False
    from app.tagging.consolidate import consolidate

    conn.execute("BEGIN")
    try:
        consolidate(conn)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return True


def _per_user(conn: sqlite3.Connection) -> bool:
    """Give the library to user 1, so that others can have their own (#12)."""
    if not _columns(conn, "item") or _columns(conn, "user_item"):
        return False
    from app.library.migrate import separate

    conn.execute("BEGIN")
    try:
        separate(conn)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return True


def _one_token_table(conn: sqlite3.Connection) -> bool:
    """Move worker tokens into api_token (#103), so every token is one kind of
    row. A token keeps its hash, so a worker using it is not interrupted, and
    goes to whoever made it, else the first admin."""
    if not _columns(conn, "worker_token") or not _columns(conn, "api_token"):
        return False
    conn.execute("BEGIN")
    try:
        conn.execute(
            """INSERT INTO api_token
                 (kind, user_id, name, scopes, token_hash, created_at, last_used_at)
               SELECT 'worker',
                      COALESCE(created_by, (SELECT MIN(id) FROM user WHERE is_admin), 1),
                      name, 'worker', token_hash, created_at, last_used_at
               FROM worker_token ORDER BY id"""
        )
        conn.execute("DROP TABLE worker_token")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return True


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Bring a database created by an earlier release up to this schema."""
    applied = []
    for table, column, ddl in _ADDED_COLUMNS:
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue  # table not created yet; schema.sql will
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            applied.append(f"{table}.{column}")
            if f"{table}.{column}" in _BACKFILLS:
                conn.execute(_BACKFILLS[f"{table}.{column}"])
    # The index first: rewriting the tags updates item rows, and the old
    # triggers would index each of them only to be thrown away.
    if _narrow_fts(conn):
        applied.append("item_fts")
    if _tag_order(conn):
        applied.append("item_tag triggers")
    if _multi_tag(conn):
        applied.append("item_tag")
    if _per_user(conn):
        applied.append("user_item")
    if _one_token_table(conn):
        applied.append("api_token")
    return applied


def init(conn: sqlite3.Connection) -> None:
    """Apply the schema, then any later column additions.

    Safe to run against an existing database; it never drops or rewrites.
    """
    applied = migrate(conn)
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    applied += migrate(conn)
    from app.library import ensure_local

    ensure_local(conn)
    if "item_fts" in applied:
        conn.execute("INSERT INTO item_fts(item_fts) VALUES ('rebuild')")


@contextmanager
def _one_at_a_time(db_path: Path | str) -> Iterator[None]:
    """Hold a lock beside the database, across processes and containers.

    On a first start every container on a library opens it at once, and init
    is many statements, not one transaction: a worker that migrates while the
    server is half-way through schema.sql takes the new database for an old
    one, and crashes (#87). flock, like the pipeline lock, so a holder that
    dies never leaves it held.
    """
    with open(f"{db_path}-init.lock", "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def open_db(db_path: Path | str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    with _one_at_a_time(db_path):
        init(conn)
    return conn
