import io
import json
import sqlite3
import tempfile
import unittest
import urllib.request
from pathlib import Path
from typing import Any
from unittest import mock

from app import ollama
from app.asr.transcriber import ASR_V
from app.db import open_db
from app.recipes import evaluate, llm, service, worker
from app.recipes.detect import is_candidate, sends_elsewhere, signals
from app.recipes.groups import groups_of
from app.recipes.llm import RECIPE_V, RecipeError, Sources, parse_check, parse_recipe
from app.tagging import service as tagging
from app.tagging.llm import TAG_V
from tests.fixtures import must

R = "https://www.instagram.com/reel/"


def yes(confidence="high", dish="noodles", edible=True) -> str:
    return json.dumps({"dish": dish, "edible": edible, "is_recipe": True, "confidence": confidence})


NO = json.dumps({"is_recipe": False, "confidence": "high"})


def ing(item, as_written=None, quantity="", unit="", note="") -> dict[str, Any]:
    return {
        "item": item,
        "as_written": as_written or item,
        "quantity": quantity,
        "unit": unit,
        "note": note,
    }


def recipe(
    title="Chilli oil noodles", ingredients=(), steps=("Boil.",), servings=0, time_minutes=0
) -> str:
    return json.dumps(
        {
            "title": title,
            "servings": servings,
            "time_minutes": time_minutes,
            "ingredients": list(ingredients),
            "steps": list(steps),
        }
    )


class TestDetect(unittest.TestCase):
    def test_quantities_with_units(self):
        for text in (
            "200g noodles",
            "2 tbsp soy",
            "1½ cups flour",
            "3 cloves garlic",
            "½ tsp salt",
            "500 ml stock",
        ):
            with self.subTest(text=text):
                self.assertIn("quantity", signals(text))

    def test_numbers_that_are_not_quantities(self):
        for text in ("my new 5G phone", "shot in 4K", "top 10 cafés", "v2.5g build"):
            with self.subTest(text=text):
                self.assertNotIn("quantity", signals(text))

    def test_words_and_steps(self):
        self.assertIn("words", signals("Ingredients: noodles"))
        self.assertIn("words", signals("la receta completa"))
        self.assertIn("steps", signals("1. Boil the water\n2. Add noodles"))
        self.assertIn("steps", signals("so step 2 is the sauce"))
        self.assertEqual(signals("best tacos in town"), [])

    def test_a_food_tag_or_a_signal_is_enough(self):
        self.assertTrue(is_candidate("Food", "best tacos in town", None, None))
        self.assertTrue(is_candidate(None, "Ingredients below", None, None))
        self.assertFalse(is_candidate("Travel", "sunset in Lisbon", None, None))
        self.assertFalse(is_candidate("Travel", "10 places:\n1. Lisbon\n2. Porto", None, None))

    def test_no_text_is_never_a_candidate(self):
        self.assertFalse(is_candidate("Food", None, " ", None))

    def test_sends_elsewhere(self):
        for text in (
            "Full recipe link in bio!",
            "Comment 'NOODLES' and I'll send the recipe",
            "recipe is on my blog",
            "DM me for the recipe",
            "Tap the link in our bio for more",
            "linked in my @example.eats profile or comment BIRRIA to have it sent",
            "the recipe can be found here - https://example.com/2023/12/rolls",
        ):
            with self.subTest(text=text):
                self.assertTrue(sends_elsewhere(text))
        for text in (
            "Boil the noodles for 4 minutes",
            "Comment and make me go viral",
            "let's talk below in the comments",
        ):
            with self.subTest(text=text):
                self.assertFalse(sends_elsewhere(text))


class TestGroups(unittest.TestCase):
    def test_words_and_their_exceptions(self):
        cases = {
            "unsalted butter": {"Dairy"},
            "peanut butter": {"Nuts"},
            "vegan butter": set(),
            "coconut milk": set(),
            "crème fraîche": {"Dairy"},
            "eggs": {"Eggs"},
            "eggplant": set(),
            "egg noodles": {"Eggs", "Gluten"},
            "rice noodles": set(),
            "soy sauce": {"Gluten"},
            "tamari": set(),
            "fish sauce": {"Fish & seafood"},
            "oyster mushrooms": set(),
            "chicken thighs": {"Poultry"},
            "goat's cheese": {"Dairy"},
            "minced garlic": set(),
            "nutmeg": set(),
            "pine nuts": {"Nuts"},
        }
        for item, groups in cases.items():
            with self.subTest(item=item):
                self.assertEqual(groups_of(item), groups)


class TestParse(unittest.TestCase):
    def test_check(self):
        self.assertEqual(parse_check(NO, food=True), 0.0)
        self.assertEqual(parse_check(yes("high"), food=True), 0.9)
        self.assertEqual(parse_check(yes("medium"), food=True), 0.6)
        self.assertEqual(parse_check(yes("whatever"), food=True), 0.0, "a low yes is a no")
        with self.assertRaises(RecipeError):
            parse_check('{"is_recipe": "yes"}', food=True)

    def test_a_yes_for_something_nobody_eats_is_a_no(self):
        # Transparent wood: "strip the lignin, fill it with epoxy" (#97).
        self.assertEqual(parse_check(yes(dish="transparent wood", edible=False), food=False), 0.0)
        # The tagger's Food outranks the model's "not edible" for pizza dough.
        self.assertEqual(parse_check(yes(dish="pizza dough", edible=False), food=True), 0.9)
        # Washing salad: a kitchen tip that makes nothing.
        self.assertEqual(parse_check(yes(dish=""), food=True), 0.0)

    def test_a_reel_not_tagged_food_needs_a_firm_yes(self):
        self.assertEqual(parse_check(yes("medium"), food=False), 0.0)
        self.assertEqual(parse_check(yes("high"), food=False), 0.9)

    def test_quantities_stay_strings_and_blanks_are_none(self):
        r = parse_recipe(
            recipe(
                ingredients=[
                    ing("salt", quantity="to taste"),
                    ing("chilli oil", quantity="1½", unit="tbsp"),
                    ing("Salt"),
                    ing(""),
                    "noise",
                ],
                servings=2,
                time_minutes=0,
            )
        )
        self.assertEqual([i.item for i in r.ingredients], ["salt", "chilli oil"])
        self.assertEqual(r.ingredients[0].quantity, "to taste")
        self.assertIsNone(r.ingredients[0].unit)
        self.assertEqual((r.ingredients[1].quantity, r.ingredients[1].unit), ("1½", "tbsp"))
        self.assertEqual((r.servings, r.time_minutes), (2, None))

    def test_a_unit_written_twice_is_said_once(self):
        r = parse_recipe(
            recipe(
                ingredients=[
                    ing("sugar", quantity="2 tsp", unit="tsp"),
                    ing("flour", quantity="500g", unit="g"),
                    ing("vinegar", quantity="100ml (1/2 cup)", unit="ml (cup)"),
                    ing("milk", quantity="1", unit="cup"),
                    ing("ginger", quantity="2 tbsp", unit="tsp"),
                    ing("onion", quantity="1/2", note="(optional)"),
                ]
            )
        )
        self.assertEqual(
            [(i.quantity, i.unit) for i in r.ingredients],
            [
                ("2 tsp", None),
                ("500g", None),
                ("100ml (1/2 cup)", None),
                ("1", "cup"),
                ("2 tbsp", "tsp"),
                ("1/2", None),
            ],
        )
        self.assertEqual(r.ingredients[-1].note, "optional")

    def test_a_placeholder_title_is_no_title(self):
        self.assertIsNone(parse_recipe(recipe(title="No title provided.")).title)
        self.assertEqual(parse_recipe(recipe(title="Naan")).title, "Naan")

    def test_malformed_replies_are_errors(self):
        for bad in ('{"title": "cut', "[]", '{"ingredients": "salt"}'):
            with self.subTest(bad=bad), self.assertRaises(RecipeError):
                parse_recipe(bad)

    def test_the_schema_forbids_nothing_it_needs_and_caps_lists(self):
        props = llm.extract_schema()["properties"]
        self.assertEqual(props["ingredients"]["maxItems"], llm.MAX_INGREDIENTS)
        self.assertEqual(props["servings"]["type"], "integer")


class TestGround(unittest.TestCase):
    def test_an_ingredient_no_source_names_is_dropped(self):
        r = parse_recipe(
            recipe(ingredients=[ing("wheat noodles"), ing("garlic"), ing("spring onions")])
        )
        fields, dropped = llm.ground(
            r,
            Sources(
                caption="200g wheat noodles, chilli oil", transcript="then top with a spring onion"
            ),
        )
        self.assertEqual([i.item for i in r.ingredients], ["wheat noodles", "spring onions"])
        self.assertEqual(dropped, ["garlic"])
        self.assertEqual(fields, ["caption", "transcript"])

    def test_the_sources_own_words_ground_a_translation(self):
        r = parse_recipe(
            recipe(
                ingredients=[
                    ing("soy sauce", as_written="醤油"),
                    ing("butter", as_written="mantequilla"),
                ]
            )
        )
        _, dropped = llm.ground(r, Sources(caption="醤油 大さじ2", ocr="mantequilla 50g"))
        self.assertEqual(dropped, [])

    def test_filler_words_need_not_appear(self):
        s = Sources(caption="a handful of basil and two tomatoes")
        self.assertEqual(s.where("fresh basil"), ["caption"])
        self.assertEqual(s.where("tomato"), ["caption"])
        self.assertEqual(s.where("cherry tomatoes"), [])


class FakeModel:
    """Answers by shortcode, which it finds in the caption."""

    def __init__(self, checks: dict[str, Any], extracts: dict[str, Any] | None = None):
        self.checks, self.extracts = checks, extracts or {}
        self.asked: list[tuple[str, str]] = []

    def _answer(self, kind, answers, prompt):
        code = next(c for c in answers if c in prompt)
        self.asked.append((kind, code))
        a = answers[code]
        if isinstance(a, Exception):
            raise a
        return a

    def check(self, prompt):
        return self._answer("check", self.checks, prompt)

    def extract(self, prompt):
        return self._answer("extract", self.extracts, prompt)


class TestRead(unittest.TestCase):
    def test_a_no_never_asks_for_a_recipe(self):
        m = FakeModel({"X": NO})
        self.assertIsNone(llm.read(m, "X tacos", [], None, None, food=True))
        self.assertEqual(m.asked, [("check", "X")])

    def test_incomplete_is_decided_from_the_text_not_the_model(self):
        full = recipe(ingredients=[ing("noodles")], steps=["Boil."])
        self.assertFalse(
            must(
                llm.read(
                    FakeModel({"X": yes()}, {"X": full}), "X noodles", [], None, None, food=True
                )
            ).recipe.incomplete
        )
        no_steps = recipe(ingredients=[ing("noodles")], steps=[])
        self.assertTrue(
            must(
                llm.read(
                    FakeModel({"X": yes()}, {"X": no_steps}), "X noodles", [], None, None, food=True
                )
            ).recipe.incomplete
        )

    def test_a_low_confidence_yes_is_a_no(self):
        self.assertIsNone(llm.read(FakeModel({"X": yes("low")}), "X", [], None, None, food=True))

    def test_link_in_bio_is_incomplete(self):
        m = FakeModel({"X": yes()}, {"X": recipe(ingredients=[ing("noodles")])})
        found = must(llm.read(m, "X noodles, full recipe link in bio", [], None, None, food=True))
        self.assertTrue(found.recipe.incomplete)
        self.assertEqual(found.source_fields, ["caption"])

    def test_nothing_to_read_asks_nothing(self):
        m = FakeModel({})
        self.assertIsNone(llm.read(m, None, [], " ", None, food=True))
        self.assertEqual(m.asked, [])


class TestOllamaClient(unittest.TestCase):
    def test_check_and_extract_send_their_own_schema_and_cap(self):
        client = llm.Ollama("http://ollama:11434", "qwen2.5:3b")
        bodies = []

        def answer(req, timeout=None):
            bodies.append(json.loads(req.data))
            return io.BytesIO(
                json.dumps({"message": {"content": "{}"}, "done_reason": "stop"}).encode()
            )

        with mock.patch.object(urllib.request, "urlopen", side_effect=answer):
            client.check("p")
            client.extract("p")
        self.assertEqual(bodies[0]["format"], llm.check_schema())
        self.assertEqual(bodies[0]["options"]["num_predict"], llm.CHECK_PREDICT)
        self.assertEqual(bodies[1]["format"], llm.extract_schema())
        self.assertEqual(bodies[1]["options"]["num_predict"], llm.EXTRACT_PREDICT)
        self.assertEqual(bodies[1]["messages"][0]["content"], llm.EXTRACT_SYSTEM)


class WorkerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = open_db(Path(self.tmp.name) / "t.sqlite3")
        rows = [
            (
                "NOODLE",
                "NOODLE chilli oil noodles. 200g wheat noodles, 2 tbsp chilli oil, "
                "1 tbsp soy sauce, spring onion",
                300,
            ),
            ("TACOS1", "TACOS1 the best tacos in town", 200),
            ("SUNSET", "SUNSET watching it go down", 100),
        ]
        for code, caption, saved in rows:
            self.conn.execute(
                """INSERT INTO item (shortcode, permalink, caption, saved_at, tag_v)
                   VALUES (?, ?, ?, ?, ?)""",
                (code, f"{R}{code}/", caption, saved, TAG_V),
            )
            self.save(code)
        tagging.ensure_taxonomy(self.conn)
        tagging.set_tag(self.conn, "TACOS1", "Food", "model")
        self.model = FakeModel(
            {"NOODLE": yes(), "TACOS1": NO},
            {
                "NOODLE": recipe(
                    ingredients=[
                        ing("wheat noodles", quantity="200", unit="g"),
                        ing("chilli oil", quantity="2", unit="tbsp"),
                        ing("soy sauce", quantity="1", unit="tbsp"),
                        ing("spring onion"),
                        ing("garlic"),
                    ],
                    steps=["Boil the noodles.", "Toss with the oil."],
                    time_minutes=15,
                )
            },
        )

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def save(self, code, user_id=1):
        """Put a reel in someone's library, as an import would."""
        self.conn.execute(
            "INSERT INTO user_item (user_id, shortcode) VALUES (?, ?)", (user_id, code)
        )

    def run_pass(self, **kw):
        return worker.read_all(self.conn, self.model, profile="cpu", **kw)

    def row(self, code) -> sqlite3.Row:
        return self.conn.execute("SELECT * FROM item WHERE shortcode = ?", (code,)).fetchone()

    def shown(self, **kw) -> list[str]:
        service.register(self.conn)
        params: dict[str, Any] = {}
        where = service.filters(
            kw.get("has", []), kw.get("without", []), kw.get("max_time"), params
        )
        return [
            r[0]
            for r in self.conn.execute(
                f"SELECT shortcode FROM item WHERE {' AND '.join(where)} ORDER BY shortcode", params
            )
        ]


class TestReadAll(WorkerCase):
    def test_writes_the_recipe_and_stamps_everything(self):
        p = self.run_pass()
        self.assertEqual((p.total, p.recipes, p.not_recipes, p.skipped), (3, 1, 1, 1))
        r = must(service.current(self.conn, "NOODLE"))
        self.assertEqual(r["title"], "Chilli oil noodles")
        self.assertEqual(
            [i["item"] for i in r["ingredients"]],
            ["wheat noodles", "chilli oil", "soy sauce", "spring onion"],
        )
        self.assertEqual(r["ingredients"][0]["quantity"], "200")
        self.assertEqual(r["time_minutes"], 15)
        self.assertFalse(r["edited"])
        for code in ("NOODLE", "TACOS1", "SUNSET"):
            self.assertEqual(self.row(code)["recipe_v"], RECIPE_V)
            self.assertEqual(self.row(code)["recipe_profile"], "cpu")

    def test_no_invented_ingredient_is_stored(self):
        self.run_pass()
        dropped = self.conn.execute(
            "SELECT dropped FROM recipe WHERE shortcode = 'NOODLE'"
        ).fetchone()[0]
        self.assertEqual(json.loads(dropped), ["garlic"])

    def test_a_reel_that_is_not_a_candidate_costs_no_request(self):
        self.run_pass()
        self.assertNotIn("SUNSET", [code for _, code in self.model.asked])
        self.assertNotIn(("extract", "TACOS1"), self.model.asked)

    def test_nothing_is_asked_twice(self):
        self.run_pass()
        self.assertEqual(self.run_pass().total, 0)

    def test_waits_for_the_tagger_and_the_transcript(self):
        self.conn.execute("UPDATE item SET tag_v = 0 WHERE shortcode = 'TACOS1'")
        self.conn.execute("""UPDATE item SET media_path = 'v.mp4', asr_v = 0
                             WHERE shortcode = 'NOODLE'""")
        self.assertEqual([r["shortcode"] for r in worker.pending(self.conn)], ["SUNSET"])
        self.assertEqual(worker.summary(self.conn)["waiting_upstream"], 2)

        self.conn.execute("UPDATE item SET fail_stage = 'tag' WHERE shortcode = 'TACOS1'")
        self.conn.execute(
            """UPDATE item SET asr_v = ?, transcript = 'boil them'
                             WHERE shortcode = 'NOODLE'""",
            (ASR_V,),
        )
        self.assertEqual(len(worker.pending(self.conn)), 3)

    def test_ingredients_and_titles_are_searchable(self):
        from app.search import keyword_hits

        self.run_pass()
        # Only the recipe says "wheat noodles" as a phrase with the title.
        self.assertIn("NOODLE", keyword_hits(self.conn, "chilli oil noodles"))
        service.save_user(
            self.conn,
            "NOODLE",
            parse_recipe(recipe(title="Gochujang noodles", ingredients=[ing("gochujang")])),
        )
        self.assertEqual(keyword_hits(self.conn, "gochujang"), ["NOODLE"])


class TestFailures(WorkerCase):
    def test_a_bad_reply_is_recorded_unstamped_and_retried(self):
        self.model.extracts["NOODLE"] = RecipeError("reply is not JSON")
        self.assertEqual(self.run_pass().failed, 1)
        r = self.row("NOODLE")
        self.assertEqual((r["fail_stage"], r["recipe_v"]), ("recipe", 0))

        self.model.extracts["NOODLE"] = recipe(ingredients=[ing("wheat noodles")])
        self.assertEqual(self.run_pass().recipes, 1)
        self.assertIsNone(self.row("NOODLE")["fail_stage"])

    def test_losing_ollama_stops_the_run_without_marking_anything(self):
        self.model.checks["TACOS1"] = ollama.OllamaError("gone")
        with self.assertRaises(ollama.OllamaError):
            self.run_pass()
        self.assertEqual(self.row("NOODLE")["recipe_v"], RECIPE_V)  # finished, kept
        self.assertEqual(self.row("TACOS1")["recipe_v"], 0)
        self.assertIsNone(self.row("TACOS1")["fail_stage"])


class TestProvenance(WorkerCase):
    def setUp(self):
        super().setUp()
        self.run_pass()
        self.fixed = parse_recipe(recipe(title="Mine", ingredients=[ing("udon")]))

    def test_a_correction_survives_a_rerun(self):
        service.save_user(self.conn, "NOODLE", self.fixed)
        self.model.extracts["NOODLE"] = recipe(title="Model v2", ingredients=[ing("wheat noodles")])
        self.run_pass(force=True)
        r = must(service.current(self.conn, "NOODLE"))
        self.assertEqual((r["title"], r["edited"], r["has_model"]), ("Mine", True, True))
        model_title = self.conn.execute(
            "SELECT title FROM recipe WHERE shortcode = 'NOODLE' AND source = 'model'"
        ).fetchone()[0]
        self.assertEqual(model_title, "Model v2")

    def test_reverting_brings_the_model_back(self):
        service.save_user(self.conn, "NOODLE", self.fixed)
        service.revert(self.conn, "NOODLE")
        self.assertEqual(must(service.current(self.conn, "NOODLE"))["title"], "Chilli oil noodles")

    def test_not_a_recipe_hides_it_and_survives(self):
        service.save_user(self.conn, "NOODLE", self.fixed, hidden=True)
        self.assertEqual(self.shown(), [])
        self.run_pass(force=True)
        self.assertEqual(self.shown(), [])
        self.assertTrue(must(service.current(self.conn, "NOODLE"))["hidden"])

    def test_a_version_bump_reruns_only_this_stage(self):
        before = self.conn.execute(
            "SELECT tag_v, asr_v, ocr_v, embed_v, hashtag_v FROM item"
        ).fetchall()
        self.model.asked.clear()
        with (
            mock.patch.object(llm, "RECIPE_V", RECIPE_V + 1),
            mock.patch.dict(worker._PARAMS, {"v": RECIPE_V + 1}),
        ):
            self.assertEqual(self.run_pass().total, 3)
            self.assertEqual(self.row("NOODLE")["recipe_v"], RECIPE_V + 1)
        after = self.conn.execute(
            "SELECT tag_v, asr_v, ocr_v, embed_v, hashtag_v FROM item"
        ).fetchall()
        self.assertEqual([tuple(r) for r in before], [tuple(r) for r in after])
        self.assertEqual({k for k, _ in self.model.asked}, {"check", "extract"})


class TestFilters(WorkerCase):
    def setUp(self):
        super().setUp()
        self.run_pass()
        self.conn.execute(
            """INSERT INTO item (shortcode, permalink, caption, tag_v)
               VALUES ('CARBON', 'p', 'CARBON', ?)""",
            (TAG_V,),
        )
        self.save("CARBON")
        service.save_user(
            self.conn,
            "CARBON",
            parse_recipe(
                recipe(
                    title="Carbonara",
                    time_minutes=40,
                    ingredients=[ing("spaghetti"), ing("eggs"), ing("pecorino"), ing("guanciale")],
                )
            ),
        )

    def test_only_recipes(self):
        self.assertEqual(self.shown(), ["CARBON", "NOODLE"])

    def test_has_and_without_by_ingredient_and_group(self):
        self.assertEqual(self.shown(has=["Eggs"]), ["CARBON"])
        self.assertEqual(self.shown(without=["dairy"]), ["NOODLE"])
        self.assertEqual(self.shown(has=["noodles"]), ["NOODLE"])
        self.assertEqual(self.shown(has=["oil"], without=["Meat"]), ["NOODLE"])
        self.assertEqual(self.shown(has=["noodle"]), [])  # whole words

    def test_time_only_matches_recipes_that_state_one(self):
        self.assertEqual(self.shown(max_time=30), ["NOODLE"])
        self.assertEqual(self.shown(max_time=60), ["CARBON", "NOODLE"])
        self.conn.execute("UPDATE recipe SET time_minutes = NULL WHERE shortcode = 'NOODLE'")
        self.assertEqual(self.shown(max_time=60), ["CARBON"])
        self.assertEqual(service.facets(self.conn)["times"][2], {"minutes": 60, "count": 1})

    def test_facets_count_recipes(self):
        f = service.facets(self.conn)
        self.assertEqual(f["total"], 2)
        groups = {g["name"]: g["count"] for g in f["groups"]}
        self.assertEqual((groups["Gluten"], groups["Dairy"]), (2, 1))
        self.assertEqual(f["times"][1], {"minutes": 30, "count": 1})

    def test_cards(self):
        cards = service.card(self.conn, ["NOODLE", "TACOS1"])
        self.assertEqual(set(cards), {"NOODLE"})
        self.assertEqual(cards["NOODLE"]["ingredients"], 4)


class TestEvaluate(WorkerCase):
    def test_precision_recall_and_grounding(self):
        self.conn.execute(
            """INSERT INTO item (shortcode, permalink, caption, tag_v)
               VALUES ('MISSED', 'p', 'MISSED ramen', ?)""",
            (TAG_V,),
        )
        self.model.checks["MISSED"] = NO
        self.run_pass()
        labels = {"NOODLE": True, "TACOS1": False, "MISSED": True, "SUNSET": False}
        r = evaluate.evaluate(self.conn, labels)
        self.assertEqual((r.tp, r.fp, r.fn, r.tn), (1, 0, 1, 2))
        self.assertEqual((r.precision, r.recall), (1.0, 0.5))
        self.assertEqual(r.false_neg, ["MISSED"])
        self.assertEqual((r.ingredients, r.ungrounded, r.dropped), (4, [], 1))

    def test_labels_round_trip_and_what_to_label(self):
        path = Path(self.tmp.name) / "labels.json"
        self.assertEqual(evaluate.load(path), {})
        evaluate.save(path, {"NOODLE": True})
        self.assertEqual(evaluate.load(path), {"NOODLE": True})
        todo = [r["shortcode"] for r in evaluate.to_label(self.conn, {"NOODLE": True})]
        self.assertEqual(todo, ["TACOS1"])  # Food; SUNSET has no signals


class TestMigration(unittest.TestCase):
    def test_an_existing_database_gains_the_recipe_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = open_db(Path(tmp) / "old.sqlite3")
            conn.execute("INSERT INTO item (shortcode, permalink) VALUES ('OLD001', 'p')")
            conn.execute("ALTER TABLE item DROP COLUMN recipe_profile")
            conn.execute("ALTER TABLE item DROP COLUMN recipe_v")
            conn.close()
            conn = open_db(Path(tmp) / "old.sqlite3")
            row = conn.execute("SELECT recipe_v, recipe_profile FROM item").fetchone()
            self.assertEqual(tuple(row), (0, None))
            conn.close()


if __name__ == "__main__":
    unittest.main()
