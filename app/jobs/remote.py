"""A worker on another machine: the job source over HTTP, and the stages run
on what it is sent (#35).

`HttpJobs` is the `JobSource` the worker loop takes in place of `LocalJobs`
when given `--server`: every claim, release and check-in is a request to the
server, which does the same with its own `LocalJobs`. `RemoteRunners` runs a
stage on the rows a claim brought, downloads the media files those rows name
into a cache, and sends one result per reel back, which the server records
and releases at once. So when the loop then releases the batch, `HttpJobs`
hands back what the server said rather than asking again: asking twice would
count a failure twice.

The server unreachable is an OSError, which the loop waits out; the server
refusing (a wrong token, another release's stage version) is `Refused`, which
stops the worker: retrying cannot fix it.

Stdlib only: urllib, not a client library.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.db import Row

from . import protocol
from .queue import Released
from .worker import Runners, Unavailable

log = logging.getLogger("hikidashi.worker")

TIMEOUT = 60.0  # seconds for one request; a video download gets DOWNLOAD
DOWNLOAD = 600.0
CACHE_MAX = 2 << 30  # bytes of downloaded media kept, newest used first

# (method, path, JSON body or None) -> (status, body). Swapped in tests.
Send = Callable[[str, str, dict[str, Any] | None], tuple[int, bytes]]
# (path, destination file) -> None, raising Missing for a file not there.
Fetch = Callable[[str, Path], None]


class Refused(Exception):
    """The server will not have it: a wrong token, remote workers off, or a
    different release. Not an OSError, so the loop does not wait it out."""


class Missing(Exception):
    """A media file the server no longer has: that reel fails, the batch
    goes on."""


def _detail(raw: bytes) -> str:
    try:
        detail = json.loads(raw).get("detail")
    except (ValueError, AttributeError):
        detail = None
    return str(detail) if detail else raw.decode(errors="replace")[:200]


class HttpJobs:
    """Claims and reports through the server's /api/worker routes."""

    def __init__(
        self,
        server: str,
        token: str,
        name: str,
        *,
        send: Send | None = None,
        fetch: Fetch | None = None,
    ):
        self.server = server.rstrip("/")
        self.token = token
        self.name = name
        self._send = send or self._urlopen
        self._fetch = fetch or self._download
        self._rows: dict[str, dict[str, dict[str, Any]]] = {}
        self._reported: dict[str, Released] = {}

    # --- transport -----------------------------------------------------------

    def _request(self, path: str, data: bytes | None, method: str) -> urllib.request.Request:
        headers = {"Authorization": f"Bearer {self.token}"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        return urllib.request.Request(self.server + path, data=data, headers=headers, method=method)

    def _urlopen(self, method: str, path: str, body: dict[str, Any] | None) -> tuple[int, bytes]:
        data = None if body is None else json.dumps(body).encode()
        try:
            with urllib.request.urlopen(self._request(path, data, method), timeout=TIMEOUT) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def _download(self, path: str, dest: Path) -> None:
        url = "/api/worker/media/" + urllib.parse.quote(path)
        part = dest.with_name(dest.name + ".part")
        try:
            with (
                urllib.request.urlopen(self._request(url, None, "GET"), timeout=DOWNLOAD) as r,
                part.open("wb") as out,
            ):
                shutil.copyfileobj(r, out)
        except urllib.error.HTTPError as exc:
            part.unlink(missing_ok=True)
            if exc.code == 404:
                raise Missing(f"the server has no {path}") from exc
            raise self._refused(exc.code, exc.read()) from exc
        except BaseException:
            part.unlink(missing_ok=True)
            raise
        part.replace(dest)

    def _refused(self, status: int, raw: bytes) -> Exception:
        if status >= 500:  # the server's trouble, likely passing
            return OSError(f"server error {status}: {_detail(raw)}")
        return Refused(f"{status}: {_detail(raw)}")

    def _post(self, route: str, **body: Any) -> Any:
        status, raw = self._send(
            "POST",
            f"/api/worker/{route}",
            {
                "worker": self.name,
                "protocol": protocol.PROTOCOL,
                "versions": protocol.versions(),
                **body,
            },
        )
        if status != 200:
            raise self._refused(status, raw)
        return json.loads(raw)

    # --- JobSource -------------------------------------------------------------

    def claim(self, stage: str, limit: int) -> list[str]:
        jobs = self._post("claim", stage=stage, limit=limit)["jobs"]
        self._rows[stage] = {j["shortcode"]: j for j in jobs}
        return [j["shortcode"] for j in jobs]

    def rows(self, stage: str, codes: list[str]) -> list[dict[str, Any]]:
        """The rows the last claim for `stage` brought, for `codes`."""
        mine = self._rows.get(stage, {})
        return [mine[c] for c in codes if c in mine]

    def report(self, stage: str, profile: str, results: list[dict[str, Any]]) -> Released:
        """Send a batch's results; the server records them and releases those reels."""
        out = self._post("results", stage=stage, profile=profile, results=results)
        got = Released(done=out["done"], again=out["again"], failed=out["failed"])
        self._reported[stage] = got
        return got

    def release(self, stage: str, shortcodes: list[str], *, count: bool = True) -> Released:
        reported = self._reported.pop(stage, None)
        if count and reported is not None:
            return reported  # released with its results already
        out = self._post("release", stage=stage, shortcodes=shortcodes, count=count)
        return Released(done=out["done"], again=out["again"], failed=out["failed"])

    def renew(self) -> None:
        pass  # the heartbeat's check-in renews: one request, not two

    def reclaim(self) -> int:
        taken: int = self._post("reclaim")["taken"]
        return taken

    def check_in(
        self,
        *,
        stages: tuple[str, ...],
        started_at: float,
        doing: str | None = None,
        problems: dict[str, str] | None = None,
        renew: bool = True,
    ) -> None:
        self._post(
            "check-in",
            stages=list(stages),
            started_at=started_at,
            doing=doing,
            problems=problems or None,
            pid=os.getpid(),
            renew=renew,
        )

    def check_out(self) -> None:
        self._post("check-out")

    def fetch(self, path: str, dest: Path) -> None:
        self._fetch(path, dest)


@dataclass
class Batch:
    """How a batch went, for the worker's log line."""

    total: int = 0
    done: int = 0
    nothing: int = 0  # finished with nothing to show: no speech, no text, no recipe
    failed: int = 0
    downloaded: int = 0  # bytes

    def line(self) -> str:
        mb = f"  downloaded {self.downloaded / 1e6:,.1f} MB" if self.downloaded else ""
        return (
            f"{self.done + self.nothing + self.failed}/{self.total}  done {self.done}"
            f"  nothing {self.nothing}  failed {self.failed}{mb}"
        )


@dataclass
class RemoteRunners(Runners):
    """The stages run on a claim's rows, with the media downloaded into
    `media_dir`, which is the cache here; results go back to the server."""

    jobs: HttpJobs | None = None
    cache_max: int = CACHE_MAX
    _results: list[dict[str, Any]] = field(default_factory=list)

    def run(self, stage: str, conn: sqlite3.Connection | None, codes: list[str]) -> str:
        if stage not in protocol.REMOTE:
            raise Unavailable(f"{stage} runs on the server, not on a remote worker")
        line = super().run(stage, conn, codes)
        self.prune()
        return line

    def _source(self) -> HttpJobs:
        assert self.jobs is not None
        return self.jobs

    def _media(self, stage: str, rows: list[dict[str, Any]], batch: Batch) -> list[Row]:
        """Download what each row needs that the cache does not have. A row
        whose file the server no longer has fails here; the rest go on."""
        ready: list[Row] = []
        for row in rows:
            try:
                for path in protocol.media(row, stage):
                    dest = self.media_dir / path
                    if not dest.resolve().is_relative_to(self.media_dir.resolve()):
                        raise Missing(f"refused a path outside the cache: {path}")
                    if dest.is_file():
                        dest.touch()  # newest used last to go
                        continue
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    self._source().fetch(path, dest)
                    batch.downloaded += dest.stat().st_size
            except Missing as exc:
                self._results.append(protocol.failed(row["shortcode"], str(exc)))
                batch.failed += 1
            else:
                ready.append(row)
        return ready

    def _send(self, stage: str, profile: str) -> None:
        results, self._results = self._results, []
        if results:
            self._source().report(stage, profile, results)

    def _each(
        self,
        stage: str,
        codes: list[str],
        profile: str,
        work: Callable[[Row], tuple[Any, str | None]],
        *,
        workers: int = 1,
        stops: tuple[type[BaseException], ...] = (),
    ) -> Batch:
        """Run `work` on each claimed reel, `workers` at a time, and send the
        results. An exception in `stops` (the model server gone) ends the
        batch, sending what was finished first."""
        rows = self._source().rows(stage, codes)
        batch = Batch(total=len(rows))
        ready = self._media(stage, rows, batch)

        lock = threading.Lock()

        def one(row: Row) -> None:
            produced, why = work(row)
            with lock:
                if why is not None:
                    self._results.append(protocol.failed(row["shortcode"], why))
                    batch.failed += 1
                else:
                    self._results.append(protocol.ok(stage, row["shortcode"], produced))
                    if produced:
                        batch.done += 1
                    else:
                        batch.nothing += 1

        try:
            with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
                for f in [pool.submit(one, row) for row in ready]:
                    f.result()
        except stops as exc:
            self._send(stage, profile)
            raise Unavailable(str(exc)) from exc
        except BaseException:
            self._results = []
            raise
        self._send(stage, profile)
        return batch

    def _asr(self, conn: sqlite3.Connection | None, codes: list[str]) -> Any:
        from app.asr.worker import transcribe_one

        profile, model = self._whisper()
        return self._each(
            "asr",
            codes,
            profile.name,
            lambda row: transcribe_one(row, self.media_dir, model),
            workers=profile.asr_workers,
        )

    def _ocr(self, conn: sqlite3.Connection | None, codes: list[str]) -> Any:
        from app.ocr.reader import OcrError, OcrUnavailable
        from app.profiles import cpus

        reader = self._reader()

        def work(row: Row) -> tuple[Any, str | None]:
            try:
                return reader(row, self.media_dir), None
            except OcrError as exc:
                return None, str(exc)

        return self._each(
            "ocr",
            codes,
            self._profile().name,
            work,
            workers=min(8, cpus()),
            stops=(OcrUnavailable,),
        )

    def _tag(self, conn: sqlite3.Connection | None, codes: list[str]) -> Any:
        from app.ollama import OllamaError
        from app.tagging import llm
        from app.tagging.worker import tag_one

        model = self._tagger()

        def work(row: Row) -> tuple[Any, str | None]:
            try:
                return tag_one(row, model), None
            except llm.TagError as exc:
                return None, str(exc)

        try:
            return self._each("tag", codes, self._profile().name, work, stops=(OllamaError,))
        except Unavailable:
            self._drop("tagger")  # look again next time, it may be back
            raise

    def _recipe(self, conn: sqlite3.Connection | None, codes: list[str]) -> Any:
        from app.ollama import OllamaError
        from app.recipes import llm
        from app.recipes.worker import candidate, read_one

        model = self._recipes()

        def work(row: Row) -> tuple[Any, str | None]:
            if not candidate(row):
                return None, None
            try:
                return read_one(row, model), None
            except llm.RecipeError as exc:
                return None, str(exc)

        try:
            return self._each("recipe", codes, self._profile().name, work, stops=(OllamaError,))
        except Unavailable:
            self._drop("recipes")
            raise

    def prune(self) -> int:
        """Delete the least recently used media past `cache_max`. Returns bytes freed."""
        files = [
            (f.stat().st_mtime, f.stat().st_size, f)
            for f in self.media_dir.rglob("*")
            if f.is_file() and not f.name.endswith(".part")
        ]
        total = sum(size for _, size, _ in files)
        freed = 0
        for _, size, f in sorted(files, key=lambda t: t[0]):
            if total - freed <= self.cache_max:
                break
            f.unlink(missing_ok=True)
            freed += size
        return freed


def wait_for(jobs: HttpJobs, *, sleep: Callable[[float], None] = time.sleep) -> None:
    """At start: wait for the server, rather than fail, if it is not up yet;
    stop at once if it refuses."""
    wait = 5.0
    while True:
        try:
            jobs.reclaim()
            return
        except OSError as exc:
            log.warning("server %s unreachable, trying again in %.0fs: %s", jobs.server, wait, exc)
            sleep(wait)
            wait = min(wait * 2, 60.0)
