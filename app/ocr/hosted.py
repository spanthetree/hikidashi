"""On-screen text read by a hosted vision model (#125).

The frames are the ones tesseract reads (reader.py): a photo's image, or a
video's cover and five frames through it. Each is scaled down to WIDTH and
sent as a JPEG at `detail: low`, all in one request, with a prompt that asks
for the text visible on screen, verbatim, in reading order, and nothing about
what the picture shows. The lines that come back go through the same
`merge` as tesseract's, so a caption held across frames is kept once.

The profile it stamps in item.ocr_profile is `provider:model`.

Stdlib only: ffmpeg is a subprocess.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from app.chat import Chat
from app.db import Row
from app.providers.client import image_part

from .reader import FRAMES, Line, OcrError, merge

WIDTH = 768  # pixels; `detail: low` sees 512 anyway
MAX_TOKENS = 1500

SYSTEM = """\
You read the text shown on screen in frames from one Instagram post. Copy every
piece of text that is visible, verbatim, in its own language, in reading
order: titles, captions, subtitles, recipe cards, signs, labels. Each distinct
line once, even when it appears on several frames. Do not describe the
picture, translate, correct, or add anything. If there is no text, reply with
an empty list. Reply with JSON only."""

PROMPT = "The frames, in order. List the lines of text on them."


def schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {"lines": {"type": "array", "items": {"type": "string"}}},
        "required": ["lines"],
    }


_SCHEMA = schema()


def frame(path: Path, at: float | None = None, *, timeout: int = 60) -> bytes:
    """One image (or a video's frame `at` seconds in), scaled and as JPEG.
    Empty past the last frame."""
    cmd = ["ffmpeg", "-nostdin", "-v", "error"]
    if at is not None:
        cmd += ["-ss", f"{at:.2f}"]
    cmd += [
        "-i", str(path), "-frames:v", "1",
        "-vf", f"scale='min({WIDTH},iw)':-2",
        "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "4", "-",
    ]  # fmt: skip
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise OcrError("ffmpeg timed out") from exc
    except OSError as exc:
        raise OcrError(f"ffmpeg not runnable: {exc}") from exc
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise OcrError((err.splitlines() or ["ffmpeg failed"])[0][:200])
    return proc.stdout


def frames(row: Row, media_dir: Path) -> list[bytes]:
    """The images to read for one reel, as reader.py picks them."""
    thumb = media_dir / row["thumb_path"] if row["thumb_path"] else None
    out: list[bytes] = []
    if thumb is not None and thumb.is_file():
        out.append(frame(thumb))
    if row["media_path"]:
        video = media_dir / row["media_path"]
        duration = row["duration"]
        if not duration:
            from app.enrich.fetcher import probe_duration

            duration = probe_duration(video)
        if not duration:
            raise OcrError("unknown duration")
        out += [f for f in (frame(video, duration * x) for x in FRAMES) if f]
    return [f for f in out if f]


def parse(reply: str) -> list[str]:
    import json

    data = json.loads(reply)
    return [" ".join(s.split()) for s in data.get("lines", []) if isinstance(s, str) and s.strip()]


class Hosted:
    """A provider's vision model, as the API's OCR stage runs it."""

    def __init__(self, chat: Chat):
        self.chat = chat

    @property
    def label(self) -> str:
        return self.chat.label

    def __call__(self, row: Row, media_dir: Path) -> str | None:
        """One reel's on-screen text, or None. Raises OcrError (no frames)
        and the provider's errors."""
        images = frames(row, media_dir)
        if not images:
            raise OcrError("no frames could be read")
        parts: list[dict[str, Any]] = [{"type": "text", "text": PROMPT}]
        parts += [image_part(img, "image/jpeg") for img in images]
        reply = self.chat.ask(SYSTEM, parts, schema=_SCHEMA, max_tokens=MAX_TOKENS, name="ocr")
        lines = parse(reply)
        return merge([[Line(text, 100.0) for text in lines]])
