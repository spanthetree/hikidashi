"""Logging and monitoring settings saved from the Account page (app/monitoring.py)."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from app import monitoring
from app.db import open_db

HAS_CIPHER = importlib.util.find_spec("cryptography") is not None


class MonitoringCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.db = self.dir / "t.sqlite3"
        self.conn = open_db(self.db)
        self.addCleanup(self.conn.close)

    def save(self, env=None, **changes):
        return monitoring.save(self.conn, self.dir, changes, 1, env or {})

    def shown(self, env=None):
        return {s["name"]: s for s in monitoring.show(self.conn, env or {})}


class TestSaving(MonitoringCase):
    def test_nothing_saved_shows_the_defaults(self):
        level = self.shown()["HIKIDASHI_LOG_LEVEL"]
        self.assertEqual(
            (level["source"], level["value"], level["default"]), ("default", "", "info")
        )
        self.assertEqual(monitoring.environ(self.conn, self.dir, {}), {})

    def test_saved_then_applied_then_cleared(self):
        env = self.save(HIKIDASHI_SYSLOG="udp://127.0.0.1:514", HIKIDASHI_METRICS=True)
        self.assertEqual(env["HIKIDASHI_SYSLOG"], "udp://127.0.0.1:514")
        self.assertEqual(env["HIKIDASHI_METRICS"], "on")
        self.assertEqual(self.shown()["HIKIDASHI_SYSLOG"]["source"], "saved")
        self.save(HIKIDASHI_SYSLOG=None, HIKIDASHI_METRICS=False)
        self.assertEqual(monitoring.environ(self.conn, self.dir, {}), {})

    def test_the_environment_wins_and_cannot_be_changed_here(self):
        env = {"HIKIDASHI_LOG_LEVEL": "warning"}
        self.assertEqual(self.shown(env)["HIKIDASHI_LOG_LEVEL"]["source"], "env")
        with self.assertRaises(monitoring.Invalid):
            self.save(env, HIKIDASHI_LOG_LEVEL="debug")
        # A secret passed as a file counts as set too.
        with self.assertRaises(monitoring.Invalid):
            self.save({"HIKIDASHI_LOKI_TOKEN_FILE": "/run/secrets/t"}, HIKIDASHI_LOKI_TOKEN="x")

    def test_a_wrong_setting_saves_nothing(self):
        for change in (
            {"HIKIDASHI_SYSLOG": "http://logs:514"},
            {"HIKIDASHI_LOKI_URL": "loki:3100"},
            {"HIKIDASHI_METRICS_PORT": "99999"},
            {"HIKIDASHI_METRICS_ALLOW": "not-a-net"},
            {"HIKIDASHI_LOG_LEVEL": "loud"},
            {"HIKIDASHI_NOT_A_SETTING": "x"},
            {"HIKIDASHI_LOKI_TENANT": "two\nlines"},
        ):
            with self.subTest(change), self.assertRaises(monitoring.Invalid):
                self.save(**change)
        self.assertEqual(monitoring.saved(self.conn), {})


@unittest.skipUnless(HAS_CIPHER, "cryptography is not installed")
class TestSecrets(MonitoringCase):
    def test_sealed_at_rest_and_never_shown(self):
        self.save(HIKIDASHI_METRICS_TOKEN="s3cret-token")
        row = self.conn.execute("SELECT value FROM setting WHERE key = 'monitoring'").fetchone()
        self.assertNotIn("s3cret-token", row[0])
        self.assertIs(self.shown()["HIKIDASHI_METRICS_TOKEN"]["value"], True)
        env = monitoring.environ(self.conn, self.dir, {})
        self.assertEqual(env["HIKIDASHI_METRICS_TOKEN"], "s3cret-token")

    def test_a_secret_under_another_key_reads_as_unset(self):
        self.save(HIKIDASHI_LOKI_PASSWORD="pw")
        (self.dir / "secret.key").unlink()
        with self.assertLogs("app.monitoring", "WARNING"):
            env = monitoring.environ(self.conn, self.dir, {})
        self.assertNotIn("HIKIDASHI_LOKI_PASSWORD", env)


class TestWatch(MonitoringCase):
    def test_a_worker_applies_now_and_after_a_change(self):
        seen: list[dict[str, str]] = []
        changed = threading.Event()

        def apply(env):
            seen.append({k: v for k, v in env.items() if k == "HIKIDASHI_LOG_LEVEL"})
            if len(seen) == 2:
                changed.set()

        stop = threading.Event()
        thread = monitoring.watch(self.db, self.dir, apply, every=0.05, stop=stop)
        self.addCleanup(thread.join, 5)
        self.addCleanup(stop.set)
        deadline = time.monotonic() + 5
        while not seen and time.monotonic() < deadline:
            time.sleep(0.02)
        self.conn.execute(
            "INSERT INTO setting (user_id, key, value, updated_at)"
            " VALUES (NULL, 'monitoring', ?, 1)",
            (json.dumps({"HIKIDASHI_LOG_LEVEL": "warning"}),),
        )
        self.conn.commit()
        self.assertTrue(changed.wait(5))
        self.assertEqual(seen[-1], {"HIKIDASHI_LOG_LEVEL": "warning"})


if __name__ == "__main__":
    unittest.main()
