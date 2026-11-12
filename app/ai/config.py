"""One API's configuration, as it stands: saved settings, env, presets.

An API is a user's own (`owner` their id) or the server's (`owner` None).
Each of the four stages has a provider, a model and, for an
OpenAI-compatible URL, a base URL; keys are per provider, so stages can share
one. For the server's API the environment wins over what the admin saved, and
says so (`source` "env:NAME"); env never turns the switch on.

    HIKIDASHI_ASR_PROVIDER / _MODEL     asr
    HIKIDASHI_OCR_PROVIDER / _MODEL     ocr
    HIKIDASHI_LLM_PROVIDER / _MODEL     tag (with the game pass) and recipe
    HIKIDASHI_OPENAI_BASE_URL           an OpenAI-compatible URL's address
    <PRESET>_API_KEY (or _FILE)         each provider's key: OPENAI_API_KEY, …
    HIKIDASHI_HOSTED_BUDGET             USD a month
    HIKIDASHI_ASR_LANGUAGE              force a language; else detected

What is saved is in the `setting` table (schema.sql), as JSON:

    stage.<stage>   {"provider", "model", "base_url"}
    key.<provider>  the key, encrypted (secrets.py)
    budget          USD a month
    language        an ISO 639-1 code
    switch          {"on", "scope", "since", "reason", "at", "by"}
    use             mine | server | workers; absent is the server default

Stdlib only, but for `cryptography` when a saved key is read.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.providers.client import Client
from app.providers.keys import Key, from_env
from app.providers.presets import COMPATIBLE, PRESETS, STAGES, Preset

from . import secrets

STAGE_ENV = {
    "asr": ("HIKIDASHI_ASR_PROVIDER", "HIKIDASHI_ASR_MODEL"),
    "ocr": ("HIKIDASHI_OCR_PROVIDER", "HIKIDASHI_OCR_MODEL"),
    "tag": ("HIKIDASHI_LLM_PROVIDER", "HIKIDASHI_LLM_MODEL"),
    "recipe": ("HIKIDASHI_LLM_PROVIDER", "HIKIDASHI_LLM_MODEL"),
}
BASE_URL_ENV = "HIKIDASHI_OPENAI_BASE_URL"
BUDGET_ENV = "HIKIDASHI_HOSTED_BUDGET"
LANGUAGE_ENV = "HIKIDASHI_ASR_LANGUAGE"

# A user's one choice: the system default (whatever the admin set), their own
# hosted AI, or workers only. "server" is what 0.23.0 before release called
# the default, and reads as it.
USES = ("default", "mine", "workers")
SCOPES = ("me", "all")


@dataclass(frozen=True)
class Value:
    value: Any
    source: str  # env:NAME | saved | default | unset

    def json(self) -> dict[str, Any]:
        return {"value": self.value, "source": self.source}


UNSET = Value(None, "unset")


@dataclass
class Stage:
    name: str
    provider: Value = UNSET
    model: Value = UNSET
    base_url: Value = UNSET

    @property
    def preset(self) -> Preset | None:
        return PRESETS.get(self.provider.value) if self.provider.value else None


@dataclass
class KeyState:
    key: Key | None
    source: str  # env:NAME | saved | unset
    problem: str | None = None  # a saved key that no longer decrypts

    def json(self) -> dict[str, Any]:
        return {
            "set": self.key is not None,
            "masked": self.key.masked if self.key else None,
            "source": self.source,
            "problem": self.problem,
        }


@dataclass
class Switch:
    on: bool = False
    scope: str | None = None  # the server's: me | all
    since: int | None = None  # only reels ready after this, when the backlog was left
    reason: str | None = None  # why it went off by itself
    at: int | None = None
    by: int | None = None

    def json(self) -> dict[str, Any]:
        return {
            "on": self.on,
            "scope": self.scope,
            "since": self.since,
            "reason": self.reason,
            "at": self.at,
            "by": self.by,
        }


@dataclass
class Api:
    owner: int | None  # None: the server's
    stages: dict[str, Stage]
    keys: dict[str, KeyState]
    budget: Value
    language: Value
    switch: Switch
    base_dir: Path = field(repr=False, default=Path("."))

    @property
    def name(self) -> str:
        """Its name as a claimer in the job queue."""
        return "api:server" if self.owner is None else f"api:user:{self.owner}"

    def key(self, provider: str) -> Key | None:
        state = self.keys.get(provider)
        return state.key if state else None

    def problems(self) -> list[str]:
        """Why the switch cannot be turned on; empty when it can."""
        out = []
        for name in STAGES:
            st = self.stages[name]
            p = st.preset
            if p is None:
                out.append(f"{name}: no provider set")
                continue
            if not p.does(name):
                out.append(f"{name}: {p.label} cannot do {name}")
            if not (st.model.value or p.model(name)):
                out.append(f"{name}: no model set")
            if p.needs_base_url and not st.base_url.value:
                out.append(f"{name}: no base URL set")
            if p.name != COMPATIBLE and self.key(p.name) is None:
                problem = self.keys.get(p.name)
                why = problem.problem if problem and problem.problem else "no key"
                out.append(f"{name}: {why} for {p.label}")
        if not self.budget.value:
            out.append("no monthly budget set")
        return out

    def model(self, stage: str) -> str:
        st = self.stages[stage]
        p = st.preset
        assert p is not None
        return str(st.model.value or p.model(stage) or "")

    def client(self, stage: str) -> Client:
        st = self.stages[stage]
        p = st.preset
        if p is None:
            raise ValueError(f"{stage}: no provider set")
        # A base URL is the setting's only where the preset has none of its
        # own: HIKIDASHI_OPENAI_BASE_URL never sends Groq's requests elsewhere.
        url = st.base_url.value if p.needs_base_url else None
        return Client(p, self.key(p.name), base_url=url or None)

    def json(self) -> dict[str, Any]:
        """Everything the settings pages show. Keys only masked."""
        return {
            "owner": self.owner,
            "stages": {
                n: {
                    "provider": s.provider.json(),
                    "model": s.model.json(),
                    "base_url": s.base_url.json(),
                    "default_model": s.preset.model(n) if s.preset else None,
                }
                for n, s in self.stages.items()
            },
            "keys": {p: k.json() for p, k in self.keys.items()},
            "budget": self.budget.json(),
            "language": self.language.json(),
            "switch": self.switch.json(),
            "problems": self.problems(),
        }


def saved(conn: sqlite3.Connection, owner: int | None) -> dict[str, Any]:
    """An API's saved settings, by key, as JSON values."""
    rows = conn.execute("SELECT key, value FROM setting WHERE user_id IS ?", (owner,)).fetchall()
    out = {}
    for r in rows:
        try:
            out[r["key"]] = json.loads(r["value"])
        except ValueError:
            continue
    return out


def load(
    conn: sqlite3.Connection,
    owner: int | None,
    data_dir: Path,
    env: Mapping[str, str] | None = None,
) -> Api:
    """The API of `owner` (None: the server's) as it stands now."""
    env = os.environ if env is None else env
    use_env = owner is None
    got = saved(conn, owner)

    def from_(name: str | None, stored: Any) -> Value:
        if use_env and name and (v := env.get(name, "").strip()):
            return Value(v, f"env:{name}")
        if stored not in (None, ""):
            return Value(stored, "saved")
        return UNSET

    stages = {}
    for s in STAGES:
        stored = got.get(f"stage.{s}") or {}
        pe, me = STAGE_ENV[s]
        stages[s] = Stage(
            s,
            provider=from_(pe, stored.get("provider")),
            model=from_(me, stored.get("model")),
            base_url=from_(BASE_URL_ENV, stored.get("base_url")),
        )

    keys: dict[str, KeyState] = {}
    for p in PRESETS.values():
        if use_env:
            try:
                k = from_env(p.key_env, dict(env))
            except ValueError as exc:
                keys[p.name] = KeyState(None, f"env:{p.key_env}", str(exc))
                continue
            if k is not None:
                keys[p.name] = KeyState(k, f"env:{p.key_env}")
                continue
        token = got.get(f"key.{p.name}")
        if isinstance(token, str) and token:
            try:
                keys[p.name] = KeyState(secrets.unseal(token, data_dir), "saved")
            except secrets.NoCipher as exc:
                keys[p.name] = KeyState(None, "saved", str(exc))
            except secrets.Unreadable:
                keys[p.name] = KeyState(
                    None,
                    "saved",
                    "the saved key cannot be read (the secret key changed); enter it again",
                )
        else:
            keys[p.name] = KeyState(None, "unset")

    budget = from_(BUDGET_ENV, got.get("budget"))
    if budget.value is not None:
        try:
            budget = Value(float(budget.value), budget.source)
        except (TypeError, ValueError):
            budget = UNSET
    sw = got.get("switch") or {}
    return Api(
        owner=owner,
        stages=stages,
        keys=keys,
        budget=budget,
        language=from_(LANGUAGE_ENV, got.get("language")),
        switch=Switch(
            on=bool(sw.get("on")),
            scope=sw.get("scope") if owner is None else None,
            since=sw.get("since"),
            reason=sw.get("reason"),
            at=sw.get("at"),
            by=sw.get("by"),
        ),
        base_dir=data_dir,
    )


def use(conn: sqlite3.Connection, user_id: int) -> str:
    """How a user's reels are processed: mine, server, workers or default."""
    row = conn.execute(
        "SELECT value FROM setting WHERE user_id = ? AND key = 'use'", (user_id,)
    ).fetchone()
    try:
        v = json.loads(row["value"]) if row else "default"
    except ValueError:
        v = "default"
    return v if v in USES else "default"


def server_choice(conn: sqlite3.Connection) -> str:
    """The system default, as the admin chose it: mine (hosted AI, the server's
    API) or workers."""
    row = conn.execute("SELECT value FROM setting WHERE user_id IS NULL AND key = 'use'").fetchone()
    try:
        v = json.loads(row["value"]) if row else None
    except ValueError:
        v = None
    if v in ("mine", "workers"):
        return str(v)
    return "mine" if any(o is None for o in switched_on(conn)) else "workers"


def switched_on(conn: sqlite3.Connection) -> list[int | None]:
    """The owners whose switch is on: user ids, and None for the server's."""
    rows = conn.execute("SELECT user_id, value FROM setting WHERE key = 'switch'").fetchall()
    out: list[int | None] = []
    for r in rows:
        try:
            if json.loads(r["value"]).get("on"):
                out.append(r["user_id"])
        except (ValueError, AttributeError):
            continue
    return out
