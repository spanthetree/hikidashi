#!/usr/bin/env python3
"""Draw the brand files from the mark: the wordmark, the favicons and app
icons, and the social preview (#89). docs/brand/README.md says what each is.

    uv run --with fonttools --with resvg-py --with pillow \\
        python scripts/make_brand.py --fonts DIR

DIR holds the two typefaces the lettering is drawn from, both under the SIL
Open Font License from Google Fonts: ZenMaruGothic-Bold.ttf ("hikidashi")
and ZenKakuGothicNew-Medium.ttf (引き出し and the tagline). They are only
read: every letter is written into the SVGs as an outline, so nothing that
shows them needs the fonts, and the fonts are not in the repository.

The mark itself is hand-drawn in docs/brand/icon.svg; MARK below is the same
five shapes, and tests/test_brand.py checks the two agree. Change the mark in
both, then run this.
"""

from __future__ import annotations

import argparse
import io
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
BRAND = ROOT / "docs" / "brand"
STATIC = ROOT / "frontend" / "static"

TAGLINE = "A searchable, self-hosted library for your Instagram saves."

# The palette (docs/brand/README.md).
INK, PAPER = "#1c1b19", "#f3ece0"
LIGHT = {"card": "#b89a74", "lit": "#d13a20", "body": INK, "slot": "#8a7660", "text": INK}
DARK = {"card": "#d8c3a5", "lit": "#e2462b", "body": PAPER, "slot": "#a89a86", "text": PAPER}
MUTED = {"light": "#8a7660", "dark": "#a89a86"}
SVG = '<svg xmlns="http://www.w3.org/2000/svg"'

# The mark on a 64-unit square: two kiri cards and the lit one standing in an
# open drawer, and the drawer's pull. As in docs/brand/icon.svg.
MARK = (
    ("card", 13, 17, 10, 22, 1.6),
    ("card", 41, 19, 10, 20, 1.6),
    ("lit", 27, 5, 10, 34, 1.6),
    ("body", 6, 33, 52, 24, 4),
    ("slot", 25, 43.3, 14, 3.4, 1.7),
)


def mark(colours: dict[str, str]) -> str:
    return "".join(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{colours[part]}"/>'
        for part, x, y, w, h, r in MARK
    )


def tile(size: int, *, rounded: bool = True) -> str:
    """The mark on an ink tile, the app icon: docs/brand/icon-tile.svg."""
    rx = 14 if rounded else 0
    return (
        f'{SVG} width="{size}" height="{size}" viewBox="0 0 64 64">'
        f'<rect width="64" height="64" rx="{rx}" fill="{INK}"/>'
        f'<g transform="translate(9.6 9.6) scale(.7)">{mark(DARK)}</g></svg>'
    )


class Lettering:
    """Text set in one font, as outlines."""

    def __init__(self, path: Path) -> None:
        from fontTools.ttLib import TTFont

        self.font: Any = TTFont(path)
        self.glyphs = self.font.getGlyphSet()
        self.cmap = self.font.getBestCmap()
        self.upm = self.font["head"].unitsPerEm

    def path(
        self, text: str, size: float, x: float, baseline: float, tracking: float = 0
    ) -> tuple[str, float]:
        """The outline of `text` at `size` px, starting at x on the baseline,
        and the x it ends at. Tracking is in ems, added after each letter."""
        from fontTools.pens.svgPathPen import SVGPathPen
        from fontTools.pens.transformPen import TransformPen

        scale = size / self.upm
        pen = SVGPathPen(self.glyphs, ntos=lambda v: f"{v:.2f}".rstrip("0").rstrip("."))
        for ch in text:
            name = self.cmap[ord(ch)]
            glyph = self.glyphs[name]
            glyph.draw(TransformPen(pen, (scale, 0, 0, -scale, x, baseline)))
            x += glyph.width * scale + tracking * size
        return pen.getCommands(), x - tracking * size


def wordmark(word: Lettering, kana: Lettering, scheme: str) -> str:
    """The mark, then hikidashi, then 引き出し smaller and quieter."""
    colours = LIGHT if scheme == "light" else DARK
    d_word, end = word.path("hikidashi", 64, 92, 62)
    d_kana, end = kana.path("引き出し", 22, end + 18, 60, tracking=0.2)
    width = round(end + 4)
    return (
        f'{SVG} viewBox="0 0 {width} 80" width="{width}" height="80" '
        f'role="img" aria-label="Hikidashi">'
        f'<g transform="translate(0 4) scale(1.125)">{mark(colours)}</g>'
        f'<path d="{d_word}" fill="{colours["text"]}"/>'
        f'<path d="{d_kana}" fill="{MUTED[scheme]}"/></svg>\n'
    )


def social(word: Lettering, kana: Lettering) -> str:
    """The card GitHub shows for a link to the repository: 1280 by 640, with
    the important part inside the middle, which some sites crop to."""
    d_word, end = word.path("hikidashi", 120, 0, 0)
    d_kana, kana_end = kana.path("引き出し", 40, end + 34, -4, tracking=0.2)
    left = (1280 - (kana_end + 168)) / 2
    d_tag, tag_end = kana.path(TAGLINE, 36, 0, 0)
    tag_left = (1280 - tag_end) / 2
    return (
        f'{SVG} viewBox="0 0 1280 640" width="1280" height="640" '
        'role="img" aria-label="Hikidashi: ' + TAGLINE + '">'
        f'<rect width="1280" height="640" fill="{INK}"/>'
        f'<g transform="translate({left:.1f} 186) scale(2.25)">{mark(DARK)}</g>'
        f'<g transform="translate({left + 168:.1f} 318)"><path d="{d_word}" fill="{PAPER}"/>'
        f'<path d="{d_kana}" fill="{MUTED["dark"]}"/></g>'
        f'<g transform="translate({tag_left:.1f} 452)">'
        f'<path d="{d_tag}" fill="{DARK["card"]}"/></g>'
        "</svg>\n"
    )


def png(svg: str, size: int | None = None) -> bytes:
    import resvg_py

    if size is None:
        return bytes(resvg_py.svg_to_bytes(svg_string=svg))
    return bytes(resvg_py.svg_to_bytes(svg_string=svg, width=size, height=size))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--fonts", type=Path, required=True, help="the folder with the two .ttf files"
    )
    args = parser.parse_args()

    from PIL import Image

    word = Lettering(args.fonts / "ZenMaruGothic-Bold.ttf")
    kana = Lettering(args.fonts / "ZenKakuGothicNew-Medium.ttf")

    written: list[Path] = []

    def write(path: Path, data: str | bytes) -> None:
        if isinstance(data, str):
            path.write_text(data)
        else:
            path.write_bytes(data)
        written.append(path)

    write(BRAND / "wordmark.svg", wordmark(word, kana, "light"))
    write(BRAND / "wordmark-dark.svg", wordmark(word, kana, "dark"))
    card = social(word, kana)
    write(BRAND / "social-preview.svg", card)
    write(BRAND / "social-preview.png", png(card))

    # Browsers that take an SVG favicon use icon.svg; the rest, and bookmarks,
    # tabs and home screens, use these. The tile reads on any tab colour.
    frames = [Image.open(io.BytesIO(png(tile(n), n))) for n in (16, 32, 48)]
    ico = io.BytesIO()
    frames[-1].save(
        ico, format="ICO", sizes=[(16, 16), (32, 32), (48, 48)], append_images=frames[:-1]
    )
    write(STATIC / "favicon.ico", ico.getvalue())
    # iOS rounds the corners itself, and draws a square's own corners black.
    write(STATIC / "apple-touch-icon.png", png(tile(180, rounded=False), 180))
    write(STATIC / "icon-192.png", png(tile(192), 192))
    write(STATIC / "icon-512.png", png(tile(512), 512))

    for path in written:
        print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()
