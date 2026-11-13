"""Which file answers a front-end URL. Stdlib only, so it tests bare.

The front end is a single-page app built from frontend/ into app/web. A file
that exists there is served as itself; any other path is a client-side route
(/item/<shortcode>, /logs, ...) and gets index.html, which routes in the
browser.
"""

from __future__ import annotations

from pathlib import Path

# Vite puts a content hash in every file under _app/immutable, so a new build
# has new names and these can be cached forever.
IMMUTABLE = "public, max-age=31536000, immutable"
# Everything else, index.html above all, is checked on every load; otherwise a
# browser runs a stale page against a newer API.
REVALIDATE = "no-cache"

# Paths that belong to the backend. A miss under these is a real 404, never a
# page: a mistyped API call must not come back as HTML with a 200.
BACKEND = ("api/", "media/")
# And these, whole: with metrics off (#149), /metrics is a 404, not the app.
BACKEND_PATHS = ("api", "media", "metrics")


def resolve(root: Path, path: str) -> tuple[Path, str] | None:
    """(file to send, Cache-Control) for a request path, or None for a 404.

    `path` is the URL path without its leading slash.
    """
    if path.startswith(BACKEND) or path in BACKEND_PATHS:
        return None

    root = root.resolve()
    if path:
        candidate = (root / path).resolve()
        # resolve() collapses "..", so this also refuses traversal out of root.
        if candidate.is_relative_to(root) and candidate.is_file():
            cache = IMMUTABLE if path.startswith("_app/immutable/") else REVALIDATE
            return candidate, cache
        # A missing asset is a 404, not the app: a stale tab asking for an old
        # hashed chunk should fail loudly, and so should a typo'd /icon.svgg.
        if Path(path).suffix:
            return None

    index = root / "index.html"
    return (index, REVALIDATE) if index.is_file() else None
