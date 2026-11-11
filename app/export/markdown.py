"""The library as Markdown, laid out as an Obsidian vault.

    reels/<shortcode>.md       one note per reel
    tags/<tag>.md              an index note per tag, author and collection,
    authors/@<handle>.md       which the reels link to, so the graph shows
    collections/<name>.md      how saves connect
    media/                     thumbnails and videos, when asked for
    Hikidashi.md               where to start

A reel's note is named for its shortcode, which never changes, so a re-run
finds the same note and a link you made to it keeps working. Its title is in
`aliases`, for Obsidian's search and link suggestions.

Every note ends in a marker line. A re-run rewrites what is above it and keeps
what you wrote below it. A note whose marker is gone is left alone, since there
is no telling which part is yours. Notes for reels no longer in the library are
left alone too: they may hold your notes.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import ingredient_line, iso_date, iso_time, title

MARKER = "%% Hikidashi writes everything above this line. Your notes go below it, and are kept. %%"

# What Obsidian refuses in a file name, or reads as part of a link.
_UNSAFE_NAME = re.compile(r'[*"\\/<>:|?#^\[\]]')


@dataclass
class Result:
    written: list[str] = field(default_factory=list)  # new or changed notes
    unchanged: int = 0
    kept: int = 0  # notes with your own notes below the marker, kept
    skipped: list[str] = field(default_factory=list)  # marker gone; left alone
    stale: list[str] = field(default_factory=list)  # reels no longer in the library
    media: int = 0  # files copied or linked


def file_name(name: str) -> str:
    return " ".join(_UNSAFE_NAME.sub("-", name).split()).strip(". ") or "untitled"


def link(path: str, label: str) -> str:
    """A wiki link by path, so a tag and a collection of the same name are
    different notes. The label loses what would end the link early."""
    return f"[[{path}|{re.sub(r'[|\[\]]', '', label)}]]"


def tag_link(tag: str) -> str:
    return link(f"tags/{file_name(tag)}", tag)


def author_link(handle: str) -> str:
    return link(f"authors/@{file_name(handle)}", f"@{handle}")


def collection_link(name: str) -> str:
    return link(f"collections/{file_name(name)}", name)


def reel_link(reel: dict[str, Any]) -> str:
    return link(f"reels/{reel['shortcode']}", title(reel))


def text(value: str) -> str:
    """Caption or transcript text, so that Markdown shows it as written: a line
    that would be a heading stays a line, and "%%" hides nothing."""
    out = []
    for line in value.splitlines():
        line = line.replace("%%", "% %").replace("<", "\\<").replace("[[", "[\\[")
        if re.match(r"\s*#+\s", line):
            line = "\\" + line.lstrip()
        out.append(line)
    # Two spaces end a line inside a paragraph, as captions break them.
    return "  \n".join(out).strip()


def frontmatter(fields: dict[str, Any]) -> str:
    """YAML properties. Strings are written as JSON strings, which YAML reads
    as they are: no value can turn into a number, a date or a list by accident."""
    lines = ["---"]
    for key, value in fields.items():
        if value is None or value == []:
            continue
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines += [f"  - {json.dumps(v, ensure_ascii=False)}" for v in value]
        elif isinstance(value, bool | int | float):
            lines.append(f"{key}: {json.dumps(value)}")
        else:
            lines.append(f"{key}: {json.dumps(str(value), ensure_ascii=False)}")
    return "\n".join([*lines, "---"])


# --- media ----------------------------------------------------------------------


def _media_files(reel: dict[str, Any], *, thumbs: bool, videos: bool) -> list[tuple[str, str]]:
    """(path under HIKIDASHI_MEDIA, name in the vault's media/) for each file."""
    code = reel["shortcode"]
    out: list[tuple[str, str]] = []
    if thumbs and reel["slides"]:
        out += [(p, f"{code}-{i}{Path(p).suffix}") for i, p in enumerate(reel["slides"], 1)]
    elif thumbs and reel["thumb_path"]:
        out.append((reel["thumb_path"], f"{code}{Path(reel['thumb_path']).suffix}"))
    if videos and reel["media_path"]:
        out.append((reel["media_path"], f"{code}{Path(reel['media_path']).suffix}"))
    return out


def _place(src: Path, dest: Path, *, symlink: bool) -> bool:
    """Copy or link one file into the vault, unless it is there already."""
    if symlink:
        if dest.is_symlink() and os.readlink(dest) == str(src):
            return False
        dest.unlink(missing_ok=True)
        dest.symlink_to(src)
        return True
    if dest.is_file() and not dest.is_symlink() and dest.stat().st_size == src.stat().st_size:
        return False
    dest.unlink(missing_ok=True)
    shutil.copyfile(src, dest)
    return True


# --- notes ----------------------------------------------------------------------


def _embeds(media: list[str]) -> list[str]:
    """The video, else the thumbnail and a carousel's other slides."""
    images = [m for m in media if not m.endswith(".mp4")]
    videos = [m for m in media if m.endswith(".mp4")]
    if videos:
        return [f"![[media/{m}|320]]" for m in videos] + [""]
    if not images:
        return []
    return [f"![[media/{images[0]}|320]]"] + [f"![[media/{m}|160]]" for m in images[1:]] + [""]


def _about(reel: dict[str, Any]) -> str:
    """Who posted it, its tag and collections as links, and where it came from."""
    about = []
    if reel["author"]:
        about.append(author_link(reel["author"]))
    if reel["tag"]:
        about.append(tag_link(reel["tag"]))
    about += [collection_link(c) for c in reel["collections"]]
    about.append(f"[Instagram]({reel['permalink']})")
    if reel["saved_at"] is not None:
        about.append(f"saved {iso_date(reel['saved_at'])}")
    return " · ".join(about)


def _recipe(recipe: dict[str, Any]) -> list[str]:
    """The recipe, its ingredients as a checklist to tick off while cooking."""
    body = ["## Recipe", ""]
    facts = []
    if recipe["servings"]:
        facts.append(f"Serves {recipe['servings']}")
    if recipe["time_minutes"]:
        facts.append(f"{recipe['time_minutes']} minutes")
    if facts:
        body += [" · ".join(facts), ""]
    if recipe["incomplete"]:
        body += ["The rest of this recipe is elsewhere: the caption says where.", ""]
    if recipe["ingredients"]:
        body += ["### Ingredients", ""]
        body += [f"- [ ] {text(ingredient_line(i))}" for i in recipe["ingredients"]]
        body.append("")
    if recipe["steps"]:
        body += ["### Steps", ""]
        body += [f"{n}. {text(s)}" for n, s in enumerate(recipe["steps"], 1)]
        body.append("")
    return body


def reel_note(reel: dict[str, Any], media: list[str]) -> str:
    """One reel's note, down to the marker."""
    name = title(reel)
    props = frontmatter(
        {
            "shortcode": reel["shortcode"],
            "aliases": [name],
            "type": reel["kind"],
            "author": reel["author"],
            "tag": reel["tag"],
            "hashtags": reel["hashtags"],
            "collections": reel["collections"],
            "saved_at": iso_time(reel["saved_at"]),
            "permalink": reel["permalink"],
            "places": [p["text"] for p in reel["places"]],
            "recipe": bool(reel["recipe"]) or None,
        }
    )
    body = [props, "", f"# {text(name)}", "", *_embeds(media), _about(reel), ""]
    if reel["summary"]:
        body += ["## Summary", "", text(reel["summary"]), ""]
    if reel["recipe"]:
        body += _recipe(reel["recipe"])
    if reel["places"]:
        body += ["## Places", ""]
        body += [f"- [{text(p['text'])}]({p['url']})" for p in reel["places"]]
        body.append("")
    for heading, key in (
        ("Caption", "caption"),
        ("Transcript", "transcript"),
        ("On-screen text", "ocr_text"),
    ):
        if reel[key] and reel[key].strip():
            body += [f"## {heading}", "", text(reel[key]), ""]
    return "\n".join(body)


def index_note(kind: str, name: str, reels: list[dict[str, Any]], *, lede: str = "") -> str:
    """A tag's, author's or collection's note: every reel in it, newest first."""
    n = len(reels)
    body = [
        frontmatter({"type": kind, "reels": n}),
        "",
        f"# {text(name)}",
        "",
    ]
    if lede:
        body += [lede, ""]
    body += [f"{n} reel{'' if n == 1 else 's'}.", ""]
    for r in reels:
        extra = f" — @{r['author']}" if r["author"] and kind != "author" else ""
        body.append(f"- {reel_link(r)}{extra}")
    body.append("")
    return "\n".join(body)


def home_note(
    reels: list[dict[str, Any]],
    tags: dict[str, list[dict[str, Any]]],
    authors: dict[str, list[dict[str, Any]]],
    collections: dict[str, list[dict[str, Any]]],
) -> str:
    def most(groups: dict[str, list[dict[str, Any]]], to_link: Any) -> list[str]:
        ranked = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0].lower()))
        return [f"- {to_link(k)} ({len(v)})" for k, v in ranked]

    body = [
        frontmatter({"type": "home", "reels": len(reels)}),
        "",
        "# Hikidashi",
        "",
        f"{len(reels)} saved reels and posts, exported from Hikidashi.",
        "",
    ]
    for heading, groups, to_link in (
        ("Tags", tags, tag_link),
        ("Collections", collections, collection_link),
        ("Authors", authors, author_link),
    ):
        if groups:
            body += [f"## {heading}", "", *most(groups, to_link), ""]
    return "\n".join(body)


def _merge(path: Path, generated: str, result: Result) -> str | None:
    """The note to write: the new part, then the marker, then whatever was
    below it before. None to leave the file as it is."""
    tail = "\n"
    if path.is_file():
        old = path.read_text(encoding="utf-8")
        at = old.find(MARKER)
        if at < 0:
            result.skipped.append(str(path))
            return None
        tail = old[at + len(MARKER) :]
        if tail.strip():
            result.kept += 1
        new = f"{generated.rstrip()}\n\n{MARKER}{tail}"
        return None if new == old else new
    return f"{generated.rstrip()}\n\n{MARKER}{tail}"


def _write(out: Path, rel: str, generated: str, result: Result) -> None:
    path = out / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    note = _merge(path, generated, result)
    if note is None:
        if str(path) not in result.skipped:
            result.unchanged += 1
        return
    path.write_text(note, encoding="utf-8")
    result.written.append(rel)


def _grouped(reels: Iterable[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in reels:
        values = r[key] if isinstance(r[key], list) else [r[key]]
        for v in values:
            if v:
                groups[v].append(r)
    return dict(groups)


def write(
    reels: list[dict[str, Any]],
    out: Path,
    media_dir: Path,
    *,
    thumbs: bool = False,
    videos: bool = False,
    symlink: bool = False,
) -> Result:
    """Write the vault into `out`, or bring the one there up to date."""
    result = Result()
    root = media_dir.resolve()
    (out / "reels").mkdir(parents=True, exist_ok=True)
    for reel in reels:
        placed = []
        for rel, name in _media_files(reel, thumbs=thumbs, videos=videos):
            src = (root / rel).resolve()
            if not src.is_relative_to(root) or not src.is_file():
                continue
            (out / "media").mkdir(exist_ok=True)
            if _place(src, out / "media" / name, symlink=symlink):
                result.media += 1
            placed.append(name)
        _write(out, f"reels/{reel['shortcode']}.md", reel_note(reel, placed), result)

    tags = _grouped(reels, "tag")
    authors = _grouped(reels, "author")
    collections = _grouped(reels, "collections")
    for name, group in tags.items():
        _write(out, f"tags/{file_name(name)}.md", index_note("tag", name, group), result)
    for handle, group in authors.items():
        lede = f"[Instagram](https://www.instagram.com/{handle}/)"
        _write(
            out,
            f"authors/@{file_name(handle)}.md",
            index_note("author", f"@{handle}", group, lede=lede),
            result,
        )
    for name, group in collections.items():
        _write(
            out,
            f"collections/{file_name(name)}.md",
            index_note("collection", name, group, lede="An Instagram collection."),
            result,
        )
    _write(out, "Hikidashi.md", home_note(reels, tags, authors, collections), result)

    known = {f"{r['shortcode']}.md" for r in reels}
    result.stale = sorted(p.name for p in (out / "reels").glob("*.md") if p.name not in known)
    return result
