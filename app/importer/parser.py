"""Parse Instagram's saved-posts export.

The real export (October 2026) is a bare list of records shaped like::

    {
      "fbid": "17923695984186502",
      "timestamp": 1792327526,
      "media": [],
      "label_values": [
        {"label": "URL", "value": "...", "href": "https://instagram.com/reel/ABC/"},
        {"label": "Caption", "value": "..."},
        {"label": "Title", "value": ""},
        {"title": "Hashtags",      "dict": [{"dict": [{"label": "Name", ...}]}, ...]},
        {"title": "Owner",         "dict": [{"dict": [{"label": "Username", ...}]}]},
        {"title": "Brand partner", "dict": []}
      ]
    }

Written tolerantly on purpose: Meta has already reshaped this file once, so the
real-shape parser runs first, an older documented shape second, and a generic
walker last. A renamed key degrades the import instead of breaking it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from .shortcode import extract

# Keys Meta has used to wrap the list of saved posts.
_POST_LIST_KEYS = ("saved_saved_media", "saved_media", "saved_posts")
_COLLECTION_LIST_KEYS = ("saved_saved_collections", "saved_collections")


def fix_mojibake(text: str | None) -> str | None:
    """Undo the latin-1/UTF-8 double encoding Instagram exports are full of.

    Meta writes UTF-8 bytes escaped as latin-1 code points, so an emoji arrives
    as "\\u00e2\\u009c\\u00a8" and "café" as "cafÃ©". Round-tripping restores
    them; text that was already clean fails the round-trip and is returned
    untouched.
    """
    if not text:
        return text
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


@dataclass(frozen=True)
class SavedPost:
    shortcode: str
    permalink: str
    author: str | None = None  # the @handle
    author_name: str | None = None  # display name
    saved_at: int | None = None
    caption: str | None = None
    hashtags: list[str] = field(default_factory=list)
    fbid: str | None = None
    raw: dict[str, Any] | None = None

    def raw_json(self) -> str | None:
        return json.dumps(self.raw, ensure_ascii=False) if self.raw else None


@dataclass
class ParseResult:
    posts: list[SavedPost]
    strategy: str
    unparsed: int


# --------------------------------------------------------------------------
# generic helpers
# --------------------------------------------------------------------------


def _iter_dicts(node: Any) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _iter_dicts(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_dicts(v)


def _find_list(data: Any, keys: tuple[str, ...]) -> list[Any] | None:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in keys:
            found = data.get(k)
            if isinstance(found, list):
                return found
    return None


# --------------------------------------------------------------------------
# label_values shape (the one Instagram actually ships)
# --------------------------------------------------------------------------


def _labelled(entries: list[Any], label: str) -> list[str]:
    """Every non-empty `value` carrying this `label`, at this level only."""
    return [
        e["value"]
        for e in entries
        if isinstance(e, dict)
        and e.get("label") == label
        and isinstance(e.get("value"), str)
        and e["value"]
    ]


def _grouped(entries: list[Any], title: str) -> list[dict[str, Any]]:
    """The inner label/value dicts of the group named `title`.

    Groups nest as {"title": "Owner", "dict": [{"dict": [{label, value}, ...]}]}.
    """
    out: list[dict[str, Any]] = []
    for e in entries:
        if not isinstance(e, dict) or e.get("title") != title:
            continue
        for sub in e.get("dict") or []:
            if isinstance(sub, dict):
                out.extend(d for d in (sub.get("dict") or []) if isinstance(d, dict))
    return out


def _group_values(entries: list[Any], title: str, label: str) -> list[str]:
    return [
        d["value"]
        for d in _grouped(entries, title)
        if d.get("label") == label and isinstance(d.get("value"), str) and d["value"]
    ]


def parse_record(record: Any) -> SavedPost | None:
    """Parse one saved-post record in the label_values shape."""
    if not isinstance(record, dict):
        return None
    # saved_posts.json keys the field list "label_values"; the same record
    # embedded inside a collection keys it "dict". Same content either way.
    entries = record.get("label_values")
    if not isinstance(entries, list):
        entries = record.get("dict")
    if not isinstance(entries, list):
        return None

    href = next(
        (
            e.get("href") or e.get("value")
            for e in entries
            if isinstance(e, dict)
            and e.get("label") == "URL"
            and extract(e.get("href") or e.get("value"))
        ),
        None,
    )
    code = extract(href)
    if not code:
        return None

    # A handful of records carry two Caption entries — near-identical variants.
    # The longest is the most complete; ties fall back to document order.
    captions = [fix_mojibake(c) for c in _labelled(entries, "Caption")]
    caption = max(captions, key=lambda c: len(c or "")) if captions else None

    owner_handles = _group_values(entries, "Owner", "Username")
    owner_names = _group_values(entries, "Owner", "Name")

    ts = record.get("timestamp")

    return SavedPost(
        shortcode=code,
        permalink=href,  # type: ignore[arg-type]
        author=fix_mojibake(owner_handles[0]) if owner_handles else None,
        author_name=fix_mojibake(owner_names[0]) if owner_names else None,
        saved_at=ts if isinstance(ts, int) else None,
        caption=caption,
        hashtags=[
            h for h in (fix_mojibake(x) for x in _group_values(entries, "Hashtags", "Name")) if h
        ],
        fbid=record.get("fbid") if isinstance(record.get("fbid"), str) else None,
        raw=record,
    )


# --------------------------------------------------------------------------
# older documented shape: {"saved_saved_media": [{title, string_map_data}]}
# --------------------------------------------------------------------------


def _parse_string_map_record(entry: Any) -> SavedPost | None:
    if not isinstance(entry, dict) or "string_map_data" not in entry:
        return None
    for d in _iter_dicts(entry.get("string_map_data")):
        href = d.get("href") or d.get("value")
        code = extract(href) if isinstance(href, str) else None
        if code:
            ts = d.get("timestamp")
            return SavedPost(
                shortcode=code,
                permalink=href,  # type: ignore[arg-type]
                author=fix_mojibake(entry.get("title")) or None,
                saved_at=ts if isinstance(ts, int) else None,
                raw=entry,
            )
    return None


# --------------------------------------------------------------------------
# entry points
# --------------------------------------------------------------------------


def parse_saved_posts(data: Any) -> ParseResult:
    """Parse saved_posts.json into SavedPost records."""
    entries = _find_list(data, _POST_LIST_KEYS)

    if entries is not None:
        for strategy, fn in (
            ("label-values", parse_record),
            ("string-map", _parse_string_map_record),
        ):
            posts, unparsed = [], 0
            for entry in entries:
                post = fn(entry)
                if post:
                    posts.append(post)
                else:
                    unparsed += 1
            if posts:
                return ParseResult(posts, strategy, unparsed)

    # Last resort: sweep the document for anything permalink-shaped. Loses the
    # per-record fields, but keeps the library importable through a reshape.
    seen: dict[str, SavedPost] = {}
    for d in _iter_dicts(data):
        href = d.get("href") or d.get("value")
        if not isinstance(href, str):
            continue
        code = extract(href)
        if not code or code in seen:
            continue
        ts = d.get("timestamp")
        seen[code] = SavedPost(code, href, saved_at=ts if isinstance(ts, int) else None)
    return ParseResult(list(seen.values()), "generic-walk", 0)


def parse_collections(data: Any) -> tuple[list[tuple[str, str]], set[str], list[SavedPost]]:
    """Parse saved_collections.json.

    Collections embed the full record of every post in them, so this returns
    membership pairs, every collection name seen (including empty ones), and the
    embedded posts — which is how a reel that is in a collection but missing
    from saved_posts.json still arrives with its caption and creator.
    """
    entries = _find_list(data, _COLLECTION_LIST_KEYS) or []
    pairs: list[tuple[str, str]] = []
    names: set[str] = set()
    posts: dict[str, SavedPost] = {}

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        name = None
        lv = entry.get("label_values")
        if isinstance(lv, list):
            found = _labelled(lv, "Name")
            name = found[0] if found else None
        if not name:
            name = entry.get("title")
        if not name and isinstance(entry.get("string_map_data"), dict):
            for key in ("Name", "Collection Name", "name"):
                f = entry["string_map_data"].get(key)
                if isinstance(f, dict) and isinstance(f.get("value"), str):
                    name = f["value"]
                    break
        name = fix_mojibake(name) if isinstance(name, str) else None
        if not name:
            continue
        names.add(name)

        # Nested post records first, so members arrive with their metadata.
        for d in _iter_dicts(entry):
            post = parse_record(d)
            if post:
                posts.setdefault(post.shortcode, post)
                pairs.append((name, post.shortcode))

        # Then any bare URL the record walk did not already account for.
        claimed = {code for n, code in pairs if n == name}
        for d in _iter_dicts(entry):
            href = d.get("href") or d.get("value")
            if isinstance(href, str):
                code = extract(href)
                if code and code not in claimed:
                    claimed.add(code)
                    pairs.append((name, code))

    return pairs, names, list(posts.values())
