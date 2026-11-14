"""The server's own log, kept where the web UI can read it back.

uvicorn logs to stdout, which Docker keeps and the app cannot see. `install`
adds a rotating file next to it, fed by the same loggers, and `tail` reads the
end of that file for the /logs page.

Stdlib only. app/logship sends the same lines to syslog and Loki, when set.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import socket
from collections.abc import Mapping
from pathlib import Path

FILE = "server.log"
WORKER_FILE = "worker.log"  # app/jobs writes its own, the same way
# Each process on one log folder needs a file of its own: two rotating
# handlers on one file each rotate it, and one keeps writing into the file the
# other renamed. Under Docker the server-worker shares /data with the worker
# (#86), and a stage run by hand for a server may run beside a worker.
SERVER_WORKER_FILE = "server-worker.log"
ONCE_FILE = "worker-once.log"
WORKER_FILES = {"worker": WORKER_FILE, "server-worker": SERVER_WORKER_FILE}
MAX_BYTES = 1_000_000
BACKUPS = 3
FORMAT = "%(asctime)s %(levelname)-7s %(name)s  %(message)s"

# uvicorn's own loggers do not propagate to the root, so each gets the handler.
LOGGERS = ("", "uvicorn", "uvicorn.access")


# Requests not worth a line in the file. They still reach stdout.
#   /api/logs   the log page's own polling: reading the log would write to it,
#               every few seconds, forever
#   /media/     one per thumbnail, sixty per page of the grid
QUIET_PATHS = ("/api/logs", "/media/")


# Not logged anywhere, stdout included: a collector's scrape and a health
# check, every few seconds, forever (#149).
SCRAPES = ("/metrics", "/healthz", "/readyz")


class _SkipScrapes(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        return not (isinstance(args, tuple) and len(args) >= 3 and str(args[2]).startswith(SCRAPES))


class _SkipNoise(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if record.name == "uvicorn.access" and isinstance(args, tuple) and len(args) >= 3:
            return not str(args[2]).startswith(QUIET_PATHS)
        return True


def install(log_dir: Path, file: str = FILE) -> Path:
    """Start writing the server's log, or `file`, to `log_dir`. Safe to call twice."""
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / file
    # One handler shared by every logger: two rotating handlers on one file
    # each rotate it, and one keeps writing into the file the other renamed.
    handler = None
    for name in LOGGERS:
        handler = handler or next(
            (
                h
                for h in logging.getLogger(name).handlers
                if getattr(h, "baseFilename", None) == str(path)
            ),
            None,
        )
    if handler is None:
        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter(FORMAT))
        handler.addFilter(_SkipNoise())
    for name in LOGGERS:
        logger = logging.getLogger(name)
        if handler not in logger.handlers:  # --reload imports the app again
            logger.addHandler(handler)
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _SkipScrapes) for f in access.filters):
        access.addFilter(_SkipScrapes())
    root = logging.getLogger()
    # The root defaults to WARNING; a library's INFO is worth keeping.
    root.setLevel(min(root.level or logging.INFO, logging.INFO))
    # And to syslog and Loki, when they are set (#149). This file's process
    # name labels the lines: server, worker, server-worker, worker-once.
    global _process
    _process = Path(file).stem
    ship()
    return path


_process = Path(FILE).stem


def ship(env: Mapping[str, str] | None = None) -> list[str]:
    """Send the log to syslog and Loki as `env` says (the environment by
    default), replacing what was set up before: app/monitoring.py calls this
    with the Account page's settings."""
    from app import logship

    worker = os.environ.get("HIKIDASHI_WORKER_NAME") or socket.gethostname()
    return logship.install(_process, worker, LOGGERS, _SkipNoise(), env)


def tail(path: Path, lines: int = 500, *, chunk: int = 64 * 1024) -> list[str]:
    """The last `lines` lines of a file, oldest first. [] if there is none."""
    try:
        f = path.open("rb")
    except FileNotFoundError:
        return []
    with f:
        f.seek(0, 2)
        end = pos = f.tell()
        data = b""
        # Read backwards until there are enough newlines, not the whole file.
        while pos > 0 and data.count(b"\n") <= lines:
            pos = max(0, pos - chunk)
            f.seek(pos)
            data = f.read(end - pos)
    text = data.decode("utf-8", "replace").splitlines()
    if pos > 0:
        text = text[1:]  # the first line is probably partial
    return text[-lines:] if lines else []
