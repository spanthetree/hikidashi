"""Every change to hosted AI settings, for the web API and both CLI paths (#125).

The routes in app/api/main.py and `hikidashi ai` call these and nothing else,
so validation, env precedence, encryption and the audit log are the same
whichever way a change comes in.

    owner     whose API: a user id, or None for the server's
    actor     who is acting: the user making the change, None for the system

A user changes their own API. Only an admin changes the server's. An admin may
see whether a user has their own key, their switch and their usage, and may
remove a user's key (which turns their switch off), but never set or read it.

Errors: `Invalid` (bad input: 400, exit 2) and `NotAllowed` (not an admin, a
setting locked by env, stages not all set, no budget: 403, exit 3).
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from app.accounts import User
from app.providers import Check
from app.providers import test as test_connection
from app.providers.client import Client, ProviderError
from app.providers.keys import Key
from app.providers.presets import PRESETS, STAGES

from . import config, eligible, secrets
from .config import Api


class AiError(Exception):
    status = 400
    exit = 2


class Invalid(AiError):
    pass


class NotAllowed(AiError):
    status = 403
    exit = 3


# --- storage ---------------------------------------------------------------


def _put(conn: sqlite3.Connection, owner: int | None, key: str, value: Any, by: int | None) -> None:
    conn.execute("DELETE FROM setting WHERE user_id IS ? AND key = ?", (owner, key))
    if value is not None:
        conn.execute(
            """INSERT INTO setting (user_id, key, value, updated_at, updated_by)
               VALUES (?, ?, ?, ?, ?)""",
            (owner, key, json.dumps(value), int(time.time()), by),
        )


def audit(
    conn: sqlite3.Connection, actor: int | None, owner: int | None, action: str, detail: str = ""
) -> None:
    conn.execute(
        "INSERT INTO ai_audit (at, actor_id, user_id, action, detail) VALUES (?, ?, ?, ?, ?)",
        (int(time.time()), actor, owner, action, detail[:500] or None),
    )


def history(conn: sqlite3.Connection, owner: int | None, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT a.at, a.action, a.detail, u.username AS actor FROM ai_audit a
           LEFT JOIN user u ON u.id = a.actor_id
           WHERE a.user_id IS ? ORDER BY a.id DESC LIMIT ?""",
        (owner, limit),
    )
    return [dict(r) for r in rows]


# --- who may --------------------------------------------------------------


def may(actor: User, owner: int | None) -> None:
    """Refuse an actor changing an API that is not theirs."""
    if owner is None and not actor.is_admin:
        raise NotAllowed("only an admin can change the server's API")
    if owner is not None and owner != actor.id:
        raise NotAllowed("you can change only your own API")


# --- reading -------------------------------------------------------------


def load(conn: sqlite3.Connection, owner: int | None, data_dir: Path) -> Api:
    return config.load(conn, owner, data_dir)


def show(conn: sqlite3.Connection, owner: int | None, data_dir: Path) -> dict[str, Any]:
    """An API's settings as the pages and `hikidashi ai show` give them."""
    api = load(conn, owner, data_dir)
    out = api.json()
    out["presets"] = presets()
    if owner is not None:
        out["use"] = config.use(conn, owner)
        out["server"] = server_offer(conn, data_dir)
    return out


def page(conn: sqlite3.Connection, owner: int | None, data_dir: Path) -> dict[str, Any]:
    """Everything the settings page and `hikidashi ai show` give: the
    settings, this month's spend, which way a user's reels go, and the log."""
    from . import usage

    out = show(conn, owner, data_dir)
    out["spent"] = round(usage.spent(conn, load(conn, owner, data_dir).name), 4)
    if owner is not None:
        out["applies"] = applies(conn, owner, data_dir)
    out["history"] = history(conn, owner, 20)
    out["choice"] = choice(conn, owner)
    out["choices"] = ["mine", "workers"] if owner is None else list(config.USES)
    return out


def presets() -> list[dict[str, Any]]:
    return [
        {
            "name": p.name,
            "label": p.label,
            "stages": [s for s in STAGES if p.does(s)],
            "models": {s: p.model(s) for s in STAGES if p.does(s)},
            "segments": sorted(p.segments),
            "free": p.free,
            "terms": p.terms,
            "checked": p.checked,
            "needs_base_url": p.needs_base_url,
            "key_env": p.key_env,
        }
        for p in PRESETS.values()
    ]


def server_offer(conn: sqlite3.Connection, data_dir: Path) -> dict[str, Any]:
    """What a user sees of the system default: whether it is hosted AI, and
    with what providers. Never a key, never the admin's settings beyond."""
    api = load(conn, None, data_dir)
    on_for_all = api.switch.on and api.switch.scope != "me"
    providers = sorted({s.preset.label for s in api.stages.values() if s.preset})
    return {"available": on_for_all, "providers": providers if on_for_all else []}


def applies(conn: sqlite3.Connection, user_id: int, data_dir: Path) -> str:
    """Which way a user's reels go now: mine, server or workers."""
    choice = config.use(conn, user_id)
    if choice == "mine":
        return "mine" if load(conn, user_id, data_dir).switch.on else "workers"
    if choice == "default":
        return "server" if server_offer(conn, data_dir)["available"] else "workers"
    return "workers"


def choice(conn: sqlite3.Connection, owner: int | None) -> str:
    """The one choice the page shows: a user's (default, mine, workers), or
    for the server's API, the admin's system default (mine, workers)."""
    return config.server_choice(conn) if owner is None else config.use(conn, owner)


def set_choice(
    conn: sqlite3.Connection, actor: User, owner: int | None, value: str, data_dir: Path
) -> str:
    """Set the one choice. For the server's API (an admin): mine is hosted AI
    as the system default, set up and then started; workers turns it off."""
    if owner is not None:
        return set_use(conn, actor, owner, value, data_dir)
    may(actor, None)
    if value not in ("mine", "workers"):
        raise Invalid("the system default is mine (hosted AI) or workers")
    _put(conn, None, "use", value, actor.id)
    audit(conn, actor.id, None, f"use {value}")
    if value == "workers" and load(conn, None, data_dir).switch.on:
        turn_off(conn, actor.id, None, data_dir)
    return value


def users(conn: sqlite3.Connection, data_dir: Path) -> list[dict[str, Any]]:
    """For the admin: each user's own API at a glance. Whether they have a
    key, never the key."""
    from . import usage

    out = []
    for r in conn.execute("SELECT id, username FROM user ORDER BY username"):
        api = load(conn, r["id"], data_dir)
        out.append(
            {
                "id": r["id"],
                "username": r["username"],
                "use": config.use(conn, r["id"]),
                "keys": sorted(p for p, k in api.keys.items() if k.source == "saved"),
                "on": api.switch.on,
                "reason": api.switch.reason,
                "spent": round(usage.spent(conn, api.name), 4),
            }
        )
    return out


# --- changing ------------------------------------------------------------


def _locked(api: Api, stage: str, field: str) -> None:
    v = getattr(api.stages[stage], field)
    if v.source.startswith("env:"):
        raise NotAllowed(
            f"{stage} {field.replace('_', ' ')} is set by environment ({v.source[4:]})"
        )


def set_stage(
    conn: sqlite3.Connection,
    actor: User,
    owner: int | None,
    stage: str,
    data_dir: Path,
    *,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
) -> Api:
    """Set a stage's provider, model or base URL ("all" for every stage).
    A field given as "" is cleared; None leaves it."""
    may(actor, owner)
    names = STAGES if stage == "all" else (stage,)
    if any(n not in STAGES for n in names):
        raise Invalid(f"unknown stage {stage!r}; use one of {', '.join(STAGES)} or all")
    if provider and provider not in PRESETS:
        raise Invalid(f"unknown provider {provider!r}; use one of {', '.join(PRESETS)}")
    if base_url and not base_url.startswith(("http://", "https://")):
        raise Invalid("the base URL must start with http:// or https://")
    if model is not None and len(model) > 200:
        raise Invalid("model too long")
    api = load(conn, owner, data_dir)
    for n in names:
        if provider and not PRESETS[provider].does(n):
            if stage == "all":
                continue  # all: the stages it can do
            raise Invalid(f"{PRESETS[provider].label} cannot do {n}")
        for field, v in (("provider", provider), ("model", model), ("base_url", base_url)):
            if v is not None:
                _locked(api, n, field)
        stored = config.saved(conn, owner).get(f"stage.{n}") or {}
        if provider is not None:
            if provider != stored.get("provider"):
                stored.pop("model", None)  # another provider's model means nothing here
            stored["provider"] = provider or None
        if model is not None:
            stored["model"] = model.strip() or None
        if base_url is not None:
            stored["base_url"] = base_url.strip().rstrip("/") or None
        _put(conn, owner, f"stage.{n}", {k: v for k, v in stored.items() if v} or None, actor.id)
        audit(
            conn,
            actor.id,
            owner,
            f"set {n}",
            " ".join(
                f"{k}={v}"
                for k, v in (("provider", provider), ("model", model), ("base_url", base_url))
                if v is not None
            ),
        )
    return _still_usable(conn, actor.id, owner, data_dir)


def _still_usable(
    conn: sqlite3.Connection, actor: int | None, owner: int | None, data_dir: Path
) -> Api:
    """After a change: a switch that is on, and now cannot be, goes off."""
    api = load(conn, owner, data_dir)
    if api.switch.on and (problems := api.problems()):
        return turn_off(conn, actor, owner, data_dir, reason=f"settings changed: {problems[0]}")
    return api


def set_key(
    conn: sqlite3.Connection,
    actor: User,
    owner: int | None,
    provider: str,
    key: str,
    data_dir: Path,
) -> Api:
    may(actor, owner)
    if provider not in PRESETS:
        raise Invalid(f"unknown provider {provider!r}")
    if (
        owner is None
        and (k := load(conn, None, data_dir).keys.get(provider))
        and k.source.startswith("env:")
    ):
        raise NotAllowed(
            f"the {PRESETS[provider].label} key is set by environment ({k.source[4:]})"
        )
    try:
        value = Key(key)
    except ValueError as exc:
        raise Invalid("an empty key") from exc
    if len(key) > 1000 or any(c.isspace() for c in value.reveal()):
        raise Invalid("that does not look like an API key")
    try:
        sealed = secrets.seal(value, data_dir)
    except secrets.NoCipher as exc:
        err = AiError(str(exc))
        err.status, err.exit = 503, 1
        raise err from None
    _put(conn, owner, f"key.{provider}", sealed, actor.id)
    audit(conn, actor.id, owner, f"key set {provider}", value.masked)
    return load(conn, owner, data_dir)


def remove_key(
    conn: sqlite3.Connection, actor: User, owner: int | None, provider: str, data_dir: Path
) -> Api:
    """Remove a saved key. An admin may remove a user's, which turns their
    switch off."""
    if not (actor.is_admin and owner is not None and owner != actor.id):
        may(actor, owner)
    if provider not in PRESETS:
        raise Invalid(f"unknown provider {provider!r}")
    _put(conn, owner, f"key.{provider}", None, actor.id)
    audit(conn, actor.id, owner, f"key remove {provider}")
    api = load(conn, owner, data_dir)
    if api.switch.on and owner is not None and owner != actor.id:
        return turn_off(conn, actor.id, owner, data_dir, reason="an admin removed a key")
    return _still_usable(conn, actor.id, owner, data_dir)


def set_budget(
    conn: sqlite3.Connection, actor: User, owner: int | None, usd: float | None, data_dir: Path
) -> Api:
    may(actor, owner)
    api = load(conn, owner, data_dir)
    if owner is None and api.budget.source.startswith("env:"):
        raise NotAllowed(f"the budget is set by environment ({api.budget.source[4:]})")
    if usd is not None and not 0 < usd <= 100_000:
        raise Invalid("the budget is a number of US dollars a month, over 0")
    _put(conn, owner, "budget", usd, actor.id)
    audit(conn, actor.id, owner, "budget", "" if usd is None else f"{usd:g} USD")
    return _still_usable(conn, actor.id, owner, data_dir)


def set_language(
    conn: sqlite3.Connection, actor: User, owner: int | None, code: str | None, data_dir: Path
) -> Api:
    may(actor, owner)
    if code and not (2 <= len(code) <= 3 and code.isalpha()):
        raise Invalid("a language is an ISO 639-1 code, as en or ja; empty to detect it")
    if owner is None and load(conn, None, data_dir).language.source.startswith("env:"):
        raise NotAllowed("the language is set by environment (HIKIDASHI_ASR_LANGUAGE)")
    _put(conn, owner, "language", code.lower() if code else None, actor.id)
    audit(conn, actor.id, owner, "language", code or "detect")
    return load(conn, owner, data_dir)


def set_use(
    conn: sqlite3.Connection, actor: User, user_id: int, choice: str, data_dir: Path
) -> str:
    """How a user's reels are processed: mine, server, workers or default."""
    may(actor, user_id)
    if choice == "server":
        choice = "default"  # the system default, by its earlier name
    if choice not in config.USES:
        raise Invalid(f"use one of {', '.join(config.USES)}")
    _put(conn, user_id, "use", None if choice == "default" else choice, actor.id)
    audit(conn, actor.id, user_id, f"use {choice}")
    api = load(conn, user_id, data_dir)
    if choice != "mine" and api.switch.on:
        turn_off(conn, actor.id, user_id, data_dir)
    return choice


# --- the switch -------------------------------------------------------------


def _as_on(
    conn: sqlite3.Connection,
    owner: int | None,
    data_dir: Path,
    actor: int | None,
    scope: str | None,
) -> Api:
    """The API as it would be, switched on (for the server's, with `scope`)."""
    api = load(conn, owner, data_dir)
    if owner is None:
        if scope is not None or api.switch.scope is None:
            api.switch.by = actor
        api.switch.scope = scope or api.switch.scope or "all"
    api.switch.on = True
    return api


def _waiting_as(conn: sqlite3.Connection, api: Api) -> list[str]:
    """The reels `api` would take now, switched on as it is in `api`."""
    if api.owner is None:
        return eligible.waiting(conn, api)
    # A user's: as if their choice were already "mine", as turning it on makes it.
    ready = eligible.ready(conn)
    by = eligible.savers(conn, ready)
    mine = [c for c in ready if api.owner in by.get(c, [])]
    skip = eligible.given_up(conn, api.name) | eligible.leased(conn, mine)
    return [c for c in mine if c not in skip]


def backlog(
    conn: sqlite3.Connection,
    owner: int | None,
    data_dir: Path,
    *,
    actor: int | None = None,
    scope: str | None = None,
) -> dict[str, Any]:
    """The reels waiting that this API would take on being switched on (the
    server's, for `scope`), and an estimate of what they would cost. For a
    scope change, only the reels it would newly take."""
    from . import usage

    target = _as_on(conn, owner, data_dir, actor, scope)
    codes = _waiting_as(conn, target)
    now = load(conn, owner, data_dir)
    if now.switch.on:
        already = set(_waiting_as(conn, now))
        codes = [c for c in codes if c not in already]
    return {"reels": len(codes), **usage.estimate(conn, target, codes)}


def turn_on(
    conn: sqlite3.Connection,
    actor: User,
    owner: int | None,
    data_dir: Path,
    *,
    scope: str | None = None,
    backlog: bool = True,
) -> Api:
    """Switch an API on (or, for the server's, change its scope). With
    `backlog` False, the reels waiting now are left to workers."""
    may(actor, owner)
    api = load(conn, owner, data_dir)
    if owner is None:
        # The system default: for everyone who keeps it, unless asked otherwise.
        scope = scope or "all"
        if scope not in config.SCOPES:
            raise Invalid("say who the server's API is for: me or all")
    elif scope is not None:
        raise Invalid("only the server's API has a scope")
    if problems := api.problems():
        raise NotAllowed("cannot turn on: " + "; ".join(problems))
    before = api.switch
    if owner is not None and config.use(conn, owner) != "mine":
        _put(conn, owner, "use", "mine", actor.id)
        audit(conn, actor.id, owner, "use mine")
    if owner is None and config.server_choice(conn) != "mine":
        _put(conn, None, "use", "mine", actor.id)
    if not backlog:
        target = _as_on(conn, owner, data_dir, actor.id, scope)
        codes = _waiting_as(conn, target)
        if before.on:
            already = set(_waiting_as(conn, api))
            codes = [c for c in codes if c not in already]
        eligible.leave(conn, api.name, codes)
    now = int(time.time())
    _put(
        conn,
        owner,
        "switch",
        {"on": True, "scope": scope, "at": now, "by": actor.id},
        actor.id,
    )
    audit(
        conn, actor.id, owner, "on" if not before.on else "scope", f"for {scope}" if scope else ""
    )
    return load(conn, owner, data_dir)


def turn_off(
    conn: sqlite3.Connection,
    actor: int | None,
    owner: int | None,
    data_dir: Path,
    *,
    reason: str | None = None,
) -> Api:
    """Switch an API off. `reason` when it goes off by itself: a rejected
    key, used-up quota, the budget reached. The claimer sees it, lets what is
    in flight finish, and gives the rest back to workers."""
    api = load(conn, owner, data_dir)
    sw = api.switch.json()
    sw.update(on=False, reason=reason, at=int(time.time()))
    _put(conn, owner, "switch", sw, actor)
    audit(conn, actor, owner, "off", reason or "")
    return load(conn, owner, data_dir)


def off_by(conn: sqlite3.Connection, actor: User, owner: int | None, data_dir: Path) -> Api:
    may(actor, owner)
    return turn_off(conn, actor.id, owner, data_dir)


# --- asking the provider -----------------------------------------------------


def _client_for(api: Api, provider: str, base_url: str | None) -> Client:
    p = PRESETS.get(provider)
    if p is None:
        raise Invalid(f"unknown provider {provider!r}")
    if p.needs_base_url and not base_url:
        raise Invalid(f"{p.label} needs a base URL")
    try:
        url = base_url if p.needs_base_url else None
        return Client(p, api.key(provider), base_url=url or None, timeout=30)
    except ValueError as exc:
        raise Invalid(str(exc)) from exc


def models(
    conn: sqlite3.Connection,
    owner: int | None,
    provider: str,
    data_dir: Path,
    *,
    base_url: str | None = None,
) -> list[str]:
    api = load(conn, owner, data_dir)
    if base_url is None:
        base_url = next(
            (
                s.base_url.value
                for s in api.stages.values()
                if s.provider.value == provider and s.base_url.value
            ),
            None,
        )
    try:
        return _client_for(api, provider, base_url).models()
    except ProviderError as exc:
        raise AiError(str(exc)) from exc


def test(
    conn: sqlite3.Connection, owner: int | None, data_dir: Path, stage: str | None = None
) -> dict[str, dict[str, Any]]:
    """Test connection: each stage set (or `stage`), with one tiny request."""
    api = load(conn, owner, data_dir)
    out: dict[str, dict[str, Any]] = {}
    for name in [stage] if stage else list(STAGES):
        if name not in STAGES:
            raise Invalid(f"unknown stage {name!r}")
        st = api.stages[name]
        p = st.preset
        if p is None:
            out[name] = {"ok": False, "said": "no provider set", "detail": ""}
            continue
        try:
            client = _client_for(api, p.name, st.base_url.value)
        except Invalid as exc:
            out[name] = {"ok": False, "said": str(exc), "detail": ""}
            continue
        got: Check = test_connection(client, name, api.model(name))
        out[name] = {"ok": got.ok, "said": got.said, "detail": got.detail, "model": api.model(name)}
    return out
