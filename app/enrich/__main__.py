"""CLI: python -m app.enrich [--limit N] [--media] [--concurrency N] [--retry-failed]

Fetches metadata and thumbnails for every item not yet fetched at the current
fetch_v. Add --media to also download the original mp4s. Safe to interrupt:
each item is its own transaction, so re-running resumes where it stopped.
"""

from __future__ import annotations

import argparse
import sys

from app.config import settings
from app.db import open_db

from .worker import Progress, enrich, fetch_media, pending, pending_media, summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.enrich", description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, help="stop after this many items")
    ap.add_argument(
        "--concurrency", type=int, default=3, help="parallel fetches (default 3; be polite)"
    )
    ap.add_argument(
        "--media", action="store_true", help="also download the original mp4s while fetching"
    )
    ap.add_argument(
        "--media-only",
        action="store_true",
        help="download mp4s for items already fetched without one",
    )
    ap.add_argument(
        "--retry-failed", action="store_true", help="include items that already failed permanently"
    )
    ap.add_argument(
        "--delay", type=float, default=0.4, help="seconds to wait before each fetch (default 0.4)"
    )
    args = ap.parse_args(argv)

    settings.ensure_dirs()
    conn = open_db(settings.db_path)

    last = [0.0]

    def tick(p: Progress) -> None:
        done = p.fetched + p.failed + p.skipped
        if done - last[0] >= 10 or done == p.total:
            last[0] = done
            print(f"  {p.line()}", flush=True)

    before = summary(conn)
    print(
        f"  {before['waiting']} waiting · {before['fetched']} fetched · "
        f"{before['failed']} failed · {before['media']} with video"
    )

    if args.media_only:
        todo = len(pending_media(conn, limit=args.limit))
        print(f"  {todo} items have no video or slides yet\n")
        try:
            result = fetch_media(
                conn,
                settings.media_dir,
                limit=args.limit,
                concurrency=args.concurrency,
                delay=args.delay,
                on_progress=tick,
            )
        except KeyboardInterrupt:
            print("\n  interrupted — progress is saved, re-run to resume")
            return 130
        after = summary(conn)
        print(f"\n  downloaded  {result.media}")
        print(f"  slides      {result.slides}  <- photos of carousels")
        print(f"  skipped     {result.skipped}  <- no video (photo posts)")
        print(f"  size        {result.bytes / 1_048_576:,.0f} MB")
        print(
            f"\n  library     {after['media']}/{after['total']} with video, "
            f"{after['bytes'] / 1_073_741_824:,.2f} GB"
        )
        return 0

    try:
        result = enrich(
            conn,
            settings.media_dir,
            limit=args.limit,
            concurrency=args.concurrency,
            with_media=args.media,
            retry_failed=args.retry_failed,
            delay=args.delay,
            on_progress=tick,
        )
    except KeyboardInterrupt:
        print("\n  interrupted — progress is saved, re-run to resume")
        return 130

    after = summary(conn)
    print(f"\n  fetched     {result.fetched}")
    print(f"  failed      {result.failed}")
    print(f"  thumbnails  {result.thumbs}")
    print(f"  slides      {result.slides}")
    if args.media:
        print(f"  videos      {result.media}  ({result.bytes / 1_048_576:,.0f} MB)")
    retryable = len(pending(conn))
    print(
        f"\n  library     {after['thumbs']}/{after['total']} with thumbnails, "
        f"{after['failed']} failed"
    )
    if retryable:
        print(f"              {retryable} will be retried on the next run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
