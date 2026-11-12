"""`hikidashi worker --backend hosted` (#125): the server's API on another
machine, with its own key; all-stage claims; its budget; usage reported."""

import os
import unittest
from typing import Any
from unittest import mock

from app.ai import hosted_worker, usage
from app.ai.hosted_worker import HostedWorker, Stopped
from app.jobs import protocol
from app.jobs.queue import LocalJobs
from app.providers.client import Usage
from tests import test_remote as base
from tests.fake_openai import KEY, Fake, stage_reply
from tests.test_ai_settings import CLEAN


def setUpModule():
    base.setUpModule()


def tearDownModule():
    base.tearDownModule()


class HostedCase(base.WorkerCase):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, CLEAN, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.fake = Fake(chat_reply=stage_reply).start()
        self.addCleanup(self.fake.stop)
        enc = mock.patch(
            "app.asr.hosted.encode", side_effect=lambda path, **kw: b"fLaC" + path.read_bytes()
        )
        enc.start()
        self.addCleanup(enc.stop)
        grace = mock.patch("app.ai.eligible.GRACE", 0)
        grace.start()
        self.addCleanup(grace.stop)
        remote_grace = mock.patch("app.ai.remote.eligible.GRACE", 0)
        remote_grace.start()
        self.addCleanup(remote_grace.stop)
        # Short enough that the fake's nine words are speech, not noise.
        self.conn.execute("UPDATE item SET duration = 6")

    def env(self, **extra: str) -> dict[str, str]:
        return {
            "HIKIDASHI_ASR_PROVIDER": "compatible",
            "HIKIDASHI_OCR_PROVIDER": "compatible",
            "HIKIDASHI_LLM_PROVIDER": "compatible",
            "HIKIDASHI_ASR_MODEL": "whisper-1",
            "HIKIDASHI_OCR_MODEL": "vision",
            "HIKIDASHI_LLM_MODEL": "chat",
            "HIKIDASHI_OPENAI_BASE_URL": self.fake.url,
            "HIKIDASHI_OPENAI_API_KEY": KEY,
            "HIKIDASHI_HOSTED_BUDGET": "5",
            **extra,
        }

    def hosted(self, scope="me", **env: str) -> HostedWorker:
        return HostedWorker(
            self.jobs(), hosted_worker.from_env(self.env(**env)), self.cache, scope=scope
        )

    def claim_hosted(self, **kw: Any):
        return self.post("hosted/claim", **{"scope": "me", "limit": 4, **kw})


class TestHostedWorker(HostedCase):
    def test_does_every_stage_of_each_reel_with_its_own_key(self):
        w = self.hosted()
        self.assertEqual(w.round(), 3)
        row = self.row()
        self.assertEqual(row["transcript"], self.fake.transcript)
        self.assertEqual(row["asr_profile"], "compatible:whisper-1")
        self.assertEqual(row["tag_profile"], "compatible:chat")
        self.assertEqual(row["primary_tag"], "Food")
        # Its key, from its env; the server has none to send.
        auth = {r.headers.get("Authorization") for r in self.fake.requests}
        self.assertEqual(auth, {f"Bearer {KEY}"})
        self.assertEqual(self.leases(), [])
        rep = usage.report(self.conn, api="hosted:desktop")
        self.assertTrue(rep["rows"])
        self.assertEqual(
            {r["model"] for r in rep["rows"]}, {"compatible:whisper-1", "compatible:chat"}
        )

    def test_a_claim_holds_every_stage(self):
        r = self.claim_hosted(limit=1)
        self.assertEqual(r.status_code, 200, r.text)
        (code,) = r.json()["reels"]
        self.assertEqual({s for s, c, _ in self.leases() if c == code}, set(protocol.REMOTE))
        self.assertNotIn(code, LocalJobs(self.conn, "gpu").claim("asr", 8))

    def test_nothing_the_server_sends_holds_a_key(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-server-secret-0123456789"}):
            r = self.claim_hosted()
            rows = self.post("hosted/rows", stage="asr", shortcodes=r.json()["reels"])
        self.assertNotIn("sk-server", r.text + rows.text)

    def test_an_old_protocol_is_refused(self):
        r = self.client.post(
            "/api/worker/hosted/claim",
            json={
                "worker": "desktop",
                "protocol": 1,
                "versions": protocol.versions(),
                "scope": "me",
            },
        )
        self.assertEqual(r.status_code, 409)

    def test_the_budget_stops_it_and_gives_back(self):
        usage.record(self.conn, "hosted:desktop", None, "tag", "x:y", Usage(), usd=5)
        with self.assertRaises(Stopped) as cm:
            self.hosted().round()
        self.assertIn("budget", str(cm.exception))
        self.assertEqual(self.leases(), [])
        self.assertEqual(self.fake.requests, [])

    def test_a_rejected_key_stops_it_and_counts_nothing(self):
        self.fake.key = "other"
        with self.assertRaises(Stopped) as cm:
            self.hosted().round()
        self.assertIn("key rejected", str(cm.exception))
        self.assertEqual(self.leases(), [])
        self.assertEqual(self.attempts("AAA111"), 0)
        self.assertIsNone(self.row()["fail_stage"])

    def test_an_api_error_is_counted_apart(self):
        self.fake.chat_reply = lambda req: {"nope": 1}
        self.hosted().round()
        errs = self.conn.execute(
            "SELECT shortcode, errors FROM api_reel WHERE api = 'hosted:desktop' ORDER BY 1"
        ).fetchall()
        self.assertEqual([tuple(r) for r in errs], [(c, 1) for c in sorted(base.CODES)])
        self.assertEqual(self.attempts("AAA111", "tag"), 0)
        self.assertEqual(self.leases(), [])

    def test_not_set_up(self):
        with self.assertRaises(Stopped):
            self.hosted(HIKIDASHI_HOSTED_BUDGET="")

    def test_stopping_gives_back(self):
        self.claim_hosted()
        self.jobs().check_out()
        self.assertEqual(self.leases(), [])


if __name__ == "__main__":
    unittest.main()
