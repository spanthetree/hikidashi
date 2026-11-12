"""The server's side of a hosted worker on another machine (#125).

`hikidashi worker --backend hosted` is the server's API run elsewhere, with
its own key, which the server never sees. It authenticates with a worker
token, which is an admin's, and takes the reels the server's API would:

    --for me     the token owner's reels
    --for all    reels where at least one saver relies on the server's API

after the same grace period, so users' own APIs are offered theirs first.
Unlike a local worker, which claims one stage at a time, it claims a reel for
all its remaining stages at once (protocol 2), asks for each stage's row as
it gets there, and sends results stage by stage, with what each used.

    claim    {scope, limit}               -> {reels, spent}
    rows     {stage, shortcodes}          -> {jobs: [row, ...]}
    results  as protocol 1, with profile provider:model, `languages` and
             `usage`, recorded against hosted:<worker>
    error    {shortcode, reason}          an API error on the reel: its
                                          stages back, uncounted

`spent` is its usage this month as the server recorded it, against which
the worker enforces its own budget.

Stdlib only.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from app.jobs.protocol import REMOTE
from app.jobs.protocol import rows as stage_rows
from app.jobs.queue import LEASE, LocalJobs
from app.providers.client import Usage

from . import config, eligible, usage
from .runner import lease_all

MAX_CLAIM = 16


def api_name(worker: str) -> str:
    return f"hosted:{worker}"


def claim(
    conn: sqlite3.Connection,
    worker: str,
    owner: int,
    scope: str,
    limit: int,
    data_dir: Path,
    *,
    now: int | None = None,
    grace: int = eligible.GRACE,
) -> dict[str, Any]:
    if scope not in config.SCOPES:
        raise ValueError("scope is me or all")
    now = int(time.time()) if now is None else now
    name = api_name(worker)
    api = config.load(conn, None, data_dir, env={})
    api.switch.on, api.switch.scope, api.switch.by = True, scope, owner
    codes = eligible.waiting(conn, api, name=name)
    if codes:
        eligible.mark_seen(conn, name, codes, now)
        codes = eligible.after_grace(conn, name, codes, now, grace)
    got = lease_all(conn, worker, codes, min(limit, MAX_CLAIM), now, LEASE)
    return {"reels": got, "spent": round(usage.spent(conn, name, now=now), 6)}


def rows(
    conn: sqlite3.Connection, worker: str, stage: str, codes: list[str]
) -> list[dict[str, Any]]:
    """The rows for the reels this worker holds at `stage` and that still
    need it."""
    held = set(LocalJobs(conn, worker).held().get(stage, []))
    return stage_rows(conn, stage, [c for c in codes if c in held])


def error(conn: sqlite3.Connection, worker: str, code: str, reason: str) -> int:
    """An API error on a reel: counted apart from workers' attempts, and
    every stage it held given back uncounted. Returns its errors so far."""
    jobs = LocalJobs(conn, worker)
    for stage in REMOTE:
        jobs.release(stage, [code], count=False)
    return eligible.count_error(conn, api_name(worker), code, reason, int(time.time()))


def record_usage(conn: sqlite3.Connection, worker: str, stage: str, items: Any) -> None:
    """The usage a hosted worker reports with its results, each item as
    {shortcode, model, requests, input_tokens, output_tokens, cached_tokens,
    audio_seconds, images, usd}. Its own cost estimate is kept as sent."""
    if not isinstance(items, list):
        return
    for u in items[:MAX_CLAIM]:
        if not isinstance(u, dict) or not isinstance(u.get("model"), str):
            continue

        def n(k: str, u: dict[str, Any] = u) -> int:
            v = u.get(k)
            ok = isinstance(v, int | float) and not isinstance(v, bool) and v >= 0
            return int(v) if ok else 0  # type: ignore[arg-type]

        used = Usage(
            input_tokens=n("input_tokens"),
            output_tokens=n("output_tokens"),
            cached_tokens=n("cached_tokens"),
            audio_seconds=float(n("audio_seconds")),
            images=n("images"),
        )
        usd = u.get("usd")
        usage.record(
            conn,
            api_name(worker),
            None,
            stage,
            u["model"][:200],
            used,
            requests=n("requests"),
            shortcode=u.get("shortcode") if isinstance(u.get("shortcode"), str) else None,
            usd=float(usd) if isinstance(usd, int | float) and 0 <= usd < 1000 else None,
        )
