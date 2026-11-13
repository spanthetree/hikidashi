"""Remote workers (#35): the server's side, a worker's claims and results over
HTTP, recorded with the stages' own functions."""

import dataclasses
import importlib.util
import io
import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from app import accounts, config
from app.asr.transcriber import ASR_V
from app.db import open_db
from app.enrich.fetcher import FETCH_V
from app.importer.service import import_export
from app.jobs import protocol, remote
from app.jobs.queue import LEASE, MAX_ATTEMPTS
from app.jobs.worker import Unavailable, Worker
from app.ocr.reader import OCR_V
from app.recipes.llm import RECIPE_V, Found, Ingredient, Recipe
from app.tagging.llm import TAG_V, Result
from tests.fixtures import post_record, saved_posts, write_zip

HAS_WEB = all(importlib.util.find_spec(m) for m in ("fastapi", "httpx", "multipart"))
R = "https://www.instagram.com/reel/"
TOKEN = "s3cret-token"
CODES = ("AAA111", "BBB222", "CCC333")


def settings_in(root: Path, **kw: Any) -> config.Settings:
    return dataclasses.replace(
        config.Settings.from_env(),
        db_path=root / "t.sqlite3",
        media_dir=root / "media",
        imports_dir=root / "imports",
        logs_dir=root / "logs",
        run_dir=root / "run",
        cache_dir=root / "cache",
        **kw,
    )


main: Any = None
_IMPORTED = tempfile.TemporaryDirectory()


def setUpModule():
    global main
    if HAS_WEB:
        with mock.patch.object(config, "settings", settings_in(Path(_IMPORTED.name))):
            from app.api import main


def tearDownModule():
    _IMPORTED.cleanup()


def body(**kw: Any) -> dict[str, Any]:
    return {
        "worker": "desktop",
        "protocol": protocol.PROTOCOL,
        "versions": protocol.versions(),
        **kw,
    }


@unittest.skipUnless(HAS_WEB, "the web dependencies are not installed")
class RemoteCase(unittest.TestCase):
    """Three reels, fetched, each with a video on disk: ready for ASR."""

    def setUp(self):
        from fastapi.testclient import TestClient

        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.settings = settings_in(self.dir)
        patcher = mock.patch.object(main, "settings", self.settings)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.conn = open_db(self.settings.db_path)
        zp = write_zip(
            self.dir / "e.zip",
            saved_posts(
                [
                    post_record(R + "AAA111/", timestamp=300, caption="brake job #cars"),
                    post_record(R + "BBB222/", timestamp=200, caption="ramen"),
                    post_record(R + "CCC333/", timestamp=100, caption="a song"),
                ]
            ),
        )
        import_export(self.conn, zp)
        for code in CODES:
            path = f"video/{code}.mp4"
            (self.settings.media_dir / "video").mkdir(parents=True, exist_ok=True)
            (self.settings.media_dir / path).write_bytes(code.encode())
            self.conn.execute(
                """UPDATE item SET state = 'fetched', fetch_v = ?, duration = 30,
                     media_path = ? WHERE shortcode = ?""",
                (FETCH_V, path, code),
            )
        # A worker token an admin made: the only kind the server takes (#103).
        self.token, _ = accounts.make_worker_token(self.conn, accounts.local(self.conn), "desktop")
        self.client = TestClient(main.app, headers={"Authorization": f"Bearer {self.token}"})

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def row(self, code="AAA111"):
        return self.conn.execute("SELECT * FROM item WHERE shortcode = ?", (code,)).fetchone()

    def post(self, path: str, **kw: Any):
        return self.client.post(f"/api/worker/{path}", json=body(**kw))

    def claim(self, stage="asr", limit=8, **kw: Any) -> list[dict[str, Any]]:
        r = self.post("claim", stage=stage, limit=limit, **kw)
        self.assertEqual(r.status_code, 200, r.text)
        jobs: list[dict[str, Any]] = r.json()["jobs"]
        return jobs

    def results(self, stage: str, results: list[dict[str, Any]], **kw: Any):
        return self.post("results", stage=stage, profile="cuda", results=results, **kw)

    def attempts(self, code: str, stage="asr") -> int:
        r = self.conn.execute(
            "SELECT attempts FROM job_attempt WHERE shortcode = ? AND stage = ?", (code, stage)
        ).fetchone()
        return r[0] if r else 0

    def leases(self) -> list[tuple[str, str, str]]:
        return [
            tuple(r)
            for r in self.conn.execute(
                "SELECT stage, shortcode, worker FROM job_lease ORDER BY shortcode"
            )
        ]


class TestAuth(RemoteCase):
    def test_off_without_a_token_made(self):
        self.conn.execute("DELETE FROM api_token")
        r = self.post("claim", stage="asr")
        self.assertEqual(r.status_code, 404)

    def test_a_wrong_or_missing_token_is_refused(self):
        r = self.client.post(
            "/api/worker/claim", json=body(stage="asr"), headers={"Authorization": "Bearer nope"}
        )
        self.assertEqual(r.status_code, 401)
        r = self.client.post(
            "/api/worker/claim", json=body(stage="asr"), headers={"Authorization": ""}
        )
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.leases(), [])

    def test_a_user_session_is_not_a_worker(self):
        from fastapi.testclient import TestClient

        r = TestClient(main.app).post("/api/worker/claim", json=body(stage="asr"))
        self.assertEqual(r.status_code, 401)


class TestClaim(RemoteCase):
    def test_sends_the_rows_and_leases_them(self):
        jobs = self.claim("asr", limit=2)
        self.assertEqual([j["shortcode"] for j in jobs], ["AAA111", "BBB222"])
        self.assertEqual(jobs[0]["media_path"], "video/AAA111.mp4")
        self.assertEqual(protocol.media(jobs[0], "asr"), ["video/AAA111.mp4"])
        self.assertEqual(
            self.leases(), [("asr", "AAA111", "desktop"), ("asr", "BBB222", "desktop")]
        )
        # Another worker gets what is left.
        r = self.client.post(
            "/api/worker/claim", json={**body(stage="asr"), "worker": "laptop"}
        ).json()
        self.assertEqual([j["shortcode"] for j in r["jobs"]], ["CCC333"])

    def test_waits_on_the_stages_before(self):
        self.conn.execute("UPDATE item SET fetch_v = 0 WHERE shortcode = 'AAA111'")
        self.assertEqual([j["shortcode"] for j in self.claim("asr")], ["BBB222", "CCC333"])
        self.assertEqual(self.claim("tag"), [])  # nothing transcribed yet

    def test_enrich_and_embed_stay_on_the_server(self):
        for stage in ("enrich", "embed", "places"):
            r = self.post("claim", stage=stage)
            self.assertEqual(r.status_code, 409, stage)
            self.assertIn("does not run remotely", r.json()["detail"])

    def test_a_worker_on_another_version_is_refused(self):
        r = self.post("claim", stage="asr", versions={**protocol.versions(), "asr": ASR_V + 1})
        self.assertEqual(r.status_code, 409)
        self.assertIn("same Hikidashi release", r.json()["detail"])
        r = self.post("claim", stage="asr", protocol=protocol.PROTOCOL + 1)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.leases(), [])

    def test_a_tag_claim_runs_the_hashtag_pass_first(self):
        self.conn.execute("UPDATE item SET asr_v = ?, ocr_v = ?", (ASR_V, OCR_V))
        with mock.patch("app.tagging.service.apply_hashtags") as hashtags:
            self.claim("tag")
            self.claim("asr")
        hashtags.assert_called_once()


class TestResults(RemoteCase):
    def test_a_transcript_is_recorded_as_a_local_run_would(self):
        self.claim("asr")
        r = self.results(
            "asr",
            [
                protocol.ok("asr", "AAA111", "you do not need a press for this"),
                protocol.ok("asr", "BBB222", None),  # no speech
                protocol.failed("CCC333", "AudioError: corrupt file"),
            ],
        )
        self.assertEqual(r.status_code, 200, r.text)
        out = r.json()
        self.assertEqual(out["recorded"], ["AAA111", "BBB222", "CCC333"])
        self.assertEqual(out["done"], ["AAA111", "BBB222"])
        self.assertEqual(out["again"], ["CCC333"])
        a = self.row()
        self.assertEqual(
            (a["transcript"], a["asr_v"], a["asr_profile"], a["state"]),
            ("you do not need a press for this", ASR_V, "cuda", "transcribed"),
        )
        self.assertEqual(self.row("BBB222")["asr_v"], ASR_V)
        c = self.row("CCC333")
        self.assertEqual((c["fail_stage"], c["fail_reason"]), ("asr", "AudioError: corrupt file"))
        self.assertEqual(c["asr_v"], 0)
        self.assertEqual(self.attempts("CCC333"), 1)
        self.assertEqual(self.leases(), [])

    def test_a_reel_failing_every_time_is_failed_at_the_cap(self):
        for _ in range(MAX_ATTEMPTS):
            self.claim("asr")
            out = self.results("asr", [protocol.failed("AAA111", "boom")]).json()
            self.post("release", stage="asr", shortcodes=["BBB222", "CCC333"], count=False)
            # Past the pause before the next try.
            self.conn.execute("UPDATE job_attempt SET last_at = last_at - 3600")
        self.assertEqual(out["failed"], ["AAA111"])
        self.assertNotIn("AAA111", [j["shortcode"] for j in self.claim("asr")])

    def test_a_reel_it_no_longer_holds_is_not_recorded(self):
        self.claim("asr", limit=1)
        r = self.results(
            "asr",
            [protocol.ok("asr", "AAA111", "mine"), protocol.ok("asr", "BBB222", "not mine")],
        ).json()
        self.assertEqual(r["recorded"], ["AAA111"])
        self.assertEqual(self.row("BBB222")["asr_v"], 0)

    def test_a_malformed_result_records_nothing(self):
        self.claim("asr")
        bad = [
            [protocol.ok("asr", "AAA111", "fine"), {"shortcode": "BBB222", "transcript": 7}],
            [{"shortcode": "AAA111"}],
            {"not": "a list"},
            [{"shortcode": "AAA111", "transcript": "x" * (protocol.TEXT + 1)}],
        ]
        for results in bad:
            r = self.post("results", stage="asr", profile="cuda", results=results)
            self.assertEqual(r.status_code, 422, results)
        self.assertEqual(self.row()["asr_v"], 0)
        r = self.post("results", stage="asr", profile="tpu", results=[])
        self.assertEqual(r.status_code, 422)

    def test_ocr_text_is_recorded(self):
        self.conn.execute("UPDATE item SET asr_v = ?", (ASR_V,))  # all found no speech
        self.claim("ocr")
        self.results("ocr", [protocol.ok("ocr", "AAA111", "TORQUE 90 NM")])
        self.assertEqual((self.row()["ocr_text"], self.row()["ocr_v"]), ("TORQUE 90 NM", OCR_V))

    def test_a_tag_is_recorded_and_checked_against_the_taxonomy(self):
        self.conn.execute("UPDATE item SET asr_v = ?, ocr_v = ?", (ASR_V, OCR_V))
        self.claim("tag")
        good = Result(
            summary="Changing brake pads.",
            category="Automotive",
            confidence=0.9,
            keywords=["brakes"],
        )
        made_up = Result(summary="A song.", category="Not A Category", confidence=0.9)
        self.results(
            "tag", [protocol.ok("tag", "AAA111", good), protocol.ok("tag", "CCC333", made_up)]
        )
        a = self.row()
        self.assertEqual(
            (a["summary"], a["tag_v"], a["tag_profile"]), ("Changing brake pads.", TAG_V, "cuda")
        )
        self.assertEqual(json.loads(a["keywords"]), ["brakes"])
        model_tags = dict(
            self.conn.execute(
                """SELECT it.shortcode, t.name FROM item_tag it JOIN tag t ON t.id = it.tag_id
                   WHERE it.source = 'model'"""
            ).fetchall()
        )
        self.assertEqual(model_tags.get("AAA111"), "Automotive")
        self.assertNotIn("CCC333", model_tags)  # no tag, as the tagger's own parse
        self.assertEqual(self.row("CCC333")["tag_v"], TAG_V)

    def test_a_recipe_is_recorded(self):
        self.conn.execute("UPDATE item SET asr_v = ?, ocr_v = ?, tag_v = ?", (ASR_V, OCR_V, TAG_V))
        self.claim("recipe")
        found = Found(
            recipe=Recipe(
                title="Shoyu ramen",
                servings=2,
                ingredients=[Ingredient(item="noodles", quantity="200", unit="g")],
                steps=["Boil the noodles."],
            ),
            confidence=0.9,
            source_fields=["caption"],
            dropped=[],
        )
        r = self.results(
            "recipe",
            [protocol.ok("recipe", "BBB222", found), protocol.ok("recipe", "AAA111", None)],
        )
        self.assertEqual(r.status_code, 200, r.text)
        rec = self.conn.execute(
            "SELECT title, servings FROM recipe WHERE shortcode = 'BBB222'"
        ).fetchone()
        self.assertEqual(tuple(rec), ("Shoyu ramen", 2))
        self.assertEqual(self.row("BBB222")["recipe_v"], RECIPE_V)
        self.assertEqual(self.row("AAA111")["recipe_v"], RECIPE_V)


class TestRelease(RemoteCase):
    def test_a_stage_that_could_not_run_counts_nothing(self):
        self.claim("asr")
        r = self.post("release", stage="asr", shortcodes=list(CODES), count=False)
        self.assertEqual(r.json()["again"], [])
        self.assertEqual([self.attempts(c) for c in CODES], [0, 0, 0])
        self.assertEqual(self.leases(), [])


class TestCheckIn(RemoteCase):
    def test_shows_in_the_pipeline_and_keeps_the_leases(self):
        self.claim("asr", limit=1)
        self.conn.execute("UPDATE job_lease SET expires_at = expires_at - ?", (LEASE - 10,))
        r = self.post(
            "check-in",
            stages=["asr", "tag"],
            started_at=1.0,
            doing="asr",
            problems={"tag": "Ollama is not running"},
            pid=4242,
            renew=True,
        )
        self.assertEqual(r.status_code, 200, r.text)
        left = self.conn.execute("SELECT expires_at - unixepoch() FROM job_lease").fetchone()[0]
        self.assertGreater(left, LEASE - 5)
        w = self.conn.execute("SELECT * FROM job_worker WHERE name = 'desktop'").fetchone()
        self.assertEqual((w["pid"], w["doing"]), (4242, "asr"))
        self.assertEqual(json.loads(w["stages"]), ["asr", "tag"])

    def test_check_out_gives_the_leases_back_uncounted(self):
        self.claim("asr")
        self.post("check-in", stages=["asr"], started_at=1.0)
        self.post("check-out")
        self.assertEqual(self.leases(), [])
        self.assertEqual(self.attempts("AAA111"), 0)

    def test_reclaim_takes_back_a_dead_run_and_counts_it(self):
        self.claim("asr", limit=1)
        self.assertEqual(self.post("reclaim").json(), {"taken": 1})
        self.assertEqual(self.attempts("AAA111"), 1)


class TestMedia(RemoteCase):
    def get(self, path: str):
        return self.client.get(f"/api/worker/media/{path}")

    def test_serves_a_reels_video_with_byte_ranges(self):
        r = self.get("video/AAA111.mp4")
        self.assertEqual((r.status_code, r.content), (200, b"AAA111"))
        r = self.client.get("/api/worker/media/video/AAA111.mp4", headers={"Range": "bytes=0-2"})
        self.assertEqual((r.status_code, r.content), (206, b"AAA"))

    def test_only_a_reels_own_files(self):
        (self.settings.media_dir / "other.bin").write_bytes(b"x")
        self.assertEqual(self.get("other.bin").status_code, 404)
        self.assertEqual(self.get("../t.sqlite3").status_code, 404)
        self.conn.execute("UPDATE item SET media_path = '../t.sqlite3' WHERE shortcode = 'CCC333'")
        self.assertEqual(self.get("../t.sqlite3").status_code, 404)

    def test_needs_the_token(self):
        from fastapi.testclient import TestClient

        r = TestClient(main.app).get("/api/worker/media/video/AAA111.mp4")
        self.assertEqual(r.status_code, 401)


class TestProtocol(unittest.TestCase):
    def test_a_recipe_survives_the_round_trip(self):
        found = Found(
            recipe=Recipe(
                title="t",
                time_minutes=5,
                ingredients=[Ingredient(item="egg", note="soft", as_written="1 soft egg")],
                steps=["a", "b"],
                incomplete=True,
            ),
            confidence=0.6,
            source_fields=["transcript"],
            dropped=["salt"],
        )
        sent = json.loads(json.dumps(protocol.ok("recipe", "X", found)))
        [(code, got, why)] = protocol.parse("recipe", [sent])
        self.assertEqual((code, got, why), ("X", found, None))

    def test_a_timed_transcript_round_trips(self):
        from app.asr.transcriber import Said

        heard = Said("salt the water", ((0, 900, "salt"), (900, 2000, "the water")))
        sent = json.loads(json.dumps(protocol.ok("asr", "X", heard)))
        self.assertEqual(protocol.parse("asr", [sent]), [("X", heard, None)])
        sent["segments"] = [[0, -1, "salt"]]
        with self.assertRaises(protocol.Refused):
            protocol.parse("asr", [sent])

    def test_translations_travel_with_the_recipe(self):
        from app.recipes.llm import Found, Recipe

        body = {"title": "トースト", "ingredients": [], "steps": ["焼く"]}
        found = Found(Recipe(title="Toast", steps=["fry"]), 0.9, [], [], {"ja": body})
        sent = json.loads(json.dumps(protocol.ok("recipe", "X", found)))
        [(_, got, _)] = protocol.parse("recipe", [sent])
        self.assertEqual(got.translations, {"ja": body})
        sent["recipe"]["translations"] = {"xx": body}
        with self.assertRaises(protocol.Refused):
            protocol.parse("recipe", [sent])

    def test_a_long_failure_reason_is_cut_short(self):
        [(_, _, why)] = protocol.parse("asr", [{"shortcode": "X", "error": "e" * 5000}])
        self.assertEqual(len(why or ""), protocol.REASON)

    def test_a_confidence_out_of_range_is_refused(self):
        for c in (1.5, -0.1, float("nan"), "high", True):
            with self.assertRaises(protocol.Refused, msg=c):
                protocol.parse(
                    "tag",
                    [
                        {
                            "shortcode": "X",
                            "tag": {
                                "summary": "s",
                                "category": "Automotive",
                                "confidence": c,
                                "keywords": [],
                            },
                        }
                    ],
                )


# --- the worker's side ---------------------------------------------------------

SPOKEN = {"AAA111": "you do not need a press for this", "BBB222": None, "CCC333": "la la"}


def tagger(prompt: str) -> str:
    return json.dumps(
        {"summary": "A reel.", "category": "Automotive", "confidence": "high", "keywords": ["x"]}
    )


class FakeRecipes:
    def check(self, prompt: str) -> str:
        return json.dumps({"is_recipe": False})

    def extract(self, prompt: str) -> str:
        raise AssertionError("never extracted")


class WorkerCase(RemoteCase):
    """The real worker loop and HttpJobs, talking to the real API through the
    test client, with every model faked."""

    def setUp(self):
        super().setUp()
        quiet = mock.patch.object(logging.getLogger("hikidashi.worker"), "disabled", True)
        quiet.start()
        self.addCleanup(quiet.stop)
        # The "audio" of a downloaded file is its contents: the shortcode.
        patch = mock.patch(
            "app.asr.worker.extract_audio", side_effect=lambda path: Path(path).read_bytes()
        )
        patch.start()
        self.addCleanup(patch.stop)
        self.cache = self.dir / "desktop-cache"
        self.offline = False
        self.sent: list[str] = []

    def send(self, method: str, path: str, data: dict[str, Any] | None) -> tuple[int, bytes]:
        if self.offline:
            raise ConnectionRefusedError("connection refused")
        self.sent.append(path)
        r = self.client.request(method, path, json=data)
        return r.status_code, r.content

    def fetch(self, path: str, dest: Path) -> None:
        if self.offline:
            raise ConnectionRefusedError("connection refused")
        r = self.client.get("/api/worker/media/" + path)
        if r.status_code == 404:
            raise remote.Missing(f"the server has no {path}")
        dest.write_bytes(r.content)

    def jobs(self, name: str = "desktop") -> remote.HttpJobs:
        return remote.HttpJobs(
            "http://server.invalid", TOKEN, name, send=self.send, fetch=self.fetch
        )

    def worker(self, stages=protocol.REMOTE, **kw: Any) -> Worker:
        source = self.jobs()
        runners = remote.RemoteRunners(
            media_dir=self.cache,
            models_dir=self.dir / "models",
            ollama_url="http://ollama.invalid",
            profile="cpu",
            whisper=lambda profile, models: lambda pcm: SPOKEN[pcm.decode()],
            tagger=lambda url, model: tagger,
            recipe_model=lambda url, model: FakeRecipes(),
            ocr_reader=lambda row, media: "TEXT ON SCREEN",
            jobs=source,
            **kw,
        )
        return Worker(
            source, None, runners, stages=stages, run_dir=self.dir / "run", sleep=lambda s: None
        )


class TestRemoteWorker(WorkerCase):
    def test_works_the_library_down_through_the_server(self):
        w = self.worker()
        w.start()
        w.drain()
        a, b, c = (self.row(code) for code in CODES)
        self.assertEqual((a["transcript"], a["asr_profile"]), (SPOKEN["AAA111"], "cpu"))
        # No speech in BBB222, so OCR read it; the tagger read them all.
        self.assertEqual((b["asr_v"], b["ocr_text"]), (ASR_V, "TEXT ON SCREEN"))
        self.assertIsNone(a["ocr_text"])
        for row in (a, b, c):
            self.assertEqual((row["tag_v"], row["tag_profile"]), (TAG_V, "cpu"))
            self.assertEqual(row["recipe_v"], RECIPE_V)
            self.assertEqual(row["embed_v"], 0)  # embed stays on the server
        # The media it read are in its cache, as the server's paths.
        self.assertEqual((self.cache / "video/AAA111.mp4").read_bytes(), b"AAA111")
        self.assertEqual(self.leases(), [])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM job_attempt").fetchone()[0], 0)
        w.stop()
        worker_row = self.conn.execute("SELECT * FROM job_worker").fetchone()
        self.assertEqual(worker_row["name"], "desktop")
        self.assertIsNotNone(worker_row["stopped_at"])

    def test_a_failure_is_counted_once(self):
        def whisper(pcm: bytes) -> str | None:
            if pcm == b"AAA111":
                raise RuntimeError("CUDA out of memory")
            return SPOKEN[pcm.decode()]

        w = self.worker(stages=("asr",))
        assert isinstance(w.runners, remote.RemoteRunners)
        w.runners.whisper = lambda profile, models: whisper
        w.round()
        self.assertEqual(self.attempts("AAA111"), 1)
        self.assertEqual(self.row()["fail_reason"], "RuntimeError: CUDA out of memory")
        self.assertNotIn("/api/worker/release", self.sent)  # the results released them

    def test_a_file_the_server_lost_fails_that_reel_only(self):
        (self.settings.media_dir / "video/AAA111.mp4").unlink()
        w = self.worker(stages=("asr",))
        w.round()
        self.assertEqual(self.row()["fail_stage"], "asr")
        self.assertIn("has no video/AAA111.mp4", self.row()["fail_reason"])
        self.assertEqual(self.row("BBB222")["asr_v"], ASR_V)

    def test_a_cached_file_is_not_downloaded_again(self):
        (self.cache / "video").mkdir(parents=True)
        (self.cache / "video/AAA111.mp4").write_bytes(b"AAA111")
        fetched: list[str] = []
        real = self.fetch

        def counting(path: str, dest: Path) -> None:
            fetched.append(path)
            real(path, dest)

        self.fetch = counting  # type: ignore[method-assign]
        self.worker(stages=("asr",)).round()
        self.assertEqual(fetched, ["video/BBB222.mp4", "video/CCC333.mp4"])

    def test_ollama_off_gives_the_reels_back_uncounted(self):
        from app.ollama import OllamaError

        def down(prompt: str) -> str:
            raise OllamaError("Ollama is not running")

        self.conn.execute("UPDATE item SET asr_v = ?, ocr_v = ?", (ASR_V, OCR_V))
        w = self.worker(stages=("tag",))
        assert isinstance(w.runners, remote.RemoteRunners)
        w.runners.tagger = lambda url, model: down
        w.round()
        self.assertEqual(w.problems, {"tag": "Ollama is not running"})
        self.assertEqual([self.attempts(c, "tag") for c in CODES], [0, 0, 0])
        self.assertEqual(self.leases(), [])
        # The server says so too: the worker's check-in carries the problem.
        w._check_in()
        problems = self.conn.execute("SELECT problems FROM job_worker").fetchone()[0]
        self.assertEqual(json.loads(problems), {"tag": "Ollama is not running"})

    def test_the_server_away_is_waited_out(self):
        w = self.worker(stages=("asr",))
        self.offline = True
        self.assertEqual(w.round(), 0)
        self.offline = False
        self.assertEqual(w.round(), 3)

    def test_the_server_going_away_mid_batch_is_waited_out(self):
        w = self.worker(stages=("asr",))
        assert isinstance(w.runners, remote.RemoteRunners)

        def gone(pcm: bytes) -> str | None:
            self.offline = True
            return "words"

        w.runners.whisper = lambda profile, models: gone
        self.assertEqual(w.round(), 0)
        self.assertEqual(self.row()["asr_v"], 0)  # nothing it could not send is recorded

    def test_a_server_on_another_release_stops_it(self):
        w = self.worker(stages=("asr",))
        with (
            mock.patch("app.jobs.protocol.versions", return_value={"asr": ASR_V + 1}),
            self.assertRaises(remote.Refused),
        ):
            w.round()

    def test_server_stages_are_not_run(self):
        with self.assertRaises(Unavailable):
            self.worker().runners.run("embed", None, ["AAA111"])

    def test_the_cache_keeps_the_newest_used(self):
        runners = self.worker().runners
        assert isinstance(runners, remote.RemoteRunners)
        self.cache.mkdir()
        for i, name in enumerate(("old", "mid", "new")):
            f = self.cache / name
            f.write_bytes(b"x" * 100)
            os.utime(f, (1000 + i, 1000 + i))
        runners.cache_max = 250
        self.assertEqual(runners.prune(), 100)
        self.assertEqual(sorted(f.name for f in self.cache.iterdir()), ["mid", "new"])


class TestCommand(unittest.TestCase):
    def run_cli(self, *argv: str, tty: bool = False, typed: str = "", **env: Any) -> int:
        """`hikidashi worker` with `env` in place of the environment, at a
        terminal or not, where `typed` is what the user types when asked. It
        stops where it would start working, with what it was given in
        self.remote."""
        from app.jobs import __main__ as cli

        self.remote = mock.Mock(side_effect=remote.Refused("stop here"))
        self.ask = mock.Mock(return_value=typed)
        with tempfile.TemporaryDirectory() as d:
            # A worker's HIKIDASHI_WORKER_TOKEN, the token it sends, unless a
            # test says otherwise.
            patched = dataclasses.replace(settings_in(Path(d)), **{"worker_token": TOKEN, **env})
            out, err = io.StringIO(), io.StringIO()
            with (
                mock.patch.object(cli, "settings", patched),
                mock.patch.object(cli, "_remote", self.remote),
                mock.patch("getpass.getpass", self.ask),
                mock.patch("sys.stdin.isatty", return_value=tty),
                mock.patch("logging.basicConfig"),
                mock.patch("app.logs.install"),
                mock.patch("sys.stdout", out),
                mock.patch("sys.stderr", err),
            ):
                code = cli.main(list(argv))
        self.out, self.err = out.getvalue(), err.getvalue()
        return code

    def test_a_remote_worker_needs_the_token(self):
        self.assertEqual(self.run_cli("--server", "http://x", worker_token=None), 2)
        self.assertIn("HIKIDASHI_WORKER_TOKEN", self.err)
        self.ask.assert_not_called()  # no terminal to ask at: Docker, a service

    def test_the_token_comes_from_the_environment_first(self):
        self.run_cli("--server", "http://x", tty=True)
        self.ask.assert_not_called()
        self.assertEqual(self.remote.call_args.args[1], TOKEN)

    def test_with_no_token_set_it_asks_for_one_at_the_terminal(self):
        self.run_cli("--server", "http://x", tty=True, typed="typed-token\n", worker_token=None)
        self.ask.assert_called_once()
        self.assertEqual(self.remote.call_args.args[1], "typed-token")
        # Typed, the token is never echoed or printed back.
        self.assertNotIn("typed-token", self.out + self.err + str(self.ask.call_args))

    def test_nothing_typed_is_no_token(self):
        code = self.run_cli("--server", "http://x", tty=True, typed="  ", worker_token=None)
        self.assertEqual(code, 2)
        self.remote.assert_not_called()

    def test_profile_and_ollama_default_to_the_environment(self):
        self.run_cli("--server", "http://x", profile="cpu", ollama_url="http://gpu:11434")
        self.assertEqual(self.remote.call_args.args[4:], ("cpu", "http://gpu:11434"))

    def test_flags_win_over_the_environment(self):
        self.run_cli(
            "--server", "http://x", "--profile", "cuda", "--ollama-url", "http://other:11434",
            profile="cpu", ollama_url="http://gpu:11434",
        )  # fmt: skip
        self.assertEqual(self.remote.call_args.args[4:], ("cuda", "http://other:11434"))

    def test_an_unknown_profile_is_refused(self):
        with self.assertRaises(SystemExit):
            self.run_cli("--server", "http://x", "--profile", "tpu")

    def test_settings_never_show_the_token(self):
        # A traceback or a log line that prints the settings.
        self.assertNotIn(TOKEN, repr(settings_in(Path("."))))

    def test_server_stages_are_refused(self):
        code = self.run_cli("--server", "http://x", "--stages", "asr,embed", worker_token="t")
        self.assertEqual(code, 2)
        self.assertIn("embed run on the server", self.err)

    def test_retry_failed_is_for_the_server(self):
        code = self.run_cli("--server", "http://x", "--retry-failed", worker_token="t")
        self.assertEqual(code, 2)


class TestWorkerTokens(RemoteCase):
    """Tokens an admin makes on the Pipeline page."""

    def setUp(self):
        super().setUp()
        from fastapi.testclient import TestClient

        self.conn.execute("DELETE FROM api_token")  # none yet
        self.admin = TestClient(main.app)  # HIKIDASHI_AUTH=none: user 1, an admin

    def make(self, name="desktop") -> str:
        r = self.admin.post("/api/worker-tokens", json={"name": name})
        self.assertEqual(r.status_code, 200, r.text)
        token: str = r.json()["token"]
        return token

    def as_worker(self, token: str):
        return self.client.post(
            "/api/worker/claim",
            json=body(stage="asr"),
            headers={"Authorization": f"Bearer {token}"},
        )

    def test_off_until_an_admin_makes_one(self):
        self.assertEqual(self.as_worker("anything").status_code, 404)
        token = self.make()
        self.assertEqual(self.as_worker(token).status_code, 200)
        self.assertEqual(self.as_worker("anything").status_code, 401)

    def test_listed_without_the_token_and_marked_when_used(self):
        token = self.make("  my   desktop ")
        [t] = self.admin.get("/api/worker-tokens").json()
        self.assertEqual(
            (t["name"], t["created_by"], t["last_used_at"]), ("my desktop", "local", None)
        )
        self.assertNotIn("token", t)
        self.assertNotIn(token, json.dumps(t))
        stored = self.conn.execute("SELECT token_hash FROM api_token").fetchone()[0]
        self.assertNotEqual(stored, token)
        self.as_worker(token)
        [t] = self.admin.get("/api/worker-tokens").json()
        self.assertIsNotNone(t["last_used_at"])

    def test_revoked_is_refused_at_once(self):
        keep, drop = self.make("desktop"), self.make("laptop")
        listed = self.admin.get("/api/worker-tokens").json()
        laptop = next(t["id"] for t in listed if t["name"] == "laptop")
        left = self.admin.delete(f"/api/worker-tokens/{laptop}").json()
        self.assertEqual([t["name"] for t in left], ["desktop"])
        self.assertEqual(self.as_worker(drop).status_code, 401)
        self.assertEqual(self.as_worker(keep).status_code, 200)

    def test_the_servers_environment_token_is_retired(self):
        # HIKIDASHI_WORKER_TOKEN is what a worker sends, not a secret the
        # server takes (#103): set on the server, it opens nothing.
        self.make()
        with mock.patch.object(
            main, "settings", dataclasses.replace(self.settings, worker_token=TOKEN)
        ):
            self.assertEqual(self.as_worker(TOKEN).status_code, 401)
            with self.assertLogs(main.log, logging.WARNING) as logged:
                main.announce_setup()
        self.assertIn("the server does not take it", "\n".join(logged.output))

    def test_a_name_is_needed(self):
        for name in ("", "   ", "x" * 65):
            r = self.admin.post("/api/worker-tokens", json={"name": name})
            self.assertEqual(r.status_code, 400, name)

    def test_admins_only(self):
        from app.accounts import User

        main.app.dependency_overrides[main.current_user] = lambda: User(id=2, username="bob")
        self.addCleanup(main.app.dependency_overrides.clear)
        self.assertEqual(self.admin.get("/api/worker-tokens").status_code, 403)
        self.assertEqual(self.admin.post("/api/worker-tokens", json={"name": "x"}).status_code, 403)
        self.assertEqual(self.admin.delete("/api/worker-tokens/1").status_code, 403)

    def test_a_worker_token_is_not_a_session(self):
        token = self.make()
        from fastapi.testclient import TestClient

        anon = TestClient(main.app, headers={"Authorization": f"Bearer {token}"})
        with mock.patch.object(
            main, "settings", dataclasses.replace(self.settings, worker_token=None, auth="local")
        ):
            self.assertEqual(anon.get("/api/worker-tokens").status_code, 401)


class TestGameProtocol(unittest.TestCase):
    """The model's game answer goes to the server as said; the server decides."""

    def test_a_game_answer_survives_the_round_trip(self):
        from app.tagging import games
        from app.tagging.llm import Result

        r = Result(
            summary="s",
            category="Gaming",
            confidence=0.9,
            keywords=["k"],
            game=games.Answer("one", "Grand Turismo 4", "high", ("iRacing",), "Gran Turismo 4"),
        )
        for result in (r, Result(summary=None)):
            sent = json.loads(json.dumps(protocol.ok("tag", "X", result)))
            [(_, got, _)] = protocol.parse("tag", [sent])
            self.assertEqual(got, result)

    def test_a_result_from_before_the_game_pass_has_no_game(self):
        sent = {"shortcode": "X", "tag": {"summary": "s", "category": None, "keywords": []}}
        [(_, got, _)] = protocol.parse("tag", [sent])
        self.assertIsNone(got.game)

    def test_a_malformed_game_is_refused(self):
        for game in (
            "Minecraft",
            {"kind": "maybe", "title": "x", "confidence": "high"},
            {"kind": "one", "title": "x", "confidence": 0.9},
            {"kind": "one", "title": "x" * 500, "confidence": "high"},
            {"kind": "one", "title": "x", "confidence": "high", "others": ["y"] * 50},
        ):
            sent = {"shortcode": "X", "tag": {"summary": "s", "keywords": [], "game": game}}
            with self.subTest(game), self.assertRaises(protocol.Refused):
                protocol.parse("tag", [sent])
