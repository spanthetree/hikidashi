"""The evals' fixture and history agree with the code (#6, #57).

scripts/evals.py scores search and tagging on tests/eval/demo.sqlite3; this
checks that every query has its stored vector, that every label names a reel
and a tag the fixture has, that the regression rules catch what they should,
and that docs/eval/history.json ends with this version, as the code scores it.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import unittest
from pathlib import Path
from typing import Any

from app.embed.embedder import DIM, EMBED_V, MODEL

ROOT = Path(__file__).resolve().parent.parent
HAS_VEC = importlib.util.find_spec("sqlite_vec") is not None
spec = importlib.util.spec_from_file_location("evals", ROOT / "scripts" / "evals.py")
assert spec and spec.loader
evals = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evals)


def fixture() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{evals.FIXTURE}?mode=ro", uri=True)


class TestFixture(unittest.TestCase):
    def test_every_query_has_its_vector(self):
        queries = json.loads(evals.QUERIES.read_text())["queries"]
        data = json.loads(evals.VECTORS.read_text())
        self.assertEqual((data["model"], data["embed_v"]), (MODEL, EMBED_V))
        missing = [q["q"] for q in queries if q["q"] not in data["vectors"]]
        self.assertEqual(missing, [], "run: scripts/demo_library.py fixture")
        from app.search.eval import read_vectors

        vec = read_vectors(evals.VECTORS)(queries[0]["q"])
        self.assertEqual(len(vec), DIM * 4)

    def test_every_expected_reel_is_in_the_fixture(self):
        conn = fixture()
        known = {r[0] for r in conn.execute("SELECT shortcode FROM item")}
        for q in json.loads(evals.QUERIES.read_text())["queries"]:
            with self.subTest(q["q"]):
                self.assertLessEqual(set(q["relevant"]), known)

    def test_labels_name_its_reels_and_the_taxonomy(self):
        from app.tagging.taxonomy import TAXONOMY

        conn = fixture()
        known = {r[0] for r in conn.execute("SELECT shortcode FROM item")}
        labels = json.loads(evals.LABELS.read_text())["labels"]
        self.assertEqual(set(labels), known, "every demo reel is labelled, and only those")
        self.assertLessEqual({t for t in labels.values() if t is not None}, set(TAXONOMY))

    def test_no_one_can_sign_in_with_it(self):
        conn = fixture()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM session").fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM api_token").fetchone()[0], 0)
        self.assertIsNone(conn.execute("SELECT MAX(password_hash) FROM user").fetchone()[0])


def _result(ndcg: float, kind: float, quiet: int, precision: float) -> dict[str, Any]:
    mode = {
        "ndcg": ndcg,
        "kinds": {"paraphrase": {"ndcg": kind}},
        "negatives": {"quiet": quiet},
    }
    return {
        "search": {"blended": mode, "keyword": mode},
        "tagging": {"shown": {"precision": precision}},
    }


class TestRegressions(unittest.TestCase):
    base = _result(0.95, 0.90, 4, 0.92)

    def test_small_moves_pass(self):
        self.assertEqual(evals.regressions(self.base, _result(0.94, 0.86, 4, 0.90)), [])

    def test_each_rule_fails_past_its_threshold(self):
        cases = {
            "nDCG": _result(0.92, 0.90, 4, 0.92),
            "paraphrase": _result(0.95, 0.84, 4, 0.92),
            "quiet": _result(0.95, 0.90, 3, 0.92),
            "tagging": _result(0.95, 0.90, 4, 0.86),
        }
        for what, new in cases.items():
            with self.subTest(what):
                fell = evals.regressions(self.base, new)
                self.assertTrue(fell)
                self.assertTrue(all(what in line for line in fell), fell)

    def test_a_new_kind_is_not_a_regression(self):
        new = _result(0.95, 0.90, 4, 0.92)
        new["search"]["blended"]["kinds"]["voiceover"] = {"ndcg": 0.5}
        self.assertEqual(evals.regressions(self.base, new), [])


class TestHistory(unittest.TestCase):
    def test_it_ends_with_this_version(self):
        rows = evals.history()["rows"]
        self.assertTrue(rows, "no releases recorded")
        self.assertEqual(
            rows[-1]["version"],
            evals.version(),
            "run: uv run python scripts/evals.py --record (CONTRIBUTING.md)",
        )

    @unittest.skipUnless(HAS_VEC, "sqlite-vec not installed")
    def test_the_last_row_is_what_the_code_scores(self):
        last = evals.history()["rows"][-1]
        now = evals.score()
        for key in ("queries", "search", "tagging"):
            with self.subTest(key):
                self.assertEqual(
                    last[key], now[key], "run: uv run python scripts/evals.py --record"
                )


if __name__ == "__main__":
    unittest.main()
