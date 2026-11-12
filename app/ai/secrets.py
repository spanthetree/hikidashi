"""API keys encrypted at rest (#125).

A key a user or the admin saves is kept in the `setting` table encrypted with
Fernet (AES-128-CBC with an HMAC), from `cryptography`, since the stdlib has no
cipher. The encryption key is `HIKIDASHI_SECRET_KEY`, or one generated on first
use into `data/secret.key` (mode 0600), outside the SQLite file.

This protects a database copied on its own. A backup of all of `data/` holds
both, and so can read the keys: docs/operation.md says so. Losing the secret
key loses no data; the keys saved under it read as missing, and have to be
entered again.
"""

from __future__ import annotations

import base64
import hashlib
import os
import threading
from pathlib import Path
from typing import Any

from app.providers.keys import Key

FILE = "secret.key"


class Unreadable(Exception):
    """A saved key no longer decrypts: the secret key changed or was lost."""


class NoCipher(Exception):
    """`cryptography` is not installed: the server extra is incomplete, as
    in a venv synced before it was added. Keys can be neither saved nor read."""

    def __init__(self) -> None:
        super().__init__(
            "the server cannot encrypt API keys: the cryptography package is missing "
            "(uv sync --extra server --extra worker)"
        )


def _fernet() -> Any:
    try:
        from cryptography import fernet
    except ImportError:
        raise NoCipher() from None
    return fernet


_lock = threading.Lock()
_ciphers: dict[str, Any] = {}


def _material(env: str | None, data_dir: Path) -> bytes:
    """A Fernet key: HIKIDASHI_SECRET_KEY (any string, hashed to size), or
    data/secret.key, made the first time."""
    if env and env.strip():
        return base64.urlsafe_b64encode(hashlib.sha256(env.strip().encode()).digest())
    path = data_dir / FILE
    try:
        return path.read_bytes().strip()
    except FileNotFoundError:
        pass
    data_dir.mkdir(parents=True, exist_ok=True)
    made = base64.urlsafe_b64encode(os.urandom(32))
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:  # made by another process meanwhile
        return path.read_bytes().strip()
    with os.fdopen(fd, "wb") as f:
        f.write(made + b"\n")
    return made


def _cipher(data_dir: Path) -> Any:
    env = os.environ.get("HIKIDASHI_SECRET_KEY")
    if not env and (f := os.environ.get("HIKIDASHI_SECRET_KEY_FILE")):
        env = Path(f).read_text(encoding="utf-8")
    fernet = _fernet()
    material = _material(env, data_dir)
    with _lock:
        cache = material.decode()
        if cache not in _ciphers:
            _ciphers[cache] = fernet.Fernet(material)
        return _ciphers[cache]


def seal(key: Key, data_dir: Path) -> str:
    return str(_cipher(data_dir).encrypt(key.reveal().encode()).decode())


def unseal(token: str, data_dir: Path) -> Key:
    cipher = _cipher(data_dir)
    try:
        return Key(cipher.decrypt(token.encode()).decode())
    except (_fernet().InvalidToken, ValueError) as exc:
        raise Unreadable("saved under another secret key") from exc
