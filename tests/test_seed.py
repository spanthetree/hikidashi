"""The seeded library the browser tests run against (tests/seed.py) is what it
says it is. Needs argon2-cffi, for the two accounts; skipped without it."""

import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

from app import accounts
from app.db import open_db
from app.library import mine
from tests import seed

HAS_ARGON2 = importlib.util.find_spec("argon2") is not None


@unittest.skipUnless(HAS_ARGON2, "argon2-cffi not installed")
class TestSeed(unittest.TestCase):
    tmp: ClassVar[tempfile.TemporaryDirectory[str]]
    seed: ClassVar[seed.Seed]
    conn: ClassVar[sqlite3.Connection]

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.seed = seed.build(Path(cls.tmp.name))
        cls.conn = open_db(cls.seed.db)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def library(self, name):
        uid = self.seed.users[name]
        return {r[0] for r in self.conn.execute(f"SELECT shortcode FROM item WHERE {mine(uid)}")}

    def test_each_person_has_their_own_reels_and_one_in_common(self):
        alice, bob = self.library("alice"), self.library("bob")
        self.assertEqual(alice, {r.code for r in seed.ALICE_REELS})
        self.assertEqual(bob, {r.code for r in seed.BOB_REELS})
        self.assertEqual(alice & bob, {seed.SHARED.code})

    def test_both_can_sign_in(self):
        for name, password in (seed.ALICE, seed.BOB):
            self.assertIsNotNone(accounts.authenticate(self.conn, name, password))
        self.assertFalse(accounts.needs_setup(self.conn))

    def test_fetched_reels_have_their_files(self):
        rows = self.conn.execute(
            "SELECT shortcode, thumb_path, media_path FROM item WHERE state != 'failed'"
            " AND thumb_path IS NOT NULL"
        ).fetchall()
        self.assertGreaterEqual(len(rows), 10)
        for row in rows:
            self.assertTrue((self.seed.media / row["thumb_path"]).is_file(), row[0])
            self.assertTrue((self.seed.media / row["media_path"]).is_file(), row[0])

    def test_it_has_what_the_flows_need(self):
        def one(sql):
            return self.conn.execute(sql).fetchone()[0]

        self.assertEqual(one("SELECT COUNT(*) FROM item WHERE state = 'failed'"), 1)
        self.assertEqual(one("SELECT COUNT(*) FROM recipe"), 1)
        self.assertGreaterEqual(one("SELECT COUNT(DISTINCT primary_tag) FROM item"), 8)
        self.assertEqual(one("SELECT COUNT(DISTINCT name) FROM collection"), 2)
        self.assertTrue(self.seed.upload.is_file())

    def test_a_first_run_has_nobody(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = seed.build(Path(tmp), people=False)
            conn = open_db(empty.db)
            self.assertTrue(accounts.needs_setup(conn))
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM item").fetchone()[0], 0)
            conn.close()


if __name__ == "__main__":
    unittest.main()
