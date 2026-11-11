"""Turning an item into a vector, and a query into one.

What gets embedded is the tagger's English summary and keywords, not the
caption. Measured on the library, adding the caption made every model worse:
captions are long, hashtag-stuffed and often not in English, and a 512-token
window full of #reels #explore drowns the one line that says what the reel is.
The caption is still searched, by FTS. It is embedded only for an item the
tagger has not reached, so a fresh import is not missing from semantic search.

The summary keeps what a reel is about and drops what was said in it, so the
transcript and on-screen text are embedded too, a stretch at a time (`chunks`):
that is what finds "the one where he says he's been a butcher for 20 years".

`document`, `chunks`, `digest` and `pack` are stdlib only. fastembed (ONNX Runtime, no
torch) is imported when an `Embedder` is built, so the worker and its tests load
without it.
"""

from __future__ import annotations

import array
import hashlib
import json
from collections.abc import Iterable, Sequence
from pathlib import Path

# Bumping this re-embeds everything. Change it with MODEL, the prefixes or
# `document`. The search side compares against whatever is stored, so a query
# from a new model against old vectors would be meaningless: re-run the worker.
EMBED_V = 2

# Chosen by measurement, not by size. Over paraphrased queries on the library
# ("warm broth for a cold night", "the Initial D car", "cheap eats in Tokyo")
# it found 0.76 of the relevant reels in its top 10, against 0.53 for
# bge-small-en-v1.5 and 0.54-0.58 for bge-large and mxbai-embed-large, both a
# little larger than it. It is 1.2 GB on disk and embeds a query in ~13 ms on CPU.
MODEL = "google/embeddinggemma-300m"
DIM = 768
QUERY_PREFIX = "task: search result | query: "
# The model card suggests "title: none | text: " for documents. Measured, it
# was no better, and for the margherita query it was worse.
DOC_PREFIX = ""

CAPTION_CHARS = 1000  # of a caption standing in for a missing summary

# Transcript and on-screen text are embedded a stretch at a time, overlapping
# so a sentence cut at one edge is whole in the next. 60 words is ~20 seconds
# of speech: one thing said, rather than the gist of the whole reel. Fewer than
# MIN_CHUNK_WORDS in all is "Thank you." or a song lyric, not worth a vector.
CHUNK_WORDS = 60
CHUNK_STEP = 40
MIN_CHUNK_WORDS = 12


def document(summary: str | None, keywords: str | None, caption: str | None) -> str | None:
    """The text an item is embedded from. None when there is nothing to embed."""
    words = ", ".join(json.loads(keywords or "[]"))
    parts = [summary, words] if summary else [(caption or "")[:CAPTION_CHARS]]
    text = "\n".join(p.strip() for p in parts if p and p.strip())
    return text or None


def chunks(*texts: str | None) -> list[str]:
    """The stretches of transcript and on-screen text embedded on their own."""
    out = []
    for text in texts:
        words = (text or "").split()
        if len(words) < MIN_CHUNK_WORDS:
            continue
        for i in range(0, max(1, len(words) - CHUNK_WORDS + CHUNK_STEP), CHUNK_STEP):
            out.append(" ".join(words[i : i + CHUNK_WORDS]))
    return out


def digest(text: str, parts: Sequence[str] = ()) -> str:
    """Identifies the text an item's vectors came from, and the model that made
    them."""
    whole = "\0".join([MODEL, text, *parts])
    return hashlib.sha256(whole.encode("utf-8")).hexdigest()[:16]


def pack(vector: Iterable[float]) -> bytes:
    """float32, native order: the blob layout sqlite-vec reads."""
    return array.array("f", vector).tobytes()


class Embedder:
    """One loaded model. Safe to call from several threads at once."""

    def __init__(self, models_dir: Path | None = None, *, local_only: bool = False):
        from fastembed import TextEmbedding

        self.model = TextEmbedding(
            MODEL,
            cache_dir=str(models_dir / "fastembed") if models_dir else None,
            # The API never downloads: a search must not stall on a 100 MB fetch.
            local_files_only=local_only,
        )

    def documents(self, texts: list[str]) -> list[bytes]:
        return [pack(v) for v in self.model.embed([DOC_PREFIX + t for t in texts])]

    def query(self, text: str) -> bytes:
        return pack(next(iter(self.model.embed([QUERY_PREFIX + text]))))
