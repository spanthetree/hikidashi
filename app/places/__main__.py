"""CLI: python -m app.places [--limit N] [--force] [--offline]

Finds the places captions name, and puts them on the map. Reading them is
free and offline; looking them up asks the Nominatim server HIKIDASHI_GEOCODER
names, and is skipped when it names none. Safe to interrupt: each place commits
as it is found, so re-running resumes where it stopped.
"""

from __future__ import annotations

import argparse
import sys
import time

from app.config import settings
from app.db import open_db

from .geocoder import GeocodeError, Nominatim
from .service import Busy, places_lock
from .worker import Progress, locate_all, pending, read_all, refine, summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.places", description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, help="look up at most this many places")
    ap.add_argument(
        "--force", action="store_true", help="read every caption and look every place up again"
    )
    ap.add_argument(
        "--offline", action="store_true", help="only read places from captions; look nothing up"
    )
    args = ap.parse_args(argv)

    settings.ensure_dirs()
    conn = open_db(settings.db_path)

    read = read_all(conn, force=args.force)
    before = summary(conn)
    print(f"  read {read} captions · {before['reels']} reels name {before['places']} places")
    print(
        f"  {before['located']} on the map · {before['approximate']} approximate · "
        f"{before['missing']} not found · {before['waiting']} waiting"
    )

    if args.offline:
        return 0
    if not settings.geocoder:
        print(
            "\n  no geocoder: set HIKIDASHI_GEOCODER to a Nominatim server to put "
            "places on the map\n  (HIKIDASHI_GEOCODER=public uses OpenStreetMap's, "
            "one place a second)"
        )
        return 0
    todo = len(pending(conn, limit=args.limit, force=args.force))
    print(f"\n  {todo} to look up at {settings.geocoder}\n", flush=True)

    geocoder = Nominatim(settings.geocoder, delay=settings.geocoder_delay)
    started = time.monotonic()
    last = [0.0]

    def tick(p: Progress) -> None:
        done = p.located + p.approximate + p.missing
        if time.monotonic() - last[0] >= 5 or done == p.total:
            last[0] = time.monotonic()
            print(f"  {p.line()}", flush=True)

    try:
        with places_lock(settings.run_dir):
            result = locate_all(
                conn, geocoder.search, limit=args.limit, force=args.force, on_progress=tick
            )
            print("  a second look at places far from the rest of their reel", flush=True)
            second = refine(conn, geocoder.search)
    except Busy:
        print(
            "  the server is looking places up now, and will finish them; "
            "hikidashi status shows how far it has got"
        )
        return 0
    except KeyboardInterrupt:
        print("\n  interrupted — places found so far are saved, re-run to resume")
        return 130
    except GeocodeError as exc:
        print(
            f"\n  stopped: {exc}\n  places found so far are saved, re-run to resume",
            file=sys.stderr,
        )
        return 1

    elapsed = time.monotonic() - started
    after = summary(conn)
    print(f"\n  located     {result.located}")
    print(f"  approximate {result.approximate}  <- too unsure to put on the map")
    print(f"  not found   {result.missing}")
    print(
        f"  second look {second.moved} moved nearer the rest of their reel · "
        f"{second.doubted} taken off the map"
    )
    print(f"  time        {elapsed / 60:,.1f} min")
    print(f"\n  library     {after['located']}/{after['places']} places on the map")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
