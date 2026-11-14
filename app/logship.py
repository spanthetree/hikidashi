"""The log, sent elsewhere as well (#149): syslog and Loki, off unless set.

`install(process)` adds a handler for each destination configured in the
environment, or saved on the Account page (app/monitoring.py: the variable
wins); called again, it replaces them. The file the /logs page reads, and
stdout, stay as they are:

    HIKIDASHI_SYSLOG=udp://host:514         udp:// | tcp:// | unix:///dev/log
    HIKIDASHI_SYSLOG_FORMAT=rfc5424         rfc5424 (default) | rfc3164
    HIKIDASHI_SYSLOG_FACILITY=local0
    HIKIDASHI_LOKI_URL=http://loki:3100     pushes to /loki/api/v1/push
    HIKIDASHI_LOKI_TOKEN / _USER / _PASSWORD (each or its *_FILE)
    HIKIDASHI_LOKI_TENANT                   X-Scope-OrgID
    HIKIDASHI_LOKI_LABELS=env=home,...      extra static labels
    HIKIDASHI_LOG_LEVEL=info                what is sent
    HIKIDASHI_LOG_FORMAT=text               text | json, for stdout and what is sent

What leaves the machine is redacted first (`redact`): tokens, keys and
passwords, every URL's query string (where a search's words are), and the
token in an invite link. The app logs no caption, transcript or username.

Loki gets lines from a queue a thread empties, a batch at a time, so logging
never waits on the network. While Loki is away the queue keeps the newest
lines up to its size and counts the rest as dropped. A shipper's own trouble
goes to stderr, never back into the log it is shipping.

Stdlib only.
"""

from __future__ import annotations

import base64
import collections
import contextlib
import datetime
import gzip
import json
import logging
import logging.handlers
import os
import re
import socket
import sys
import threading
import time
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

APP = "hikidashi"
# Fields a record may carry (`extra=`), sent along in JSON when present.
FIELDS = ("stage", "route", "status", "duration_ms")
QUEUE = 10_000  # lines held for Loki while it is away
BATCH = 500  # lines a push
EVERY = 1.0  # seconds between pushes
TIMEOUT = 10.0

_REDACT: tuple[tuple[re.Pattern[str], str], ...] = (
    # A URL's query string: ?q=<what someone searched for>, ?token=...
    (re.compile(r'(/[^\s?"\']*)\?[^\s"\']*'), r"\1"),
    (re.compile(r"(/invite/)[^/\s\"'?]+"), r"\1[redacted]"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+"), r"\1 [redacted]"),
    (re.compile(r"\bhk_(api|worker)_[A-Za-z0-9_-]+"), r"hk_\1_[redacted]"),
    (re.compile(r"\b(sk|gsk|xai|pk|rk)[-_][A-Za-z0-9_-]{12,}"), "[redacted]"),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|token|password|passwd|secret|authorization|cookie)"
            r"(\"?\s*[=:]\s*\"?)[^\s\"',;&]+"
        ),
        r"\1\2[redacted]",
    ),
)


def on(value: str | None) -> bool:
    return (value or "").strip().lower() in ("on", "1", "true", "yes")


Env = Mapping[str, str]


def secret(name: str, env: Env | None = None) -> str | None:
    """`name` from the environment, or the contents of the file `name_FILE`
    names, as Docker secrets are passed."""
    env = os.environ if env is None else env
    if value := env.get(name):
        return value
    if path := env.get(f"{name}_FILE"):
        return Path(path).read_text(encoding="utf-8").strip() or None
    return None


_dropped = 0
_dropped_lock = threading.Lock()


def dropped() -> int:
    """Lines Loki never got, since this process started."""
    return _dropped


def _drop(n: int = 1) -> None:
    global _dropped
    with _dropped_lock:
        _dropped += n


def redact(text: str) -> str:
    for pattern, repl in _REDACT:
        text = pattern.sub(repl, text)
    return text


def _complain(what: str) -> None:
    """A shipper's own trouble: stderr only, so it cannot feed itself."""
    with contextlib.suppress(Exception):
        print(f"hikidashi log shipping: {what}", file=sys.stderr, flush=True)


class Shipped(logging.Formatter):
    """A line as it leaves the machine: text or JSON, then redacted."""

    def __init__(self, process: str, worker: str, *, json_lines: bool) -> None:
        super().__init__("%(levelname)s %(name)s  %(message)s")
        self.process, self.worker, self.json_lines = process, worker, json_lines

    def format(self, record: logging.LogRecord) -> str:
        if not self.json_lines:
            return redact(super().format(record))
        line: dict[str, Any] = {
            "ts": datetime.datetime.fromtimestamp(record.created, datetime.UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
            "process": self.process,
            "worker": self.worker,
        }
        for f in FIELDS:
            if hasattr(record, f):
                line[f] = getattr(record, f)
        if record.exc_info:
            line["exc"] = self.formatException(record.exc_info)
        return redact(json.dumps(line, ensure_ascii=False, default=str))


class Rfc5424(Shipped):
    """`1 TIMESTAMP HOST APP PROCID MSGID [SD] MSG`; SysLogHandler adds <PRI>."""

    def __init__(self, process: str, worker: str, *, json_lines: bool) -> None:
        super().__init__(process, worker, json_lines=json_lines)
        self.host = socket.gethostname() or "-"

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.datetime.fromtimestamp(record.created, datetime.UTC).isoformat(
            timespec="milliseconds"
        )
        msgid = re.sub(r"[^!-~]", "_", record.name)[:32] or "-"
        sd = f'[{APP}@32473 process="{self.process}" worker="{_sd(self.worker)}"]'
        return f"1 {ts} {self.host} {APP} {os.getpid()} {msgid} {sd} {super().format(record)}"


def _sd(value: str) -> str:
    return re.sub(r'(["\\\]])', r"\\\1", value)


class Rfc3164(Shipped):
    """`Mmm dd hh:mm:ss HOST APP[PID]: MSG`, for older receivers."""

    def format(self, record: logging.LogRecord) -> str:
        ts = time.strftime("%b %d %H:%M:%S", time.localtime(record.created))
        host = socket.gethostname() or "-"
        return f"{ts} {host} {APP}[{os.getpid()}]: {super().format(record)}"


class SysLog(logging.handlers.SysLogHandler):
    """SysLogHandler, a line a message over TCP (no NUL), never raising into
    the app."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.stream = self.socktype == socket.SOCK_STREAM
        self.append_nul = not self.stream

    def format(self, record: logging.LogRecord) -> str:
        return super().format(record) + ("\n" if self.stream else "")

    def createSocket(self) -> None:
        # A receiver that is away at start must not stop the server starting:
        # the next line tries again.
        try:
            super().createSocket()
        except OSError as exc:
            self.socket = None
            _complain(f"syslog at {self.address}: {exc}")

    def handleError(self, record: logging.LogRecord) -> None:
        _complain(f"syslog: {sys.exc_info()[1]}")
        sock: socket.socket | None = getattr(self, "socket", None)
        if self.stream and sock is not None:
            sock.close()  # reconnect on the next line
            self.socket = None


def syslog_handler(url: str, process: str, worker: str, env: Env | None = None) -> logging.Handler:
    env = os.environ if env is None else env
    parts = urlsplit(url)
    facility = (env.get("HIKIDASHI_SYSLOG_FACILITY") or "local0").lower()
    fac = logging.handlers.SysLogHandler.facility_names.get(facility)
    if fac is None:
        raise ValueError(f"HIKIDASHI_SYSLOG_FACILITY: no facility called {facility}")
    if parts.scheme == "unix":
        handler = SysLog(address=parts.path or "/dev/log", facility=fac)
    elif parts.scheme in ("udp", "tcp"):
        kind = socket.SOCK_DGRAM if parts.scheme == "udp" else socket.SOCK_STREAM
        address = (parts.hostname or "localhost", parts.port or 514)
        handler = SysLog(address=address, facility=fac, socktype=kind)
    else:
        raise ValueError(f"HIKIDASHI_SYSLOG: use udp://, tcp:// or unix://, not {url}")
    style = (env.get("HIKIDASHI_SYSLOG_FORMAT") or "rfc5424").lower()
    if style not in ("rfc5424", "rfc3164"):
        raise ValueError("HIKIDASHI_SYSLOG_FORMAT: rfc5424 or rfc3164")
    fmt = Rfc5424 if style == "rfc5424" else Rfc3164
    handler.setFormatter(fmt(process, worker, json_lines=_json(env)))
    return handler


class Loki(logging.Handler):
    """Batched pushes to Loki from a thread; a full queue drops its oldest."""

    def __init__(
        self,
        url: str,
        labels: dict[str, str],
        *,
        headers: dict[str, str] | None = None,
        size: int = QUEUE,
        every: float = EVERY,
    ) -> None:
        super().__init__()
        self.url = url.rstrip("/") + "/loki/api/v1/push"
        self.labels = labels
        self.headers = headers or {}
        self.every = every
        self.queue: collections.deque[tuple[str, str, str]] = collections.deque()
        self.size = size
        self._wake = threading.Event()
        self._stop = threading.Event()
        self.sent = 0
        self._thread = threading.Thread(target=self._run, name="loki", daemon=True)
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
            ns = str(int(record.created * 1e9))
            if len(self.queue) >= self.size:
                self.queue.popleft()
                _drop()
            self.queue.append((record.levelname.lower(), ns, line))
            if len(self.queue) >= BATCH:
                self._wake.set()
        except Exception:
            _complain(f"loki: {sys.exc_info()[1]}")

    def _take(self) -> list[tuple[str, str, str]]:
        out: list[tuple[str, str, str]] = []
        while self.queue and len(out) < BATCH:
            out.append(self.queue.popleft())
        return out

    def push(self, batch: list[tuple[str, str, str]]) -> None:
        streams: dict[str, list[list[str]]] = {}
        for level, ns, line in batch:
            streams.setdefault(level, []).append([ns, line])
        body = gzip.compress(
            json.dumps(
                {
                    "streams": [
                        {"stream": {**self.labels, "level": level}, "values": values}
                        for level, values in streams.items()
                    ]
                }
            ).encode()
        )
        req = urllib.request.Request(
            self.url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Content-Encoding": "gzip",
                **self.headers,
            },
        )
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            resp.read()

    def _run(self) -> None:
        wait = self.every
        while not self._stop.is_set():
            self._wake.wait(wait)
            self._wake.clear()
            batch = self._take()
            if not batch:
                wait = self.every
                continue
            try:
                self.push(batch)
                self.sent += len(batch)
                wait = self.every
            except Exception as exc:
                # Back on the queue, at the front, as far as there is room.
                room = self.size - len(self.queue)
                self.queue.extendleft(reversed(batch[len(batch) - max(room, 0) :]))
                if room < len(batch):
                    _drop(len(batch) - max(room, 0))
                _complain(f"loki at {self.url}: {exc}")
                wait = min(max(wait * 2, self.every), 60.0)

    def flush(self) -> None:
        """Send what is queued now; for a clean exit and the tests."""
        while batch := self._take():
            try:
                self.push(batch)
                self.sent += len(batch)
            except Exception as exc:
                _complain(f"loki at {self.url}: {exc}")
                _drop(len(batch))

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        self.flush()
        super().close()


def loki_handler(url: str, process: str, worker: str, env: Env | None = None) -> Loki:
    env = os.environ if env is None else env
    headers = {}
    if token := secret("HIKIDASHI_LOKI_TOKEN", env):
        headers["Authorization"] = f"Bearer {token}"
    elif user := secret("HIKIDASHI_LOKI_USER", env):
        pair = f"{user}:{secret('HIKIDASHI_LOKI_PASSWORD', env) or ''}".encode()
        headers["Authorization"] = "Basic " + base64.b64encode(pair).decode()
    if tenant := env.get("HIKIDASHI_LOKI_TENANT"):
        headers["X-Scope-OrgID"] = tenant
    labels = {"app": APP, "process": process, "worker": worker}
    for kv in (env.get("HIKIDASHI_LOKI_LABELS") or "").split(","):
        key, _, value = kv.partition("=")
        if key.strip() and value.strip():
            labels.setdefault(key.strip(), value.strip())
    handler = Loki(url, labels, headers=headers)
    handler.setFormatter(Shipped(process, worker, json_lines=_json(env)))
    return handler


def _json(env: Env) -> bool:
    style = (env.get("HIKIDASHI_LOG_FORMAT") or "text").lower()
    if style not in ("text", "json"):
        raise ValueError("HIKIDASHI_LOG_FORMAT: text or json")
    return style == "json"


def _level(env: Env) -> int:
    name = (env.get("HIKIDASHI_LOG_LEVEL") or "info").upper()
    level = logging.getLevelName(name)
    if not isinstance(level, int):
        raise ValueError(f"HIKIDASHI_LOG_LEVEL: no level called {name.lower()}")
    return level


def check(env: Env) -> None:
    """Raise ValueError if these settings could not be installed, before any
    are: what the Account page says when one is wrong."""
    _level(env)
    _json(env)
    if url := env.get("HIKIDASHI_SYSLOG"):
        if urlsplit(url).scheme not in ("udp", "tcp", "unix"):
            raise ValueError(f"HIKIDASHI_SYSLOG: use udp://, tcp:// or unix://, not {url}")
        facility = (env.get("HIKIDASHI_SYSLOG_FACILITY") or "local0").lower()
        if facility not in logging.handlers.SysLogHandler.facility_names:
            raise ValueError(f"HIKIDASHI_SYSLOG_FACILITY: no facility called {facility}")
        if (env.get("HIKIDASHI_SYSLOG_FORMAT") or "rfc5424").lower() not in ("rfc5424", "rfc3164"):
            raise ValueError("HIKIDASHI_SYSLOG_FORMAT: rfc5424 or rfc3164")
    if (url := env.get("HIKIDASHI_LOKI_URL")) and urlsplit(url).scheme not in ("http", "https"):
        raise ValueError(f"HIKIDASHI_LOKI_URL: use http:// or https://, not {url}")


def install(
    process: str,
    worker: str,
    loggers: tuple[str, ...],
    noise: logging.Filter,
    env: Env | None = None,
) -> list[str]:
    """Add a handler for each destination set in `env` (the environment by
    default) to `loggers`, and make stdout JSON if asked. Returns what it
    added, as `syslog`, `loki`. Called again, it replaces what it added
    before, so a change on the Account page takes without a restart."""
    env = os.environ if env is None else env
    check(env)
    level = _level(env)
    for name in (f"{APP}-syslog", f"{APP}-loki"):
        old = [h for h in logging.getLogger(loggers[0]).handlers if h.get_name() == name]
        for h in old:
            for logger in loggers:
                logging.getLogger(logger).removeHandler(h)
            # Off the caller's thread: a Loki that is away makes its flush wait.
            threading.Thread(target=h.close, name=f"{name}-close", daemon=True).start()
    wanted = {
        "syslog": (env.get("HIKIDASHI_SYSLOG"), syslog_handler),
        "loki": (env.get("HIKIDASHI_LOKI_URL"), loki_handler),
    }
    added: list[str] = []
    for kind, (url, make) in wanted.items():
        if not url:
            continue
        handler = make(url, process, worker, env)
        added.append(kind)
        handler.set_name(f"{APP}-{kind}")
        handler.setLevel(level)
        handler.addFilter(noise)
        for logger in loggers:
            logging.getLogger(logger).addHandler(handler)
    as_json = _json(env)
    for name in loggers:
        for h in logging.getLogger(name).handlers:
            if type(h) is not logging.StreamHandler:
                continue
            # The formatter it had before JSON, to go back to.
            if not isinstance(h.formatter, Shipped):
                h._hk_text = h.formatter  # type: ignore[attr-defined]
            if as_json:
                h.setFormatter(Shipped(process, worker, json_lines=True))
            elif isinstance(h.formatter, Shipped):
                h.setFormatter(getattr(h, "_hk_text", None))
    return added
