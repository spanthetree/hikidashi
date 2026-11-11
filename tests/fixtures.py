"""Synthetic export fixtures.

`saved_posts` mirrors the shape Instagram actually ships (October 2026);
`legacy_saved_posts` mirrors the older documented shape, which the parser still
accepts. Both are exercised by the test suite.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

BASE = "your_instagram_activity/saved/"


def must[T](value: T | None) -> T:
    """`value`, which the test expects to be there. Fails the test, with this
    message rather than an AttributeError on None, when it is not."""
    assert value is not None, "expected a value, got None"
    return value


def post_record(
    href: str,
    *,
    timestamp: int,
    caption: str | None = None,
    username: str | None = None,
    name: str | None = None,
    hashtags: tuple[str, ...] = (),
    fbid: str = "17900000000000000",
    extra_caption: str | None = None,
) -> dict[str, Any]:
    """One record in the label_values shape."""
    owner = []
    if username:
        owner.append({"label": "Username", "value": username})
    if name:
        owner.append({"label": "Name", "value": name})

    label_values: list[dict[str, Any]] = [{"label": "URL", "value": href, "href": href}]
    if caption is not None:
        label_values.append({"label": "Caption", "value": caption})
        label_values.append({"label": "Title", "value": ""})
    if extra_caption is not None:
        label_values.append({"label": "Caption", "value": extra_caption})
        label_values.append({"label": "Title", "value": ""})

    label_values.append(
        {
            "title": "Hashtags",
            "dict": [{"dict": [{"label": "Name", "value": h}], "title": ""} for h in hashtags],
        }
    )
    label_values.append({"title": "Owner", "dict": [{"dict": owner, "title": ""}] if owner else []})
    label_values.append({"title": "Brand partner", "dict": []})

    return {"fbid": fbid, "timestamp": timestamp, "media": [], "label_values": label_values}


def saved_posts(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The file is a bare list at the top level."""
    return records


def legacy_saved_posts(entries: list[tuple[str | None, str, int]]) -> dict[str, Any]:
    """The older documented shape: (author, permalink, saved_at)."""
    return {
        "saved_saved_media": [
            {"title": author, "string_map_data": {"Saved on": {"href": href, "timestamp": ts}}}
            for author, href, ts in entries
        ]
    }


def saved_collections(groups: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Collections embed the full record of each member, as the real file does."""
    return [
        {
            "timestamp": 1705712802,
            "media": [],
            "label_values": [
                {"label": "Name", "value": name},
                {"label": "Type", "value": "Default"},
                {"label": "Update time", "timestamp_value": 1767189934},
                {
                    "title": "Media",
                    "dict": [{"title": "", "dict": r["label_values"]} for r in records],
                },
            ],
        }
        for name, records in groups.items()
    ]


def write_zip(path: Path, posts, collections=None) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(BASE + "saved_posts.json", json.dumps(posts))
        if collections is not None:
            zf.writestr(BASE + "saved_collections.json", json.dumps(collections))
    return path
