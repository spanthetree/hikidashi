import json
import re
import tempfile
import unittest
from pathlib import Path

from app import search
from app.db import init, open_db
from app.importer.service import import_export
from app.tagging import evaluate, service
from app.tagging.taxonomy import classify, clean, is_noise, names, normalise, primary
from tests.fixtures import post_record, saved_posts, write_zip

R = "https://www.instagram.com/reel/"


class TestNormalise(unittest.TestCase):
    def test_strips_case_and_punctuation(self):
        self.assertEqual(normalise("#Easy_Recipe!"), "easyrecipe")
        self.assertEqual(normalise("4AGE-16V"), "4age16v")


class TestClassify(unittest.TestCase):
    def test_maps_hashtags_onto_the_taxonomy(self):
        cases = {
            ("recipe", "dinner"): "Food",
            ("roadtrip", "japan"): "Travel",
            ("ae86", "jdm"): "Automotive",
            ("simracing",): "Sim racing",
            ("cybersecurity",): "Technology",
        }
        for tags, expected in cases.items():
            with self.subTest(tags=tags):
                self.assertIn(expected, classify(tags))

    def test_one_post_can_land_in_several_categories(self):
        self.assertEqual(
            classify(["ae86", "simracing", "roadtrip"]),
            {"Automotive", "Sim racing", "Travel"},
        )

    def test_substrings_do_not_produce_false_positives(self):
        """The reason "car" is not a stem: it is inside unrelated words."""
        for tag, wrong in [
            ("cardio", "Automotive"),
            ("scartissue", "Automotive"),
            ("foodforthought", None),
        ]:
            with self.subTest(tag=tag):
                if wrong:
                    self.assertNotIn(wrong, classify([tag]))

    def test_unmatched_hashtags_produce_nothing(self):
        self.assertEqual(classify(["txfoodie2024xyz", "qqq"]), {"Food"})
        self.assertEqual(classify(["zzzznothing"]), set())

    def test_empty_input_is_safe(self):
        self.assertEqual(classify([]), set())


class TestShownInEveryLanguage(unittest.TestCase):
    def test_every_category_has_a_name_in_the_ui(self):
        """A category the web app has no name for shows untranslated (#145)."""
        root = Path(__file__).resolve().parent.parent / "frontend"
        i18n = (root / "src" / "lib" / "i18n.ts").read_text()
        tags = i18n[i18n.index("const TAGS") :]
        tags = tags[: tags.index("};")]
        for name in names():
            with self.subTest(name):
                self.assertIn(f'"{name}": m.', tags)
        used = set(re.findall(r"m\.(\w+)", tags))
        for lang in ("en", "ja"):
            messages = json.loads((root / "messages" / f"{lang}.json").read_text())
            self.assertLessEqual(used, set(messages), lang)


class TestPrimary(unittest.TestCase):
    def test_the_category_with_the_most_hashtags_wins(self):
        self.assertEqual(primary(["recipe", "dinner", "pasta", "travel"]), "Food")

    def test_a_tie_goes_to_the_narrower_category(self):
        self.assertEqual(primary(["simracing", "gaming"]), "Sim racing")
        self.assertEqual(primary(["simracing", "jdm"]), "Sim racing")

    def test_otherwise_a_tie_goes_by_name(self):
        self.assertEqual(primary(["roadtrip", "jdm"]), "Automotive")

    def test_nothing_matching_is_none(self):
        self.assertIsNone(primary(["zzzznothing"]))
        self.assertIsNone(primary([]))

    def test_the_six_from_hand_labels(self):
        """#145: each new category, and the hashtags kept out of them."""
        self.assertEqual(primary(["animerecommendation", "cowboybebop"]), "Movies & TV")
        self.assertEqual(primary(["film"]), "Movies & TV")
        self.assertEqual(primary(["filmphotography"]), "Design")  # not a film
        self.assertEqual(primary(["astronomy", "milkyway"]), "Nature & science")
        self.assertIsNone(primary(["nature"]))  # travel reels use it as much
        self.assertEqual(primary(["parenting", "toddler"]), "Family & wellbeing")
        self.assertEqual(primary(["fitnessmotivation"]), "Fitness")  # not wellbeing
        self.assertEqual(primary(["hairstyle", "braids"]), "Style & beauty")
        self.assertEqual(primary(["austinrealtor", "austinlife"]), "Home & living")
        self.assertEqual(primary(["realestate", "trust"]), "Finance")  # property as money
        self.assertEqual(primary(["giftideas", "austintx"]), "Shopping")
        self.assertEqual(primary(["workshop"]), "Home & DIY")  # not a shop

    def test_dropped_categories_are_gone(self):
        self.assertIsNone(primary(["dogsofinstagram", "guitar"]))


class TestClean(unittest.TestCase):
    def test_normalises_drops_noise_and_dedupes_in_order(self):
        self.assertEqual(
            clean(["#Birria", "fyp", "birria", "Easy_Recipe", "ab", "1"]), ["birria", "easyrecipe"]
        )

    def test_empty(self):
        self.assertEqual(clean([]), [])


class TestNoise(unittest.TestCase):
    def test_engagement_bait_is_noise(self):
        for tag in ["viral", "fyp", "reels", "explorepage", "1", "ab"]:
            with self.subTest(tag=tag):
                self.assertTrue(is_noise(tag))

    def test_real_topics_are_not_noise(self):
        for tag in ["sourdough", "ae86", "homelab"]:
            with self.subTest(tag=tag):
                self.assertFalse(is_noise(tag))


class TaggingCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.conn = open_db(self.dir / "t.sqlite3")
        zp = write_zip(
            self.dir / "e.zip",
            saved_posts(
                [
                    post_record(
                        R + "FOOD01/",
                        timestamp=300,
                        username="crumb",
                        caption="soup",
                        hashtags=("recipe", "dinner", "viral"),
                    ),
                    post_record(
                        R + "CAR001/",
                        timestamp=200,
                        username="rolla",
                        caption="4age",
                        hashtags=("ae86", "jdm", "4age16v"),
                    ),
                    post_record(R + "NONE01/", timestamp=100, username="x", caption="no tags here"),
                ]
            ),
        )
        import_export(self.conn, zp)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def primary(self, shortcode):
        return self.conn.execute(
            "SELECT primary_tag FROM item WHERE shortcode = ?", (shortcode,)
        ).fetchone()[0]

    def shown(self, shortcode):
        """The tag the library's owner sees: theirs, else the shared one."""
        tags = service.item_tags(self.conn, shortcode)
        return tags[0]["name"] if tags else None

    def tags_on(self, shortcode, source=None):
        return {
            t["name"]
            for t in service.item_tags(self.conn, shortcode)
            if source in (None, t["source"])
        }


class TestApplyHashtags(TaggingCase):
    def test_assigns_one_category_from_hashtags(self):
        stats = service.apply_hashtags(self.conn)
        self.assertEqual(stats.items, 3)
        self.assertEqual(stats.tagged, 2)
        self.assertEqual(stats.untagged, 1)
        self.assertEqual(self.tags_on("FOOD01"), {"Food"})
        self.assertEqual(self.tags_on("CAR001"), {"Automotive"})
        self.assertEqual(self.tags_on("NONE01"), set())

    def test_the_item_shows_it(self):
        service.apply_hashtags(self.conn)
        self.assertEqual(self.primary("FOOD01"), "Food")
        self.assertIsNone(self.primary("NONE01"))

    def test_cleans_hashtags_and_keeps_the_raw_ones(self):
        service.apply_hashtags(self.conn)
        row = self.conn.execute(
            "SELECT hashtags, clean_hashtags FROM item WHERE shortcode = 'FOOD01'"
        ).fetchone()
        self.assertEqual(json.loads(row["hashtags"]), ["recipe", "dinner", "viral"])
        self.assertEqual(json.loads(row["clean_hashtags"]), ["recipe", "dinner"])
        self.assertIsNone(
            self.conn.execute(
                "SELECT clean_hashtags FROM item WHERE shortcode = 'NONE01'"
            ).fetchone()[0]
        )

    def test_sets_hashtag_v_so_a_second_run_is_a_no_op(self):
        service.apply_hashtags(self.conn)
        again = service.apply_hashtags(self.conn)
        self.assertEqual(again.items, 0)

    def test_force_re_tags_everything(self):
        service.apply_hashtags(self.conn)
        again = service.apply_hashtags(self.conn, force=True)
        self.assertEqual(again.items, 3)

    def test_re_tagging_does_not_duplicate_rows(self):
        service.apply_hashtags(self.conn)
        before = self.conn.execute("SELECT COUNT(*) FROM item_tag").fetchone()[0]
        service.apply_hashtags(self.conn, force=True)
        after = self.conn.execute("SELECT COUNT(*) FROM item_tag").fetchone()[0]
        self.assertEqual(before, after)

    def test_taxonomy_tags_are_created_with_the_right_kind(self):
        service.apply_hashtags(self.conn)
        kinds = dict(self.conn.execute("SELECT name, kind FROM tag"))
        self.assertEqual(kinds["Food"], "taxonomy")

    def test_hashtags_tags_and_keywords_are_searchable(self):
        service.apply_hashtags(self.conn)
        self.conn.execute(
            """UPDATE item SET keywords = '["brake pads"]' WHERE shortcode = 'CAR001'"""
        )
        self.assertEqual(search.rank(self.conn, "jdm"), ["CAR001"])  # hashtag
        self.assertEqual(search.rank(self.conn, "automotive"), ["CAR001"])  # tag
        self.assertEqual(search.rank(self.conn, "brake pads"), ["CAR001"])  # keyword
        self.assertEqual(search.rank(self.conn, "viral"), [])  # noise


class TestProvenance(TaggingCase):
    def test_one_row_per_source(self):
        service.set_tag(self.conn, "FOOD01", "Design", service.MODEL)
        service.set_tag(self.conn, "FOOD01", "Travel", service.MODEL)
        self.assertEqual(self.tags_on("FOOD01", "model"), {"Travel"})

    def test_yours_outranks_the_model_which_outranks_the_hashtags(self):
        service.apply_hashtags(self.conn)
        self.assertEqual(self.primary("FOOD01"), "Food")
        service.set_tag(self.conn, "FOOD01", "Travel", service.MODEL)
        self.assertEqual(self.primary("FOOD01"), "Travel")
        service.set_user_tag(self.conn, "FOOD01", "Design")
        self.assertEqual(self.shown("FOOD01"), "Design")
        self.assertEqual(self.primary("FOOD01"), "Travel")  # shared, unchanged
        service.clear_user_tag(self.conn, "FOOD01")
        self.assertEqual(self.shown("FOOD01"), "Travel")
        service.set_tag(self.conn, "FOOD01", None, service.MODEL)
        self.assertEqual(self.primary("FOOD01"), "Food")

    def test_a_user_tag_survives_re_tagging(self):
        service.apply_hashtags(self.conn)
        service.set_user_tag(self.conn, "FOOD01", "Weeknight")
        service.apply_hashtags(self.conn, force=True)

        self.assertEqual(self.tags_on("FOOD01", "user"), {"Weeknight"})
        self.assertEqual(self.tags_on("FOOD01", "hashtag"), {"Food"})
        self.assertEqual(self.shown("FOOD01"), "Weeknight")

    def test_a_model_tag_is_untouched_by_the_hashtag_pass(self):
        service.set_tag(self.conn, "NONE01", "Design", service.MODEL)
        service.apply_hashtags(self.conn, force=True)
        self.assertEqual(self.tags_on("NONE01", "model"), {"Design"})

    def test_agreeing_sources_keep_their_own_rows(self):
        """One source re-running cannot take another's identical answer with it."""
        service.apply_hashtags(self.conn)
        service.set_tag(self.conn, "FOOD01", "Food", service.MODEL)
        self.conn.execute("UPDATE item SET hashtags = '[]' WHERE shortcode = 'FOOD01'")
        service.apply_hashtags(self.conn, force=True)
        self.assertEqual(self.tags_on("FOOD01", "model"), {"Food"})
        self.assertEqual(self.primary("FOOD01"), "Food")

    def test_a_user_tag_reuses_an_existing_name_whatever_the_case(self):
        service.apply_hashtags(self.conn)
        self.assertEqual(service.set_user_tag(self.conn, "CAR001", "food"), "Food")
        self.assertEqual(self.tags_on("CAR001", "user"), {"Food"})
        n = self.conn.execute(
            "SELECT COUNT(*) FROM tag WHERE name = 'food' COLLATE NOCASE"
        ).fetchone()[0]
        self.assertEqual(n, 1)

    def test_a_new_name_of_yours_goes_when_nothing_uses_it(self):
        service.set_user_tag(self.conn, "FOOD01", "Weeknight")
        self.assertIn(
            {"name": "Weeknight", "kind": "user", "count": 1}, service.tag_counts(self.conn)
        )
        service.clear_user_tag(self.conn, "FOOD01")
        self.assertNotIn("Weeknight", [t["name"] for t in service.tag_counts(self.conn)])

    def test_item_tags_lists_the_shown_one_first(self):
        service.apply_hashtags(self.conn)
        service.set_tag(self.conn, "FOOD01", "Travel", service.MODEL, confidence=0.9)
        service.set_user_tag(self.conn, "FOOD01", "Design")
        tags = service.item_tags(self.conn, "FOOD01")
        self.assertEqual(
            [(t["name"], t["source"]) for t in tags],
            [("Design", "user"), ("Travel", "model"), ("Food", "hashtag")],
        )

    def test_tag_counts_count_what_items_show(self):
        service.apply_hashtags(self.conn)
        service.set_tag(self.conn, "CAR001", "Travel", service.MODEL)
        counts = {t["name"]: t["count"] for t in service.tag_counts(self.conn)}
        self.assertEqual((counts["Food"], counts["Travel"], counts["Automotive"]), (1, 1, 0))

    def test_tags_and_collections_stay_separate(self):
        """Collections are Instagram's; tags are ours. Different tables."""
        service.apply_hashtags(self.conn)
        cols = self.conn.execute("SELECT COUNT(*) FROM collection").fetchone()[0]
        self.assertEqual(cols, 0)
        self.assertTrue(self.conn.execute("SELECT COUNT(*) FROM item_tag").fetchone()[0] > 0)


class TestEvaluate(TaggingCase):
    def test_no_labels(self):
        self.assertIn("no labels yet", evaluate.report(evaluate.evaluate(self.conn)))

    def test_scores_each_source_against_yours(self):
        service.apply_hashtags(self.conn)  # FOOD01 Food, CAR001 Automotive
        service.set_tag(self.conn, "FOOD01", "Travel", service.MODEL)
        for code, yours in (("FOOD01", "Food"), ("CAR001", "Automotive"), ("NONE01", "Design")):
            service.set_user_tag(self.conn, code, yours)
        scores = evaluate.evaluate(self.conn)
        model, hashtag, shown = scores["model"], scores["hashtag"], scores["shown"]
        self.assertEqual((model.labelled, model.answered, model.exact), (3, 1, 0))
        self.assertEqual((hashtag.answered, hashtag.exact), (2, 2))
        self.assertEqual((shown.answered, shown.exact), (2, 1))
        self.assertEqual(shown.misses, [("FOOD01", "Food", "Travel")])
        self.assertIn("3 reels tagged by hand", evaluate.report(scores))

    def test_labels_from_a_file_and_none_fits(self):
        """A null label says no category fits: answering it is wrong, and
        leaving it blank is right (tests/tag_labels.json, #6)."""
        service.apply_hashtags(self.conn)  # FOOD01 Food, CAR001 Automotive
        path = self.dir / "labels.json"
        labels = {"FOOD01": "Food", "CAR001": None, "NONE01": None}
        path.write_text(json.dumps({"labels": labels}))
        scores = evaluate.evaluate(self.conn, truth=evaluate.labels_from(path))
        hashtag = scores["hashtag"]
        self.assertEqual((hashtag.labelled, hashtag.unfit), (1, 2))
        self.assertEqual((hashtag.answered, hashtag.exact, hashtag.abstained), (1, 1, 1))
        self.assertEqual(hashtag.precision, 0.5)  # Food right, Automotive on a "none"
        self.assertEqual(hashtag.misses, [("CAR001", "none", "Automotive")])
        self.assertIn("2 where no category fits", evaluate.report(scores))
        self.assertEqual(evaluate.summary(scores)["hashtag"]["abstained"], 1)

    def test_pick_skips_your_labels_and_says_why(self):
        service.apply_hashtags(self.conn)
        service.set_user_tag(self.conn, "FOOD01", "Food")
        chosen = evaluate.pick(self.conn, 10)
        codes = [code for code, _, _ in chosen]
        self.assertNotIn("FOOD01", codes)
        self.assertCountEqual(codes, ["CAR001", "NONE01"])
        why = {code: reason for code, _, reason in chosen}
        self.assertEqual(why["NONE01"], "no hashtags")
        self.assertEqual(evaluate.pick(self.conn, 1)[0][0], "NONE01")


# The tables as they were before #21: any number of tags per item.
OLD_TAGS = """
DROP TRIGGER item_tag_ai; DROP TRIGGER item_tag_ad; DROP TRIGGER item_tag_au;
DROP TABLE item_tag;
CREATE TABLE item_tag (
  shortcode TEXT NOT NULL REFERENCES item(shortcode) ON DELETE CASCADE,
  tag_id INTEGER NOT NULL REFERENCES tag(id) ON DELETE CASCADE,
  source TEXT NOT NULL, confidence REAL, PRIMARY KEY (shortcode, tag_id));
DROP TRIGGER item_ai; DROP TRIGGER item_ad; DROP TRIGGER item_au;
DROP TABLE item_fts;
CREATE VIRTUAL TABLE item_fts USING fts5(caption, transcript, ocr_text, summary,
  content='item', content_rowid='rowid');
"""


class TestConsolidate(TaggingCase):
    def setUp(self):
        super().setUp()
        # As before #12 too: the user rows below move to user_item after.
        self.conn.execute("DROP TABLE user_item")
        self.conn.executescript(OLD_TAGS)
        self.conn.execute("UPDATE item SET primary_tag = NULL, clean_hashtags = NULL")
        tags = {
            "Food": "taxonomy",
            "Travel": "taxonomy",
            "Automotive": "taxonomy",
            "Music": "taxonomy",
            "texas": "taxonomy",
            "bucket list": "taxonomy",
            "Weeknight": "user",
            "Spicy": "user",
            "tortellini": "proposed",
            "nope": "dismissed",
        }
        for name, kind in tags.items():
            self.conn.execute("INSERT INTO tag (name, kind) VALUES (?, ?)", (name, kind))
        rows = [
            ("FOOD01", "Food", "hashtag"),
            ("FOOD01", "Travel", "model"),
            ("FOOD01", "texas", "model"),
            ("FOOD01", "Weeknight", "user"),
            ("FOOD01", "Spicy", "user"),
            ("CAR001", "Automotive", "hashtag"),
            ("CAR001", "Travel", "model"),
            ("CAR001", "Music", "model"),
            ("CAR001", "bucket list", "hashtag"),
            ("NONE01", "Music", "model"),
        ]
        for code, name, source in rows:
            self.conn.execute(
                """INSERT INTO item_tag (shortcode, tag_id, source)
                   VALUES (?, (SELECT id FROM tag WHERE name = ?), ?)""",
                (code, name, source),
            )
        self.conn.execute(
            """UPDATE item SET keywords = '["bucketlist"]' WHERE shortcode = 'CAR001'"""
        )
        init(self.conn)

    def keywords(self, code):
        return json.loads(
            self.conn.execute("SELECT keywords FROM item WHERE shortcode = ?", (code,)).fetchone()[
                0
            ]
            or "[]"
        )

    def test_one_row_per_source(self):
        self.assertEqual(
            self.conn.execute(
                """SELECT COUNT(*) FROM (SELECT 1 FROM item_tag
               GROUP BY shortcode, source HAVING COUNT(*) > 1)"""
            ).fetchone()[0],
            0,
        )
        self.assertEqual(self.tags_on("CAR001", "model"), {"Travel"})
        self.assertEqual(self.tags_on("CAR001", "hashtag"), {"Automotive"})

    def test_yours_is_kept_and_shown(self):
        self.assertEqual(self.tags_on("FOOD01", "user"), {"Spicy"})
        self.assertEqual(self.shown("FOOD01"), "Spicy")
        self.assertEqual(self.primary("FOOD01"), "Travel")  # the shared one

    def test_the_model_outranks_the_hashtags(self):
        self.assertEqual(self.primary("CAR001"), "Travel")

    def test_a_dropped_category_leaves_nothing_behind(self):
        self.assertIsNone(self.primary("NONE01"))
        self.assertEqual(self.keywords("NONE01"), ["music"])

    def test_proposals_and_extra_tags_of_yours_become_keywords(self):
        self.assertEqual(self.keywords("FOOD01"), ["texas", "weeknight"])
        self.assertEqual(self.keywords("CAR001"), ["bucketlist", "bucket list", "music"])

    def test_former_proposals_still_find_their_reels(self):
        self.assertIn("FOOD01", search.rank(self.conn, "texas"))
        self.assertIn("CAR001", search.rank(self.conn, "bucket list"))

    def test_only_categories_and_yours_are_left(self):
        kinds = dict(self.conn.execute("SELECT name, kind FROM tag"))
        self.assertNotIn("tortellini", kinds)
        self.assertNotIn("Music", kinds)
        self.assertNotIn("texas", kinds)
        self.assertEqual(kinds["Spicy"], "user")
        self.assertEqual(kinds["Food"], "taxonomy")

    def test_hashtags_are_cleaned_and_searchable(self):
        self.assertEqual(
            json.loads(
                self.conn.execute(
                    "SELECT clean_hashtags FROM item WHERE shortcode = 'FOOD01'"
                ).fetchone()[0]
            ),
            ["recipe", "dinner"],
        )
        self.assertEqual(search.rank(self.conn, "dinner"), ["FOOD01"])

    def test_it_runs_once(self):
        service.set_user_tag(self.conn, "CAR001", "Food")
        init(self.conn)
        self.assertEqual(self.shown("CAR001"), "Food")


if __name__ == "__main__":
    unittest.main()
