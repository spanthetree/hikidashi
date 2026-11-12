"""A fake OpenAI-compatible API on localhost, for the hosted stages' tests.

It answers the three requests the client makes: `GET /v1/models`,
`POST /v1/chat/completions` (text and images) and
`POST /v1/audio/transcriptions`. By default:

    chat            the JSON `chat_reply(request)` returns, which a test sets;
                    else an empty object
    transcriptions  `transcript` (with segments in verbose_json)
    models          `models`

`then(path, status, body, headers)` queues one reply for the next request to
that path, so a test can script a 401, a 429 with Retry-After, a 5xx, a bad
reply. Every request is kept in `requests`, with its headers and body.

Run on its own (`python -m tests.fake_openai --port 8091`) it serves the same
for the browser tests: chat replies that fit each stage's schema.
"""

from __future__ import annotations

import argparse
import collections
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

KEY = "sk-test-fake0123456789abcdef"


@dataclass
class Seen:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body)

    def form(self) -> dict[str, str]:
        """The text fields of a multipart body."""
        out: dict[str, str] = {}
        ctype = self.headers.get("Content-Type", "")
        boundary = ctype.split("boundary=", 1)[-1].encode()
        for part in self.body.split(b"--" + boundary):
            head, _, value = part.partition(b"\r\n\r\n")
            if b"filename=" in head or b'name="' not in head:
                continue
            name = head.split(b'name="', 1)[1].split(b'"', 1)[0].decode()
            out[name] = value.rstrip(b"\r\n").decode()
        return out


@dataclass
class Fake:
    key: str | None = KEY
    models: list[str] = field(
        default_factory=lambda: ["gpt-4.1-mini", "whisper-1", "gpt-4o-mini-transcribe"]
    )
    transcript: str = "first stretch the dough and then bake it hot"
    no_speech_prob: float = 0.01
    language: str = "english"
    chat_reply: Callable[[dict[str, Any]], Any] | None = None
    requests: list[Seen] = field(default_factory=list)
    _queued: dict[str, collections.deque[tuple[int, Any, dict[str, str]]]] = field(
        default_factory=lambda: collections.defaultdict(collections.deque)
    )
    _lock: threading.Lock = field(default_factory=threading.Lock)
    server: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        assert self.server is not None
        host, port = self.server.server_address[:2]
        return f"http://{host!s}:{port}/v1"

    def then(
        self, path: str, status: int, body: Any = None, headers: dict[str, str] | None = None
    ) -> None:
        with self._lock:
            self._queued[path].append((status, body, headers or {}))

    def seen(self, path: str) -> list[Seen]:
        with self._lock:
            return [r for r in self.requests if r.path.endswith(path)]

    # --- serving -----------------------------------------------------------

    def handle(self, seen: Seen) -> tuple[int, Any, dict[str, str]]:
        with self._lock:
            self.requests.append(seen)
            path = seen.path.removeprefix("/v1")
            queued = self._queued.get(path)
            if queued:
                return queued.popleft()
        if self.key and seen.headers.get("Authorization") != f"Bearer {self.key}":
            return (
                401,
                {"error": {"message": "Incorrect API key provided", "code": "invalid_api_key"}},
                {},
            )
        if path == "/models":
            return 200, {"object": "list", "data": [{"id": m} for m in self.models]}, {}
        if path == "/chat/completions":
            req = seen.json()
            reply = self.chat_reply(req) if self.chat_reply else {}
            content = reply if isinstance(reply, str) else json.dumps(reply)
            return 200, completion(content), {}
        if path == "/audio/transcriptions":
            form = seen.form()
            if form.get("response_format") == "verbose_json":
                return 200, verbose(self.transcript, self.no_speech_prob, self.language), {}
            return 200, {"text": self.transcript}, {}
        return 404, {"error": {"message": f"no route {path}"}}, {}

    def start(self, port: int = 0) -> Fake:
        """Serve in a thread, on `port` (any free one by default)."""
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _serve(self) -> None:
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                seen = Seen(self.command, self.path, dict(self.headers.items()), body)
                status, out, headers = fake.handle(seen)
                raw = out if isinstance(out, bytes) else json.dumps(out).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                for k, v in headers.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = _serve

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True).start()
        return self

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None


def completion(content: str, *, finish: str = "stop", refusal: str | None = None) -> dict[str, Any]:
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content, "refusal": refusal},
                "finish_reason": finish,
            }
        ],
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "prompt_tokens_details": {"cached_tokens": 64},
        },
    }


def verbose(text: str, no_speech_prob: float, language: str = "english") -> dict[str, Any]:
    words = text.split()
    half = max(1, len(words) // 2)
    parts = [" ".join(words[:half]), " ".join(words[half:])]
    segments = [
        {
            "id": i,
            "start": i * 3.0,
            "end": i * 3.0 + 3.0,
            "text": " " + p,
            "no_speech_prob": no_speech_prob,
            "avg_logprob": -0.2,
        }
        for i, p in enumerate(parts)
        if p
    ]
    return {
        "task": "transcribe",
        "language": language,
        "duration": 3.0 * len(segments),
        "text": " ".join(parts).strip(),
        "segments": segments,
    }


def stage_reply(req: dict[str, Any]) -> Any:
    """A reply that fits whichever stage asked, for the browser tests: the
    schema read from response_format, or from the prompt in JSON mode."""
    fmt = req.get("response_format") or {}
    system = req["messages"][0]["content"]
    schema = (fmt.get("json_schema") or {}).get("schema")
    if schema is None and "JSON schema:\n" in system:
        schema = json.loads(system.rsplit("JSON schema:\n", 1)[1])
    props = set((schema or {}).get("properties", {}))
    if "category" in props:
        return {
            "summary": "Pizza dough stretched and baked.",
            "category": "Food",
            "confidence": "high",
            "keywords": ["pizza"],
        }
    if "is_recipe" in props:
        return {"dish": "pizza", "edible": True, "is_recipe": True, "confidence": "high"}
    if "ingredients" in props:
        return {
            "title": "pizza",
            "servings": 2,
            "time_minutes": 30,
            "steps": ["Bake it."],
            "ingredients": [
                {"item": "dough", "as_written": "dough", "quantity": "", "unit": "", "note": ""}
            ],
        }
    if "kind" in props:
        return {"kind": "none", "title": "", "confidence": "low", "others": []}
    if "known" in props:
        return {"known": False, "title": ""}
    if "lines" in props:
        return {"lines": ["Pizza night"]}
    return "OK"


def main() -> None:
    ap = argparse.ArgumentParser(description="A fake OpenAI-compatible API, for browser tests.")
    ap.add_argument("--port", type=int, default=8091)
    args = ap.parse_args()
    fake = Fake(chat_reply=stage_reply).start(args.port)
    print(f"fake OpenAI API on {fake.url}", flush=True)
    threading.Event().wait()


if __name__ == "__main__":
    main()
