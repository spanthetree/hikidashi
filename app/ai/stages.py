"""The four worker stages on a hosted API, one reel at a time (#125).

`run` takes a stage's row (the columns the stage reads, as its `pending`
query gives them) and returns what the stage produced, in the shape its
`record` takes, with what the requests used. It reads only the row and the
media files, and writes nothing, so the server's API (runner.py) and a hosted
worker on another machine (hosted_worker.py) both run it.

    asr     app/asr/hosted.py; also the language heard
    ocr     app/ocr/hosted.py
    tag     the tagger and the game pass on the chat model
    recipe  the recipe pass on the chat model, for candidates only

A reel's backends are made for it alone, so its usage is its own; their
clients share each provider account's limiter (app/providers/limits.py).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.asr import hosted as asr_hosted
from app.chat import Chat
from app.db import Row
from app.ocr import hosted as ocr_hosted
from app.providers.client import Client, Usage
from app.recipes import llm as recipe_llm
from app.recipes import worker as recipe_worker
from app.tagging import llm as tag_llm
from app.tagging import worker as tag_worker

# Stage -> (provider client, model), as an Api or a hosted worker's env gives them.
Made = Callable[[str], tuple[Client, str]]


@dataclass
class Done:
    produced: Any  # what the stage's record takes: text, a Result, a Found, or None
    profile: str  # provider:model
    usage: Usage = field(default_factory=Usage)
    requests: int = 0
    language: str | None = None  # asr only


def run(stage: str, row: Row, make: Made, media_dir: Path, *, language: str | None = None) -> Done:
    """Run one stage on one reel. Raises the provider's errors, and the
    stages' own for a reel that cannot be read (AudioError, OcrError,
    TagError, RecipeError): all of them API errors on the reel to the caller."""
    client, model = make(stage)
    if stage == "asr":
        asr = asr_hosted.Hosted(client, model, language=language)
        heard = asr_hosted.transcribe_one(row, media_dir, asr)
        return Done(
            heard.text,
            asr.label,
            heard.usage,
            1 if heard.usage.audio_seconds else 0,
            heard.language,
        )
    chat = Chat(client, model)
    if stage == "ocr":
        text = ocr_hosted.Hosted(chat)(row, media_dir)
        used, n = chat.take_usage()
        return Done(text, chat.label, used, n)
    if stage == "tag":
        tagger = tag_llm.Hosted(client, model)
        result = tag_worker.tag_one(row, tagger)
        used, n = tagger.take_usage()
        return Done(result, tagger.label, used, n)
    if stage == "recipe":
        reader = recipe_llm.Hosted(client, model)
        found = recipe_worker.read_one(row, reader) if recipe_worker.candidate(row) else None
        used, n = reader.take_usage()
        return Done(found, reader.label, used, n)
    raise ValueError(f"{stage} does not run on an API")
