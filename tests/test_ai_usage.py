"""Usage, budget and estimates; the per-user view; re-queueing (#125)."""

import calendar
import contextlib
import io
import unittest
import unittest.mock

from app.ai import config, status, usage
from app.cli import print_apis
from app.providers.client import Usage
from tests.test_ai_settings import ADMIN, BOB, AiCase


class TestCost(unittest.TestCase):
    def test_tokens_and_cache(self):
        u = Usage(input_tokens=1_000_000, cached_tokens=500_000, output_tokens=1_000_000)
        # gpt-4.1-mini: 0.40 in, 0.10 cached, 1.60 out
        self.assertAlmostEqual(usage.cost("openai:gpt-4.1-mini", u), 0.20 + 0.05 + 1.60)

    def test_audio(self):
        self.assertAlmostEqual(usage.cost("openai:whisper-1", Usage(audio_seconds=120)), 0.012)

    def test_unknown_model_is_free_and_flagged(self):
        self.assertEqual(usage.cost("x:y", Usage(input_tokens=10**9)), 0.0)

    def test_month_bounds(self):
        start, end, label = usage.month_bounds("2026-12")
        self.assertEqual(label, "2026-12")
        self.assertEqual(start, calendar.timegm((2026, 12, 1, 0, 0, 0)))
        self.assertEqual(end, calendar.timegm((2027, 1, 1, 0, 0, 0)))
        with self.assertRaises(ValueError):
            usage.month_bounds("2026-13")


class TestUsage(AiCase):
    def test_spend_this_month_and_split_by_user(self):
        usage.record(self.conn, "api:server", None, "tag", "x:y", Usage(), usd=1.0, shortcode="S1")
        usage.record(self.conn, "api:server", None, "tag", "x:y", Usage(), usd=0.5, shortcode="B1")
        usage.record(self.conn, "api:user:2", 2, "asr", "x:y", Usage(), usd=9.0, at=0)  # 1970
        self.assertEqual(usage.spent(self.conn, "api:server"), 1.5)
        self.assertEqual(usage.spent(self.conn, "api:user:2"), 0.0)
        rep = usage.report(self.conn)
        self.assertEqual(rep["total_usd"], 1.5)
        by = {r["username"]: r["usd"] for r in rep["server_by_user"]}
        self.assertEqual(by, {"bob": 1.0, "carol": 0.5})  # S1 split between bob and carol
        self.assertEqual(rep["unpriced"], ["x:y"])

    def test_estimate(self):
        self.ready(BOB, 2)
        api = config.load(self.conn, 2, self.dir)
        self.conn.execute("UPDATE item SET duration = 60")
        est = usage.estimate(self.conn, api, ["B1", "S1"])
        self.assertGreater(est["usd_low"], 0.012)  # two minutes of whisper-1, and more
        self.assertGreater(est["usd_high"], est["usd_low"])
        self.assertEqual(est["unpriced"], [])
        self.assertEqual(usage.estimate(self.conn, api, [])["usd_high"], 0.0)


class TestStatus(AiCase):
    def test_a_users_view_is_their_own(self):
        mine = status.mine(self.conn, 2, self.dir)
        asr = next(s for s in mine["stages"] if s["stage"] == "asr")
        self.assertEqual(asr["waiting"], 2)  # B1, S1; not A1 or C1
        self.assertEqual(mine["applies"], "workers")
        self.assertIsNone(mine["worker_seen_at"])

    def test_requeue_what_an_older_model_made(self):
        self.conn.execute("UPDATE item SET tag_v = 9, tag_profile = 'openai:gpt-4o-mini'")
        self.conn.execute("UPDATE item SET tag_profile = 'cuda' WHERE shortcode = 'A1'")
        self.assertEqual(
            status.profiles(self.conn)["tag"],
            [{"profile": "openai:gpt-4o-mini", "reels": 3}, {"profile": "cuda", "reels": 1}],
        )
        self.assertEqual(status.requeue(self.conn, "tag", "openai:gpt-4o-mini"), 3)
        self.assertEqual(
            self.conn.execute("SELECT tag_v FROM item WHERE shortcode = 'A1'").fetchone()[0], 9
        )
        with self.assertRaises(ValueError):
            status.requeue(self.conn, "embed", "x")

    def test_status_lines(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            print_apis(self.conn)
        self.assertIn("off; workers process every reel", out.getvalue())
        self.ready(ADMIN, None)
        from app.ai import service

        service.turn_on(self.conn, ADMIN, None, self.dir, scope="all")
        service.turn_off(self.conn, None, None, self.dir, reason="key rejected (401)")
        out = io.StringIO()
        with (
            contextlib.redirect_stdout(out),
            unittest.mock.patch(
                "app.cli.settings", unittest.mock.Mock(db_path=self.dir / "t.sqlite3")
            ),
        ):
            print_apis(self.conn)
        self.assertIn("api:server  off: key rejected (401)", out.getvalue())


if __name__ == "__main__":
    unittest.main()
