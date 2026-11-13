"""Ask your library (#45): retrieval, grounding, citations."""

import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import Any

from app import ask
from app.db import open_db

DEMO = Path(__file__).parent / "eval" / "demo.sqlite3"
EVAL = json.loads((Path(__file__).parent / "ask_eval.json").read_text(encoding="utf-8"))


class Fake:
    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    def ask(self, system, prompt, *, schema, max_tokens, name):
        self.prompts.append(prompt)
        return json.dumps(self.reply)


SOURCES: list[dict[str, Any]] = [
    {
        "shortcode": c,
        "summary": c,
        "caption": "",
        "said": "",
        "on_screen": "",
        "recipe": None,
        "places": [],
    }
    for c in ("A", "B", "C")
]


class TestAnswer(unittest.TestCase):
    def test_citations_in_the_order_first_cited(self):
        out = ask.answer(
            "q", SOURCES, Fake({"found": True, "answer": "Proof 4 h [3], then bake [1][3]."})
        )
        self.assertEqual(
            out, {"found": True, "answer": "Proof 4 h [3], then bake [1][3].", "cited": [3, 1]}
        )

    def test_a_number_it_made_up_is_dropped(self):
        out = ask.answer("q", SOURCES, Fake({"found": True, "answer": "Yes [2] and [9]."}))
        self.assertEqual((out["answer"], out["cited"]), ("Yes [2] and .", [2]))

    def test_nothing_covers_it(self):
        out = ask.answer("q", SOURCES, Fake({"found": False, "answer": "Probably 3 hours."}))
        self.assertEqual(out, {"found": False, "answer": "", "cited": []})
        fake = Fake({})
        self.assertFalse(ask.answer("q", [], fake)["found"])
        self.assertEqual(fake.prompts, [])  # no reels, no question to the model

    def test_the_prompt_numbers_the_reels(self):
        fake = Fake({"found": False, "answer": ""})
        ask.answer("what?", SOURCES, fake)
        self.assertIn("[1]\nSummary: A", fake.prompts[0])
        self.assertTrue(fake.prompts[0].endswith("Question: what?"))


class TestRetrieval(unittest.TestCase):
    """The eval: every reel an answer needs is among those the model sees."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db = Path(self.tmp.name) / "demo.sqlite3"
        shutil.copy(DEMO, db)
        self.conn: sqlite3.Connection = open_db(db)
        self.user = self.conn.execute("SELECT user_id FROM user_item LIMIT 1").fetchone()[0]

    def test_ask_eval(self):
        for case in EVAL["questions"]:
            if case.get("needs") == "vectors":
                continue
            got = [s["shortcode"] for s in ask.sources(self.conn, case["q"], None, self.user)]
            for code in case["cite"]:
                self.assertIn(code, got, case["q"])

    def test_what_the_model_is_told(self):
        [crab] = [
            s
            for s in ask.sources(self.conn, "crab dip", None, self.user)
            if s["shortcode"] == "DQcrabdip01"
        ]
        self.assertIn("crab", crab["summary"].lower())
        self.assertIsNotNone(crab["recipe"])


if __name__ == "__main__":
    unittest.main()
