"""The job model (#72): leases, readiness, the attempts cap, and check-ins."""

import dataclasses
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import dupes, jobs, profiles
from app.asr.transcriber import ASR_V
from app.config import Settings
from app.db import connect, open_db
from app.enrich.fetcher import FETCH_V
from app.importer.service import import_export
from app.jobs import queue
from app.jobs.queue import DIED, LEASE, MAX_ATTEMPTS, STALE, LocalJobs
from app.tagging.llm import TAG_V
from tests.fixtures import post_record, saved_posts, write_zip

R = "https://www.instagram.com/reel/"


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class JobsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.path = self.dir / "test.sqlite3"
        self.conn = open_db(self.path)
        zp = write_zip(
            self.dir / "e.zip",
            saved_posts(
                [
                    post_record(R + "AAA111/", timestamp=300, caption="brake job on the e30"),
                    post_record(R + "BBB222/", timestamp=200, caption="ramen at home tonight"),
                    post_record(R + "CCC333/", timestamp=100, caption="a song for the drive"),
                ]
            ),
        )
        import_export(self.conn, zp)
        self.clock = Clock()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def jobs(self, name="a", conn=None, **kw) -> LocalJobs:
        # No pause between tries unless a test is about the pause.
        return LocalJobs(conn or self.conn, name, clock=self.clock, **{"retry_after": 0, **kw})

    def other_conn(self) -> sqlite3.Connection:
        conn = connect(self.path)
        self.addCleanup(conn.close)
        return conn

    def fetched(self, *codes):
        """As enrich leaves a reel: fetched, with a video and a thumbnail."""
        for c in codes:
            self.conn.execute(
                """UPDATE item SET state = 'fetched', fetch_v = ?, media_path = ?,
                       thumb_path = ? WHERE shortcode = ?""",
                (FETCH_V, f"video/{c}.mp4", f"thumbs/{c}.jpg", c),
            )

    def transcribed(self, *codes):
        for c in codes:
            self.conn.execute(
                "UPDATE item SET asr_v = ?, transcript = 'words' WHERE shortcode = ?", (ASR_V, c)
            )

    def tagged(self, *codes):
        for c in codes:
            self.conn.execute(
                "UPDATE item SET tag_v = ?, summary = 'a reel' WHERE shortcode = ?", (TAG_V, c)
            )

    def attempts(self, code, stage):
        row = self.conn.execute(
            "SELECT attempts, reason FROM job_attempt WHERE shortcode = ? AND stage = ?",
            (code, stage),
        ).fetchone()
        return (row["attempts"], row["reason"]) if row else (0, None)

    def counts(self, stage):
        return next(q for q in queue.queue(self.conn, now=self.clock()) if q["stage"] == stage)


class TestStages(unittest.TestCase):
    def test_all_none_and_a_list(self):
        self.assertEqual(jobs.parse_stages("all"), jobs.STAGES)
        self.assertEqual(jobs.parse_stages(""), jobs.STAGES)
        self.assertEqual(jobs.parse_stages("none"), ())
        self.assertEqual(jobs.parse_stages("ocr, asr"), ("asr", "ocr"))
        self.assertEqual(jobs.parse_stages("transcribe recipes"), ("asr", "recipe"))

    def test_an_unknown_stage_is_named(self):
        with self.assertRaisesRegex(ValueError, "places"):
            jobs.parse_stages("asr,places")

    def test_this_hosts_stages_come_from_the_environment(self):
        with mock.patch.dict(os.environ, {"HIKIDASHI_LOCAL_STAGES": "asr,ocr"}):
            self.assertEqual(Settings.from_env().local_stages, ("asr", "ocr"))
        with mock.patch.dict(os.environ, {"HIKIDASHI_LOCAL_STAGES": "none"}):
            self.assertEqual(Settings.from_env().local_stages, ())
        with mock.patch.dict(os.environ, {"HIKIDASHI_LOCAL_STAGES": "gpu"}):
            with self.assertRaisesRegex(ValueError, "HIKIDASHI_LOCAL_STAGES"):
                Settings.from_env()

    def test_a_containers_cpu_limit_is_the_cpus_there_are(self):
        with mock.patch.object(Path, "read_text", return_value="150000 100000\n"):
            self.assertEqual(profiles.cpus(), 2)
        with mock.patch.object(Path, "read_text", return_value="max 100000\n"):
            self.assertEqual(profiles.cpus(), len(os.sched_getaffinity(0)))
        with mock.patch.object(Path, "read_text", side_effect=FileNotFoundError):
            self.assertEqual(profiles.cpus(), len(os.sched_getaffinity(0)))

    def test_wake_is_seen_across_processes(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(jobs.woken_at(Path(d)), 0)
            jobs.wake(Path(d) / "run")
            self.assertGreater(jobs.woken_at(Path(d) / "run"), 0)


class TestLeases(JobsCase):
    def test_each_stage_can_be_narrowed_to_the_reels_leased(self):
        def narrowed(*stages):
            for name in stages:
                stage = queue.REGISTRY[name]
                every = stage.pending(self.conn, None)
                self.assertTrue(every, name)
                self.assertEqual(stage.pending(self.conn, every[-1:]), every[-1:], name)
                self.assertEqual(stage.pending(self.conn, []), [], name)

        self.fetched("AAA111", "BBB222")
        self.transcribed("AAA111")
        narrowed("enrich", "asr", "tag")
        self.tagged("AAA111")
        # A photo post: no video, so it is read for on-screen text.
        self.conn.execute("UPDATE item SET media_path = NULL WHERE shortcode = 'BBB222'")
        narrowed("ocr", "recipe", "embed")

    def test_a_fresh_import_is_offered_to_enrich_only(self):
        a = self.jobs()
        for stage in ("asr", "ocr", "tag", "recipe", "embed"):
            self.assertEqual(a.claim(stage, 10), [], stage)
        self.assertEqual(a.claim("enrich", 10), ["AAA111", "BBB222", "CCC333"])

    def test_two_workers_never_hold_the_same_reel(self):
        a, b = self.jobs("a"), self.jobs("b", self.other_conn())
        got_a = a.claim("enrich", 2)
        got_b = b.claim("enrich", 2)
        self.assertEqual(got_a, ["AAA111", "BBB222"])
        self.assertEqual(got_b, ["CCC333"])
        self.assertEqual(b.claim("enrich", 2), [])

    def test_a_lease_kept_renewed_is_kept(self):
        a, b = self.jobs("a"), self.jobs("b")
        a.claim("enrich", 3)
        self.clock.t += LEASE - 10
        a.renew()
        self.clock.t += 20
        self.assertEqual(b.claim("enrich", 3), [])

    def test_an_expired_lease_goes_back_to_the_pool_and_counts(self):
        a, b = self.jobs("a"), self.jobs("b")
        a.claim("enrich", 1)
        self.clock.t += LEASE + 1
        self.assertEqual(b.claim("enrich", 1), ["AAA111"])
        self.assertEqual(self.attempts("AAA111", "enrich"), (1, DIED))

    def test_finished_reels_are_released_with_their_count_cleared(self):
        a = self.jobs()
        a.claim("enrich", 1)
        a.release("enrich", ["AAA111"])  # tried once, not done
        a.claim("enrich", 1)
        self.fetched("AAA111")
        out = a.release("enrich", ["AAA111"])
        self.assertEqual(out.done, ["AAA111"])
        self.assertEqual(self.attempts("AAA111", "enrich"), (0, None))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM job_lease").fetchone()[0], 0)

    def test_the_stages_own_reason_is_kept(self):
        a = self.jobs()
        a.claim("enrich", 1)
        self.conn.execute(
            """UPDATE item SET state = 'failed', fail_stage = 'fetch',
                   fail_reason = 'rate limited', attempts = 1 WHERE shortcode = 'AAA111'"""
        )
        out = a.release("enrich", ["AAA111"])
        self.assertEqual(out.again, ["AAA111"])
        self.assertEqual(self.attempts("AAA111", "enrich"), (1, "rate limited"))

    def test_a_stage_that_could_not_run_counts_nothing(self):
        a = self.jobs()
        a.claim("enrich", 3)
        out = a.release("enrich", ["AAA111", "BBB222", "CCC333"], count=False)
        self.assertEqual((out.done, out.again, out.failed), ([], [], []))
        self.assertEqual(self.attempts("AAA111", "enrich"), (0, None))
        self.assertEqual(a.claim("enrich", 3), ["AAA111", "BBB222", "CCC333"])

    def test_a_reel_no_longer_pending_loses_its_count(self):
        a = self.jobs()
        a.claim("enrich", 1)
        a.release("enrich", ["AAA111"])
        self.fetched("AAA111")  # done by hand, say
        a.claim("enrich", 1)
        self.assertEqual(self.attempts("AAA111", "enrich"), (0, None))


class TestAttemptsCap(JobsCase):
    def fail_all(self, a, code="AAA111", stage="enrich", times=MAX_ATTEMPTS):
        out = None
        for _ in range(times):
            self.assertIn(code, a.claim(stage, 3))
            out = a.release(stage, a.held()[stage])
        return out

    def test_after_the_cap_a_reel_is_failed_and_not_offered(self):
        a = self.jobs()
        out = self.fail_all(a)
        self.assertIn("AAA111", out.failed)
        self.assertNotIn("AAA111", a.claim("enrich", 3))
        self.assertEqual(self.counts("enrich")["failed"], 3)

    def test_failures_say_why(self):
        a = self.jobs()
        self.fail_all(a)
        rows = queue.failures(self.conn, "enrich")
        self.assertEqual({r["shortcode"] for r in rows}, {"AAA111", "BBB222", "CCC333"})
        self.assertEqual(rows[0]["attempts"], MAX_ATTEMPTS)

    def test_a_reel_that_kills_its_worker_is_capped_too(self):
        for i in range(MAX_ATTEMPTS):
            self.jobs(f"w{i}").claim("enrich", 1)
            self.clock.t += LEASE + 1
        self.assertNotIn("AAA111", self.jobs("next").claim("enrich", 3))
        self.assertEqual(self.attempts("AAA111", "enrich"), (MAX_ATTEMPTS, DIED))

    def test_a_failed_reel_waits_longer_before_each_try(self):
        a = self.jobs(retry_after=60)
        a.claim("enrich", 1)
        a.release("enrich", ["AAA111"])
        self.assertNotIn("AAA111", a.claim("enrich", 3))
        self.assertEqual(self.counts("enrich")["waiting"], 1)
        a.release("enrich", a.held()["enrich"], count=False)
        self.clock.t += 61
        self.assertEqual(a.claim("enrich", 1), ["AAA111"])
        a.release("enrich", ["AAA111"])
        self.clock.t += 61
        self.assertNotIn("AAA111", a.claim("enrich", 3))  # two minutes now
        a.release("enrich", a.held()["enrich"], count=False)
        self.clock.t += 60
        self.assertEqual(a.claim("enrich", 1), ["AAA111"])

    def test_a_reel_that_killed_its_worker_is_failed_on_the_item_too(self):
        self.fetched("AAA111")
        for i in range(MAX_ATTEMPTS):
            self.jobs(f"w{i}").claim("asr", 1)
            self.clock.t += LEASE + 1
        a = self.jobs("next")
        a.claim("asr", 3)  # takes the last lease back, and gives up on it
        row = self.conn.execute(
            "SELECT fail_stage, fail_reason FROM item WHERE shortcode = 'AAA111'"
        ).fetchone()
        self.assertEqual(tuple(row), ("asr", DIED))
        # So the tagger stops waiting for a transcript that will not come.
        self.assertEqual(a.claim("tag", 3), ["AAA111"])

    def test_retry_by_hand_offers_it_again(self):
        a = self.jobs()
        self.fail_all(a)
        self.assertEqual(queue.retry(self.conn, ["enrich"]), 3)
        self.assertEqual(a.claim("enrich", 3), ["AAA111", "BBB222", "CCC333"])

    def test_a_version_bump_starts_the_count_again(self):
        a = self.jobs()
        self.fail_all(a)
        bumped = dataclasses.replace(queue.REGISTRY["enrich"], version=FETCH_V + 1)
        with mock.patch.dict(queue.REGISTRY, {"enrich": bumped}):
            self.assertEqual(a.claim("enrich", 1), ["AAA111"])
            a.release("enrich", ["AAA111"])
            self.assertEqual(self.attempts("AAA111", "enrich")[0], 1)


class TestReadiness(JobsCase):
    def test_transcribe_and_ocr_wait_for_enrich(self):
        a = self.jobs()
        self.fetched("AAA111")
        self.assertEqual(a.claim("asr", 3), ["AAA111"])
        # OCR is for reels with no speech, known only once transcribed.
        self.assertEqual(a.claim("ocr", 3), [])

    def test_tag_waits_for_the_transcript(self):
        a = self.jobs()
        self.fetched("AAA111")
        self.assertEqual(a.claim("tag", 3), [])
        self.transcribed("AAA111")
        self.assertEqual(a.claim("tag", 3), ["AAA111"])

    def test_tag_waits_while_the_transcript_is_still_being_retried(self):
        a = self.jobs()
        self.fetched("AAA111")
        a.claim("asr", 1)
        self.conn.execute(
            "UPDATE item SET fail_stage = 'asr', fail_reason = 'oom' WHERE shortcode = 'AAA111'"
        )
        a.release("asr", ["AAA111"])
        # The tagger alone would go ahead without it; a worker waits.
        self.assertEqual(a.claim("tag", 3), [])
        for _ in range(MAX_ATTEMPTS - 1):
            a.claim("asr", 1)
            a.release("asr", ["AAA111"])
        # Given up on: the tagger goes ahead with what there is.
        self.assertEqual(a.claim("tag", 3), ["AAA111"])

    def test_recipe_and_embed_wait_for_the_tagger(self):
        a = self.jobs()
        a.check_in(stages=jobs.STAGES, started_at=self.clock())
        self.fetched("AAA111")
        self.transcribed("AAA111")
        self.assertEqual(a.claim("recipe", 3), [])
        self.assertEqual(a.claim("embed", 3), [])
        self.tagged("AAA111")
        self.assertEqual(a.claim("recipe", 3), ["AAA111"])
        self.assertEqual(a.claim("embed", 3), ["AAA111"])

    def test_with_no_worker_tagging_embed_goes_ahead_from_captions(self):
        a = self.jobs()
        a.check_in(stages=("enrich", "asr", "ocr", "embed"), started_at=self.clock())
        self.fetched("AAA111")
        self.transcribed("AAA111")
        self.assertEqual(a.claim("embed", 3), ["AAA111"])
        # The recipe pass needs the tagger's model, so it has nothing to go on.
        self.assertEqual(a.claim("recipe", 3), [])
        embed = self.counts("embed")
        self.assertEqual(embed["without"], ["tag"])
        self.assertEqual(self.counts("recipe")["without"], [])

    def test_a_server_alone_embeds_new_reels_before_the_desktop_transcribes(self):
        # A NAS running only enrich and embed, its ASR and tagging done by
        # hand on a desktop now and then (#86).
        nas = self.jobs("nas")
        nas.check_in(stages=("enrich", "embed"), started_at=self.clock())
        self.fetched("AAA111")
        self.assertEqual(nas.claim("embed", 3), ["AAA111"])
        self.assertEqual(self.counts("embed")["without"], ["asr", "ocr", "tag"])

    def test_embed_goes_ahead_while_every_tagger_reports_a_problem(self):
        a, b = self.jobs("a"), self.jobs("b")
        a.check_in(stages=jobs.STAGES, started_at=self.clock(), problems={"tag": "no Ollama"})
        b.check_in(stages=("tag",), started_at=self.clock())
        self.fetched("AAA111")
        self.transcribed("AAA111")
        # b can tag, so embed waits for it.
        self.assertEqual(a.claim("embed", 3), [])
        b.check_in(stages=("tag",), started_at=self.clock(), problems={"tag": "no Ollama"})
        self.assertEqual(a.claim("embed", 3), ["AAA111"])

    def test_a_stopped_or_lost_tagger_does_not_hold_embed_back(self):
        a, desktop = self.jobs("a"), self.jobs("desktop")
        a.check_in(stages=("embed",), started_at=self.clock())
        desktop.check_in(stages=("tag",), started_at=self.clock())
        self.fetched("AAA111", "BBB222")
        self.transcribed("AAA111", "BBB222")
        self.assertEqual(a.claim("embed", 1), [])
        desktop.check_out()
        self.assertEqual(a.claim("embed", 1), ["AAA111"])
        desktop.check_in(stages=("tag",), started_at=self.clock())
        self.clock.t += STALE + 1
        a.check_in(stages=("embed",), started_at=self.clock())
        self.assertEqual(a.claim("embed", 1), ["BBB222"])

    def test_embedded_from_its_caption_then_again_from_its_summary(self):
        from app.embed.worker import embed_all

        a, desktop = self.jobs("a"), self.jobs("desktop")
        a.check_in(stages=("embed",), started_at=self.clock())
        self.fetched("AAA111")
        self.transcribed("AAA111")
        got = a.claim("embed", 3)
        embed_all(self.conn, lambda texts: [b"\0" * 4] * len(texts), shortcodes=got)
        # The stage fingerprints it too, for finding reposts (#58).
        dupes.hash_all(self.conn, Path("."), got, hasher=lambda *_: [0])
        a.release("embed", got)
        self.assertEqual(a.claim("embed", 3), [])
        # The desktop comes on and tags it: the summary is new text to embed.
        desktop.check_in(stages=("tag",), started_at=self.clock())
        self.tagged("AAA111")
        self.assertEqual(a.claim("embed", 3), ["AAA111"])

    def test_a_reel_whose_fetch_failed_for_good_is_still_embedded(self):
        self.conn.execute(
            """UPDATE item SET state = 'failed', fail_stage = 'fetch', fetch_v = ?
               WHERE shortcode = 'AAA111'""",
            (FETCH_V,),
        )
        self.assertEqual(self.jobs().claim("embed", 3), ["AAA111"])

    def test_queue_counts(self):
        a = self.jobs()
        self.fetched("AAA111", "BBB222")
        a.claim("asr", 1)
        asr = self.counts("asr")
        self.assertEqual((asr["ready"], asr["working"], asr["failed"]), (1, 1, 0))
        tag = self.counts("tag")
        self.assertEqual((tag["ready"], tag["waiting"]), (0, 3))
        self.clock.t += LEASE + 1
        self.assertEqual(self.counts("asr")["working"], 0)


class TestCheckIn(JobsCase):
    def test_running_stopped_and_lost(self):
        a, b = self.jobs("a"), self.jobs("b")
        a.check_in(stages=("asr",), started_at=self.clock(), doing="asr", problems={"tag": "x"})
        b.check_in(stages=jobs.STAGES, started_at=self.clock())
        b.check_out()
        found = {w["name"]: w for w in queue.workers(self.conn, now=self.clock())}
        self.assertEqual(found["a"]["state"], "running")
        self.assertEqual(found["a"]["doing"], "asr")
        self.assertEqual(found["a"]["problems"], {"tag": "x"})
        self.assertEqual(found["b"]["state"], "stopped")
        found = {w["name"]: w for w in queue.workers(self.conn, now=self.clock() + STALE + 1)}
        self.assertEqual(found["a"]["state"], "lost")
        self.assertEqual(found["a"]["problems"], {})

    def test_a_clean_stop_gives_leases_back_uncounted(self):
        a = self.jobs()
        a.claim("enrich", 3)
        a.check_in(stages=jobs.STAGES, started_at=self.clock())
        a.check_out()
        self.assertEqual(a.held(), {})
        self.assertEqual(self.attempts("AAA111", "enrich"), (0, None))

    def test_a_restart_takes_back_what_the_last_run_died_holding(self):
        self.jobs("a").claim("enrich", 2)
        again = self.jobs("a")
        self.assertEqual(again.reclaim(), 2)
        self.assertEqual(self.attempts("AAA111", "enrich"), (1, DIED))
        self.assertEqual(again.claim("enrich", 3), ["AAA111", "BBB222", "CCC333"])

    def test_long_gone_workers_are_forgotten(self):
        a = self.jobs("a")
        a.check_in(stages=(), started_at=self.clock())
        a.check_out()
        self.clock.t += queue.FORGET + 1
        self.jobs("b").check_in(stages=(), started_at=self.clock())
        names = [w["name"] for w in queue.workers(self.conn, now=self.clock())]
        self.assertEqual(names, ["b"])


if __name__ == "__main__":
    unittest.main()
