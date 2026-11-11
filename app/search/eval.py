"""CLI: python -m app.search.eval [--set PATH] [--mode MODE ...] [--queries] [--against REF]

Scores search against a graded query set (data/search_eval.json, your own
and git-ignored, else tests/search_eval.json over the demo library):
recall@10, MRR and nDCG@10, overall and per kind of query, plus how much the
negatives return and how long a search takes. Each mode is one way of
searching, so the gain from each stage is visible next to the others:

    keyword     FTS5 and the LIKE fallbacks only
    blended     keywords and vectors, fused: what the app searches with

A grade of 2 is the reel the query is after, 1 a reel that is fair to show.

`--against REF` scores search as it was at a git ref too, on today's library
and query set, and lists the queries whose score moved: a before and after
that the library changing underneath cannot skew.
"""

from __future__ import annotations

import argparse
import inspect
import json
import math
import sqlite3
import statistics
import subprocess
import sys
import time
import types
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import ROOT, settings
from app.db import open_db

from . import load_vec, rank

Explained = dict[str, dict[str, Any]]
# (conn, query, its vector or None, explanations to fill) -> shortcodes, best first
Ranker = Callable[[sqlite3.Connection, str, bytes | None, Explained], list[str]]

# Your own set, over your own library: git-ignored, since its shortcodes are a
# list of what you saved. Else the one over the demo library.
OWN_SET = ROOT / "data" / "search_eval.json"
DEFAULT_SET = OWN_SET if OWN_SET.exists() else ROOT / "tests" / "search_eval.json"
AT = 10
# A negative "stays quiet" when it returns no more than this many reels: one
# or two weak matches are a fair answer to a query with no good one.
QUIET = 3
MODES = ("keyword", "blended")


@dataclass
class Query:
    q: str
    kind: str
    relevant: dict[str, int]


@dataclass
class Scored:
    query: Query
    hits: list[str]
    ms: float
    recall: float = 0.0
    mrr: float = 0.0
    ndcg: float = 0.0
    why: dict[str, dict[str, Any]] = field(default_factory=dict[str, Any])


def load(path: Path, known: set[str]) -> tuple[list[Query], list[str]]:
    """The query set, minus shortcodes this library does not hold."""
    data = json.loads(path.read_text(encoding="utf-8"))
    queries, missing = [], []
    for row in data["queries"]:
        rel = {}
        for code, grade in row["relevant"].items():
            if code in known:
                rel[code] = grade
            else:
                missing.append(f"{row['q']!r}: {code}")
        queries.append(Query(row["q"], row["kind"], rel))
    return queries, missing


def dcg(grades: list[int]) -> float:
    return sum((2.0**g - 1) / math.log2(i + 2) for i, g in enumerate(grades))


def score(query: Query, hits: list[str], ms: float) -> Scored:
    s = Scored(query, hits, ms)
    rel = query.relevant
    if not rel:
        return s
    top = hits[:AT]
    s.recall = sum(1 for h in top if h in rel) / min(len(rel), AT)
    s.mrr = next((1 / (i + 1) for i, h in enumerate(hits) if h in rel), 0.0)
    ideal = dcg(sorted(rel.values(), reverse=True)[:AT])
    s.ndcg = dcg([rel.get(h, 0) for h in top]) / ideal
    return s


def rank_at(ref: str) -> Ranker:
    """`rank` as it was at git `ref`. Run against this checkout's app package,
    so a ref whose search imports something since removed will not load."""
    for path in ("app/search/__init__.py", "app/search.py"):
        shown = subprocess.run(
            ["git", "-C", str(ROOT), "show", f"{ref}:{path}"], capture_output=True, text=True
        )
        if shown.returncode == 0:
            break
    else:
        raise SystemExit(f"no search module at {ref}")
    module = types.ModuleType(f"search@{ref}")
    exec(compile(shown.stdout, f"{ref}:{path}", "exec"), module.__dict__)
    # Older versions take neither: no explanations, and one library.
    takes = inspect.signature(module.rank).parameters

    def ranked(
        conn: sqlite3.Connection, q: str, vec: bytes | None, explain: Explained
    ) -> list[str]:
        hits: list[str] = module.rank(
            conn, q, vec, **({"explain": explain} if "explain" in takes else {})
        )
        return hits

    return ranked


def run(
    conn: sqlite3.Connection,
    queries: list[Query],
    mode: str,
    embed: Callable[[str], bytes] | None,
    ranker: Ranker | None = None,
) -> list[Scored]:
    ranker = ranker or (lambda conn, q, vec, explain: rank(conn, q, vec, explain=explain))
    out = []
    for query in queries:
        started = time.perf_counter()
        vec = embed(query.q) if embed and not mode.startswith("keyword") else None
        why: Explained = {}
        hits = ranker(conn, query.q, vec, why)
        ms = (time.perf_counter() - started) * 1000
        s = score(query, hits, ms)
        s.why = why
        out.append(s)
    return out


def mean(xs: Iterable[float]) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def p95(xs: list[float]) -> float:
    return statistics.quantiles(xs, n=20)[-1] if len(xs) > 1 else (xs[0] if xs else 0.0)


def summary(results: list[Scored]) -> dict[str, Any]:
    graded = [r for r in results if r.query.relevant]
    negatives = [r for r in results if not r.query.relevant]
    kinds: dict[str, dict[str, Any]] = {}
    for kind in sorted({r.query.kind for r in graded}):
        rs = [r for r in graded if r.query.kind == kind]
        kinds[kind] = {
            "n": len(rs),
            "recall": mean(r.recall for r in rs),
            "mrr": mean(r.mrr for r in rs),
            "ndcg": mean(r.ndcg for r in rs),
        }
    ms = [r.ms for r in results]
    return {
        "n": len(graded),
        "recall": mean(r.recall for r in graded),
        "mrr": mean(r.mrr for r in graded),
        "ndcg": mean(r.ndcg for r in graded),
        "kinds": kinds,
        "negatives": {
            "n": len(negatives),
            "mean_hits": mean(len(r.hits) for r in negatives),
            "quiet": sum(len(r.hits) <= QUIET for r in negatives),
        },
        "ms_p50": statistics.median(ms) if ms else 0.0,
        "ms_p95": p95(ms),
    }


def print_table(by_mode: dict[str, dict[str, Any]]) -> None:
    modes = list(by_mode)
    head = f"  {'':16}" + "".join(f"{m:>26}" for m in modes)
    print(head)
    print(f"  {'':16}" + "".join(f"{'recall  MRR   nDCG':>26}" for _ in modes))

    def row(label: str, get: Callable[[dict[str, Any]], tuple[float, float, float] | None]) -> None:
        cells = []
        for m in modes:
            v = get(by_mode[m])
            cells.append(f"{v[0]:>6.2f} {v[1]:>5.2f} {v[2]:>6.2f}" if v else "")
        print(f"  {label:16}" + "".join(f"{c:>26}" for c in cells))

    row("all", lambda s: (s["recall"], s["mrr"], s["ndcg"]))
    for kind in by_mode[modes[0]]["kinds"]:
        n = by_mode[modes[0]]["kinds"][kind]["n"]

        def of_kind(s: dict[str, Any], k: str = kind) -> tuple[float, float, float]:
            return (s["kinds"][k]["recall"], s["kinds"][k]["mrr"], s["kinds"][k]["ndcg"])

        row(f"{kind} ({n})", of_kind)
    print()
    neg = by_mode[modes[0]]["negatives"]["n"]

    def quiet(s: dict[str, Any]) -> str:
        return f"{s['negatives']['quiet']}/{neg} quiet, {s['negatives']['mean_hits']:.1f} hits avg"

    print(f"  {'negatives':16}" + "".join(f"{quiet(s):>26}" for s in by_mode.values()))
    print(
        f"  {'latency':16}"
        + "".join(
            f"{f'p50 {s["ms_p50"]:.0f} ms, p95 {s["ms_p95"]:.0f} ms':>26}" for s in by_mode.values()
        )
    )


def print_queries(results: list[Scored]) -> None:
    """Where each expected reel ranked, and why the top hits matched."""
    for r in results:
        rel = r.query.relevant
        pos = {h: i + 1 for i, h in enumerate(r.hits)}
        if rel:
            found = " ".join(
                f"{c}{'*' if g == 2 else ''}@{pos.get(c, '-')}"
                for c, g in sorted(rel.items(), key=lambda kv: pos.get(kv[0], 1e9))
            )
            print(f"  {r.ndcg:4.2f}  [{r.query.kind}] {r.query.q!r}\n        {found}")
        else:
            print(f"  {len(r.hits):4d}  [{r.query.kind}] {r.query.q!r} returned")
        top = ", ".join(f"{h}({'+'.join(r.why.get(h, {}).get('match', []))})" for h in r.hits[:5])
        print(f"        top: {top}")


def print_moved(before: list[Scored], after: list[Scored]) -> None:
    """The queries whose score changed between two runs of one mode."""
    for b, a in zip(before, after, strict=False):
        if b.query.relevant:
            old, new, fmt = b.ndcg, a.ndcg, "%.2f"
        else:
            old, new, fmt = len(b.hits), len(a.hits), "%d hits"
        if round(new, 2) != round(old, 2):
            print(f"  {fmt % old:>8} -> {fmt % new:<8} [{a.query.kind}] {a.query.q!r}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m app.search.eval", description=__doc__.splitlines()[0]
    )
    ap.add_argument("--set", type=Path, default=DEFAULT_SET, help="the graded query set")
    ap.add_argument(
        "--mode",
        action="append",
        choices=MODES,
        help="search mode to score (repeatable; default: all)",
    )
    ap.add_argument("--queries", action="store_true", help="print where each expected reel ranked")
    ap.add_argument("--json", type=Path, help="also write the summary here")
    ap.add_argument(
        "--against",
        metavar="REF",
        help="also score search as it was at this git ref, and list what moved",
    )
    args = ap.parse_args(argv)
    modes = args.mode or list(MODES)

    conn = open_db(settings.db_path)
    known = {r[0] for r in conn.execute("SELECT shortcode FROM item")}
    queries, missing = load(args.set, known)
    if missing:
        print(f"  {len(missing)} expected shortcodes are not in this library, ignored:")
        for m in missing:
            print(f"    {m}")

    embed = None
    if "blended" in modes:
        if not load_vec(conn):
            print("  sqlite-vec is not installed: keyword mode only")
            modes = ["keyword"]
        else:
            from app.embed.embedder import Embedder

            embed = Embedder(settings.models_dir).query
            embed("warm up")

    by_mode, detail = {}, {}
    old = rank_at(args.against) if args.against else None
    for mode in modes:
        if old:
            then = f"{mode}@{args.against}"
            detail[then] = run(conn, queries, mode, embed, old)
            by_mode[then] = summary(detail[then])
        detail[mode] = run(conn, queries, mode, embed)
        by_mode[mode] = summary(detail[mode])

    print(f"\n  {len(queries)} queries, {settings.db_path.name}, {len(known)} items\n")
    print_table(by_mode)
    if args.queries:
        for mode in by_mode:
            print(f"\n  --- {mode} ---")
            print_queries(detail[mode])
    if old:
        for mode in modes:
            print(f"\n  --- {mode}: moved since {args.against} ---")
            print_moved(detail[f"{mode}@{args.against}"], detail[mode])
    if args.json:
        args.json.write_text(json.dumps(by_mode, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
