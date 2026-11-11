"""CLI: hikidashi token <command>

API and worker tokens, from the host, for a server with no browser at hand.

An API token is a person's, for scripts reading or changing their library:
it has the scopes it is given and lives 30 days at most. A worker token is an
admin's, for a worker on another machine, and lasts until it is revoked. The
token is printed once, alone on stdout; nothing keeps it but its hash.
"""

from __future__ import annotations

import argparse
import sys
import time

from app.accounts import (
    SCOPES,
    TOKEN_DAYS,
    WORKER,
    AccountError,
    User,
    by_name,
    first_admin,
    make_token,
    make_worker_token,
    revoke_token,
    tokens,
)
from app.config import settings
from app.db import open_db


def when(at: int | None, never: str) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(at)) if at else never


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hikidashi token", description=__doc__.splitlines()[2])
    sub = ap.add_subparsers(dest="command", required=True)
    make = sub.add_parser("make", help="make a token, and print it this once")
    make.add_argument("name", help='what it is for, as "monitor" or "desktop"')
    make.add_argument("--user", help="whose it is (default: the first admin)")
    make.add_argument(
        "--scope",
        action="append",
        choices=SCOPES,
        help="what it may do; give it again for more (default: read:library)",
    )
    make.add_argument(
        "--days",
        type=int,
        default=TOKEN_DAYS,
        help=f"how long it lives, 1 to {TOKEN_DAYS} (default: %(default)s)",
    )
    make.add_argument(
        "--worker",
        action="store_true",
        help="a worker token, for a worker on another machine: an admin's, "
        "with no scopes, that lasts until revoked",
    )
    listed = sub.add_parser("list", help="every live token, never the tokens themselves")
    listed.add_argument("--user", help="only this person's")
    sub.add_parser("revoke", help="revoke a token by the id `list` shows").add_argument(
        "id", type=int
    )
    args = ap.parse_args(argv)

    settings.ensure_dirs()
    conn = open_db(settings.db_path)
    try:

        def owner(name: str | None) -> User:
            user = by_name(conn, name) if name else first_admin(conn)
            if user is None:
                raise AccountError(
                    f"There is no account called {name}."
                    if name
                    else "There is no admin yet: claim the library first, with "
                    "`hikidashi user passwd local`."
                )
            return user

        if args.command == "make":
            user = owner(args.user)
            if args.worker:
                if args.scope or args.days != TOKEN_DAYS:
                    raise AccountError("A worker token has no scopes and does not expire.")
                token, _ = make_worker_token(conn, user, args.name)
                note = "a worker token; set it as HIKIDASHI_WORKER_TOKEN on the worker"
            else:
                scopes = args.scope or ["read:library"]
                token, _ = make_token(conn, user, args.name, scopes, args.days)
                until = when(int(time.time()) + args.days * 86400, "")
                note = f"{' '.join(scopes)}, until {until}"
            print(token)
            print(f"  {user.username}'s, {note}", file=sys.stderr)
            return 0
        if args.command == "list":
            user_id = owner(args.user).id if args.user else None
            for t in tokens(conn, user_id=user_id):
                scopes = "worker" if t["kind"] == WORKER else " ".join(t["scopes"])
                print(
                    f"  {t['id']:>3}  {t['owner']:<16} {t['name']:<20} {scopes:<40} "
                    f"until {when(t['expires_at'], 'revoked'):<16}  "
                    f"used {when(t['last_used_at'], 'never')}"
                )
            return 0
        if not revoke_token(conn, args.id, kind=None):
            raise AccountError(f"There is no token {args.id}.")
        print(f"revoked token {args.id}")
        return 0
    except AccountError as exc:
        print(f"hikidashi token: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
