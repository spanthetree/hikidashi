"""CLI: python -m app.importer [--user NAME] data/imports/instagram-export.zip

Into the library of the account named by --user, by default the library's
owner (user 1)."""

from __future__ import annotations

import sys
from pathlib import Path

from app import accounts, jobs
from app.config import settings
from app.db import open_db

from .service import ImportError_, import_export


def main(argv: list[str]) -> int:
    args = argv[1:]
    username = None
    if len(args) == 3 and args[0] == "--user":
        username, args = args[1], args[2:]
    if len(args) != 1:
        print(__doc__.strip(), file=sys.stderr)
        return 2

    source = Path(args[0])
    if not source.exists():
        print(f"not found: {source}", file=sys.stderr)
        return 1

    settings.ensure_dirs()
    conn = open_db(settings.db_path)
    user = accounts.by_name(conn, username) if username else accounts.local(conn)
    if user is None:
        print(f"no account called {username}; see `hikidashi user list`", file=sys.stderr)
        return 1
    try:
        stats = import_export(conn, source, user_id=user.id)
    except ImportError_ as exc:
        print(f"could not read export: {exc}", file=sys.stderr)
        return 1

    if stats.already_imported:
        print("note: this exact export has been imported before\n")
    elif stats.inserted:
        jobs.wake(settings.run_dir)  # a worker picks them up now, not at its next poll

    print(f"  parser      {stats.strategy}")
    print(f"  seen        {stats.seen}")
    print(f"  inserted    {stats.inserted}")
    print(f"  updated     {stats.updated}")
    print(f"  unchanged   {stats.unchanged}")
    if stats.unparsed:
        print(f"  unparsed    {stats.unparsed}  <- entries with no usable permalink")
    print(
        f"  collections {stats.collections} new memberships "
        f"across {len(stats.collection_names)} collections"
    )
    if stats.collection_names:
        print("              " + ", ".join(stats.collection_names[:12]))
    print(f"\n  library     {user.username}")
    print(f"  database    {settings.db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
