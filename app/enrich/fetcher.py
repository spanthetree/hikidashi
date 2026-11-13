"""Fetching one reel's metadata, thumbnail, slides and (optionally) video.

Uses `yt-dlp` as a subprocess rather than a logged-in browser session: it reads
public posts without cookies, which is both lower-risk and far less brittle than
driving Instagram's UI. Private and deleted posts simply fail, and that failure
is recorded rather than retried forever.
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Bumping this invalidates every fetched column and schedules a re-fetch.
FETCH_V = 1

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# Failure reasons we classify, so a permanent failure is not retried like a
# transient one. The worker uses these to decide whether another attempt helps.
# Each entry maps a substring of yt-dlp's stderr to the short label we store,
# because the raw message is long and its matched fragment reads badly in a UI.
PERMANENT: tuple[tuple[str, str], ...] = (
    ("available to everyone", "restricted audience"),
    ("no longer available", "deleted"),
    ("does not exist", "deleted"),
    ("has been removed", "deleted"),
    ("unavailable", "unavailable"),
    ("not found", "unavailable"),
    ("empty media response", "login required"),
    ("login required", "login required"),
    ("private", "private account"),
    ("age-restricted", "age restricted"),
)


class FetchError(Exception):
    """A fetch failed. `permanent` means retrying will not help."""

    def __init__(self, reason: str, *, permanent: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.permanent = permanent


@dataclass
class Fetched:
    shortcode: str
    author: str | None = None
    author_name: str | None = None
    caption: str | None = None
    duration: int | None = None
    width: int | None = None
    height: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    thumbnail_url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def raw_json(self) -> str:
        return json.dumps(self.raw, ensure_ascii=False)


def _classify(stderr: str) -> FetchError:
    low = stderr.lower()
    for marker, label in PERMANENT:
        if marker in low:
            return FetchError(label, permanent=True)
    if "rate" in low and "limit" in low:
        return FetchError("rate-limited")
    if "timed out" in low or "timeout" in low:
        return FetchError("timeout")
    # Keep it short: this lands in a database column, not a log file.
    first = next((ln for ln in stderr.splitlines() if ln.strip()), "unknown error")
    return FetchError(first.strip()[:200])


def _best_thumbnail(info: dict[str, Any]) -> str | None:
    """Prefer the largest thumbnail yt-dlp knows about."""
    thumbs = [t for t in (info.get("thumbnails") or []) if t.get("url")]
    if thumbs:

        def area(t: dict[str, Any]) -> int:
            return (t.get("width") or 0) * (t.get("height") or 0)

        best = max(thumbs, key=area)
        if area(best):
            url: str = best["url"]
            return url
    thumbnail: str | None = info.get("thumbnail")
    return thumbnail


def probe(url: str, *, timeout: int = 90) -> Fetched:
    """Read one post's metadata. Raises FetchError on failure."""
    proc = subprocess.run(
        # --ignore-no-formats-error is what makes photo posts work: Instagram
        # image posts have no video formats, and without it yt-dlp refuses the
        # whole request rather than returning the metadata we actually want.
        [
            "yt-dlp",
            "--no-warnings",
            "--skip-download",
            "--dump-json",
            "--no-playlist",
            "--ignore-no-formats-error",
            "--socket-timeout",
            "30",
            url,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise _classify(proc.stderr or "yt-dlp produced no output")

    try:
        info = json.loads(proc.stdout.splitlines()[0])
    except json.JSONDecodeError as exc:
        raise FetchError(f"unparseable yt-dlp output: {exc}") from exc

    return from_info(info)


def from_info(info: dict[str, Any]) -> Fetched:
    """Build a Fetched from a yt-dlp info dict. Pure, so it is testable.

    `shortcode` here is whatever yt-dlp reported and is informational only: for
    a carousel it is the id of a child item, not the post we asked for. The
    caller keeps its own shortcode as the key.
    """
    duration = info.get("duration")
    return Fetched(
        shortcode=info.get("id") or "",
        author=info.get("channel") or info.get("uploader_id"),
        author_name=info.get("uploader"),
        caption=info.get("description"),
        duration=int(duration) if isinstance(duration, (int, float)) else None,
        width=info.get("width"),
        height=info.get("height"),
        like_count=info.get("like_count"),
        comment_count=info.get("comment_count"),
        thumbnail_url=_best_thumbnail(info),
        raw=info,
    )


def has_video(info: dict[str, Any]) -> bool:
    """yt-dlp lists formats only for a video: a photo post has none to download."""
    return bool(info.get("formats"))


def is_carousel(info: dict[str, Any]) -> bool:
    """A post of several slides whose first is a photo. Whether the rest are
    photos too only the full list can say: see probe_slides."""
    return (info.get("playlist_count") or 0) > 1 and not info.get("formats")


def slides_from(entries: list[dict[str, Any]]) -> list[str]:
    """Each slide's image, in order, or none at all: a carousel with a video in
    it is not a set of photos, and a slide with no image would leave a gap."""
    if len(entries) < 2 or any(e.get("formats") for e in entries):
        return []
    urls = [_best_thumbnail(e) for e in entries]
    return [u for u in urls if u] if all(urls) else []


def probe_slides(url: str, *, timeout: int = 120) -> list[str]:
    """The image of every slide of a carousel of photos. `probe` asks for the
    first slide only; without --no-playlist yt-dlp prints a line for each."""
    proc = subprocess.run(
        [
            "yt-dlp",
            "--no-warnings",
            "--skip-download",
            "--dump-json",
            "--ignore-no-formats-error",
            "--socket-timeout",
            "30",
            url,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise _classify(proc.stderr or "yt-dlp produced no output")
    try:
        entries = [json.loads(ln) for ln in proc.stdout.splitlines() if ln.strip()]
    except json.JSONDecodeError as exc:
        raise FetchError(f"unparseable yt-dlp output: {exc}") from exc
    entries.sort(key=lambda e: e.get("playlist_index") or 0)
    return slides_from(entries)


def shard(shortcode: str) -> str:
    """Two-character shard, so no directory holds thousands of files."""
    return (shortcode[:2] or "__").lower()


def download_thumbnail(url: str, dest: Path, *, timeout: int = 60) -> int:
    """Save a thumbnail, returning its size in bytes."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FetchError(f"thumbnail download failed: {exc}") from exc

    if not body:
        raise FetchError("thumbnail was empty")

    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(body)
    tmp.replace(dest)  # atomic, so a kill never leaves a half file
    return len(body)


def probe_duration(path: Path) -> int | None:
    """Read a media file's duration with ffprobe.

    Instagram's metadata has no duration field at all — not at the top level and
    not on any format — so the only honest source is the downloaded file.
    """
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        return int(float(proc.stdout.strip()))
    except (TypeError, ValueError):
        return None


def probe_dimensions(path: Path) -> tuple[int, int] | None:
    """An image's or video's width and height, read with ffprobe. A photo post
    reports no size in its metadata, so its saved thumbnail is the source."""
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height",
                "-of",
                "csv=p=0:s=x",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        w, h = (int(n) for n in proc.stdout.strip().split("x")[:2])
    except ValueError:
        return None
    return (w, h) if w > 0 and h > 0 else None


def download_media(url: str, dest: Path, *, timeout: int = 600) -> int:
    """Download the original mp4, never re-encoded. Returns size in bytes."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part.mp4")
    proc = subprocess.run(
        [
            "yt-dlp",
            "--no-warnings",
            "--no-playlist",
            "--no-part",
            "-f",
            "mp4/best",
            "-o",
            str(tmp),
            url,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0 or not tmp.exists():
        tmp.unlink(missing_ok=True)
        raise _classify(proc.stderr or "yt-dlp download failed")

    size = tmp.stat().st_size
    tmp.replace(dest)
    return size
