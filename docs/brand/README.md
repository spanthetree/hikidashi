# Brand

The mark is a *hikidashi* (引き出し), a drawer, standing open with three cards
in it, the middle one vermilion: the saves you can pull out again.

| File | Use |
|---|---|
| `icon.svg` | The mark on its own, drawn by hand. It switches to its dark colours with `prefers-color-scheme`. The app's copy is `frontend/static/icon.svg`. |
| `icon-tile.svg` | The mark on an ink tile, for app icons. `deploy/unraid/icon.png` is rendered from it at 256 px. |
| `wordmark.svg`, `wordmark-dark.svg` | The mark and the name, for light and dark backgrounds. The README shows the one that suits the reader's theme. |
| `social-preview.svg`, `.png` | The 1280×640 card GitHub shows for a link to the repository (Settings → General → Social preview takes the PNG). |
| `frontend/static/favicon.ico` | 16, 32 and 48 px, for browsers and places that don't take an SVG icon. |
| `frontend/static/apple-touch-icon.png`, `icon-192.png`, `icon-512.png` | Home screens and app launchers. |

Everything but the two icons is drawn by `scripts/make_brand.py` from the
mark and two typefaces, and never edited by hand:

```bash
uv run --with fonttools --with resvg-py --with pillow \
    python scripts/make_brand.py --fonts DIR
```

`DIR` holds `ZenMaruGothic-Bold.ttf` and `ZenKakuGothicNew-Medium.ttf`, from
Google Fonts under the SIL Open Font License. The letters go into the files as
outlines, so the fonts are needed only to draw them, and aren't in the
repository. `tests/test_brand.py` checks the script's copy of the mark against
`icon.svg`.

There's no lettering in the mark itself. The name is set in Zen Maru Gothic Bold,
lower case, with 引き出し beside it in Zen Kaku Gothic New.

| Colour | Light | Dark |
|---|---|---|
| Ink | `#1c1b19` | |
| Kiri wood (cards) | `#b89a74` | `#d8c3a5` |
| Shu (the lit card) | `#d13a20` | `#e2462b` |
| Paper | `#f3ece0` | |
