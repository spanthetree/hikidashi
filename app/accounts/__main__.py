"""CLI: hikidashi user <command>

Accounts, from the host. `passwd local` is how the library's owner claims it
before turning on HIKIDASHI_AUTH=local; everyone else is invited.

A password is asked for twice at a terminal, or read as one line from stdin.
"""

from __future__ import annotations

import argparse
import getpass
import sys
import time

from app.config import settings
from app.db import open_db

from . import AccountError, create, everyone, first_admin, invite, rename, set_password


def read_password() -> str:
    if not sys.stdin.isatty():
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("password: ")
    if getpass.getpass("again: ") != first:
        raise AccountError("The two passwords differ.")
    return first


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hikidashi user", description=__doc__.splitlines()[2])
    sub = ap.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="make an account; asks for its password")
    add.add_argument("username")
    add.add_argument("--admin", action="store_true", help="it can invite and manage others")
    passwd = sub.add_parser("passwd", help="set an account's password")
    passwd.add_argument("username")
    named = sub.add_parser(
        "rename",
        help="change an account's username, as for a proxy "
        "that signs the owner in under another name",
    )
    named.add_argument("username")
    named.add_argument("new")
    sub.add_parser("list", help="every account, with how many reels each has saved")
    for name, what in (
        ("disable", "stop an account signing in"),
        ("enable", "let a disabled account sign in again"),
    ):
        sub.add_parser(name, help=what).add_argument("username")
    link = sub.add_parser("invite", help="a one-time link to make an account (7 days)")
    link.add_argument(
        "--url",
        default=f"http://localhost:{settings.port}",
        help="where people reach this server (default: %(default)s)",
    )
    args = ap.parse_args(argv)

    settings.ensure_dirs()
    conn = open_db(settings.db_path)
    try:
        if args.command == "list":
            for u in everyone(conn):
                flags = [
                    f
                    for f, on in (
                        ("admin", u["is_admin"]),
                        ("disabled", u["disabled"]),
                        ("no password", not u["has_password"]),
                    )
                    if on
                ]
                handle = f"@{u['ig_handle']}" if u["ig_handle"] else ""
                print(
                    f"  {u['id']:>3}  {u['username']:<24} {handle:<20} "
                    f"{u['reels']:>5} reels  {', '.join(flags)}"
                )
            return 0
        if args.command == "invite":
            by = first_admin(conn)
            if by is None:
                raise AccountError(
                    "There is no admin to invite on behalf of: claim the "
                    "library first, with `hikidashi user passwd local`."
                )
            token, expires = invite(conn, by)
            print(f"{args.url.rstrip('/')}/invite/{token}")
            print(
                f"  one use, until {time.strftime('%Y-%m-%d %H:%M', time.localtime(expires))}",
                file=sys.stderr,
            )
            return 0
        if args.command == "add":
            user = create(conn, args.username, read_password(), is_admin=args.admin)
            print(f"made {user.username}{' (admin)' if user.is_admin else ''}")
            return 0

        row = conn.execute("SELECT id FROM user WHERE username = ?", (args.username,)).fetchone()
        if row is None:
            raise AccountError(f"There is no account called {args.username}.")
        if args.command == "passwd":
            set_password(conn, row["id"], read_password())
            print(f"password set for {args.username}")
        elif args.command == "rename":
            rename(conn, row["id"], args.new)
            print(f"{args.username} is {args.new} now")
        else:
            conn.execute(
                "UPDATE user SET disabled = ? WHERE id = ?",
                (int(args.command == "disable"), row["id"]),
            )
            if args.command == "disable":
                conn.execute("DELETE FROM session WHERE user_id = ?", (row["id"],))
                conn.execute("DELETE FROM api_token WHERE user_id = ?", (row["id"],))
            print(f"{args.command}d {args.username}")
        return 0
    except AccountError as exc:
        print(f"hikidashi user: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
