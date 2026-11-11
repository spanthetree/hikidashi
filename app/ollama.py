"""A client for one model on an Ollama server, shared by every stage that asks it.

Each stage brings its own prompt, JSON schema and output cap; this only knows
how to reach the server, pull the model and make one strict-JSON chat request.
The tagger and the recipe pass use the same model, so on a run of both it is
loaded once and kept warm between them.

Stdlib only: urllib, so the stages and their tests need nothing installed
beyond a reachable Ollama.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

NUM_CTX = 8192  # the longest transcript in the library is ~3k tokens


class OllamaError(Exception):
    """Ollama cannot be used at all: not running, or the model is missing.

    Raised for the whole run, never recorded against an item, so a stopped
    server does not mark the entire library as failed.
    """


class ReplyError(Exception):
    """This one request failed. The stage records it and retries next run."""


class Client:
    def __init__(self, url: str, model: str, *, timeout: float = 300):
        self.url = url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def _post(self, path: str, body: dict[str, Any], timeout: float | None = None) -> Any:
        req = urllib.request.Request(
            self.url + path,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        return urllib.request.urlopen(req, timeout=timeout or self.timeout)

    def has_model(self) -> bool:
        """True when the model is pulled. Raises OllamaError if unreachable."""
        try:
            with urllib.request.urlopen(self.url + "/api/tags", timeout=10) as r:
                models = json.load(r).get("models", [])
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise OllamaError(f"no Ollama at {self.url}: {exc}") from exc
        wanted = self.model if ":" in self.model else self.model + ":latest"
        return any(m.get("name") == wanted for m in models)

    def pull(self, on_status: Callable[[str], None] | None = None) -> None:
        """Download the model. Several GB the first time; a no-op after that."""
        try:
            with self._post("/api/pull", {"model": self.model}, timeout=3600) as r:
                last = None
                for line in r:
                    msg = json.loads(line)
                    if "error" in msg:
                        raise OllamaError(f"pull failed: {msg['error']}")
                    status = msg.get("status")
                    if on_status and status and status != last:
                        on_status(status)
                        last = status
        except urllib.error.URLError as exc:
            raise OllamaError(f"pull failed: {exc}") from exc

    def chat(
        self,
        system: str,
        prompt: str,
        *,
        schema: dict[str, Any],
        num_predict: int,
        num_ctx: int = NUM_CTX,
    ) -> str:
        """One request, constrained to `schema`. Returns the reply as text."""
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "format": schema,
            "stream": False,
            # Keep the model loaded between reels, not the default five minutes
            # after the last one; a cold load costs more than a request.
            "keep_alive": "15m",
            "options": {"temperature": 0, "num_predict": num_predict, "num_ctx": num_ctx},
        }
        try:
            with self._post("/api/chat", body) as r:
                data = json.load(r)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:200]
            if exc.code == 404:
                raise OllamaError(f"model {self.model!r} not found: {detail}") from exc
            raise ReplyError(f"HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            # The server went away mid-run. Stop rather than fail every item.
            raise OllamaError(f"lost Ollama at {self.url}: {exc.reason}") from exc
        except TimeoutError as exc:
            raise ReplyError("timed out") from exc

        if data.get("done_reason") == "length":
            raise ReplyError(f"reply hit the {num_predict}-token cap")
        content: str = data.get("message", {}).get("content", "")
        return content
