import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "make_diagrams", ROOT / "scripts" / "make_diagrams.py"
)
assert spec and spec.loader
make_diagrams = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_diagrams)


class TestDiagrams(unittest.TestCase):
    def test_up_to_date(self):
        """The ER and stage diagrams follow app/schema.sql and app/jobs, and
        the animated SVGs their generator. Regenerate with
        `uv run python scripts/make_diagrams.py`."""
        stale = [str(p.relative_to(ROOT)) for p in make_diagrams.stale()]
        self.assertEqual(stale, [])

    def test_every_table_drawn(self):
        tables = make_diagrams.parse_schema((ROOT / "app" / "schema.sql").read_text())
        make_diagrams.check_groups(tables)  # raises SystemExit if one is missing

    def test_schema_read(self):
        tables = make_diagrams.parse_schema((ROOT / "app" / "schema.sql").read_text())
        self.assertEqual(tables["user_item"].pk, {"user_id", "shortcode"})
        self.assertIn(
            (("shortcode", "source", "user_id"), "recipe"), tables["recipe_ingredient"].fks
        )
        self.assertEqual(tables["item_fts"].content, "item")

    def test_stage_graph_edges(self):
        self.assertEqual(make_diagrams.direct("tag"), ["ocr"])
        self.assertEqual(make_diagrams.direct("embed"), ["tag"])
        self.assertEqual(make_diagrams.direct("enrich"), [])

    def test_svgs_honour_motion_and_theme(self):
        for name, make in make_diagrams.SVGS.items():
            with self.subTest(name):
                svg = make()
                self.assertIn("prefers-reduced-motion:reduce", svg)
                self.assertIn("prefers-color-scheme:dark", svg)
                self.assertIn('aria-label="', svg)
                self.assertNotIn("<image", svg)
                self.assertNotIn("<script", svg)
                # A drawing stays under 12 KB; the recorded terminal has a
                # keyframe set a line, so it is allowed a little more.
                limit = 20_000 if name.endswith("-terminal.svg") else 12_000
                self.assertLess(len(svg.encode()), limit)


if __name__ == "__main__":
    unittest.main()
