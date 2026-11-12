"""`hikidashi worker --backend hosted`: the server's API, run on another
machine with its own key (#125).

Its provider, models and key come from this machine's env only
(HIKIDASHI_*_PROVIDER / _MODEL, OPENAI_API_KEY or another preset's, and
HIKIDASHI_HOSTED_BUDGET, which is required), never from the server: the
server never sends a key to a worker. It works for the server named by
--server, with a worker token, and takes the reels the server's API would,
for the token's owner (`--for me`) or for all who rely on it (`--for all`).

Running it is switching it on; stopping it is switching it off. It claims
reels as they are ready, all or nothing, and on Ctrl-C or `docker stop`
finishes what is in flight and gives the rest back. A machine that sleeps
or dies lets its leases expire, which does the same.

Its budget is enforced here, against what the server has recorded of its
usage this month (which it reports with each result), so a restart does
not start the month again. A key the provider rejects, or a quota used up,
stops it with the reason.

Stdlib only.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from app.jobs import protocol
from app.jobs.protocol import REMOTE
from app.jobs.remote import HttpJobs, Missing
from app.providers.client import Outage, Unusable, Wait

from . import config, stages
from .config import Api
from .runner import OUTAGE, REEL_ERRORS

log = logging.getLogger("hikidashi.worker")

BATCH = 4
IDLE = (5.0, 60.0)
HEARTBEAT = 30.0


REEL_OR_MEDIA: tuple[type[BaseException], ...] = (*REEL_ERRORS, Missing)


class Stopped(Exception):
    """The worker cannot go on: the reason is why (a key, a quota, the budget)."""


def from_env(env: Mapping[str, str] | None = None) -> Api:
    """The API this machine's env describes. Nothing is read from a database
    or from the server."""
    env = os.environ if env is None else env
    mem = sqlite3.connect(":memory:")
    mem.row_factory = sqlite3.Row
    mem.execute("CREATE TABLE setting (user_id INTEGER, key TEXT, value TEXT)")
    return config.load(mem, None, Path("."), env)


class HostedWorker:
    def __init__(
        self,
        jobs: HttpJobs,
        api: Api,
        cache: Path,
        *,
        scope: str,
        batch: int = BATCH,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        if problems := api.problems():
            raise Stopped("not set up: " + "; ".join(problems))
        self.jobs = jobs
        self.api = api
        self.cache = cache
        self.scope = scope
        self.batch = batch
        self.sleep = sleep
        self.clock = clock
        self.budget = float(api.budget.value)
        self.spent = 0.0
        self.until = 0.0
        self.outages = 0
        self.started_at = time.time()
        self.stopped: str | None = None
        self._halt = threading.Event()
        self._lock = threading.Lock()

    # --- talking to the server ---------------------------------------------

    def _post(self, route: str, **body: Any) -> Any:
        return self.jobs._post(route, **body)

    def check_in(self, doing: str | None = None) -> None:
        self.jobs.check_in(stages=REMOTE, started_at=self.started_at, doing=doing, renew=True)

    # --- a round ---------------------------------------------------------------

    def round(self) -> int:
        """Claim and do one batch. Returns the reels done."""
        if self.clock() < self.until:
            return 0
        got = self._post("hosted/claim", scope=self.scope, limit=self.batch)
        self.spent = float(got.get("spent") or 0.0)
        reels: list[str] = got["reels"]
        if self.spent >= self.budget:
            self._give_back(reels)
            raise Stopped(f"monthly budget of {self.budget:g} USD reached")
        if not reels:
            return 0
        done = 0
        with ThreadPoolExecutor(max_workers=max(1, self.batch)) as pool:
            for ok in pool.map(self._reel, reels):
                done += ok
        if self.stopped:
            raise Stopped(self.stopped)
        return done

    def _give_back(self, codes: list[str]) -> None:
        for stage in REMOTE:
            if codes:
                self._post("release", stage=stage, shortcodes=codes, count=False)

    def _reel(self, code: str) -> bool:
        """Every remaining stage of one reel. True when it was all done."""
        for i, stage in enumerate(REMOTE):
            if self._halt.is_set():
                self._give_back([code])
                return False
            jobs = self._post("hosted/rows", stage=stage, shortcodes=[code])["jobs"]
            if not jobs:
                continue
            row = jobs[0]
            try:
                self._download(row, stage)
                done = stages.run(
                    stage,
                    row,
                    lambda s: (self.api.client(s), self.api.model(s)),
                    self.cache,
                    language=self.api.language.value,
                )
            except Unusable as exc:
                with self._lock:
                    self.stopped = str(exc)
                self._halt.set()
                self._give_back([code])
                return False
            except (Wait, Outage) as exc:
                wait = exc.seconds if isinstance(exc, Wait) else self._outage()
                self.until = self.clock() + wait
                log.warning("hosted: %s; waiting %.0fs", exc, wait)
                self._halt.set()
                self._give_back([code])
                return False
            except REEL_OR_MEDIA as exc:
                n = self._post("hosted/error", shortcode=code, reason=f"{stage}: {exc}")["errors"]
                log.warning("hosted: API error %d on %s at %s: %s", n, code, stage, exc)
                return False
            self.outages = 0
            usd = self._report(stage, code, done)
            with self._lock:
                self.spent += usd
                if self.spent >= self.budget:
                    self.stopped = f"monthly budget of {self.budget:g} USD reached"
                    self._halt.set()
            if self._halt.is_set() and i < len(REMOTE) - 1:
                self._give_back([code])
                return False
        # The stages it turned out not to need (OCR on a reel with speech).
        self._give_back([code])
        return True

    def _outage(self) -> float:
        self.outages += 1
        return float(min(OUTAGE[0] * 2 ** (self.outages - 1), OUTAGE[1]))

    def _download(self, row: dict[str, Any], stage: str) -> None:
        for path in protocol.media(row, stage):
            dest = self.cache / path
            if not dest.resolve().is_relative_to(self.cache.resolve()):
                raise Missing(f"refused a path outside the cache: {path}")
            if dest.is_file():
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            self.jobs.fetch(path, dest)

    def _report(self, stage: str, code: str, done: stages.Done) -> float:
        from .usage import cost

        usd = float(cost(done.profile, done.usage))
        u = done.usage
        self._post(
            "results",
            stage=stage,
            profile=done.profile,
            results=[protocol.ok(stage, code, done.produced)],
            languages={code: done.language} if stage == "asr" else None,
            usage=[
                {
                    "shortcode": code,
                    "model": done.profile,
                    "requests": done.requests,
                    "input_tokens": u.input_tokens,
                    "output_tokens": u.output_tokens,
                    "cached_tokens": u.cached_tokens,
                    "audio_seconds": u.audio_seconds,
                    "images": u.images,
                    "usd": usd,
                }
            ],
        )
        return usd

    # --- the loop ------------------------------------------------------------------

    def run(self) -> str:
        """Rounds until stopped. Returns why it stopped."""
        beat_done = threading.Event()

        def beat() -> None:
            while not beat_done.wait(HEARTBEAT):
                try:
                    self.check_in("hosted")
                except OSError as exc:
                    log.warning("check-in failed: %s", exc)

        threading.Thread(target=beat, name="heartbeat", daemon=True).start()
        self.jobs.reclaim()
        self.check_in()
        wait = IDLE[0]
        try:
            while True:
                self._halt.clear()
                try:
                    n = self.round()
                except OSError as exc:
                    log.warning("server unreachable: %s", exc)
                    n = 0
                if n:
                    wait = IDLE[0]
                    continue
                self.sleep(max(wait, self.until - self.clock()))
                wait = min(wait * 2, IDLE[1])
        except Stopped as exc:
            log.error("hosted worker stopped: %s", exc)
            return str(exc)
        finally:
            beat_done.set()
            try:
                self.jobs.check_out()  # what it holds goes back, uncounted
            except Exception as exc:
                log.warning("could not check out: %s", exc)
