"""Settings, resolved once from the environment."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from pathlib import Path

from app.jobs import parse_stages

ROOT = Path(__file__).resolve().parent.parent


AUTH_MODES = ("none", "local", "proxy")


def _auth(mode: str) -> str:
    mode = mode.strip().lower()
    if mode not in AUTH_MODES:
        raise ValueError(f"HIKIDASHI_AUTH is {mode!r}; use one of {', '.join(AUTH_MODES)}")
    return mode


OSM_TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
OSM_CREDIT = (
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
)


def _geocoder(value: str) -> str | None:
    """A Nominatim URL; `public` for OpenStreetMap's own, by explicit choice."""
    value = value.strip()
    if value.lower() == "public":
        return "https://nominatim.openstreetmap.org"
    return value.rstrip("/") or None


def _stages(value: str) -> tuple[str, ...]:
    try:
        return parse_stages(value)
    except ValueError as exc:
        raise ValueError(f"HIKIDASHI_LOCAL_STAGES: {exc}") from None


def _path(env: str, default: str) -> Path:
    p = Path(os.environ.get(env, default))
    return p if p.is_absolute() else ROOT / p


@dataclass(frozen=True)
class Settings:
    db_path: Path
    media_dir: Path
    imports_dir: Path
    models_dir: Path
    logs_dir: Path
    run_dir: Path
    profile: str
    ollama_url: str
    host: str
    port: int
    # none: everyone is the library's owner, as before accounts. local: sign
    # in with a username and password. proxy: a proxy in front (Authelia,
    # Tailscale, Cloudflare Access) has signed people in, and says who in a header.
    auth: str
    # Where browsers reach the server, as https://reels.example.com, when a
    # proxy in front changes the Host it sees. Also makes the cookie Secure.
    origin: str | None
    # proxy mode: the header naming the user, the addresses it is believed
    # from, and whether a name with no account gets one.
    proxy_header: str
    trusted_proxies: tuple[str, ...]
    proxy_creates: bool
    # Where places are looked up: a Nominatim server's address, or None to
    # look nothing up. Captions leave the machine only when this is set.
    geocoder: str | None
    geocoder_delay: float  # seconds between lookups; the public server asks 1
    locate_every: float  # the server's locator looks for new places this often; 0: off
    # Map tiles, as a {z}/{x}/{y} URL template, and the credit they require.
    # The browser fetches them, so they too leave the machine.
    tiles: str
    tiles_attribution: str
    # The always-on worker (app/jobs): the stages it runs on this host, all,
    # none or a list; its name in status, which must differ between workers
    # sharing a database; and how long a model it loaded stays loaded with no
    # work for it.
    local_stages: tuple[str, ...]
    worker_name: str
    unload_after: float
    # Remote workers (#35). On the server: the token a worker on another
    # machine must present, or None to refuse them all. On that machine: the
    # server it works for (`hikidashi worker --server`), and where it keeps
    # the media files it downloads.
    # Out of the repr, so no traceback or log line that prints settings shows it.
    worker_token: str | None = field(repr=False)
    server: str | None
    cache_dir: Path

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            db_path=_path("HIKIDASHI_DB", "data/hikidashi.sqlite3"),
            media_dir=_path("HIKIDASHI_MEDIA", "data/media"),
            imports_dir=_path("HIKIDASHI_IMPORTS", "data/imports"),
            models_dir=_path("HIKIDASHI_MODELS", "data/models"),
            logs_dir=_path("HIKIDASHI_LOGS", "data/logs"),
            # Pidfile and pipeline lock. Shared with ./hikidashi, which runs on
            # the host, so it sits next to the data rather than in /run.
            run_dir=_path("HIKIDASHI_RUN", "data/run"),
            profile=os.environ.get("HIKIDASHI_PROFILE", "auto"),
            ollama_url=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
            host=os.environ.get("HIKIDASHI_HOST", "0.0.0.0"),
            port=int(os.environ.get("HIKIDASHI_PORT", "8080")),
            auth=_auth(os.environ.get("HIKIDASHI_AUTH", "none")),
            origin=os.environ.get("HIKIDASHI_ORIGIN") or None,
            proxy_header=os.environ.get("HIKIDASHI_PROXY_HEADER", "Remote-User"),
            trusted_proxies=tuple(
                n.strip()
                for n in os.environ.get("HIKIDASHI_TRUSTED_PROXIES", "127.0.0.1/32,::1/128").split(
                    ","
                )
                if n.strip()
            ),
            proxy_creates=os.environ.get("HIKIDASHI_PROXY_CREATES", "") in ("1", "true", "yes"),
            geocoder=_geocoder(os.environ.get("HIKIDASHI_GEOCODER", "")),
            geocoder_delay=float(os.environ.get("HIKIDASHI_GEOCODER_DELAY") or "1"),
            locate_every=float(os.environ.get("HIKIDASHI_LOCATE_EVERY") or "300"),
            tiles=os.environ.get("HIKIDASHI_TILES") or OSM_TILES,
            tiles_attribution=os.environ.get("HIKIDASHI_TILES_ATTRIBUTION")
            or (OSM_CREDIT if not os.environ.get("HIKIDASHI_TILES") else ""),
            local_stages=_stages(os.environ.get("HIKIDASHI_LOCAL_STAGES", "all")),
            worker_name=os.environ.get("HIKIDASHI_WORKER_NAME") or socket.gethostname(),
            unload_after=float(os.environ.get("HIKIDASHI_UNLOAD_AFTER") or "300"),
            worker_token=os.environ.get("HIKIDASHI_WORKER_TOKEN") or None,
            server=os.environ.get("HIKIDASHI_SERVER") or None,
            cache_dir=_path("HIKIDASHI_CACHE", "data/cache"),
        )

    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.imports_dir.mkdir(parents=True, exist_ok=True)
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.run_dir.mkdir(parents=True, exist_ok=True)


settings = Settings.from_env()
