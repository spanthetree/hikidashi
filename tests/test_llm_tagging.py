import io
import json
import sqlite3
import tempfile
import unittest
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from typing import Any
from unittest import mock

from app import db
from app.asr.transcriber import ASR_V
from app.db import open_db
from app.importer.service import import_export
from app.ollama import NUM_CTX
from app.profiles import resolve
from app.tagging import llm, service, worker
from app.tagging.llm import TAG_V, TagError, build_prompt, parse
from app.tagging.taxonomy import names
from tests.fixtures import must, post_record, saved_posts, write_zip

R = "https://www.instagram.com/reel/"


def reply(summary="", category="Other", keywords=(), confidence="high") -> str:
    return json.dumps(
        {
            "summary": summary,
            "category": category,
            "confidence": confidence,
            "keywords": list(keywords),
        }
    )


class TestProfiles(unittest.TestCase):
    def test_each_profile_names_a_tag_model(self):
        self.assertEqual(resolve("cpu").tag_model, "qwen2.5:3b")
        self.assertEqual(resolve("cuda", devices=1).tag_model, "qwen2.5:7b")


class TestPrompt(unittest.TestCase):
    def test_includes_every_source_and_the_categories(self):
        p = build_prompt("pizza night", ["napoli"], "first stretch the dough")
        for part in ("pizza night", "#napoli", "first stretch the dough", "Food"):
            self.assertIn(part, must(p))

    def test_the_transcript_is_never_cut(self):
        long = "word " * 5000
        self.assertIn(long.strip(), must(build_prompt(None, [], long)))

    def test_every_category_is_defined(self):
        self.assertEqual(set(llm.CATEGORY_NOTES), set(names()))

    def test_nothing_to_read_is_none(self):
        self.assertIsNone(build_prompt(None, [], None))
        self.assertIsNone(build_prompt("  ", [], "\n"))

    def test_a_caption_too_thin_to_tag_is_none(self):
        """Without this, the model invented "a beautiful car" for both."""
        self.assertIsNone(build_prompt("Find your @happyplace 🌍", [], None))
        self.assertIsNone(build_prompt("@a @b https://x.co/y 2026 🔥", [], None))
        self.assertIsNotNone(build_prompt("THE BEST SIM RIG UPGRADES", [], None))
        self.assertIsNotNone(build_prompt("Find your @happyplace", ["drift"], None))
        self.assertIsNotNone(build_prompt("Find your @happyplace", [], "so we drove"))

    def test_schema_asks_for_one_category_from_the_taxonomy(self):
        props = llm.schema()["properties"]
        self.assertEqual(props["category"], {"type": "string", "enum": [*names(), llm.OTHER]})
        self.assertEqual(props["confidence"]["enum"], ["high", "medium", "low"])


class TestParse(unittest.TestCase):
    def test_a_good_reply(self):
        r = parse(reply("A marinara stretched by hand.", "Food", ["pizza"], "medium"))
        self.assertEqual(r.summary, "A marinara stretched by hand.")
        self.assertEqual((r.category, r.confidence), ("Food", 0.6))
        self.assertEqual(r.keywords, ["pizza"])

    def test_case_is_forgiven(self):
        self.assertEqual(parse(reply(category="sim Racing")).category, "Sim racing")

    def test_a_category_outside_the_taxonomy_is_no_category(self):
        for cat in ("Cooking", "Pets", "", None):
            with self.subTest(cat=cat):
                r = parse(reply(category=cat))
                self.assertEqual((r.category, r.confidence), (None, None))

    def test_other_means_no_category(self):
        self.assertIsNone(parse(reply(category="Other")).category)

    def test_an_unknown_confidence_is_none(self):
        self.assertIsNone(parse(reply(category="Food", confidence="certain")).confidence)

    def test_keywords_are_cleaned_and_capped(self):
        r = parse(
            reply(
                keywords=["#Pizza", "pizza", "  Brake   Pads ", "", "food", "a", "b", "c", "d", 7]
            )
        )
        self.assertEqual(r.keywords, ["pizza", "brake pads", "a", "b", "c"])

    def test_empty_summary_is_none(self):
        self.assertIsNone(parse(reply("   ")).summary)

    def test_malformed_replies_are_tag_errors(self):
        for bad in (
            '{"summary": "cut off',
            "[]",
            '{"category": ["Food"]}',
            '{"keywords": "pizza"}',
        ):
            with self.subTest(bad=bad), self.assertRaises(TagError):
                parse(bad)

    def test_no_text_is_answered_without_the_model(self):
        called = mock.Mock()
        self.assertIsNone(llm.tag(called, None, [], None).summary)
        called.assert_not_called()


def _http(body: dict[str, Any]):
    return io.BytesIO(json.dumps(body).encode())


class TestOllamaClient(unittest.TestCase):
    def setUp(self):
        self.client = llm.Ollama("http://ollama:11434/", "qwen2.5:3b")

    def test_caps_output_not_input_and_sends_the_schema(self):
        with mock.patch.object(
            urllib.request,
            "urlopen",
            return_value=_http({"message": {"content": reply()}, "done_reason": "stop"}),
        ) as op:
            self.assertEqual(self.client("prompt"), reply())
        req = op.call_args.args[0]
        self.assertEqual(req.full_url, "http://ollama:11434/api/chat")
        body = json.loads(req.data)
        self.assertEqual(body["options"]["num_predict"], llm.NUM_PREDICT)
        self.assertEqual(body["options"]["num_ctx"], NUM_CTX)
        self.assertEqual(body["options"]["temperature"], 0)
        self.assertEqual(body["format"], llm.schema())
        self.assertFalse(body["stream"])

    def test_hitting_the_token_cap_is_a_tag_error(self):
        with mock.patch.object(
            urllib.request,
            "urlopen",
            return_value=_http({"message": {"content": '{"summ'}, "done_reason": "length"}),
        ):
            with self.assertRaises(TagError):
                self.client("prompt")

    def test_an_unreachable_server_stops_the_run(self):
        with mock.patch.object(
            urllib.request, "urlopen", side_effect=urllib.error.URLError("refused")
        ):
            with self.assertRaises(llm.OllamaError):
                self.client("prompt")
            with self.assertRaises(llm.OllamaError):
                self.client.has_model()

    def test_a_missing_model_stops_the_run(self):
        err = urllib.error.HTTPError("u", 404, "nf", Message(), io.BytesIO(b"not found"))
        with mock.patch.object(urllib.request, "urlopen", side_effect=err):
            with self.assertRaises(llm.OllamaError):
                self.client("prompt")

    def test_a_server_error_fails_only_the_item(self):
        err = urllib.error.HTTPError("u", 500, "boom", Message(), io.BytesIO(b"oom"))
        with mock.patch.object(urllib.request, "urlopen", side_effect=err):
            with self.assertRaises(TagError):
                self.client("prompt")

    def test_has_model_matches_the_implicit_latest_tag(self):
        tags = {"models": [{"name": "qwen2.5:3b"}, {"name": "llama3:latest"}]}
        with mock.patch.object(urllib.request, "urlopen", side_effect=lambda *a, **k: _http(tags)):
            self.assertTrue(self.client.has_model())
            self.assertTrue(llm.Ollama("http://x", "llama3").has_model())
            self.assertFalse(llm.Ollama("http://x", "qwen2.5:7b").has_model())


class Fake:
    """A Generate that answers by shortcode, which it finds in the caption."""

    def __init__(self, answers: dict[str, Any]):
        self.answers = answers
        self.prompts: dict[str, str] = {}

    def __call__(self, prompt: str) -> str:
        code = next(c for c in self.answers if c in prompt)
        self.prompts[code] = prompt
        answer = self.answers[code]
        if isinstance(answer, Exception):
            raise answer
        return answer


class WorkerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.conn = open_db(self.dir / "t.sqlite3")
        zp = write_zip(
            self.dir / "e.zip",
            saved_posts(
                [
                    post_record(R + "PIZZA1/", timestamp=300, caption="PIZZA1 sunday pizza night"),
                    post_record(
                        R + "CAR001/",
                        timestamp=200,
                        caption="CAR001 track day again",
                        hashtags=("ae86",),
                    ),
                    post_record(R + "QUIET1/", timestamp=100, caption="QUIET1 watching it go down"),
                ]
            ),
        )
        import_export(self.conn, zp)
        service.apply_hashtags(self.conn)
        self.fake = Fake(
            {
                "PIZZA1": reply("Neapolitan pizza at home.", "Food", ["pizza", "dough"]),
                "CAR001": reply("An AE86 at the track.", "Automotive", ["ae86"], "medium"),
                "QUIET1": reply("A sunset.", "Other", ["sunset"]),
            }
        )

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def run_llm(self, **kw):
        return worker.tag_all(self.conn, self.fake, profile="cpu", **kw)

    def row(self, code) -> sqlite3.Row:
        return self.conn.execute("SELECT * FROM item WHERE shortcode = ?", (code,)).fetchone()

    def primary(self, code) -> str | None:
        return self.row(code)["primary_tag"]

    def shown(self, code) -> str | None:
        """The tag the library's owner sees: theirs, else the shared one."""
        tags = service.item_tags(self.conn, code)
        return tags[0]["name"] if tags else None

    def tags_on(self, code, source=None) -> set[str]:
        return {
            t["name"] for t in service.item_tags(self.conn, code) if source in (None, t["source"])
        }


class TestTagAll(WorkerCase):
    def test_tags_a_reel_its_hashtags_said_nothing_about(self):
        self.assertEqual(self.tags_on("PIZZA1"), set())
        p = self.run_llm()
        self.assertEqual((p.total, p.tagged, p.untagged, p.failed), (3, 2, 1, 0))
        self.assertEqual(self.tags_on("PIZZA1", "model"), {"Food"})
        self.assertEqual(self.primary("PIZZA1"), "Food")

    def test_one_category_with_its_confidence(self):
        self.run_llm()
        self.assertEqual(
            service.item_tags(self.conn, "CAR001")[0],
            {"name": "Automotive", "kind": "taxonomy", "source": "model", "confidence": 0.6},
        )

    def test_writes_summary_and_keywords_and_stamps(self):
        self.run_llm()
        r = self.row("PIZZA1")
        self.assertEqual(r["summary"], "Neapolitan pizza at home.")
        self.assertEqual(json.loads(r["keywords"]), ["pizza", "dough"])
        self.assertEqual((r["tag_v"], r["tag_profile"]), (TAG_V, "cpu"))

    def test_no_category_is_still_stamped_and_not_asked_again(self):
        self.run_llm()
        self.assertEqual(self.tags_on("QUIET1"), set())
        self.assertEqual(self.row("QUIET1")["tag_v"], TAG_V)
        self.assertEqual(self.run_llm().total, 0)

    def search(self, q):
        return [
            r[0]
            for r in self.conn.execute(
                """SELECT shortcode FROM item WHERE rowid IN
                 (SELECT rowid FROM item_fts WHERE item_fts MATCH ?)""",
                (q,),
            )
        ]

    def test_the_summary_keywords_and_tag_are_searchable(self):
        self.run_llm()
        self.assertEqual(self.search("neapolitan"), ["PIZZA1"])  # summary
        self.assertEqual(self.search("dough"), ["PIZZA1"])  # keyword
        self.assertEqual(self.search("food"), ["PIZZA1"])  # its tag

    def test_state_moves_forward_only(self):
        self.conn.execute("UPDATE item SET state = 'transcribed' WHERE shortcode='PIZZA1'")
        self.conn.execute("UPDATE item SET state = 'ready' WHERE shortcode='CAR001'")
        self.run_llm()
        self.assertEqual(self.row("PIZZA1")["state"], "tagged")
        self.assertEqual(self.row("CAR001")["state"], "ready")
        self.assertEqual(self.row("QUIET1")["state"], "imported")


class TestProvenance(WorkerCase):
    def test_a_rerun_replaces_only_the_model_row(self):
        self.run_llm()
        service.set_user_tag(self.conn, "CAR001", "Mine")
        self.fake.answers["CAR001"] = reply("x", "Sim racing")
        self.run_llm(force=True)

        self.assertEqual(self.tags_on("CAR001", "model"), {"Sim racing"})
        self.assertEqual(self.tags_on("CAR001", "user"), {"Mine"})
        self.assertEqual(self.tags_on("CAR001", "hashtag"), {"Automotive"})
        self.assertEqual(self.shown("CAR001"), "Mine")

    def test_the_model_outranks_the_hashtags(self):
        self.fake.answers["CAR001"] = reply("x", "Travel")
        self.run_llm()
        self.assertEqual(self.primary("CAR001"), "Travel")

    def test_the_hashtags_speak_when_the_model_abstains(self):
        self.fake.answers["CAR001"] = reply("x", "Other")
        self.run_llm()
        self.assertEqual(self.tags_on("CAR001", "model"), set())
        self.assertEqual(self.primary("CAR001"), "Automotive")

    def test_yours_survives_a_rerun_that_disagrees(self):
        self.run_llm()
        service.set_user_tag(self.conn, "PIZZA1", "Travel")
        self.fake.answers["PIZZA1"] = reply("x", "Food")
        self.run_llm(force=True)
        self.assertEqual(self.tags_on("PIZZA1", "user"), {"Travel"})
        self.assertEqual(self.shown("PIZZA1"), "Travel")

    def test_the_hashtag_pass_leaves_the_model_row_alone(self):
        self.run_llm()
        service.apply_hashtags(self.conn, force=True)
        self.assertEqual(self.tags_on("PIZZA1", "model"), {"Food"})


class TestFailures(WorkerCase):
    def test_a_bad_reply_is_recorded_unstamped_and_retried(self):
        self.fake.answers["PIZZA1"] = TagError("reply is not JSON")
        p = self.run_llm()
        self.assertEqual(p.failed, 1)
        r = self.row("PIZZA1")
        self.assertEqual((r["fail_stage"], r["tag_v"]), ("tag", 0))
        self.assertNotEqual(r["state"], "failed")

        self.fake.answers["PIZZA1"] = reply("ok", "Food")
        self.assertEqual(self.run_llm().total, 1)
        r = self.row("PIZZA1")
        self.assertIsNone(r["fail_stage"])
        self.assertIsNone(r["fail_reason"])

    def test_losing_ollama_stops_the_run_without_marking_anything(self):
        self.fake.answers["CAR001"] = llm.OllamaError("gone")
        with self.assertRaises(llm.OllamaError):
            self.run_llm()
        self.assertEqual(self.row("PIZZA1")["tag_v"], TAG_V)  # finished, kept
        self.assertEqual(self.row("CAR001")["tag_v"], 0)
        self.assertIsNone(self.row("CAR001")["fail_stage"])

    def test_a_crash_mid_item_writes_nothing_for_it(self):
        with mock.patch.object(worker, "set_tag", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                self.run_llm()
        self.assertEqual(self.row("PIZZA1")["tag_v"], 0)
        self.assertIsNone(self.row("PIZZA1")["summary"])


class TestWaitsForTranscripts(WorkerCase):
    def test_a_reel_with_a_transcript_coming_waits_for_it(self):
        self.conn.execute(
            "UPDATE item SET media_path = 'v.mp4', asr_v = 0 WHERE shortcode = 'PIZZA1'"
        )
        self.assertNotIn("PIZZA1", [r["shortcode"] for r in worker.pending(self.conn)])
        self.assertEqual(worker.summary(self.conn)["waiting_asr"], 1)

        self.conn.execute(
            "UPDATE item SET asr_v = ?, transcript = 'stretch it' WHERE shortcode = 'PIZZA1'",
            (ASR_V,),
        )
        self.run_llm()
        self.assertIn("stretch it", self.fake.prompts["PIZZA1"])

    def test_a_reel_whose_transcription_failed_does_not_wait(self):
        self.conn.execute("""UPDATE item SET media_path = 'v.mp4', asr_v = 0,
                             fail_stage = 'asr' WHERE shortcode = 'PIZZA1'""")
        self.assertIn("PIZZA1", [r["shortcode"] for r in worker.pending(self.conn)])

    def test_unreachable_posts_are_skipped(self):
        self.conn.execute("UPDATE item SET state = 'failed' WHERE shortcode = 'PIZZA1'")
        self.assertNotIn("PIZZA1", [r["shortcode"] for r in worker.pending(self.conn)])


class TestMigration(unittest.TestCase):
    def test_hashtag_stamps_move_out_of_tag_v(self):
        """Before phase 03, tag_v held the hashtag pass's stamp."""
        with tempfile.TemporaryDirectory() as tmp:
            conn = open_db(Path(tmp) / "old.sqlite3")
            conn.execute("INSERT INTO item (shortcode, permalink, tag_v) VALUES ('OLD001', 'p', 1)")
            conn.execute("ALTER TABLE item DROP COLUMN hashtag_v")

            self.assertIn("item.hashtag_v", db.migrate(conn))
            row = conn.execute(
                "SELECT tag_v, hashtag_v FROM item WHERE shortcode = 'OLD001'"
            ).fetchone()
            self.assertEqual((row["tag_v"], row["hashtag_v"]), (0, 1))

            db.init(conn)  # and only once
            row = conn.execute("SELECT hashtag_v FROM item").fetchone()
            self.assertEqual(row["hashtag_v"], 1)
            conn.close()


if __name__ == "__main__":
    unittest.main()


class GameFake(Fake):
    """A tagger that is also a games.Model: games by shortcode, and a table of
    titles the check knows."""

    def __init__(self, answers, games, known):
        super().__init__(answers)
        self.games = games
        self.known = known
        self.asked: list[str] = []

    def game(self, prompt: str) -> str:
        code = next(c for c in self.games if c in prompt)
        self.asked.append(code)
        return self.games[code]

    def check(self, title: str) -> str:
        official = self.known.get(title)
        return json.dumps({"known": official is not None, "title": official or ""})


def game_said(title="", kind="one", confidence="high", others=()) -> str:
    return json.dumps(
        {"kind": kind, "title": title, "confidence": confidence, "others": list(others)}
    )


class TestGamePass(unittest.TestCase):
    """The tagger asks gaming reels which game they show (#117)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.conn = open_db(self.dir / "t.sqlite3")
        posts = [
            ("MINE01", "MINE01 my Minecraft Disneyland server has audio", ()),
            ("CYBER1", "CYBER1 what details have you found?", ("cyberpunk", "gaming")),
            ("TABLE1", "TABLE1 D&D with the kids", ("gaming",)),
            ("PIZZA1", "PIZZA1 sunday pizza night", ()),
            ("GT4CRT", "GT4CRT Grand Turismo 4 on a CRT", ("gaming",)),
        ]
        zp = write_zip(
            self.dir / "e.zip",
            saved_posts(
                [
                    post_record(R + f"{c}/", timestamp=100 + i, caption=cap, hashtags=h)
                    for i, (c, cap, h) in enumerate(posts)
                ]
            ),
        )
        import_export(self.conn, zp)
        service.apply_hashtags(self.conn)
        self.fake = GameFake(
            {
                "MINE01": reply("A Minecraft theme park server.", "Technology"),
                "CYBER1": reply("Hidden details in a game.", "Gaming"),
                "TABLE1": reply("Tabletop role-play with kids.", "Gaming"),
                "PIZZA1": reply("Pizza.", "Food"),
                "GT4CRT": reply("A racing game on a CRT.", "Gaming"),
            },
            {
                "MINE01": game_said("Minecraft"),
                "CYBER1": game_said("Cyberpunk 2077"),
                "TABLE1": game_said(kind="none"),
                "GT4CRT": game_said("Grand Turismo 4"),
            },
            {
                "Minecraft": "Minecraft",
                "Cyberpunk 2077": "Cyberpunk 2077",
                "Grand Turismo 4": "Gran Turismo 4",
            },
        )

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def row(self, code):
        return self.conn.execute(
            "SELECT game, game_guess, primary_tag FROM item WHERE shortcode = ?", (code,)
        ).fetchone()

    def test_sure_games_are_applied_and_guesses_kept_to_ask_about(self):
        p = worker.tag_all(self.conn, self.fake, profile="cpu")
        self.assertEqual((p.games, p.guesses), (2, 1))
        self.assertEqual(tuple(self.row("GT4CRT")), ("Gran Turismo 4", None, "Gaming"))
        self.assertEqual(tuple(self.row("CYBER1")), (None, "Cyberpunk 2077", "Gaming"))
        self.assertEqual(tuple(self.row("TABLE1")), (None, None, "Gaming"))
        self.assertIn("games 2 (+1 to confirm)", p.line())

    def test_only_candidates_are_asked(self):
        worker.tag_all(self.conn, self.fake, profile="cpu")
        self.assertNotIn("PIZZA1", self.fake.asked)
        self.assertEqual(self.row("PIZZA1")["game"], None)

    def test_a_sure_game_on_a_reel_filed_elsewhere_makes_it_a_gaming_reel(self):
        worker.tag_all(self.conn, self.fake, profile="cpu")
        self.assertEqual(tuple(self.row("MINE01")), ("Minecraft", None, "Gaming"))
        tags = {t["source"]: t["name"] for t in service.item_tags(self.conn, "MINE01")}
        self.assertEqual(tags, {"game": "Gaming", "model": "Technology"})
        # Your own tag still wins over it.
        service.set_user_tag(self.conn, "MINE01", "Technology")
        self.assertEqual(service.item_tags(self.conn, "MINE01")[0]["name"], "Technology")

    def test_a_retag_that_is_no_longer_sure_withdraws_the_gaming_tag(self):
        worker.tag_all(self.conn, self.fake, profile="cpu")
        self.fake.games["MINE01"] = game_said("Minecraft", confidence="medium")
        worker.tag_all(self.conn, self.fake, profile="cpu", force=True)
        self.assertEqual(tuple(self.row("MINE01")), (None, "Minecraft", "Technology"))

    def test_a_tagger_that_cannot_ask_about_games_tags_as_before(self):
        plain = Fake(self.fake.answers)
        p = worker.tag_all(self.conn, plain, profile="cpu")
        self.assertEqual((p.tagged, p.games, p.guesses), (5, 0, 0))
        self.assertEqual(tuple(self.row("MINE01")), (None, None, "Technology"))

    def test_a_malformed_game_reply_fails_the_reel_and_writes_nothing(self):
        self.fake.games["CYBER1"] = "not json"
        p = worker.tag_all(self.conn, self.fake, profile="cpu")
        self.assertEqual(p.failed, 1)
        r = self.conn.execute(
            "SELECT tag_v, fail_stage FROM item WHERE shortcode = 'CYBER1'"
        ).fetchone()
        self.assertEqual(tuple(r), (0, "tag"))

    def test_a_sure_game_is_searchable(self):
        from app import search

        worker.tag_all(self.conn, self.fake, profile="cpu")
        self.assertEqual(search.keyword_hits(self.conn, "gran turismo"), ["GT4CRT"])
        # A guess is not the reel's game, so it does not find it.
        self.assertEqual(search.keyword_hits(self.conn, "2077"), [])

    def test_your_answer_is_yours_and_survives_a_retag(self):
        worker.tag_all(self.conn, self.fake, profile="cpu")
        self.assertEqual(
            service.item_game(self.conn, "CYBER1"),
            {"game": None, "yours": False, "guess": "Cyberpunk 2077"},
        )
        service.set_user_game(self.conn, "CYBER1", "  Cyberpunk   2077 ")
        self.assertEqual(
            service.item_game(self.conn, "CYBER1"),
            {"game": "Cyberpunk 2077", "yours": True, "guess": None},
        )
        # Saying there is no game hides even a sure one.
        service.set_user_game(self.conn, "GT4CRT", None)
        self.assertEqual(
            service.item_game(self.conn, "GT4CRT"), {"game": None, "yours": True, "guess": None}
        )
        worker.tag_all(self.conn, self.fake, profile="cpu", force=True)
        self.assertEqual(service.item_game(self.conn, "CYBER1")["game"], "Cyberpunk 2077")
        self.assertIsNone(service.item_game(self.conn, "GT4CRT")["game"])
        service.clear_user_game(self.conn, "GT4CRT")
        self.assertEqual(service.item_game(self.conn, "GT4CRT")["game"], "Gran Turismo 4")


class TestGameMigration(unittest.TestCase):
    def test_a_database_from_before_the_game_pass_is_brought_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "t.sqlite3"
            conn = open_db(path)
            # Back to the triggers and index of 0.15: no game column, model first.
            for name in ("item_tag_ai", "item_tag_ad", "item_tag_au"):
                old = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE name = ?", (name,)
                ).fetchone()[0]
                conn.execute(f"DROP TRIGGER {name}")
                conn.execute(
                    old.replace(
                        "CASE item_tag.source WHEN 'game' THEN 0 WHEN 'model' THEN 1 ELSE 2 END",
                        "item_tag.source = 'model' DESC",
                    )
                )
            for name in ("item_ai", "item_ad", "item_au"):
                conn.execute(f"DROP TRIGGER {name}")
            conn.execute("DROP TABLE item_fts")
            conn.execute(
                """CREATE VIRTUAL TABLE item_fts USING fts5(caption, transcript, ocr_text,
                   summary, primary_tag, clean_hashtags, keywords,
                   content='item', content_rowid='rowid')"""
            )
            conn.close()

            conn = open_db(path)
            triggers = [
                r[0]
                for r in conn.execute("SELECT sql FROM sqlite_master WHERE name LIKE 'item_tag_a_'")
            ]
            self.assertEqual(len(triggers), 3)
            self.assertTrue(all("'game'" in t for t in triggers))
            fts = [r[1] for r in conn.execute("PRAGMA table_info(item_fts)")]
            self.assertEqual(fts[-1], "game")
            self.assertEqual(db.migrate(conn), [])
            conn.close()
