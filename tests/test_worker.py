"""The always-on worker (#72), with every model faked: an import goes all the
way down the stages with no other command."""

import array
import dataclasses
import io
import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import jobs
from app.asr.transcriber import ASR_V
from app.config import settings
from app.db import open_db
from app.embed.embedder import EMBED_V
from app.enrich.fetcher import FETCH_V, Fetched, FetchError
from app.importer.service import import_export
from app.jobs import __main__ as cli
from app.jobs import queue
from app.jobs.queue import LEASE, MAX_ATTEMPTS, LocalJobs
from app.jobs.worker import Runners, Unavailable, Worker
from app.ocr.reader import OCR_V
from app.recipes.llm import RECIPE_V
from app.tagging.llm import TAG_V
from tests.fixtures import post_record, saved_posts, write_zip

R = "https://www.instagram.com/reel/"
P = "https://www.instagram.com/p/"

SPOKEN = {"AAA111": "you do not need a press for this", "BBB222": None}


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def probe(url):
    code = url.rstrip("/").rsplit("/", 1)[-1]
    raw = {} if code == "CCC333" else {"formats": [{"format_id": "mp4"}]}
    return Fetched(code, thumbnail_url=f"https://cdn.example/{code}.jpg", raw=raw)


def download(url, dest):
    if "CCC333" in str(url):
        raise FetchError("no video formats")  # a photo post
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"mp4")
    return 3


def thumbnail(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"jpg")


class FakeEmbedder:
    def documents(self, texts):
        return [array.array("f", [1.0, 0.0, 0.0, 0.0]).tobytes() for _ in texts]


class FakeRecipes:
    def check(self, prompt):
        return json.dumps({"is_recipe": False})

    def extract(self, prompt):
        raise AssertionError("not a recipe, so never extracted")


def tagger(prompt):
    return json.dumps({"summary": "a reel", "category": "Other", "keywords": ["reel"]})


class WorkerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.run_dir = self.dir / "run"
        self.conn = open_db(self.dir / "test.sqlite3")
        zp = write_zip(
            self.dir / "e.zip",
            saved_posts(
                [
                    post_record(R + "AAA111/", timestamp=300, caption="brake job on the e30"),
                    post_record(R + "BBB222/", timestamp=200, caption="sunset drive, no words"),
                    post_record(P + "CCC333/", timestamp=100, caption="a menu board in kyoto"),
                ]
            ),
        )
        import_export(self.conn, zp)
        self.clock = Clock()
        for target, fake in [
            ("app.enrich.worker.probe", probe),
            ("app.enrich.worker.download_media", download),
            ("app.enrich.worker.download_thumbnail", thumbnail),
            ("app.enrich.worker.probe_duration", lambda path: 30),
            ("app.enrich.worker.is_carousel", lambda raw: False),
            ("app.asr.worker.extract_audio", lambda path: Path(path).stem.encode()),
        ]:
            patch = mock.patch(target, side_effect=fake)
            patch.start()
            self.addCleanup(patch.stop)
        # No politeness delay between fetches in tests.
        patch = mock.patch("app.enrich.worker.time")
        patch.start()
        self.addCleanup(patch.stop)
        self.read = []  # what OCR read, in order
        quiet = mock.patch.object(logging.getLogger("hikidashi.worker"), "disabled", True)
        quiet.start()
        self.addCleanup(quiet.stop)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def whisper(self, pcm):
        return SPOKEN[pcm.decode()]

    def ocr(self, row, media_dir):
        self.read.append(row["shortcode"])
        return "words on the screen"

    def runners(self, **kw) -> Runners:
        defaults = {
            "whisper": lambda profile, models: self.whisper,
            "embedder": lambda models: FakeEmbedder(),
            "tagger": lambda url, model: tagger,
            "recipe_model": lambda url, model: FakeRecipes(),
            "ocr_reader": self.ocr,
            "which": lambda tool: f"/usr/bin/{tool}",
        }
        return Runners(
            media_dir=self.dir / "media",
            models_dir=self.dir / "models",
            ollama_url="http://ollama.invalid",
            profile="cpu",
            clock=self.clock,
            **{**defaults, **kw},
        )

    def worker(self, *, stages=jobs.STAGES, name="w", runners=None, retry_after=0, **kw) -> Worker:
        return Worker(
            LocalJobs(self.conn, name, clock=self.clock, retry_after=retry_after),
            self.conn,
            runners or self.runners(),
            stages=stages,
            run_dir=self.run_dir,
            clock=self.clock,
            sleep=lambda s: setattr(self.clock, "t", self.clock.t + s),
            **kw,
        )

    def row(self, code):
        return self.conn.execute("SELECT * FROM item WHERE shortcode = ?", (code,)).fetchone()

    def counts(self, stage):
        return next(q for q in queue.queue(self.conn, now=self.clock()) if q["stage"] == stage)


class TestEndToEnd(WorkerCase):
    def test_an_import_goes_all_the_way_down_with_no_command(self):
        w = self.worker()
        w.start()
        self.assertGreater(w.drain(), 0)
        for code in ("AAA111", "BBB222", "CCC333"):
            row = self.row(code)
            self.assertEqual(row["fetch_v"], FETCH_V, code)
            self.assertEqual(row["tag_v"], TAG_V, code)
            self.assertEqual(row["recipe_v"], RECIPE_V, code)
            self.assertEqual(row["embed_v"], EMBED_V, code)
        self.assertEqual(self.row("AAA111")["transcript"], SPOKEN["AAA111"])
        self.assertEqual(self.row("BBB222")["asr_v"], ASR_V)
        # Read for on-screen text: the silent reel and the photo, not the one with speech.
        self.assertEqual(sorted(self.read), ["BBB222", "CCC333"])
        self.assertEqual(self.row("CCC333")["ocr_v"], OCR_V)
        # Tagged once, with everything: after the transcript and the text.
        self.assertEqual(self.row("CCC333")["ocr_text"], "words on the screen")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM job_lease").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM job_attempt").fetchone()[0], 0)
        for q in queue.queue(self.conn, now=self.clock()):
            self.assertEqual((q["ready"], q["waiting"], q["failed"]), (0, 0, 0), q["stage"])

    def test_the_hashtag_pass_runs_with_the_tagger(self):
        self.worker().drain()
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM item WHERE hashtag_v = 0").fetchone()[0], 0
        )

    def test_nothing_runs_ahead_of_the_fetch(self):
        self.worker(stages=("asr", "ocr", "tag", "recipe", "embed")).drain()
        self.assertEqual(self.row("AAA111")["tag_v"], 0)
        self.assertEqual(self.row("AAA111")["embed_v"], 0)
        self.assertEqual(self.counts("embed")["waiting"], 3)


class TestLocalStages(WorkerCase):
    def test_none_runs_nothing(self):
        w = self.worker(stages=())
        self.assertEqual(w.drain(), 0)
        self.assertEqual(self.row("AAA111")["fetch_v"], 0)

    def test_a_list_runs_only_those(self):
        w = self.worker(stages=("enrich", "asr"))
        w.drain()
        self.assertEqual(self.row("AAA111")["asr_v"], ASR_V)
        self.assertEqual(self.row("AAA111")["tag_v"], 0)
        self.assertEqual(self.read, [])
        self.assertEqual(self.counts("tag")["ready"], 1)  # the reel with speech


class TestFailures(WorkerCase):
    def test_killed_mid_batch_loses_nothing(self):
        def dies(pcm):
            raise KeyboardInterrupt  # as good as kill -9: nothing gets to clean up

        w = self.worker(runners=self.runners(whisper=lambda p, m: dies))
        with self.assertRaises(KeyboardInterrupt):
            w.drain()
        self.assertEqual(self.counts("asr")["working"], 2)
        self.clock.t += LEASE + 1
        self.worker(name="next").drain()
        self.assertEqual(self.row("AAA111")["transcript"], SPOKEN["AAA111"])
        self.assertEqual(self.row("AAA111")["embed_v"], EMBED_V)

    def test_a_reel_that_always_fails_stops_being_tried(self):
        calls = []

        def broken(pcm):
            calls.append(pcm.decode())
            if pcm == b"AAA111":
                raise RuntimeError("CUDA out of memory")
            return None

        w = self.worker(runners=self.runners(whisper=lambda p, m: broken), retry_after=60)
        w.drain()
        self.assertEqual(calls.count("AAA111"), 1)  # and a minute's pause before the next
        for _ in range(MAX_ATTEMPTS):
            self.clock.t += 600
            w.drain()
        self.assertEqual(calls.count("AAA111"), MAX_ATTEMPTS)
        self.assertEqual(self.counts("asr")["failed"], 1)
        [failed] = queue.failures(self.conn, "asr")
        self.assertEqual(failed["shortcode"], "AAA111")
        self.assertIn("out of memory", failed["reason"])
        # Given up on, so the later stages go ahead without its transcript.
        self.assertEqual(self.row("AAA111")["tag_v"], TAG_V)

    def test_a_stage_that_cannot_run_counts_nothing_and_says_why(self):
        def no_ollama(url, model):
            raise Unavailable("no Ollama at http://ollama.invalid")

        w = self.worker(runners=self.runners(tagger=no_ollama))
        w.drain()
        self.assertEqual(w.problems, {"tag": "no Ollama at http://ollama.invalid"})
        self.assertEqual(self.counts("tag")["failed"], 0)
        self.assertEqual(self.counts("tag")["ready"], 3)
        # With no tagger, embed went ahead from captions rather than wait.
        embed = self.counts("embed")
        self.assertEqual((embed["ready"], embed["waiting"], embed["without"]), (0, 0, ["tag"]))
        [me] = queue.workers(self.conn, now=self.clock())
        self.assertEqual(me["problems"], {"tag": "no Ollama at http://ollama.invalid"})

    def test_a_stage_that_could_not_run_is_tried_again_later(self):
        tries = []

        def no_ollama(url, model):
            tries.append(self.clock())
            raise Unavailable("no Ollama")

        w = self.worker(runners=self.runners(tagger=no_ollama), backoff=(60, 3600))
        w.drain()
        self.assertEqual(len(tries), 1)  # once, however many rounds
        w.round()
        self.assertEqual(len(tries), 1)  # still backing off
        self.clock.t += 61
        w.round()
        self.assertEqual(len(tries), 2)
        self.clock.t += 61
        w.round()
        self.assertEqual(len(tries), 2)  # twice as long now

    def test_running_again_clears_the_problem(self):
        state = {"up": False}

        def flaky(url, model):
            if not state["up"]:
                raise Unavailable("no Ollama")
            return tagger

        w = self.worker(runners=self.runners(tagger=flaky))
        w.drain()
        state["up"] = True
        self.clock.t += 61
        w.drain()
        self.assertEqual(w.problems, {})
        self.assertEqual(self.row("AAA111")["tag_v"], TAG_V)


class TestMissingLibraries(WorkerCase):
    """An image without a stage's library: the worker image has no fastembed,
    the server image no faster-whisper. Its reels go back uncounted (#86)."""

    def assert_uncounted(self, stage, module, w):
        for _ in range(MAX_ATTEMPTS + 1):
            self.clock.t += 3600
            w.round()
        self.assertEqual(w.problems, {stage: f"not installed: {module}"})
        self.assertEqual(self.counts(stage)["failed"], 0)
        attempts = self.conn.execute(
            "SELECT MAX(attempts) FROM job_attempt WHERE stage = ?", (stage,)
        ).fetchone()[0]
        self.assertIn(attempts, (None, 0))

    def test_asr_without_faster_whisper(self):
        w = self.worker(stages=("enrich", "asr"), runners=self.runners(whisper=None))
        with mock.patch.dict(sys.modules, {"faster_whisper": None}):
            self.assert_uncounted("asr", "faster_whisper", w)
        self.assertNotEqual(self.row("AAA111")["asr_v"], ASR_V)

    def test_embed_without_fastembed(self):
        w = self.worker(runners=self.runners(embedder=None))
        with mock.patch.dict(sys.modules, {"fastembed": None}):
            self.assert_uncounted("embed", "fastembed", w)
        self.assertNotEqual(self.row("AAA111")["embed_v"], EMBED_V)

    def test_enrich_without_yt_dlp(self):
        w = self.worker(runners=self.runners(which=lambda tool: None))
        self.assert_uncounted("enrich", "yt-dlp, ffprobe", w)
        self.assertFalse(w._away)  # not mistaken for the server being away


class TestStopping(WorkerCase):
    def test_a_clean_stop_gives_the_reels_back(self):
        w = self.worker()
        w.start()
        w.jobs.claim("enrich", 3)
        w.stop()
        self.assertEqual(self.counts("enrich")["ready"], 3)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM job_attempt").fetchone()[0], 0)
        [me] = queue.workers(self.conn, now=self.clock())
        self.assertEqual(me["state"], "stopped")

    def test_a_restart_takes_back_what_it_died_holding(self):
        self.worker().jobs.claim("enrich", 3)
        again = self.worker()
        self.assertEqual(again.start(), 3)
        again.drain()
        self.assertEqual(self.row("AAA111")["embed_v"], EMBED_V)


class TestIdle(WorkerCase):
    def test_models_are_dropped_after_a_stretch_with_no_work(self):
        runners = self.runners(unload_after=300)
        w = self.worker(runners=runners)
        w.drain()
        self.assertIn("whisper", runners.loaded())
        self.clock.t += 301
        w.round()
        self.assertEqual(runners.loaded(), [])

    def test_an_import_wakes_it(self):
        w = self.worker()
        start = self.clock()

        def sleep(seconds):
            self.clock.t += seconds
            if self.clock.t - start >= 3:
                jobs.wake(self.run_dir)

        w.sleep = sleep
        w._wait(60)
        self.assertLess(self.clock.t - start, 10)

    def test_an_empty_queue_waits_longer_each_time(self):
        waits: list[float] = []

        class Enough(Exception):
            pass

        def wait(seconds):
            if len(waits) == 6:
                raise Enough
            waits.append(seconds)

        w = self.worker(stages=(), idle=(5, 60))
        with mock.patch.object(w, "_wait", wait), self.assertRaises(Enough):
            w.run()
        self.assertEqual(waits, [5, 10, 20, 40, 60, 60])


class TestCommand(unittest.TestCase):
    def run_main(self, *argv):
        self.installed = mock.Mock()
        with tempfile.TemporaryDirectory() as d:
            here = dataclasses.replace(
                settings,
                db_path=Path(d) / "db.sqlite3",
                logs_dir=Path(d) / "logs",
                run_dir=Path(d) / "run",
                media_dir=Path(d) / "media",
                imports_dir=Path(d) / "imports",
                models_dir=Path(d) / "models",
            )
            out, err = io.StringIO(), io.StringIO()
            with (
                mock.patch.object(cli, "settings", here),
                mock.patch("logging.basicConfig"),
                mock.patch("app.logs.install", self.installed),
                mock.patch("signal.signal"),
                mock.patch("os.nice"),
                mock.patch("sys.stdout", out),
                mock.patch("sys.stderr", err),
            ):
                code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_once_with_no_stages_does_nothing(self):
        code, out, _ = self.run_main("--once", "--stages", "none")
        self.assertEqual(code, 0)
        self.assertIn("worked on 0 reels", out)

    def test_each_worker_logs_to_a_file_of_its_own(self):
        # The server-worker shares the worker's log folder under Docker (#86).
        self.run_main("--once", "--stages", "none")
        self.assertEqual(self.installed.call_args.args[1], "worker.log")
        self.run_main("--once", "--stages", "none", "--log", "server-worker.log")
        self.assertEqual(self.installed.call_args.args[1], "server-worker.log")

    def test_an_unknown_stage_is_refused(self):
        code, _, err = self.run_main("--once", "--stages", "asr,frobnicate")
        self.assertEqual(code, 2)
        self.assertIn("frobnicate", err)


if __name__ == "__main__":
    unittest.main()
