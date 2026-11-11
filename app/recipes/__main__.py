"""CLI: python -m app.recipes [--force] [--limit N] [--label] [--eval]

Finds the reels that are recipes and reads each one's ingredients and steps out
of its caption, transcript and on-screen text, with the same local model as the
tagger (Ollama). Resumable: interrupt it and re-run. --label and --eval measure
it against reels you have labelled by hand.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import textwrap
import time
from pathlib import Path

from app.config import settings
from app.db import open_db
from app.ollama import OllamaError
from app.profiles import CHOICES, resolve

from . import evaluate


def labels_path() -> Path:
    return settings.db_path.parent / "recipe_labels.json"


def run(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    from . import llm, worker

    try:
        profile = resolve(args.profile)
    except ValueError as exc:
        print(f"  {exc}", file=sys.stderr)
        return 2

    before = worker.summary(conn)
    print(
        f"  recipe pass: {before['done']} read · {before['recipes']} recipes · "
        f"{before['waiting']} waiting · {before['waiting_upstream']} waiting on "
        f"transcript, OCR or tags · {before['failed']} failed"
    )
    todo = len(worker.pending(conn, limit=args.limit, force=args.force))
    if not todo:
        print("  nothing to do")
        return 0

    model = llm.Ollama(settings.ollama_url, profile.tag_model)
    print(f"  profile {profile.name}: {model.model} via {model.url}", flush=True)
    try:
        if not model.has_model():
            print(f"  pulling {model.model} (once; several GB)", flush=True)
            model.pull(on_status=lambda s: print(f"    {s}", flush=True))
    except OllamaError as exc:
        print(f"  {exc}", file=sys.stderr)
        print(
            "  start it with `docker compose --profile ai up -d`, or point "
            "OLLAMA_URL at a running server",
            file=sys.stderr,
        )
        return 1
    print(f"  {todo} to read\n", flush=True)

    started = time.monotonic()
    last = [0]

    def tick(p: worker.Progress) -> None:
        done = p.recipes + p.not_recipes + p.skipped + p.failed
        if done - last[0] >= 10 or done == p.total:
            last[0] = done
            print(f"  {p.line()}", flush=True)

    try:
        result = worker.read_all(
            conn, model, profile=profile.name, limit=args.limit, force=args.force, on_progress=tick
        )
    except KeyboardInterrupt:
        print("\n  interrupted — finished items are saved, re-run to resume")
        return 130
    except OllamaError as exc:
        print(f"\n  stopped: {exc}\n  finished items are saved, re-run to resume", file=sys.stderr)
        return 1

    elapsed = time.monotonic() - started
    after = worker.summary(conn)
    print(f"\n  recipes     {result.recipes}")
    print(f"  not recipes {result.not_recipes}  <- the model said no")
    print(f"  skipped     {result.skipped}  <- no Food tag and no recipe signals")
    print(f"  failed      {result.failed}")
    print(f"  time        {elapsed / 60:,.1f} min")
    print(
        f"\n  library     {after['recipes']} recipes, {after['incomplete']} incomplete, "
        f"{after['edited']} corrected by you"
    )
    if after["failed"]:
        print(f"              {after['failed']} failed, will be retried on the next run")
    return 0


def _clip(text: str | None, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n] + "…"


def label(conn: sqlite3.Connection) -> int:
    path = labels_path()
    labels = evaluate.load(path)
    todo = evaluate.to_label(conn, labels)
    print(
        f"  {len(labels)} labelled, {len(todo)} to go. "
        "y: a recipe · n: not one · s: skip · q: quit\n"
    )
    for i, row in enumerate(todo, 1):
        print(
            f"  [{i}/{len(todo)}] {row['shortcode']}  @{row['author'] or '?'}  {row['permalink']}"
        )
        for name in ("caption", "transcript", "ocr_text"):
            if row[name]:
                body = textwrap.indent(textwrap.fill(_clip(row[name], 500), 76), "      ")
                print(f"    {name}\n{body}")
        while True:
            try:
                answer = input("  recipe? ").strip().lower()[:1]
            except EOFError:
                answer = "q"
            if answer in ("y", "n", "s", "q"):
                break
        if answer == "q":
            break
        if answer in ("y", "n"):
            labels[row["shortcode"]] = answer == "y"
            evaluate.save(path, labels)
        print()
    print(f"  {len(labels)} labelled, in {path}")
    return 0


def report(conn: sqlite3.Connection) -> int:
    labels = evaluate.load(labels_path())
    if not labels:
        print(f"  no labels yet; make some with --label (they go in {labels_path()})")
        return 1
    r = evaluate.evaluate(conn, labels)

    def pct(x: float | None) -> str:
        return "—" if x is None else f"{x:.0%}"

    print(
        f"  labelled    {r.labelled}"
        + (f"  ({r.unread} not read yet: run the pass first)" if r.unread else "")
    )
    print(f"  precision   {pct(r.precision)}  ({r.tp} of {r.tp + r.fp} found are recipes)")
    print(f"  recall      {pct(r.recall)}  ({r.tp} of {r.tp + r.fn} recipes found)")
    print(f"  confusion   tp {r.tp} · fp {r.fp} · fn {r.fn} · tn {r.tn}")
    print(
        f"  ingredients {r.ingredients} stored · {len(r.ungrounded)} not in the text"
        f" · {r.dropped} dropped by grounding"
    )
    for name, codes in (("false positives", r.false_pos), ("missed", r.false_neg)):
        if codes:
            print(f"\n  {name}\n" + textwrap.indent(textwrap.fill(" ".join(codes), 72), "    "))
    for code, item in r.ungrounded:
        print(f"  ungrounded  {code}: {item}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.recipes", description=__doc__.splitlines()[0])
    ap.add_argument(
        "--profile",
        choices=CHOICES,
        default=settings.profile,
        help=f"hardware profile, which picks the model (default "
        f"{settings.profile}, from HIKIDASHI_PROFILE)",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="read everything again, not just items behind the current version",
    )
    ap.add_argument("--limit", type=int)
    ap.add_argument("--label", action="store_true", help="label reels as recipe or not, for --eval")
    ap.add_argument("--eval", action="store_true", help="precision and recall against your labels")
    args = ap.parse_args(argv)

    settings.ensure_dirs()
    conn = open_db(settings.db_path)
    if args.label:
        return label(conn)
    if args.eval:
        return report(conn)
    return run(conn, args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
