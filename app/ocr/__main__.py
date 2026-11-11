"""CLI: python -m app.ocr [--limit N] [--workers N] [--force]

Reads the text burned into photo posts and into reels with no speech. Safe to
interrupt: each item is its own transaction, so re-running resumes where it
stopped. Run `python -m app.asr` first; a reel is read only once it is known to
have no speech.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time

from app.config import settings
from app.db import open_db
from app.profiles import cpus

from .reader import OcrUnavailable
from .worker import Progress, ocr_all, pending, summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.ocr", description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, help="stop after this many items")
    ap.add_argument(
        "--workers",
        type=int,
        default=min(8, cpus()),
        help="items read at once, one tesseract each (default: cores, up to 8)",
    )
    ap.add_argument(
        "--force", action="store_true", help="re-read everything, not just items behind ocr_v"
    )
    args = ap.parse_args(argv)

    missing = [t for t in ("tesseract", "ffmpeg") if not shutil.which(t)]
    if missing:
        print(f"  not installed: {', '.join(missing)}", file=sys.stderr)
        print("  apt install tesseract-ocr ffmpeg, or run it in the Docker image", file=sys.stderr)
        return 1

    settings.ensure_dirs()
    conn = open_db(settings.db_path)

    before = summary(conn)
    print(
        f"  {before['needed']} with no other words · {before['text']} read with "
        f"text · {before['empty']} no text · {before['waiting']} waiting · "
        f"{before['failed']} failed"
    )
    todo = len(pending(conn, limit=args.limit, force=args.force))
    if not todo:
        print("  nothing to do")
        return 0
    print(f"  {todo} to read, {args.workers} at a time\n", flush=True)

    started = time.monotonic()
    last = [0]

    def tick(p: Progress) -> None:
        done = p.text + p.empty + p.failed
        if done - last[0] >= 10 or done == p.total:
            last[0] = done
            print(f"  {p.line()}", flush=True)

    try:
        result = ocr_all(
            conn,
            settings.media_dir,
            workers=args.workers,
            limit=args.limit,
            force=args.force,
            on_progress=tick,
        )
    except KeyboardInterrupt:
        print("\n  interrupted — finished items are saved, re-run to resume", flush=True)
        os._exit(130)
    except OcrUnavailable as exc:
        print(f"\n  stopped: {exc}", file=sys.stderr)
        return 1

    elapsed = time.monotonic() - started
    after = summary(conn)
    print(f"\n  with text   {result.text}  ({result.lines} lines)")
    print(f"  no text     {result.empty}  <- nothing legible on screen")
    print(f"  failed      {result.failed}")
    per = elapsed / max(1, result.text + result.empty + result.failed)
    print(f"  time        {elapsed / 60:,.1f} min ({per:,.1f} s per item)")
    print(
        f"\n  library     {after['text']}/{after['needed']} items with no other "
        f"words now have on-screen text"
    )
    if result.text:
        print("              re-run `python -m app.tagging --llm` to tag them with it")
    if after["failed"]:
        print(f"              {after['failed']} failed, will be retried on the next run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
