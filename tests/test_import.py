import tempfile
import unittest
from pathlib import Path

from app.db import open_db
from app.importer.service import ImportError_, import_export
from app.tagging.service import item_tags, set_user_tag
from tests.fixtures import post_record, saved_collections, saved_posts, write_zip

R = "https://www.instagram.com/reel/"


class ImportCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.conn = open_db(self.dir / "test.sqlite3")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def make_zip(self, name, entries, groups=None):
        """entries: (author, permalink, saved_at) -> real-shape records."""
        records = [
            post_record(href, timestamp=ts, username=author, caption=f"caption for {href}")
            for author, href, ts in entries
        ]
        groups_rec = None
        if groups is not None:
            groups_rec = {
                name_: [post_record(h, timestamp=1, username="x") for h in hrefs]
                for name_, hrefs in groups.items()
            }
        return write_zip(
            self.dir / name,
            saved_posts(records),
            saved_collections(groups_rec) if groups_rec is not None else None,
        )

    def count(self, table):
        return self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


class TestIdempotency(ImportCase):
    """Phase 00 is judged on this: the same export twice changes zero rows."""

    def test_reimporting_the_same_export_changes_nothing(self):
        zp = self.make_zip(
            "export.zip",
            [
                ("bay7.garage", R + "AAA111/", 1710000000),
                ("ramen.lab", R + "BBB222/", 1720000000),
            ],
            {"Cars": [R + "AAA111/"]},
        )

        first = import_export(self.conn, zp)
        self.assertEqual(first.inserted, 2)
        self.assertEqual(first.collections, 1)

        second = import_export(self.conn, zp)
        self.assertEqual(second.inserted, 0)
        self.assertEqual(second.updated, 0)
        self.assertEqual(second.unchanged, 2)
        self.assertEqual(second.collections, 0)
        self.assertTrue(second.already_imported)
        self.assertEqual(self.count("item"), 2)

    def test_reimport_never_resets_enrichment(self):
        zp = self.make_zip("export.zip", [("bay7.garage", R + "AAA111/", 1710000000)])
        import_export(self.conn, zp)

        # Pretend phases 01-03 have run against this row.
        self.conn.execute(
            """UPDATE item SET state='ready', transcript='you do not need a press',
                   summary='brake pads', tag_v=3, asr_v=1, media_path='media/AA/AAA111.mp4'
               WHERE shortcode='AAA111'"""
        )

        import_export(self.conn, zp)

        row = self.conn.execute(
            "SELECT state, transcript, tag_v, asr_v, media_path FROM item WHERE shortcode='AAA111'"
        ).fetchone()
        self.assertEqual(row["state"], "ready")
        self.assertEqual(row["transcript"], "you do not need a press")
        self.assertEqual(row["tag_v"], 3)
        self.assertEqual(row["asr_v"], 1)
        self.assertEqual(row["media_path"], "media/AA/AAA111.mp4")

    def test_user_tags_survive_reimport(self):
        zp = self.make_zip("export.zip", [("a", R + "AAA111/", 1)])
        import_export(self.conn, zp)
        set_user_tag(self.conn, "AAA111", "Brakes")
        import_export(self.conn, zp)
        self.assertEqual(item_tags(self.conn, "AAA111")[0]["name"], "Brakes")


class TestIncremental(ImportCase):
    def test_second_export_adds_only_new_reels(self):
        first = self.make_zip("first.zip", [("a", R + "AAA111/", 100)])
        import_export(self.conn, first)

        second = self.make_zip(
            "second.zip",
            [
                ("a", R + "AAA111/", 100),
                ("b", R + "BBB222/", 200),
            ],
        )
        stats = import_export(self.conn, second)

        self.assertEqual(stats.inserted, 1)
        self.assertEqual(stats.unchanged, 1)
        self.assertEqual(self.count("item"), 2)

    def test_earliest_save_wins(self):
        later = self.make_zip("later.zip", [("a", R + "AAA111/", 999)])
        import_export(self.conn, later)

        earlier = self.make_zip("earlier.zip", [("a", R + "AAA111/", 111)])
        stats = import_export(self.conn, earlier)

        self.assertEqual(stats.updated, 1)
        saved_at = self.conn.execute(
            "SELECT saved_at FROM item WHERE shortcode='AAA111'"
        ).fetchone()[0]
        self.assertEqual(saved_at, 111)

    def test_a_later_export_does_not_push_saved_at_forward(self):
        import_export(self.conn, self.make_zip("a.zip", [("a", R + "AAA111/", 111)]))
        import_export(self.conn, self.make_zip("b.zip", [("a", R + "AAA111/", 999)]))
        saved_at = self.conn.execute(
            "SELECT saved_at FROM item WHERE shortcode='AAA111'"
        ).fetchone()[0]
        self.assertEqual(saved_at, 111)

    def test_missing_author_is_filled_in_later(self):
        import_export(self.conn, self.make_zip("a.zip", [(None, R + "AAA111/", 111)]))
        stats = import_export(
            self.conn, self.make_zip("b.zip", [("bay7.garage", R + "AAA111/", 111)])
        )
        self.assertEqual(stats.updated, 1)
        author = self.conn.execute("SELECT author FROM item WHERE shortcode='AAA111'").fetchone()[0]
        self.assertEqual(author, "bay7.garage")


class TestCollections(ImportCase):
    def test_adopts_members_missing_from_saved_posts(self):
        zp = self.make_zip(
            "export.zip",
            [("a", R + "AAA111/", 100)],
            {"Cars": [R + "AAA111/", R + "GHOST9/"]},
        )
        stats = import_export(self.conn, zp)
        self.assertEqual(stats.inserted, 2)
        self.assertEqual(self.count("collection"), 2)
        row = self.conn.execute(
            "SELECT permalink, author FROM item WHERE shortcode='GHOST9'"
        ).fetchone()
        self.assertIn("GHOST9", row["permalink"])
        # Collections embed the record, so an adopted member keeps its creator.
        self.assertEqual(row["author"], "x")

    def test_one_reel_can_be_in_several_collections(self):
        zp = self.make_zip(
            "export.zip",
            [("a", R + "AAA111/", 100)],
            {"Cars": [R + "AAA111/"], "Watch later": [R + "AAA111/"]},
        )
        import_export(self.conn, zp)
        names = [
            r[0]
            for r in self.conn.execute(
                "SELECT name FROM collection WHERE shortcode='AAA111' ORDER BY name"
            )
        ]
        self.assertEqual(names, ["Cars", "Watch later"])


class TestSources(ImportCase):
    def test_reads_an_unpacked_directory(self):
        import json

        root = self.dir / "unpacked" / "your_instagram_activity" / "saved"
        root.mkdir(parents=True)
        (root / "saved_posts.json").write_text(
            json.dumps(saved_posts([post_record(R + "AAA111/", timestamp=1)])),
            encoding="utf-8",
        )
        stats = import_export(self.conn, self.dir / "unpacked")
        self.assertEqual(stats.inserted, 1)

    def test_export_without_saved_posts_is_an_error(self):
        import zipfile

        bad = self.dir / "bad.zip"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("readme.txt", "nothing here")
        with self.assertRaises(ImportError_):
            import_export(self.conn, bad)

    def test_a_failed_import_leaves_no_partial_rows(self):
        import zipfile

        bad = self.dir / "bad.zip"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("readme.txt", "nothing here")
        with self.assertRaises(ImportError_):
            import_export(self.conn, bad)
        self.assertEqual(self.count("item"), 0)


if __name__ == "__main__":
    unittest.main()
