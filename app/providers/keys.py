"""API keys: held so that nothing prints them.

A `Key` is a string that will not say what it is. Its `repr` and `str` are the
masked form (`sk-…abcd`), so a key that ends up in a log line, a traceback, an
f-string or `repr(settings)` shows only that. The real value comes out through
`reveal()`, which only the request that sends it calls.

Keys come from the environment as `NAME`, or `NAME_FILE` naming a file that
holds it (Docker secrets: `/run/secrets/groq`). An admin's or a user's key comes
from the database instead, encrypted at rest (app/ai/secrets.py).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

SHOWN = 4  # trailing characters a masked key keeps


def mask(value: str) -> str:
    """`sk-…abcd`: the prefix up to its first dash or underscore, and the end.
    A key too short to have both shows as `…`."""
    if len(value) < 12:
        return "…"
    cut = min((i for i in (value.find("-"), value.find("_")) if 0 < i < 6), default=0)
    head = value[: cut + 1] if cut else ""
    return f"{head}…{value[-SHOWN:]}"


class Key:
    """A secret. Compares and hashes by value; prints masked."""

    __slots__ = ("_value",)

    def __init__(self, value: str):
        value = value.strip()
        if not value:
            raise ValueError("an empty API key")
        self._value = value

    def reveal(self) -> str:
        """The key itself, for the Authorization header and nothing else."""
        return self._value

    @property
    def masked(self) -> str:
        return mask(self._value)

    def scrub(self, text: str) -> str:
        """`text` with the key, wherever it appears, masked: for a provider's
        error message that echoes what was sent."""
        return text.replace(self._value, self.masked)

    def __repr__(self) -> str:
        return f"Key({self.masked})"

    __str__ = __repr__

    def __format__(self, spec: str) -> str:
        return repr(self)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Key) and other._value == self._value

    def __hash__(self) -> int:
        return hash(("Key", self._value))

    def __reduce__(self) -> str | tuple[Any, ...]:
        raise TypeError("a Key is not pickled")


def from_env(name: str, env: dict[str, str] | None = None) -> Key | None:
    """`name` from the environment, or the file `name_FILE` names. None when
    neither is set. A `_FILE` that cannot be read is an error, not None: a
    secret mounted at the wrong path should stop the server, not pass quietly."""
    env = os.environ if env is None else env  # type: ignore[assignment]
    assert env is not None
    value = env.get(name, "").strip()
    if value:
        return Key(value)
    path = env.get(f"{name}_FILE", "").strip()
    if not path:
        return None
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError(f"{name}_FILE: cannot read {path}: {exc.strerror}") from None
    if not text:
        raise ValueError(f"{name}_FILE: {path} is empty")
    return Key(text)
