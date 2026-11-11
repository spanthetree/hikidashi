"""One library per person over one shared cache of reels (#12)."""

import importlib.util
import tempfile
import time
import unittest
from pathlib import Path

from app import library, search
from app.db import init, open_db
from app.embed.embedder import pack
from app.importer.service import import_export
from app.recipes import service as recipes
from app.tagging import service as tagging
from tests.fixtures import must, post_record, saved_collections, saved_posts, write_zip

R = "https://www.instagram.com/reel/"
HAS_VEC = importlib.util.find_spec("sqlite_vec") is not None

A, B = 1, 2


class TwoPeople(unittest.TestCase):
    """A (user 1, the library's owner) and B. They both saved SHARED; each
    saved one reel the other did not."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.conn = open_db(self.dir / "t.sqlite3")
        self.conn.execute(
            "INSERT INTO user (id, username, created_at) VALUES (?, 'b', ?)", (B, int(time.time()))
        )
        self.a = self.export(
            "a.zip",
            [
                ("SHARED", 200, "shared pasta night"),
                ("ONLYA1", 100, "brake pads for the corolla"),
            ],
            {"Cars": ["ONLYA1"]},
        )
        self.b = self.export(
            "b.zip",
            [
                ("SHARED", 150, "shared pasta night"),
                ("ONLYB1", 300, "secret ramen spot"),
            ],
            {"Food": ["SHARED"]},
        )
        self.first_a = import_export(self.conn, self.a, user_id=A)
        self.first_b = import_export(self.conn, self.b, user_id=B)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def export(self, name, reels, groups=None):
        records = [
            post_record(R + code + "/", timestamp=ts, username="x", caption=caption)
            for code, ts, caption in reels
        ]
        groups = groups and {
            g: [post_record(R + code + "/", timestamp=1, username="x") for code in codes]
            for g, codes in groups.items()
        }
        return write_zip(
            self.dir / name, saved_posts(records), saved_collections(groups) if groups else None
        )

    def library(self, user_id):
        return {
            r[0]
            for r in self.conn.execute(
                "SELECT shortcode FROM user_item WHERE user_id = ?", (user_id,)
            )
        }


class TestImport(TwoPeople):
    def test_each_library_has_its_own_reels_over_one_shared_row(self):
        self.assertEqual(self.library(A), {"SHARED", "ONLYA1"})
        self.assertEqual(self.library(B), {"SHARED", "ONLYB1"})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM item").fetchone()[0], 3)

    def test_counts_are_of_your_library(self):
        # SHARED was already on the server, but it is new to B.
        self.assertEqual(self.first_b.inserted, 2)

    def test_save_times_are_each_persons_own(self):
        saved = dict(
            self.conn.execute("SELECT user_id, saved_at FROM user_item WHERE shortcode = 'SHARED'")
        )
        self.assertEqual(saved, {A: 200, B: 150})

    def test_an_export_already_imported_by_someone_else_is_not_a_repeat(self):
        again = import_export(self.conn, self.a, user_id=B)
        self.assertFalse(again.already_imported)
        self.assertTrue(import_export(self.conn, self.a, user_id=A).already_imported)

    def test_reimporting_changes_nothing(self):
        again = import_export(self.conn, self.b, user_id=B)
        self.assertEqual((again.inserted, again.updated), (0, 0))

    def test_collections_are_each_persons_own(self):
        rows = {
            tuple(r) for r in self.conn.execute("SELECT user_id, name, shortcode FROM collection")
        }
        self.assertEqual(rows, {(A, "Cars", "ONLYA1"), (B, "Food", "SHARED")})


class TestTags(TwoPeople):
    def test_your_tag_is_yours(self):
        tagging.set_user_tag(self.conn, "SHARED", "Weeknight", user_id=A)
        self.assertEqual(tagging.item_tags(self.conn, "SHARED", user_id=A)[0]["name"], "Weeknight")
        self.assertEqual(tagging.item_tags(self.conn, "SHARED", user_id=B), [])

    def test_counts_leave_out_other_peoples_names(self):
        tagging.ensure_taxonomy(self.conn)
        tagging.set_user_tag(self.conn, "SHARED", "Weeknight", user_id=A)
        names = {t["name"] for t in tagging.tag_counts(self.conn, user_id=B)}
        self.assertNotIn("Weeknight", names)
        self.assertIn("Food", names)  # categories are everyone's

    def test_a_name_both_use_stays_until_neither_does(self):
        tagging.set_user_tag(self.conn, "SHARED", "Weeknight", user_id=A)
        tagging.set_user_tag(self.conn, "SHARED", "Weeknight", user_id=B)
        tagging.clear_user_tag(self.conn, "SHARED", user_id=A)
        self.assertIn("Weeknight", {t["name"] for t in tagging.tag_counts(self.conn, user_id=B)})


class TestRecipes(TwoPeople):
    def setUp(self):
        super().setUp()
        recipes.save_user(
            self.conn,
            "SHARED",
            recipes.from_dict(
                {"title": "Nonna's gochujang pasta", "ingredients": [{"item": "gochujang"}]}
            ),
            user_id=A,
        )

    def test_your_correction_is_yours(self):
        self.assertEqual(
            must(recipes.current(self.conn, "SHARED", user_id=A))["title"],
            "Nonna's gochujang pasta",
        )
        self.assertIsNone(recipes.current(self.conn, "SHARED", user_id=B))

    def test_counts_are_of_your_library(self):
        self.assertEqual(recipes.total(self.conn, user_id=A), 1)
        self.assertEqual(recipes.total(self.conn, user_id=B), 0)
        self.assertEqual(recipes.facets(self.conn, user_id=B)["total"], 0)

    def test_search_finds_your_title_and_not_someone_elses(self):
        self.assertEqual(search.rank(self.conn, "gochujang", user_id=A), ["SHARED"])
        self.assertEqual(search.rank(self.conn, "gochujang", user_id=B), [])

    def test_hiding_it_hides_it_from_you_only(self):
        recipes.set_model(self.conn, "ONLYB1", None)
        recipes.save_user(
            self.conn, "SHARED", recipes.from_dict({"title": "x"}), hidden=True, user_id=B
        )
        self.assertEqual(recipes.total(self.conn, user_id=A), 1)


class TestSearch(TwoPeople):
    def test_you_find_only_your_reels(self):
        self.assertEqual(search.rank(self.conn, "ramen", user_id=B), ["ONLYB1"])
        self.assertEqual(search.rank(self.conn, "ramen", user_id=A), [])
        self.assertEqual(search.rank(self.conn, "pasta", user_id=A), ["SHARED"])

    def test_not_by_creator_or_shortcode_either(self):
        self.assertEqual(search.rank(self.conn, "ONLYB", user_id=A), [])

    def test_your_own_tag_finds_your_reel(self):
        tagging.set_user_tag(self.conn, "ONLYA1", "Brakes", user_id=A)
        self.assertIn("ONLYA1", search.rank(self.conn, "brakes", user_id=A))

    @unittest.skipUnless(HAS_VEC, "sqlite-vec not installed")
    def test_the_vector_cap_is_spent_on_your_library(self):
        self.assertTrue(search.load_vec(self.conn))
        near = pack([1.0, 0.0, 0.1])
        for i in range(search.MAX_VECTOR_HITS + 5):
            code = f"B{i:05d}"
            self.conn.execute("INSERT INTO item (shortcode, permalink) VALUES (?, 'p')", (code,))
            self.conn.execute("INSERT INTO user_item (user_id, shortcode) VALUES (?, ?)", (B, code))
            self.conn.execute("INSERT INTO item_vec VALUES (?, 'd', ?)", (code, near))
        self.conn.execute(
            "INSERT INTO item_vec VALUES ('ONLYA1', 'd', ?)", (pack([0.9, 0.1, 0.1]),)
        )
        hits = [code for code, _ in search.vector_hits(self.conn, near, A)]
        self.assertEqual(hits, ["ONLYA1"])


class TestRemove(TwoPeople):
    def remove(self, user_id, code):
        self.conn.execute("BEGIN")
        removed = library.remove(self.conn, user_id, code)
        self.conn.execute("COMMIT")
        return removed

    def test_a_reel_someone_else_saved_stays_for_them(self):
        tagging.set_user_tag(self.conn, "SHARED", "Weeknight", user_id=B)
        removed = self.remove(A, "SHARED")
        self.assertFalse(removed.purged)
        self.assertNotIn("SHARED", self.library(A))
        self.assertIn("SHARED", self.library(B))
        self.assertEqual(tagging.item_tags(self.conn, "SHARED", user_id=B)[0]["name"], "Weeknight")
        self.assertEqual(search.rank(self.conn, "pasta", user_id=A), [])
        self.assertEqual(search.rank(self.conn, "pasta", user_id=B), ["SHARED"])

    def test_takes_your_collections_and_recipe_with_it(self):
        recipes.save_user(self.conn, "ONLYA1", recipes.from_dict({"title": "brakes"}), user_id=A)
        self.remove(A, "ONLYA1")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM collection WHERE user_id = ?", (A,)).fetchone()[
                0
            ],
            0,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM recipe_fts WHERE shortcode = 'ONLYA1'"
            ).fetchone()[0],
            0,
        )

    def test_the_last_one_out_deletes_the_reel_and_its_media(self):
        media = self.dir / "media"
        (media / "thumbs").mkdir(parents=True)
        (media / "thumbs" / "SHARED.jpg").write_bytes(b"jpg")
        self.conn.execute(
            "UPDATE item SET thumb_path = 'thumbs/SHARED.jpg' WHERE shortcode = 'SHARED'"
        )
        self.conn.execute("INSERT INTO item_slide VALUES ('SHARED', 0, 'slides/SHARED-1.jpg')")
        self.assertFalse(self.remove(A, "SHARED").purged)
        removed = self.remove(B, "SHARED")
        self.assertTrue(removed.purged)
        self.assertEqual(removed.files, ["thumbs/SHARED.jpg", "slides/SHARED-1.jpg"])
        self.assertIsNone(
            self.conn.execute("SELECT 1 FROM item WHERE shortcode = 'SHARED'").fetchone()
        )
        library.delete_files(media, removed.files)
        self.assertFalse((media / "thumbs" / "SHARED.jpg").exists())

    def test_never_deletes_outside_the_media_root(self):
        outside = self.dir / "precious.txt"
        outside.write_text("keep")
        library.delete_files(self.dir / "media", ["../precious.txt"])
        self.assertTrue(outside.exists())

    def test_a_reel_not_in_your_library_is_none(self):
        self.assertIsNone(self.remove(A, "ONLYB1"))
        self.assertIn("ONLYB1", self.library(B))

    def test_a_reimport_brings_it_back(self):
        self.remove(A, "ONLYA1")
        import_export(self.conn, self.a, user_id=A)
        self.assertIn("ONLYA1", self.library(A))


# A library from before #12, as far as this migration cares.
BEFORE = """
DROP TABLE user_item;
DROP TABLE collection;
DROP TABLE recipe_ingredient;
DROP TABLE recipe;
DROP TABLE recipe_fts;
CREATE TABLE collection (
  name TEXT NOT NULL,
  shortcode TEXT NOT NULL REFERENCES item(shortcode) ON DELETE CASCADE,
  PRIMARY KEY (name, shortcode));
CREATE TABLE recipe (
  shortcode TEXT NOT NULL REFERENCES item(shortcode) ON DELETE CASCADE,
  source TEXT NOT NULL, title TEXT, servings INTEGER, time_minutes INTEGER,
  steps TEXT NOT NULL DEFAULT '[]', incomplete INTEGER NOT NULL DEFAULT 0,
  hidden INTEGER NOT NULL DEFAULT 0, confidence REAL, source_fields TEXT, dropped TEXT,
  updated_at INTEGER NOT NULL, PRIMARY KEY (shortcode, source));
CREATE TABLE recipe_ingredient (
  shortcode TEXT NOT NULL, source TEXT NOT NULL, position INTEGER NOT NULL,
  item TEXT NOT NULL, quantity TEXT, unit TEXT, note TEXT, as_written TEXT,
  PRIMARY KEY (shortcode, source, position),
  FOREIGN KEY (shortcode, source) REFERENCES recipe(shortcode, source) ON DELETE CASCADE);
CREATE VIEW recipe_now AS SELECT * FROM recipe r WHERE r.source = 'user' OR NOT EXISTS (
  SELECT 1 FROM recipe u WHERE u.shortcode = r.shortcode AND u.source = 'user');
CREATE VIRTUAL TABLE recipe_fts USING fts5(shortcode UNINDEXED, title, ingredients);

INSERT INTO collection VALUES ('Cars', 'CAR001');
INSERT INTO recipe (shortcode, source, title, updated_at) VALUES
  ('SOUP01', 'model', 'Potato soup', 0), ('SOUP01', 'user', 'Gran''s leek soup', 0);
INSERT INTO recipe_ingredient (shortcode, source, position, item) VALUES
  ('SOUP01', 'model', 0, 'potato'), ('SOUP01', 'user', 0, 'leek');
INSERT INTO recipe_fts VALUES ('SOUP01', 'Gran''s leek soup', 'leek');
INSERT INTO tag (name, kind) VALUES ('Weeknight', 'user');
INSERT INTO item_tag (shortcode, tag_id, source)
  SELECT 'SOUP01', id, 'user' FROM tag WHERE name = 'Weeknight';
UPDATE item SET primary_tag = 'Weeknight' WHERE shortcode = 'SOUP01';
"""


class TestMigration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.conn = open_db(self.dir / "t.sqlite3")
        zp = write_zip(
            self.dir / "e.zip",
            saved_posts(
                [
                    post_record(
                        R + "SOUP01/",
                        timestamp=300,
                        username="x",
                        caption="soup",
                        hashtags=("recipe",),
                    ),
                    post_record(R + "CAR001/", timestamp=200, username="y", caption="ae86"),
                ]
            ),
        )
        import_export(self.conn, zp)
        tagging.apply_hashtags(self.conn)
        self.conn.executescript(BEFORE)
        init(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_the_library_is_user_ones(self):
        rows = {
            tuple(r)
            for r in self.conn.execute(
                "SELECT user_id, shortcode, saved_at, first_seen_in FROM user_item"
            )
        }
        self.assertEqual(rows, {(1, "SOUP01", 300, 1), (1, "CAR001", 200, 1)})
        self.assertEqual(
            tuple(
                self.conn.execute("SELECT username, is_admin, password_hash FROM user").fetchone()
            ),
            ("local", 1, None),
        )

    def test_your_tag_moves_out_of_the_shared_row(self):
        self.assertEqual(tagging.item_tags(self.conn, "SOUP01")[0]["name"], "Weeknight")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM item_tag WHERE source = 'user'").fetchone()[0],
            0,
        )
        self.assertEqual(
            self.conn.execute("SELECT primary_tag FROM item WHERE shortcode = 'SOUP01'").fetchone()[
                0
            ],
            "Food",
        )

    def test_collections_and_recipes_keep_their_rows(self):
        self.assertEqual(
            [tuple(r) for r in self.conn.execute("SELECT * FROM collection")],
            [(1, "Cars", "CAR001")],
        )
        self.assertEqual(must(recipes.current(self.conn, "SOUP01"))["title"], "Gran's leek soup")
        self.assertEqual(
            must(recipes.current(self.conn, "SOUP01", user_id=2))["title"], "Potato soup"
        )

    def test_recipe_search_is_rebuilt(self):
        self.assertEqual(search.rank(self.conn, "leek"), ["SOUP01"])
        self.assertEqual(search.rank(self.conn, "potato"), [])  # yours is shown

    def test_it_runs_once(self):
        tagging.set_user_tag(self.conn, "CAR001", "Travel")
        init(self.conn)
        self.assertEqual(tagging.item_tags(self.conn, "CAR001")[0]["name"], "Travel")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM user_item").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
