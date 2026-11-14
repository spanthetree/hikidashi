"""Prometheus metrics (#149): off unless HIKIDASHI_METRICS=on, or switched
on on the Account page (app/monitoring.py), which takes without a restart.

`/metrics` on the main port, or on HIKIDASHI_METRICS_PORT instead, in the
Prometheus text format (OpenMetrics when the scraper asks for it). A scrape
needs HIKIDASHI_METRICS_TOKEN as a bearer token when one is set, and otherwise
comes from an address in HIKIDASHI_METRICS_ALLOW. A session cookie never
counts: these are the server's numbers, not a library's.

Two kinds of numbers. What happens in this process (requests, search time) is
counted as it happens, so a scrape costs nothing for it. What lives in the
database (reels by state, the pipeline, workers, hosted AI) is read by one
collector at most once every HIKIDASHI_METRICS_REFRESH seconds, on a read-only
connection of its own; the media folder's size, once an hour. The worker
containers are covered by the same scrape, since their state is in the
database too.

Labels come from bounded sets only: stages, states, route templates, worker
and provider names. Never a shortcode, URL, caption, query, username or
token, and a user's hosted API is `api="user"`, summed, unless
HIKIDASHI_METRICS_PER_USER=on. tests/test_metrics.py holds it to that.

prometheus_client is imported only when this is on.
"""

from __future__ import annotations

import contextlib
import hmac
import ipaddress
import logging
import os
import sqlite3
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI, Request, Response
    from prometheus_client import CollectorRegistry
    from prometheus_client.metrics_core import Metric

    from app.config import Settings

from app.logship import Env, on, secret

log = logging.getLogger(__name__)

PATH = "/metrics"
# Loopback and the private ranges: a collector on the LAN or the Docker network.
ALLOW = "127.0.0.0/8,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,fc00::/7"
# Search answers in milliseconds; a slow page in seconds.
BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
MEDIA_EVERY = 3600.0  # walking the media folder is the one slow read
UNMATCHED = "unmatched"  # a request no route took: one label for all of them
# Not counted, and not logged (app/logs.QUIET_PATHS): the scrapes themselves.
UNCOUNTED = (PATH, "/healthz", "/readyz")


Network = ipaddress.IPv4Network | ipaddress.IPv6Network


@dataclass(frozen=True)
class Options:
    on: bool = False
    token: str | None = field(default=None, repr=False)
    port: int | None = None
    allow: tuple[Network, ...] = ()
    refresh: float = 30.0
    per_user: bool = False

    @classmethod
    def from_env(cls, env: Env | None = None) -> Options:
        """From the environment, or `env`. ValueError names a setting that is wrong."""
        env = os.environ if env is None else env

        def number(name: str, default: float, kind: Callable[[str], Any] = float) -> Any:
            value = env.get(name) or ""
            if not value.strip():
                return default
            try:
                got = kind(value)
            except ValueError:
                raise ValueError(f"{name}: not a number: {value}") from None
            if got <= 0 or (name.endswith("_PORT") and got > 65535):
                raise ValueError(f"{name}: out of range: {value}")
            return got

        allow = []
        for n in (env.get("HIKIDASHI_METRICS_ALLOW") or ALLOW).split(","):
            if n.strip():
                try:
                    allow.append(ipaddress.ip_network(n.strip(), strict=False))
                except ValueError:
                    raise ValueError(
                        f"HIKIDASHI_METRICS_ALLOW: not an address: {n.strip()}"
                    ) from None
        return cls(
            on=on(env.get("HIKIDASHI_METRICS")),
            token=secret("HIKIDASHI_METRICS_TOKEN", env),
            port=number("HIKIDASHI_METRICS_PORT", 0, int) or None,
            allow=tuple(allow),
            refresh=number("HIKIDASHI_METRICS_REFRESH", 30.0),
            per_user=on(env.get("HIKIDASHI_METRICS_PER_USER")),
        )

    def refuse(self, authorization: str | None, address: str | None) -> int | None:
        """401 or 403 for a scrape that may not have the numbers, else None."""
        if self.token:
            given = (authorization or "").removeprefix("Bearer ").strip()
            ok = bool(given) and hmac.compare_digest(given.encode(), self.token.encode())
            return None if ok else 401
        try:
            ip = ipaddress.ip_address(address or "")
        except ValueError:
            return 403
        return None if any(ip in net for net in self.allow) else 403


def switched_off(reason: str | None) -> str:
    """Why a hosted API went off, as one of a few words: the reason itself is
    free text, which may name a provider or quote an error."""
    text = (reason or "").lower()
    for word in ("budget", "key", "settings", "admin", "error"):
        if word in text:
            return word
    return "other" if text else "none"


def fetch_reason(reason: str | None) -> str:
    """A fetch failure's label, or `other`: an unclassified one is the first
    line of yt-dlp's error, which can hold a URL."""
    from app.enrich.fetcher import PERMANENT

    known = {label for _, label in PERMANENT} | {"rate-limited", "timeout"}
    return reason if reason in known else "other"


class Library:
    """The numbers that live in the database, read at most once per refresh."""

    def __init__(self, settings: Settings, options: Options, version: str) -> None:
        self.settings = settings
        self.options = options
        self.version = version
        self.reads = 0  # how many times the database was read, for the tests
        self._lock = threading.Lock()
        self._at = -1e18
        self._families: list[Metric] = []
        self._media_at = -1e18
        self._media = 0

    def describe(self) -> list[Metric]:
        return []  # so registering it does not read the database

    def collect(self) -> Iterator[Metric]:
        with self._lock:
            now = time.monotonic()
            if now - self._at >= self.options.refresh:
                try:
                    self._families = self._read(now)
                except sqlite3.Error as exc:
                    log.warning("metrics: could not read the database: %s", exc)
                self._at = now
            families = list(self._families)
        yield from families

    def _media_size(self, now: float) -> int:
        if now - self._media_at >= MEDIA_EVERY:
            total = 0
            for root, _, files in os.walk(self.settings.media_dir):
                for f in files:
                    with contextlib.suppress(OSError):
                        total += os.stat(os.path.join(root, f)).st_size
            self._media, self._media_at = total, now
        return self._media

    def _read(self, now: float) -> list[Metric]:
        from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

        db = self.settings.db_path
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            out = self._families_from(conn, GaugeMetricFamily, CounterMetricFamily, now)
        finally:
            conn.close()
        self.reads += 1
        return out

    def _families_from(
        self, conn: sqlite3.Connection, Gauge: Any, Counter: Any, now: float
    ) -> list[Metric]:
        from app.ai import status
        from app.jobs import STAGES, queue

        def gauge(name: str, doc: str, labels: list[str], rows: Any) -> Any:
            g = Gauge(name, doc, labels=labels)
            for *values, value in rows:
                g.add_metric([str(v) for v in values], value or 0)
            return g

        out: list[Any] = []
        build = Gauge(
            "hikidashi_build_info", "The running version", labels=["version", "profile", "auth"]
        )
        build.add_metric([self.version, self.settings.profile, self.settings.auth], 1)
        out.append(build)

        # The library.
        def one(sql: str) -> Any:
            return conn.execute(sql).fetchone()[0] or 0

        out.append(
            gauge(
                "hikidashi_items",
                "Reels in the shared cache, by state",
                ["state"],
                conn.execute("SELECT state, COUNT(*) FROM item GROUP BY state"),
            )
        )
        for name, doc, sql in (
            ("hikidashi_users", "Accounts", "SELECT COUNT(*) FROM user"),
            (
                "hikidashi_collections",
                "Collections, each person's counted apart",
                "SELECT COUNT(*) FROM (SELECT DISTINCT user_id, name FROM collection)",
            ),
            ("hikidashi_recipes", "Reels with a recipe", "SELECT COUNT(*) FROM recipe"),
        ):
            out.append(gauge(name, doc, [], [(one(sql),)]))
        coverage = conn.execute(
            """SELECT SUM(media_path IS NOT NULL), SUM(transcript IS NOT NULL AND transcript != ''),
                      SUM(ocr_text IS NOT NULL AND ocr_text != ''), SUM(primary_tag IS NOT NULL),
                      (SELECT COUNT(*) FROM recipe),
                      (SELECT COUNT(DISTINCT shortcode) FROM item_place)
               FROM item"""
        ).fetchone()
        out.append(
            gauge(
                "hikidashi_items_with",
                "Reels that have each thing",
                ["field"],
                zip(
                    ("media", "transcript", "ocr", "tags", "recipe", "place"), coverage, strict=True
                ),
            )
        )
        sizes = [
            ("hikidashi_db_size_bytes", "The database file", self.settings.db_path),
            (
                "hikidashi_db_wal_size_bytes",
                "Its write-ahead log",
                Path(f"{self.settings.db_path}-wal"),
            ),
        ]
        for name, doc, path in sizes:
            out.append(gauge(name, doc, [], [(path.stat().st_size if path.exists() else 0,)]))
        out.append(
            gauge(
                "hikidashi_media_size_bytes",
                f"Media files, measured every {int(MEDIA_EVERY)} s",
                [],
                [(self._media_size(now),)],
            )
        )
        out.append(
            gauge(
                "hikidashi_import_last_timestamp_seconds",
                "When the last import finished",
                [],
                [(one("SELECT MAX(finished_at) FROM import_run"),)],
            )
        )
        imports = Counter(
            "hikidashi_imports",
            "Imports, finished or not (an upload that failed)",
            labels=["result"],
        )
        for result, n in conn.execute(
            """SELECT CASE WHEN finished_at IS NULL THEN 'unfinished' ELSE 'finished' END,
                      COUNT(*) FROM import_run GROUP BY 1"""
        ):
            imports.add_metric([result], n)
        out.append(imports)

        # The pipeline.
        stages = Gauge(
            "hikidashi_stage_reels",
            "Reels at each stage: ready to claim, waiting, leased, failed at the cap",
            labels=["stage", "status"],
        )
        for s in queue.queue(conn):
            for status_, key in (
                ("ready", "ready"),
                ("waiting", "waiting"),
                ("leased", "working"),
                ("failed", "failed"),
            ):
                stages.add_metric([s["stage"], status_], s[key])
        out.append(stages)
        done = Gauge(
            "hikidashi_stage_done_reels",
            "Reels each stage has done at its current version; its rate is the throughput",
            labels=["stage"],
        )
        for name in STAGES:
            info = queue.REGISTRY[name]
            done.add_metric(
                [name],
                one(f"SELECT COUNT(*) FROM item WHERE {info.stamp} >= {int(info.version)}"),
            )
        out.append(done)
        attempts = Gauge(
            "hikidashi_stage_failed_attempts",
            "Failed tries at each stage's current version, summed over reels",
            labels=["stage"],
        )
        for name in STAGES:
            attempts.add_metric(
                [name],
                one(
                    f"""SELECT SUM(attempts) FROM job_attempt
                        WHERE stage = '{name}' AND version = {int(queue.REGISTRY[name].version)}"""
                ),
            )
        out.append(attempts)
        reasons: dict[str, int] = {}
        for r in conn.execute(
            """SELECT fail_reason, COUNT(*) FROM item
               WHERE state = 'failed' AND fail_stage = 'fetch' GROUP BY 1"""
        ):
            label = fetch_reason(r[0])
            reasons[label] = reasons.get(label, 0) + r[1]
        out.append(
            gauge(
                "hikidashi_fetch_failures",
                "Reels whose fetch failed, by why",
                ["reason"],
                sorted(reasons.items()),
            )
        )

        found = queue.workers(conn)
        up = Gauge(
            "hikidashi_worker_up",
            f"1 if the worker checked in within {queue.STALE} s",
            labels=["worker"],
        )
        seen = Gauge(
            "hikidashi_worker_last_seen_timestamp_seconds",
            "A worker's last check-in",
            labels=["worker"],
        )
        runs = Gauge(
            "hikidashi_worker_stages", "1 for each stage a worker runs", labels=["worker", "stage"]
        )
        for w in found:
            up.add_metric([w["name"]], 1 if w["state"] == "running" else 0)
            seen.add_metric([w["name"]], w["seen_at"])
            for stage in w["stages"]:
                runs.add_metric([w["name"], stage], 1)
        overall = Gauge(
            "hikidashi_pipeline_overall",
            "1 for the workers' state in a word, as the Pipeline page shows it",
            labels=["state"],
        )
        overall.add_metric([queue.overall(found)], 1)
        out += [up, seen, runs, overall]

        out += self._hosted(conn, Gauge, Counter, status)
        return out

    def _api(self, conn: sqlite3.Connection, name: str) -> tuple[str, str | None]:
        """An API's label, and whose it is when per-user labels are on."""
        if name == "api:server":
            return "server", None
        if name.startswith("api:user:"):
            if not self.options.per_user:
                return "user", None
            row = conn.execute(
                "SELECT username FROM user WHERE id = ?", (int(name.rsplit(":", 1)[1]),)
            ).fetchone()
            return "user", row[0] if row else "?"
        return name, None  # hosted:<worker>

    def _hosted(self, conn: sqlite3.Connection, Gauge: Any, Counter: Any, status: Any) -> list[Any]:
        per = self.options.per_user
        labels = ["api", "user"] if per else ["api"]

        def key(name: str) -> tuple[str, ...]:
            api, user = self._api(conn, name)
            return (api, user or "") if per else (api,)

        enabled: dict[tuple[str, ...], int] = {}
        off: dict[tuple[str, ...], int] = {}
        flight: dict[tuple[str, ...], int] = {}
        budget: dict[tuple[str, ...], float] = {}
        for a in status.apis(conn, self.settings.db_path.parent):
            if a.get("on") is None:
                continue  # a hosted worker elsewhere: only its usage is known here
            k = key(a["name"])
            enabled[k] = enabled.get(k, 0) + (1 if a["on"] else 0)
            flight[k] = flight.get(k, 0) + a.get("in_flight", 0)
            if not a["on"] and a.get("reason"):
                rk = (*k, switched_off(a["reason"]))
                off[rk] = off.get(rk, 0) + 1
            if a.get("budget") is not None:
                budget[k] = budget.get(k, 0.0) + float(a["budget"])

        def family(cls: Any, name: str, doc: str, extra: list[str], values: dict[Any, Any]) -> Any:
            f = cls(name, doc, labels=labels + extra)
            for k, v in sorted(values.items()):
                f.add_metric(list(k), v)
            return f

        requests: dict[tuple[str, ...], int] = {}
        cost: dict[tuple[str, ...], float] = {}
        for r in conn.execute(
            """SELECT api, model, stage, SUM(requests), SUM(usd) FROM hosted_usage
               GROUP BY api, model, stage"""
        ):
            provider = str(r[1]).split(":", 1)[0]
            requests[(*key(r[0]), provider, r[2])] = (
                requests.get((*key(r[0]), provider, r[2]), 0) + r[3]
            )
            cost[(*key(r[0]), provider)] = cost.get((*key(r[0]), provider), 0.0) + (r[4] or 0)
        return [
            family(Gauge, "hikidashi_api_enabled", "1 if the hosted API is on", [], enabled),
            family(
                Gauge,
                "hikidashi_api_disabled_reason",
                "1 for why a hosted API went off by itself, in a word",
                ["reason"],
                off,
            ),
            family(Gauge, "hikidashi_api_in_flight", "Reels it is working on now", [], flight),
            family(
                Counter,
                "hikidashi_api_requests",
                "Requests to hosted providers",
                ["provider", "stage"],
                requests,
            ),
            family(
                Counter,
                "hikidashi_api_cost_usd",
                "Estimated spend on hosted providers",
                ["provider"],
                cost,
            ),
            family(Gauge, "hikidashi_api_budget_usd", "The monthly budget", [], budget),
        ]


class Metrics:
    """The registry, what feeds it in-process, and how a scrape is answered."""

    def __init__(self, settings: Settings, options: Options, version: str) -> None:
        from prometheus_client import (
            GC_COLLECTOR,
            PLATFORM_COLLECTOR,
            PROCESS_COLLECTOR,
            CollectorRegistry,
            Counter,
            Histogram,
        )
        from prometheus_client.metrics_core import CounterMetricFamily

        self.options = options
        self.registry: CollectorRegistry = CollectorRegistry(auto_describe=False)
        for c in (PROCESS_COLLECTOR, PLATFORM_COLLECTOR, GC_COLLECTOR):
            self.registry.register(c)
        self.library = Library(settings, options, version)
        self.registry.register(self.library)  # type: ignore[arg-type]
        self.requests = Counter(
            "hikidashi_http_requests",
            "HTTP requests, by route template",
            ["route", "method", "status"],
            registry=self.registry,
        )
        self.latency = Histogram(
            "hikidashi_http_request_duration_seconds",
            "How long requests took, by route template",
            ["route", "method"],
            buckets=BUCKETS,
            registry=self.registry,
        )
        self.search = Histogram(
            "hikidashi_search_duration_seconds",
            "How long each part of a search took",
            ["part"],
            buckets=BUCKETS,
            registry=self.registry,
        )

        class Dropped:
            def describe(self) -> list[Any]:
                return []

            def collect(self) -> Iterator[Any]:
                from app import logship

                c = CounterMetricFamily(
                    "hikidashi_log_lines_dropped",
                    "Log lines not shipped: the queue was full while a destination was away",
                )
                c.add_metric([], logship.dropped())
                yield c

        self.registry.register(Dropped())  # type: ignore[arg-type]

    def render(self, accept: str | None) -> tuple[bytes, str]:
        from prometheus_client.exposition import choose_encoder

        encoder, content_type = choose_encoder(accept or "")
        return encoder(self.registry), content_type

    def time_search(self, part: str, seconds: float) -> None:
        self.search.labels(part).observe(seconds)

    async def middleware(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = getattr(request.scope.get("route"), "path", None) or UNMATCHED
            if route not in UNCOUNTED:
                self.requests.labels(route, request.method, str(status)).inc()
                self.latency.labels(route, request.method).observe(time.perf_counter() - start)

    def serve(self, host: str, port: int) -> ThreadingHTTPServer:
        """/metrics on a port of its own, so it can stay off the published one."""
        metrics = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path.split("?", 1)[0] != PATH:
                    self.send_error(404)
                    return
                refused = metrics.options.refuse(
                    self.headers.get("Authorization"), self.client_address[0]
                )
                if refused:
                    self.send_response(refused)
                    if refused == 401:
                        self.send_header("WWW-Authenticate", 'Bearer realm="metrics"')
                    self.end_headers()
                    return
                body, ctype = metrics.render(self.headers.get("Accept"))
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:
                pass  # scrapes are not worth a line

        server = ThreadingHTTPServer((host, port), Handler)
        threading.Thread(target=server.serve_forever, name="metrics", daemon=True).start()
        log.info("metrics on port %d", server.server_address[1])
        return server


class Live:
    """What /metrics answers with now. Switched on, off or changed from the
    Account page, it starts again: the counters go back to zero, as after a
    restart, which Prometheus takes in its stride."""

    def __init__(self, settings: Settings, version: str) -> None:
        self.settings = settings
        self.version = version
        self.options = Options()
        self.metrics: Metrics | None = None
        self.server: ThreadingHTTPServer | None = None
        self._lock = threading.Lock()

    def switch(self, options: Options) -> Metrics | None:
        from app import search

        with self._lock:
            if options == self.options:
                return self.metrics
            if self.server is not None:
                self.server.shutdown()
                self.server.server_close()
                self.server = None
            self.metrics = Metrics(self.settings, options, self.version) if options.on else None
            search.observe = self.metrics.time_search if self.metrics else None
            if self.metrics and options.port:
                self.server = self.metrics.serve(self.settings.host, options.port)
            self.options = options
            return self.metrics


def install(
    app: FastAPI, settings: Settings, version: str, env: Env | None = None
) -> Metrics | None:
    """Give `app` a /metrics that answers while metrics are on, as
    HIKIDASHI_METRICS (or `env`) says now; `switch` changes that later."""
    from starlette.responses import Response

    live = Live(settings, version)
    app.state.metrics = live

    async def middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        m = live.metrics
        return await (m.middleware(request, call_next) if m else call_next(request))

    app.middleware("http")(middleware)

    # A plain Starlette route: nothing for FastAPI to read from the request.
    def scrape(request: Request) -> Response:
        m, options = live.metrics, live.options
        if m is None or options.port:
            return Response(status_code=404)
        refused = options.refuse(
            request.headers.get("authorization"), request.client.host if request.client else None
        )
        if refused:
            headers = {"WWW-Authenticate": 'Bearer realm="metrics"'} if refused == 401 else None
            return Response(status_code=refused, headers=headers)
        body, ctype = m.render(request.headers.get("accept"))
        return Response(body, media_type=ctype)

    app.router.add_route(PATH, scrape, methods=["GET"], include_in_schema=False)
    return live.switch(Options.from_env(env))


def switch(app: FastAPI, env: Env) -> Metrics | None:
    """Metrics as `env` now says: on, off, another port or token."""
    live: Live = app.state.metrics
    return live.switch(Options.from_env(env))
