"""Turning one photo or one video into the text burned into it.

A photo post is read once, from its thumbnail: for a single image that is the
image itself, and for a carousel it is the first slide. A video is read at five
points through its length, plus its cover, which is where a reel's title
usually sits. ffmpeg hands each frame to tesseract on a pipe, with no temp
files.

Tesseract reads anything, including a brick wall, so the raw output is not
kept. Each word comes back with a confidence; low ones are dropped, and so is
any line left with no real word in it. The same caption on five frames, read
slightly differently each time, is kept once.

Stdlib only: tesseract and ffmpeg are subprocesses, so the worker and its tests
load with nothing installed.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

# Bumping this re-reads every item. Change it with the frames, the filters or
# the merging below.
OCR_V = 1

# Where in a video to look, as fractions of its length. The ends are skipped:
# the first and last tenth are fades, logos and "follow for more".
FRAMES = (0.1, 0.3, 0.5, 0.7, 0.9)

# Tesseract's per-word confidence, 0-100. Real burned-in text scores in the
# 90s; texture misread as letters mostly scores under 50.
MIN_CONF = 60

# Two readings of one line from different frames, close enough to be the same.
SAME_LINE = 0.85

# One tesseract per worker, one thread each. Its own OpenMP threading is slower
# than running several single-threaded copies side by side.
_ENV = {**os.environ, "OMP_THREAD_LIMIT": "1"}

_LETTERS = re.compile(r"[^\W\d_]{3,}")
_ALNUM = re.compile(r"\w")


class OcrError(Exception):
    """This one item could not be read. Recorded, and retried next run."""


class OcrUnavailable(Exception):
    """tesseract or ffmpeg cannot be run at all. Stops the run, marks nothing."""


@dataclass(frozen=True)
class Line:
    text: str
    conf: float


def parse_tsv(tsv: str, *, min_conf: float = MIN_CONF) -> list[Line]:
    """Tesseract's TSV output into lines worth keeping, in reading order.

    A word survives on confidence and on having a letter or digit in it, so a
    stray `©` or `|` goes. A line survives only if a word of three or more
    letters is left, which drops the lone numbers and the `ln,` and `of`
    fragments that texture and half-visible words produce.
    """
    lines: dict[tuple[str, str, str], list[tuple[float, str]]] = {}
    for row in tsv.splitlines()[1:]:
        f = row.split("\t")
        if len(f) < 12 or f[0] != "5":  # level 5 is a word
            continue
        word = f[11].strip()
        try:
            conf = float(f[10])
        except ValueError:
            continue
        if word and conf >= min_conf and _ALNUM.search(word):
            lines.setdefault((f[2], f[3], f[4]), []).append((conf, word))

    out = []
    for words in lines.values():
        text = " ".join(w for _, w in words)
        if _LETTERS.search(text):
            out.append(Line(text, sum(c for c, _ in words) / len(words)))
    return out


def _key(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.lower()).split())


def merge(frames: list[list[Line]]) -> str | None:
    """Lines from every frame into one text, each line once. None if empty.

    Frames repeat themselves: a caption held on screen is read on every frame
    it is on, a word or two differently each time. A line that matches one
    already kept, or is a fragment of it, is the same line; the longer reading
    wins, then the more confident one.
    """
    kept: list[Line] = []
    for lines in frames:
        for line in lines:
            k = _key(line.text)
            for i, old in enumerate(kept):
                o = _key(old.text)
                if k in o or o in k or SequenceMatcher(None, k, o).ratio() >= SAME_LINE:
                    if (len(k), line.conf) > (len(o), old.conf):
                        kept[i] = line
                    break
            else:
                kept.append(line)
    return "\n".join(line.text for line in kept) or None


def recognise(image: bytes, *, timeout: int = 60) -> list[Line]:
    """Read one encoded image (PNG, JPEG) with tesseract."""
    try:
        proc = subprocess.run(
            ["tesseract", "stdin", "stdout", "-l", "eng", "--psm", "3", "tsv"],
            input=image,
            capture_output=True,
            timeout=timeout,
            env=_ENV,
        )
    except subprocess.TimeoutExpired as exc:
        raise OcrError("tesseract timed out") from exc
    except OSError as exc:
        raise OcrUnavailable(f"tesseract not runnable: {exc}") from exc
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise OcrError((err.splitlines() or ["tesseract failed"])[-1][:200])
    return parse_tsv(proc.stdout.decode("utf-8", "replace"))


def grab_frame(path: Path, at: float, *, timeout: int = 60) -> bytes:
    """One frame of a video as PNG. Empty if `at` is past the last frame."""
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-ss",
                f"{at:.2f}",
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-f",
                "image2pipe",
                "-c:v",
                "png",
                "-",
            ],
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise OcrError("ffmpeg timed out") from exc
    except OSError as exc:
        raise OcrUnavailable(f"ffmpeg not runnable: {exc}") from exc
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise OcrError((err.splitlines() or ["ffmpeg failed"])[0][:200])
    return proc.stdout


def read_image(path: Path) -> str | None:
    try:
        image = path.read_bytes()
    except OSError as exc:
        raise OcrError(f"cannot read {path.name}: {exc.strerror}") from exc
    return merge([recognise(image)])


def read_video(path: Path, duration: float | None, cover: Path | None = None) -> str | None:
    """The cover, then five frames through the video, read and merged."""
    if not duration:
        from app.enrich.fetcher import probe_duration

        duration = probe_duration(path)
    if not duration:
        raise OcrError("unknown duration")
    images = []
    if cover is not None and cover.is_file():
        images.append(cover.read_bytes())
    images += [f for f in (grab_frame(path, duration * x) for x in FRAMES) if f]
    if not images:
        raise OcrError("no frames could be read")
    return merge([recognise(img) for img in images])
