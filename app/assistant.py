"""The model a person's own questions go to: asking the library (#45), and
translating a recipe they corrected, on their hosted API only (#114).

These run while someone waits, on the server, so they are not pipeline stages.
They follow the person's AI choice (#125): their hosted API when it is what
their reels use, the server's when they follow a system default that is hosted,
and otherwise the local Ollama model the tagger uses. A hosted API over its
monthly budget is not used, and its use is counted like the stages'.

Stdlib only, like app/chat.py.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app.chat import Asks, Chat
from app.profiles import resolve

if TYPE_CHECKING:
    from app.ai.config import Api

# The stage whose provider and model answer: a chat stage, and the one closest
# to reading a recipe and writing about it.
STAGE = "recipe"


@dataclass
class Model:
    asks: Asks
    label: str  # provider:model, or ollama:<model>
    api: Api | None = None  # when hosted


def model_for(
    conn: sqlite3.Connection, user_id: int, data_dir: Path, *, ollama_url: str, profile: str
) -> Model:
    """The model this person's questions go to. Never None: an Ollama that is
    not running fails when asked, with an OllamaError that says so."""
    if hosted := hosted_for(conn, user_id, data_dir):
        return hosted
    from app.ollama import Client

    try:
        name = resolve(profile).tag_model
    except ValueError:
        name = resolve("cpu").tag_model
    return Model(Client(ollama_url, name, timeout=180), f"ollama:{name}")


def hosted_for(conn: sqlite3.Connection, user_id: int, data_dir: Path) -> Model | None:
    """This person's hosted API, when their reels use one and it is under
    budget, or a self-hosted provider set for the recipe stage, theirs or the
    server's, switched on or not; else None. Needs no worker (#125)."""
    from app.ai import service, usage

    way = service.applies(conn, user_id, data_dir)
    if way in ("mine", "server"):
        api = service.load(conn, user_id if way == "mine" else None, data_dir)
        if not api.problems() and usage.spent(conn, api.name) < float(api.budget.value or 0):
            chat = Chat(api.client(STAGE), api.model(STAGE))
            return Model(chat, chat.label, api)
    # A self-hosted model (Ollama of your own) sends nothing off your network,
    # so it answers with the switch off too: yours, else the server's.
    for owner in (user_id, None):
        api = service.load(conn, owner, data_dir)
        st = api.stages[STAGE]
        if st.preset and st.preset.self_hosted and st.base_url.value and api.model(STAGE):
            chat = Chat(api.client(STAGE), api.model(STAGE))
            return Model(chat, chat.label, api)
    return None


def record(conn: sqlite3.Connection, model: Model, stage: str, shortcode: str | None) -> None:
    """Count what a hosted model used, against its budget."""
    if not isinstance(model.asks, Chat) or model.api is None:
        return
    from app.ai import usage

    used, n = model.asks.take_usage()
    if n:
        api = model.api
        usage.record(
            conn,
            api.name,
            api.owner,
            stage,
            model.label,
            used,
            requests=n,
            shortcode=shortcode,
        )
