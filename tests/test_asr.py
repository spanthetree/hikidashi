import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.asr import worker
from app.asr.transcriber import (
    ASR_V,
    AudioError,
    NoAudio,
    extract_audio,
    is_noise,
    join_segments,
)
from app.db import open_db
from app.importer.service import import_export
from app.profiles import resolve
from app.recipes.llm import RECIPE_V
from app.tagging.llm import TAG_V
from tests.fixtures import post_record, saved_posts, write_zip

R = "https://www.instagram.com/reel/"


class TestProfiles(unittest.TestCase):
    def test_cpu_is_the_small_model_at_int8_with_four_workers(self):
        p = resolve("cpu")
        self.assertEqual(
            (p.name, p.asr_model, p.asr_compute, p.asr_workers),
            ("cpu", "distil-small.en", "int8", 4),
        )
        self.assertGreaterEqual(p.cpu_threads, 1)

    def test_cuda_is_the_large_model_at_float16(self):
        p = resolve("cuda", devices=1)
        self.assertEqual(
            (p.name, p.asr_model, p.asr_compute), ("cuda", "distil-large-v3", "float16")
        )

    def test_auto_follows_the_hardware(self):
        self.assertEqual(resolve("auto", devices=0).name, "cpu")
        self.assertEqual(resolve("auto", devices=2).name, "cuda")
        self.assertEqual(resolve("", devices=0).name, "cpu")
        self.assertEqual(resolve(" CUDA ", devices=1).name, "cuda")

    def test_explicit_cuda_without_a_gpu_refuses_rather_than_falling_back(self):
        with self.assertRaises(ValueError):
            resolve("cuda", devices=0)

    def test_unknown_profile_is_rejected(self):
        with self.assertRaises(ValueError):
            resolve("tpu", devices=0)


class TestJoinSegments(unittest.TestCase):
    def test_joins_and_trims(self):
        self.assertEqual(
            join_segments([" you do not ", "need a press. "]), "you do not need a press."
        )

    def test_nothing_said_is_none_not_empty(self):
        self.assertIsNone(join_segments([]))
        self.assertIsNone(join_segments(["  ", ""]))


class TestIsNoise(unittest.TestCase):
    """Cases from the library and the demo library (#95)."""

    def test_a_word_or_two_on_a_second_of_music(self):
        self.assertTrue(is_noise("Thank you.", 0.9))
        self.assertTrue(is_noise("Okay.", 1.8))
        self.assertTrue(is_noise("A song", 2.3))

    def test_one_word_over_a_crowd(self):
        self.assertTrue(is_noise("Alcohol.", 12.9))

    def test_a_run_of_stock_phrases(self):
        text = (
            "Thank you. Thank you. I think that's it. I don't know. I don't know. "
            "I don't think that's it. I think it's good."
        )
        self.assertTrue(is_noise(text, 16.9))

    def test_short_real_speech_stays(self):
        self.assertFalse(is_noise("This is the hotel at Monument Valley.", 2.7))
        self.assertFalse(is_noise("Three, two, one.", 2.8))

    def test_thanks_at_the_end_of_a_real_transcript_stays(self):
        self.assertFalse(is_noise("Fold the dough twice and bake it. Thank you.", 4.0))
        # Half is not more than half: sung lyrics with an "Oh" in them.
        self.assertFalse(is_noise("Oh, sweet and make the flowers.", 2.9))


def _proc(returncode=0, stdout=b"", stderr=b""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class TestExtractAudio(unittest.TestCase):
    def test_returns_the_pcm(self):
        with mock.patch.object(
            subprocess, "run", return_value=_proc(stdout=b"\x01\x00" * 8)
        ) as run:
            self.assertEqual(len(extract_audio(Path("x.mp4"))), 16)
        argv = run.call_args.args[0]
        self.assertIn("16000", argv)
        self.assertEqual(argv[argv.index("-ac") + 1], "1")

    def test_a_video_with_no_audio_stream_is_no_audio(self):
        for err in (
            b"Output file #0 does not contain any stream\n",
            b"Output file does not contain any stream\n",
            b"Stream map '0:a' matches no streams.\n",
        ):
            with mock.patch.object(subprocess, "run", return_value=_proc(1, stderr=err)):
                with self.assertRaises(NoAudio):
                    extract_audio(Path("x.mp4"))

    def test_an_empty_track_is_no_audio(self):
        with mock.patch.object(subprocess, "run", return_value=_proc()):
            with self.assertRaises(NoAudio):
                extract_audio(Path("x.mp4"))

    def test_a_broken_file_is_an_error_with_a_short_reason(self):
        err = b"\n" + b"moov atom not found " * 40
        with mock.patch.object(subprocess, "run", return_value=_proc(1, stderr=err)):
            with self.assertRaises(AudioError) as cm:
                extract_audio(Path("x.mp4"))
        self.assertTrue(str(cm.exception).startswith("moov atom not found"))
        self.assertLessEqual(len(str(cm.exception)), 200)

    def test_missing_ffmpeg_is_an_error(self):
        with mock.patch.object(subprocess, "run", side_effect=FileNotFoundError("ffmpeg")):
            with self.assertRaises(AudioError):
                extract_audio(Path("x.mp4"))


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
class TestExtractAudioForReal(unittest.TestCase):
    """The no-audio check matches ffmpeg's wording, so test it against ffmpeg."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, name, *inputs):
        out = self.dir / name
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", *inputs, "-t", "1", "-shortest", str(out)],
            check=True,
            capture_output=True,
        )
        return out

    def test_a_silent_video_has_no_audio(self):
        mp4 = self.make("silent.mp4", "-f", "lavfi", "-i", "color=c=black:s=64x64")
        with self.assertRaises(NoAudio):
            extract_audio(mp4)

    def test_one_second_of_audio_is_16k_mono_s16(self):
        mp4 = self.make(
            "tone.mp4",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x64",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100",
        )
        pcm = extract_audio(mp4)
        self.assertAlmostEqual(len(pcm) / 2 / 16_000, 1.0, delta=0.1)


class AsrCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.media = self.dir / "media"
        self.conn = open_db(self.dir / "test.sqlite3")
        zp = write_zip(
            self.dir / "e.zip",
            saved_posts(
                [
                    post_record(R + "AAA111/", timestamp=300, caption="brake job"),
                    post_record(R + "BBB222/", timestamp=200, caption="ramen"),
                    post_record(R + "CCC333/", timestamp=100, caption="a song"),
                ]
            ),
        )
        import_export(self.conn, zp)
        # As phase 01 leaves them: fetched, with an mp4 on disk.
        self.conn.execute(
            """UPDATE item SET state = 'fetched', duration = 30,
                   media_path = 'video/' || lower(substr(shortcode, 1, 2)) || '/'
                                || shortcode || '.mp4'"""
        )
        # The worker never reads the file itself; extract_audio is patched, and
        # hands the shortcode through as the "audio" so fakes can tell them apart.
        patch = mock.patch.object(
            worker, "extract_audio", side_effect=lambda p: Path(p).stem.encode()
        )
        patch.start()
        self.addCleanup(patch.stop)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def row(self, code="AAA111") -> sqlite3.Row:
        return self.conn.execute("SELECT * FROM item WHERE shortcode = ?", (code,)).fetchone()

    def run_asr(self, transcribe, **kw):
        return worker.transcribe_all(self.conn, self.media, transcribe, profile="cpu", **kw)

    def search(self, phrase) -> list[str]:
        return [
            r[0]
            for r in self.conn.execute(
                """SELECT shortcode FROM item WHERE rowid IN
                 (SELECT rowid FROM item_fts WHERE item_fts MATCH ?)""",
                (f'"{phrase}"',),
            )
        ]


SPOKEN = {
    "AAA111": "you do not need a press for this",
    "BBB222": "the broth simmers for eighteen hours",
    "CCC333": None,  # music only
}


def fake(pcm: bytes):
    return SPOKEN[pcm.decode()]


class TestTranscribe(AsrCase):
    def test_writes_and_stamps(self):
        result = self.run_asr(fake)
        self.assertEqual((result.transcribed, result.silent, result.failed), (2, 1, 0))
        row = self.row()
        self.assertEqual(row["transcript"], SPOKEN["AAA111"])
        self.assertEqual(row["asr_v"], ASR_V)
        self.assertEqual(row["asr_profile"], "cpu")
        self.assertEqual(row["state"], "transcribed")

    def test_a_phrase_only_spoken_in_a_reel_finds_that_reel(self):
        self.assertEqual(self.search("eighteen hours"), [])
        self.run_asr(fake)
        self.assertEqual(self.search("eighteen hours"), ["BBB222"])
        self.assertEqual(self.search("ramen"), ["BBB222"])  # caption still indexed

    def test_no_speech_is_stamped_and_not_retried(self):
        self.run_asr(fake)
        row = self.row("CCC333")
        self.assertIsNone(row["transcript"])
        self.assertEqual(row["asr_v"], ASR_V)
        self.assertEqual(worker.pending(self.conn), [])

    def test_no_audio_track_is_no_speech_not_a_failure(self):
        def no_audio(p):
            raise NoAudio("no audio track")

        with mock.patch.object(worker, "extract_audio", side_effect=no_audio):
            result = self.run_asr(fake)
        self.assertEqual((result.silent, result.failed), (3, 0))
        self.assertIsNone(self.row()["fail_stage"])

    def test_only_items_with_a_video_are_queued(self):
        self.conn.execute("UPDATE item SET media_path = NULL WHERE shortcode = 'BBB222'")
        self.conn.execute("UPDATE item SET state = 'failed' WHERE shortcode = 'CCC333'")
        self.assertEqual([r["shortcode"] for r in worker.pending(self.conn)], ["AAA111"])

    def test_newest_saves_first(self):
        self.assertEqual(
            [r["shortcode"] for r in worker.pending(self.conn)], ["AAA111", "BBB222", "CCC333"]
        )

    def test_a_later_state_is_not_moved_backwards(self):
        self.conn.execute("UPDATE item SET state = 'ready' WHERE shortcode = 'AAA111'")
        self.run_asr(fake)
        self.assertEqual(self.row()["state"], "ready")

    def test_a_second_run_does_nothing(self):
        self.run_asr(fake)
        calls = mock.Mock(side_effect=fake)
        self.assertEqual(self.run_asr(calls).total, 0)
        calls.assert_not_called()

    def test_force_redoes_everything(self):
        self.run_asr(fake)
        self.assertEqual(self.run_asr(fake, force=True).total, 3)

    def test_a_version_bump_requeues(self):
        self.run_asr(fake)
        with mock.patch.object(worker, "ASR_V", ASR_V + 1):
            self.assertEqual(len(worker.pending(self.conn)), 3)

    def test_parallel_workers_write_every_row(self):
        result = self.run_asr(fake, workers=4)
        self.assertEqual(result.transcribed + result.silent, 3)
        self.assertEqual(self.row("BBB222")["transcript"], SPOKEN["BBB222"])

    def test_a_new_transcript_sends_a_reel_back_to_the_tagger(self):
        self.conn.execute("UPDATE item SET tag_v = ?, recipe_v = ?", (TAG_V, RECIPE_V))
        self.run_asr(fake)
        self.assertEqual((self.row()["tag_v"], self.row()["recipe_v"]), (0, 0))
        silent = self.row("CCC333")  # no words, so nothing new to read
        self.assertEqual((silent["tag_v"], silent["recipe_v"]), (TAG_V, RECIPE_V))

        self.conn.execute("UPDATE item SET tag_v = ?, recipe_v = ?", (TAG_V, RECIPE_V))
        self.run_asr(fake, force=True)  # the same words again
        self.assertEqual((self.row()["tag_v"], self.row()["recipe_v"]), (TAG_V, RECIPE_V))

    def test_a_transcript_that_is_now_noise_sends_a_reel_back_too(self):
        self.run_asr(fake)
        self.conn.execute("UPDATE item SET tag_v = ?, recipe_v = ?", (TAG_V, RECIPE_V))
        self.run_asr(lambda pcm: None, force=True)  # "Thank you." dropped (#95)
        row = self.row()
        self.assertIsNone(row["transcript"])
        self.assertEqual((row["tag_v"], row["recipe_v"]), (0, 0))


class TestFailures(AsrCase):
    def test_an_error_is_recorded_not_stamped_and_retried(self):
        def flaky(pcm):
            if pcm == b"BBB222":
                raise RuntimeError("CUDA out of memory")
            return fake(pcm)

        result = self.run_asr(flaky)
        self.assertEqual(result.failed, 1)
        row = self.row("BBB222")
        self.assertEqual(row["fail_stage"], "asr")
        self.assertIn("out of memory", row["fail_reason"])
        self.assertEqual(row["asr_v"], 0)
        self.assertEqual(row["state"], "fetched")  # the reel is still fine
        self.assertEqual([r["shortcode"] for r in worker.pending(self.conn)], ["BBB222"])

        self.run_asr(fake)
        row = self.row("BBB222")
        self.assertEqual(row["transcript"], SPOKEN["BBB222"])
        self.assertIsNone(row["fail_stage"])
        self.assertIsNone(row["fail_reason"])

    def test_an_audio_error_keeps_ffmpegs_reason(self):
        def broken(p):
            raise AudioError("moov atom not found")

        with mock.patch.object(worker, "extract_audio", side_effect=broken):
            self.run_asr(fake)
        self.assertEqual(self.row()["fail_reason"], "moov atom not found")

    def test_a_fetch_failure_is_not_cleared_by_asr(self):
        self.conn.execute("""UPDATE item SET fail_stage = 'fetch', fail_reason = 'timeout'
                             WHERE shortcode = 'AAA111'""")
        self.run_asr(fake)
        self.assertEqual(self.row()["fail_stage"], "fetch")

    def test_killing_the_run_mid_way_loses_no_completed_transcripts(self):
        def dies_on_the_second(pcm):
            if pcm == b"BBB222":
                raise KeyboardInterrupt
            return fake(pcm)

        with self.assertRaises(KeyboardInterrupt):
            self.run_asr(dies_on_the_second)
        self.assertEqual(self.row("AAA111")["transcript"], SPOKEN["AAA111"])
        self.assertEqual(self.row("BBB222")["asr_v"], 0)

        # Re-running picks up what was left. (CCC333 may or may not have been
        # taken by the worker before the interrupt reached the pool.)
        self.assertIn("BBB222", [r["shortcode"] for r in worker.pending(self.conn)])
        self.run_asr(fake)
        self.assertEqual(self.search("eighteen hours"), ["BBB222"])


class TestSummary(AsrCase):
    def test_counts(self):
        def flaky(pcm):
            if pcm == b"BBB222":
                raise RuntimeError("boom")
            return fake(pcm)

        self.run_asr(flaky)
        s = worker.summary(self.conn)
        self.assertEqual(
            (s["media"], s["transcribed"], s["silent"], s["failed"], s["waiting"]), (3, 1, 1, 1, 1)
        )


if __name__ == "__main__":
    unittest.main()
