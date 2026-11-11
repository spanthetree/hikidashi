"""CLI: python -m app.embed [--limit N] [--force]

Embeds every item whose vector is missing or stale, for semantic search. Safe
to interrupt: each batch is its own transaction, so re-running resumes where it
stopped. Run it after `python -m app.tagging --llm`; it works before, from
captions, and picks up the summaries when they arrive.
"""

from __future__ import annotations

import argparse
import sys
import time

from app.config import settings
from app.db import open_db

from .embedder import MODEL
from .worker import embed_all, pending, summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.embed", description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, help="stop after this many items")
    ap.add_argument(
        "--force",
        action="store_true",
        help="re-embed everything, not just missing or stale vectors",
    )
    args = ap.parse_args(argv)

    settings.ensure_dirs()
    conn = open_db(settings.db_path)

    before = summary(conn)
    print(f"  {before['vectors']}/{before['items']} items embedded")
    todo = len(pending(conn, limit=args.limit, force=args.force))
    if not todo:
        print("  nothing to do")
        return 0

    print(f"  loading {MODEL} (downloaded once, into {settings.models_dir})", flush=True)
    from .embedder import Embedder

    model = Embedder(settings.models_dir)
    print(f"  {todo} to embed\n", flush=True)

    started = time.monotonic()
    try:
        result = embed_all(
            conn,
            model.documents,
            limit=args.limit,
            force=args.force,
            on_progress=lambda p: print(f"  {p.line()}", flush=True),
        )
    except KeyboardInterrupt:
        print("\n  interrupted — finished batches are saved, re-run to resume")
        return 130

    elapsed = time.monotonic() - started
    after = summary(conn)
    print(f"\n  embedded    {result.embedded}")
    print(f"  no text     {result.empty}")
    print(f"  time        {elapsed:,.1f} s")
    print(
        f"\n  library     {after['vectors']}/{after['items']} items in semantic search, "
        f"{after['chunks']} transcript chunks"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
