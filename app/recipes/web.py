"""Where the rest of an incomplete recipe is, and fetching it from there (#114).

`link` reads it from the caption, with no request: a URL (a recipe page, or a
site's home page), "link in bio", or "comment KEYWORD" when the recipe is sent
by DM and nothing can be fetched.

`fetch` gets a recipe page and `parse` reads the schema.org Recipe that most
recipe blogs embed as JSON-LD for search engines: title, servings, time,
ingredients and steps. It is stored as the reel's `web` recipe, which stands in
for the model's incomplete one (app/recipes/service.py).

Fetching sends one request to the site the caption names, from the server: the
site sees the server's address and which page someone saved. Nothing about the
person goes with it. HIKIDASHI_RECIPE_FETCH=off turns it off (ADR 0020). The
server refuses to fetch anything on its own network, so a caption cannot point
it at a router or another container.

Stdlib only.
"""

from __future__ import annotations

import html
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import detect
from .amounts import unit_named
from .llm import Ingredient, Recipe

URL = re.compile(r"https?://[^\s<>\"'()（）「」]+", re.I)
LIMIT = 3_000_000  # bytes of a page read at most
TIMEOUT = 10
AGENT = "Hikidashi recipe fetch (+https://github.com/spanthetree/hikidashi)"
# 'Comment "BIRRIA" to have it sent': the keyword in capitals.
COMMENT = re.compile(r"\b(?i:comment)\s+[\"“'‘]?([A-Z]{3,})\b")
HOSTS_SKIPPED = ("instagram.com", "facebook.com", "tiktok.com", "youtube.com", "youtu.be")


class FetchError(Exception):
    """The page could not be fetched, or has no recipe in it."""


def link(caption: str | None) -> dict[str, Any] | None:
    """Where the caption says the rest of the recipe is, or None.

    {"kind": "recipe" | "site", "url", "site"}: a URL in the caption, the
    longest path first, since a recipe page beats a home page;
    {"kind": "comment", "keyword"}: sent by DM to whoever comments;
    {"kind": "bio"}: a link on the author's profile.
    """
    if not caption:
        return None
    found = []
    for raw in URL.findall(caption):
        url = raw.rstrip(".,!?;:…")
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").removeprefix("www.")
        if not host or any(host == h or host.endswith("." + h) for h in HOSTS_SKIPPED):
            continue
        found.append((len(parts.path.strip("/")), url, host))
    if found:
        depth, url, host = max(found, key=lambda f: f[0])
        return {"kind": "recipe" if depth else "site", "url": url, "site": host}
    if m := COMMENT.search(caption):
        return {"kind": "comment", "keyword": m.group(1)}
    if re.search(r"\bcomment\b.{0,30}\b(?:recipe|link)\b|\bdm\b.{0,20}\brecipe\b", caption, re.I):
        return {"kind": "comment", "keyword": None}
    if detect.sends_elsewhere(caption):
        return {"kind": "bio"}
    return None


# --- fetching -----------------------------------------------------------------


def _public(host: str) -> None:
    """Raise FetchError unless every address the host has is on the internet."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise FetchError(f"{host} not found") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise FetchError(f"{host} is not on the internet")


def _checked(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchError("only http and https links are fetched")
    # shortcut: checked when asked, not pinned for the connection; a DNS answer
    # that changes in between (rebinding) gets past it. Pin the address if the
    # server ever holds anything worth reaching that way.
    _public(parts.hostname)
    return url


class _Redirects(urllib.request.HTTPRedirectHandler):
    max_redirections = 4

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return super().redirect_request(req, fp, code, msg, headers, _checked(newurl))


_opener = urllib.request.build_opener(_Redirects)


def fetch(url: str, *, timeout: float = TIMEOUT) -> str:
    """A page's HTML. Raises FetchError."""
    req = urllib.request.Request(
        _checked(url), headers={"User-Agent": AGENT, "Accept": "text/html"}
    )
    try:
        with _opener.open(req, timeout=timeout) as r:
            body: bytes = r.read(LIMIT)
            charset = str(r.headers.get_content_charset() or "utf-8")
    except FetchError:
        raise
    except urllib.error.HTTPError as exc:
        raise FetchError(f"the site answered {exc.code}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise FetchError(f"the site did not answer: {exc}") from exc
    try:
        return body.decode(charset, "replace")
    except LookupError:  # a charset Python does not know
        return body.decode("utf-8", "replace")


# --- reading ------------------------------------------------------------------

_SCRIPT = re.compile(
    r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.I | re.S
)
_TAG = re.compile(r"<[^>]+>")


def _text(v: Any) -> str:
    return " ".join(html.unescape(_TAG.sub(" ", str(v))).split()) if v else ""


def _recipes(node: Any) -> list[dict[str, Any]]:
    """Every object typed Recipe in a JSON-LD document, however nested."""
    if isinstance(node, list):
        return [r for n in node for r in _recipes(n)]
    if not isinstance(node, dict):
        return []
    kind = node.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    if "Recipe" in kinds:
        return [node]
    return [r for v in node.values() if isinstance(v, (dict, list)) for r in _recipes(v)]


def minutes(iso: Any) -> int | None:
    """ISO 8601 durations as recipe sites write them: PT1H30M, P0DT45M."""
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:\d+S)?", str(iso or "").strip())
    if not m or not any(m.groups()):
        return None
    d, h, mins = (int(x or 0) for x in m.groups())
    return d * 1440 + h * 60 + mins or None


def _servings(v: Any) -> int | None:
    for x in v if isinstance(v, list) else [v]:
        if m := re.search(r"\d+", str(x or "")):
            n = int(m.group(0))
            return n if 0 < n < 10_000 else None
    return None


def _steps(v: Any) -> list[str]:
    if isinstance(v, str):
        return [s for s in (_text(p) for p in re.split(r"\n+|<br\s*/?>|</p>", v)) if s]
    if isinstance(v, dict):
        if v.get("itemListElement"):
            return _steps(v["itemListElement"])
        return [t] if (t := _text(v.get("text") or v.get("name"))) else []
    if isinstance(v, list):
        return [s for x in v for s in _steps(x)]
    return []


_QTY = re.compile(r"^\s*((?:[\d½¼¾⅓⅔⅛]+(?:[.,/]\d+)?\s*)+(?:\s*(?:-|–|to)\s*[\d½¼¾⅓⅔⅛./]+)?)\s*")


def ingredient(line: str) -> Ingredient:
    """ "1 ½ cups all-purpose flour, sifted" as quantity, unit, item and note."""
    text = _text(line)
    rest, quantity, unit = text, None, None
    if m := _QTY.match(rest):
        quantity, rest = m.group(1).strip(), rest[m.end() :]
        word = rest.split(" ", 1)[0] if rest else ""
        if u := unit_named(word.strip("()")):
            unit, rest = u, rest[len(word) :].strip()
    item, _, note = rest.partition(",")
    return Ingredient(
        item=item.strip().lower()[:120] or text.lower()[:120],
        quantity=quantity,
        unit=unit,
        note=note.strip() or None,
        as_written=text,
    )


def parse(page: str) -> Recipe:
    """The page's schema.org Recipe. Raises FetchError when it has none."""
    for block in _SCRIPT.findall(page):
        try:
            doc = json.loads(block.strip())
        except ValueError:
            continue
        for r in _recipes(doc):
            ingredients = [ingredient(i) for i in r.get("recipeIngredient") or [] if _text(i)]
            steps = _steps(r.get("recipeInstructions"))
            if not (ingredients or steps):
                continue
            return Recipe(
                title=_text(r.get("name"))[:120] or None,
                servings=_servings(r.get("recipeYield")),
                time_minutes=minutes(r.get("totalTime"))
                or (minutes(r.get("prepTime")) or 0) + (minutes(r.get("cookTime")) or 0)
                or None,
                ingredients=ingredients[:60],
                steps=steps[:60],
                incomplete=not (ingredients and steps),
            )
    raise FetchError("the page has no recipe in it")
