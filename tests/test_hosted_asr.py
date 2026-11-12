import dataclasses
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from app.asr import hosted
from app.asr.hosted import Hosted, guard, language_code
from app.asr.worker import record
from app.db import open_db
from app.providers import limits
from app.providers.client import Client, Transcript
from app.providers.keys import Key
from app.providers.presets import PRESETS
from tests.fake_openai import KEY, Fake, verbose


def seg(text, start, end, nsp=0.01, lp=-0.2):
    return {"text": text, "start": start, "end": end, "no_speech_prob": nsp, "avg_logprob": lp}


class TestGuard(unittest.TestCase):
    def test_speech_is_kept(self):
        t = Transcript(
            "", segments=[seg(" first stretch the dough", 0, 3), seg(" then bake", 3, 5)]
        )
        self.assertEqual(guard(t), "first stretch the dough then bake")

    def test_a_song_is_empty(self):
        # Whisper writes lyrics over music, and says it is unsure there is speech.
        t = Transcript(
            "la la love you baby",
            segments=[seg(" la la love you", 0, 10, nsp=0.85), seg(" baby", 10, 20, nsp=0.7)],
        )
        self.assertIsNone(guard(t))

    def test_a_failed_decode_is_dropped(self):
        t = Transcript("", segments=[seg(" words words words words", 0, 30, lp=-1.6)])
        self.assertIsNone(guard(t))

    def test_stock_phrases_are_noise(self):
        t = Transcript("Thank you.", segments=[seg(" Thank you.", 0, 1.0)])
        self.assertIsNone(guard(t))

    def test_without_segments_only_is_noise_applies(self):
        self.assertIsNone(guard(Transcript("you", duration=1.0)))
        self.assertEqual(
            guard(Transcript("first stretch the dough then bake it", duration=4.0)),
            "first stretch the dough then bake it",
        )
        self.assertIsNone(guard(Transcript("  ", duration=4.0)))

    def test_language_code(self):
        self.assertEqual(language_code("english"), "en")
        self.assertEqual(language_code("Japanese"), "ja")
        self.assertEqual(language_code("ja"), "ja")
        self.assertEqual(language_code("klingon"), "klingon")
        self.assertIsNone(language_code(None))


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
class TestHostedTranscription(unittest.TestCase):
    def setUp(self):
        self.fake = Fake().start()
        self.addCleanup(self.fake.stop)
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        self.mp4 = self.dir / "reel.mp4"
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-y",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
                "-f", "lavfi", "-i", "color=c=black:s=64x64:d=6",
                "-shortest", str(self.mp4),
            ],
            check=True,
        )  # fmt: skip

    def asr(self, preset="openai", model="whisper-1", **kw) -> Hosted:
        p = dataclasses.replace(PRESETS[preset], **kw) if kw else PRESETS[preset]
        limiter = limits.Limiter(in_flight=2, per_minute=1000)
        return Hosted(Client(p, Key(KEY), base_url=self.fake.url, limiter=limiter), model)

    def test_uploads_flac_and_asks_for_segments(self):
        heard = self.asr()(self.mp4, 6.0)
        self.assertEqual(heard.text, self.fake.transcript)
        self.assertEqual(heard.language, "en")
        req = self.fake.seen("/audio/transcriptions")[0]
        self.assertIn(b"fLaC", req.body)
        self.assertEqual(req.form()["response_format"], "verbose_json")
        self.assertEqual(self.asr().label, "openai:whisper-1")

    def test_music_comes_back_empty(self):
        self.fake.no_speech_prob = 0.9
        self.assertIsNone(self.asr()(self.mp4, 6.0).text)

    def test_a_model_without_segments(self):
        heard = self.asr(model="gpt-4o-mini-transcribe")(self.mp4, 6.0)
        self.assertEqual(heard.text, self.fake.transcript)
        self.assertIsNone(heard.language)
        self.assertEqual(
            self.fake.seen("/audio/transcriptions")[0].form()["response_format"], "json"
        )

    def test_a_forced_language(self):
        asr = self.asr()
        asr.language = "ja"
        self.assertEqual(asr(self.mp4, 6.0).language, "ja")
        self.assertEqual(self.fake.seen("/audio/transcriptions")[0].form()["language"], "ja")

    def test_too_big_is_sent_in_parts_and_joined(self):
        size = len(hosted.encode(self.mp4))
        heard = self.asr(upload=size // 2)(self.mp4, 6.0)
        reqs = self.fake.seen("/audio/transcriptions")
        self.assertGreaterEqual(len(reqs), 2)
        self.assertTrue(all(len(r.body) < size for r in reqs))
        self.assertEqual(heard.text, " ".join([self.fake.transcript] * len(reqs)))
        self.assertEqual(heard.usage.audio_seconds, 6.0 * len(reqs))

    def test_no_audio_track_is_no_speech(self):
        silent = self.dir / "silent.mp4"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
             str(silent)],
            check=True,
        )  # fmt: skip
        heard = hosted.transcribe_one(
            {"media_path": "silent.mp4", "duration": 1}, self.dir, self.asr()
        )
        self.assertIsNone(heard.text)
        self.assertEqual(self.fake.seen("/audio/transcriptions"), [])


class TestRecordsLanguage(unittest.TestCase):
    def test_asr_language_is_stored(self):
        with tempfile.TemporaryDirectory() as d:
            conn = open_db(Path(d) / "t.sqlite3")
            conn.execute("INSERT INTO item (shortcode, permalink) VALUES ('A1', 'u')")
            record(conn, threading.Lock(), "A1", "hola", "groq:whisper-large-v3", "es")
            row = conn.execute("SELECT asr_language, asr_profile FROM item").fetchone()
            self.assertEqual(tuple(row), ("es", "groq:whisper-large-v3"))
            # The verbose reply the fake gives is what the client parses.
            self.assertEqual(verbose("a b", 0.1)["language"], "english")


if __name__ == "__main__":
    unittest.main()
