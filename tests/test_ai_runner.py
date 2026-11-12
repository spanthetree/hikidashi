"""The switched-on APIs in the job queue (#125): all or nothing, the switch,
the automatic off, API errors apart from workers' attempts."""

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from app.ai import config, eligible, runner, service, usage
from app.db import connect, open_db
from app.jobs.queue import LocalJobs
from app.providers.client import Usage
from tests.fake_openai import KEY, Fake, stage_reply
from tests.test_ai_settings import ADMIN, BOB, CLEAN, HAS_CRYPTO

HAS_FFMPEG = shutil.which("ffmpeg") is not None


@unittest.skipUnless(HAS_CRYPTO and HAS_FFMPEG, "cryptography or ffmpeg missing")
class RunnerCase(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, CLEAN, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.media = self.dir / "media"
        self.media.mkdir()
        self.db = self.dir / "t.sqlite3"
        self.conn = open_db(self.db)
        self.fake = Fake(chat_reply=stage_reply).start()
        self.addCleanup(self.fake.stop)
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y",
             "-f", "lavfi", "-i", "sine=frequency=300:duration=3",
             "-f", "lavfi", "-i", "color=c=black:s=64x64:d=3",
             "-shortest", str(self.media / "r.mp4")],
            check=True,
        )  # fmt: skip
        now = int(time.time())
        self.conn.execute(
            "INSERT INTO user (id, username, created_at) VALUES (2, 'bob', ?)", (now,)
        )
        for code, user in (("R1", 2), ("R2", 2), ("A1", 1)):
            self.conn.execute(
                """INSERT INTO item (shortcode, permalink, fetch_v, media_path, duration,
                                     caption, state, saved_at)
                   VALUES (?, ?, 99, 'r.mp4', 3, 'pizza dough recipe at home', 'fetched', ?)""",
                (code, f"https://x/{code}", now),
            )
            self.conn.execute(
                "INSERT INTO user_item (user_id, shortcode) VALUES (?, ?)", (user, code)
            )
        # The compatible preset, pointed at the fake, so no real provider is named.
        service.set_stage(
            self.conn, BOB, 2, "all", self.dir, provider="compatible", base_url=self.fake.url
        )
        for s, m in (("asr", "whisper-1"), ("ocr", "vision"), ("tag", "chat"), ("recipe", "chat")):
            service.set_stage(self.conn, BOB, 2, s, self.dir, model=m)
        service.set_key(self.conn, BOB, 2, "compatible", KEY, self.dir)
        service.set_budget(self.conn, BOB, 2, 5, self.dir)

    def claimer(self, owner=2, **kw):
        return runner.Claimer(lambda: connect(self.db), owner, self.dir, self.media, **kw)

    def on(self, **kw):
        service.turn_on(self.conn, BOB, 2, self.dir, **kw)

    def item(self, code):
        return self.conn.execute("SELECT * FROM item WHERE shortcode = ?", (code,)).fetchone()


class TestRunner(RunnerCase):
    def test_off_sends_nothing(self):
        r = self.claimer().round()
        self.assertEqual(r.stopped, "off")
        self.assertEqual(self.fake.requests, [])

    def test_on_does_every_stage_and_records_it(self):
        self.on()
        r = self.claimer().round()
        self.assertEqual((r.claimed, r.done, r.errors), (2, 2, 0))
        row = self.item("R1")
        self.assertEqual(row["transcript"], self.fake.transcript)
        self.assertEqual(row["asr_profile"], "compatible:whisper-1")
        self.assertEqual(row["asr_language"], None)  # whisper-1 is not in the compatible's segments
        self.assertEqual(row["tag_profile"], "compatible:chat")
        self.assertEqual(row["primary_tag"], "Food")
        self.assertEqual(row["recipe_profile"], "compatible:chat")
        self.assertIsNone(row["fail_stage"])
        self.assertIsNone(self.item("A1")["transcript"])  # the admin's: not Bob's API's
        used = usage.report(self.conn, api="api:user:2")
        self.assertEqual({r["stage"] for r in used["rows"]}, {"asr", "tag", "recipe"})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM job_lease").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM job_attempt").fetchone()[0], 0)

    def test_all_or_nothing_holds_every_stage(self):
        self.on()
        c = self.claimer(batch=1)
        api = config.load(self.conn, 2, self.dir)
        got = c.claim(self.conn, api)
        stages = {
            r[0] for r in self.conn.execute("SELECT stage FROM job_lease WHERE shortcode = ?", got)
        }
        self.assertEqual(stages, {"asr", "ocr", "tag", "recipe"})
        # A worker cannot take a later stage of it: mark it transcribed elsewhere.
        self.conn.execute("UPDATE item SET asr_v = 99, transcript = 'x' WHERE shortcode = ?", got)
        self.assertNotIn(got[0], LocalJobs(self.conn, "w").claim("tag", 10))
        # And another API never takes it.
        other = runner.Claimer(lambda: connect(self.db), None, self.dir, self.media)
        self.assertNotIn(got[0], other.claim(self.conn, api))

    def test_a_reel_a_worker_transcribed_gets_only_the_rest(self):
        self.conn.execute(
            "UPDATE item SET asr_v = 99, transcript = 'pizza dough by hand', asr_profile = 'cuda'"
        )
        self.on()
        self.claimer().round()
        self.assertEqual(self.fake.seen("/audio/transcriptions"), [])
        self.assertEqual(self.item("R1")["asr_profile"], "cuda")
        self.assertEqual(self.item("R1")["tag_profile"], "compatible:chat")

    def test_a_rejected_key_turns_it_off_and_fails_nothing(self):
        self.on()
        self.fake.key = "another"
        r = self.claimer().round()
        self.assertEqual(r.stopped, "unusable")
        api = config.load(self.conn, 2, self.dir)
        self.assertFalse(api.switch.on)
        self.assertIn("key rejected", api.switch.reason or "")
        self.assertIsNone(self.item("R1")["fail_stage"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM job_lease").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM job_attempt").fetchone()[0], 0)
        # Workers can claim them at once.
        self.assertEqual(set(LocalJobs(self.conn, "w").claim("asr", 10)), {"R1", "R2", "A1"})

    def test_switching_off_gives_the_rest_back_uncounted(self):
        self.on()
        c = self.claimer()
        c.claim(self.conn, config.load(self.conn, 2, self.dir))
        service.off_by(self.conn, BOB, 2, self.dir)
        self.assertEqual(c.round().stopped, "off")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM job_lease").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM job_attempt").fetchone()[0], 0)

    def test_api_errors_are_counted_apart_then_left_to_workers(self):
        self.on()
        self.fake.chat_reply = lambda req: {"summary": "x"}  # never fits the tag schema
        c = self.claimer()
        for _ in range(eligible.MAX_ERRORS):
            c.round()
        self.assertEqual(c.round().claimed, 0)
        errs = self.conn.execute("SELECT errors FROM api_reel WHERE shortcode = 'R1'").fetchone()
        self.assertEqual(errs[0], eligible.MAX_ERRORS)
        row = self.item("R1")
        self.assertIsNone(row["fail_stage"])
        self.assertEqual(row["transcript"], self.fake.transcript)  # asr was done
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM job_attempt").fetchone()[0], 0)
        self.assertIn("R1", LocalJobs(self.conn, "w").claim("tag", 10))

    def test_a_rate_limit_waits_and_gives_back(self):
        self.on()
        for _ in range(4):
            self.fake.then(
                "/audio/transcriptions", 429, {"error": {"message": "slow"}}, {"Retry-After": "600"}
            )
        c = self.claimer()
        self.assertEqual(c.round().stopped, "wait")
        self.assertTrue(config.load(self.conn, 2, self.dir).switch.on)
        self.assertEqual(c.round().stopped, "waiting")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM job_lease").fetchone()[0], 0)

    def test_the_budget_turns_it_off(self):
        self.on()
        usage.record(self.conn, "api:user:2", 2, "tag", "x:y", Usage(), usd=5.0)
        self.assertEqual(self.claimer().round().stopped, "budget")
        api = config.load(self.conn, 2, self.dir)
        self.assertFalse(api.switch.on)
        self.assertIn("budget", api.switch.reason or "")
        self.assertEqual(self.fake.requests, [])

    def test_the_server_waits_out_the_grace_period(self):
        service.set_stage(
            self.conn, ADMIN, None, "all", self.dir, provider="compatible", base_url=self.fake.url
        )
        for s, m in (("asr", "whisper-1"), ("ocr", "v"), ("tag", "c"), ("recipe", "c")):
            service.set_stage(self.conn, ADMIN, None, s, self.dir, model=m)
        service.set_budget(self.conn, ADMIN, None, 5, self.dir)
        service.turn_on(self.conn, ADMIN, None, self.dir, scope="all")
        clock = [1000.0]
        c = runner.Claimer(
            lambda: connect(self.db), None, self.dir, self.media, clock=lambda: clock[0]
        )
        self.assertEqual(c.claim(self.conn, config.load(self.conn, None, self.dir)), [])
        clock[0] += eligible.GRACE
        self.assertEqual(len(c.claim(self.conn, config.load(self.conn, None, self.dir))), 3)

    def test_the_host_runs_the_apis_that_are_on(self):
        self.on()
        host = runner.Host(lambda: connect(self.db), self.dir, self.media)
        self.assertEqual(host.owners(), {2})
        self.assertEqual(host.once(), 2)


if __name__ == "__main__":
    unittest.main()
