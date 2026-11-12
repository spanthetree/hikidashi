"""Staying inside a provider's rate limits.

One `Limiter` per provider account (its base URL and key), shared by every
request to it in this process: the stages of one reel, and the reels of
several switched-on APIs that happen to use the same key.

    in flight   at most `in_flight` requests at once
    per minute  at most `per_minute` started in any 60 seconds
    told        a 429's `Retry-After`, or `x-ratelimit-remaining-*` at 0 with
                its `x-ratelimit-reset-*`, holds every request until then

Stdlib only. The clock and sleep are swapped in tests.
"""

from __future__ import annotations

import collections
import re
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager

LONGEST = 3600.0  # the most a header can make us wait; a daily cap is the client's to judge

_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")


def seconds(value: str | None) -> float | None:
    """A wait as headers give it: `20`, `1.5`, `6m0s`, `2m59.56s`, `450ms`,
    `1h2m`. None when it is not one."""
    if value is None:
        return None
    value = value.strip().lower()
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    total, pos = 0.0, 0
    for m in _PART.finditer(value):
        if m.start() != pos:
            return None
        n, unit = float(m.group(1)), m.group(2)
        total += n * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]
        pos = m.end()
    return total if pos == len(value) and pos else None


def told(headers: Mapping[str, str]) -> float | None:
    """How long the headers say to wait before the next request, if at all."""
    h = {k.lower(): v for k, v in headers.items()}
    waits = []
    if (s := seconds(h.get("retry-after"))) is not None:
        waits.append(s)
    for what in ("requests", "tokens", "audio-seconds"):
        left = h.get(f"x-ratelimit-remaining-{what}")
        if left is not None and left.strip() in ("0", "0.0"):
            s = seconds(h.get(f"x-ratelimit-reset-{what}"))
            waits.append(60.0 if s is None else s)
    return max(waits) if waits else None


class Limiter:
    def __init__(
        self,
        *,
        in_flight: int,
        per_minute: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.in_flight = max(1, in_flight)
        self.per_minute = max(1, per_minute)
        self.clock = clock
        self.sleep = sleep
        self._slots = threading.BoundedSemaphore(self.in_flight)
        self._lock = threading.Lock()
        self._started: collections.deque[float] = collections.deque()
        self._until = 0.0

    def hold(self, wait: float) -> None:
        """No request starts for `wait` seconds (at most LONGEST)."""
        with self._lock:
            self._until = max(self._until, self.clock() + min(max(0.0, wait), LONGEST))

    def held_for(self) -> float:
        """Seconds until requests may start again; 0 when they may now."""
        with self._lock:
            return max(0.0, self._until - self.clock())

    def observe(self, headers: Mapping[str, str]) -> None:
        """Take in what a reply's headers say about the limits."""
        wait = told(headers)
        if wait:
            self.hold(wait)

    def _wait_turn(self) -> None:
        while True:
            with self._lock:
                now = self.clock()
                while self._started and now - self._started[0] >= 60.0:
                    self._started.popleft()
                wait = self._until - now
                if wait <= 0 and len(self._started) < self.per_minute:
                    self._started.append(now)
                    return
                if wait <= 0:
                    wait = 60.0 - (now - self._started[0])
            self.sleep(min(max(wait, 0.01), 5.0))

    @contextmanager
    def slot(self) -> Iterator[None]:
        """Hold one of the request slots, once the limits allow one."""
        with self._slots:
            self._wait_turn()
            yield


_limiters: dict[tuple[str, str], Limiter] = {}
_limiters_lock = threading.Lock()


def shared(base_url: str, account: str, *, in_flight: int, per_minute: int) -> Limiter:
    """The one limiter for this provider account in this process. `account`
    identifies the key without being it (a hash)."""
    with _limiters_lock:
        key = (base_url.rstrip("/"), account)
        if key not in _limiters:
            _limiters[key] = Limiter(in_flight=in_flight, per_minute=per_minute)
        return _limiters[key]
