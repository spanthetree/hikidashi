"""The one request the chat stages make, on whichever backend runs them.

The tagger, the game pass and the recipe pass each bring a system prompt, a
JSON schema and an output cap, and want the reply as JSON text:

    ask(system, prompt, schema=…, max_tokens=…, name=…) -> str

`app.ollama.Client` answers it on a local model, and `Chat` here on a hosted
provider (#116). The stages' prompts, schemas and taxonomy are the same on
both; only the backend differs, so `tagging.llm.Tagger`,
`tagging.games.Asker` and `recipes.llm.Reader` take either.

On a provider the reply has already been checked against the schema
(app/providers/client.py) before the stage parses it; a reply that does not
fit is a `RequestFailed`, an API error on that reel.

Stdlib only.
"""

from __future__ import annotations

import threading
from typing import Any, Protocol, runtime_checkable

from app.providers.client import Client, Usage

# What a hosted reply may run to, at the least. A stage's cap is sized for a
# local model on CPU, where every token is seconds; a hosted model may think
# before it answers, and its cap is a ceiling on cost, not the cost.
HOSTED_FLOOR = 1024


@runtime_checkable
class Asks(Protocol):
    def ask(
        self, system: str, prompt: str, *, schema: dict[str, Any], max_tokens: int, name: str
    ) -> str: ...


class Chat:
    """One model on one provider account, as the stages ask it. Counts what
    it used, for usage and the budget (app/ai)."""

    def __init__(self, client: Client, model: str):
        self.client = client
        self.model = model
        self.usage = Usage()
        self.requests = 0
        self._lock = threading.Lock()

    @property
    def label(self) -> str:
        """`provider:model`, stamped as the stage's profile."""
        return f"{self.client.preset.name}:{self.model}"

    def ask(
        self, system: str, prompt: str, *, schema: dict[str, Any], max_tokens: int, name: str
    ) -> str:
        reply = self.client.chat(
            self.model,
            system,
            prompt,
            schema=schema,
            max_tokens=max(max_tokens, HOSTED_FLOOR),
            name=name,
        )
        with self._lock:
            self.usage.add(reply.usage)
            self.requests += 1
        return reply.text

    def take_usage(self) -> tuple[Usage, int]:
        """What was used since last asked, and how many requests; then zero."""
        with self._lock:
            used, n = self.usage, self.requests
            self.usage, self.requests = Usage(), 0
        return used, n

    def __repr__(self) -> str:
        return f"Chat({self.label})"
