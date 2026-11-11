#!/usr/bin/env python3
"""Regenerate the diagrams whose source of truth is code, and the animated SVGs.

    uv run python scripts/make_diagrams.py          write them
    uv run python scripts/make_diagrams.py --check  exit 1 if any is stale

Two kinds of output:

- Mermaid blocks inside the Markdown, between `<!-- make_diagrams: NAME -->`
  and `<!-- /make_diagrams -->`. The ER diagrams are read from app/schema.sql,
  the stage graph from the stage registry in app/jobs. Edit the code, not the
  block: the next run puts the block back.
- The animated SVGs in docs/ (docs/diagrams/ and docs/pipeline.svg). Each is a
  function below with its timeline written out, sharing one palette, the
  light and dark themes, and the reduced-motion rule, so they can be changed
  here rather than reverse-engineered from the markup. One is drawn from a
  recording rather than written out: the remote worker's terminal, from
  docs/demo/remote-worker.cast, which scripts/demo_library.py records.

The SVG rules, from #88: CSS animation only (GitHub shows an SVG in an <img>,
with no scripts); the base style of every animated element is the end state,
which is what reduced motion and a viewer that does not animate show; until
it is reached, everything that will appear is drawn faint, so the first frame
reads too; and no raster images, a few KB each.

Stdlib only. tests/test_diagrams.py runs the check, so CI fails on a diff.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
from collections.abc import Callable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.jobs import STAGES  # noqa: E402
from app.jobs.protocol import REMOTE  # noqa: E402
from app.jobs.queue import REGISTRY  # noqa: E402

DOCS = ROOT / "docs"
SCHEMA = ROOT / "app" / "schema.sql"

# --- Mermaid: the ER diagrams, from app/schema.sql ----------------------------

# Which diagram each table is drawn in. A table in none fails the run, so a
# new table cannot be left out of the docs by accident. A table another group
# refers to is drawn there too, with its key only.
GROUPS = {
    "er-reels": (
        "item",
        "item_slide",
        "item_tag",
        "tag",
        "recipe",
        "recipe_ingredient",
        "recipe_fts",
        "item_place",
        "geocode",
        "item_vec",
        "item_chunk_vec",
        "item_fts",
    ),
    "er-people": (
        "user",
        "user_item",
        "collection",
        "import_run",
        "shopping_recipe",
        "shopping_tick",
        "session",
        "invite",
        "api_token",
    ),
    "er-jobs": ("job_lease", "job_attempt", "job_worker"),
}

# Links the schema makes by value rather than by a foreign key.
# (parent, child, cardinality, label)
LOGICAL = (
    ("recipe", "recipe_fts", "||..o|", "indexed by app/recipes"),
    ("geocode", "item_place", "|o..o{", "text"),
    ("job_worker", "job_lease", "||..o{", "worker"),
)


class Table:
    def __init__(self, name: str, virtual: bool = False):
        self.name = name
        self.virtual = virtual
        self.columns: list[tuple[str, str, bool]] = []  # name, type, not null
        self.pk: set[str] = set()
        # (columns, parent table)
        self.fks: list[tuple[tuple[str, ...], str]] = []
        self.content: str | None = None  # an FTS5 table's content table

    def column(self, name: str) -> tuple[str, str, bool]:
        return next(c for c in self.columns if c[0] == name)


def _strip(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def parse_schema(sql: str) -> dict[str, Table]:
    sql = _strip(sql)
    tables: dict[str, Table] = {}
    for m in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", sql, re.S):
        t = tables[m[1]] = Table(m[1])
        for raw in m[2].split("\n"):
            line = raw.strip().rstrip(",")
            if not line:
                continue
            if pk := re.match(r"PRIMARY KEY \(([^)]*)\)", line):
                t.pk |= {c.strip() for c in pk[1].split(",")}
            elif fk := re.match(r"FOREIGN KEY \(([^)]*)\) REFERENCES (\w+)", line):
                t.fks.append((tuple(c.strip() for c in fk[1].split(",")), fk[2]))
            elif col := re.match(r"(\w+)\s+(\w+)(.*)", line):
                name, kind, rest = col[1], col[2].lower(), col[3]
                t.columns.append((name, kind, "NOT NULL" in rest or "PRIMARY KEY" in rest))
                if "PRIMARY KEY" in rest:
                    t.pk.add(name)
                if ref := re.search(r"REFERENCES (\w+)", rest):
                    t.fks.append(((name,), ref[1]))
            elif not line.startswith(("ON DELETE", "ON UPDATE")):
                raise ValueError(f"{t.name}: cannot read {line!r}")
    for m in re.finditer(
        r"CREATE VIRTUAL TABLE IF NOT EXISTS (\w+) USING fts5\((.*?)\);", sql, re.S
    ):
        t = tables[m[1]] = Table(m[1], virtual=True)
        for part in (p.strip() for p in m[2].split(",")):
            if c := re.match(r"content='(\w+)'", part):
                t.content = c[1]
            elif part and "=" not in part:
                t.columns.append((part.split()[0], "text", False))
    return tables


def entity(table: Table, full: bool) -> list[str]:
    """A table's attribute lines: all its columns, or only its keys when it
    is wide or drawn here only because another group refers to it."""
    fk = {c for cols, _ in table.fks for c in cols}
    keys = table.pk | fk
    wide = len(table.columns) > 8
    attrs = []
    for col, kind, _ in table.columns:
        if col not in (table.pk if not full else keys) and (not full or (keys and wide)):
            continue
        flags = ", ".join(f for f, on in (("PK", col in table.pk), ("FK", col in fk)) if on)
        attrs.append(f"    {kind} {col}" + (f" {flags}" if flags else ""))
    if full and len(table.columns) > len(attrs):
        more = len(table.columns) - len(attrs)
        note = f"and {more} more column{'s' * (more != 1)}"
        if attrs:
            attrs[0] += f' "{note}"'
        else:
            attrs.append(f'    text _ "{note}"')
    if table.virtual:
        attrs = attrs or ["    text _"]
        attrs[0] = attrs[0].split(' "')[0] + ' "FTS5 virtual table"'
    return attrs


def er(name: str, tables: dict[str, Table]) -> str:
    """One group's ER diagram, as a Mermaid block."""
    mine = GROUPS[name]
    rels: list[str] = []
    shown: dict[str, bool] = {t: True for t in mine}  # True: drawn in full here

    def touch(t: str) -> None:
        shown.setdefault(t, False)

    for t in mine:
        table = tables[t]
        for cols, parent in table.fks:
            touch(parent)
            required = all(table.column(c)[2] or c in table.pk for c in cols)
            card = "||--o{" if required else "|o--o{"
            if set(cols) == table.pk:  # its key is the parent's: one each
                card = "||--o|"
            rels.append(f'  {parent} {card} {t} : "{", ".join(cols)}"')
        if table.content:
            touch(table.content)
            rels.append(f'  {table.content} ||--|| {t} : "content (FTS5)"')
    for parent, child, card, label in LOGICAL:
        if child in mine or parent in mine:
            touch(parent)
            touch(child)
            rels.append(f'  {parent} {card} {child} : "{label}"')

    out = ["```mermaid", "erDiagram"]
    for t, full in shown.items():
        out.append(f"  {t} {{")
        out.extend(entity(tables[t], full))
        out.append("  }")
    out.extend(rels)
    out.append("```")
    return "\n".join(out)


def check_groups(tables: dict[str, Table]) -> None:
    grouped = {t for g in GROUPS.values() for t in g}
    missing = sorted(set(tables) - grouped)
    unknown = sorted(grouped - set(tables))
    if missing or unknown:
        raise SystemExit(
            f"scripts/make_diagrams.py GROUPS is out of step with app/schema.sql: "
            f"not drawn {missing}, not in the schema {unknown}"
        )


# --- Mermaid: the stage graph, from app/jobs ----------------------------------


def direct(stage: str) -> list[str]:
    """The stages `stage` waits on directly: its upstream, less what one of
    them already waits on (the registry lists them transitively)."""
    ups = REGISTRY[stage].upstream
    return [u for u in ups if not any(u in REGISTRY[v].upstream for v in ups)]


def stage_graph() -> str:
    def node(s: str) -> str:
        return f'    {s}["{s}<br/><small>{REGISTRY[s].stamp}</small>"]'

    server = [s for s in STAGES if s not in REMOTE]
    out = ["```mermaid", "flowchart LR"]
    out.append('  import(["import<br/><small>caption, FTS5</small>"])')
    out.append(
        '  subgraph server["server image · HIKIDASHI_LOCAL_STAGES=' + ",".join(server) + '"]'
    )
    out.extend(node(s) for s in server)
    out.append("  end")
    out.append('  subgraph worker["worker image, here or remote · ' + ",".join(REMOTE) + '"]')
    out.extend(node(s) for s in REMOTE)
    out.append("  end")
    first = [s for s in STAGES if not REGISTRY[s].upstream]
    out.extend(f"  import --> {s}" for s in first)
    for s in STAGES:
        out.extend(f"  {u} --> {s}" for u in direct(s))
        if REGISTRY[s].skippable:
            skip = REGISTRY[s].skippable
            # From the last stage it always waits on.
            always = [u for u in REGISTRY[s].upstream if u not in skip]
            label = "when no worker can run " + ", ".join(skip)
            out.append(f'  {always[-1]} -. "{label}" .-> {s}')
    out.append("```")
    return "\n".join(out)


def blocks() -> dict[str, str]:
    tables = parse_schema(SCHEMA.read_text())
    check_groups(tables)
    out = {name: er(name, tables) for name in GROUPS}
    out["stage-graph"] = stage_graph()
    return out


MARK = re.compile(
    r"(<!-- make_diagrams: ([\w-]+) -->\n)(.*?)(<!-- /make_diagrams -->)",
    re.S,
)


def fill(text: str, made: dict[str, str]) -> str:
    def sub(m: re.Match[str]) -> str:
        if m[2] not in made:
            raise SystemExit(f"no diagram called {m[2]!r}; there are {', '.join(made)}")
        return f"{m[1]}{made[m[2]]}\n{m[4]}"

    return MARK.sub(sub, text)


# --- SVG: the shared frame ----------------------------------------------------

# GitHub's own greys in light, pipeline.svg's in dark; one accent each for a
# reel in hand, a failure, a success, a second worker, and a warning.
LIGHT = dict(
    bg="#f6f8fa",
    panel="#ffffff",
    line="#d0d7de",
    text="#1f2328",
    sub="#59636e",
    acc="#8250df",
    accbg="#fbefff",
    bad="#cf222e",
    badbg="#ffebe9",
    good="#1a7f37",
    goodbg="#dafbe1",
    blue="#0969da",
    bluebg="#ddf4ff",
    warn="#9a6700",
)
DARK = dict(
    bg="#101220",
    panel="#191c2b",
    line="#333852",
    text="#e8e9f2",
    sub="#8d92a8",
    acc="#a59bff",
    accbg="#242244",
    bad="#f1877b",
    badbg="#301b19",
    good="#5fcc9f",
    goodbg="#16302a",
    blue="#6cb6ff",
    bluebg="#172a45",
    warn="#e3b341",
)

BASE_CSS = """
text{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;fill:var(--text);font-size:11px}}
.bg{{fill:var(--bg)}}.box{{fill:var(--panel);stroke:var(--line)}}
.l{{font-size:12px;font-weight:600}}.s{{font-size:9.5px;fill:var(--sub)}}
.c{{font-size:10px;fill:var(--sub)}}.m{{text-anchor:middle}}.e{{text-anchor:end}}
.ln{{stroke:var(--line);stroke-width:1.4;fill:none}}.ah{{fill:var(--line)}}
.tok{{fill:var(--acc)}}
:root{{{light}}}
@media (prefers-color-scheme:dark){{:root{{{dark}}}}}
@media (prefers-reduced-motion:reduce){{*{{animation:none!important}}}}
"""

ARROW = (
    '<defs><marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
    'markerHeight="7" orient="auto-start-reverse"><path class="ah" d="M0,0L10,5L0,10z"/>'
    "</marker></defs>"
)


def _vars(p: dict[str, str]) -> str:
    return ";".join(f"--{k}:{v}" for k, v in p.items())


def svg(w: int, h: int, alt: str, css: str, body: str) -> str:
    style = BASE_CSS.format(light=_vars(LIGHT), dark=_vars(DARK)).strip() + "\n" + css.strip()
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" '
        f'height="{h}" role="img" aria-label="{alt}">\n<title>{alt}</title>\n'
        f"<style>\n{style}\n</style>\n{ARROW}\n"
        f'<rect class="bg" width="{w}" height="{h}" rx="8"/>\n{body.strip()}\n</svg>\n'
    )


def _pct(p: float) -> str:
    return f"{p:g}%"


def keyframes(name: str, stops: Sequence[tuple[float, str]]) -> str:
    """@keyframes from (percent, declarations), merging equal percents."""
    merged: dict[str, list[str]] = {}
    for p, decl in sorted(stops, key=lambda s: s[0]):
        merged.setdefault(_pct(p), []).append(decl)
    body = "".join(f"{p}{{{';'.join(d)}}}" for p, d in merged.items())
    return f"@keyframes {name}{{{body}}}"


def anim(
    cls: str, name: str, period: float, stops: Sequence[tuple[float, str]], base: str = ""
) -> str:
    """A class running one keyframe set over the cycle, with `base` as its
    still (end) state."""
    rule = f".{cls}{{{base + ';' if base else ''}animation:{name} {period:g}s infinite}}"
    return rule + keyframes(name, stops)


def at(x: float, y: float) -> str:
    return f"transform:translate({x:g}px,{y:g}px)"


FAINT = 0.22  # what is still to appear, in the first frame


def appear(cls: str, period: float, on: float, *, off: float = 97) -> str:
    """Faint until `on`, full until `off`, faint again for the next cycle."""
    return anim(
        cls,
        cls,
        period,
        [
            (0, f"opacity:{FAINT}"),
            (max(0, on - 2), f"opacity:{FAINT}"),
            (on, "opacity:1"),
            (off, "opacity:1"),
            (min(100, off + 2), f"opacity:{FAINT}"),
            (100, f"opacity:{FAINT}"),
        ],
    )


def lit(cls: str, period: float, windows: Sequence[tuple[float, float, str]]) -> str:
    """A box that changes colour inside each (start, end, colour) window:
    colour is acc, bad, good, blue or warn. Windows a point or less apart
    run into each other with no plain frame between."""
    plain = "fill:var(--panel);stroke:var(--line)"
    ws = sorted(windows)
    stops: list[tuple[float, str]] = [] if ws[0][0] == 0 else [(0, plain)]
    done = 0.0
    for i, (a, b, colour) in enumerate(ws):
        on = f"fill:var(--{colour}bg,var(--panel));stroke:var(--{colour})"
        if a - 1 > done:
            stops.append((a - 1, plain))
        stops += [(a, on), (b, on)]
        following = ws[i + 1][0] if i + 1 < len(ws) else 101
        if b + 1 < following:
            stops.append((min(100, b + 1), plain))
        done = b + 1
    if done < 100:
        stops.append((100, plain))
    return anim(cls, cls, period, stops)


def box(x: float, y: float, w: float, h: float, cls: str = "box") -> str:
    return f'<rect class="{cls}" x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}" rx="5"/>'


def text(x: float, y: float, s: str, cls: str = "") -> str:
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f'<text class="{cls}" x="{x:g}" y="{y:g}">{s}</text>'


def line(x1: float, y1: float, x2: float, y2: float, cls: str = "ln", head: bool = True) -> str:
    m = ' marker-end="url(#a)"' if head else ""
    return f'<path class="{cls}" d="M{x1:g},{y1:g}L{x2:g},{y2:g}"{m}/>'


def captions(y: float, paragraph: str, width: int = 108) -> str:
    """The caption under a diagram, wrapped to lines of `width` characters."""
    lines = textwrap.wrap(paragraph, width)
    return "".join(text(20, y + 14 * i, s, "c") for i, s in enumerate(lines))


# --- SVG 1: a reel through the pipeline ----------------------------------------


def pipeline() -> str:
    T = 14
    W, H = 900, 290
    bw, bh = 104, 56
    y = 64
    col = {
        "import": (16, y),
        "enrich": (136, y),
        "asr": (256, y),
        "ocr": (376, y),
        "tag": (496, y),
        "recipe": (636, 22),
        "embed": (636, 106),
        "done": (776, y),
    }
    subs = {
        "import": ("imported", "caption, FTS5", "server"),
        "enrich": ("enrich", "yt-dlp: video", "server"),
        "asr": ("asr", "faster-whisper", "worker"),
        "ocr": ("ocr", "tesseract", "worker"),
        "tag": ("tag", "Ollama: summary", "worker"),
        "recipe": ("recipe", "Ollama, if food", "worker"),
        "embed": ("embed", "embeddinggemma", "server"),
        "done": ("searchable", "words + meaning", ""),
    }

    def centre(k: str) -> tuple[float, float]:
        x, yy = col[k]
        return x + bw / 2, yy + bh

    # Where the reel is, and when (percent of the cycle).
    path = [
        (0, "import"),
        (6, "import"),
        (10, "enrich"),
        (16, "enrich"),
        (20, "asr"),
        (24, "asr"),
        (29, "retry"),
        (39, "retry"),
        (43, "asr"),
        (48, "asr"),
        (52, "ocr"),
        (56, "ocr"),
        (60, "tag"),
        (65, "tag"),
        (70, "embed"),
        (76, "embed"),
        (81, "done"),
    ]
    retry = (316, 176)
    pos = {**{k: centre(k) for k in col}, "retry": retry}
    stops = [(p, at(*pos[k]) + ";opacity:1") for p, k in path]
    stops += [
        (98, at(*pos["done"]) + ";opacity:1"),
        (99, at(*pos["done"]) + ";opacity:0"),
        (100, at(*pos["import"]) + ";opacity:0"),
    ]
    css = [anim("t1", "t1", T, stops, at(*pos["done"]))]
    # The recipe pass runs beside the embedder, both after the tagger.
    ghost = [
        (0, at(*pos["tag"]) + ";opacity:0"),
        (64, at(*pos["tag"]) + ";opacity:0"),
        (65, at(*pos["tag"]) + ";opacity:.7"),
        (70, at(*pos["recipe"]) + ";opacity:.7"),
        (76, at(*pos["recipe"]) + ";opacity:.7"),
        (81, at(*pos["done"]) + ";opacity:.7"),
        (83, at(*pos["done"]) + ";opacity:0"),
        (100, at(*pos["done"]) + ";opacity:0"),
    ]
    css.append(anim("t2", "t2", T, ghost, at(*pos["done"]) + ";opacity:0"))

    windows: dict[str, list[tuple[float, float, str]]] = {
        "import": [(0, 6, "acc")],
        "enrich": [(10, 16, "acc")],
        "asr": [(20, 24, "acc"), (24.5, 29, "bad"), (43, 48, "good")],
        "ocr": [(52, 56, "acc")],
        "tag": [(60, 65, "acc")],
        "recipe": [(70, 76, "acc")],
        "embed": [(70, 76, "acc")],
        "done": [(81, 97, "acc")],
    }
    body = []
    for k, (x, yy) in col.items():
        css.append(lit(f"b-{k}", T, windows[k]))
        body.append(box(x, yy, bw, bh, f"box b-{k}"))
        label, sub, image = subs[k]
        cx = x + bw / 2
        body.append(text(cx, yy + 20, label, "l m"))
        body.append(text(cx, yy + 34, sub, "s m"))
        if image:
            body.append(text(cx, yy + 47, image + " image", "s m"))

    def edge(a: str, b: str) -> str:
        ax, ay = col[a]
        bx, by = col[b]
        return line(ax + bw, ay + bh / 2, bx - 2, by + bh / 2)

    for a, b in [
        ("import", "enrich"),
        ("enrich", "asr"),
        ("asr", "ocr"),
        ("ocr", "tag"),
        ("tag", "recipe"),
        ("tag", "embed"),
        ("recipe", "done"),
        ("embed", "done"),
    ]:
        body.append(edge(a, b))

    # The attempts box under ASR.
    css.append(lit("b-retry", T, [(26, 41, "bad")]))
    rx, ry, rw, rh = 196, 176, 240, 58
    body.append(box(rx, ry, rw, rh, "box b-retry"))
    body.append(text(rx + rw / 2, ry + 18, "job_attempt: 1 of 3", "l m"))
    body.append(text(rx + rw / 2, ry + 33, "not offered again for 60 s,", "s m"))
    body.append(text(rx + rw / 2, ry + 46, "then 120 s; at 3, failed at asr", "s m"))
    css.append(
        ".fail{stroke:var(--bad);stroke-dasharray:4 4}"
        ".back{stroke:var(--good);stroke-dasharray:5 4}"
    )
    body.append(line(296, y + bh, 296, ry - 2, "ln fail"))
    body.append(line(336, ry, 336, y + bh + 2, "ln back"))
    body.append(text(290, 150, "fails", "s e"))
    body.append(text(342, 150, "claimed again", "s"))

    body.append('<circle class="tok t2" r="6"/>')
    body.append('<circle class="tok t1" r="7"/>')
    body.append(
        captions(
            252,
            (
                "Each stage claims a reel only once the stages before it are done with "
                "it. A failure is counted, and the reel waits 60 s, then 120 s; at the "
                "third it is failed at that stage and later stages go on without it."
            ),
        )
    )
    alt = (
        "A reel moving through the pipeline: imported, enrich, asr, ocr and tag, then recipe and "
        "embed side by side, then searchable. At asr it fails once, is counted in job_attempt as "
        "attempt 1 of 3, waits out its backoff and is claimed again, and this time succeeds."
    )
    return svg(W, H, alt, "\n".join(css), "\n".join(body))


# --- SVG 2: leases ------------------------------------------------------------


def leases() -> str:
    T = 16
    W, H = 900, 300
    body = []
    css = [
        ".chip{fill:var(--panel);stroke:var(--line)}",
        ".dead{stroke:var(--line);stroke-dasharray:4 3}",
        ".bar{fill:var(--acc)}",
    ]
    # Worker A, the pool, worker B.
    body.append(box(20, 60, 240, 150, "box b-a"))
    body.append(text(140, 82, "worker A · desktop", "l m"))
    body.append(text(140, 97, "asr, renews every 30 s", "s m"))
    body.append(box(330, 60, 240, 150))
    body.append(text(450, 82, "ready for asr", "l m"))
    body.append(text(450, 97, "the stage's pending query", "s m"))
    body.append(box(640, 60, 240, 150, "box b-b"))
    body.append(text(760, 82, "worker B · always on", "l m"))
    body.append(text(760, 97, "asr, renews every 30 s", "s m"))
    css.append(lit("b-a", T, [(6, 34, "acc"), (35, 97, "bad")]))
    css.append(lit("b-b", T, [(10, 97, "blue")]))

    # A's state, which turns from running to lost.
    css.append(appear("lostA", T, 36))
    body.append(text(140, 200, "lost: no check-in for 2 min", "s m lostA"))

    # A's lease, full while renewed, then draining to its expiry.
    css.append(
        anim(
            "lease",
            "lease",
            T,
            [
                (0, "transform:scaleX(1)"),
                (36, "transform:scaleX(1)"),
                (60, "transform:scaleX(0)"),
                (97, "transform:scaleX(0)"),
                (99, "transform:scaleX(1)"),
                (100, "transform:scaleX(1)"),
            ],
            "transform:scaleX(0)",
        )
    )
    css.append(".lease{transform-box:fill-box;transform-origin:left}")
    body.append(text(40, 178, "lease, 5 min", "s"))
    body.append('<rect x="130" y="170" width="110" height="9" rx="2" class="box"/>')
    body.append('<rect x="130" y="170" width="110" height="9" rx="2" class="bar lease"/>')

    # The heartbeat: A's stops when it goes; B's keeps on.
    beats = []
    for p in (12, 18, 24, 30):
        beats += [(p - 1, "opacity:.15"), (p, "opacity:1"), (p + 2, "opacity:.15")]
    css.append(
        anim("hbA", "hbA", T, [(0, "opacity:.15"), *beats, (100, "opacity:.15")], "opacity:.15")
    )
    css.append(
        ".hbB{animation:hbB 2s infinite}"
        "@keyframes hbB{0%,100%{opacity:.15}10%{opacity:1}30%{opacity:.15}}"
    )
    css.append(".hb{fill:var(--good)}")
    body.append('<circle class="hb hbA" cx="244" cy="72" r="4"/>')
    body.append('<circle class="hb hbB" cx="864" cy="72" r="4"/>')

    pool = [(350, 112), (420, 112), (490, 112), (350, 150), (420, 150), (490, 150)]
    in_a = [(40, 120), (110, 120), (180, 120)]
    b_top = [(660, 112), (730, 112), (800, 112)]
    b_low = [(660, 150), (730, 150), (800, 150)]
    for i in range(6):
        n = f"r{i + 1}"
        if i < 3:
            route = [
                (0, pool[i]),
                (3, pool[i]),
                (6, in_a[i]),
                (60, in_a[i]),
                (63, pool[i]),
                (68, pool[i]),
                (72, b_low[i]),
                (100, b_low[i]),
            ]
            end = b_low[i]
        else:
            route = [(0, pool[i]), (7, pool[i]), (10, b_top[i - 3]), (100, b_top[i - 3])]
            end = b_top[i - 3]
        stops = [(p, at(*xy)) for p, xy in route]
        css.append(anim(n, n, T, stops, at(*end)))
        body.append(
            f'<g class="{n}"><rect class="chip k{"AB"[i >= 3]}" width="60" height="26" rx="4"/>'
        )
        plus = text(56, 11, "+1", "plus") if i < 3 else ""
        body.append(text(30, 17, n, "m") + plus + "</g>")
    # Chips: in hand, then done. A's chips also go back with an attempt counted.
    css.append(
        lit("kA", T, [(6, 60, "acc"), (60.5, 71.5, "warn"), (72, 85.5, "blue"), (86, 97, "good")])
    )
    css.append(lit("kB", T, [(10, 29.5, "blue"), (30, 97, "good")]))
    css.append(
        ".kA,.kB{fill:var(--goodbg);stroke:var(--good)}"
        ".plus{fill:var(--warn);font-size:9px;font-weight:600;text-anchor:end}"
    )
    css.append(appear("plus", T, 62))

    css.append(appear("note1", T, 62))
    body.append(text(450, 196, "expired: back, attempt counted", "s m note1"))
    body.append(
        captions(
            240,
            (
                "A worker claims a few reels for one stage and holds them on a 5-minute "
                "lease, which each 30 s check-in renews. Worker A goes away mid-batch: "
                "its lease runs out, its reels go back with one attempt counted, and "
                "worker B claims them. A worker stopped cleanly gives its reels back at "
                "once, uncounted."
            ),
        )
    )
    alt = (
        "Leases: six reels ready for asr. Worker A claims three and worker B the other three. "
        "Worker A stops checking in; its lease drains and expires, its three reels return to the "
        "pool with one attempt counted, and worker B claims and finishes them."
    )
    return svg(W, H, alt, "\n".join(css), "\n".join(body))


# --- SVG 3: the remote worker's round trip ------------------------------------


def remote() -> str:
    T = 15
    W, H = 900, 540
    sx, dx = 150, 750
    body = [
        box(40, 16, 220, 48),
        text(sx, 37, "server · always on", "l m"),
        text(sx, 52, "SQLite, media, LocalJobs", "s m"),
        box(640, 16, 220, 48),
        text(dx, 37, "worker · GPU desktop", "l m"),
        text(dx, 52, "HttpJobs, media cache", "s m"),
        line(sx, 64, sx, 474, "ln life", head=False),
        line(dx, 64, dx, 474, "ln life", head=False),
    ]
    css = [".life{stroke-dasharray:3 4}.pk{fill:var(--acc)}.hbt{fill:var(--good)}"]
    # (kind, text, sub): kind is "to" (worker to server), "back", "server" or "worker".
    rows = [
        ("to", "POST /api/worker/claim", "stage asr, limit 4, protocol, stage versions"),
        ("server", "leases 4 reels", "readiness, attempts cap: as locally"),
        ("back", "the rows", "shortcode, media_path: what asr reads"),
        ("to", "GET /api/worker/media/<path>", "only what the cache lacks"),
        ("back", "the video, into the cache", "kept; least recently used go past 2 GB"),
        ("worker", "faster-whisper on the GPU", "transcribe_one, per reel"),
        ("to", "POST /api/worker/results", "one per reel: transcript, or why it failed"),
        ("server", "checks, then records", "versions match? shape right? lease held?"),
        ("back", "done · again · failed", "asr.record(), then the leases released"),
    ]
    y0, step = 96, 42
    span = 84 / len(rows)
    for i, (kind, label, sub) in enumerate(rows):
        y = y0 + step * i
        on = 4 + span * i
        cls = f"r{i}"
        css.append(appear(cls, T, on + 2))
        if kind in ("to", "back"):
            a, b = (dx, sx) if kind == "to" else (sx, dx)
            body.append(f'<g class="{cls}">' + line(a, y, b + (4 if kind == "to" else -4), y))
            body.append(text(450, y - 12, label, "l m") + text(450, y + 12, sub, "s m") + "</g>")
            # A packet along the arrow while this row is current.
            stops = [
                (0, at(a, y) + ";opacity:0"),
                (on - 0.5, at(a, y) + ";opacity:0"),
                (on, at(a, y) + ";opacity:1"),
                (on + span - 1, at(b, y) + ";opacity:1"),
                (on + span - 0.5, at(b, y) + ";opacity:0"),
                (100, at(b, y) + ";opacity:0"),
            ]
            css.append(anim(f"p{i}", f"p{i}", T, stops, "opacity:0"))
            body.append(f'<circle class="pk p{i}" r="5"/>')
        else:
            x = sx if kind == "server" else dx
            css.append(lit(f"n{i}", T, [(on, on + span, "acc")]))
            body.append(f'<g class="{cls}">' + box(x - 130, y - 16, 260, 32, f"box n{i}"))
            body.append(text(x, y - 2, label, "l m") + text(x, y + 11, sub, "s m") + "</g>")

    # The heartbeat, all along.
    hy = 474
    body.append(line(dx, hy, sx + 4, hy, "ln life"))
    body.append(
        text(450, hy - 8, "POST /api/worker/check-in every 30 s: alive, and keep my leases", "s m")
    )
    css.append(
        ".hbt{animation:hbt 3s linear infinite}"
        f"@keyframes hbt{{0%{{{at(dx, hy)};opacity:0}}5%{{opacity:1}}"
        f"60%{{{at(sx, hy)};opacity:1}}65%,100%{{{at(sx, hy)};opacity:0}}}}"
    )
    body.append('<circle class="hbt" r="3.5"/>')
    body.append(
        captions(
            500,
            (
                "The server stays the only writer: it records each result with the "
                "stage's own record function. A worker on another release, or a "
                "protocol it does not speak, is refused with a 409 and stops."
            ),
        )
    )
    alt = (
        "A remote worker's round trip, as a sequence: the desktop claims asr work, the server "
        "leases four reels and sends their rows, the desktop downloads the videos it lacks, "
        "transcribes them on its GPU, posts one result per reel, and the server checks the "
        "versions, the shape and the lease, records them and releases the leases. A check-in "
        "every 30 seconds keeps the leases alive."
    )
    return svg(W, H, alt, "\n".join(css), "\n".join(body))


# --- SVG 4: search fusion -----------------------------------------------------


def search() -> str:
    T = 14
    W, H = 900, 330
    cw, ch = 260, 24
    cols = {"kw": 20, "sem": 310, "fused": 620}
    slot = [118, 148, 178, 208]
    body = [
        box(20, 16, 260, 34),
        text(32, 37, "search: potato soup", "l"),
        text(300, 37, "one person's library, filtered inside each list", "s"),
    ]
    heads = {
        "kw": ("keyword · FTS5 bm25", "every word, best first"),
        "sem": ("semantic · cosine", "summary ≥ 0.27, voiceover chunk ≥ 0.5"),
        "fused": ("fused · Σ w / (60 + rank)", "then exact phrase first"),
    }
    for k, (h1, h2) in heads.items():
        x = cols[k]
        body.append(text(x, 82, h1, "l") + text(x, 97, h2, "s"))
    css = [
        ".row{fill:var(--panel);stroke:var(--line)}",
        ".pin{fill:var(--acc);font-size:9px;font-weight:600}",
        ".dim{opacity:.6}",
    ]
    kw = ["potato and leek soup", "soup with roast potato", "best potato soup"]
    sem = ["warm broth for a cold night", "potato and leek soup", "soup with roast potato"]
    for col, items, start in (("kw", kw, 10), ("sem", sem, 22)):
        for i, s in enumerate(items):
            cls = f"{col}{i}"
            css.append(appear(cls, T, start + 3 * i))
            x, y = cols[col], slot[i]
            body.append(f'<g class="{cls}">' + box(x, y, cw, ch, "row"))
            body.append(text(x + 10, y + 16, f"{i + 1}  {s}") + "</g>")
    body.append(text(20, 226, "content words, × 0.5: only when no", "s dim"))
    body.append(text(20, 239, "reel has every word (none here)", "s dim"))

    # (title, score, slot before the pin, slot after)
    fused = [
        ("potato and leek soup", ".0325", 0, 1),
        ("soup with roast potato", ".0320", 1, 2),
        ("warm broth for a cold…", ".0164", 2, 3),
        ("best potato soup", ".0159", 3, 0),
    ]
    x = cols["fused"]
    for i, (s, score, before, after) in enumerate(fused):
        cls = f"f{i}"
        on = 38 + 3 * i
        y0, y1 = slot[before] - slot[0], slot[after] - slot[0]
        stops = [
            (0, at(0, y0) + f";opacity:{FAINT}"),
            (on - 2, at(-24, y0) + f";opacity:{FAINT}"),
            (on, at(0, y0) + ";opacity:1"),
            (60, at(0, y0) + ";opacity:1"),
            (66, at(0, y1) + ";opacity:1"),
            (97, at(0, y1) + ";opacity:1"),
            (99, at(0, y0) + f";opacity:{FAINT}"),
            (100, at(0, y0) + f";opacity:{FAINT}"),
        ]
        css.append(anim(cls, cls, T, stops, at(0, y1)))
        body.append(f'<g class="{cls}">' + box(x, slot[0], cw, ch, "row"))
        body.append(text(x + 10, slot[0] + 16, s) + text(x + cw - 8, slot[0] + 16, score, "s e"))
        if after == 0:
            body.append(text(x + cw - 50, slot[0] + 16, "phrase", "pin e"))
        body.append("</g>")
    css.append(appear("pin", T, 64))
    body.append(
        captions(
            262,
            (
                "Each list ranks one way; fusion adds weight / (60 + rank) for every "
                "list a reel is on, so bm25 and cosine never share a scale, and a reel "
                "two lists found beats one only one did. A reel with the query as an "
                "exact phrase is then pinned first. There is no reranker: "
                "cross-encoders were measured, and rejected (docs/search.md)."
            ),
        )
    )
    alt = (
        "Search fusion for the query potato soup: a keyword list and a semantic list fill in, "
        "their reels merge into one list by reciprocal rank fusion, and the one reel containing "
        "the exact phrase potato soup moves from last to first."
    )
    return svg(W, H, alt, "\n".join(css), "\n".join(body))


# --- SVG 5: shared cache, separate libraries ----------------------------------


def shared() -> str:
    T = 16
    W, H = 900, 330
    body = [
        box(20, 60, 200, 70, "box u1"),
        text(120, 84, "user 1 imports", "l m"),
        text(120, 100, "export: a b c", "s m"),
        box(20, 170, 200, 70, "box u2"),
        text(120, 194, "user 2 imports", "l m"),
        text(120, 210, "export: b c d", "s m"),
        text(300, 40, "item · shared cache", "l"),
        text(300, 54, "one row per shortcode", "s"),
        text(620, 40, "user_item · one library each", "l"),
        text(620, 54, "every read goes through here", "s"),
        text(300, 226, "data/media · one download each", "l"),
    ]
    css = [
        ".row{fill:var(--panel);stroke:var(--line)}",
        ".file{fill:var(--accbg);stroke:var(--acc)}",
        ".have{font-size:9px;fill:var(--warn);font-weight:600}",
    ]
    css.append(lit("u1", T, [(5, 32, "acc")]))
    css.append(lit("u2", T, [(40, 70, "blue")]))
    # item rows, and when each appears; b and c are found, not made, by user 2.
    items = {"a": 12, "b": 15, "c": 18, "d": 52}
    for i, (sc, on) in enumerate(items.items()):
        y = 66 + 30 * i
        css.append(appear(f"i{sc}", T, on))
        body.append(
            f'<g class="i{sc}">' + box(300, y, 200, 24, "row") + text(312, y + 16, sc) + "</g>"
        )
    for i, (sc, on) in enumerate((("b", 45), ("c", 48))):
        css.append(appear(f"h{sc}", T, on))
        body.append(text(490, 66 + 30 * (i + 1) + 16, "already here", f"have e h{sc}"))
    saves = [
        ("1", "a", 14),
        ("1", "b", 17),
        ("1", "c", 20),
        ("2", "b", 46),
        ("2", "c", 49),
        ("2", "d", 53),
    ]
    for i, (u, sc, on) in enumerate(saves):
        y = 66 + 26 * i
        css.append(appear(f"s{u}{sc}", T, on))
        body.append(f'<g class="s{u}{sc}">' + box(620, y, 260, 22, "row"))
        body.append(text(632, y + 15, f"user {u} · {sc}") + "</g>")
    for i, (sc, on) in enumerate((("a", 24), ("b", 26), ("c", 28), ("d", 60))):
        css.append(appear(f"m{sc}", T, on))
        body.append(f'<g class="m{sc}">' + box(300 + 52 * i, 236, 44, 26, "file"))
        body.append(text(322 + 52 * i, 253, f"{sc}.mp4", "s m") + "</g>")
    css.append(appear("tot", T, 64))
    body.append(text(620, 248, "4 items, 4 downloads, 6 saves", "l tot"))
    body.append(text(620, 264, "user 2 asks for a: 404, as if it never existed", "s tot"))
    body.append(
        captions(
            290,
            (
                "A reel and what is made from it (media, transcript, tag, vectors) is "
                "fetched and computed once, whoever saved it. The save is each person's "
                "own: a user_item row with their save time, tag and collections."
            ),
        )
    )
    alt = (
        "Shared cache, separate libraries: user 1 imports reels a, b and c, making three item "
        "rows, "
        "three downloads and three user_item rows. User 2 imports b, c and d: b and c are already "
        "there, so only d is added and downloaded, and user 2 gets three user_item rows of their "
        "own."
    )
    return svg(W, H, alt, "\n".join(css), "\n".join(body))


# --- SVG 6: a remote worker's terminal, from a recording -------------------------

CAST = DOCS / "demo" / "remote-worker.cast"
LOG_LINE = re.compile(r"^\d{4}-\d\d-\d\d (\d\d:\d\d:\d\d),\d+ (\w+)\s+\S+\s+(.*)$")
STAGE_COLOUR = {"asr": "acc", "ocr": "blue", "tag": "good", "recipe": "warn", "worker": "sub"}


def _cast_lines() -> list[tuple[float, str]]:
    """(seconds, line) from the asciicast, one event a line."""
    rows = CAST.read_text().splitlines()[1:]
    return [(float(t), s.rstrip("\r\n")) for t, _, s in (json.loads(r) for r in rows if r)]


def terminal() -> str:
    """docs/demo/remote-worker.cast, as the terminal it was recorded in.

    Lines print at their recorded times, with any pause longer than GAP cut
    to GAP, and the window scrolls as a terminal does. Unlike the drawings,
    nothing is faint before it appears: an unprinted line is not there. With
    motion reduced, the window shows the run's first ROWS lines, all printed.
    """
    ROWS, GAP, LH, W = 18, 1.4, 17.0, 900
    TOP = 44  # below the title bar
    lines = _cast_lines()
    when: list[float] = []
    last_real, last = 0.0, 0.0
    for t, _ in lines:
        last += min(max(t - last_real, 0.25), GAP) if when else 0.0
        last_real = t
        when.append(last)
    T = round(last + 4, 1)
    H = int(TOP + ROWS * LH + 22)

    def pct(t: float) -> float:
        return round(100 * t / T, 2)

    body = [
        box(0.5, 0.5, W - 1, H - 1, "term"),
        '<path class="bar" d="M8.5,0.5H891.5a8,8 0 0 1 8,8V30.5H0.5V8.5a8,8 0 0 1 8,-8z"/>',
        *(f'<circle class="dot" cx="{x}" cy="15.5" r="5"/>' for x in (20, 38, 56)),
        text(W / 2, 20, "desktop: hikidashi worker --server, on the demo library", "c m"),
        f'<clipPath id="win"><rect x="0" y="{TOP - 12}" width="{W}" height="{ROWS * LH}"/>'
        "</clipPath>",
        '<g clip-path="url(#win)"><g class="scroll">',
    ]
    css = [
        ".term{fill:var(--panel);stroke:var(--line)}.bar{fill:var(--bg);stroke:var(--line)}",
        ".dot{fill:var(--line)}.t{font-size:11.5px;white-space:pre}.ts{fill:var(--sub)}.w{fill:var(--warn)}",
        ".p{fill:var(--good);font-weight:600}",
        *(f".k-{k}{{fill:var(--{v});font-weight:600}}" for k, v in STAGE_COLOUR.items()),
    ]
    for i, ((_, raw), t) in enumerate(zip(lines, when, strict=True)):
        y = TOP + i * LH
        m = LOG_LINE.match(raw)
        if raw.startswith("$ "):
            spans = f'<tspan class="p">$</tspan> {_esc(raw[2:])}'
        elif m:
            clock, level, msg = m.groups()
            head, sep, rest = msg.partition(": ")
            key = head if head in STAGE_COLOUR else head.split(" ")[0]
            cls = f"k-{key}" if sep and key in STAGE_COLOUR else ""
            msg_html = (
                f'<tspan class="{cls}">{_esc(head)}</tspan>{sep}{_esc(_clip(rest, 92))}'
                if cls
                else _esc(_clip(msg, 100))
            )
            flag = f'<tspan class="w">{level.lower()} </tspan>' if level != "INFO" else ""
            spans = f'<tspan class="ts">{clock}</tspan>  {flag}{msg_html}'
        else:
            spans = _esc(_clip(raw, 110))
        body.append(f'<text class="t l{i}" x="16" y="{y:g}">{spans}</text>')
        if t > 0:
            css.append(
                anim(
                    f"l{i}",
                    f"l{i}",
                    T,
                    [
                        (0, "opacity:0"),
                        (pct(t) - 0.01, "opacity:0"),
                        (pct(t), "opacity:1"),
                        (100, "opacity:1"),
                    ],
                )
            )
    body.append("</g></g>")
    # The scroll: a line's height up for each line past the window.
    stops: list[tuple[float, str]] = [(0, "transform:translateY(0)")]
    for i, t in enumerate(when):
        if i >= ROWS:
            before = -(i - ROWS) * LH
            stops += [
                (pct(t) - 0.01, f"transform:translateY({before:g}px)"),
                (pct(t), f"transform:translateY({before - LH:g}px)"),
            ]
    end = -max(0, len(when) - ROWS) * LH
    stops.append((100, f"transform:translateY({end:g}px)"))
    css.append(anim("scroll", "scroll", T, stops))
    alt = (
        "A terminal on the desktop running hikidashi worker with --server: it starts, claims "
        "batches of reels from the server for asr, ocr, tag and recipe, logs each batch as it "
        "finishes, and exits when there is nothing left to do."
    )
    return svg(W, H, alt, "\n".join(css), "\n".join(body))


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


SVGS: dict[str, Callable[[], str]] = {
    "pipeline.svg": pipeline,
    "diagrams/leases.svg": leases,
    "diagrams/remote-worker.svg": remote,
    "diagrams/search-fusion.svg": search,
    "diagrams/shared-cache.svg": shared,
    "diagrams/remote-worker-terminal.svg": terminal,
}


# --- running it ---------------------------------------------------------------


def outputs() -> dict[Path, str]:
    """Every file this script owns, with what it should hold."""
    out = {DOCS / name: make() for name, make in SVGS.items()}
    made = blocks()
    for md in sorted(DOCS.rglob("*.md")):
        text_ = md.read_text()
        if MARK.search(text_):
            out[md] = fill(text_, made)
    return out


def stale() -> list[Path]:
    return [p for p, want in outputs().items() if not p.is_file() or p.read_text() != want]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if any output is stale")
    args = ap.parse_args(argv)
    if args.check:
        old = stale()
        for p in old:
            print(f"stale: {p.relative_to(ROOT)}")
        if old:
            print("run: uv run python scripts/make_diagrams.py")
        return 1 if old else 0
    for p, want in outputs().items():
        if not p.is_file() or p.read_text() != want:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(want)
            print(f"wrote {p.relative_to(ROOT)} ({len(want.encode()) / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
