"""How the hosted APIs are doing, for the dashboard, `hikidashi status` and
`hikidashi ai status` (#125); a user's view of their own reels; and putting
reels an older model made back in the queue.

Stdlib only.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from app.jobs.protocol import REMOTE
from app.jobs.queue import REGISTRY, workers

from . import config, eligible, usage

# The column each stage stamps its provider:model in, and its version stamp.
PROFILE = {
    "asr": "asr_profile",
    "ocr": "ocr_profile",
    "tag": "tag_profile",
    "recipe": "recipe_profile",
}


def one(conn: sqlite3.Connection, owner: int | None, data_dir: Path) -> dict[str, Any]:
    """One API: its switch (and why it went off), reels waiting for it and
    in flight, its API errors, this month's spend and budget."""
    api = config.load(conn, owner, data_dir)
    held = {
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT shortcode FROM job_lease WHERE worker = ? AND expires_at > ?",
            (api.name, int(time.time())),
        )
    }
    errors = conn.execute(
        """SELECT COUNT(*) FROM api_reel WHERE api = ? AND errors >= ?""",
        (api.name, eligible.MAX_ERRORS),
    ).fetchone()[0]
    return {
        "name": api.name,
        "owner": owner,
        "on": api.switch.on,
        "scope": api.switch.scope,
        "reason": api.switch.reason,
        "since": api.switch.at,
        "waiting": len(eligible.waiting(conn, api)) if api.switch.on else 0,
        "in_flight": len(held),
        "left_to_workers": errors,
        "spent": round(usage.spent(conn, api.name), 4),
        "budget": api.budget.value,
        "providers": sorted({s.preset.label for s in api.stages.values() if s.preset}),
    }


def apis(conn: sqlite3.Connection, data_dir: Path) -> list[dict[str, Any]]:
    """Every API that is on, or has been set up, or went off by itself; and
    the hosted workers that reported usage this month."""
    owners: list[int | None] = [None]
    owners += [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT user_id FROM setting WHERE user_id IS NOT NULL AND key = 'switch'"
        )
    ]
    out = [one(conn, o, data_dir) for o in owners]
    out = [a for a in out if a["on"] or a["reason"] or a["spent"] or a["owner"] is None]
    start, end, _ = usage.month_bounds()
    for r in conn.execute(
        """SELECT api, ROUND(SUM(usd), 4) AS spent, MAX(at) AS last FROM hosted_usage
           WHERE api LIKE 'hosted:%' AND at >= ? AND at < ? GROUP BY api""",
        (start, end),
    ):
        out.append(
            {"name": r["api"], "owner": None, "on": None, "spent": r["spent"], "last": r["last"]}
        )
    return out


def mine(conn: sqlite3.Connection, user_id: int, data_dir: Path) -> dict[str, Any]:
    """A user's own reels: waiting per stage, when a worker last checked in,
    and their own API. Nothing of anyone else's reels or spend."""
    codes = [
        r[0] for r in conn.execute("SELECT shortcode FROM user_item WHERE user_id = ?", (user_id,))
    ]
    stages = []
    for s in ("enrich", *REMOTE, "embed"):
        todo = set(REGISTRY[s].pending(conn, codes)) if codes else set()
        stages.append({"stage": s, "waiting": len(todo)})
    seen = [w["seen_at"] for w in workers(conn) if w["state"] == "running"]
    from .service import applies

    api = one(conn, user_id, data_dir)
    return {
        "stages": stages,
        "worker_seen_at": max(seen) if seen else None,
        "applies": applies(conn, user_id, data_dir),
        "api": {k: api[k] for k in ("on", "reason", "in_flight", "spent", "budget", "waiting")},
        "now": int(time.time()),
    }


def requeue(conn: sqlite3.Connection, stage: str, made_by: str) -> int:
    """Put the reels `made_by` (a profile: provider:model, or cpu or cuda)
    made at `stage` back in its queue, for whatever takes them: an API
    that is on, or a worker. Returns how many."""
    if stage not in PROFILE:
        raise ValueError(f"use one of {', '.join(PROFILE)}")
    stamp = REGISTRY[stage].stamp
    n = conn.execute(
        f"UPDATE item SET {stamp} = 0 WHERE {PROFILE[stage]} = ? AND {stamp} > 0", (made_by,)
    ).rowcount
    return int(n)


def profiles(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    """Per stage, which profiles made how many reels: what requeue offers."""
    out: dict[str, list[dict[str, Any]]] = {}
    for stage, col in PROFILE.items():
        out[stage] = [
            {"profile": r[0], "reels": r[1]}
            for r in conn.execute(
                f"""SELECT {col}, COUNT(*) FROM item WHERE {col} IS NOT NULL
                    GROUP BY {col} ORDER BY COUNT(*) DESC"""
            )
        ]
    return out
