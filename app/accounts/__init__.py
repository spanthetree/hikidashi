"""The people who use this library, and how they sign in. Stdlib only, except
for argon2, which only hashing a password imports.

Each person has a library of their own (app/library). User 1 always exists: it
owned the library before there were accounts, and with HIKIDASHI_AUTH=none
every request is it. It has no password until someone claims it, with
`hikidashi user passwd local` or the one-time setup link the server logs.

Nobody signs up: an admin invites people with a one-time link. Sessions and
invites are random tokens, and only their SHA-256 is stored, so a copy of the
database cannot be used to sign in.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import string
import time
import zlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.library import LOCAL

if TYPE_CHECKING:
    from argon2 import PasswordHasher


USERNAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,31}")
HANDLE = re.compile(r"[A-Za-z0-9._]{1,30}")  # Instagram's own rules
MIN_PASSWORD = 8
SESSION_SECONDS = 30 * 86400  # sliding: each day of use pushes it back
INVITE_SECONDS = 7 * 86400


class AccountError(ValueError):
    """Something the account rules refuse. The message is for the person."""


@dataclass(frozen=True)
class User:
    id: int
    username: str
    ig_handle: str | None = None
    is_admin: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "username": self.username,
            "ig_handle": self.ig_handle,
            "is_admin": self.is_admin,
        }


def _user(row: sqlite3.Row | None) -> User | None:
    if row is None:
        return None
    return User(
        id=row["id"],
        username=row["username"],
        ig_handle=row["ig_handle"],
        is_admin=bool(row["is_admin"]),
    )


def get(conn: sqlite3.Connection, user_id: int) -> User | None:
    """An account that may use the library: None if missing or disabled."""
    return _user(
        conn.execute("SELECT * FROM user WHERE id = ? AND NOT disabled", (user_id,)).fetchone()
    )


def by_name(conn: sqlite3.Connection, username: str) -> User | None:
    return _user(
        conn.execute(
            "SELECT * FROM user WHERE username = ? AND NOT disabled", (username,)
        ).fetchone()
    )


def first_admin(conn: sqlite3.Connection) -> User | None:
    """The longest-standing admin who may still sign in, for the CLI to act as."""
    return _user(
        conn.execute("SELECT * FROM user WHERE is_admin AND NOT disabled ORDER BY id").fetchone()
    )


def local(conn: sqlite3.Connection) -> User:
    """The library's owner, whom every request is when nobody signs in."""
    user = get(conn, LOCAL)
    assert user is not None, "user 1 is created with the schema"
    return user


def _transaction[T](conn: sqlite3.Connection, fn: Callable[[], T]) -> T:
    conn.execute("BEGIN IMMEDIATE")
    try:
        out = fn()
        conn.execute("COMMIT")
        return out
    except BaseException:
        conn.execute("ROLLBACK")
        raise


# --- names and passwords -----------------------------------------------------


def check_username(name: str) -> str:
    name = name.strip()
    if not USERNAME.fullmatch(name):
        raise AccountError(
            "Usernames are 1 to 32 letters, digits, dots, dashes or "
            "underscores, starting with a letter or digit."
        )
    return name


def check_handle(handle: str | None) -> str | None:
    """An Instagram @handle as people type it, or None to clear it."""
    handle = (handle or "").strip().removeprefix("@").lower()
    if not handle:
        return None
    if not HANDLE.fullmatch(handle):
        raise AccountError("An Instagram handle is up to 30 letters, digits, dots or underscores.")
    return handle


def _hasher() -> PasswordHasher:
    from argon2 import PasswordHasher

    return PasswordHasher()


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD:
        raise AccountError(f"Use a password of at least {MIN_PASSWORD} characters.")
    return _hasher().hash(password)


_DECOY: str | None = None


def _verify(stored: str | None, password: str) -> bool:
    """Whether `password` matches. Takes as long when there is no account, so
    timing does not tell anyone which usernames exist."""
    from argon2.exceptions import InvalidHashError, VerificationError

    global _DECOY
    if stored is None:
        _DECOY = _DECOY or _hasher().hash(secrets.token_hex())
        stored, password = _DECOY, ""
    try:
        return _hasher().verify(stored, password)
    except (VerificationError, InvalidHashError):
        return False


def authenticate(conn: sqlite3.Connection, username: str, password: str) -> User | None:
    row = conn.execute(
        "SELECT * FROM user WHERE username = ? AND NOT disabled", (username.strip(),)
    ).fetchone()
    if not _verify(row["password_hash"] if row else None, password) or row is None:
        return None
    if _hasher().check_needs_rehash(row["password_hash"]):
        conn.execute(
            "UPDATE user SET password_hash = ? WHERE id = ?", (_hasher().hash(password), row["id"])
        )
    return _user(row)


def create(
    conn: sqlite3.Connection, username: str, password: str | None, *, is_admin: bool = False
) -> User:
    """A new account. None for `password` makes one that cannot sign in yet."""
    name = check_username(username)
    hashed = hash_password(password) if password is not None else None
    try:
        cur = conn.execute(
            """INSERT INTO user (username, password_hash, is_admin, created_at)
               VALUES (?, ?, ?, ?)""",
            (name, hashed, int(is_admin), int(time.time())),
        )
    except sqlite3.IntegrityError:
        raise AccountError(f"The username {name} is taken.") from None
    user = get(conn, cur.lastrowid or 0)
    assert user is not None, "just inserted"
    return user


def set_password(conn: sqlite3.Connection, user_id: int, password: str) -> None:
    conn.execute(
        "UPDATE user SET password_hash = ? WHERE id = ?", (hash_password(password), user_id)
    )


def rename(conn: sqlite3.Connection, user_id: int, username: str) -> None:
    name = check_username(username)
    try:
        conn.execute("UPDATE user SET username = ? WHERE id = ?", (name, user_id))
    except sqlite3.IntegrityError:
        raise AccountError(f"The username {name} is taken.") from None


def set_handle(conn: sqlite3.Connection, user_id: int, handle: str | None) -> None:
    conn.execute("UPDATE user SET ig_handle = ? WHERE id = ?", (check_handle(handle), user_id))


def needs_setup(conn: sqlite3.Connection) -> bool:
    """True until some admin can sign in: nobody has claimed the library yet."""
    return (
        conn.execute(
            """SELECT 1 FROM user WHERE is_admin AND NOT disabled
           AND password_hash IS NOT NULL"""
        ).fetchone()
        is None
    )


def claim(conn: sqlite3.Connection, username: str, password: str) -> User:
    """Give user 1, the library's owner, a name and a password."""

    def run() -> None:
        rename(conn, LOCAL, username)
        set_password(conn, LOCAL, password)
        conn.execute("UPDATE user SET is_admin = 1, disabled = 0 WHERE id = ?", (LOCAL,))

    _transaction(conn, run)
    return local(conn)


# --- sessions ----------------------------------------------------------------


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(conn: sqlite3.Connection, user_id: int) -> str:
    """A new signed-in session. Returns the token for the cookie."""
    now = int(time.time())
    conn.execute("DELETE FROM session WHERE expires_at < ?", (now,))
    token = secrets.token_urlsafe(32)
    conn.execute(
        """INSERT INTO session (token_hash, user_id, created_at, last_seen, expires_at)
           VALUES (?, ?, ?, ?, ?)""",
        (_digest(token), user_id, now, now, now + SESSION_SECONDS),
    )
    return token


def session_user(conn: sqlite3.Connection, token: str | None) -> User | None:
    """Who this cookie signs in, if anyone. Using a session keeps it alive."""
    if not token:
        return None
    now = int(time.time())
    row = conn.execute(
        """SELECT user.*, session.last_seen FROM session
           JOIN user ON user.id = session.user_id AND NOT user.disabled
           WHERE session.token_hash = ? AND session.expires_at > ?""",
        (_digest(token), now),
    ).fetchone()
    if row is None:
        return None
    if now - row["last_seen"] > 86400:  # one write a day, not one a request
        conn.execute(
            "UPDATE session SET last_seen = ?, expires_at = ? WHERE token_hash = ?",
            (now, now + SESSION_SECONDS, _digest(token)),
        )
    return _user(row)


def end_session(conn: sqlite3.Connection, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM session WHERE token_hash = ?", (_digest(token),))


def end_sessions(conn: sqlite3.Connection, user_id: int, *, keep: str | None = None) -> None:
    """Sign this person out everywhere, except the session `keep` if given."""
    conn.execute(
        "DELETE FROM session WHERE user_id = ? AND token_hash != ?",
        (user_id, _digest(keep) if keep else ""),
    )


# --- invites -----------------------------------------------------------------


def invite(conn: sqlite3.Connection, by: User) -> tuple[str, int]:
    """A one-time link to make an account: (token, expires at)."""
    token = secrets.token_urlsafe(24)
    expires = int(time.time()) + INVITE_SECONDS
    conn.execute(
        """INSERT INTO invite (token_hash, created_by, created_at, expires_at)
           VALUES (?, ?, ?, ?)""",
        (_digest(token), by.id, int(time.time()), expires),
    )
    return token, expires


def _open_invite(conn: sqlite3.Connection, token: str) -> int | None:
    row = conn.execute(
        """SELECT id FROM invite WHERE token_hash = ? AND used_by IS NULL
           AND expires_at > ?""",
        (_digest(token), int(time.time())),
    ).fetchone()
    return row[0] if row else None


def invite_open(conn: sqlite3.Connection, token: str) -> bool:
    return _open_invite(conn, token) is not None


def accept(conn: sqlite3.Connection, token: str, username: str, password: str) -> User:
    """Make the account an invite was for. Each invite makes one."""

    def run() -> User:
        invite_id = _open_invite(conn, token)
        if invite_id is None:
            raise AccountError("This invite link has expired or has been used.")
        user = create(conn, username, password)
        conn.execute(
            "UPDATE invite SET used_by = ?, used_at = ? WHERE id = ?",
            (user.id, int(time.time()), invite_id),
        )
        return user

    return _transaction(conn, run)


def invites(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Invites not used yet and not expired."""
    return [
        dict(r)
        for r in conn.execute(
            """SELECT invite.id, invite.created_at, invite.expires_at,
                  user.username AS created_by
           FROM invite JOIN user ON user.id = invite.created_by
           WHERE used_by IS NULL AND expires_at > ? ORDER BY invite.id DESC""",
            (int(time.time()),),
        )
    ]


def revoke_invite(conn: sqlite3.Connection, invite_id: int) -> None:
    conn.execute("DELETE FROM invite WHERE id = ? AND used_by IS NULL", (invite_id,))


# --- tokens (#103) -----------------------------------------------------------
#
# What anything that is not a browser presents instead of a session cookie, as
# `Authorization: Bearer <token>`. Shown once; the server keeps its hash. Two
# kinds, and neither stands in for the other:
#
# - api: a person's own, for their own library, with the scopes they pick and
#   a life of 30 days at most. Only an admin's can have the admin scopes, and
#   those stop counting the moment the owner stops being an admin.
# - worker: an admin's, for a worker on another machine (#35). It opens
#   /api/worker/* and nothing else, and lasts until it is revoked.
#
# A token reads hk_api_ or hk_wkr_, then 30 random base62 characters (178
# bits), then a 6-character CRC32 of them, as GitHub's do: the prefix says
# what a leaked one is, and the checksum turns a mistyped one away unlooked-up.

API, WORKER = "api", "worker"
SCOPES = ("read:library", "write:library", "read:admin", "write:admin")
ADMIN_SCOPES = frozenset({"read:admin", "write:admin"})
TOKEN_NAME = 64  # characters
TOKEN_DAYS = 30  # the longest an api token lives; there is no extending one
USED_EVERY = 60  # seconds: last_used_at is written at most this often

_PREFIX = {API: "hk_api_", WORKER: "hk_wkr_"}
_BASE62 = string.digits + string.ascii_letters
_RANDOM, _CHECK = 30, 6


@dataclass(frozen=True)
class Token:
    """A live token, and whose it is."""

    id: int
    kind: str
    scopes: frozenset[str]
    user: User

    def grants(self, area: str, *, write: bool) -> bool:
        """Whether this lets its owner read `area` ("library" or "admin"), or
        with `write` change it. Writing includes reading."""
        if self.kind != API or (area == "admin" and not self.user.is_admin):
            return False
        return f"write:{area}" in self.scopes or (not write and f"read:{area}" in self.scopes)


def _base62(n: int, width: int) -> str:
    out = ""
    while n:
        n, digit = divmod(n, 62)
        out = _BASE62[digit] + out
    return out.rjust(width, "0")


def _new_token(kind: str) -> str:
    body = "".join(secrets.choice(_BASE62) for _ in range(_RANDOM))
    return _PREFIX[kind] + body + _base62(zlib.crc32(body.encode()), _CHECK)


def well_formed(token: str) -> bool:
    """Whether `token` could be one of ours, checked without the database. A
    worker token made before #103 has no prefix, and is only looked up."""
    for prefix in _PREFIX.values():
        if token.startswith(prefix):
            body, check = token[len(prefix) : -_CHECK], token[-_CHECK:]
            return len(body) == _RANDOM and _base62(zlib.crc32(body.encode()), _CHECK) == check
    return bool(token)


def _check_name(name: str) -> str:
    name = " ".join(name.split())
    if not name or len(name) > TOKEN_NAME:
        raise AccountError(f"Name it, in at most {TOKEN_NAME} characters.")
    return name


def check_scopes(owner: User, scopes: Iterable[str]) -> str:
    """The scopes as stored, in SCOPES order, or why they cannot be had."""
    chosen = set(scopes)
    if not chosen:
        raise AccountError("Pick at least one scope.")
    if unknown := sorted(chosen - set(SCOPES)):
        raise AccountError(f"There is no scope called {unknown[0]}.")
    if chosen & ADMIN_SCOPES and not owner.is_admin:
        raise AccountError("Only an admin's token can have an admin scope.")
    return " ".join(s for s in SCOPES if s in chosen)


def _insert(
    conn: sqlite3.Connection,
    kind: str,
    owner: User,
    name: str,
    scopes: str,
    expires_at: int | None,
) -> tuple[str, int]:
    token = _new_token(kind)
    cur = conn.execute(
        """INSERT INTO api_token
             (kind, user_id, name, scopes, token_hash, created_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (kind, owner.id, name, scopes, _digest(token), int(time.time()), expires_at),
    )
    return token, int(cur.lastrowid or 0)


def make_token(
    conn: sqlite3.Connection,
    owner: User,
    name: str,
    scopes: Iterable[str],
    days: int = TOKEN_DAYS,
) -> tuple[str, int]:
    """A new api token for `owner`'s own use: (the token, its id)."""
    name = _check_name(name)
    stored = check_scopes(owner, scopes)
    if not 1 <= days <= TOKEN_DAYS:
        raise AccountError(f"A token lives 1 to {TOKEN_DAYS} days.")
    return _insert(conn, API, owner, name, stored, int(time.time()) + days * 86400)


def make_worker_token(conn: sqlite3.Connection, by: User, name: str) -> tuple[str, int]:
    """A new worker token named `name`: (the token, its id)."""
    name = _check_name(name)
    if not by.is_admin:
        raise AccountError("Only an admin can make a worker token.")
    return _insert(conn, WORKER, by, name, WORKER, None)


def tokens(
    conn: sqlite3.Connection, *, user_id: int | None = None, kind: str | None = None
) -> list[dict[str, Any]]:
    """Live tokens, newest first, everyone's or one person's. Never the tokens
    themselves. Expired ones are cleared out on the way."""
    conn.execute("DELETE FROM api_token WHERE expires_at <= ?", (int(time.time()),))
    rows = conn.execute(
        """SELECT t.id, t.kind, t.name, t.scopes, t.created_at, t.expires_at,
                  t.last_used_at, u.username AS owner
           FROM api_token t JOIN user u ON u.id = t.user_id
           WHERE (? IS NULL OR t.user_id = ?) AND (? IS NULL OR t.kind = ?)
           ORDER BY t.created_at DESC, t.id DESC""",
        (user_id, user_id, kind, kind),
    )
    return [{**dict(r), "scopes": r["scopes"].split()} for r in rows]


def revoke_token(
    conn: sqlite3.Connection,
    token_id: int,
    *,
    user_id: int | None = None,
    kind: str | None = API,
) -> bool:
    """Delete a token: an api token unless `kind` says otherwise, and only
    `user_id`'s when that is given. Whether there was one."""
    cur = conn.execute(
        """DELETE FROM api_token WHERE id = ? AND (? IS NULL OR user_id = ?)
           AND (? IS NULL OR kind = ?)""",
        (token_id, user_id, user_id, kind, kind),
    )
    return cur.rowcount > 0


def find_token(conn: sqlite3.Connection, token: str | None) -> Token | None:
    """The live token `token` is, if any, with its owner as they are now.
    Notes when it was last used."""
    if not token or not well_formed(token):
        return None
    now = int(time.time())
    row = conn.execute(
        """SELECT t.id AS token_id, t.kind, t.scopes, t.last_used_at, user.*
           FROM api_token t JOIN user ON user.id = t.user_id AND NOT user.disabled
           WHERE t.token_hash = ? AND (t.expires_at IS NULL OR t.expires_at > ?)""",
        (_digest(token), now),
    ).fetchone()
    if row is None:
        return None
    if row["last_used_at"] is None or now - row["last_used_at"] >= USED_EVERY:
        conn.execute("UPDATE api_token SET last_used_at = ? WHERE id = ?", (now, row["token_id"]))
    user = _user(row)
    assert user is not None, "joined"
    return Token(row["token_id"], row["kind"], frozenset(row["scopes"].split()), user)


# Worker tokens, as the Pipeline page and /api/worker/* use them.


def worker_tokens(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every worker token, newest first. Never the tokens themselves."""
    return [
        {k: t[k] for k in ("id", "name", "created_at", "last_used_at")} | {"created_by": t["owner"]}
        for t in tokens(conn, kind=WORKER)
    ]


def revoke_worker_token(conn: sqlite3.Connection, token_id: int) -> None:
    revoke_token(conn, token_id, kind=WORKER)


def any_worker_token(conn: sqlite3.Connection) -> bool:
    found = conn.execute("SELECT 1 FROM api_token WHERE kind = ? LIMIT 1", (WORKER,))
    return found.fetchone() is not None


def worker_token_ok(conn: sqlite3.Connection, token: str) -> bool:
    """Whether `token` is a live worker token whose owner is still an admin."""
    found = find_token(conn, token)
    return found is not None and found.kind == WORKER and found.user.is_admin


# --- for admins --------------------------------------------------------------


def everyone(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        {
            **dict(r),
            "is_admin": bool(r["is_admin"]),
            "disabled": bool(r["disabled"]),
            "has_password": bool(r["has_password"]),
        }
        for r in conn.execute(
            """SELECT id, username, ig_handle, is_admin, disabled, created_at,
                  password_hash IS NOT NULL AS has_password,
                  (SELECT COUNT(*) FROM user_item WHERE user_id = user.id) AS reels
           FROM user ORDER BY id"""
        )
    ]


def update(
    conn: sqlite3.Connection,
    by: User,
    user_id: int,
    *,
    disabled: bool | None = None,
    is_admin: bool | None = None,
) -> None:
    """Disable or enable an account, or make it an admin or not. Nobody changes
    their own, and there is always an admin left who can sign in."""
    if user_id == by.id:
        raise AccountError("You can't change your own account here.")

    def run() -> None:
        if not conn.execute("SELECT 1 FROM user WHERE id = ?", (user_id,)).fetchone():
            raise AccountError("There is no such account.")
        if disabled is not None:
            conn.execute("UPDATE user SET disabled = ? WHERE id = ?", (int(disabled), user_id))
            if disabled:
                end_sessions(conn, user_id)
                conn.execute("DELETE FROM api_token WHERE user_id = ?", (user_id,))
        if is_admin is not None:
            conn.execute("UPDATE user SET is_admin = ? WHERE id = ?", (int(is_admin), user_id))
        if needs_setup(conn):
            raise AccountError("That would leave no admin who can sign in.")

    _transaction(conn, run)
