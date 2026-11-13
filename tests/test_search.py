import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path
from typing import Any

from app import search
from app.db import open_db
from app.embed import embedder, worker
from app.embed.embedder import EMBED_V, document, pack
from app.importer.service import import_export
from tests.fixtures import must, post_record, saved_posts, write_zip

R = "https://www.instagram.com/reel/"
HAS_VEC = importlib.util.find_spec("sqlite_vec") is not None

# A three-topic toy space. A word votes for its topic; anything else is noise.
# Enough to tell "marinara" is about pizza without a 100 MB model.
TOPICS = (
    {"pizza", "marinara", "rome", "dough", "oven", "neapolitan"},
    {"car", "ae86", "drift", "track", "corolla", "initial"},
)


def toy_vector(text: str) -> bytes:
    words = re.findall(r"\w+", text.lower())
    v = [sum(w in topic for w in words) for topic in TOPICS] + [0.1]
    return pack(v)


class ToyEmbedder:
    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, texts: list[str]) -> list[bytes]:
        self.calls.append(list(texts))
        return [toy_vector(t) for t in texts]


class TestDocument(unittest.TestCase):
    def test_summary_and_keywords_not_the_caption(self):
        self.assertEqual(
            document("Neapolitan pizza at home.", '["pizza", "dough"]', "#reels #fyp"),
            "Neapolitan pizza at home.\npizza, dough",
        )

    def test_the_caption_stands_in_until_there_is_a_summary(self):
        self.assertEqual(document(None, None, "  sunday pizza night "), "sunday pizza night")
        self.assertEqual(len(must(document(None, None, "x" * 5000))), embedder.CAPTION_CHARS)

    def test_nothing_to_embed_is_none(self):
        self.assertIsNone(document(None, None, None))
        self.assertIsNone(document(None, "[]", "   "))

    def test_the_digest_changes_with_the_text(self):
        self.assertEqual(embedder.digest("a"), embedder.digest("a"))
        self.assertNotEqual(embedder.digest("a"), embedder.digest("b"))

    def test_pack_is_float32(self):
        self.assertEqual(len(pack([1.0, 2.0, 3.0])), 12)


class LibraryCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.conn = open_db(self.dir / "t.sqlite3")
        zp = write_zip(
            self.dir / "e.zip",
            saved_posts(
                [
                    post_record(
                        R + "DOUGH1/", timestamp=500, caption="Sunday dough, 72 hours cold"
                    ),
                    post_record(R + "OVEN01/", timestamp=400, caption="New oven day"),
                    post_record(
                        R + "PHRASE/",
                        timestamp=300,
                        caption="the best marinara in rome, says everyone",
                    ),
                    post_record(
                        R + "AE8600/",
                        timestamp=200,
                        caption="track day in the ae86",
                        username="drifter",
                    ),
                    post_record(R + "EMPTY1/", timestamp=100),
                ]
            ),
        )
        import_export(self.conn, zp)
        self.summarise("DOUGH1", "Neapolitan pizza dough at home.", ["pizza", "dough"])
        self.embed = ToyEmbedder()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def summarise(self, code, summary, keywords):
        self.conn.execute(
            "UPDATE item SET summary = ?, keywords = ? WHERE shortcode = ?",
            (summary, json.dumps(keywords), code),
        )

    def vec(self, code):
        return self.conn.execute(
            "SELECT digest, embedding FROM item_vec WHERE shortcode = ?", (code,)
        ).fetchone()


class TestEmbedAll(LibraryCase):
    def test_embeds_everything_with_text_and_stamps_everything(self):
        p = worker.embed_all(self.conn, self.embed)
        self.assertEqual((p.total, p.embedded, p.empty), (5, 4, 1))
        self.assertIsNotNone(self.vec("DOUGH1"))
        self.assertIsNone(self.vec("EMPTY1"))
        stamps = {r[0] for r in self.conn.execute("SELECT embed_v FROM item")}
        self.assertEqual(stamps, {EMBED_V})

    def test_a_second_run_does_nothing(self):
        worker.embed_all(self.conn, self.embed)
        self.embed.calls.clear()
        self.assertEqual(worker.embed_all(self.conn, self.embed).total, 0)
        self.assertEqual(self.embed.calls, [])

    def test_a_new_summary_re_embeds_that_item_only(self):
        worker.embed_all(self.conn, self.embed)
        before = self.vec("OVEN01")
        self.summarise("OVEN01", "A wood-fired pizza oven.", ["pizza", "oven"])
        self.embed.calls.clear()
        p = worker.embed_all(self.conn, self.embed)
        self.assertEqual(p.embedded, 1)
        self.assertEqual(self.embed.calls, [["A wood-fired pizza oven.\npizza, oven"]])
        self.assertNotEqual(self.vec("OVEN01")["digest"], before["digest"])

    def test_text_disappearing_removes_the_vector(self):
        worker.embed_all(self.conn, self.embed)
        self.conn.execute("UPDATE item SET caption = NULL WHERE shortcode = 'OVEN01'")
        p = worker.embed_all(self.conn, self.embed)
        self.assertEqual((p.embedded, p.empty), (0, 1))
        self.assertIsNone(self.vec("OVEN01"))

    def test_bumping_embed_v_re_embeds(self):
        worker.embed_all(self.conn, self.embed)
        self.conn.execute("UPDATE item SET embed_v = 0 WHERE shortcode = 'AE8600'")
        self.assertEqual([j.shortcode for j in worker.pending(self.conn)], ["AE8600"])

    def test_force_and_limit(self):
        worker.embed_all(self.conn, self.embed)
        self.assertEqual(len(worker.pending(self.conn, force=True)), 5)
        self.assertEqual(len(worker.pending(self.conn, force=True, limit=2)), 2)

    def test_batches_commit_as_they_go(self):
        calls = 0

        def dies_on_the_second_batch(texts):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt
            return self.embed(texts)

        with self.assertRaises(KeyboardInterrupt):
            worker.embed_all(self.conn, dies_on_the_second_batch, batch=2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM item_vec").fetchone()[0], 2)
        self.assertEqual(worker.embed_all(self.conn, self.embed).embedded, 2)

    def test_a_short_reply_writes_nothing(self):
        with self.assertRaises(RuntimeError):
            worker.embed_all(self.conn, lambda texts: [])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM item_vec").fetchone()[0], 0)

    def test_deleting_an_item_deletes_its_vector(self):
        worker.embed_all(self.conn, self.embed)
        self.conn.execute("DELETE FROM item WHERE shortcode = 'OVEN01'")
        self.assertIsNone(self.vec("OVEN01"))

    def test_summary(self):
        worker.embed_all(self.conn, self.embed)
        self.assertEqual(
            worker.summary(self.conn), {"items": 5, "vectors": 4, "chunks": 0, "behind": 0}
        )

    def say(self, code, transcript):
        self.conn.execute("UPDATE item SET transcript = ? WHERE shortcode = ?", (transcript, code))

    def chunks(self, code):
        return [
            r[0]
            for r in self.conn.execute(
                "SELECT seq FROM item_chunk_vec WHERE shortcode = ? ORDER BY seq", (code,)
            )
        ]

    def test_a_transcript_is_embedded_a_stretch_at_a_time(self):
        self.say("OVEN01", " ".join(f"w{i}" for i in range(100)))
        worker.embed_all(self.conn, self.embed)
        # 100 words: 0-59 and 40-99.
        self.assertEqual(self.chunks("OVEN01"), [0, 1])
        self.assertEqual(self.embed.calls[0].count("New oven day"), 1)

    def test_a_short_transcript_is_not_chunked(self):
        self.say("OVEN01", "Thank you.")
        worker.embed_all(self.conn, self.embed)
        self.assertEqual(self.chunks("OVEN01"), [])

    def test_a_new_transcript_re_embeds_and_replaces_the_chunks(self):
        self.say("OVEN01", " ".join(["pizza"] * 100))
        worker.embed_all(self.conn, self.embed)
        self.say("OVEN01", " ".join(["oven"] * 30))
        self.assertEqual([j.shortcode for j in worker.pending(self.conn)], ["OVEN01"])
        worker.embed_all(self.conn, self.embed)
        self.assertEqual(self.chunks("OVEN01"), [0])

    def test_losing_the_text_removes_the_chunks(self):
        self.say("OVEN01", " ".join(["pizza"] * 30))
        worker.embed_all(self.conn, self.embed)
        self.conn.execute("UPDATE item SET caption = NULL WHERE shortcode = 'OVEN01'")
        worker.embed_all(self.conn, self.embed)
        self.assertEqual(self.chunks("OVEN01"), [])


class TestKeywordSearch(LibraryCase):
    def test_without_a_vector_it_is_keyword_search(self):
        self.assertEqual(search.rank(self.conn, "dough"), ["DOUGH1"])
        self.assertEqual(search.rank(self.conn, "marinara"), ["PHRASE"])
        self.assertEqual(search.rank(self.conn, "drifter"), ["AE8600"])

    def test_operators_in_the_query_are_data(self):
        self.assertEqual(search.rank(self.conn, 'dough"*) ('), ["DOUGH1"])

    def test_a_query_with_no_words_matches_creators(self):
        self.assertEqual(search.rank(self.conn, "@@"), [])
        self.assertEqual(search.rank(self.conn, "DOUGH"), ["DOUGH1"])

    def test_phrase_query(self):
        self.assertEqual(search.phrase_query("the best, marinara!"), '"the best marinara"')
        self.assertIsNone(search.phrase_query("pizza"))

    def test_a_sentence_falls_back_to_any_of_its_words(self):
        # ANDing every token matches nothing; "dough" is the word that counts.
        self.assertEqual(
            search.rank(self.conn, "the one where he talks about his dough"), ["DOUGH1"]
        )

    def test_every_word_beats_any_word(self):
        self.assertEqual(search.rank(self.conn, "new oven"), ["OVEN01"])
        self.assertEqual(search.fts_content(self.conn, "oven"), None)

    def test_content_words_must_all_match(self):
        # "cold" is in one reel and "oven" in another: no reel is about both.
        self.assertEqual(search.fts_content(self.conn, "the cold oven"), '"cold" AND "oven"')
        self.assertEqual(search.rank(self.conn, "the one with a cold oven"), [])

    def test_without_vectors_two_content_words_will_do(self):
        self.conn.execute(
            "UPDATE item SET caption = 'cold oven, warm dough' WHERE shortcode = 'OVEN01'"
        )
        q = "a warm oven for a cold night"
        self.assertEqual(search.content_word_hits(self.conn, q), [])
        self.assertEqual(search.content_word_hits(self.conn, q, loose=True), ["OVEN01"])
        self.assertEqual(search.rank(self.conn, q), ["OVEN01"])

    def test_common_words_do_not_count_on_their_own(self):
        # "day" is in two of five reels, "cold" in one.
        self.assertEqual(search.fts_content(self.conn, "a cold day"), '"cold"')
        self.assertEqual(search.rank(self.conn, "a cold day"), ["DOUGH1"])
        self.assertIsNone(search.fts_content(self.conn, "the day"))

    def test_stopwords(self):
        self.assertEqual(search.content_tokens("That video where he says Pizza pizza"), ["Pizza"])

    def test_unspaced_scripts_match_inside_a_run(self):
        zp = write_zip(
            self.dir / "j.zip",
            saved_posts(
                [
                    post_record(R + "KOYO01/", timestamp=50, caption="長野の紅葉スポット5選"),
                ]
            ),
        )
        import_export(self.conn, zp)
        self.assertEqual(search.rank(self.conn, "紅葉"), ["KOYO01"])

    def test_unspaced_words_match_inside_a_recipe(self):
        from app.recipes import service

        zp = write_zip(
            self.dir / "r.zip",
            saved_posts(
                [
                    post_record(R + "CURRY1/", timestamp=50, caption="わが家の決定版🍛"),
                ]
            ),
        )
        import_export(self.conn, zp)
        service.save_user(
            self.conn,
            "CURRY1",
            service.from_dict({"title": "究極のおうちカレー", "ingredients": [{"item": "玉ねぎ"}]}),
        )
        self.assertEqual(search.rank(self.conn, "カレー"), ["CURRY1"])
        self.assertEqual(search.rank(self.conn, "玉ねぎ"), ["CURRY1"])

    def test_a_creator_name_typed_with_spaces(self):
        zp = write_zip(
            self.dir / "c.zip",
            saved_posts(
                [
                    post_record(
                        R + "SUPER1/",
                        timestamp=50,
                        caption="Fuji Speedway",
                        username="tokyocarspots_example",
                    ),
                ]
            ),
        )
        import_export(self.conn, zp)
        self.assertEqual(search.rank(self.conn, "tokyo carspots"), ["SUPER1"])

    def test_explain(self):
        why: dict[str, dict[str, Any]] = {}
        hits = search.rank(self.conn, "the best marinara in rome", explain=why)
        self.assertEqual(hits, ["PHRASE"])
        self.assertEqual(why["PHRASE"]["match"], ["phrase", "keyword"])
        self.assertIsNone(why["PHRASE"]["similarity"])

    def test_fuse_rewards_agreement(self):
        self.assertEqual(search.fuse(["a", "b", "c"], ["b"]), ["b", "a", "c"])


@unittest.skipUnless(HAS_VEC, "sqlite-vec not installed")
class TestSemanticSearch(LibraryCase):
    def setUp(self):
        super().setUp()
        self.assertTrue(search.load_vec(self.conn))
        worker.embed_all(self.conn, self.embed)

    def rank(self, q):
        return search.rank(self.conn, q, toy_vector(q))

    def test_finds_a_reel_that_never_uses_the_words(self):
        hits = self.rank("a marinara from a hot oven")
        self.assertIn("DOUGH1", hits)
        self.assertIn("OVEN01", hits)
        self.assertNotIn("AE8600", hits)

    def test_the_exact_phrase_still_comes_first(self):
        hits = self.rank("the best marinara in rome")
        self.assertEqual(hits[0], "PHRASE")
        self.assertIn("DOUGH1", hits)

    def test_unrelated_reels_stay_out(self):
        self.assertEqual(self.rank("initial d corolla"), ["AE8600"])

    def test_a_reel_is_found_by_what_was_said_in_it(self):
        # Nothing in its caption is about cars; the voiceover is.
        self.conn.execute(
            "UPDATE item SET transcript = ? WHERE shortcode = 'OVEN01'",
            (" ".join(["drift", "corolla"] * 10),),
        )
        worker.embed_all(self.conn, self.embed)
        why: dict[str, dict[str, Any]] = {}
        hits = search.rank(self.conn, "corolla drift", toy_vector("corolla drift"), explain=why)
        self.assertIn("OVEN01", hits)
        self.assertIn("transcript", why["OVEN01"]["match"])

    def test_more_like_this(self):
        # Two pizza reels are near each other, and the car reel is near neither.
        near = search.similar(self.conn, "DOUGH1")
        self.assertIn("OVEN01", near)
        self.assertNotIn("DOUGH1", near)
        self.assertNotIn("AE8600", near)

    def test_vectors_of_another_size_are_skipped_not_an_error(self):
        self.conn.execute(
            "UPDATE item_vec SET embedding = ? WHERE shortcode = 'AE8600'", (pack([1.0] * 8),)
        )
        self.assertNotIn("AE8600", self.rank("corolla"))


class TestEval(LibraryCase):
    def test_scores(self):
        from app.search.eval import Query, score

        q = Query("pizza", "keyword", {"A": 2, "B": 1})
        self.assertEqual(score(q, ["A", "B"], 1).ndcg, 1.0)
        s = score(q, ["X", "B", "A"], 1)
        self.assertEqual((s.recall, s.mrr), (1.0, 0.5))
        self.assertLess(s.ndcg, score(q, ["X", "A", "B"], 1).ndcg)
        self.assertEqual(score(q, [], 1).ndcg, 0.0)

    def test_search_as_it_was_at_a_ref(self):
        # HEAD is there in a shallow CI checkout too.
        from app.search.eval import rank_at

        why: dict[str, dict[str, Any]] = {}
        self.assertEqual(rank_at("HEAD")(self.conn, "dough", None, why), ["DOUGH1"])
        with self.assertRaises(SystemExit):
            rank_at("no-such-ref")


if __name__ == "__main__":
    unittest.main()
