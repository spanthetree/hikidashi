"""The locator: the places stage, run by the server as reels arrive.

Started with the server when a geocoder is set. Each pass does what
`hikidashi places` does: reads the places new captions name, looks up the
ones not looked up before, and takes a second look at any far from the rest of
their reel. Then it waits, `every` seconds or until woken, as an import
through the web wakes it. Captions that arrive another way, from an import or
enrich run on the command line, are found by the next pass.

One locator at a time, across processes: looking up holds places.lock, as
`hikidashi places` does, so the public server is never asked twice as often
as it allows. A pass that finds the lock held skips; its holder does the work.

When the geocoder fails, each wait is twice the last, from a minute up to an
hour. Every place found is saved as it is found, so the next pass resumes.
"""

from __future__ import annotations

import fcntl
import logging
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .geocoder import GeocodeError
from .worker import locate_all, pending, read_all, refine

LOCK = "places.lock"
BACKOFF = (60.0, 3600.0)  # first and longest wait after a failure, seconds

log = logging.getLogger(__name__)


class Busy(Exception):
    """Another locator holds the lock."""


@contextmanager
def places_lock(run_dir: Path) -> Iterator[None]:
    """Hold the lookup lock for the duration. Raises Busy if it is taken.
    flock, so a killed holder never leaves it stale; and per open file, so two
    threads of one process exclude each other too."""
    run_dir.mkdir(parents=True, exist_ok=True)
    with (run_dir / LOCK).open("a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Busy from None
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def locating(run_dir: Path) -> bool:
    """Whether a locator is looking places up now."""
    try:
        with places_lock(run_dir):
            return False
    except Busy:
        return True


class Locator:
    def __init__(
        self,
        connect: Callable[[], sqlite3.Connection],
        search: Callable[..., list[dict[str, Any]]],
        run_dir: Path,
        *,
        every: float,
        backoff: tuple[float, float] = BACKOFF,
    ):
        self.connect = connect
        self.search = search
        self.run_dir = run_dir
        self.every = every
        self.backoff = backoff
        self.failures = 0
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="locator", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)

    def wake(self) -> None:
        """Look now, not at the end of the wait: new reels are in."""
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            wait = self.step()
            self._wake.wait(wait)
            self._wake.clear()

    def step(self) -> float:
        """One pass. Returns how long to wait before the next."""
        try:
            self.run_once()
        except Exception as exc:
            self.failures += 1
            wait = float(min(self.backoff[0] * 2 ** (self.failures - 1), self.backoff[1]))
            if isinstance(exc, GeocodeError):
                log.warning("locator: %s; trying again in %.0fs", exc, wait)
            else:
                log.exception("locator failed; trying again in %.0fs", wait)
            return wait
        self.failures = 0
        return self.every

    def run_once(self) -> int:
        """Read new captions, then look up what they name. Returns how many
        places were looked up: none when another locator holds the lock."""
        conn = self.connect()
        try:
            read = read_all(conn)
            if not read and not pending(conn, limit=1):
                return 0
            try:
                with places_lock(self.run_dir):
                    done = locate_all(conn, self.search)
                    again = refine(conn, self.search)
            except Busy:
                return 0
        finally:
            conn.close()
        if done.total or again.moved or again.doubted:
            log.info(
                "locator: %d looked up, %d on the map, %d approximate, "
                "%d not found; %d moved nearer the rest of their reel",
                done.total,
                done.located,
                done.approximate,
                done.missing,
                again.moved,
            )
        return done.total
