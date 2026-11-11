"""Properties that hold for any input, checked with Hypothesis (#39).

The other tests check the inputs someone thought of; these generate their own
and look for one that breaks a rule. Needs hypothesis, a dev-only dependency
(`uv sync --group test`); skipped without it, so the suite still runs bare.
"""

import importlib.util
import sqlite3
import unittest
from typing import ClassVar

from app.importer import shortcode
from app.importer.parser import fix_mojibake, parse_collections, parse_saved_posts
from app.search import fts_query, phrase_query, tokens
from tests.fixtures import must, post_record

if importlib.util.find_spec("hypothesis") is None:
    raise unittest.SkipTest("hypothesis not installed")

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# unittest makes a new TestCase for every run, which looks like "multiple
# executors" to Hypothesis; that only matters for a test keeping state between
# examples, and none here does. No deadline: CI machines are slow at times.
SETTINGS = settings(deadline=None, suppress_health_check=[HealthCheck.differing_executors])

CODE = st.from_regex(r"[A-Za-z0-9_-]{1,40}", fullmatch=True)
# JSON as the export might arrive after Meta reshapes it again.
JSON = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | st.text(max_size=20),
    lambda inner: (
        st.lists(inner, max_size=5)
        | st.dictionaries(
            st.sampled_from(
                [
                    "label",
                    "value",
                    "href",
                    "title",
                    "dict",
                    "label_values",
                    "timestamp",
                    "string_map_data",
                    "saved_saved_media",
                    "media",
                    "Saved on",
                ]
            )
            | st.text(max_size=8),
            inner,
            max_size=5,
        )
    ),
    max_leaves=30,
)


def _fts() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(body)")
    conn.execute("INSERT INTO t VALUES ('the quick brown fox'), ('紅葉スポット')")
    return conn


class TestSearchQueries(unittest.TestCase):
    conn: ClassVar[sqlite3.Connection]

    @classmethod
    def setUpClass(cls):
        cls.conn = _fts()

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def match(self, expr: str) -> None:
        self.conn.execute("SELECT rowid FROM t WHERE t MATCH ?", (expr,)).fetchall()

    @SETTINGS
    @given(st.text())
    def test_fts_query_is_always_valid_syntax(self, q):
        expr = fts_query(q)
        if not tokens(q):
            self.assertIsNone(expr)
            return
        self.match(must(expr))  # raises sqlite3.OperationalError on bad syntax

    @SETTINGS
    @given(st.text())
    def test_phrase_query_is_always_valid_syntax(self, q):
        expr = phrase_query(q)
        self.assertEqual(expr is None, len(tokens(q)) < 2)
        if expr is not None:
            self.match(expr)

    @SETTINGS
    @given(
        st.lists(
            st.sampled_from(
                [
                    '"',
                    "*",
                    "^",
                    ":",
                    "(",
                    ")",
                    "-",
                    "+",
                    "NEAR",
                    "AND",
                    "OR",
                    "NOT",
                    "body",
                    "fox",
                    " ",
                ]
            ),
            max_size=12,
        )
    )
    def test_operators_typed_in_are_words_not_syntax(self, parts):
        q = "".join(parts)
        if tokens(q):
            self.match(must(fts_query(q)))


class TestShortcodes(unittest.TestCase):
    @SETTINGS
    @given(CODE)
    def test_the_canonical_permalink_gives_the_code_back(self, code):
        self.assertEqual(shortcode.extract(shortcode.canonical_permalink(code)), code)

    @SETTINGS
    @given(
        CODE,
        st.sampled_from(["p", "reel", "reels", "tv"]),
        st.sampled_from(["https://www.", "https://", "http://", "www.", ""]),
        st.sampled_from(["", "en/", "en_GB/", "ja/"]),
        st.sampled_from(["", "/", "/?igsh=abc", "?utm_source=ig_web_copy_link"]),
    )
    def test_every_permalink_shape_gives_the_same_code(self, code, kind, scheme, locale, tail):
        url = f"{scheme}instagram.com/{locale}{kind}/{code}{tail}"
        self.assertEqual(shortcode.extract(url), code)

    @SETTINGS
    @given(st.text())
    def test_extract_never_raises(self, text):
        found = shortcode.extract(text)
        self.assertTrue(found is None or found in text)


class TestParser(unittest.TestCase):
    @settings(SETTINGS, max_examples=300)
    @given(JSON)
    def test_any_json_parses_without_raising(self, data):
        result = parse_saved_posts(data)
        for post in result.posts:
            self.assertTrue(post.shortcode)
        parse_collections(data)

    @SETTINGS
    @given(CODE, st.text(), st.lists(st.text(min_size=1, max_size=10), max_size=4))
    def test_a_real_shaped_record_keeps_its_caption(self, code, caption, hashtags):
        rec = post_record(
            f"https://www.instagram.com/reel/{code}/",
            timestamp=1,
            caption=caption,
            username="someone",
            hashtags=tuple(hashtags),
        )
        result = parse_saved_posts([rec])
        self.assertEqual([p.shortcode for p in result.posts], [code])
        self.assertEqual(result.posts[0].caption, fix_mojibake(caption) or None)

    @SETTINGS
    @given(st.text())
    def test_mojibake_of_any_text_is_repaired(self, text):
        garbled = text.encode("utf-8").decode("latin-1")
        self.assertEqual(fix_mojibake(garbled), text)


if __name__ == "__main__":
    unittest.main()
