"""CLI: python -m app.asr [--limit N] [--profile auto|cpu|cuda] [--force]

Transcribes every reel that has a video on disk and no transcript at the current
asr_v. Safe to interrupt: each item is its own transaction, so re-running
resumes where it stopped. Run `python -m app.enrich --media` first.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from app.config import settings
from app.db import open_db
from app.profiles import CHOICES, resolve

from .worker import Progress, pending, summary, transcribe_all


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.asr", description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, help="stop after this many items")
    ap.add_argument(
        "--profile",
        choices=CHOICES,
        default=settings.profile,
        help=f"hardware profile (default {settings.profile}, from HIKIDASHI_PROFILE)",
    )
    ap.add_argument(
        "--force", action="store_true", help="re-transcribe everything, not just items behind asr_v"
    )
    args = ap.parse_args(argv)

    try:
        profile = resolve(args.profile)
    except ValueError as exc:
        print(f"  {exc}", file=sys.stderr)
        return 2

    settings.ensure_dirs()
    conn = open_db(settings.db_path)

    before = summary(conn)
    print(
        f"  {before['waiting']} waiting · {before['transcribed']} transcribed · "
        f"{before['silent']} no speech · {before['failed']} failed"
    )
    todo = len(pending(conn, limit=args.limit, force=args.force))
    if not todo:
        print("  nothing to do")
        return 0

    print(
        f"  profile {profile.name}: {profile.asr_model} at {profile.asr_compute}, "
        f"{profile.asr_workers} worker{'s' if profile.asr_workers > 1 else ''}"
    )
    print(f"  loading model (downloaded once, into {settings.models_dir})", flush=True)
    from .transcriber import Whisper

    try:
        model = Whisper(profile, settings.models_dir)
    except RuntimeError as exc:
        # Most often a CUDA library that will not load. Nothing was written.
        print(f"  model failed to start: {exc}", file=sys.stderr)
        if profile.name == "cuda":
            print(
                "  install the GPU libraries with `uv sync --extra cuda`, "
                "or run with --profile cpu",
                file=sys.stderr,
            )
        return 1
    print(f"  {todo} to transcribe\n", flush=True)

    started = time.monotonic()
    last = [0]

    def tick(p: Progress) -> None:
        done = p.transcribed + p.silent + p.failed
        if done - last[0] >= 10 or done == p.total:
            last[0] = done
            print(f"  {p.line()}", flush=True)

    try:
        result = transcribe_all(
            conn,
            settings.media_dir,
            model,
            profile=profile.name,
            workers=profile.asr_workers,
            limit=args.limit,
            force=args.force,
            on_progress=tick,
        )
    except KeyboardInterrupt:
        print("\n  interrupted — finished transcripts are saved, re-run to resume", flush=True)
        # Do not wait for the reels still in flight: a ten-minute one holds the
        # exit for a minute. Every finished row is already committed, and SQLite
        # survives an abrupt exit the same way it survives kill -9.
        os._exit(130)

    elapsed = time.monotonic() - started
    after = summary(conn)
    print(f"\n  transcribed {result.transcribed}")
    print(f"  no speech   {result.silent}  <- music-only, or no audio track")
    print(f"  failed      {result.failed}")
    speed = result.seconds / elapsed if elapsed else 0
    print(
        f"  time        {elapsed / 60:,.1f} min for {result.seconds / 60:,.0f} min "
        f"of audio ({speed:,.0f}x realtime)"
    )
    print(f"\n  library     {after['transcribed']}/{after['media']} videos with a transcript")
    if after["failed"]:
        print(f"              {after['failed']} failed, will be retried on the next run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
