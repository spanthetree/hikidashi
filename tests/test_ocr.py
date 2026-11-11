import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.asr.transcriber import ASR_V
from app.db import open_db
from app.importer.service import import_export
from app.ocr import reader, worker
from app.ocr.reader import (
    OCR_V,
    Line,
    OcrError,
    OcrUnavailable,
    merge,
    parse_tsv,
    read_image,
    read_video,
)
from app.tagging import llm
from app.tagging import worker as tagger
from app.tagging.llm import build_prompt
from tests.fixtures import must, post_record, saved_posts, write_zip

R = "https://www.instagram.com/reel/"
P = "https://www.instagram.com/p/"

HEAD = (
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
)


def tsv(*lines: list[tuple[str, float]]) -> str:
    """Tesseract TSV with one line per argument, each a list of (word, conf)."""
    rows = [HEAD]
    for n, words in enumerate(lines, 1):
        rows.append(f"4\t1\t1\t1\t{n}\t0\t0\t0\t0\t0\t-1\t")
        for i, (w, c) in enumerate(words, 1):
            rows.append(f"5\t1\t1\t1\t{n}\t{i}\t0\t0\t10\t10\t{c}\t{w}")
    return "\n".join(rows) + "\n"


class TestParseTsv(unittest.TestCase):
    def test_keeps_confident_words_in_reading_order(self):
        out = parse_tsv(tsv([("DATES", 96), ("THAT", 96)], [("EVERY", 95), ("DAD", 94)]))
        self.assertEqual([line.text for line in out], ["DATES THAT", "EVERY DAD"])
        self.assertAlmostEqual(out[0].conf, 96)

    def test_drops_low_confidence_words(self):
        out = parse_tsv(tsv([("Opening", 96), ("j2", 0), ("September", 90)]))
        self.assertEqual([line.text for line in out], ["Opening September"])

    def test_drops_punctuation_only_words(self):
        out = parse_tsv(tsv([("©", 96), ("Vancouver", 91), ("|", 88)]))
        self.assertEqual([line.text for line in out], ["Vancouver"])

    def test_a_line_needs_a_real_word(self):
        """A lone number or a two-letter fragment is texture, not text."""
        out = parse_tsv(
            tsv(
                [("10", 95)],
                [("of", 90)],
                [("027]", 80)],
                [("You", 96), ("wanna", 96), ("go?", 95)],
            )
        )
        self.assertEqual([line.text for line in out], ["You wanna go?"])

    def test_no_text_is_empty(self):
        self.assertEqual(parse_tsv(HEAD + "\n"), [])
        self.assertEqual(parse_tsv(""), [])


class TestMerge(unittest.TestCase):
    def test_a_caption_held_on_screen_is_kept_once(self):
        a = [Line("If I was moving to Austin, Texas and wanted", 92)]
        b = [Line("If |was moving to Austin, Texas and wanted", 88)]
        self.assertEqual(merge([a, b, a]), "If I was moving to Austin, Texas and wanted")

    def test_the_fuller_reading_wins(self):
        out = merge([[Line("CAPE BRE", 96)], [Line("CAPE BRETON", 91)]])
        self.assertEqual(out, "CAPE BRETON")

    def test_different_lines_keep_their_first_order(self):
        out = merge([[Line("TOP 5 NYC", 95)], [Line("Pancakes", 93), Line("TOP 5 NYC", 90)]])
        self.assertEqual(out, "TOP 5 NYC\nPancakes")

    def test_nothing_is_none(self):
        self.assertIsNone(merge([]))
        self.assertIsNone(merge([[], []]))


def _proc(returncode=0, stdout=b"", stderr=b""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class TestSubprocesses(unittest.TestCase):
    def test_recognise_runs_single_threaded_tesseract_for_tsv(self):
        with mock.patch.object(
            subprocess, "run", return_value=_proc(stdout=tsv([("Hello", 95)]).encode())
        ) as run:
            self.assertEqual(reader.recognise(b"png"), [Line("Hello", 95)])
        argv, kw = run.call_args.args[0], run.call_args.kwargs
        self.assertEqual((argv[0], argv[-1]), ("tesseract", "tsv"))
        self.assertEqual(kw["env"]["OMP_THREAD_LIMIT"], "1")

    def test_missing_tesseract_stops_the_run(self):
        with mock.patch.object(subprocess, "run", side_effect=FileNotFoundError):
            with self.assertRaises(OcrUnavailable):
                reader.recognise(b"png")

    def test_a_bad_image_fails_only_the_item(self):
        with mock.patch.object(
            subprocess, "run", return_value=_proc(1, stderr=b"Error in pixReadMem\n")
        ):
            with self.assertRaisesRegex(OcrError, "pixReadMem"):
                reader.recognise(b"nope")

    def test_frames_are_taken_through_the_video_after_the_cover(self):
        with tempfile.TemporaryDirectory() as d:
            cover = Path(d) / "c.jpg"
            cover.write_bytes(b"cover")
            seen = []

            def recognise(img):
                seen.append(img)
                return []

            with (
                mock.patch.object(
                    reader, "grab_frame", side_effect=lambda p, at: f"{at:.0f}".encode()
                ),
                mock.patch.object(reader, "recognise", side_effect=recognise),
            ):
                self.assertIsNone(read_video(Path("v.mp4"), 50, cover))
        self.assertEqual(seen, [b"cover", b"5", b"15", b"25", b"35", b"45"])

    def test_an_unknown_length_is_probed(self):
        with mock.patch("app.enrich.fetcher.probe_duration", return_value=None):
            with self.assertRaisesRegex(OcrError, "duration"):
                read_video(Path("v.mp4"), None)

    def test_a_missing_image_fails_the_item(self):
        with self.assertRaises(OcrError):
            read_image(Path("/nonexistent/x.jpg"))


@unittest.skipUnless(
    shutil.which("tesseract") and shutil.which("ffmpeg"), "tesseract or ffmpeg not installed"
)
class TestForReal(unittest.TestCase):
    """The TSV layout and the frame pipe are tesseract's and ffmpeg's to define."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, name, seconds, text=None):
        out = self.dir / name
        vf = ["-vf", f"drawtext=text='{text}':fontsize=64:x=40:y=120"] if text else []
        proc = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=white:s=1280x320",
                *vf,
                "-t",
                str(seconds),
                *(["-frames:v", "1"] if name.endswith(".png") else []),
                str(out),
            ],
            capture_output=True,
        )
        if proc.returncode != 0:
            self.skipTest(f"ffmpeg cannot draw text here: {proc.stderr[:80]!r}")
        return out

    def test_reads_text_burned_into_a_video(self):
        mp4 = self.make("t.mp4", 2, "BRAKE COOLING GUIDE")
        self.assertEqual(read_video(mp4, 2), "BRAKE COOLING GUIDE")

    def test_reads_an_image(self):
        png = self.make("t.png", 1, "TOKYO RAMEN")
        self.assertEqual(read_image(png), "TOKYO RAMEN")

    def test_a_blank_frame_has_no_text(self):
        self.assertIsNone(read_video(self.make("b.mp4", 2), 2))


class OcrCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.media = self.dir / "media"
        self.conn = open_db(self.dir / "t.sqlite3")
        zp = write_zip(
            self.dir / "e.zip",
            saved_posts(
                [
                    post_record(P + "PHOTO1/", timestamp=500, caption="PHOTO1 every dad"),
                    post_record(R + "SILENT/", timestamp=400, caption="SILENT this is texas"),
                    post_record(R + "SPOKEN/", timestamp=300, caption="SPOKEN brake job"),
                    post_record(R + "NOASR1/", timestamp=200, caption="NOASR1 not yet"),
                    post_record(R + "GONE01/", timestamp=100, caption="GONE01 deleted"),
                ]
            ),
        )
        import_export(self.conn, zp)
        # As phases 01 and 02 leave them.
        self.conn.executescript(f"""
            UPDATE item SET state = 'fetched', thumb_path = 'thumbs/' || shortcode || '.jpg';
            UPDATE item SET media_path = 'video/' || shortcode || '.mp4', duration = 30
              WHERE shortcode != 'PHOTO1';
            UPDATE item SET asr_v = {ASR_V} WHERE shortcode IN ('SILENT', 'SPOKEN');
            UPDATE item SET transcript = 'pads and rotors' WHERE shortcode = 'SPOKEN';
            UPDATE item SET state = 'failed' WHERE shortcode = 'GONE01';
        """)
        self.read: dict[str, str | Exception | None] = {
            "PHOTO1": "DATES THAT EVERY DAD",
            "SILENT": "Opening September",
        }

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def fake(self, row, media_dir):
        answer = self.read.get(row["shortcode"])
        if isinstance(answer, Exception):
            raise answer
        return answer

    def run_ocr(self, **kw):
        return worker.ocr_all(self.conn, self.media, reader=self.fake, **kw)

    def row(self, code) -> sqlite3.Row:
        return self.conn.execute("SELECT * FROM item WHERE shortcode = ?", (code,)).fetchone()

    def search(self, phrase) -> list[str]:
        return [
            r[0]
            for r in self.conn.execute(
                """SELECT shortcode FROM item WHERE rowid IN
                 (SELECT rowid FROM item_fts WHERE item_fts MATCH ?)""",
                (f'"{phrase}"',),
            )
        ]


class TestPending(OcrCase):
    def test_photo_posts_and_reels_with_no_speech_only(self):
        """A reel with speech has its words; one not transcribed yet may too."""
        self.assertEqual([r["shortcode"] for r in worker.pending(self.conn)], ["PHOTO1", "SILENT"])

    def test_a_reel_is_read_once_its_transcription_finds_nothing(self):
        self.conn.execute(f"UPDATE item SET asr_v = {ASR_V} WHERE shortcode = 'NOASR1'")
        self.assertIn("NOASR1", [r["shortcode"] for r in worker.pending(self.conn)])


class TestOcrAll(OcrCase):
    def test_writes_and_stamps(self):
        p = self.run_ocr()
        self.assertEqual((p.total, p.text, p.empty, p.failed), (2, 2, 0, 0))
        self.assertEqual(self.row("PHOTO1")["ocr_text"], "DATES THAT EVERY DAD")
        self.assertEqual(self.row("PHOTO1")["ocr_v"], OCR_V)
        self.assertEqual(worker.pending(self.conn), [])

    def test_text_on_screen_is_searchable(self):
        self.assertEqual(self.search("dates that"), [])
        self.run_ocr()
        self.assertEqual(self.search("dates that"), ["PHOTO1"])

    def test_no_text_is_stamped_and_not_read_again(self):
        self.read["SILENT"] = None
        p = self.run_ocr()
        self.assertEqual(p.empty, 1)
        self.assertIsNone(self.row("SILENT")["ocr_text"])
        self.assertEqual(self.row("SILENT")["ocr_v"], OCR_V)
        self.assertEqual(self.run_ocr().total, 0)

    def test_an_error_is_recorded_unstamped_and_retried(self):
        self.read["SILENT"] = OcrError("ffmpeg timed out")
        self.assertEqual(self.run_ocr().failed, 1)
        row = self.row("SILENT")
        self.assertEqual(
            (row["fail_stage"], row["fail_reason"], row["ocr_v"]), ("ocr", "ffmpeg timed out", 0)
        )
        self.assertEqual(row["state"], "fetched")  # the reel is still fine

        self.read["SILENT"] = "Opening September"
        self.run_ocr()
        row = self.row("SILENT")
        self.assertEqual((row["fail_stage"], row["ocr_text"]), (None, "Opening September"))

    def test_missing_tesseract_stops_the_run_without_marking_anything(self):
        self.read["PHOTO1"] = OcrUnavailable("tesseract not runnable")
        with self.assertRaises(OcrUnavailable):
            self.run_ocr(workers=1)
        self.assertIsNone(self.row("PHOTO1")["fail_stage"])
        self.assertEqual(self.row("PHOTO1")["ocr_v"], 0)

    def test_new_text_sends_an_item_back_to_the_tagger(self):
        self.conn.execute("UPDATE item SET tag_v = ?", (llm.TAG_V,))
        self.read["SILENT"] = None
        self.run_ocr()
        self.assertEqual(self.row("PHOTO1")["tag_v"], 0)
        self.assertEqual(self.row("SILENT")["tag_v"], llm.TAG_V)  # nothing new

        self.conn.execute("UPDATE item SET tag_v = ?", (llm.TAG_V,))
        self.run_ocr(force=True)  # same text again
        self.assertEqual(self.row("PHOTO1")["tag_v"], llm.TAG_V)

    def test_a_video_arriving_later_is_read_again(self):
        from app.enrich import worker as enrich

        self.run_ocr()
        with (
            mock.patch.object(enrich, "download_media", return_value=10),
            mock.patch.object(enrich, "probe_duration", return_value=5),
            mock.patch.object(
                enrich, "pending_media", return_value=[{**self.row("PHOTO1"), "carousel": 0}]
            ),
        ):
            enrich.fetch_media(self.conn, self.media, concurrency=1, delay=0)
        self.assertEqual(self.row("PHOTO1")["ocr_v"], 0)

    def test_summary(self):
        self.read["SILENT"] = None
        self.run_ocr()
        s = worker.summary(self.conn)
        self.assertEqual((s["needed"], s["text"], s["empty"], s["waiting"]), (2, 1, 1, 0))


class TestTaggerReadsIt(OcrCase):
    def test_the_prompt_carries_the_text_on_screen(self):
        p = build_prompt("cap", [], None, "DATES THAT EVERY DAD")
        self.assertIn("DATES THAT EVERY DAD", must(p))

    def test_on_screen_words_make_a_thin_caption_worth_sending(self):
        self.assertIsNone(build_prompt("Find your @happyplace 🌍", [], None))
        self.assertIsNotNone(build_prompt("Find your @happyplace 🌍", [], None, "You wanna go?"))

    def test_the_tagger_waits_for_ocr_then_reads_it(self):
        waiting = {r["shortcode"] for r in tagger.pending(self.conn)}
        self.assertEqual(waiting & {"PHOTO1", "SILENT"}, set())
        self.assertEqual(tagger.summary(self.conn)["waiting_ocr"], 2)

        self.run_ocr()
        rows = {r["shortcode"]: r for r in tagger.pending(self.conn)}
        self.assertEqual(rows["PHOTO1"]["ocr_text"], "DATES THAT EVERY DAD")

    def test_a_failed_read_does_not_hold_up_tagging(self):
        self.read["PHOTO1"] = OcrError("bad image")
        self.run_ocr()
        self.assertIn("PHOTO1", [r["shortcode"] for r in tagger.pending(self.conn)])


if __name__ == "__main__":
    unittest.main()
