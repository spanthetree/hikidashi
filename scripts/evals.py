#!/usr/bin/env python3
"""Score search and tagging on the demo library, and track them by version (#57).

    uv run python scripts/evals.py                  print the scores
    uv run python scripts/evals.py --check          and fail if they regressed
    uv run python scripts/evals.py --record [--note TEXT]
                                                    write this version's row

The library is tests/eval/demo.sqlite3, the demo library without its sign-ins,
with a vector for every query in tests/search_eval.json beside it
(`scripts/demo_library.py fixture` makes both). So no model runs: search is
scored with the stored vectors, and tagging is the tags the demo build gave,
against the hand labels in tests/tag_labels.json (#6). The same inputs give the
same numbers on any machine, which is what lets CI compare them.

docs/eval/history.json keeps one row a release. `--check` compares today's
code against the last row and fails past THRESHOLDS; CI runs it on every pull
request, and writes the comparison to the job summary. The release commit
runs `--record` (CONTRIBUTING.md), and `scripts/make_diagrams.py` draws the
history as docs/diagrams/eval-history.svg and the table in docs/eval.md.

Tagging only moves when the fixture is built again or the labels change: the
demo build is what ran the model. Latency is left out, since on a library of
41 reels it measures the machine; docs/search.md has it on a real one.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "tests" / "eval" / "demo.sqlite3"
VECTORS = ROOT / "tests" / "eval" / "query_vectors.json"
QUERIES = ROOT / "tests" / "search_eval.json"
LABELS = ROOT / "tests" / "tag_labels.json"
HISTORY = ROOT / "docs" / "eval" / "history.json"

# How far a score may fall from the last release before --check fails. A kind
# has 2 to 7 demo queries, so one query moving one place is worth ~0.03 there.
THRESHOLDS = {"overall": 0.02, "kind": 0.05, "tagging": 0.05}
METRICS = ("recall", "mrr", "ndcg")


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 3)


def version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    v: str = data["project"]["version"]
    return v


def score() -> dict[str, Any]:
    """Search, both modes, and tagging, scored on the fixture."""
    from app.db import open_db
    from app.search import load_vec
    from app.search.eval import load, read_vectors, run, summary
    from app.tagging.evaluate import evaluate, labels_from
    from app.tagging.evaluate import summary as tag_summary

    with tempfile.TemporaryDirectory() as tmp:
        # A copy: opening the library brings its schema up to date.
        db = Path(tmp) / "demo.sqlite3"
        shutil.copy(FIXTURE, db)
        conn = open_db(db)
        if not load_vec(conn):
            raise SystemExit("sqlite-vec is not installed: pip install sqlite-vec")
        known = {r[0] for r in conn.execute("SELECT shortcode FROM item")}
        queries, missing = load(QUERIES, known)
        if missing:
            raise SystemExit(f"not in the fixture: {missing}; run demo_library.py fixture")
        embed = read_vectors(VECTORS)
        search = {}
        for mode in ("blended", "keyword"):
            s = summary(run(conn, queries, mode, embed))
            search[mode] = {
                **{m: _r(s[m]) for m in METRICS},
                "kinds": {
                    k: {"n": v["n"], **{m: _r(v[m]) for m in METRICS}}
                    for k, v in s["kinds"].items()
                },
                "negatives": {
                    "n": s["negatives"]["n"],
                    "quiet": s["negatives"]["quiet"],
                    "mean_hits": _r(s["negatives"]["mean_hits"]),
                },
            }
        conn.close()
        ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        tags = tag_summary(evaluate(ro, truth=labels_from(LABELS)))
        ro.close()
    tagging = {
        source: {k: _r(v) if isinstance(v, float) else v for k, v in scores.items()}
        for source, scores in tags.items()
    }
    return {"queries": len(queries), "search": search, "tagging": tagging}


def history() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(HISTORY.read_text(encoding="utf-8"))
    return data


def regressions(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """What fell past a threshold between two results."""
    out = []
    for mode, now in new["search"].items():
        was = old["search"].get(mode)
        if not was:
            continue
        if now["ndcg"] < was["ndcg"] - THRESHOLDS["overall"]:
            out.append(f"{mode} nDCG@10 {was['ndcg']:.3f} -> {now['ndcg']:.3f}")
        for kind, k in now["kinds"].items():
            before = was["kinds"].get(kind)
            if before and k["ndcg"] < before["ndcg"] - THRESHOLDS["kind"]:
                out.append(f"{mode} {kind} nDCG@10 {before['ndcg']:.3f} -> {k['ndcg']:.3f}")
        if now["negatives"]["quiet"] < was["negatives"]["quiet"]:
            out.append(
                f"{mode} negatives quiet {was['negatives']['quiet']} -> {now['negatives']['quiet']}"
            )
    was_p, now_p = old["tagging"]["shown"]["precision"], new["tagging"]["shown"]["precision"]
    if was_p is not None and now_p is not None and now_p < was_p - THRESHOLDS["tagging"]:
        out.append(f"tagging precision {was_p:.3f} -> {now_p:.3f}")
    return out


def table(old: dict[str, Any] | None, new: dict[str, Any]) -> str:
    """Markdown: today's scores, and the change since the last release."""
    label = f"since {old['version']}" if old else ""
    lines = [f"| | nDCG@10 | MRR | recall@10 | {label} |", "|---|---|---|---|---|"]

    def delta(now: float, was: float | None) -> str:
        if was is None:
            return ""
        d = now - was
        return "" if abs(d) < 0.0005 else f"{d:+.3f}"

    for mode, s in new["search"].items():
        was = old["search"].get(mode) if old else None
        lines.append(
            f"| **{mode}** | {s['ndcg']:.3f} | {s['mrr']:.3f} | {s['recall']:.3f} | "
            f"{delta(s['ndcg'], was['ndcg'] if was else None)} |"
        )
        for kind, k in s["kinds"].items():
            before = was["kinds"].get(kind) if was else None
            lines.append(
                f"| {mode} · {kind} ({k['n']}) | {k['ndcg']:.3f} | {k['mrr']:.3f} | "
                f"{k['recall']:.3f} | {delta(k['ndcg'], before['ndcg'] if before else None)} |"
            )
        neg = s["negatives"]
        lines.append(f"| {mode} · negatives | {neg['quiet']}/{neg['n']} quiet | | | |")
    t = new["tagging"]["shown"]
    lines.append(
        f"| **tagging** (shown) | precision {t['precision']:.3f} | "
        f"coverage {t['coverage']:.3f} | abstained {t['abstained']}/{t['unfit']} | |"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="fail if a score regressed")
    ap.add_argument("--record", action="store_true", help="write this version's row")
    ap.add_argument("--note", default="", help="with --record: what moved the numbers")
    args = ap.parse_args(argv)

    new = score()
    data = history()
    rows = data["rows"]
    v = version()
    # The last release before this version: on a release PR, the row before
    # the one it records.
    old = next((r for r in reversed(rows) if r["version"] != v), None)
    print(table(old, new))

    if summary_file := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary_file, "a", encoding="utf-8") as f:
            f.write(f"### Evals on the demo library\n\n{table(old, new)}\n\n")

    if args.record:
        commit = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
        ).stdout.strip()
        row = {
            "version": v,
            "date": datetime.date.today().isoformat(),
            "commit": commit,
            "note": args.note,
            **new,
        }
        data["rows"] = [r for r in rows if r["version"] != v] + [row]
        HISTORY.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
        print(f"\nrecorded {v} in {HISTORY.relative_to(ROOT)}")
        print("now run: uv run python scripts/make_diagrams.py")

    if args.check and old:
        fell = regressions(old, new)
        if fell:
            print(f"\nregressed since {old['version']}:")
            for line in fell:
                print(f"  {line}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
