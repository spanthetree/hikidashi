"""Incomplete recipes: where the rest is, fetching it, translating (#114)."""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any, ClassVar

from app.db import open_db
from app.recipes import translate, web

PAGE = """<html><head>
<script type="application/ld+json">{"@context": "https://schema.org", "@graph": [
  {"@type": "WebPage", "name": "French toast"},
  {"@type": ["Recipe"], "name": "Best French Toast",
   "recipeYield": ["4", "4 servings"], "prepTime": "PT10M", "cookTime": "PT15M",
   "recipeIngredient": ["4 large eggs", "1 ½ cups whole milk, warm", "<b>salt</b>"],
   "recipeInstructions": [
     {"@type": "HowToSection", "name": "Batter", "itemListElement": [
       {"@type": "HowToStep", "text": "Whisk the eggs &amp; milk."}]},
     {"@type": "HowToStep", "text": "Fry until golden."}]}
]}</script></head><body>...</body></html>"""


class TestLink(unittest.TestCase):
    def test_a_recipe_page_beats_a_home_page(self):
        cap = "Recipe at www.blog.example → https://blog.example and https://blog.example/2023/12/toast/."
        self.assertEqual(
            web.link(cap),
            {
                "kind": "recipe",
                "url": "https://blog.example/2023/12/toast/",
                "site": "blog.example",
            },
        )
        self.assertEqual(web.link("see https://www.blog.example")["kind"], "site")  # type: ignore[index]

    def test_instagram_links_are_not_where_the_rest_is(self):
        self.assertEqual(
            web.link("Full recipe linked in bio! https://instagram.com/x"), {"kind": "bio"}
        )

    def test_comment_a_keyword(self):
        self.assertEqual(
            web.link('Comment "BIRRIA" to have the recipe sent'),
            {"kind": "comment", "keyword": "BIRRIA"},
        )

    def test_nothing_said(self):
        self.assertIsNone(web.link("just pasta"))
        self.assertIsNone(web.link(None))


class TestParse(unittest.TestCase):
    def test_a_schema_org_recipe(self):
        r = web.parse(PAGE)
        self.assertEqual((r.title, r.servings, r.time_minutes), ("Best French Toast", 4, 25))
        self.assertEqual(r.steps, ["Whisk the eggs & milk.", "Fry until golden."])
        milk = r.ingredients[1]
        self.assertEqual(
            (milk.quantity, milk.unit, milk.item, milk.note), ("1 ½", "cup", "whole milk", "warm")
        )
        self.assertEqual(r.ingredients[2].item, "salt")
        self.assertFalse(r.incomplete)

    def test_no_recipe(self):
        with self.assertRaises(web.FetchError):
            web.parse("<html>nothing</html>")

    def test_durations(self):
        self.assertEqual(web.minutes("PT1H30M"), 90)
        self.assertEqual(web.minutes("P0DT45M"), 45)
        self.assertIsNone(web.minutes("soon"))

    def test_nothing_on_the_servers_own_network(self):
        for url in (
            "http://127.0.0.1/x",
            "http://localhost/x",
            "http://10.0.0.1/",
            "ftp://a.example/",
        ):
            with self.assertRaises(web.FetchError, msg=url):
                web.fetch(url)


class FakeAsks:
    def __init__(self):
        self.asked = 0

    def ask(self, system, prompt, *, schema, max_tokens, name):
        self.asked += 1
        texts = json.loads(prompt.split("\n\n", 1)[1])
        return json.dumps(
            {
                "title": "フレンチトースト",
                "ingredients": [{"item": "卵", "note": ""} for _ in texts["ingredients"]],
                "steps": ["混ぜる" for _ in texts["steps"]],
            }
        )


class TestTranslate(unittest.TestCase):
    RECIPE: ClassVar[dict[str, Any]] = {
        "title": "French toast",
        "ingredients": [{"item": "eggs", "quantity": "4", "unit": None, "note": None}],
        "steps": ["Whisk"],
    }

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn: sqlite3.Connection = open_db(Path(self.tmp.name) / "t.sqlite3")

    def test_the_apps_languages(self):
        settings = Path(__file__).parents[1] / "frontend" / "project.inlang" / "settings.json"
        locales = json.loads(settings.read_text(encoding="utf-8"))["locales"]
        self.assertEqual(sorted(translate.UI), sorted(locales))

    def test_who_needs_it(self):
        self.assertTrue(translate.needed(self.RECIPE, "ja"))
        self.assertFalse(translate.needed(self.RECIPE, "en"))
        foreign = {**self.RECIPE, "ingredients": [{"item": "eggs", "note": "常温"}]}
        self.assertTrue(translate.needed(foreign, "en"))
        self.assertFalse(translate.needed(self.RECIPE, "xx"))

    def test_translated_once_then_kept(self):
        asks = FakeAsks()
        for _ in range(2):
            body = translate.translate(self.conn, self.RECIPE, "ja", asks, "fake:model")
        self.assertEqual(asks.asked, 1)
        out = translate.merge(self.RECIPE, body)
        self.assertEqual(out["title"], "フレンチトースト")
        self.assertEqual(
            out["ingredients"][0], {"item": "卵", "quantity": "4", "unit": None, "note": None}
        )
        self.assertEqual(out["steps"], ["混ぜる"])
        # A corrected recipe is translated afresh.
        translate.translate(self.conn, {**self.RECIPE, "title": "Toast"}, "ja", asks, "fake:model")
        self.assertEqual(asks.asked, 2)

    def test_a_short_answer_leaves_the_rest_as_it_was(self):
        out = translate.merge(self.RECIPE, {"title": "", "ingredients": [], "steps": []})
        self.assertEqual(out, self.RECIPE)


if __name__ == "__main__":
    unittest.main()
