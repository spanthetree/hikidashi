"""The brand files agree with the mark (#89). scripts/make_brand.py draws the
wordmark, favicons and social preview from its own copy of the mark; this
checks that copy against the hand-drawn docs/brand/icon.svg, and that the
app's icon is the same file."""

from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRAND = ROOT / "docs" / "brand"
spec = importlib.util.spec_from_file_location("make_brand", ROOT / "scripts" / "make_brand.py")
assert spec and spec.loader
make_brand = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_brand)
LIGHT, MARK = make_brand.LIGHT, make_brand.MARK


class TestMark(unittest.TestCase):
    def test_script_draws_the_mark_in_icon_svg(self):
        svg = (BRAND / "icon.svg").read_text()
        drawn = [
            (part, *map(float, (x, y, w, h, r)))
            for part, x, y, w, h, r in re.findall(
                r'<rect class="(\w+)" x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" '
                r'height="([\d.]+)" rx="([\d.]+)"/>',
                svg,
            )
        ]
        self.assertEqual(drawn, [(part, *map(float, rest)) for part, *rest in MARK])
        # The light colours, as the icon's own stylesheet has them.
        for part in ("card", "lit", "body", "slot"):
            with self.subTest(part):
                self.assertIn(f".{part} {{ fill: {LIGHT[part]} }}", svg)

    def test_app_icon_is_the_brand_icon(self):
        self.assertEqual(
            (ROOT / "frontend" / "static" / "icon.svg").read_bytes(),
            (BRAND / "icon.svg").read_bytes(),
        )

    def test_generated_files_are_there(self):
        for path in (
            BRAND / "wordmark.svg",
            BRAND / "wordmark-dark.svg",
            BRAND / "social-preview.svg",
            BRAND / "social-preview.png",
            ROOT / "frontend" / "static" / "favicon.ico",
            ROOT / "frontend" / "static" / "apple-touch-icon.png",
            ROOT / "frontend" / "static" / "icon-192.png",
            ROOT / "frontend" / "static" / "icon-512.png",
        ):
            with self.subTest(path.name):
                self.assertTrue(path.is_file(), "run scripts/make_brand.py")

    def test_lettering_is_outlined(self):
        # No <text>: the fonts are not in the repository, nor where it shows.
        for name in ("wordmark.svg", "wordmark-dark.svg", "social-preview.svg"):
            with self.subTest(name):
                self.assertNotIn("<text", (BRAND / name).read_text())


if __name__ == "__main__":
    unittest.main()
