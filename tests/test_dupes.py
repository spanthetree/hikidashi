"""Reposts and duplicate saves (#58): fingerprints, grouping, splitting."""

import shutil
import tempfile
import unittest
from pathlib import Path

from app import dupes
from app.db import open_db
from app.importer.service import import_export
from tests.fixtures import post_record, saved_posts, write_zip

R = "https://www.instagram.com/reel/"
SAME = 0x0F0F_0F0F_0F0F_0F0F


class TestHash(unittest.TestCase):
    def test_brighter_than_the_right_hand_neighbour(self):
        self.assertEqual(dupes.dhash(bytes([9, 8, 7, 6, 5, 4, 3, 2, 1] * 8)), (1 << 64) - 1)
        self.assertEqual(dupes.dhash(bytes(range(9)) * 8), 0)
        with self.assertRaises(ValueError):
            dupes.dhash(b"short")

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
    def test_a_real_video_has_three_frames(self):
        reel = Path(__file__).parent / "assets" / "reel.mp4"
        self.assertEqual(len(dupes.fingerprint(reel, 2.0)), 3)


class TestGroups(unittest.TestCase):
    """Four reels: AAA and BBB the same video (BBB a repost, with a new
    caption), CCC the same picture with another voiceover, DDD another."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        self.conn = open_db(d / "t.sqlite3")
        posts = [
            post_record(R + c + "/", timestamp=ts, caption=c)
            for c, ts in (("AAA", 100), ("BBB", 200), ("CCC", 300), ("DDD", 400))
        ]
        import_export(self.conn, write_zip(d / "e.zip", saved_posts(posts)))
        self.conn.execute(
            "UPDATE item SET media_path = shortcode || '.mp4', duration = 30, transcript = ?",
            ("salt the water then cook the pasta until it is just right",),
        )
        self.conn.execute(
            "UPDATE item SET transcript = ? WHERE shortcode = 'CCC'",
            ("a completely different voice over about brake pads and rotors today",),
        )
        frames = {"AAA": SAME, "BBB": SAME ^ 0b111, "CCC": SAME, "DDD": ~SAME & (1 << 64) - 1}
        dupes.hash_all(self.conn, d, hasher=lambda p, _: [frames[p.stem]] * 3)

    def dup_of(self):
        return dict(self.conn.execute("SELECT shortcode, dup_of FROM item"))

    def test_a_repost_is_grouped_under_the_first_saved(self):
        self.assertEqual(self.dup_of(), {"AAA": None, "BBB": "AAA", "CCC": None, "DDD": None})
        self.assertEqual(dupes.copies(self.conn, "BBB", 1), ["AAA"])
        self.assertEqual(dupes.copies(self.conn, "AAA", 1), ["BBB"])
        self.assertEqual(dupes.copies(self.conn, "DDD", 1), [])

    def test_not_the_same_is_remembered(self):
        dupes.split(self.conn, "BBB", "AAA")
        self.assertEqual(self.dup_of()["BBB"], None)
        dupes.group(self.conn)
        self.assertEqual(self.dup_of()["BBB"], None)

    def test_a_different_length_is_not_a_copy(self):
        self.conn.execute("UPDATE item SET duration = 45 WHERE shortcode = 'BBB'")
        dupes.group(self.conn)
        self.assertIsNone(self.dup_of()["BBB"])

    def test_already_fingerprinted_is_not_pending(self):
        self.assertEqual(dupes.pending(self.conn), [])


if __name__ == "__main__":
    unittest.main()
