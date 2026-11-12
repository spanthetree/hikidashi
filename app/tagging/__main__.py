"""CLI: python -m app.tagging [--llm] [--force] [--eval [--labels PATH] [--db PATH]] [--pick N]

Derives each post's category from its hashtags, and cleans them: deterministic,
offline and instant. With --llm it then asks a local model (Ollama) about each
reel's caption, hashtags, transcript and on-screen text, which is what tags the
posts whose hashtags say nothing. The model pass is resumable: interrupt it and
re-run. --eval scores both against the tags you set by hand (#6), and --pick
lists reels worth setting one on.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

from app.config import settings
from app.db import open_db
from app.profiles import CHOICES, resolve

from .service import apply_hashtags, tag_counts


def run_llm(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    from . import llm, worker

    try:
        profile = resolve(args.profile)
    except ValueError as exc:
        print(f"  {exc}", file=sys.stderr)
        return 2

    before = worker.summary(conn)
    print(
        f"\n  model pass: {before['done']} done · {before['waiting']} waiting · "
        f"{before['waiting_asr']} waiting for a transcript · "
        f"{before['waiting_ocr']} waiting for OCR · "
        f"{before['failed']} failed"
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
    except llm.OllamaError as exc:
        print(f"  {exc}", file=sys.stderr)
        print(
            "  start it with `docker compose --profile ai up -d`, or point "
            "OLLAMA_URL at a running server",
            file=sys.stderr,
        )
        return 1
    print(f"  {todo} to tag\n", flush=True)

    started = time.monotonic()
    last = [0]

    def tick(p: worker.Progress) -> None:
        done = p.tagged + p.untagged + p.failed
        if done - last[0] >= 10 or done == p.total:
            last[0] = done
            print(f"  {p.line()}", flush=True)

    try:
        result = worker.tag_all(
            conn, model, profile=profile.name, limit=args.limit, force=args.force, on_progress=tick
        )
    except KeyboardInterrupt:
        print("\n  interrupted — finished items are saved, re-run to resume")
        return 130
    except llm.OllamaError as exc:
        print(f"\n  stopped: {exc}\n  finished items are saved, re-run to resume", file=sys.stderr)
        return 1

    elapsed = time.monotonic() - started
    after = worker.summary(conn)
    print(f"\n  tagged      {result.tagged}")
    print(f"  untagged    {result.untagged}  <- no category clearly fits")
    print(f"  failed      {result.failed}")
    per = elapsed / max(1, result.tagged + result.untagged + result.failed)
    print(f"  time        {elapsed / 60:,.1f} min ({per:,.1f} s per reel)")
    print(f"\n  library     {after['untagged']} items still without any tag")
    if after["failed"]:
        print(f"              {after['failed']} failed, will be retried on the next run")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.tagging", description=__doc__.splitlines()[0])
    ap.add_argument(
        "--llm", action="store_true", help="also run the model tagger over caption and transcript"
    )
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
        help="re-tag everything, not just items behind the current version",
    )
    ap.add_argument(
        "--eval",
        action="store_true",
        help="score the model and the hashtags against the tags you "
        "set by hand, and change nothing",
    )
    ap.add_argument(
        "--db",
        metavar="PATH",
        help="with --eval: score the answers in another database, such "
        "as a backup from before a change, on today's labels",
    )
    ap.add_argument(
        "--labels",
        metavar="PATH",
        type=Path,
        help="with --eval: labels from a file instead of your hand-set tags "
        "(tests/tag_labels.json labels the demo library)",
    )
    ap.add_argument(
        "--pick",
        metavar="N",
        type=int,
        help="list about N reels worth labelling by hand, and change nothing",
    )
    ap.add_argument("--limit", type=int)
    args = ap.parse_args(argv)

    if args.eval:
        from .evaluate import evaluate, labels_from, report

        # Read only, and not through open_db: scoring an old backup must not
        # migrate it to this schema first. Your labels are always the live ones.
        def read_only(path: Path) -> sqlite3.Connection:
            return sqlite3.connect(f"file:{path}?mode=ro", uri=True)

        conn = read_only(settings.db_path)
        truth = labels_from(args.labels) if args.labels else None
        print(report(evaluate(conn, read_only(args.db) if args.db else None, truth)))
        return 0

    if args.pick:
        from .evaluate import pick

        conn = sqlite3.connect(f"file:{settings.db_path}?mode=ro", uri=True)
        chosen = pick(conn, args.pick)
        print(f"  {len(chosen)} reels to label: open each, and set its tag in the detail sheet\n")
        for shortcode, shown, why in chosen:
            print(f"  {shortcode:<14} shows {shown:<12} {why}")
        return 0

    settings.ensure_dirs()
    conn = open_db(settings.db_path)

    stats = apply_hashtags(conn, limit=args.limit, force=args.force)
    print(f"  items       {stats.items}")
    print(f"  tagged      {stats.tagged}")
    print(f"  untagged    {stats.untagged}  <- no hashtag matched the taxonomy")

    if stats.per_tag:
        print("\n  taxonomy coverage")
        for name, n in sorted(stats.per_tag.items(), key=lambda kv: -kv[1]):
            print(f"    {n:4}  {name}")

    if args.llm:
        code = run_llm(conn, args)
        if code:
            return code

    total = sum(1 for t in tag_counts(conn, user_id=None) if t["count"])
    print(f"\n  {total} tags now in use")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
