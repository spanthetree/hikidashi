"""CLI: hikidashi export <format> [--user NAME] OUT

Take a library elsewhere. Each format reads the library of the account named
by --user, by default the library's owner (user 1), and writes OUT.

  recipes   schema.org JSON-LD: a folder of one file per recipe, or with a
            .json OUT, one file of them all. --format nextcloud writes a ZIP
            Mealie and Tandoor import; --format paprika, a .paprikarecipes.
  markdown  an Obsidian vault: a note per reel, and notes for each tag, author
            and collection that link them. Run it again on the same folder to
            bring it up to date: what you write below a note's marker line is
            kept.
  json      every reel, as a JSON list, for scripting. OUT - is stdout.
  csv       the same, one row per reel.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from app import accounts
from app.accounts import User
from app.config import settings
from app.db import open_db

from . import library, recipes_of
from . import recipes as recipe_formats


def _user(conn: sqlite3.Connection, name: str | None) -> User | None:
    return accounts.by_name(conn, name) if name else accounts.local(conn)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="hikidashi export",
        description=__doc__.splitlines()[2],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="what", required=True)

    def command(name: str, help_: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_, description=help_)
        p.add_argument("--user", metavar="NAME", help="whose library (default: its owner)")
        return p

    rec = command("recipes", "recipes as schema.org JSON-LD, or for Mealie, Tandoor and Paprika")
    rec.add_argument(
        "--format",
        choices=("jsonld", "nextcloud", "paprika"),
        default="jsonld",
        help="jsonld (default): a folder of .json files, or one .json; nextcloud: a "
        "ZIP Mealie and Tandoor import; paprika: a .paprikarecipes file",
    )
    rec.add_argument("out", type=Path, help="where to write it")

    md = command("markdown", "the library as Markdown notes, an Obsidian vault")
    md.add_argument("--thumbs", action="store_true", help="copy thumbnails into the vault")
    md.add_argument(
        "--with-media", action="store_true", help="copy videos into the vault too, and thumbnails"
    )
    md.add_argument(
        "--link",
        action="store_true",
        help="symlink thumbnails and videos rather than copy them (these point "
        "into HIKIDASHI_MEDIA, so under Docker they work only inside the container)",
    )
    md.add_argument("out", type=Path, help="the vault's folder; made if need be")

    for name, what in (("json", "the library as a JSON list"), ("csv", "the library as CSV")):
        command(name, what).add_argument(
            "out", type=Path, help="the file to write, or - for stdout"
        )

    args = ap.parse_args(argv)
    settings.ensure_dirs()
    conn = open_db(settings.db_path)
    user = _user(conn, args.user)
    if user is None:
        print(f"no account called {args.user}; see `hikidashi user list`", file=sys.stderr)
        return 1
    reels = library(conn, user.id)
    {"recipes": _recipes, "markdown": _markdown, "json": _table, "csv": _table}[args.what](
        args, user, reels
    )
    return 0


def _recipes(args: argparse.Namespace, user: User, reels: list[dict[str, Any]]) -> None:
    out: Path = args.out
    found = recipes_of(reels)
    if args.format == "jsonld" and out.suffix.lower() == ".json":
        doc = recipe_formats.graph(found, base_url=settings.origin)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif args.format == "jsonld":
        recipe_formats.write_files(found, out, base_url=settings.origin)
    else:
        if out.is_dir():
            out = out / f"hikidashi-{user.username}.{_suffix(args.format)}"
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("wb") as f:
            if args.format == "nextcloud":
                recipe_formats.nextcloud(found, settings.media_dir, f, base_url=settings.origin)
            else:
                recipe_formats.paprika(found, settings.media_dir, f)
    n = len(found)
    print(f"  {n} recipe{'' if n == 1 else 's'} from {user.username}'s library -> {out}")


def _markdown(args: argparse.Namespace, user: User, reels: list[dict[str, Any]]) -> None:
    from .markdown import MARKER, write

    result = write(
        reels,
        args.out,
        settings.media_dir,
        thumbs=args.thumbs or args.with_media,
        videos=args.with_media,
        symlink=args.link,
    )
    print(f"  {len(reels)} reels from {user.username}'s library -> {args.out}")
    print(f"  written     {len(result.written)} notes new or changed")
    print(f"  unchanged   {result.unchanged}")
    if result.kept:
        print(f"  kept        your own notes in {result.kept}")
    if result.media:
        print(f"  media       {result.media} files {'linked' if args.link else 'copied'}")
    for path in result.skipped:
        print(f"  left alone  {path}: its marker line is gone")
    if result.skipped:
        print(f"              (put back {MARKER!r} to have it updated)")
    if result.stale:
        print(f"  left alone  {len(result.stale)} notes for reels no longer in the library")


def _table(args: argparse.Namespace, user: User, reels: list[dict[str, Any]]) -> None:
    from .table import write_csv, write_json

    writer = write_json if args.what == "json" else write_csv
    out: Path = args.out
    if str(out) == "-":
        writer(reels, sys.stdout)
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    # newline="": the csv module writes its own line endings.
    with out.open("w", encoding="utf-8", newline="") as f:
        writer(reels, f)
    print(f"  {len(reels)} reels from {user.username}'s library -> {out}")


def _suffix(fmt: str) -> str:
    return {"nextcloud": "zip", "paprika": "paprikarecipes"}[fmt]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
