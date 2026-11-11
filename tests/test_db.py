"""Opening the database: the schema and migrations, from several processes."""

import multiprocessing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.db import open_db


def _open(db, start, errors):
    """One container's start: wait for the others, then open the database."""
    start.wait()
    try:
        open_db(db).close()
    except Exception as exc:
        errors.put(repr(exc))


class TestFirstStart(unittest.TestCase):
    def test_every_container_opening_a_new_database_at_once_succeeds(self):
        # The app, server-worker and worker all start together on a first
        # install, in any order (#87). Each opened a database another was
        # still creating, took it for an old one, and migrated it.
        for _ in range(5):
            with tempfile.TemporaryDirectory() as tmp:
                db = str(Path(tmp) / "hikidashi.sqlite3")
                ctx = multiprocessing.get_context("fork")
                start, errors = ctx.Barrier(6), ctx.Queue()
                procs = [ctx.Process(target=_open, args=(db, start, errors)) for _ in range(6)]
                for p in procs:
                    p.start()
                for p in procs:
                    p.join(30)
                failed = []
                while not errors.empty():
                    failed.append(errors.get())
                self.assertEqual(failed, [])
                self.assertEqual([p.exitcode for p in procs], [0] * 6)

                conn = sqlite3.connect(db)
                tables = {
                    r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                conn.close()
                self.assertTrue({"item", "user", "user_item", "tag"} <= tables)


class TestTokenMigration(unittest.TestCase):
    def test_worker_tokens_move_into_api_token_and_keep_working(self):
        # Before #103 a worker token had a table of its own, and no prefix.
        from app import accounts

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "hikidashi.sqlite3"
            conn = open_db(db)
            conn.execute("DROP TABLE api_token")
            conn.execute(
                """CREATE TABLE worker_token (
                     id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                     token_hash TEXT NOT NULL UNIQUE,
                     created_by INTEGER REFERENCES user(id) ON DELETE SET NULL,
                     created_at INTEGER NOT NULL, last_used_at INTEGER)"""
            )
            conn.execute("UPDATE user SET is_admin = 1 WHERE id = 1")
            for name, by in (("desktop", 1), ("laptop", None)):
                conn.execute(
                    "INSERT INTO worker_token (name, token_hash, created_by, created_at)"
                    " VALUES (?, ?, ?, 5)",
                    (name, accounts._digest(f"old-{name}"), by),
                )
            conn.close()

            conn = open_db(db)
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
            self.assertNotIn("worker_token", tables)
            rows = conn.execute(
                "SELECT name, kind, user_id, scopes, expires_at FROM api_token ORDER BY id"
            ).fetchall()
            self.assertEqual(
                [tuple(r) for r in rows],
                [("desktop", "worker", 1, "worker", None), ("laptop", "worker", 1, "worker", None)],
            )
            self.assertTrue(accounts.worker_token_ok(conn, "old-desktop"))
            conn.close()


if __name__ == "__main__":
    unittest.main()
