"""The job model, and the job source that reads it straight from SQLite.

A stage's work is its own `pending` query: what is behind its version stamp.
On top of that, three tables (see schema.sql):

    job_lease    who holds which reel for which stage, until when
    job_attempt  how often a stage has tried a reel without finishing it
    job_worker   each worker's last check-in, for status

A claim, for one stage, is the stage's pending reels in its own order, less
those an earlier stage has still to do (readiness), those held by a live lease,
and those at the attempts cap. A stage may go ahead without an earlier one no
worker can run: the embedder without ASR, OCR or the tagger, from what the
reel has so far. A lease past
its time is taken back, and counts as an attempt: its worker died holding it.
A worker stopping cleanly gives its leases back uncounted.

A reel is counted when a worker releases it still pending, or when its lease
expires, and is not offered again for a minute, then two: what failed may
have been passing, like a file still being written or a GPU out of memory.
It is never counted for a stage that could not run at all, like a stopped
Ollama; the worker gives those back with count=False. Attempts are kept per
stage version, so a version bump starts every count again.

`LocalJobs` is the job source on the same host as the database. A remote one
(#35) implements `JobSource` over HTTP; the worker loop takes either.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.asr import worker as asr
from app.asr.transcriber import ASR_V
from app.embed import worker as embed
from app.embed.embedder import EMBED_V
from app.enrich import worker as enrich
from app.enrich.fetcher import FETCH_V
from app.ocr import worker as ocr
from app.ocr.reader import OCR_V
from app.recipes import worker as recipe
from app.recipes.llm import RECIPE_V
from app.tagging import worker as tag
from app.tagging.llm import TAG_V

from . import STAGES

LEASE = 300  # seconds a claim lasts; a working worker renews it every HEARTBEAT
HEARTBEAT = 30  # seconds between a worker's check-ins
STALE = 120  # a running worker not seen for this long is not responding
MAX_ATTEMPTS = 3  # tries at one stage before a reel is failed there
RETRY_AFTER = 60  # seconds before a reel that failed is tried again; doubles each time
FORGET = 7 * 86400  # a stopped worker's row is dropped after this long

Pending = Callable[[sqlite3.Connection, "list[str] | None"], list[str]]


@dataclass(frozen=True)
class Stage:
    name: str
    version: int
    # Every stage before this one: a reel waits while any of them still has it
    # to do. Transitive, so a reel is not tagged before it is even fetched.
    upstream: tuple[str, ...]
    fail_stage: str | None  # what the stage writes to item.fail_stage
    stamp: str  # the item column it stamps with its version
    pending: Pending
    # Upstream stages it stops waiting for while no worker can run them.
    skippable: tuple[str, ...] = ()


def _codes(rows: Iterable[Any]) -> list[str]:
    return [r["shortcode"] if isinstance(r, sqlite3.Row) else r.shortcode for r in rows]


REGISTRY: dict[str, Stage] = {
    s.name: s
    for s in (
        Stage(
            "enrich",
            FETCH_V,
            (),
            "fetch",
            "fetch_v",
            lambda c, only: _codes(enrich.pending(c, shortcodes=only)),
        ),
        Stage(
            "asr",
            ASR_V,
            ("enrich",),
            "asr",
            "asr_v",
            lambda c, only: _codes(asr.pending(c, shortcodes=only)),
        ),
        Stage(
            "ocr",
            OCR_V,
            ("enrich", "asr"),
            "ocr",
            "ocr_v",
            lambda c, only: _codes(ocr.pending(c, shortcodes=only)),
        ),
        Stage(
            "tag",
            TAG_V,
            ("enrich", "asr", "ocr"),
            "tag",
            "tag_v",
            lambda c, only: _codes(tag.pending(c, shortcodes=only)),
        ),
        Stage(
            "recipe",
            RECIPE_V,
            ("enrich", "asr", "ocr", "tag"),
            "recipe",
            "recipe_v",
            lambda c, only: _codes(recipe.pending(c, shortcodes=only)),
        ),
        # The embedder could run on captions alone, and does when run by hand.
        # A worker waits for the transcript, on-screen text and the tagger's
        # summary instead, so it embeds once, unless no worker can make them:
        # then it embeds from what there is rather than leave new reels out of
        # semantic search. A server whose ASR and tagging are done by hand on
        # a desktop (#86) is the case. Each, when it comes, changes the digest
        # and embeds the reel again.
        Stage(
            "embed",
            EMBED_V,
            ("enrich", "asr", "ocr", "tag"),
            None,
            "embed_v",
            lambda c, only: _codes(embed.pending(c, shortcodes=only)),
            skippable=("asr", "ocr", "tag"),
        ),
    )
}
assert tuple(REGISTRY) == STAGES


@dataclass
class Released:
    done: list[str] = field(default_factory=list)  # finished; their count cleared
    again: list[str] = field(default_factory=list)  # still to do, under the cap
    failed: list[str] = field(default_factory=list)  # still to do, and now at the cap


class JobSource(Protocol):
    """Where a worker gets its work and reports back. Local here; HTTP in #35."""

    name: str

    def claim(self, stage: str, limit: int) -> list[str]: ...

    def release(self, stage: str, shortcodes: list[str], *, count: bool = True) -> Released: ...

    def renew(self) -> None: ...

    def reclaim(self) -> int: ...

    def check_in(
        self,
        *,
        stages: tuple[str, ...],
        started_at: float,
        doing: str | None = None,
        problems: dict[str, str] | None = None,
    ) -> None: ...

    def check_out(self) -> None: ...


@contextmanager
def _immediate(conn: sqlite3.Connection) -> Iterator[None]:
    """A write transaction taken at once, so two claims cannot interleave."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def unrunnable(conn: sqlite3.Connection, *, now: float | None = None) -> set[str]:
    """Stages no worker can run now: no running worker has them, or every one
    that does reports them as a problem, like the tagger with its Ollama off.
    A stopped or lost worker runs nothing."""
    able = {
        s
        for w in workers(conn, now=now)
        if w["state"] == "running"
        for s in w["stages"]
        if s not in w["problems"]
    }
    return set(STAGES) - able


def skipped(conn: sqlite3.Connection, stage: str, *, now: float | None = None) -> tuple[str, ...]:
    """The upstream stages `stage` is going ahead without, as no worker can
    run them."""
    skippable = REGISTRY[stage].skippable
    if not skippable:
        return ()
    off = unrunnable(conn, now=now)
    return tuple(s for s in skippable if s in off)


def upstream(conn: sqlite3.Connection, stage: str, *, now: float | None = None) -> tuple[str, ...]:
    """The stages `stage` waits for now: its upstream, less those it is
    going ahead without."""
    skip = skipped(conn, stage, now=now)
    return tuple(u for u in REGISTRY[stage].upstream if u not in skip)


def capped(conn: sqlite3.Connection, stage: str, *, cap: int = MAX_ATTEMPTS) -> set[str]:
    """Reels at the attempts cap for `stage`, at its current version."""
    return {
        r[0]
        for r in conn.execute(
            """SELECT shortcode FROM job_attempt
               WHERE stage = ? AND version = ? AND attempts >= ?""",
            (stage, REGISTRY[stage].version, cap),
        )
    }


def cooling(
    conn: sqlite3.Connection, stage: str, now: float, *, cap: int, after: float
) -> set[str]:
    """Reels that failed at `stage` lately, waiting out the pause before their
    next try: `after` seconds since the last, doubled for each before it."""
    return {
        r[0]
        for r in conn.execute(
            """SELECT shortcode FROM job_attempt
               WHERE stage = ? AND version = ? AND attempts < ?
                 AND last_at + ? * (1 << (attempts - 1)) > ?""",
            (stage, REGISTRY[stage].version, cap, after, now),
        )
    }


def _count(conn: sqlite3.Connection, stage: str, reasons: dict[str, str], now: int) -> None:
    conn.executemany(
        """INSERT INTO job_attempt (shortcode, stage, version, attempts, reason, last_at)
           VALUES (?, ?, ?, 1, ?, ?)
           ON CONFLICT (shortcode, stage) DO UPDATE SET
             attempts = CASE WHEN job_attempt.version = excluded.version
                             THEN job_attempt.attempts + 1 ELSE 1 END,
             version  = excluded.version,
             reason   = excluded.reason,
             last_at  = excluded.last_at""",
        [(code, stage, REGISTRY[stage].version, why[:200], now) for code, why in reasons.items()],
    )


DIED = "its worker stopped before finishing it"


class LocalJobs:
    """Claims straight from the SQLite file, for a worker on the same host."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        name: str,
        *,
        lease: float = LEASE,
        cap: int = MAX_ATTEMPTS,
        retry_after: float = RETRY_AFTER,
        clock: Callable[[], float] = time.time,
    ):
        self.conn = conn
        self.name = name
        self.lease = lease
        self.cap = cap
        self.retry_after = retry_after
        self.clock = clock

    def _now(self) -> int:
        return int(self.clock())

    def _open(self, stages: Iterable[str]) -> set[str]:
        """Reels any of `stages` still has to do and has not given up on."""
        out: set[str] = set()
        for name in stages:
            out |= set(REGISTRY[name].pending(self.conn, None)) - capped(
                self.conn, name, cap=self.cap
            )
        return out

    def _expire(self, stage: str, now: int, *, worker: str | None = None) -> None:
        """Take back leases past their time, or all of `worker`'s, counting
        an attempt for each: whoever held them died holding them."""
        if worker is None:
            rows = self.conn.execute(
                "SELECT shortcode FROM job_lease WHERE stage = ? AND expires_at <= ?",
                (stage, now),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT shortcode FROM job_lease WHERE stage = ? AND worker = ?", (stage, worker)
            ).fetchall()
        if not rows:
            return
        codes = [r[0] for r in rows]
        _count(self.conn, stage, dict.fromkeys(codes, DIED), now)
        self._mark_failed(stage, codes)
        self.conn.executemany(
            "DELETE FROM job_lease WHERE stage = ? AND shortcode = ?",
            [(stage, c) for c in codes],
        )

    def _mark_failed(self, stage: str, codes: list[str]) -> None:
        """Record a reel at the cap as failed at `stage` on the item itself,
        when nothing is recorded there: one that killed its worker never got
        to say so. The later stages read item.fail_stage to stop waiting for
        this one, as they do when it fails the ordinary way. Not for enrich,
        whose failures mean the post is gone, which a crash does not say."""
        info = REGISTRY[stage]
        if not info.fail_stage or stage == "enrich":
            return
        self.conn.execute(
            """UPDATE item SET fail_stage = :stage, fail_reason = a.reason
               FROM job_attempt AS a
               WHERE a.shortcode = item.shortcode AND a.stage = :name
                 AND a.version = :v AND a.attempts >= :cap
                 AND item.fail_stage IS NULL
                 AND item.shortcode IN (SELECT value FROM json_each(:codes))""",
            {
                "stage": info.fail_stage,
                "name": stage,
                "v": info.version,
                "cap": self.cap,
                "codes": json.dumps(codes),
            },
        )

    def claim(self, stage: str, limit: int) -> list[str]:
        """Lease up to `limit` reels that are ready for `stage`, in its order."""
        todo = REGISTRY[stage].pending(self.conn, None)
        if not todo:
            return []
        # Read outside the transaction: a stale answer only costs a reel that
        # turns out to be done already, which the stage itself skips.
        now = self._now()
        waiting = self._open(upstream(self.conn, stage, now=now))
        with _immediate(self.conn):
            # A reel no longer pending has nothing left to count against it.
            self.conn.execute(
                """DELETE FROM job_attempt WHERE stage = ?
                   AND shortcode NOT IN (SELECT value FROM json_each(?))""",
                (stage, json.dumps(todo)),
            )
            self._expire(stage, now)
            held = {
                r[0]
                for r in self.conn.execute(
                    "SELECT shortcode FROM job_lease WHERE stage = ?", (stage,)
                )
            }
            skip = (
                waiting
                | held
                | capped(self.conn, stage, cap=self.cap)
                | cooling(self.conn, stage, now, cap=self.cap, after=self.retry_after)
            )
            got = [c for c in todo if c not in skip][: max(0, limit)]
            self.conn.executemany(
                """INSERT INTO job_lease (shortcode, stage, worker, expires_at)
                   VALUES (?, ?, ?, ?)""",
                [(c, stage, self.name, now + int(self.lease)) for c in got],
            )
        return got

    def release(self, stage: str, shortcodes: list[str], *, count: bool = True) -> Released:
        """Give leases back. Whatever is still pending is counted, unless
        `count` is False: the stage could not run, which is not the reel's fault."""
        info = REGISTRY[stage]
        still = set(info.pending(self.conn, shortcodes))
        out = Released(done=[c for c in shortcodes if c not in still])
        now = self._now()
        with _immediate(self.conn):
            self.conn.executemany(
                "DELETE FROM job_attempt WHERE stage = ? AND shortcode = ?",
                [(stage, c) for c in out.done],
            )
            if count and still:
                why = {
                    r["shortcode"]: (
                        r["fail_reason"]
                        if info.fail_stage and r["fail_stage"] == info.fail_stage
                        else None
                    )
                    or "tried, and not finished"
                    for r in self.conn.execute(
                        """SELECT shortcode, fail_stage, fail_reason FROM item
                           WHERE shortcode IN (SELECT value FROM json_each(?))""",
                        (json.dumps(sorted(still)),),
                    )
                }
                _count(self.conn, stage, why, now)
                self._mark_failed(stage, list(why))
                at_cap = capped(self.conn, stage, cap=self.cap)
                for c in shortcodes:
                    if c in still:
                        (out.failed if c in at_cap else out.again).append(c)
            self.conn.executemany(
                "DELETE FROM job_lease WHERE stage = ? AND shortcode = ? AND worker = ?",
                [(stage, c, self.name) for c in shortcodes],
            )
        return out

    def renew(self) -> None:
        """Push back the expiry of every lease this worker holds."""
        self.conn.execute(
            "UPDATE job_lease SET expires_at = ? WHERE worker = ?",
            (self._now() + int(self.lease), self.name),
        )

    def held(self) -> dict[str, list[str]]:
        """This worker's leases, by stage."""
        out: dict[str, list[str]] = {}
        for r in self.conn.execute(
            "SELECT stage, shortcode FROM job_lease WHERE worker = ? ORDER BY stage", (self.name,)
        ):
            out.setdefault(r["stage"], []).append(r["shortcode"])
        return out

    def reclaim(self) -> int:
        """At start: leases still under this worker's name are a previous run's,
        which died holding them. Take them back and count them. Returns how many."""
        mine = self.held()
        now = self._now()
        with _immediate(self.conn):
            for stage in mine:
                self._expire(stage, now, worker=self.name)
        return sum(map(len, mine.values()))

    def check_in(
        self,
        *,
        stages: tuple[str, ...],
        started_at: float,
        doing: str | None = None,
        problems: dict[str, str] | None = None,
        pid: int | None = None,
    ) -> None:
        """Say this worker is alive, and what it is doing. `pid` is the
        worker's own when it checks in through the server (#35)."""
        now = self._now()
        self.conn.execute(
            """INSERT INTO job_worker
                 (name, pid, stages, started_at, seen_at, stopped_at, doing, problems)
               VALUES (:name, :pid, :stages, :started, :now, NULL, :doing, :problems)
               ON CONFLICT (name) DO UPDATE SET
                 pid = excluded.pid, stages = excluded.stages,
                 started_at = excluded.started_at, seen_at = excluded.seen_at,
                 stopped_at = NULL, doing = excluded.doing, problems = excluded.problems""",
            {
                "name": self.name,
                "pid": pid or os.getpid(),
                "stages": json.dumps(list(stages)),
                "started": int(started_at),
                "now": now,
                "doing": doing,
                "problems": json.dumps(problems) if problems else None,
            },
        )
        self.conn.execute(
            "DELETE FROM job_worker WHERE MAX(seen_at, COALESCE(stopped_at, 0)) < ?",
            (now - FORGET,),
        )

    def check_out(self) -> None:
        """A clean stop: give every lease back uncounted, and say so."""
        for stage, codes in self.held().items():
            self.release(stage, codes, count=False)
        self.conn.execute(
            "UPDATE job_worker SET stopped_at = ?, doing = NULL WHERE name = ?",
            (self._now(), self.name),
        )


# --- status ------------------------------------------------------------------


def queue(
    conn: sqlite3.Connection, *, cap: int = MAX_ATTEMPTS, now: float | None = None
) -> list[dict[str, Any]]:
    """Per stage: reels ready to claim, waiting (on an earlier stage, or out
    the pause before another try), being worked on, and failed at the cap;
    and the earlier stages it is going ahead without (`without`), as no
    worker can run them.

    Waiting counts every reel an earlier stage still has to do that this one
    has not stamped: the stages' own queries leave out what they are waiting
    for, so it cannot come from them. It may include a reel the stage will
    turn out to have nothing to do for, like OCR for a reel with speech."""
    now = time.time() if now is None else now
    todo = {s: set(REGISTRY[s].pending(conn, None)) for s in STAGES}
    gave_up = {s: capped(conn, s, cap=cap) & todo[s] for s in STAGES}
    leased: dict[str, set[str]] = {s: set() for s in STAGES}
    for r in conn.execute("SELECT stage, shortcode FROM job_lease WHERE expires_at > ?", (now,)):
        if r["stage"] in leased:
            leased[r["stage"]].add(r["shortcode"])
    off = unrunnable(conn, now=now)
    out = []
    for s in STAGES:
        info = REGISTRY[s]
        without = [u for u in info.skippable if u in off]
        upstream = set().union(*(todo[u] - gave_up[u] for u in info.upstream if u not in without))
        behind = (
            {
                r[0]
                for r in conn.execute(
                    f"SELECT shortcode FROM item WHERE {info.stamp} < ?", (info.version,)
                )
            }
            if upstream
            else set()
        )
        working = (leased[s] & todo[s]) - gave_up[s]
        pausing = cooling(conn, s, now, cap=cap, after=RETRY_AFTER) & todo[s]
        waiting = (((todo[s] | behind) & upstream) | pausing) - gave_up[s] - working
        out.append(
            {
                "stage": s,
                "ready": len(todo[s] - gave_up[s] - working - upstream - pausing),
                "waiting": len(waiting),
                "working": len(working),
                "failed": len(gave_up[s]),
                "without": without,
            }
        )
    return out


def failures(
    conn: sqlite3.Connection, stage: str, *, cap: int = MAX_ATTEMPTS, limit: int = 20
) -> list[dict[str, Any]]:
    """The reels failed at `stage`, most recent first, with why."""
    todo = set(REGISTRY[stage].pending(conn, None))
    rows = conn.execute(
        """SELECT shortcode, attempts, reason, last_at FROM job_attempt
           WHERE stage = ? AND version = ? AND attempts >= ?
           ORDER BY last_at DESC, shortcode""",
        (stage, REGISTRY[stage].version, cap),
    ).fetchall()
    return [dict(r) for r in rows if r["shortcode"] in todo][:limit]


def retry(conn: sqlite3.Connection, stages: Iterable[str], *, cap: int = MAX_ATTEMPTS) -> int:
    """Give every reel failed at `stages` another `cap` tries. Returns how many."""
    n = 0
    for s in stages:
        n += conn.execute(
            "DELETE FROM job_attempt WHERE stage = ? AND attempts >= ?", (s, cap)
        ).rowcount
    return n


def workers(conn: sqlite3.Connection, *, now: float | None = None) -> list[dict[str, Any]]:
    """Every worker that has checked in lately, newest first, with its state:
    running; stopped, having said so; or lost, gone quiet without a word, which
    is a crash, a kill or a host that went away."""
    now = time.time() if now is None else now
    out = []
    for r in conn.execute("SELECT * FROM job_worker ORDER BY seen_at DESC, name"):
        if r["stopped_at"] is not None:
            state = "stopped"
        elif now - r["seen_at"] > STALE:
            state = "lost"
        else:
            state = "running"
        out.append(
            {
                "name": r["name"],
                "pid": r["pid"],
                "state": state,
                "stages": json.loads(r["stages"]),
                "started_at": r["started_at"],
                "seen_at": r["seen_at"],
                "stopped_at": r["stopped_at"],
                "doing": r["doing"] if state == "running" else None,
                "problems": json.loads(r["problems"] or "{}") if state == "running" else {},
            }
        )
    return out


def overall(found: list[dict[str, Any]]) -> str:
    """The workers in a word, the most telling first: `problem` (running, and
    a stage it runs cannot), `running`, `lost` (one stopped checking in
    without a word), `stopped`, or `none` (none has ever checked in)."""
    states = {w["state"] for w in found}
    if any(w["state"] == "running" and w["problems"] for w in found):
        return "problem"
    for state in ("running", "lost", "stopped"):
        if state in states:
            return state
    return "none"
