"""FastAPI app. A thin layer over app/db.py and app/importer — all the logic
that matters lives in stdlib modules so it can be tested without installing it.

Every route reads one person's library: the user `current_user` names. A reel
outside it is a 404 everywhere, media included, exactly as if it did not exist.
With HIKIDASHI_AUTH=none that is always the library's owner; with local, it is
whoever the session cookie signs in.
"""

from __future__ import annotations

import collections
import dataclasses
import functools
import ipaddress
import json
import logging
import secrets
import shutil
import sqlite3
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from app import accounts, jobs, library, logs, search, spa
from app.accounts import AccountError, User
from app.api import ai_routes
from app.config import settings
from app.db import open_db
from app.enrich import worker as fetches
from app.importer.service import ImportError_, import_export
from app.jobs import STAGES, protocol
from app.jobs import queue as job_queue
from app.library import SHOWN_GAME, SHOWN_TAG, mine
from app.places import Place
from app.places import as_dicts as places_in
from app.places.geocoder import CONFIDENT, Nominatim
from app.places.service import Locator
from app.recipes import service as recipes
from app.recipes import shopping
from app.tagging.taxonomy import normalise

if TYPE_CHECKING:
    from app.embed.embedder import Embedder


# Built from frontend/ by `npm run build`; not checked in.
WEB = Path(__file__).resolve().parent.parent / "web"

log = logging.getLogger("uvicorn.error")
settings.ensure_dirs()
LOG_FILE = logs.install(settings.logs_dir)

app = FastAPI(title="Hikidashi", docs_url="/api/docs", openapi_url="/api/openapi.json")


_embedder: Embedder | None = None
_embedder_failed_at = 0.0
_embedder_lock = threading.Lock()
RETRY_EMBEDDER = 60.0  # seconds before trying a model that failed to load again


def embedder() -> Embedder | None:
    """The query embedder, loaded on first use. None while it cannot load.

    Never downloads: weights arrive with `python -m app.embed`, and until that
    has run there are no vectors to compare against anyway.
    """
    global _embedder, _embedder_failed_at
    with _embedder_lock:
        if _embedder is None and time.monotonic() - _embedder_failed_at > RETRY_EMBEDDER:
            try:
                from app.embed.embedder import Embedder

                _embedder = Embedder(settings.models_dir, local_only=True)
            except Exception as exc:
                _embedder_failed_at = time.monotonic()
                log.warning("semantic search off, embedder did not load: %s", exc)
        return _embedder


def query_vector(q: str) -> bytes | None:
    model = embedder()
    return _embed_query(model, q) if model else None


@functools.lru_cache(maxsize=256)
def _embed_query(model: Embedder, q: str) -> bytes:
    # Typing re-sends the same query for every page of results.
    return model.query(q)


@app.on_event("startup")
def warm_embedder() -> None:
    """Load the query model in the background, so the first search is not slow."""
    threading.Thread(target=embedder, daemon=True).start()


def db() -> sqlite3.Connection:
    settings.ensure_dirs()
    return open_db(settings.db_path)


_geocoders: dict[tuple[str, float], Nominatim] = {}
_geocoders_lock = threading.Lock()


def geocoder() -> Nominatim | None:
    """The one geocoder client, so the locator and a typed city share its
    rate limit. None when no geocoder is set."""
    if not settings.geocoder:
        return None
    key = (settings.geocoder, settings.geocoder_delay)
    with _geocoders_lock:
        if key not in _geocoders:
            _geocoders[key] = Nominatim(settings.geocoder, delay=settings.geocoder_delay)
        return _geocoders[key]


_locator: Locator | None = None


@app.on_event("startup")
def start_locator() -> None:
    """Put new reels' places on the map as they arrive. See app/places/service.py."""
    global _locator
    found = geocoder()
    if found and settings.locate_every > 0:
        _locator = Locator(db, found.search, settings.run_dir, every=settings.locate_every)
        _locator.start()


@app.on_event("shutdown")
def stop_locator() -> None:
    global _locator
    if _locator:
        _locator.stop()
        _locator = None


COOKIE = "hk_session"


def from_proxy(conn: sqlite3.Connection, request: Request) -> User:
    """The user a trusted proxy names. A header from anywhere else is ignored:
    anyone can send one, so only the proxy's own address is believed."""
    try:
        peer = ipaddress.ip_address(request.client.host if request.client else "")
        trusted = any(
            peer in ipaddress.ip_network(n, strict=False) for n in settings.trusted_proxies
        )
    except ValueError:
        trusted = False
    name = request.headers.get(settings.proxy_header, "").strip() if trusted else ""
    if not name:
        raise HTTPException(
            401,
            f"The proxy in front of Hikidashi did not say who you "
            f"are: no {settings.proxy_header} header from a trusted "
            f"address.",
        )
    user = accounts.by_name(conn, name)
    if user is None and settings.proxy_creates:
        try:
            user = accounts.create(conn, name, None)
        except AccountError:
            user = accounts.by_name(conn, name)  # made by a request alongside
    if user is None:
        raise HTTPException(
            403, f"There is no Hikidashi account called {name}. Ask an admin to add one."
        )
    return user


def bearer(request: Request) -> str | None:
    """The token in `Authorization: Bearer <token>`, if one was sent. Never
    from the URL, where logs and browser history would keep it."""
    scheme, _, given = request.headers.get("authorization", "").partition(" ")
    return given.strip() if scheme.lower() == "bearer" else None


def _unauthorized(detail: str, error: str | None = None) -> HTTPException:
    """A 401 as RFC 6750 has it: saying a bearer token is what is wanted, and
    with `error`, what was wrong with the one sent."""
    challenge = 'Bearer realm="Hikidashi"' + (f', error="{error}"' if error else "")
    return HTTPException(401, detail, headers={"WWW-Authenticate": challenge})


def from_token(conn: sqlite3.Connection, request: Request, given: str) -> User:
    """Whoever an API token is. A worker token is not one: it opens
    /api/worker/* and nothing else."""
    token = accounts.find_token(conn, given)
    if token is None:
        raise _unauthorized("That token is wrong, expired or revoked.", "invalid_token")
    if token.kind != accounts.API:
        raise _unauthorized("A worker token only opens /api/worker/*.", "invalid_token")
    request.state.token = token
    return token.user


def current_user(request: Request) -> User:
    """Who is asking: their session, the proxy's word for them, or, from a
    script, their API token (#103). The cookie and the proxy come first."""
    conn = db()
    if settings.auth == "none":
        return accounts.local(conn)
    given = bearer(request)
    if settings.auth == "proxy" and (request.headers.get(settings.proxy_header) or not given):
        return from_proxy(conn, request)
    if settings.auth == "local":
        user = accounts.session_user(conn, request.cookies.get(COOKIE))
        if user is not None:
            return user
    if given:
        return from_token(conn, request, given)
    raise _unauthorized("Sign in to see your library.")


def _scoped(request: Request, area: str) -> None:
    """Refuse a token that was not given `area`: to read it, or with any other
    method than GET or HEAD, to change it. A session has every scope."""
    token: accounts.Token | None = getattr(request.state, "token", None)
    write = request.method not in ("GET", "HEAD")
    if token is not None and not token.grants(area, write=write):
        scope = f"{'write' if write else 'read'}:{area}"
        raise HTTPException(
            403,
            f"This token does not have the {scope} scope.",
            headers={
                "WWW-Authenticate": f'Bearer realm="Hikidashi", error="insufficient_scope", '
                f'scope="{scope}"'
            },
        )


Who = Annotated[User, Depends(current_user)]


def library_user(request: Request, user: Who) -> User:
    """Whose library this request reads."""
    _scoped(request, "library")
    return user


Me = Annotated[User, Depends(library_user)]


def admin(request: Request, user: Who) -> User:
    """The same, for what only an admin may see: the server, not a library."""
    if not user.is_admin:
        raise HTTPException(403, "Only an admin can see this.")
    _scoped(request, "admin")
    return user


Admin = Annotated[User, Depends(admin)]


def sees_admin(request: Request, user: User) -> bool:
    """Whether a library route may show this person what is for admins only:
    always for an admin's session, and for a token with read:admin."""
    token: accounts.Token | None = getattr(request.state, "token", None)
    return user.is_admin and (token is None or token.grants("admin", write=False))


def by_session(request: Request, user: Who) -> User:
    """For what a token must never do, whatever its scopes: a password, other
    sessions, and making or revoking tokens. Only a person signed in can."""
    if getattr(request.state, "token", None) is not None:
        raise HTTPException(403, "Sign in to do this; a token cannot.")
    return user


Signed = Annotated[User, Depends(by_session)]


# Hosted AI settings (#125): /api/me/providers and /api/admin/settings/providers.
ai_routes.install(app, db, current_user, _scoped, lambda: settings.db_path.parent)


@app.exception_handler(AccountError)
def account_error(request: Request, exc: AccountError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=400)


UNSAFE = ("POST", "PUT", "PATCH", "DELETE")


def same_origin(request: Request) -> bool:
    """Whether a request comes from this app's own pages, or from no browser.

    A browser says where a request comes from: Sec-Fetch-Site, or failing that
    Origin. A request with neither is curl or a script, which a forged page on
    another site cannot make on your behalf.
    """
    site = request.headers.get("sec-fetch-site")
    if site is not None:
        return site in ("same-origin", "none")
    origin = request.headers.get("origin")
    if origin is None:
        return True
    ours = {request.headers.get("host", "")}
    if settings.origin:
        ours.add(urlsplit(settings.origin).netloc)
    return urlsplit(origin).netloc in ours


@app.middleware("http")
async def refuse_cross_site(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """CSRF protection for every route that changes something, in every mode:
    without accounts, another site could still post to a server on your
    network. SameSite=Lax on the cookie is the second line."""
    if request.method in UNSAFE and not same_origin(request):
        return JSONResponse({"detail": "Requests from other sites are refused."}, status_code=403)
    return await call_next(request)


# --- signing in ---------------------------------------------------------------

# Until an admin can sign in, the library is claimed with this, which only
# someone who can read the server's log has. A new one each start.
SETUP_TOKEN = secrets.token_urlsafe(18)

FAILURES_ALLOWED = 10  # per username, and per address...
FAILURE_WINDOW = 15 * 60  # ...in this many seconds
_failures: dict[str, collections.deque[float]] = collections.defaultdict(collections.deque)
_failures_lock = threading.Lock()


@app.on_event("startup")
def announce_setup() -> None:
    if settings.auth == "local" and accounts.needs_setup(db()):
        log.warning(
            "No admin can sign in yet. Claim the library at /setup?token=%s "
            "or with `hikidashi user passwd local`.",
            SETUP_TOKEN,
        )
    if settings.worker_token:
        # What a worker sends, read on the worker. A server took it as a
        # secret of its own before #103; now only tokens it made are taken.
        log.warning(
            "HIKIDASHI_WORKER_TOKEN is set here, but the server does not take it: "
            "make a worker token on the Pipeline page or with `hikidashi token make "
            "<name> --worker`, give it to the worker, and unset this."
        )


def _throttled(*keys: str) -> bool:
    """Whether any of these has failed too often lately."""
    cutoff = time.monotonic() - FAILURE_WINDOW
    with _failures_lock:
        for key in keys:
            seen = _failures[key]
            while seen and seen[0] < cutoff:
                seen.popleft()
        return any(len(_failures[k]) >= FAILURES_ALLOWED for k in keys)


def _failed(*keys: str) -> None:
    with _failures_lock:
        for key in keys:
            _failures[key].append(time.monotonic())


def _sign_in(
    request: Request, response: Response, conn: sqlite3.Connection, user: User
) -> dict[str, Any]:
    token = accounts.start_session(conn, user.id)
    secure = request.url.scheme == "https" or (settings.origin or "").startswith("https:")
    response.set_cookie(
        COOKIE,
        token,
        max_age=accounts.SESSION_SECONDS,
        path="/",
        httponly=True,
        samesite="lax",
        secure=secure,
    )
    return user.as_dict()


def _local_auth_only() -> None:
    if settings.auth != "local":
        raise HTTPException(404, "Accounts are off: HIKIDASHI_AUTH is not local.")


@app.get("/api/auth")
def auth_state() -> dict[str, Any]:
    """How this server signs people in. Public: the sign-in page needs it, and
    so does the health check."""
    return {"mode": settings.auth, "setup": settings.auth == "local" and accounts.needs_setup(db())}


class Credentials(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
def login(body: Credentials, request: Request, response: Response) -> dict[str, Any]:
    _local_auth_only()
    keys = (
        f"user:{body.username.strip().lower()}",
        f"addr:{request.client.host if request.client else ''}",
    )
    if _throttled(*keys):
        raise HTTPException(429, "Too many failed sign-ins. Try again in 15 minutes.")
    conn = db()
    user = accounts.authenticate(conn, body.username, body.password)
    if user is None:
        _failed(*keys)
        raise HTTPException(401, "That username and password don't match.")
    return _sign_in(request, response, conn, user)


@app.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict[str, Any]:
    accounts.end_session(db(), request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")
    return {}


class Setup(Credentials):
    token: str


@app.post("/api/auth/setup")
def setup(body: Setup, request: Request, response: Response) -> dict[str, Any]:
    """Claim the library: name its owner, and give them a password."""
    _local_auth_only()
    conn = db()
    if not accounts.needs_setup(conn):
        raise HTTPException(409, "This library already has an admin. Sign in instead.")
    if not secrets.compare_digest(body.token, SETUP_TOKEN):
        raise HTTPException(
            403,
            "That setup link is not this server's. Copy it from "
            "the server's log again; it changes on each start.",
        )
    return _sign_in(request, response, conn, accounts.claim(conn, body.username, body.password))


@app.get("/api/auth/invite/{token}")
def invite_state(token: str) -> dict[str, Any]:
    _local_auth_only()
    return {"open": accounts.invite_open(db(), token)}


@app.post("/api/auth/invite/{token}")
def accept_invite(
    token: str, body: Credentials, request: Request, response: Response
) -> dict[str, Any]:
    """Make the account an invite is for, and sign in to it."""
    _local_auth_only()
    conn = db()
    return _sign_in(
        request, response, conn, accounts.accept(conn, token, body.username, body.password)
    )


# --- your account ---------------------------------------------------------------


@app.get("/api/me")
def me(user: Me) -> dict[str, Any]:
    return {**user.as_dict(), "auth": settings.auth}


class Profile(BaseModel):
    ig_handle: str | None = None


@app.put("/api/me")
def update_me(body: Profile, user: Me) -> dict[str, Any]:
    """Your Instagram @handle: yours to say, never checked with Instagram."""
    conn = db()
    accounts.set_handle(conn, user.id, body.ig_handle)
    me = accounts.get(conn, user.id)
    if me is None:  # disabled a moment ago
        raise HTTPException(401, "Sign in again.")
    return {**me.as_dict(), "auth": settings.auth}


class NewPassword(BaseModel):
    current: str
    new: str


@app.put("/api/me/password")
def change_password(body: NewPassword, user: Signed, request: Request) -> dict[str, Any]:
    """A new password, which signs you out everywhere but here."""
    conn = db()
    if accounts.authenticate(conn, user.username, body.current) is None:
        raise HTTPException(403, "Your current password is not that.")
    accounts.set_password(conn, user.id, body.new)
    accounts.end_sessions(conn, user.id, keep=request.cookies.get(COOKIE))
    return {}


@app.post("/api/me/sign-out-everywhere")
def sign_out_everywhere(user: Signed, request: Request) -> dict[str, Any]:
    accounts.end_sessions(db(), user.id, keep=request.cookies.get(COOKIE))
    return {}


# Your API tokens (#103): for scripts, and anything else that is not a browser.


def _my_tokens(conn: sqlite3.Connection, user: User) -> dict[str, Any]:
    """Your tokens, the scopes you may give one, and the longest it may live."""
    return {
        "tokens": accounts.tokens(conn, user_id=user.id, kind=accounts.API),
        "scopes": [s for s in accounts.SCOPES if user.is_admin or s not in accounts.ADMIN_SCOPES],
        "max_days": accounts.TOKEN_DAYS,
    }


@app.get("/api/me/tokens")
def my_tokens(user: Me) -> dict[str, Any]:
    return _my_tokens(db(), user)


class TokenIn(BaseModel):
    name: str
    scopes: list[str]
    days: int = accounts.TOKEN_DAYS


@app.post("/api/me/tokens")
def make_my_token(body: TokenIn, user: Signed) -> dict[str, Any]:
    """A new API token, shown this once."""
    conn = db()
    token, token_id = accounts.make_token(conn, user, body.name, body.scopes, body.days)
    return {"id": token_id, "token": token, **_my_tokens(conn, user)}


@app.delete("/api/me/tokens/{token_id}")
def revoke_my_token(token_id: int, user: Signed) -> dict[str, Any]:
    """Refused from its next request."""
    conn = db()
    if not accounts.revoke_token(conn, token_id, user_id=user.id):
        raise HTTPException(404, "You have no such token.")
    return _my_tokens(conn, user)


# --- other people, for admins -------------------------------------------------


@app.get("/api/users")
def users(user: Admin) -> list[dict[str, Any]]:
    return accounts.everyone(db())


class AccountChange(BaseModel):
    disabled: bool | None = None
    is_admin: bool | None = None


@app.put("/api/users/{user_id}")
def change_user(user_id: int, body: AccountChange, user: Admin) -> list[dict[str, Any]]:
    conn = db()
    accounts.update(conn, user, user_id, disabled=body.disabled, is_admin=body.is_admin)
    return accounts.everyone(conn)


@app.get("/api/invites")
def open_invites(user: Admin) -> list[dict[str, Any]]:
    return accounts.invites(db())


@app.post("/api/invites")
def make_invite(user: Admin) -> dict[str, Any]:
    """A one-time link to make an account. The token is shown this once."""
    token, expires = accounts.invite(db(), user)
    return {"path": f"/invite/{token}", "expires_at": expires}


@app.delete("/api/invites/{invite_id}")
def revoke_invite(invite_id: int, user: Admin) -> list[dict[str, Any]]:
    conn = db()
    accounts.revoke_invite(conn, invite_id)
    return accounts.invites(conn)


# --- your library ---------------------------------------------------------------


@app.get("/api/stats")
def stats(user: Me, request: Request) -> dict[str, Any]:
    conn = db()
    states = {
        r["state"]: r["n"]
        for r in conn.execute(
            f"SELECT state, COUNT(*) AS n FROM item WHERE {mine(user.id)} GROUP BY state"
        )
    }
    collections = conn.execute(
        "SELECT COUNT(DISTINCT name) AS n FROM collection WHERE user_id = ?", (user.id,)
    ).fetchone()["n"]
    last = conn.execute(
        """SELECT source_name, finished_at, seen, inserted, updated, parser
           FROM import_run WHERE finished_at IS NOT NULL AND user_id = ?
           ORDER BY id DESC LIMIT 1""",
        (user.id,),
    ).fetchone()
    coverage = conn.execute(
        f"""SELECT SUM(author IS NOT NULL)  AS with_author,
                   SUM(caption IS NOT NULL) AS with_caption,
                   SUM(hashtags IS NOT NULL) AS with_hashtags,
                   SUM(thumb_path IS NOT NULL) AS with_thumb,
                   SUM(media_path IS NOT NULL) AS with_media,
                   SUM(transcript IS NOT NULL) AS with_transcript,
                   SUM(permalink LIKE '%/reel/%') AS reels
            FROM item WHERE {mine(user.id)}"""
    ).fetchone()
    return {
        "total": sum(states.values()),
        "states": states,
        "collections": collections,
        "with_author": coverage["with_author"] or 0,
        "with_caption": coverage["with_caption"] or 0,
        "with_hashtags": coverage["with_hashtags"] or 0,
        "with_thumb": coverage["with_thumb"] or 0,
        "with_media": coverage["with_media"] or 0,
        "with_transcript": coverage["with_transcript"] or 0,
        "reels": coverage["reels"] or 0,
        "last_import": dict(last) if last else None,
        "profile": settings.profile,
        "recipes": recipes.total(conn, user_id=user.id),
        "shopping": len(shopping.listed(conn, user_id=user.id)),
        "places": conn.execute(
            f"""SELECT COUNT(DISTINCT shortcode) AS n FROM item_place
                WHERE {mine(user.id, "shortcode")}"""
        ).fetchone()["n"],
        # Places by the tag each shows: Places' Restaurants (Food) and Travel.
        "place_tags": dict(
            conn.execute(
                f"""SELECT {SHOWN_TAG}, COUNT(*) FROM {SOURCE}
                WHERE EXISTS (SELECT 1 FROM item_place p WHERE p.shortcode = item.shortcode)
                GROUP BY 1 HAVING {SHOWN_TAG} IS NOT NULL""",
                {"me": user.id},
            ).fetchall()
        ),
        # The pipeline's workers in a word, for the sidebar: whole-library
        # state, so for admins only. See /api/pipeline.
        "worker": (
            job_queue.overall(job_queue.workers(conn)) if sees_admin(request, user) else None
        ),
        # What the map needs: tiles to draw, and whether a typed place can be found.
        "map": {
            "tiles": settings.tiles,
            "attribution": settings.tiles_attribution,
            "geocoder": bool(settings.geocoder),
        },
    }


@app.get("/api/collections")
def collection_names(user: Me) -> list[dict[str, Any]]:
    conn = db()
    return [
        dict(r)
        for r in conn.execute(
            """SELECT name, COUNT(*) AS count FROM collection WHERE user_id = ?
           GROUP BY name ORDER BY count DESC, name""",
            (user.id,),
        )
    ]


@app.get("/api/tags")
def tags(user: Me) -> list[dict[str, Any]]:
    """Every category, with how many reels show it. Tags are ours; collections
    are Instagram's. Deliberately separate."""
    from app.tagging.service import tag_counts

    return tag_counts(db(), user_id=user.id)


@dataclass
class Selection:
    """Which of your reels the filters choose: SQL for after WHERE, and why
    a search chose each. Shared by the grid and the map, so that every filter
    narrows both alike. Its connection is the one to run them on: a recipe
    filter registers functions on it."""

    conn: sqlite3.Connection
    where: list[str]
    params: dict[str, Any]
    q: str | None
    why: dict[str, dict[str, Any]]

    @property
    def clause(self) -> str:
        return f"WHERE {' AND '.join(self.where)}" if self.where else ""


@dataclass
class Filters:
    """What the grid, the map and an export can be narrowed by."""

    collection: str | None = None
    # Labels, each repeatable: a reel must have every one (#117). It has one
    # tag, so two tags find nothing; a tag with a game or hashtags narrows.
    tag: list[str] = dataclasses.field(default_factory=list)
    hashtag: list[str] = dataclasses.field(default_factory=list)
    game: list[str] = dataclasses.field(default_factory=list)
    media: str | None = None
    q: str | None = None
    recipes_only: bool = False
    places_only: bool = False
    has: list[str] = dataclasses.field(default_factory=list)
    without: list[str] = dataclasses.field(default_factory=list)
    max_time: int | None = None


def filter_params(
    collection: str | None = None,
    tag: list[str] = Query([]),
    hashtag: list[str] = Query([]),
    game: list[str] = Query([]),
    media: str | None = Query(None, pattern="^(video|image)$"),
    q: str | None = None,
    recipes_only: bool = Query(False, alias="recipes"),
    places_only: bool = Query(False, alias="places"),
    has: list[str] = Query([]),
    without: list[str] = Query([]),
    max_time: int | None = Query(None, ge=1),
) -> Filters:
    return Filters(
        collection,
        _labels(tag),
        _labels(hashtag),
        _labels(game),
        media,
        q,
        recipes_only,
        places_only,
        has,
        without,
        max_time,
    )


def _labels(values: list[str]) -> list[str]:
    """Each once, in order, without blanks."""
    return list(dict.fromkeys(v.strip() for v in values if v.strip()))


FilterParams = Annotated[Filters, Depends(filter_params)]


def selection(user: Me, f: FilterParams) -> Selection:
    return select(user, f)


def select(user: User, f: Filters) -> Selection:
    """The filters, on this person's library: `selection` for whoever is
    asking, and for an export, whoever's library it is."""
    conn = db()
    where: list[str] = []
    params: dict[str, Any] = {"me": user.id}

    if f.recipes_only:
        # Only reels with a recipe, then by ingredient, ingredient group
        # ("Dairy") and time. app/recipes/groups.py says what is in a group.
        recipes.register(conn)
        where += recipes.filters(f.has, f.without, f.max_time, params, user_id=user.id)

    if f.places_only:
        # Reels whose caption names a place, found or not on the map yet.
        where.append("EXISTS (SELECT 1 FROM item_place WHERE shortcode = item.shortcode)")

    if f.collection:
        where.append(
            """item.shortcode IN (SELECT shortcode FROM collection
                                  WHERE name = :coll AND user_id = :me)"""
        )
        params["coll"] = f.collection

    for i, h in enumerate(f.hashtag):
        # json_each gives an exact match on one element, which beats a LIKE
        # over the serialised array matching "ai" inside "aigenerated". Cleaned
        # hashtags are normalised, so "#Easy_Recipe" finds "easyrecipe".
        where.append(
            f"""EXISTS (SELECT 1 FROM json_each(item.clean_hashtags)
                        WHERE json_each.value = :hashtag{i})"""
        )
        params[f"hashtag{i}"] = normalise(h)

    for i, t in enumerate(f.tag):
        where.append(f"{SHOWN_TAG} = :tag{i}")
        params[f"tag{i}"] = t

    for i, g in enumerate(f.game):
        # The game this person sees: theirs, else a sure one, never a guess.
        where.append(f"{SHOWN_GAME} = :game{i} COLLATE NOCASE")
        params[f"game{i}"] = g

    if f.media:
        # The link says which: /reel/ is a video, /p/ a photo or carousel.
        # The same rule as the reels count in /api/stats.
        where.append(f"item.permalink {'' if f.media == 'video' else 'NOT '}LIKE '%/reel/%'")

    why: dict[str, dict[str, Any]] = {}
    if f.q:
        # Keywords over captions, transcripts, summaries, tags, hashtags and
        # the tagger's keywords (FTS5), blended with
        # embedding similarity when vectors exist. app/search/ has the rules.
        query_vec = None
        if conn.execute("SELECT 1 FROM item_vec LIMIT 1").fetchone() and search.load_vec(conn):
            query_vec = query_vector(f.q)
        hits = search.rank(conn, f.q, query_vec, user_id=user.id, explain=why)
        where.append("item.shortcode IN (SELECT value FROM json_each(:hits))")
        params["hits"] = json.dumps(hits)

    return Selection(conn, where, params, f.q, why)


Selected = Annotated[Selection, Depends(selection)]

SORT = Query("saved_desc", pattern="^(relevance|saved_desc|saved_asc|author|random)$")
# Your library: only your reels, with your save times and tags.
SOURCE = "item JOIN user_item ui ON ui.shortcode = item.shortcode AND ui.user_id = :me"
CARD_COLUMNS = f"""item.shortcode, item.permalink, item.author, item.author_name,
                   ui.saved_at, item.state, item.thumb_path, item.caption,
                   item.clean_hashtags, item.duration, item.like_count,
                   item.media_path, {SHOWN_TAG} AS tag,
                   (SELECT json_group_array(name) FROM collection
                     WHERE shortcode = item.shortcode AND user_id = :me) AS collections"""


def _order(sel: Selection, sort: str) -> str:
    return {
        "saved_desc": "ui.saved_at IS NULL, ui.saved_at DESC",
        "saved_asc": "ui.saved_at IS NULL, ui.saved_at ASC",
        "author": "item.author IS NULL, item.author COLLATE NOCASE",
        # Shuffle (#50): one at random from whatever the filters chose.
        "random": "RANDOM()",
        # Best match first. Without a query there is nothing to match, so newest.
        "relevance": (
            "(SELECT key FROM json_each(:hits) WHERE value = item.shortcode)"
            if sel.q
            else "ui.saved_at IS NULL, ui.saved_at DESC"
        ),
    }[sort]


def _cards(
    conn: sqlite3.Connection, user: User, sel: Selection, rows: list[sqlite3.Row]
) -> list[dict[str, Any]]:
    """What the grid shows of each reel."""
    cards = recipes.card(conn, [r["shortcode"] for r in rows], user_id=user.id)
    return [
        {
            **{
                k: r[k]
                for k in (
                    "shortcode",
                    "permalink",
                    "author",
                    "author_name",
                    "saved_at",
                    "state",
                    "caption",
                    "duration",
                    "like_count",
                )
            },
            # Paths are relative to the media root; the browser needs a URL.
            "thumb": f"/media/{r['thumb_path']}" if r["thumb_path"] else None,
            "video": f"/media/{r['media_path']}" if r["media_path"] else None,
            "hashtags": json.loads(r["clean_hashtags"] or "[]"),
            "collections": json.loads(r["collections"] or "[]"),
            "tag": r["tag"],
            "recipe": cards.get(r["shortcode"]),
            # Why a search returned it: which of phrase, keyword and
            # semantic matched, and the similarity. None without a query.
            "match": sel.why.get(r["shortcode"]),
        }
        for r in rows
    ]


@app.get("/api/items")
def items(
    user: Me,
    sel: Selected,
    sort: str = SORT,
    limit: int = Query(60, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    conn = sel.conn
    total = conn.execute(f"SELECT COUNT(*) AS n FROM {SOURCE} {sel.clause}", sel.params).fetchone()[
        "n"
    ]

    rows = conn.execute(
        f"""SELECT {CARD_COLUMNS}
            FROM {SOURCE} {sel.clause}
            ORDER BY {_order(sel, sort)}
            LIMIT :limit OFFSET :offset""",
        {**sel.params, "limit": limit, "offset": offset},
    ).fetchall()

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": _cards(conn, user, sel, rows),
    }


def _located(conn: sqlite3.Connection, shortcodes: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Each reel's places that are on the map, in caption order."""
    out: dict[str, list[dict[str, Any]]] = {}
    for r in conn.execute(
        f"""SELECT p.shortcode, p.seq, p.text, g.lat, g.lng, g.label, g.kind
            FROM item_place p JOIN geocode g ON g.text = p.text
            WHERE p.shortcode IN (SELECT value FROM json_each(?))
              AND g.lat IS NOT NULL AND g.confidence >= {CONFIDENT}
            ORDER BY p.shortcode, p.seq""",
        (json.dumps(shortcodes),),
    ):
        out.setdefault(r["shortcode"], []).append(
            {
                **Place(r["text"]).as_dict(),
                "seq": r["seq"],
                "lat": r["lat"],
                "lng": r["lng"],
                "label": r["label"],
                "kind": r["kind"],
            }
        )
    return out


@app.get("/api/places")
def places(user: Me, sel: Selected, sort: str = SORT) -> dict[str, Any]:
    """Every chosen reel with a place on the map, each with its pins. All of
    them at once, not a page: the map shows them together. `total` is how
    many the filters chose, pinned or not, so the map can say what it leaves
    out rather than look broken."""
    conn = sel.conn
    rows = conn.execute(
        f"""SELECT {CARD_COLUMNS}, EXISTS (SELECT 1 FROM item_place
                                         WHERE shortcode = item.shortcode) AS named
            FROM {SOURCE} {sel.clause}
            ORDER BY {_order(sel, sort)}""",
        sel.params,
    ).fetchall()
    pins = _located(conn, [r["shortcode"] for r in rows if r["named"]])
    placed = [r for r in rows if r["shortcode"] in pins]
    return {
        "total": len(rows),
        "named": sum(r["named"] for r in rows),
        "items": [
            {**card, "places": pins[card["shortcode"]]} for card in _cards(conn, user, sel, placed)
        ],
    }


@app.get("/api/geocode")
def geocode_city(user: Me, q: str = Query(..., min_length=2, max_length=120)) -> dict[str, Any]:
    """Where a typed place is, to centre the map on: "Osaka". Asked of the
    geocoder at most once per text; the answer is kept with the captions'."""
    from app.places.geocoder import GeocodeError, locate
    from app.places.worker import store

    conn = db()
    text = " ".join(q.split())
    row = conn.execute("SELECT lat, lng, label FROM geocode WHERE text = ?", (text,)).fetchone()
    if row is None:
        client = geocoder()
        if not client:
            raise HTTPException(503, "No geocoder is set up: HIKIDASHI_GEOCODER is empty.")
        try:
            found = locate(text, client.search)
        except GeocodeError as exc:
            raise HTTPException(502, f"The geocoder did not answer: {exc}") from exc
        store(conn, text, found)
        row = found and {"lat": found.lat, "lng": found.lng, "label": found.label}
    if not row or row["lat"] is None:
        raise HTTPException(404, f"Nowhere called {text} was found.")
    return {"lat": row["lat"], "lng": row["lng"], "label": row["label"]}


NOT_HERE = "No reel with that shortcode in this library."


def base_url(request: Request) -> str:
    """Where people reach this server, for links that leave it: HIKIDASHI_ORIGIN
    when it is set, since a proxy in front may say otherwise."""
    return settings.origin or str(request.base_url)


@app.get("/api/items/{shortcode}")
def item_detail(shortcode: str, user: Me, request: Request) -> dict[str, Any]:
    conn = db()
    r = conn.execute(
        """SELECT item.*, ui.saved_at AS my_saved_at,
                  item.fetch_meta IS NOT NULL
                  AND COALESCE(json_array_length(item.fetch_meta, '$.formats'), 0) = 0
                    AS photo
           FROM item
           JOIN user_item ui ON ui.shortcode = item.shortcode AND ui.user_id = ?
           WHERE item.shortcode = ?""",
        (user.id, shortcode),
    ).fetchone()
    if r is None:
        raise HTTPException(404, NOT_HERE)

    from app.tagging.service import item_game, item_tags

    collections = [
        x[0]
        for x in conn.execute(
            "SELECT name FROM collection WHERE shortcode = ? AND user_id = ? ORDER BY name",
            (shortcode, user.id),
        )
    ]
    tags = item_tags(conn, shortcode, user_id=user.id)

    return {
        # The video game it shows, yours over the game pass's, and a title to
        # ask you about when neither of you has said (#117).
        "game": item_game(conn, shortcode, user_id=user.id),
        **{
            k: r[k]
            for k in (
                "shortcode",
                "permalink",
                "author",
                "author_name",
                "fbid",
                "caption",
                "state",
                "fail_stage",
                "fail_reason",
                "attempts",
                "duration",
                "width",
                "height",
                "like_count",
                "comment_count",
                "bytes",
                "fetch_v",
                "asr_v",
                "ocr_v",
                "tag_v",
                "embed_v",
                "recipe_v",
                "asr_profile",
                "tag_profile",
                "recipe_profile",
                "transcript",
                "ocr_text",
                "summary",
            )
        },
        "saved_at": r["my_saved_at"],
        "keywords": json.loads(r["keywords"] or "[]"),
        "thumb": f"/media/{r['thumb_path']}" if r["thumb_path"] else None,
        "video": f"/media/{r['media_path']}" if r["media_path"] else None,
        # The fetch found no video in the post, so there is none to download:
        # a photo, or a carousel that opens on one.
        "photo": bool(r["photo"]),
        "slides": [
            f"/media/{p}"
            for (p,) in conn.execute(
                "SELECT path FROM item_slide WHERE shortcode = ? ORDER BY position", (shortcode,)
            )
        ],
        "hashtags": json.loads(r["clean_hashtags"] or "[]"),
        "collections": collections,
        # Each source's answer, the one shown first.
        "tag": tags[0]["name"] if tags else None,
        "tags": tags,
        "places": _places_of(conn, r["caption"]),
        "profile_url": f"https://www.instagram.com/{r['author']}/" if r["author"] else None,
        "recipe": recipes.current(conn, shortcode, user_id=user.id),
        # The recipe as schema.org JSON-LD, for the sheet to embed in the page,
        # where browser extensions and recipe managers look for it.
        "jsonld": _jsonld(conn, user, shortcode, base_url(request)),
        "listed": shortcode in shopping.listed(conn, user_id=user.id),
    }


@app.post("/api/items/{shortcode}/opened", status_code=204)
def item_opened(shortcode: str, user: Me) -> Response:
    """You opened it: for Rediscover's never-opened strip (#50). Yours only."""
    conn = db()
    done = conn.execute(
        """UPDATE user_item SET opened_at = ?, open_count = open_count + 1
           WHERE user_id = ? AND shortcode = ?""",
        (int(time.time()), user.id, shortcode),
    ).rowcount
    if not done:
        raise HTTPException(404, NOT_HERE)
    return Response(status_code=204)


REDISCOVER = 12


@app.get("/api/rediscover")
def rediscover(
    user: Me,
    md: str = Query(..., pattern=r"^\d\d-\d\d$"),
    tz: int = Query(0, ge=-840, le=840),
) -> dict[str, Any]:
    """Old saves to bring back (#50): those saved on this day (`md`, MM-DD,
    in the browser's time zone, `tz` minutes behind UTC as getTimezoneOffset
    says) in earlier years, and those never opened here, oldest first."""
    conn = db()
    sel = Selection(conn, [], {"me": user.id}, None, {})
    local = "ui.saved_at - :tz * 60, 'unixepoch'"
    on_day = conn.execute(
        f"""SELECT {CARD_COLUMNS} FROM {SOURCE}
            WHERE strftime('%m-%d', {local}) = :md
              AND strftime('%Y', {local}) < strftime('%Y', 'now', :shift)
            ORDER BY ui.saved_at DESC LIMIT :n""",
        {"me": user.id, "md": md, "tz": tz, "shift": f"{-tz} minutes", "n": REDISCOVER},
    ).fetchall()
    unopened = conn.execute(
        f"""SELECT {CARD_COLUMNS} FROM {SOURCE}
            WHERE ui.opened_at IS NULL AND ui.saved_at IS NOT NULL AND item.state != 'failed'
            ORDER BY ui.saved_at ASC LIMIT :n""",
        {"me": user.id, "n": REDISCOVER},
    ).fetchall()
    return {
        "on_this_day": _cards(conn, user, sel, on_day),
        "never_opened": _cards(conn, user, sel, unopened),
    }


@app.get("/api/items/{shortcode}/similar")
def item_similar(shortcode: str, user: Me, limit: int = Query(12, ge=1, le=48)) -> dict[str, Any]:
    """The reels in your library closest to this one in meaning: more like
    this (#53). Empty without vectors."""
    conn = db()
    if not library.has(conn, user.id, shortcode):
        raise HTTPException(404, NOT_HERE)
    hits = search.similar(conn, shortcode, user.id, limit) if search.load_vec(conn) else []
    sel = Selection(conn, [], {"me": user.id}, None, {})
    rows = conn.execute(
        f"""SELECT {CARD_COLUMNS} FROM {SOURCE}
            WHERE item.shortcode IN (SELECT value FROM json_each(:hits))
            ORDER BY (SELECT key FROM json_each(:hits) WHERE value = item.shortcode)""",
        {"me": user.id, "hits": json.dumps(hits)},
    ).fetchall()
    return {"items": _cards(conn, user, sel, rows)}


def _jsonld(
    conn: sqlite3.Connection, user: User, shortcode: str, base: str
) -> dict[str, Any] | None:
    from app.export import library
    from app.export.recipes import jsonld

    reels = library(conn, user.id, shortcode)
    return jsonld(reels[0], base_url=base) if reels and reels[0]["recipe"] else None


def _places_of(conn: sqlite3.Connection, caption: str | None) -> list[dict[str, Any]]:
    """The caption's places, read now, each with where it is when that is
    known and sure enough to show."""
    found = places_in(caption)
    where = {
        r["text"]: r
        for r in conn.execute(
            f"""SELECT text, lat, lng FROM geocode WHERE lat IS NOT NULL
              AND confidence >= {CONFIDENT}
              AND text IN (SELECT value FROM json_each(?))""",
            (json.dumps([p["text"] for p in found]),),
        )
    }
    return [
        {
            **p,
            "lat": where[p["text"]]["lat"] if p["text"] in where else None,
            "lng": where[p["text"]]["lng"] if p["text"] in where else None,
        }
        for p in found
    ]


@app.delete("/api/items/{shortcode}")
def remove_item(shortcode: str, user: Me) -> dict[str, Any]:
    """Take a reel out of your library. Whoever else saved it keeps it; when
    nobody has, it is deleted from the server, video and all."""
    conn = db()
    _known_item(conn, user, shortcode)
    removed = _in_transaction(conn, lambda: library.remove(conn, user.id, shortcode))
    if removed is None:  # gone between the check and the transaction
        raise HTTPException(404, NOT_HERE)
    library.delete_files(settings.media_dir, removed.files)
    return {"shortcode": shortcode, "purged": removed.purged}


@app.post("/api/items/{shortcode}/retry")
def retry_fetch(shortcode: str, user: Me, request: Request) -> dict[str, Any]:
    """Fetch a reel whose fetch failed again, now. Anyone who has it saved may:
    the reel is shared, so a fetch that works works for everyone who has it.
    The later stages run with the worker or the pipeline, as for any newly
    fetched reel."""
    from app.enrich.worker import enrich

    conn = db()
    _known_item(conn, user, shortcode)
    row = conn.execute(
        "SELECT state, fail_stage FROM item WHERE shortcode = ?", (shortcode,)
    ).fetchone()
    if row["state"] != "failed" or row["fail_stage"] != "fetch":
        raise HTTPException(409, "Only a reel whose fetch failed can be retried.")
    enrich(
        conn,
        settings.media_dir,
        with_media=True,
        retry_failed=True,
        shortcodes=[shortcode],
        delay=0,
    )
    jobs.wake(settings.run_dir)  # the later stages, if a worker is running
    return item_detail(shortcode, user, request)


class TagName(BaseModel):
    name: str


def _known_item(conn: sqlite3.Connection, user: User, shortcode: str) -> None:
    if not library.has(conn, user.id, shortcode):
        raise HTTPException(404, NOT_HERE)


def _tag_of(conn: sqlite3.Connection, user: User, shortcode: str) -> dict[str, Any]:
    from app.tagging.service import item_tags

    tags = item_tags(conn, shortcode, user_id=user.id)
    return {"tag": tags[0]["name"] if tags else None, "tags": tags}


@app.put("/api/items/{shortcode}/tag")
def set_tag(shortcode: str, body: TagName, user: Me) -> dict[str, Any]:
    """Choose a reel's tag yourself. Stored as source='user', so no re-tag
    changes it, and it outranks the model and the hashtags."""
    from app.tagging.service import set_user_tag

    name = " ".join(body.name.split())
    if not name:
        raise HTTPException(400, "A tag needs a name.")
    if len(name) > 64:
        raise HTTPException(400, "Keep tag names under 64 characters.")
    conn = db()
    _known_item(conn, user, shortcode)
    set_user_tag(conn, shortcode, name, user_id=user.id)
    return _tag_of(conn, user, shortcode)


@app.delete("/api/items/{shortcode}/tag")
def clear_tag(shortcode: str, user: Me) -> dict[str, Any]:
    """Drop your choice; the reel shows the model's or the hashtags' again."""
    from app.tagging.service import clear_user_tag

    conn = db()
    _known_item(conn, user, shortcode)
    clear_user_tag(conn, shortcode, user_id=user.id)
    return _tag_of(conn, user, shortcode)


class GameName(BaseModel):
    # None: the reel shows no game, whatever the game pass thinks.
    name: str | None


@app.put("/api/items/{shortcode}/game")
def set_game(shortcode: str, body: GameName, user: Me) -> dict[str, Any]:
    """Say which video game a reel shows: confirm the suggestion, correct it,
    or (null) say it shows none. Yours alone, and no re-tag changes it."""
    from app.tagging.service import MAX_GAME, item_game, set_user_game

    name = " ".join((body.name or "").split())
    if body.name is not None and not name:
        raise HTTPException(400, "A game needs a title; send null for no game.")
    if len(name) > MAX_GAME:
        raise HTTPException(400, f"Keep game titles under {MAX_GAME} characters.")
    conn = db()
    _known_item(conn, user, shortcode)
    set_user_game(conn, shortcode, name or None, user_id=user.id)
    return item_game(conn, shortcode, user_id=user.id)


@app.delete("/api/items/{shortcode}/game")
def clear_game(shortcode: str, user: Me) -> dict[str, Any]:
    """Drop your answer; the reel shows the game pass's, or asks again."""
    from app.tagging.service import clear_user_game, item_game

    conn = db()
    _known_item(conn, user, shortcode)
    clear_user_game(conn, shortcode, user_id=user.id)
    return item_game(conn, shortcode, user_id=user.id)


@app.get("/api/recipes/facets")
def recipe_facets(user: Me) -> dict[str, Any]:
    """What the Recipes view can filter by, with counts."""
    return recipes.facets(db(), user_id=user.id)


class IngredientIn(BaseModel):
    item: str
    quantity: str | None = None
    unit: str | None = None
    note: str | None = None


class RecipeIn(BaseModel):
    title: str | None = None
    servings: int | None = None
    time_minutes: int | None = None
    ingredients: list[IngredientIn] = []
    steps: list[str] = []
    incomplete: bool = False
    hidden: bool = False  # "not a recipe": hides it from the Recipes view


def _in_transaction[T](conn: sqlite3.Connection, fn: Callable[[], T]) -> T:
    conn.execute("BEGIN")
    try:
        out = fn()
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return out


@app.put("/api/items/{shortcode}/recipe")
def save_recipe(shortcode: str, body: RecipeIn, user: Me) -> dict[str, Any] | None:
    """Your correction of this reel's recipe. Re-running the pass keeps it."""
    conn = db()
    _known_item(conn, user, shortcode)
    recipe = recipes.from_dict(body.model_dump())
    _in_transaction(
        conn,
        lambda: recipes.save_user(conn, shortcode, recipe, hidden=body.hidden, user_id=user.id),
    )
    return recipes.current(conn, shortcode, user_id=user.id)


@app.delete("/api/items/{shortcode}/recipe")
def revert_recipe(shortcode: str, user: Me) -> dict[str, Any] | None:
    """Drop your correction; the model's recipe, if any, shows again."""
    conn = db()
    _known_item(conn, user, shortcode)
    _in_transaction(conn, lambda: recipes.revert(conn, shortcode, user_id=user.id))
    return recipes.current(conn, shortcode, user_id=user.id)


@app.get("/api/shopping")
def shopping_list(user: Me) -> dict[str, Any]:
    """Your shopping list: its recipes, and their ingredients merged by aisle."""
    return shopping.view(db(), user_id=user.id)


class Shortcodes(BaseModel):
    shortcodes: list[str]


@app.post("/api/shopping/recipes")
def add_to_list(body: Shortcodes, user: Me) -> dict[str, Any]:
    """Put recipes on your list. Reels with no recipe you can see are skipped."""
    conn = db()
    _in_transaction(conn, lambda: shopping.add(conn, body.shortcodes[:500], user_id=user.id))
    return shopping.view(conn, user_id=user.id)


@app.post("/api/shopping/recipes/matching")
def add_matching_to_list(user: Me, sel: Selected) -> dict[str, Any]:
    """Put every recipe the filters choose on your list: a whole collection,
    or a search in the Recipes view."""
    conn = sel.conn
    codes = [
        r["shortcode"]
        for r in conn.execute(
            f"SELECT item.shortcode FROM {SOURCE} {sel.clause} LIMIT 500", sel.params
        )
    ]
    _in_transaction(conn, lambda: shopping.add(conn, codes, user_id=user.id))
    return shopping.view(conn, user_id=user.id)


@app.delete("/api/shopping/recipes/{shortcode}")
def drop_from_list(shortcode: str, user: Me) -> dict[str, Any]:
    conn = db()
    _in_transaction(conn, lambda: shopping.drop(conn, shortcode, user_id=user.id))
    return shopping.view(conn, user_id=user.id)


@app.delete("/api/shopping")
def clear_list(user: Me) -> dict[str, Any]:
    conn = db()
    _in_transaction(conn, lambda: shopping.clear(conn, user_id=user.id))
    return shopping.view(conn, user_id=user.id)


class Tick(BaseModel):
    line: str
    ticked: bool


@app.put("/api/shopping/ticks")
def tick_line(body: Tick, user: Me) -> dict[str, Any]:
    """Tick a line off, or put it back. Kept by the line's key, from the list."""
    shopping.tick(db(), body.line[:200], body.ticked, user_id=user.id)
    return {"line": body.line, "ticked": body.ticked}


# --- exports (#56) ---------------------------------------------------------------

# kind -> (file name, media type). The file is named for whose library it is.
EXPORTS = {
    "recipes.json": ("recipes.json", "application/ld+json"),
    "recipes-nextcloud.zip": ("recipes-nextcloud.zip", "application/zip"),
    "recipes.paprikarecipes": ("recipes.paprikarecipes", "application/octet-stream"),
    "markdown.zip": ("markdown.zip", "application/zip"),
    "note.md": ("note.md", "text/markdown; charset=utf-8"),
    "library.json": ("library.json", "application/json"),
    "library.csv": ("library.csv", "text/csv; charset=utf-8"),
}


def export_owner(user: Me, request: Request, of: str | None = Query(None, alias="user")) -> User:
    """Whose library an export reads: yours, or for an admin, anyone's."""
    if not of or of.lower() == user.username.lower():
        return user
    if not user.is_admin:
        raise HTTPException(403, "Only an admin can export someone else's library.")
    _scoped(request, "admin")
    owner = accounts.by_name(db(), of)
    if owner is None:
        raise HTTPException(404, f"There is no account called {of}.")
    return owner


Owner = Annotated[User, Depends(export_owner)]


def _export_name(owner: User, kind: str, f: Filters, shortcode: str | None) -> str:
    """hikidashi-alice-recipes.json; narrowed, hikidashi-alice-dinners-recipes.json."""
    from app.export.recipes import slug

    labels = "-".join([*f.tag, *f.game, *f.hashtag])
    scope = shortcode or f.collection or labels or f.q
    if not scope and (f.recipes_only or f.places_only or f.media):
        scope = "selection"
    parts = ["hikidashi", owner.username, slug(scope, 40) if scope and not shortcode else scope]
    name = EXPORTS[kind][0]
    if shortcode:  # one of it: recipe.json, reel.json
        name = name.replace("recipes", "recipe").replace("library", "reel")
    return "-".join(p for p in parts if p) + "-" + name


@app.get("/api/export/options")
def export_options(owner: Owner) -> dict[str, Any]:
    """What an export of this library can be narrowed to, for the export form."""
    from app.tagging.service import tag_counts

    conn = db()
    return {
        "user": owner.username,
        "total": conn.execute(
            "SELECT COUNT(*) FROM user_item WHERE user_id = ?", (owner.id,)
        ).fetchone()[0],
        "recipes": recipes.total(conn, user_id=owner.id),
        "collections": [
            dict(r)
            for r in conn.execute(
                """SELECT name, COUNT(*) AS count FROM collection WHERE user_id = ?
                   GROUP BY name ORDER BY name COLLATE NOCASE""",
                (owner.id,),
            )
        ],
        "tags": [t for t in tag_counts(conn, user_id=owner.id) if t.get("count")],
    }


@app.get("/api/export/{kind}")
def export_download(
    kind: str,
    owner: Owner,
    f: FilterParams,
    request: Request,
    shortcode: str | None = None,
    images: bool = False,
    videos: bool = False,
) -> FileResponse:
    """A library, or its recipes, as a file to take elsewhere: everything in
    it, what the filters choose, or one reel. Built in a temporary file that is
    deleted once sent. `images` and `videos`: the Markdown vault with its
    thumbnails and slides, and its videos, in it."""
    import io
    import tempfile
    import zipfile

    from starlette.background import BackgroundTask

    from app.export import library as reels_of
    from app.export import markdown, recipes_of, table
    from app.export import recipes as recipe_formats

    if kind not in EXPORTS:
        raise HTTPException(404, f"No export called {kind}.")
    if kind == "note.md" and not shortcode:
        raise HTTPException(400, "A note is of one reel: say which with ?shortcode=.")
    sel = select(owner, f)
    chosen = {
        r[0]
        for r in sel.conn.execute(f"SELECT item.shortcode FROM {SOURCE} {sel.clause}", sel.params)
    }
    reels = [r for r in reels_of(sel.conn, owner.id, shortcode) if r["shortcode"] in chosen]
    if shortcode and not reels:
        raise HTTPException(404, NOT_HERE)
    if shortcode and kind.startswith("recipes") and not reels[0]["recipe"]:
        raise HTTPException(404, "That reel has no recipe.")

    base = base_url(request)
    fd, tmp = tempfile.mkstemp(prefix="hikidashi-export-")
    path = Path(tmp)
    try:
        with open(fd, "wb") as out:
            if kind == "recipes.json":
                found = recipes_of(reels)
                doc = (
                    recipe_formats.jsonld(found[0], base_url=base)
                    if shortcode
                    else recipe_formats.graph(found, base_url=base)
                )
                out.write(json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8"))
            elif kind == "recipes-nextcloud.zip":
                recipe_formats.nextcloud(recipes_of(reels), settings.media_dir, out, base_url=base)
            elif kind == "recipes.paprikarecipes":
                recipe_formats.paprika(recipes_of(reels), settings.media_dir, out)
            elif kind == "note.md":
                note = markdown.reel_note(reels[0], [])
                out.write(f"{note.rstrip()}\n\n{markdown.MARKER}\n".encode())
            elif kind == "markdown.zip":
                with tempfile.TemporaryDirectory(prefix="hikidashi-vault-") as vault:
                    markdown.write(
                        reels,
                        Path(vault),
                        settings.media_dir,
                        thumbs=images or videos,
                        videos=videos,
                    )
                    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
                        for file in sorted(Path(vault).rglob("*")):
                            if file.is_file():
                                # Media is compressed already; storing it is faster.
                                how = zipfile.ZIP_STORED if file.parent.name == "media" else None
                                z.write(file, Path("Hikidashi") / file.relative_to(vault), how)
            else:
                text = io.TextIOWrapper(out, encoding="utf-8", newline="")
                (table.write_json if kind == "library.json" else table.write_csv)(reels, text)
                text.flush()
                text.detach()
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return FileResponse(
        path,
        media_type=EXPORTS[kind][1],
        filename=_export_name(owner, kind, f, shortcode),
        background=BackgroundTask(path.unlink, missing_ok=True),
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/logs")
def server_log(
    user: Admin,
    lines: int = Query(500, ge=1, le=5000),
    of: Literal["server", "worker", "server-worker"] = Query("server", alias="log"),
) -> dict[str, Any]:
    """The end of the server's log file, or a worker's on this host (under
    Docker, the worker container's or the server-worker's), oldest line first."""
    path = LOG_FILE if of == "server" else settings.logs_dir / logs.WORKER_FILES[of]
    return {"file": str(path), "lines": logs.tail(path, lines)}


# --- the pipeline's workers (#72) ---------------------------------------------


@app.get("/api/pipeline")
def pipeline_state(user: Admin) -> dict[str, Any]:
    """Per stage, what is ready, waiting, being worked on and failed; and
    every worker's last check-in. Across the whole library, so admins only."""
    conn = db()
    stages = job_queue.queue(conn)
    for stage in stages:
        failed = job_queue.failures(conn, stage["stage"], limit=10)
        links = {
            r["shortcode"]: r["permalink"]
            for r in conn.execute(
                """SELECT shortcode, permalink FROM item
                   WHERE shortcode IN (SELECT value FROM json_each(?))""",
                (json.dumps([f["shortcode"] for f in failed]),),
            )
        }
        stage["failures"] = [{**f, "permalink": links.get(f["shortcode"])} for f in failed]
    # Before the stages, and not one of them: a reel that could not be fetched
    # has nothing for them to work on. The sidebar's "failed" counts these.
    fetch = fetches.failures(conn, limit=10)
    found = job_queue.workers(conn)
    from app.ai import status as ai_status

    return {
        "fetch": fetch,
        "stages": stages,
        "workers": found,
        # The hosted APIs (#125): each one's switch, why it went off, in flight, spend.
        "apis": ai_status.apis(conn, settings.db_path.parent),
        "overall": job_queue.overall(found),
        "attempts": job_queue.MAX_ATTEMPTS,
        "stale": job_queue.STALE,
        "now": int(time.time()),
    }


@app.get("/api/me/pipeline")
def my_pipeline(user: Me) -> dict[str, Any]:
    """Your own reels' way through the pipeline (#125): waiting per stage,
    when a worker last checked in, and your API. Nothing of anyone else's."""
    from app.ai import status as ai_status

    return ai_status.mine(db(), user.id, settings.db_path.parent)


class StageName(BaseModel):
    stage: str


@app.post("/api/pipeline/retry")
def retry_stage(body: StageName, user: Admin) -> dict[str, Any]:
    """Give the reels failed at one stage, or at the fetch before them,
    another round of tries."""
    if body.stage == "fetch":
        fetches.retry(db())
    elif body.stage in STAGES:
        job_queue.retry(db(), [body.stage])
    else:
        raise HTTPException(400, f"No stage called {body.stage}.")
    jobs.wake(settings.run_dir)
    return pipeline_state(user)


# --- remote workers (#35) -----------------------------------------------------
#
# A worker on another machine, a GPU desktop say, claims reels here, downloads
# their media, and sends back what it made of them. The server records it with
# the stages' own functions, so it stays the only writer of the database. See
# app/jobs/protocol.py. Not a person: a token of its own, not an account.


def worker_auth(request: Request) -> User:
    """The worker's token, as `Authorization: Bearer <token>`: one an admin
    made on the Pipeline page or with `hikidashi token make --worker`. Off, a
    404, while there is none."""
    conn = db()
    if not accounts.any_worker_token(conn):
        raise HTTPException(
            404, "Remote workers are off: make a worker token on the Pipeline page."
        )
    given = bearer(request) or ""
    if not accounts.worker_token_ok(conn, given):
        raise _unauthorized("Wrong or missing worker token.", "invalid_token" if given else None)
    found = accounts.find_token(conn, given)
    assert found is not None
    return found.user


# The worker token's owner: an admin.
RemoteWorker = Annotated[User, Depends(worker_auth)]


class WorkerIn(BaseModel):
    worker: str = Field(min_length=1, max_length=64)
    protocol: int
    versions: dict[str, int] = {}


class ClaimIn(WorkerIn):
    stage: str
    limit: int = Field(8, ge=1, le=64)


class ResultsIn(WorkerIn):
    stage: str
    profile: str
    results: list[dict[str, Any]]
    # A hosted worker's (#125): the language each transcript is in, and what
    # each reel's requests used.
    languages: dict[str, str | None] | None = None
    usage: list[dict[str, Any]] | None = None


class ReleaseIn(WorkerIn):
    stage: str
    shortcodes: list[str] = Field(max_length=64)
    count: bool = True


class CheckInIn(WorkerIn):
    stages: list[str]
    started_at: float
    doing: str | None = None
    problems: dict[str, str] | None = None
    pid: int | None = None
    renew: bool = False


def _remote(body: WorkerIn, stage: str) -> None:
    try:
        protocol.check(stage, body.protocol, body.versions)
    except protocol.Refused as exc:
        raise HTTPException(409, str(exc)) from exc


def _released(out: job_queue.Released) -> dict[str, Any]:
    return {"done": out.done, "again": out.again, "failed": out.failed}


@app.post("/api/worker/claim")
def worker_claim(body: ClaimIn, _: RemoteWorker) -> dict[str, Any]:
    """Lease reels ready for a stage, and send their rows."""
    _remote(body, body.stage)
    conn = db()
    if body.stage == "tag":
        # A local worker runs the hashtag pass before it tags; this one can't.
        from app.tagging.service import apply_hashtags

        apply_hashtags(conn)
    codes = job_queue.LocalJobs(conn, body.worker).claim(body.stage, body.limit)
    return {"jobs": protocol.rows(conn, body.stage, codes)}


@app.post("/api/worker/results")
def worker_results(body: ResultsIn, _: RemoteWorker) -> dict[str, Any]:
    """Record what a worker made of the reels it holds, and give them back:
    what is still to do after this counts as an attempt, as locally."""
    _remote(body, body.stage)
    hosted = bool(protocol.HOSTED_PROFILE.match(body.profile))
    if body.profile not in ("cpu", "cuda") and not hosted:
        raise HTTPException(422, f"Unknown profile {body.profile!r}.")
    try:
        parsed = protocol.parse(body.stage, body.results)
    except protocol.Refused as exc:
        raise HTTPException(422, str(exc)) from exc
    conn = db()
    source = job_queue.LocalJobs(conn, body.worker)
    held = set(source.held().get(body.stage, []))
    languages = None
    if hosted and body.languages:
        languages = {k: (v[:16] if isinstance(v, str) else None) for k, v in body.languages.items()}
    recorded = protocol.record(
        conn, body.stage, parsed, profile=body.profile, held=held, languages=languages
    )
    if hosted:
        from app.ai import remote as hosted_remote

        hosted_remote.record_usage(conn, body.worker, body.stage, body.usage)
    out = source.release(body.stage, [c for c, _, _ in parsed if c in held])
    return {"recorded": recorded, **_released(out)}


class HostedClaimIn(WorkerIn):
    scope: str = Field(max_length=3)  # me | all: --for
    limit: int = Field(4, ge=1, le=16)


class HostedRowsIn(WorkerIn):
    stage: str
    shortcodes: list[str] = Field(max_length=16)


class HostedErrorIn(WorkerIn):
    shortcode: str = Field(max_length=64)
    reason: str = Field(max_length=500)


def _hosted(body: WorkerIn) -> None:
    for stage in protocol.REMOTE:
        _remote(body, stage)


@app.post("/api/worker/hosted/claim")
def hosted_claim(body: HostedClaimIn, owner: RemoteWorker) -> dict[str, Any]:
    """A hosted worker (#125) leases reels for all their remaining stages:
    those the server's API would take, for the token owner or for all."""
    from app.ai import remote as hosted_remote

    _hosted(body)
    try:
        return hosted_remote.claim(
            db(), body.worker, owner.id, body.scope, body.limit, settings.db_path.parent
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/worker/hosted/rows")
def hosted_rows(body: HostedRowsIn, _: RemoteWorker) -> dict[str, Any]:
    """A stage's rows for the reels a hosted worker holds that still need it."""
    from app.ai import remote as hosted_remote

    _remote(body, body.stage)
    return {"jobs": hosted_remote.rows(db(), body.worker, body.stage, body.shortcodes)}


@app.post("/api/worker/hosted/error")
def hosted_error(body: HostedErrorIn, _: RemoteWorker) -> dict[str, Any]:
    """An API error on a reel: its stages back, uncounted; counted apart."""
    from app.ai import remote as hosted_remote

    _hosted(body)
    return {"errors": hosted_remote.error(db(), body.worker, body.shortcode, body.reason)}


@app.post("/api/worker/release")
def worker_release(body: ReleaseIn, _: RemoteWorker) -> dict[str, Any]:
    """Give reels back unrecorded: count=false when the stage could not run
    at all, which is not the reels' fault."""
    _remote(body, body.stage)
    out = job_queue.LocalJobs(db(), body.worker).release(
        body.stage, body.shortcodes, count=body.count
    )
    return _released(out)


@app.post("/api/worker/check-in")
def worker_check_in(body: CheckInIn, _: RemoteWorker) -> dict[str, Any]:
    """The heartbeat: alive, what it runs and is doing, and, with renew, keep
    its leases."""
    for stage in body.stages:
        _remote(body, stage)
    source = job_queue.LocalJobs(db(), body.worker)
    if body.renew:
        source.renew()
    source.check_in(
        stages=tuple(body.stages),
        started_at=body.started_at,
        doing=body.doing,
        problems=body.problems,
        pid=body.pid,
    )
    return {"ok": True}


@app.post("/api/worker/reclaim")
def worker_reclaim(body: WorkerIn, _: RemoteWorker) -> dict[str, Any]:
    """At a worker's start: take back what its last run died holding."""
    return {"taken": job_queue.LocalJobs(db(), body.worker).reclaim()}


@app.post("/api/worker/check-out")
def worker_check_out(body: WorkerIn, _: RemoteWorker) -> dict[str, Any]:
    """A clean stop: its leases back uncounted."""
    job_queue.LocalJobs(db(), body.worker).check_out()
    return {"ok": True}


class WorkerTokenIn(BaseModel):
    name: str


@app.get("/api/tokens")
def everyones_tokens(user: Admin) -> list[dict[str, Any]]:
    """Every user's API tokens, for an admin to see who can reach what. Never
    the tokens."""
    return accounts.tokens(db(), kind=accounts.API)


@app.delete("/api/tokens/{token_id}")
def revoke_anyones_token(token_id: int, user: Admin, _: Signed) -> list[dict[str, Any]]:
    conn = db()
    if not accounts.revoke_token(conn, token_id):
        raise HTTPException(404, "There is no such token.")
    return accounts.tokens(conn, kind=accounts.API)


@app.get("/api/worker-tokens")
def list_worker_tokens(user: Admin) -> list[dict[str, Any]]:
    """The tokens workers on other machines sign in with. Never the tokens."""
    return accounts.worker_tokens(db())


@app.post("/api/worker-tokens")
def make_worker_token(body: WorkerTokenIn, user: Admin, _: Signed) -> dict[str, Any]:
    """A new worker token, shown this once."""
    conn = db()
    token, token_id = accounts.make_worker_token(conn, user, body.name)
    return {"id": token_id, "token": token, "tokens": accounts.worker_tokens(conn)}


@app.delete("/api/worker-tokens/{token_id}")
def revoke_worker_token(token_id: int, user: Admin, _: Signed) -> list[dict[str, Any]]:
    """A worker using it is refused from its next request."""
    conn = db()
    accounts.revoke_worker_token(conn, token_id)
    return accounts.worker_tokens(conn)


@app.get("/api/worker/media/{path:path}", include_in_schema=False)
def worker_media(path: str, _: RemoteWorker) -> FileResponse:
    """A reel's video or thumbnail, for a worker to read. Any reel's: a worker
    works on the whole library, not one person's."""
    known = (
        db()
        .execute("SELECT 1 FROM item WHERE media_path = :p OR thumb_path = :p", {"p": path})
        .fetchone()
    )
    root = settings.media_dir.resolve()
    file = (root / path).resolve()
    if not known or not file.is_relative_to(root) or not file.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(file, headers={"Cache-Control": "no-store"})


@app.post("/api/import")
def upload_export(file: UploadFile, user: Me) -> dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(400, "Upload the export ZIP from Instagram.")

    # A folder each, so two people's instagram-export.zip never collide.
    dest = settings.imports_dir / str(user.id) / Path(file.filename).name
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    try:
        result = import_export(db(), dest, user_id=user.id)
    except ImportError_ as exc:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, f"That ZIP has no saved_posts.json in it. ({exc})") from exc
    if _locator:
        _locator.wake()
    jobs.wake(settings.run_dir)
    return result.as_dict()


@app.api_route("/media/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
def media(path: str, user: Me) -> FileResponse:
    """A thumbnail, slide or video, to someone who has its reel saved.
    FileResponse serves byte ranges, so reels can be scrubbed in place."""
    conn = db()
    known = conn.execute(
        f"""SELECT 1 FROM item WHERE (thumb_path = :p OR media_path = :p
              OR shortcode IN (SELECT shortcode FROM item_slide WHERE path = :p))
            AND {mine(user.id)}""",
        {"p": path},
    ).fetchone()
    root = settings.media_dir.resolve()
    file = (root / path).resolve()
    if not known or not file.is_relative_to(root) or not file.is_file():
        raise HTTPException(404, "Not found")
    # Private: it is one person's, and a shared cache must not hand it on.
    return FileResponse(file, headers={"Cache-Control": "private, max-age=86400"})


NOT_BUILT = """<!doctype html><meta charset="utf-8"><title>Hikidashi</title>
<p style="font-family:system-ui;margin:3em auto;max-width:40em">
The front end has not been built. From the repository root run
<code>npm ci --prefix frontend &amp;&amp; npm run build --prefix frontend</code>,
or use <code>npm run dev</code> in <code>frontend/</code> while developing.</p>"""


# Registered last so every API route and the media mount match first.
@app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
def web(path: str) -> Response:
    """The Svelte app: built files as themselves, every other route as index.html."""
    hit = spa.resolve(WEB, path)
    if hit is not None:
        file, cache = hit
        return FileResponse(file, headers={"Cache-Control": cache})
    if not path.startswith(spa.BACKEND) and not (WEB / "index.html").is_file():
        return HTMLResponse(NOT_BUILT, status_code=503)
    raise HTTPException(404, "Not found")
