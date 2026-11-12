import json
import unittest
from typing import Any

from app.chat import HOSTED_FLOOR, Asks, Chat
from app.providers import limits
from app.providers.client import Client, RequestFailed
from app.providers.keys import Key
from app.providers.presets import PRESETS
from app.recipes import llm as recipe_llm
from app.tagging import games
from app.tagging import llm as tag_llm
from app.tagging.worker import tag_one
from tests.fake_openai import KEY, Fake, stage_reply


def chat(fake: Fake, preset: str = "openai", model: str = "gpt-4.1-mini") -> Chat:
    limiter = limits.Limiter(in_flight=4, per_minute=1000)
    client = Client(PRESETS[preset], Key(KEY), base_url=fake.url, limiter=limiter)
    return Chat(client, model)


class HostedCase(unittest.TestCase):
    def setUp(self):
        self.fake = Fake(chat_reply=stage_reply).start()
        self.addCleanup(self.fake.stop)

    def sent(self) -> list[dict[str, Any]]:
        return [r.json() for r in self.fake.seen("/chat/completions")]


class TestBackends(unittest.TestCase):
    def test_both_backends_answer_ask(self):
        self.assertIsInstance(tag_llm.Ollama("http://x", "m"), Asks)
        self.assertIsInstance(Chat(Client(PRESETS["openai"], Key(KEY)), "m"), Asks)

    def test_hosted_classes_are_the_stages_models(self):
        c = Client(PRESETS["openai"], Key(KEY))
        self.assertIsInstance(tag_llm.Hosted(c, "m"), games.Model)
        self.assertTrue(callable(recipe_llm.Hosted(c, "m").extract))

    def test_label_is_provider_and_model(self):
        self.assertEqual(Chat(Client(PRESETS["groq"], Key(KEY)), "m1").label, "groq:m1")
        self.assertNotIn(KEY, repr(Chat(Client(PRESETS["groq"], Key(KEY)), "m1")))


class TestHostedTagging(HostedCase):
    def test_tags_a_reel_with_the_same_prompt_and_schema(self):
        c = chat(self.fake)
        model = tag_llm.Hosted(c.client, c.model)
        row = {
            "caption": "Neapolitan pizza at home, the dough stretched by hand",
            "hashtags": json.dumps(["pizza"]),
            "transcript": None,
            "ocr_text": None,
        }
        r = tag_one(row, model)
        self.assertEqual(r.category, "Food")
        self.assertEqual(r.keywords, ["pizza"])
        sent = self.sent()[0]
        self.assertEqual(sent["messages"][0]["content"], tag_llm.SYSTEM)
        schema = sent["response_format"]["json_schema"]["schema"]
        self.assertEqual(
            schema["properties"]["category"]["enum"],
            tag_llm.schema()["properties"]["category"]["enum"],
        )
        self.assertGreaterEqual(sent["max_completion_tokens"], HOSTED_FLOOR)
        used, n = model.take_usage()
        self.assertEqual(n, 1)
        self.assertEqual(used.input_tokens, 120)
        self.assertEqual(model.take_usage()[1], 0)

    def test_the_game_pass_runs_on_the_hosted_model_too(self):
        self.fake.chat_reply = lambda req: (
            {"summary": "Gameplay.", "category": "Gaming", "confidence": "high", "keywords": []}
            if req["response_format"]["json_schema"]["name"] == "tag"
            else {"kind": "none", "title": "", "confidence": "low", "others": []}
        )
        c = chat(self.fake)
        r = tag_llm.tag(
            tag_llm.Hosted(c.client, c.model), "my new speedrun of the game", ["gaming"], None
        )
        self.assertEqual(r.category, "Gaming")
        self.assertIsNotNone(r.game)
        names = [s["response_format"]["json_schema"]["name"] for s in self.sent()]
        self.assertEqual(names, ["tag", "game"])

    def test_a_reply_off_the_schema_is_this_request_failing(self):
        self.fake.chat_reply = lambda req: {"summary": "x", "category": "Cooking"}
        c = chat(self.fake)
        with self.assertRaises(RequestFailed):
            tag_llm.Hosted(c.client, c.model)("Caption:\npizza night with friends")


class TestHostedRecipes(HostedCase):
    def test_reads_a_recipe(self):
        c = chat(self.fake)
        found = recipe_llm.read(
            recipe_llm.Hosted(c.client, c.model),
            "Pizza: dough, then bake it",
            [],
            None,
            None,
            food=True,
        )
        assert found is not None
        self.assertEqual(found.recipe.title, "pizza")
        self.assertEqual([i.item for i in found.recipe.ingredients], ["dough"])
        self.assertEqual(
            [s["response_format"]["json_schema"]["name"] for s in self.sent()],
            ["recipe_check", "recipe"],
        )

    def test_json_mode_provider(self):
        self.fake.chat_reply = lambda req: {
            "dish": "",
            "edible": False,
            "is_recipe": False,
            "confidence": "high",
        }
        c = chat(self.fake, "compatible", "llama3")
        model = recipe_llm.Hosted(c.client, c.model)
        self.assertIsNone(recipe_llm.read(model, "a wood carving", [], None, None, food=False))
        self.assertEqual(self.sent()[0]["response_format"], {"type": "json_object"})


if __name__ == "__main__":
    unittest.main()
