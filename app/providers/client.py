"""One client for every OpenAI-compatible provider.

Three requests: `chat` (with images, for OCR), `transcribe`, and `models`. The
preset (presets.py) says how a provider differs; the rest is the same.

What can go wrong is sorted by what it means for the reels, as the hosted
switch (app/ai) needs it:

    Unusable       the provider cannot be used at all: the key is rejected,
                   the account's quota or credit is used up, the model is
                   not there for this key. The switch turns off, with the
                   reason, and nothing is recorded against any reel.
    Wait           the provider says not now: rate-limited past what is
                   worth waiting in the request, or a free tier's daily cap.
                   Nothing is counted; the reels wait or go back.
    Outage         the provider cannot be reached, or keeps answering 5xx.
                   As Wait, with a backoff.
    RequestFailed  this one request failed: a 400 on this file, a reply that
                   is not the JSON asked for, a refusal, a reply cut short.
                   An API error on that reel, counted apart from workers'.

A short 429 is waited out in the request itself, and a 5xx tried again
twice, so only what outlasts that comes out as an error.

Keys never appear in an error: whatever a provider echoes back is scrubbed
(keys.py), and the key is in no URL.

Stdlib only: urllib, as app/ollama.py, so the server image runs it.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from . import limits
from .keys import Key
from .presets import Preset
from .schema import strict, validate

TIMEOUT = 120.0  # seconds for one request
RETRIES = 2  # more tries after a 5xx or a dropped connection
BACKOFF = (1.0, 4.0)
SHORT_WAIT = 30.0  # a 429 asking for at most this long is waited out in the request
DETAIL = 300  # characters of a provider's error message kept


class ProviderError(Exception):
    """Something a provider said, or did not say. The message has no key in it."""


class Unusable(ProviderError):
    """The provider cannot be used at all, until someone does something."""

    def __init__(self, message: str, *, why: str):
        super().__init__(message)
        self.why = why  # key | quota | model | address


class Wait(ProviderError):
    """Not now; try again in `seconds`."""

    def __init__(self, message: str, *, seconds: float, daily: bool = False):
        super().__init__(message)
        self.seconds = seconds
        self.daily = daily


class Outage(ProviderError):
    """The provider is down or unreachable for now."""


class RequestFailed(ProviderError):
    """This one request failed; the next may not."""


@dataclass
class Usage:
    """What a request used, as the provider counted it."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    audio_seconds: float = 0.0
    images: int = 0

    def add(self, other: Usage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cached_tokens += other.cached_tokens
        self.audio_seconds += other.audio_seconds
        self.images += other.images


@dataclass
class Reply:
    text: str
    usage: Usage = field(default_factory=Usage)


@dataclass
class Transcript:
    """A transcription's reply. `segments` only from a model that gives them:
    each with start, end, text, and no_speech_prob and avg_logprob."""

    text: str
    language: str | None = None
    duration: float | None = None
    segments: list[dict[str, Any]] | None = None
    usage: Usage = field(default_factory=Usage)


# (method, url, headers, body, timeout) -> (status, headers, body). Swapped in tests.
Send = Callable[
    [str, str, dict[str, str], bytes | None, float], tuple[int, Mapping[str, str], bytes]
]


def urlopen(
    method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
) -> tuple[int, Mapping[str, str], bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers.items()), r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()) if exc.headers else {}, exc.read()


def account(base_url: str, key: Key | None) -> str:
    """Names a provider account without holding its key: for the limiter."""
    raw = (key.reveal() if key else "") + "@" + base_url.rstrip("/")
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def multipart(
    fields: dict[str, str], files: dict[str, tuple[str, str, bytes]]
) -> tuple[str, bytes]:
    """A multipart/form-data body: (content type, body)."""
    boundary = "hikidashi-" + secrets.token_hex(12)
    out = bytearray()
    for name, value in fields.items():
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        out += value.encode() + b"\r\n"
    for name, (filename, ctype, data) in files.items():
        out += (
            f"--{boundary}\r\nContent-Disposition: form-data; "
            f'name="{name}"; filename="{filename}"\r\nContent-Type: {ctype}\r\n\r\n'
        ).encode()
        out += data + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return f"multipart/form-data; boundary={boundary}", bytes(out)


def image_part(data: bytes, mime: str = "image/jpeg", *, detail: str = "low") -> dict[str, Any]:
    """An image in a chat message."""
    url = f"data:{mime};base64,{base64.b64encode(data).decode()}"
    return {"type": "image_url", "image_url": {"url": url, "detail": detail}}


def _error(raw: bytes) -> tuple[str, str, str]:
    """(message, code, type) from an OpenAI-style error body, as far as it has them."""
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return raw.decode("utf-8", "replace")[:DETAIL], "", ""
    err = data.get("error", data) if isinstance(data, dict) else {}
    if isinstance(err, str):
        return err[:DETAIL], "", ""
    if not isinstance(err, dict):
        return str(data)[:DETAIL], "", ""
    msg = err.get("message") or err.get("detail") or json.dumps(err)
    return str(msg)[:DETAIL], str(err.get("code") or ""), str(err.get("type") or "")


class Client:
    """Requests to one provider account."""

    def __init__(
        self,
        preset: Preset,
        key: Key | None,
        *,
        base_url: str | None = None,
        timeout: float = TIMEOUT,
        limiter: limits.Limiter | None = None,
        send: Send | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.preset = preset
        self.key = key
        self.base_url = (base_url or preset.base_url).rstrip("/")
        if not self.base_url:
            raise ValueError(f"{preset.label} needs a base URL")
        self.timeout = timeout
        self.limiter = limiter or limits.shared(
            self.base_url,
            account(self.base_url, key),
            in_flight=preset.in_flight,
            per_minute=preset.per_minute,
        )
        self._send = send or urlopen
        self.sleep = sleep
        # Learnt per model from a 400: some reject `temperature`, some want
        # max_tokens rather than max_completion_tokens.
        self._no_temperature: set[str] = set()
        self._tokens_param: dict[str, str] = {}

    def __repr__(self) -> str:
        return f"Client({self.preset.name}, {self.base_url}, key={self.key!r})"

    # --- transport ---------------------------------------------------------

    def _scrub(self, text: str) -> str:
        return self.key.scrub(text) if self.key else text

    def _headers(self, content_type: str | None) -> dict[str, str]:
        h = {"Accept": "application/json", "User-Agent": "hikidashi"}
        if content_type:
            h["Content-Type"] = content_type
        if self.key:
            h["Authorization"] = f"Bearer {self.key.reveal()}"
        return h

    def request(
        self, method: str, path: str, body: bytes | None = None, content_type: str | None = None
    ) -> Any:
        """One request, its JSON reply parsed. Raises the errors above."""
        url = self.base_url + path
        tries = 0
        while True:
            try:
                with self.limiter.slot():
                    status, headers, raw = self._send(
                        method, url, self._headers(content_type), body, self.timeout
                    )
            except TimeoutError as exc:
                raise RequestFailed("timed out") from exc
            except (urllib.error.URLError, OSError) as exc:
                if tries < RETRIES:
                    self.sleep(BACKOFF[min(tries, len(BACKOFF) - 1)])
                    tries += 1
                    continue
                reason = getattr(exc, "reason", exc)
                raise Outage(self._scrub(f"cannot reach {self.base_url}: {reason}")) from None
            self.limiter.observe(headers)
            if 200 <= status < 300:
                try:
                    return json.loads(raw)
                except (ValueError, UnicodeDecodeError) as exc:
                    raise RequestFailed("the reply is not JSON") from exc
            if status >= 500 and tries < RETRIES:
                self.sleep(BACKOFF[min(tries, len(BACKOFF) - 1)])
                tries += 1
                continue
            # Raises, unless it is a 429 short enough to wait out.
            wait = self._judge(status, headers, raw)
            if tries > RETRIES:
                raise Wait("rate-limited, and still after waiting", seconds=wait)
            self.limiter.hold(wait)  # every request waits, not only this one
            tries += 1

    def _judge(self, status: int, headers: Mapping[str, str], raw: bytes) -> float:
        """Raise the error a failed reply means, or return how long to wait
        before trying the same request again."""
        message, code, kind = _error(raw)
        message = self._scrub(message)
        said = f"{status}: {message}" if message else str(status)
        lowered = f"{code} {kind} {message}".lower()
        if status in (401, 403):
            raise Unusable(f"key rejected ({said})", why="key")
        if status == 404 and ("model" in lowered or code == "model_not_found"):
            raise Unusable(f"model not available to this key ({said})", why="model")
        if status == 404:
            raise Unusable(f"no API at {self.base_url} ({said})", why="address")
        if status == 402 or "insufficient_quota" in lowered or "billing" in lowered:
            raise Unusable(f"quota or credit used up ({said})", why="quota")
        if status == 429:
            wait = limits.told(headers)
            # A free tier's daily cap: wait for the reset, never turn off.
            daily = any(w in lowered for w in ("per day", "(rpd)", "(tpd)", "(asd)")) or (
                wait is not None and wait > limits.LONGEST
            )
            if wait is not None and wait <= SHORT_WAIT and not daily:
                return wait
            raise Wait(
                f"rate-limited ({said})",
                seconds=wait if wait is not None else 60.0,
                daily=daily,
            )
        if status >= 500:
            raise Outage(f"server error ({said})")
        raise RequestFailed(said)

    # --- requests ----------------------------------------------------------

    def models(self) -> list[str]:
        """The models this key can use, by ID, sorted."""
        data = self.request("GET", "/models")
        rows = data.get("data", []) if isinstance(data, dict) else data
        ids = (
            {r.get("id") for r in rows if isinstance(r, dict)} if isinstance(rows, list) else set()
        )
        return sorted(i for i in ids if isinstance(i, str))

    def chat(
        self,
        model: str,
        system: str,
        user: str | list[dict[str, Any]],
        *,
        max_tokens: int,
        schema: dict[str, Any] | None = None,
        name: str = "reply",
    ) -> Reply:
        """One chat request. With `schema`, the reply must be JSON that fits
        it: asked for as the preset can, and checked here either way."""
        if schema is not None and self.preset.structured != "json_schema":
            # The schema in the prompt instead: after the system prompt, so
            # the start of every request stays the same for prompt caching.
            system = (
                f"{system}\n\nReply with one JSON object, and nothing else, "
                f"matching this JSON schema:\n{json.dumps(schema)}"
            )
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if schema is not None and self.preset.structured == "json_schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": name, "schema": strict(schema), "strict": True},
            }
        elif schema is not None and self.preset.structured == "json_object":
            body["response_format"] = {"type": "json_object"}
        data = self._chat(model, body, max_tokens)

        try:
            choice = data["choices"][0]
            message = choice.get("message") or {}
        except (KeyError, IndexError, TypeError) as exc:
            raise RequestFailed("the reply has no message") from exc
        if message.get("refusal"):
            raise RequestFailed(f"refused: {str(message['refusal'])[:DETAIL]}")
        if choice.get("finish_reason") == "length":
            raise RequestFailed(f"reply hit the {max_tokens}-token cap")
        text = message.get("content")
        if not isinstance(text, str):
            raise RequestFailed("the reply has no text")
        if schema is not None:
            text = _json_text(text)
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise RequestFailed(f"reply is not JSON: {exc.msg}") from exc
            if wrong := validate(parsed, schema):
                raise RequestFailed("reply does not fit the schema: " + "; ".join(wrong[:3]))
        return Reply(text=text, usage=_chat_usage(data, user))

    def _chat(self, model: str, body: dict[str, Any], max_tokens: int) -> Any:
        """Send a chat body, learning once per model which of temperature and
        the token cap's name it takes."""
        for _ in range(3):
            send = dict(body)
            if model not in self._no_temperature:
                send["temperature"] = 0
            send[self._tokens_param.get(model, self.preset.tokens_param)] = max_tokens
            try:
                return self.request(
                    "POST", "/chat/completions", json.dumps(send).encode(), "application/json"
                )
            except RequestFailed as exc:
                said = str(exc).lower()
                if "temperature" in said and model not in self._no_temperature:
                    self._no_temperature.add(model)
                    continue
                param = self._tokens_param.get(model, self.preset.tokens_param)
                if param in said and ("unsupported" in said or "not supported" in said):
                    other = (
                        "max_tokens"
                        if param == "max_completion_tokens"
                        else "max_completion_tokens"
                    )
                    self._tokens_param[model] = other
                    continue
                raise
        raise RequestFailed("the provider refused every form of the request")

    def transcribe(
        self,
        model: str,
        audio: bytes,
        *,
        filename: str = "audio.flac",
        content_type: str = "audio/flac",
        language: str | None = None,
        segments: bool = False,
    ) -> Transcript:
        """Speech to text. `segments`: ask for verbose_json, for the music
        guard, from a model that gives it."""
        fields = {"model": model, "response_format": "verbose_json" if segments else "json"}
        if segments:
            fields["timestamp_granularities[]"] = "segment"
        if language:
            fields["language"] = language
        ctype, body = multipart(fields, {"file": (filename, content_type, audio)})
        data = self.request("POST", "/audio/transcriptions", body, ctype)
        if not isinstance(data, dict) or not isinstance(data.get("text"), str):
            raise RequestFailed("the transcription has no text")
        segs = data.get("segments")
        duration = data.get("duration")
        usage = Usage()
        u = data.get("usage") or {}
        if isinstance(u, dict) and u.get("type") == "duration":
            usage.audio_seconds = float(u.get("seconds") or 0)
        elif isinstance(u, dict) and u.get("type") == "tokens":
            usage.input_tokens = int(u.get("input_tokens") or 0)
            usage.output_tokens = int(u.get("output_tokens") or 0)
        if not usage.audio_seconds and isinstance(duration, int | float):
            usage.audio_seconds = float(duration)
        return Transcript(
            text=data["text"],
            language=data.get("language") if isinstance(data.get("language"), str) else None,
            duration=float(duration) if isinstance(duration, int | float) else None,
            segments=[s for s in segs if isinstance(s, dict)] if isinstance(segs, list) else None,
            usage=usage,
        )


def _json_text(text: str) -> str:
    """The JSON in a reply that wrapped it in a code fence, as models in JSON
    mode sometimes do."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rsplit("```", 1)[0]
    return t.strip()


def _chat_usage(data: Any, user: str | list[dict[str, Any]]) -> Usage:
    u = data.get("usage") if isinstance(data, dict) else None
    out = Usage(
        images=sum(1 for p in user if p.get("type") == "image_url") if isinstance(user, list) else 0
    )
    if isinstance(u, dict):
        out.input_tokens = int(u.get("prompt_tokens") or 0)
        out.output_tokens = int(u.get("completion_tokens") or 0)
        details = u.get("prompt_tokens_details") or {}
        if isinstance(details, dict):
            out.cached_tokens = int(details.get("cached_tokens") or 0)
    return out
