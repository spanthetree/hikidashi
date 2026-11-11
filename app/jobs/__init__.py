"""Pipeline jobs: the stages as work that always-on workers claim (#72).

Each stage already knows what it has left to do: its `pending` query, behind
its version stamp. This package adds what running them unattended needs:

    leases      a worker claims a few reels for one stage, for a while, and
                renews the claim as it works. Two workers never hold the same
                reel, and a claim its worker stops renewing expires, so a
                worker killed mid-batch loses nothing.
    readiness   a reel is offered to a stage only once every stage before it
                is done with it, or has given up on it.
    attempts    a stage that tries a reel and does not finish it, by failing
                or by dying, counts it. At the cap the reel is failed for that
                stage until the stage's version changes or it is retried.

`queue` holds the model and the local job source; `worker` the loop that runs
on it; `python -m app.jobs` is `hikidashi worker`.

This module is stdlib only and imports nothing, so config and the importer
can use it without pulling in the stages.
"""

from __future__ import annotations

from pathlib import Path

# In pipeline order. The names the worker's --stages and
# HIKIDASHI_LOCAL_STAGES take.
STAGES = ("enrich", "asr", "ocr", "tag", "recipe", "embed")

# Other names for a stage: the command that runs it by hand.
ALIASES = {"transcribe": "asr", "recipes": "recipe"}

WAKE = "worker.wake"


def parse_stages(value: str) -> tuple[str, ...]:
    """`all`, `none`, or a list such as `asr,ocr`, as stages in pipeline order.
    Raises ValueError naming what it does not know."""
    words = [w for w in value.replace(",", " ").lower().split() if w]
    if not words or words == ["all"]:
        return STAGES
    if words == ["none"]:
        return ()
    wanted = {ALIASES.get(w, w) for w in words}
    unknown = sorted(wanted - set(STAGES))
    if unknown:
        raise ValueError(
            f"unknown stage {', '.join(unknown)}; use all, none, or some of {', '.join(STAGES)}"
        )
    return tuple(s for s in STAGES if s in wanted)


def wake(run_dir: Path) -> None:
    """Tell the workers on this host there is new work, rather than leave it
    to their next poll. A file, so it works across processes and containers
    that share the data folder."""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / WAKE).touch()


def woken_at(run_dir: Path) -> float:
    """When `wake` was last called, or 0."""
    try:
        return (run_dir / WAKE).stat().st_mtime
    except OSError:
        return 0.0
