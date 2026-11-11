"""Shortcode extraction.

The shortcode is the primary key for the whole system: dedupe, incremental
re-import, retrying a failed stage and never losing a hand-written tag all
depend on one stable identifier per reel.
"""

from __future__ import annotations

import re

# instagram.com/{p,reel,reels,tv}/<shortcode>/ with optional www, locale prefix,
# query string or missing trailing slash.
_PERMALINK = re.compile(
    r"instagram\.com/"
    r"(?:[a-z]{2}(?:[-_][a-zA-Z]{2,4})?/)?"  # optional /en/ or /en_GB/ locale
    r"(?:p|reel|reels|tv)/"
    r"([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)


def extract(url: str | None) -> str | None:
    """Return the shortcode in `url`, or None if it is not a post permalink."""
    if not url:
        return None
    m = _PERMALINK.search(url)
    return m.group(1) if m else None


def canonical_permalink(shortcode: str) -> str:
    return f"https://www.instagram.com/reel/{shortcode}/"


def is_permalink(url: str | None) -> bool:
    return extract(url) is not None
