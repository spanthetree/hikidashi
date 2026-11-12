"""CLI: python -m app.jobs [--stages enrich,asr,ocr,tag,recipe,embed] [--once] [--retry-failed]
                          [--server URL] [--profile auto|cpu|cuda] [--ollama-url URL]

Runs the pipeline's stages on new reels as they arrive, until stopped: a reel
imported now is fetched, transcribed, read, tagged, checked for a recipe and
embedded with no other command. --once does what there is to do, and exits.
Safe to stop at any point: a clean stop gives back the reels in hand, and a
killed worker's are taken back when their leases expire.

With --server (or HIKIDASHI_SERVER), it works for a Hikidashi server on
another machine instead of a database here: a GPU desktop transcribing and
tagging for an always-on server (#35). It runs asr, ocr, tag and recipe, and
needs a worker token the server made: HIKIDASHI_WORKER_TOKEN, else it asks
for one, hidden, when there is a terminal to ask at. Never a flag, so the
token stays out of shell history and the process list.
"""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import signal
import sys
import time
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING

from app import logs
from app.config import settings
from app.db import connect, open_db
from app.profiles import CHOICES

from . import STAGES, parse_stages
from .protocol import REMOTE
from .queue import LocalJobs, retry
from .remote import HttpJobs, Refused, RemoteRunners, wait_for
from .worker import Runners, Stop, Worker

if TYPE_CHECKING:
    from app.ai.runner import Host


def _stop(signum: int, frame: FrameType | None) -> None:
    raise Stop


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hikidashi worker", description=__doc__.splitlines()[0])
    ap.add_argument(
        "--stages",
        help=f"the stages to run, from {','.join(STAGES)} (default "
        f"{','.join(settings.local_stages) or 'none'}, from HIKIDASHI_LOCAL_STAGES)",
    )
    ap.add_argument(
        "--once", action="store_true", help="do what is ready now, then exit, rather than wait"
    )
    ap.add_argument(
        "--retry-failed",
        action="store_true",
        help="give reels that failed too often at these stages another go first",
    )
    ap.add_argument(
        "--name",
        default=settings.worker_name,
        help=f"this worker's name in status (default {settings.worker_name}, "
        "from HIKIDASHI_WORKER_NAME, else the host's name)",
    )
    ap.add_argument(
        "--log",
        default=logs.WORKER_FILE,
        metavar="FILE",
        help=f"its log file in HIKIDASHI_LOGS (default {logs.WORKER_FILE}); each worker "
        "sharing that folder needs its own",
    )
    ap.add_argument(
        "--server",
        default=settings.server,
        metavar="URL",
        help="work for the Hikidashi server at this address, as https://reels.example.com, "
        "rather than the database here (default from HIKIDASHI_SERVER)",
    )
    ap.add_argument(
        "--profile",
        choices=CHOICES,
        default=settings.profile,
        help=f"the models and device to run them on (default {settings.profile}, from "
        "HIKIDASHI_PROFILE); auto picks cuda when a GPU is visible, cuda insists on one",
    )
    ap.add_argument(
        "--ollama-url",
        default=settings.ollama_url,
        metavar="URL",
        help=f"the Ollama that tags and finds recipes (default {settings.ollama_url}, "
        "from OLLAMA_URL)",
    )
    args = ap.parse_args(argv)

    try:
        stages = parse_stages(args.stages) if args.stages is not None else settings.local_stages
    except ValueError as exc:
        print(f"  {exc}", file=sys.stderr)
        return 2
    if args.server:
        if args.retry_failed:
            print("  --retry-failed is for the server: run it there", file=sys.stderr)
            return 2
        token = settings.worker_token or _ask_token(args.server) or ""
        if not token:
            print(
                "  set HIKIDASHI_WORKER_TOKEN to a worker token from the server's Pipeline "
                "page (or `hikidashi token make <name> --worker` there)",
                file=sys.stderr,
            )
            return 2
        server_only = [s for s in stages if s not in REMOTE]
        if args.stages is not None and server_only:
            print(
                f"  {', '.join(server_only)} run on the server; a remote worker runs some "
                f"of {', '.join(REMOTE)}",
                file=sys.stderr,
            )
            return 2
        stages = tuple(s for s in stages if s in REMOTE)

    logging.basicConfig(level=logging.INFO, format=logs.FORMAT, stream=sys.stdout)
    # A line a request as models download, and two a reel about Whisper's VAD
    # filter: the worker's own lines say enough.
    for noisy in ("faster_whisper", "httpx", "huggingface_hub"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    settings.ensure_dirs()
    logs.install(settings.logs_dir, Path(args.log).name)

    try:
        if args.server:
            worker = _remote(args.server, token, args.name, stages, args.profile, args.ollama_url)
        else:
            worker = _local(
                args.name, stages, args.profile, args.ollama_url, retry_failed=args.retry_failed
            )

        signal.signal(signal.SIGTERM, _stop)
        # Behind the server for the CPU: transcribing a backlog should never
        # make a page slow to load. Containers also cap it, with `cpus:`.
        os.nice(10)
        worker.start()
    except Refused as exc:
        print(f"  the server refused this worker: {exc}", file=sys.stderr)
        return 1
    started = time.monotonic()
    try:
        if args.once:
            n = worker.drain()
            worker.stop()
            print(f"\n  worked on {n} reels in {time.monotonic() - started:,.0f} s")
            for stage, why in worker.problems.items():
                print(f"  {stage} could not run: {why}", file=sys.stderr)
            return 1 if worker.problems else 0
        hosts = _hosted_apis(stages)  # the switched-on APIs, on the server's own worker
        try:
            worker.run()  # until stopped
        finally:
            if hosts:
                hosts.stop()
        return 0
    except Refused as exc:  # a server upgraded under it, say: retrying cannot help
        worker.stop()
        print(f"  the server refused this worker: {exc}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, Stop):
        worker.stop()  # the hosted APIs' reels go back in the finally above
        logging.getLogger("hikidashi.worker").info("stopped; reels in hand given back")
        logging.shutdown()
        # Do not wait for a transcription still in flight: every finished reel
        # is committed, and the lease of the one in hand is already given back.
        os._exit(0)


def _ask_token(server: str) -> str | None:
    """The worker token, typed at the terminal with nothing echoed, or None
    with no terminal to ask at (Docker, a service) or nothing typed."""
    if not sys.stdin.isatty():
        return None
    try:
        return getpass.getpass(f"  worker token for {server}: ").strip() or None
    except (EOFError, KeyboardInterrupt):
        print(file=sys.stderr)
        return None


def _hosted_apis(stages: tuple[str, ...]) -> Host | None:
    """The hosted APIs (#125) run in the worker that runs enrich: the
    server's own, on the database's host. Started whether or not any switch
    is on, since a switch goes on from the UI while it runs."""
    if "enrich" not in stages:
        return None
    from app.ai.runner import Host

    host = Host(lambda: connect(settings.db_path), settings.db_path.parent, settings.media_dir)
    host.start()
    return host


def _local(
    name: str, stages: tuple[str, ...], profile: str, ollama_url: str, *, retry_failed: bool
) -> Worker:
    conn = open_db(settings.db_path)
    if retry_failed:
        print(f"  {retry(conn, stages)} failed reels will be tried again", flush=True)
    runners = Runners(
        media_dir=settings.media_dir,
        models_dir=settings.models_dir,
        ollama_url=ollama_url,
        profile=profile,
        unload_after=settings.unload_after,
    )
    return Worker(
        LocalJobs(conn, name),
        conn,
        runners,
        stages=stages,
        run_dir=settings.run_dir,
        beat=LocalJobs(connect(settings.db_path), name),
    )


def _remote(
    server: str, token: str, name: str, stages: tuple[str, ...], profile: str, ollama_url: str
) -> Worker:
    """A worker for the server at `server`: no database here, and the media
    it reads downloaded into the cache."""
    jobs = HttpJobs(server, token, name)
    logging.getLogger("hikidashi.worker").info("working for %s", jobs.server)
    wait_for(jobs)
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    runners = RemoteRunners(
        media_dir=settings.cache_dir,
        models_dir=settings.models_dir,
        ollama_url=ollama_url,
        profile=profile,
        unload_after=settings.unload_after,
        jobs=jobs,
    )
    return Worker(
        jobs,
        None,
        runners,
        stages=stages,
        run_dir=settings.run_dir,
        beat=HttpJobs(server, token, name),
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
