"""The worker: every stage, run on new reels as they arrive.

A round goes through the stages in pipeline order and, for each, claims a
batch of the reels ready for it, runs the stage on exactly those, and gives
them back. A reel imported now is fetched in one round, transcribed in a later
one, and so on down, since each stage is offered a reel only once the stages
before it are done with it.

When a round finds nothing, the worker waits before the next: a few seconds,
doubling to a minute while the queue stays empty. An import wakes it at once
(see app.jobs.wake). Whisper and the embedder are loaded on first use and
dropped after a stretch with no work for them, since each holds a gigabyte or
more; Ollama unloads its own models.

A stage that cannot run here at all, like the tagger with no Ollama or ASR in
an image without faster-whisper, gives its reels back uncounted, is tried again
after a minute, then longer each time up to an hour, and is reported as a
problem at every check-in until it runs.

A clean stop (SIGTERM, Ctrl-C) gives every lease back uncounted. Finished
reels are already committed, as with the stages run by hand.
"""

from __future__ import annotations

import gc
import logging
import shutil
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.asr.worker import Transcribe
from app.db import Row
from app.ollama import OllamaError
from app.profiles import Profile, cpus, resolve

from . import STAGES, woken_at
from .queue import HEARTBEAT, JobSource, Released

log = logging.getLogger("hikidashi.worker")

# Reels claimed at once. Small, so a fresh import moves down the stages a few
# at a time rather than waiting for all of it to be fetched first; enough to
# keep each stage's own parallelism busy.
BATCH = {"enrich": 6, "asr": 4, "ocr": 16, "tag": 8, "recipe": 8, "embed": 32}

IDLE = (5.0, 60.0)  # first and longest wait between empty rounds, seconds
BACKOFF = (60.0, 3600.0)  # the same for a stage that could not run
UNLOAD_AFTER = 300.0  # seconds without work before a model is dropped


class Unavailable(Exception):
    """This stage cannot run here now: no Ollama, no tesseract, a model that
    will not load. Not the reels' fault, so nothing is counted against them."""


class Embed(Protocol):
    def documents(self, texts: list[str]) -> list[bytes]: ...


@dataclass
class _Loaded:
    model: Any
    used: float


@dataclass
class Runners:
    """How each stage runs on a batch here. The loaders are swapped in tests;
    by default they load the real models, which is what makes this module
    importable with none of them installed."""

    media_dir: Path
    models_dir: Path
    ollama_url: str
    profile: str
    unload_after: float = UNLOAD_AFTER
    clock: Callable[[], float] = time.monotonic
    whisper: Callable[[Profile, Path], Callable[[bytes], str | None]] | None = None
    embedder: Callable[[Path], Embed] | None = None
    tagger: Callable[[str, str], Any] | None = None
    recipe_model: Callable[[str, str], Any] | None = None
    ocr_reader: Callable[[Row, Path], str | None] | None = None
    which: Callable[[str], str | None] = shutil.which
    _loaded: dict[str, _Loaded] = field(default_factory=dict)

    def _profile(self) -> Profile:
        try:
            return resolve(self.profile)
        except ValueError as exc:
            raise Unavailable(str(exc)) from exc

    def _get(self, key: str, load: Callable[[], Any]) -> Any:
        if key not in self._loaded:
            self._loaded[key] = _Loaded(load(), self.clock())
        got = self._loaded[key]
        got.used = self.clock()
        return got.model

    def _drop(self, key: str) -> None:
        self._loaded.pop(key, None)

    def unload_idle(self) -> list[str]:
        """Drop the models not used for `unload_after`. Returns their names."""
        now = self.clock()
        idle = [k for k, v in self._loaded.items() if now - v.used >= self.unload_after]
        for k in idle:
            del self._loaded[k]
        if idle:
            gc.collect()
        return idle

    def loaded(self) -> list[str]:
        return list(self._loaded)

    def run(self, stage: str, conn: sqlite3.Connection | None, codes: list[str]) -> str:
        """Run `stage` on `codes`. Returns its progress line."""
        line: str = getattr(self, f"_{stage}")(conn, codes).line()
        return line

    def _enrich(self, conn: sqlite3.Connection | None, codes: list[str]) -> Any:
        from app.enrich.worker import enrich

        assert conn is not None
        # Not in the worker image. Left to the subprocess, it would be an
        # OSError, which reads as the server being away.
        self._need("yt-dlp", "ffprobe")
        return enrich(conn, self.media_dir, with_media=True, shortcodes=codes)

    def _whisper(self) -> tuple[Profile, Callable[[bytes], str | None]]:
        """The profile, and Whisper loaded for it."""
        profile = self._profile()

        def load() -> Transcribe:
            if self.whisper:
                return self.whisper(profile, self.models_dir)
            from app.asr.transcriber import Whisper

            log.info("loading %s for transcription", profile.asr_model)
            try:
                return Whisper(profile, self.models_dir)
            except ImportError as exc:  # the server image has no faster-whisper
                raise Unavailable(f"not installed: {exc.name or exc}") from exc
            except RuntimeError as exc:
                raise Unavailable(f"{profile.asr_model} will not load: {exc}") from exc

        return profile, self._get("whisper", load)

    def _asr(self, conn: sqlite3.Connection | None, codes: list[str]) -> Any:
        from app.asr.worker import transcribe_all

        assert conn is not None
        profile, model = self._whisper()
        return transcribe_all(
            conn,
            self.media_dir,
            model,
            profile=profile.name,
            workers=profile.asr_workers,
            shortcodes=codes,
        )

    def _reader(self) -> Callable[[Row, Path], str | None]:
        """OCR's per-reel reader, once tesseract and ffmpeg are here."""
        from app.ocr.worker import read

        if self.ocr_reader is not None:
            return self.ocr_reader
        self._need("tesseract", "ffmpeg")
        return read

    def _need(self, *tools: str) -> None:
        missing = [t for t in tools if not self.which(t)]
        if missing:
            raise Unavailable(f"not installed: {', '.join(missing)}")

    def _ocr(self, conn: sqlite3.Connection | None, codes: list[str]) -> Any:
        from app.ocr.reader import OcrUnavailable
        from app.ocr.worker import ocr_all

        assert conn is not None
        reader = self._reader()
        try:
            return ocr_all(
                conn,
                self.media_dir,
                workers=min(8, cpus()),
                reader=reader,
                shortcodes=codes,
            )
        except OcrUnavailable as exc:
            raise Unavailable(str(exc)) from exc

    def _ollama(self, key: str, make: Callable[[str, str], Any] | None, default: Any) -> Any:
        """One stage's model on Ollama, pulled on first use."""
        model_name = self._profile().tag_model

        def load() -> Any:
            if make:
                return make(self.ollama_url, model_name)
            client = default(self.ollama_url, model_name)
            try:
                if not client.has_model():
                    log.info("pulling %s (once; several GB)", model_name)
                    client.pull()
            except OllamaError as exc:
                raise Unavailable(str(exc)) from exc
            return client

        return self._get(key, load)

    def _tagger(self) -> Any:
        from app.tagging import llm

        return self._ollama("tagger", self.tagger, llm.Ollama)

    def _recipes(self) -> Any:
        from app.recipes import llm

        return self._ollama("recipes", self.recipe_model, llm.Ollama)

    def _tag(self, conn: sqlite3.Connection | None, codes: list[str]) -> Any:
        from app.tagging.worker import tag_all

        assert conn is not None
        model = self._tagger()
        try:
            return tag_all(conn, model, profile=self._profile().name, shortcodes=codes)
        except OllamaError as exc:
            self._drop("tagger")  # look again next time, it may be back
            raise Unavailable(str(exc)) from exc

    def _recipe(self, conn: sqlite3.Connection | None, codes: list[str]) -> Any:
        from app.recipes.worker import read_all

        assert conn is not None
        model = self._recipes()
        try:
            return read_all(conn, model, profile=self._profile().name, shortcodes=codes)
        except OllamaError as exc:
            self._drop("recipes")
            raise Unavailable(str(exc)) from exc

    def _embed(self, conn: sqlite3.Connection | None, codes: list[str]) -> Any:
        from app import dupes
        from app.embed.worker import embed_all

        assert conn is not None
        # First, and without the model: a fingerprint needs only ffmpeg.
        dupes.hash_all(conn, self.media_dir, codes)

        def load() -> Embed:
            if self.embedder:
                return self.embedder(self.models_dir)
            from app.embed.embedder import MODEL, Embedder

            log.info("loading %s for semantic search", MODEL)
            try:
                return Embedder(self.models_dir)
            except ImportError as exc:  # the worker image has no fastembed
                raise Unavailable(f"not installed: {exc.name or exc}") from exc
            except Exception as exc:
                raise Unavailable(f"the embedder will not load: {exc}") from exc

        model: Embed = self._get("embedder", load)
        return embed_all(conn, model.documents, shortcodes=codes)


class Stop(BaseException):
    """SIGTERM: stop cleanly. A BaseException, like KeyboardInterrupt, so no
    stage's `except Exception` swallows it."""


class Worker:
    def __init__(
        self,
        jobs: JobSource,
        conn: sqlite3.Connection | None,
        runners: Runners,
        *,
        stages: tuple[str, ...],
        run_dir: Path,
        beat: JobSource | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        idle: tuple[float, float] = IDLE,
        backoff: tuple[float, float] = BACKOFF,
        heartbeat: float = HEARTBEAT,
        batch: dict[str, int] | None = None,
    ):
        self.jobs = jobs
        self.conn = conn
        self.runners = runners
        self.stages = tuple(s for s in STAGES if s in stages)
        self.run_dir = run_dir
        # Its own source on its own connection: the heartbeat writes while the
        # main thread may be in the middle of a transaction.
        self.beat = beat
        self.clock = clock
        self.sleep = sleep
        self.idle = idle
        self.backoff = backoff
        self.heartbeat = heartbeat
        self.batch = {**BATCH, **(batch or {})}
        self.started_at = time.time()
        self.doing: str | None = None
        self.problems: dict[str, str] = {}
        self._failures: dict[str, int] = {}
        self._retry_at: dict[str, float] = {}
        self._woken = woken_at(run_dir)
        self._stopped = threading.Event()
        self._away = False  # the server could not be reached last time

    # --- one round ---------------------------------------------------------

    def round(self) -> int:
        """Claim and run one batch for each stage. Returns how many reels
        were worked on."""
        worked = 0
        # A worker on another machine has no database: the server runs the
        # hashtag pass when it is asked for reels to tag.
        if "tag" in self.stages and self.conn is not None:
            self._hashtags()
        for stage in self.stages:
            if self.clock() < self._retry_at.get(stage, 0):
                continue
            try:
                codes = self.jobs.claim(stage, self.batch[stage])
            except OSError as exc:  # a remote worker's server is away
                self._offline(exc)
                break
            if codes and self._run(stage, codes):
                worked += len(codes)
        for name in self.runners.unload_idle():
            log.info("unloaded %s after %.0fs with no work", name, self.runners.unload_after)
        return worked

    def _offline(self, exc: OSError) -> None:
        """The server cannot be reached: wait for the next round, quietly
        after the first time. Leases held meanwhile run out, as for a worker
        that died."""
        if not self._away:
            log.warning("server unreachable, waiting: %s", exc)
        self._away = True

    def _hashtags(self) -> None:
        """The hashtag pass: offline, instant, and needs no lease."""
        from app.tagging.service import apply_hashtags

        assert self.conn is not None
        stats = apply_hashtags(self.conn)
        if stats.items:
            log.info("tag: hashtags read on %d reels", stats.items)

    def _run(self, stage: str, codes: list[str]) -> bool:
        """Run one batch and give it back. False if the stage could not run."""
        if self._away:
            log.info("server reachable again")
            self._away = False
        self.doing = stage
        try:
            self._check_in()
            line = self.runners.run(stage, self.conn, codes)
        except Unavailable as exc:
            self._release(stage, codes, count=False)
            self._unavailable(stage, str(exc))
            return False
        except OSError as exc:  # the server went away mid-batch
            self._offline(exc)
            return False
        except Exception:
            log.exception("%s: failed on %s", stage, ", ".join(codes))
            self._release(stage, codes)
            return True
        finally:
            self.doing = None
        self._release(stage, codes)
        log.info("%s: %s", stage, line)
        if self.problems.pop(stage, None) is not None:
            log.info("%s: running again", stage)
        self._failures.pop(stage, None)
        self._retry_at.pop(stage, None)
        try:
            self._check_in()
        except OSError as exc:
            self._offline(exc)
        return True

    def _release(self, stage: str, codes: list[str], *, count: bool = True) -> None:
        try:
            out = self.jobs.release(stage, codes, count=count)
        except OSError as exc:  # its leases run out instead
            self._offline(exc)
            return
        self._report(stage, out)

    def _report(self, stage: str, out: Released) -> None:
        for code in out.failed:
            log.warning("%s: gave up on %s after repeated failures", stage, code)

    def _unavailable(self, stage: str, why: str) -> None:
        n = self._failures[stage] = self._failures.get(stage, 0) + 1
        wait = min(self.backoff[0] * 2 ** (n - 1), self.backoff[1])
        self._retry_at[stage] = self.clock() + wait
        if self.problems.get(stage) != why:
            log.warning("%s: cannot run: %s; trying again in %.0fs", stage, why, wait)
        self.problems[stage] = why
        try:
            self._check_in()
        except OSError as exc:
            self._offline(exc)

    # --- the loop ----------------------------------------------------------

    def _check_in(self) -> None:
        self.jobs.check_in(
            stages=self.stages,
            started_at=self.started_at,
            doing=self.doing,
            problems=self.problems,
        )

    def _beat(self) -> None:
        source = self.beat
        if source is None:
            return
        while not self._stopped.wait(self.heartbeat):
            try:
                source.renew()
                source.check_in(
                    stages=self.stages,
                    started_at=self.started_at,
                    doing=self.doing,
                    problems=dict(self.problems),
                )
            # Busy past the timeout, or a remote worker's server away: next beat.
            except (sqlite3.Error, OSError) as exc:
                log.warning("check-in failed: %s", exc)

    def _wait(self, seconds: float) -> None:
        """Sleep, a second at a time, until `seconds` pass or an import wakes us."""
        end = self.clock() + seconds
        while self.clock() < end:
            woke = woken_at(self.run_dir)
            if woke > self._woken:
                self._woken = woke
                return
            self.sleep(min(1.0, max(0.0, end - self.clock())))

    def drain(self) -> int:
        """`--once`: rounds until one finds nothing ready. A stage that cannot
        run is tried once, and not waited for. Returns reels worked on."""
        total = 0
        while n := self.round():
            total += n
        return total

    def run(self) -> None:
        """Rounds until stopped, waiting longer the longer there is nothing."""
        log.info(
            "worker %s: %s",
            self.jobs.name,
            ", ".join(self.stages) if self.stages else "no stages to run here",
        )
        thread = threading.Thread(target=self._beat, name="heartbeat", daemon=True)
        thread.start()
        wait = self.idle[0]
        try:
            while True:
                if self.stages and self.round():
                    wait = self.idle[0]
                    continue
                self._wait(wait)
                wait = min(wait * 2, self.idle[1])
        finally:
            self._stopped.set()

    def start(self) -> int:
        """Check in, take back what an earlier run died holding, then work."""
        taken = self.jobs.reclaim()
        if taken:
            log.warning("took back %d reels the last run stopped holding", taken)
        self._check_in()
        return taken

    def stop(self) -> None:
        """Give every lease back, uncounted, and check out."""
        self._stopped.set()
        try:
            self.jobs.check_out()
        except Exception as exc:  # the server away or refusing: its leases run out instead
            log.warning("could not check out: %s", exc)
