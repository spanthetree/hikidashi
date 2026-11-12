"""The switched-on APIs as workers in the job queue (#125).

Each API whose switch is on (a user's, `api:user:<id>`, and the server's,
`api:server`) runs as a claimer in the server's own worker process
(`hikidashi worker`, wherever it runs enrich; see app/jobs/__main__.py). The
provider client is stdlib only, so the server image needs no faster-whisper,
tesseract or Ollama.

    claiming    a round takes the reels the API may take now (eligible.py)
                and leases every worker stage each still needs at once: one
                job_lease row per stage, under the API's name. A worker's
                claim skips a reel with a lease for its stage, so no worker
                takes a later stage of a reel the API holds. All or nothing.
    running     each reel's stages in pipeline order, each recorded with the
                stage's own `record` as a worker's are, and its usage with
                its cost. Embed and places then pick the reel up as usual.
    errors      the provider unusable (key, quota): the switch goes off, with
                the reason. Not now (a rate limit, a daily cap, an outage):
                the reels go back, and the API waits. This one reel failed:
                an API error on it, counted apart from workers' attempts,
                never failing it; after MAX_ERRORS the API leaves it.
    off         a switch turned off stops claiming. The reel in hand
                finishes its request; what is not sent goes back to workers,
                uncounted. The monthly budget reached turns it off.
    crashes     leases expire and the reels go back, as for any worker. At
                most the requests in flight are sent again.

Stdlib only.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from app.asr.transcriber import AudioError
from app.jobs.protocol import REMOTE, ROWS, record_one
from app.jobs.queue import HEARTBEAT, LEASE, LocalJobs, _immediate
from app.ocr.reader import OcrError
from app.ollama import ReplyError
from app.providers.client import Outage, RequestFailed, Unusable, Wait

from . import config, eligible, service, stages, usage
from .config import Api

log = logging.getLogger("hikidashi.api")

BATCH = 4  # reels claimed a round, and run at once
IDLE = 10.0  # seconds between rounds that found nothing
OUTAGE = (30.0, 900.0)  # first and longest wait after an outage

# A reel this one request could not do: an API error on it.
REEL_ERRORS: tuple[type[BaseException], ...] = (
    RequestFailed,
    ReplyError,
    AudioError,
    OcrError,
)


@dataclass
class Round:
    claimed: int = 0
    done: int = 0
    errors: int = 0
    usd: float = 0.0
    stopped: str | None = None  # why the round stopped early


@dataclass
class Claimer:
    """One API in the job queue."""

    connect: Callable[[], sqlite3.Connection]
    owner: int | None
    data_dir: Path
    media_dir: Path
    batch: int = BATCH
    grace: int = eligible.GRACE
    max_errors: int = eligible.MAX_ERRORS
    lease: float = LEASE
    clock: Callable[[], float] = time.time
    until: float = 0.0  # no claims before this: a rate limit or an outage
    _outages: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def name(self) -> str:
        return "api:server" if self.owner is None else f"api:user:{self.owner}"

    def _jobs(self, conn: sqlite3.Connection) -> LocalJobs:
        return LocalJobs(conn, self.name, lease=self.lease, clock=self.clock)

    def held(self, conn: sqlite3.Connection) -> dict[str, list[str]]:
        return self._jobs(conn).held()

    def release_all(self, conn: sqlite3.Connection) -> int:
        """Give every reel back to workers, uncounted. Returns how many."""
        mine = self.held(conn)
        jobs = self._jobs(conn)
        for stage, codes in mine.items():
            jobs.release(stage, codes, count=False)
        return len({c for codes in mine.values() for c in codes})

    # --- claiming -----------------------------------------------------------

    def claim(self, conn: sqlite3.Connection, api: Api) -> list[str]:
        """Lease every worker stage of up to `batch` reels the API may take."""
        now = int(self.clock())
        codes = eligible.waiting(conn, api)
        if self.owner is None and codes:
            eligible.mark_seen(conn, self.name, codes, now)
            codes = eligible.after_grace(conn, self.name, codes, now, self.grace)
        got: list[str] = []
        with _immediate(conn):
            for code in codes:
                if len(got) >= self.batch:
                    break
                # Read again inside the transaction: a worker or another API
                # may have just taken one of its stages.
                if conn.execute(
                    f"""SELECT 1 FROM job_lease WHERE shortcode = ? AND expires_at > ?
                        AND stage IN ({",".join("?" * len(REMOTE))})""",
                    (code, now, *REMOTE),
                ).fetchone():
                    continue
                conn.execute(
                    f"""DELETE FROM job_lease WHERE shortcode = ?
                        AND stage IN ({",".join("?" * len(REMOTE))})""",
                    (code, *REMOTE),
                )  # an expired lease, whoever's
                conn.executemany(
                    """INSERT INTO job_lease (shortcode, stage, worker, expires_at)
                       VALUES (?, ?, ?, ?)""",
                    [(code, s, self.name, now + int(self.lease)) for s in REMOTE],
                )
                got.append(code)
        return got

    # --- one round -------------------------------------------------------------

    def round(self) -> Round:
        """Claim and run one batch, if the switch is on. Returns how it went."""
        out = Round()
        conn = self.connect()
        try:
            api = config.load(conn, self.owner, self.data_dir)
            if not api.switch.on:
                if n := self.release_all(conn):
                    log.info("%s: off; gave %d reels back to workers", self.name, n)
                out.stopped = "off"
                return out
            if self.clock() < self.until:
                out.stopped = "waiting"
                return out
            if (budget := api.budget.value) and usage.spent(conn, self.name) >= budget:
                self._off(conn, f"monthly budget of {budget:g} USD reached")
                out.stopped = "budget"
                return out
            codes = self.claim(conn, api)
            out.claimed = len(codes)
            if not codes:
                return out
        finally:
            conn.close()
        self._run_all(api, codes, out)
        return out

    def _run_all(self, api: Api, codes: list[str], out: Round) -> None:
        stop = threading.Event()
        finished = threading.Event()

        def beat() -> None:
            # A long transcription outlasts a lease: renew, as workers do.
            while not finished.wait(HEARTBEAT):
                conn = self.connect()
                try:
                    self._jobs(conn).renew()
                except sqlite3.Error as exc:
                    log.warning("%s: renewing leases failed: %s", self.name, exc)
                finally:
                    conn.close()

        threading.Thread(target=beat, name=f"{self.name}-beat", daemon=True).start()

        def one(code: str) -> None:
            if stop.is_set():
                return
            why = self._reel(api, code, out)
            if why is not None:
                stop.set()
                with self._lock:
                    out.stopped = out.stopped or why

        try:
            with ThreadPoolExecutor(max_workers=max(1, self.batch)) as pool:
                for f in [pool.submit(one, c) for c in codes]:
                    f.result()
        finally:
            finished.set()
        conn = self.connect()
        try:
            # What was claimed and not run (a stop), back to workers.
            if stop.is_set():
                self.release_all(conn)
        finally:
            conn.close()

    def _reel(self, api: Api, code: str, out: Round) -> str | None:
        """Every remaining stage of one reel. Returns why the API must stop,
        or None to go on."""
        conn = self.connect()
        jobs = self._jobs(conn)
        try:
            for stage in REMOTE:
                if not config.load(conn, self.owner, self.data_dir).switch.on:
                    return "off"
                rows = ROWS[stage](conn, shortcodes=[code])
                if not rows:
                    continue  # not needed: done, or nothing for it to do (OCR with speech)
                if stage == "tag":
                    from app.tagging.service import apply_hashtags, ensure_taxonomy

                    ensure_taxonomy(conn)
                    apply_hashtags(conn)
                try:
                    done = stages.run(
                        stage,
                        rows[0],
                        lambda s: (api.client(s), api.model(s)),
                        self.media_dir,
                        language=api.language.value,
                    )
                except Unusable as exc:
                    self._off(conn, str(exc))
                    return "unusable"
                except Wait as exc:
                    self.until = self.clock() + exc.seconds
                    log.info("%s: %s; waiting %.0fs", self.name, exc, exc.seconds)
                    return "wait"
                except Outage as exc:
                    self._outages += 1
                    wait = min(OUTAGE[0] * 2 ** (self._outages - 1), OUTAGE[1])
                    self.until = self.clock() + wait
                    log.warning("%s: %s; trying again in %.0fs", self.name, exc, wait)
                    return "outage"
                except REEL_ERRORS as exc:
                    n = eligible.count_error(
                        conn, self.name, code, f"{stage}: {exc}", int(self.clock())
                    )
                    with self._lock:
                        out.errors += 1
                    if n >= self.max_errors:
                        log.warning(
                            "%s: left %s to workers after %d API errors: %s",
                            self.name,
                            code,
                            n,
                            exc,
                        )
                    return None
                self._outages = 0
                record_one(
                    conn,
                    stage,
                    code,
                    done.produced,
                    None,
                    profile=done.profile,
                    language=done.language,
                )
                cost = usage.record(
                    conn,
                    self.name,
                    self.owner,
                    stage,
                    done.profile,
                    done.usage,
                    requests=done.requests,
                    shortcode=code,
                )
                with self._lock:
                    out.usd += cost
                jobs.renew()
            with self._lock:
                out.done += 1
            return None
        finally:
            for stage in REMOTE:
                jobs.release(stage, [code], count=False)
            conn.close()

    def _off(self, conn: sqlite3.Connection, reason: str) -> None:
        log.warning("%s: switched off: %s", self.name, reason)
        service.turn_off(conn, None, self.owner, self.data_dir, reason=reason)
        self.release_all(conn)


class Host:
    """Runs a round of every API that is on, in a thread of the worker
    process; and one more for an API just turned off, to give its reels
    back. Each round runs its batch's reels at once."""

    def __init__(
        self,
        connect: Callable[[], sqlite3.Connection],
        data_dir: Path,
        media_dir: Path,
        *,
        idle: float = IDLE,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.connect = connect
        self.data_dir = data_dir
        self.media_dir = media_dir
        self.idle = idle
        self.sleep = sleep
        self.claimers: dict[int | None, Claimer] = {}
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None

    def owners(self) -> set[int | None]:
        """The APIs that are on, and those still holding reels."""
        conn = self.connect()
        try:
            out: set[int | None] = set(config.switched_on(conn))
            for (worker,) in conn.execute(
                "SELECT DISTINCT worker FROM job_lease WHERE worker LIKE 'api:%'"
            ):
                if worker == "api:server":
                    out.add(None)
                elif worker.startswith("api:user:"):
                    out.add(int(worker.rsplit(":", 1)[1]))
            return out
        finally:
            conn.close()

    def once(self) -> int:
        """One round of every API. Returns the reels done."""
        done = 0
        for owner in self.owners():
            c = self.claimers.setdefault(
                owner, Claimer(self.connect, owner, self.data_dir, self.media_dir)
            )
            try:
                r = c.round()
            except Exception:
                log.exception("%s: round failed", c.name)
                continue
            if r.claimed:
                log.info(
                    "%s: %d/%d done, %d API errors, %.4f USD%s",
                    c.name, r.done, r.claimed, r.errors, r.usd,
                    f", stopped: {r.stopped}" if r.stopped else "",
                )  # fmt: skip
            done += r.done
        return done

    def run(self) -> None:
        while not self._stopped.is_set():
            if not self.once():
                self._stopped.wait(self.idle)

    def start(self) -> None:
        self._thread = threading.Thread(target=self.run, name="hosted-apis", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop claiming; what is held goes back to workers."""
        self._stopped.set()
        if self._thread:
            self._thread.join(timeout=30)
        conn = self.connect()
        try:
            for c in self.claimers.values():
                c.release_all(conn)
        finally:
            conn.close()
