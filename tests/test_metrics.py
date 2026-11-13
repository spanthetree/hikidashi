"""/metrics, /healthz and /readyz (#149).

Needs the web dependencies and prometheus_client; skipped without them.
"""

import dataclasses
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from unittest import mock

from app import config
from app.db import open_db
from app.importer.service import import_export
from tests.fixtures import post_record, saved_posts, write_zip

HAS = all(importlib.util.find_spec(m) for m in ("fastapi", "httpx", "prometheus_client"))
R = "https://www.instagram.com/reel/"
SECRET_CAPTION = "grandma's secret dumpling recipe"
SHORTCODES = ("SECRETreel01", "SECRETreel02")

# Every documented metric, with its labels (docs/operation.md, Monitoring).
DOCUMENTED = {
    "hikidashi_build_info": {"version", "profile", "auth"},
    "hikidashi_http_requests": {"route", "method", "status"},
    "hikidashi_http_request_duration_seconds": {"route", "method"},
    "hikidashi_search_duration_seconds": {"part"},
    "hikidashi_items": {"state"},
    "hikidashi_users": set(),
    "hikidashi_collections": set(),
    "hikidashi_recipes": set(),
    "hikidashi_items_with": {"field"},
    "hikidashi_db_size_bytes": set(),
    "hikidashi_db_wal_size_bytes": set(),
    "hikidashi_media_size_bytes": set(),
    "hikidashi_import_last_timestamp_seconds": set(),
    "hikidashi_imports": {"result"},
    "hikidashi_stage_reels": {"stage", "status"},
    "hikidashi_stage_done_reels": {"stage"},
    "hikidashi_stage_failed_attempts": {"stage"},
    "hikidashi_fetch_failures": {"reason"},
    "hikidashi_worker_up": {"worker"},
    "hikidashi_worker_last_seen_timestamp_seconds": {"worker"},
    "hikidashi_worker_stages": {"worker", "stage"},
    "hikidashi_pipeline_overall": {"state"},
    "hikidashi_api_enabled": {"api"},
    "hikidashi_api_disabled_reason": {"api", "reason"},
    "hikidashi_api_in_flight": {"api"},
    "hikidashi_api_requests": {"api", "provider", "stage"},
    "hikidashi_api_cost_usd": {"api", "provider"},
    "hikidashi_api_budget_usd": {"api"},
    "hikidashi_log_lines_dropped": set(),
}


def local(app: Any) -> Any:
    """`app`, with every request from 127.0.0.1: TestClient says "testclient"."""

    async def wrapped(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            scope = {**scope, "client": ("127.0.0.1", 50000)}
        await app(scope, receive, send)

    return wrapped


def env(**kw: str) -> Any:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("HIKIDASHI_METRICS")}
    return mock.patch.dict(os.environ, {**clean, **kw}, clear=True)


@unittest.skipUnless(HAS, "the web dependencies or prometheus_client are not installed")
class MetricsCase(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app import metrics, search

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.settings = dataclasses.replace(
            config.Settings.from_env(), db_path=root / "t.sqlite3", media_dir=root / "media"
        )
        conn = open_db(self.settings.db_path)
        conn.execute("INSERT INTO user (id, username, created_at) VALUES (2, 'mallory', 0)")
        records = [
            post_record(R + c + "/", timestamp=1, caption=SECRET_CAPTION) for c in SHORTCODES
        ]
        import_export(conn, write_zip(root / "e.zip", saved_posts(records)))
        conn.execute(
            "UPDATE item SET state = 'failed', fail_stage = 'fetch', fail_reason = ? "
            "WHERE shortcode = ?",
            (f"ERROR: {R}{SHORTCODES[1]}/ broke", SHORTCODES[1]),
        )
        conn.execute(
            """INSERT INTO job_worker (name, pid, stages, started_at, seen_at)
               VALUES ('desktop', 1, '["asr","ocr"]', ?, ?)""",
            (int(time.time()), int(time.time())),
        )
        conn.execute(
            """INSERT INTO hosted_usage (at, api, user_id, stage, model, requests, usd)
               VALUES (?, 'api:user:2', 2, 'asr', 'groq:whisper-large-v3-turbo', 3, 0.25)""",
            (int(time.time()),),
        )
        conn.close()

        self.app = FastAPI()

        @self.app.get("/api/items/{shortcode}")
        def item(shortcode: str) -> dict[str, str]:
            return {"shortcode": shortcode}

        self.addCleanup(setattr, search, "observe", None)
        self.metrics = self.install()
        self.client = TestClient(local(self.app))
        self.metrics_module = metrics

    def install(self, **kw: str) -> Any:
        from app import metrics

        with env(HIKIDASHI_METRICS="on", HIKIDASHI_METRICS_REFRESH="3600", **kw):
            return metrics.install(self.app, self.settings, "9.9.9")

    def scrape(self, **headers: str) -> str:
        r = self.client.get("/metrics", headers=headers)
        self.assertEqual(r.status_code, 200, r.text)
        return r.text

    def families(self, text: str) -> dict[str, Any]:
        from prometheus_client.parser import text_string_to_metric_families

        return {f.name: f for f in text_string_to_metric_families(text)}


class TestOff(unittest.TestCase):
    def test_off_by_default_nothing_is_loaded_or_served(self):
        """A plain server answers /metrics with the app, not numbers, and has
        not imported prometheus_client."""
        code = (
            "import sys; from fastapi.testclient import TestClient; "
            "from app.api import main; "
            "r = TestClient(main.app).get('/metrics'); "
            "print(r.status_code, 'prometheus_client' in sys.modules, main.METRICS)"
        )
        with tempfile.TemporaryDirectory() as d:
            clean = {k: v for k, v in os.environ.items() if not k.startswith("HIKIDASHI_")}
            out = subprocess.run(
                [sys.executable, "-c", code],
                env={
                    **clean,
                    "HIKIDASHI_DB": f"{d}/t.sqlite3",
                    "HIKIDASHI_LOGS": d,
                    "HIKIDASHI_RUN": d,
                    "HIKIDASHI_MEDIA": d,
                    "HIKIDASHI_IMPORTS": d,
                },
                capture_output=True,
                text=True,
                timeout=120,
            )
        if "No module named" in out.stderr:
            self.skipTest("the web dependencies are not installed")
        status, imported, installed = out.stdout.split()[-3:]
        self.assertNotEqual(status, "200")
        self.assertEqual((imported, installed), ("False", "None"))


class TestScrape(MetricsCase):
    def test_parses_and_has_every_documented_metric_and_label(self):
        self.client.get("/api/items/abc")
        self.metrics.time_search("fts", 0.01)
        found = self.families(self.scrape())
        for name, labels in DOCUMENTED.items():
            self.assertIn(name, found, name)
            for sample in found[name].samples:
                if sample.name.endswith(("_bucket", "_created")):
                    continue
                self.assertEqual(set(sample.labels) - {"le"}, labels, (name, sample))
        self.assertIn("process_resident_memory_bytes", found)

    def test_values_match_the_library(self):
        found = self.families(self.scrape())
        states = {s.labels["state"]: s.value for s in found["hikidashi_items"].samples}
        self.assertEqual(states, {"imported": 1, "failed": 1})
        users = found["hikidashi_users"].samples[0].value
        self.assertEqual(users, 2)
        up = {s.labels["worker"]: s.value for s in found["hikidashi_worker_up"].samples}
        self.assertEqual(up, {"desktop": 1})
        cost = found["hikidashi_api_cost_usd"].samples[0]
        self.assertEqual((cost.labels["api"], cost.value), ("user", 0.25))
        stages = {
            (s.labels["stage"], s.labels["status"]): s.value
            for s in found["hikidashi_stage_reels"].samples
        }
        from app.jobs import queue

        conn = open_db(self.settings.db_path)
        for row in queue.queue(conn):
            self.assertEqual(stages[(row["stage"], "ready")], row["ready"])
            self.assertEqual(stages[(row["stage"], "failed")], row["failed"])

    def test_openmetrics_when_asked(self):
        r = self.client.get("/metrics", headers={"Accept": "application/openmetrics-text"})
        self.assertIn("openmetrics", r.headers["content-type"])
        self.assertTrue(r.text.rstrip().endswith("# EOF"))

    def test_the_cache_reads_the_database_once_per_refresh(self):
        self.scrape()
        self.scrape()
        self.assertEqual(self.metrics.library.reads, 1)


class TestPrivacy(MetricsCase):
    def test_no_shortcode_caption_url_or_username_in_any_label(self):
        for code in SHORTCODES:
            self.client.get(f"/api/items/{code}")
        text = self.scrape()
        for secret in (*SHORTCODES, SECRET_CAPTION, "mallory", "instagram.com", "dumpling"):
            self.assertNotIn(secret, text)
        reasons = {
            s.labels["reason"] for s in self.families(text)["hikidashi_fetch_failures"].samples
        }
        self.assertEqual(reasons, {"other"})

    def test_routes_are_templates_so_a_thousand_reels_add_no_series(self):
        self.client.get("/api/items/first")
        before = self.scrape().count("\n")
        for i in range(1000):
            self.client.get(f"/api/items/r{i}")
        self.client.get("/no/such/page")
        after = self.scrape()
        self.assertIn('route="/api/items/{shortcode}"', after)
        self.assertIn(f'route="{self.metrics_module.UNMATCHED}"', after)
        # The unmatched request adds its own few lines; the thousand reels none.
        self.assertLess(after.count("\n") - before, 40)

    def test_per_user_on_names_whose_api(self):
        from fastapi import FastAPI

        self.app = FastAPI()
        m = self.install(HIKIDASHI_METRICS_PER_USER="on")
        text = m.render(None)[0].decode()
        self.assertIn('user="mallory"', text)


@unittest.skipUnless(HAS, "the web dependencies or prometheus_client are not installed")
class TestAuth(MetricsCase):
    def test_a_token_is_needed_when_set(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        self.app = FastAPI()
        self.install(HIKIDASHI_METRICS_TOKEN="s3cret")
        client = TestClient(self.app)
        self.assertEqual(client.get("/metrics").status_code, 401)
        bad = client.get("/metrics", headers={"Authorization": "Bearer nope"})
        self.assertEqual(bad.status_code, 401)
        ok = client.get("/metrics", headers={"Authorization": "Bearer s3cret"})
        self.assertEqual(ok.status_code, 200)

    def test_a_session_cookie_counts_for_nothing(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        self.app = FastAPI()
        self.install(HIKIDASHI_METRICS_TOKEN="s3cret")
        client = TestClient(self.app, cookies={"hikidashi_session": "anything"})
        self.assertEqual(client.get("/metrics").status_code, 401)

    def test_without_a_token_only_the_allowed_addresses(self):
        from app.metrics import Options

        with env(HIKIDASHI_METRICS_ALLOW="10.0.0.0/8"):
            options = Options.from_env()
        self.assertIsNone(options.refuse(None, "10.1.2.3"))
        self.assertEqual(options.refuse(None, "203.0.113.9"), 403)
        self.assertEqual(options.refuse(None, None), 403)
        with env():
            default = Options.from_env()
        self.assertIsNone(default.refuse(None, "192.168.1.20"))
        self.assertIsNone(default.refuse(None, "::1"))
        self.assertEqual(default.refuse(None, "8.8.8.8"), 403)

    def test_the_token_can_come_from_a_file(self):
        from app.metrics import Options

        path = Path(self.tmp.name) / "token"
        path.write_text("from-a-file\n")
        with env(HIKIDASHI_METRICS_TOKEN_FILE=str(path)):
            self.assertEqual(Options.from_env().token, "from-a-file")

    def test_a_port_of_its_own(self):
        from fastapi import FastAPI

        self.app = FastAPI()
        with mock.patch("app.metrics.Metrics.serve") as serve:
            self.install(HIKIDASHI_METRICS_PORT="9464")
        serve.assert_called_once()
        self.assertFalse(any(getattr(r, "path", "") == "/metrics" for r in self.app.routes))

    def test_the_port_answers_with_the_same_rules(self):
        from app.metrics import Metrics, Options

        m = Metrics(self.settings, Options(on=True, token="t"), "9.9.9")
        server = m.serve("127.0.0.1", 0)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_address[1]}/metrics"
        with self.assertRaises(urllib.error.HTTPError) as no:
            urllib.request.urlopen(url, timeout=5)
        self.assertEqual(no.exception.code, 401)
        req = urllib.request.Request(url, headers={"Authorization": "Bearer t"})
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertIn(b"hikidashi_items", r.read())


class TestSearchTiming(MetricsCase):
    def test_a_search_times_its_parts(self):
        from app import search

        conn = open_db(self.settings.db_path)
        search.rank(conn, "dumpling")
        found = self.families(self.scrape())
        parts = {
            s.labels["part"]
            for s in found["hikidashi_search_duration_seconds"].samples
            if s.name.endswith("_count") and s.value
        }
        self.assertEqual(parts, {"fts", "rank"})


@unittest.skipUnless(importlib.util.find_spec("fastapi"), "the web dependencies are not installed")
class TestHealth(unittest.TestCase):
    def setUp(self):
        from tests import test_api

        if test_api.main is None:
            test_api.setUpModule()  # imports the app with its log in a temporary folder
        self.main = test_api.main
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.settings = test_api.settings_in(Path(self.tmp.name))
        patcher = mock.patch.object(self.main, "settings", self.settings)
        patcher.start()
        self.addCleanup(patcher.stop)

    def client(self):
        from fastapi.testclient import TestClient

        return TestClient(self.main.app)

    def test_healthz_answers_without_signing_in(self):
        r = self.client().get("/healthz")
        self.assertEqual((r.status_code, r.json()), (200, {"status": "ok"}))

    def test_readyz_migrates_an_old_database_and_says_ready(self):
        conn = open_db(self.settings.db_path)
        conn.execute("ALTER TABLE item DROP COLUMN media_note")  # as a release before #8
        conn.close()
        self.assertEqual(self.client().get("/readyz").status_code, 200)

    def test_readyz_fails_when_the_database_cannot_open_or_is_behind(self):
        self.settings.db_path.mkdir(parents=True)  # a folder where the file should be
        self.assertEqual(self.client().get("/readyz").status_code, 503)
        self.settings.db_path.rmdir()
        with mock.patch.object(self.main, "is_current", return_value=False):
            r = self.client().get("/readyz")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(json.loads(r.text)["detail"], "schema not current")


if __name__ == "__main__":
    unittest.main()
