"""Logging and monitoring settings, saved from the Account page.

The settings are the environment variables of app/logship.py (syslog, Loki,
the log's level and format) and app/metrics.py (Prometheus). An admin can set
each on the Account page instead, saved in the `setting` table (the server's
row, key `monitoring`, as JSON). A variable that is set wins, and shows there
as set by the environment, which the page cannot change. A secret (the
metrics token, Loki's token and password) is sealed with the server's secret
key (app/ai/secrets.py) and never shown back, only whether one is saved.

`environ` gives what the shippers and /metrics read: the saved settings with
the environment over them. The server applies a change as it is saved; a
worker beside it reads the same database and applies one within `EVERY`
seconds (`watch`). A worker on another machine has no database, and keeps to
its own environment.

Stdlib only, but for the secrets, which need `cryptography`.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.metrics import ALLOW

log = logging.getLogger(__name__)

KEY = "monitoring"
EVERY = 30.0  # seconds between a worker's looks for a change


@dataclass(frozen=True)
class Field:
    env: str
    kind: str = "text"  # text | bool | choice | secret
    choices: tuple[str, ...] = ()
    default: str = ""


FIELDS: tuple[Field, ...] = (
    Field("HIKIDASHI_METRICS", "bool"),
    Field("HIKIDASHI_METRICS_TOKEN", "secret"),
    Field("HIKIDASHI_METRICS_ALLOW", default=ALLOW),
    Field("HIKIDASHI_METRICS_PORT"),  # unset: on the web port
    Field("HIKIDASHI_METRICS_REFRESH", default="30"),
    Field("HIKIDASHI_METRICS_PER_USER", "bool"),
    Field("HIKIDASHI_LOG_LEVEL", "choice", ("debug", "info", "warning", "error"), "info"),
    Field("HIKIDASHI_LOG_FORMAT", "choice", ("text", "json"), "text"),
    Field("HIKIDASHI_SYSLOG"),
    Field("HIKIDASHI_SYSLOG_FORMAT", "choice", ("rfc5424", "rfc3164"), "rfc5424"),
    Field("HIKIDASHI_SYSLOG_FACILITY", default="local0"),
    Field("HIKIDASHI_LOKI_URL"),
    Field("HIKIDASHI_LOKI_TOKEN", "secret"),
    Field("HIKIDASHI_LOKI_USER"),
    Field("HIKIDASHI_LOKI_PASSWORD", "secret"),
    Field("HIKIDASHI_LOKI_TENANT"),
    Field("HIKIDASHI_LOKI_LABELS"),
)
BY_ENV = {f.env: f for f in FIELDS}


class Invalid(ValueError):
    """A change that cannot be saved: says which setting and why."""


def _from_env(env: Mapping[str, str], name: str) -> bool:
    return bool(env.get(name, "").strip() or env.get(f"{name}_FILE", "").strip())


def saved(conn: sqlite3.Connection) -> dict[str, str]:
    """What is saved, by variable name; secrets still sealed. {} before the
    table exists (an old database, before /readyz migrated it)."""
    try:
        row = conn.execute(
            "SELECT value FROM setting WHERE user_id IS NULL AND key = ?", (KEY,)
        ).fetchone()
    except sqlite3.OperationalError:
        return {}
    if row is None:
        return {}
    try:
        got = json.loads(row[0])
    except ValueError:
        return {}
    return {k: str(v) for k, v in got.items() if k in BY_ENV and isinstance(v, str | int | float)}


def changed_at(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute(
            "SELECT updated_at FROM setting WHERE user_id IS NULL AND key = ?", (KEY,)
        ).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0]) if row else 0


def environ(
    conn: sqlite3.Connection, data_dir: Path, env: Mapping[str, str] | None = None
) -> dict[str, str]:
    """The environment the shippers and /metrics read: what is saved, with
    the real environment over it."""
    return environ_of(saved(conn), data_dir, os.environ if env is None else env)


def show(conn: sqlite3.Connection, env: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Every setting for the Account page: its value (a secret: only whether
    there is one), where it comes from, and its choices and default."""
    env = os.environ if env is None else env
    got = saved(conn)
    out = []
    for f in FIELDS:
        locked = _from_env(env, f.env)
        raw = env.get(f.env, "") if locked else got.get(f.env, "")
        out.append(
            {
                "name": f.env,
                "kind": f.kind,
                "choices": list(f.choices),
                "default": f.default,
                "source": "env" if locked else "saved" if f.env in got else "default",
                "value": bool(locked or raw) if f.kind == "secret" else raw,
            }
        )
    return out


def save(
    conn: sqlite3.Connection,
    data_dir: Path,
    changes: Mapping[str, str | bool | None],
    user_id: int,
    env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Save `changes` (None or "" clears one) after checking the whole
    result would install. Returns the environment to apply. Raises Invalid."""
    from app import logship, metrics
    from app.ai import secrets
    from app.providers.keys import Key

    env = os.environ if env is None else env
    got = saved(conn)
    for name, value in changes.items():
        f = BY_ENV.get(name)
        if f is None:
            raise Invalid(f"{name}: not a logging or monitoring setting")
        if _from_env(env, name):
            raise Invalid(f"{name}: set in the environment, which wins; change it there")
        if value is None or value == "" or value is False:
            got.pop(name, None)
            continue
        text = ("on" if value else "") if isinstance(value, bool) else str(value).strip()
        if len(text) > 2000 or "\n" in text:
            raise Invalid(f"{name}: too long, or more than one line")
        if f.kind == "choice" and text not in f.choices:
            raise Invalid(f"{name}: one of {', '.join(f.choices)}")
        if f.kind == "bool":
            text = "on"
        if f.kind == "secret":
            text = secrets.seal(Key(text), data_dir)
        got[name] = text
    applied = environ_of(got, data_dir, env)
    try:
        logship.check(applied)
        metrics.Options.from_env(applied)
    except ValueError as exc:
        raise Invalid(str(exc)) from None
    conn.execute(
        """INSERT INTO setting (user_id, key, value, updated_at, updated_by)
           VALUES (NULL, ?, ?, ?, ?)
           ON CONFLICT (ifnull(user_id, 0), key) DO UPDATE
           SET value = excluded.value, updated_at = excluded.updated_at,
               updated_by = excluded.updated_by""",
        (KEY, json.dumps(got, sort_keys=True), int(time.time()), user_id),
    )
    conn.commit()
    # Which settings, never their values: some are secrets, some addresses.
    log.info("monitoring settings changed: %s", ", ".join(sorted(changes)) or "none")
    return applied


def environ_of(got: Mapping[str, str], data_dir: Path, env: Mapping[str, str]) -> dict[str, str]:
    """`environ` of settings as saved, or about to be. A secret saved under
    another secret key reads as unset, and the log says so."""
    from app.ai import secrets

    out = {}
    for name, value in got.items():
        if _from_env(env, name):
            continue
        if BY_ENV[name].kind == "secret":
            try:
                value = secrets.unseal(value, data_dir).reveal()
            except (secrets.Unreadable, secrets.NoCipher) as exc:
                log.warning("monitoring: the saved %s cannot be read: %s", name, exc)
                continue
        out[name] = value
    out.update(env)
    return out


def watch(
    db_path: Path,
    data_dir: Path,
    apply: Callable[[Mapping[str, str]], object],
    *,
    every: float = EVERY,
    stop: threading.Event | None = None,
) -> threading.Thread:
    """Apply the settings now, and again whenever they are saved, from a
    thread with its own connection: for a worker beside the server."""
    from app.db import connect

    def look(last: int) -> int:
        conn = connect(db_path)
        try:
            at = changed_at(conn)
            if at != last:
                apply(environ(conn, data_dir))
            return at
        finally:
            conn.close()

    done = stop or threading.Event()

    def run() -> None:
        last = -1
        while not done.is_set():
            try:
                last = look(last)
            except Exception as exc:  # a bad saved setting, a locked database
                log.warning("monitoring settings not applied: %s", exc)
            done.wait(every)

    thread = threading.Thread(target=run, name="monitoring", daemon=True)
    thread.start()
    return thread
