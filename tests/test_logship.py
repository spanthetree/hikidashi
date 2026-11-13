"""Sending the log to syslog and Loki (#149). Stdlib only: fake receivers."""

import gzip
import json
import logging
import os
import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar
from unittest import mock

from app import logs, logship

QUERY_LINE = '127.0.0.1:5000 - "GET /api/items?q=my+secret+search&tag=Food HTTP/1.1" 200'


def env(**kw: str) -> Any:
    keep = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("HIKIDASHI_SYSLOG", "HIKIDASHI_LOKI", "HIKIDASHI_LOG_"))
    }
    return mock.patch.dict(os.environ, {**keep, **kw}, clear=True)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class LoggerCase(unittest.TestCase):
    def setUp(self):
        self.logger = logging.getLogger(f"hikidashi.test.{self.id()}")
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False
        self.addCleanup(self.logger.handlers.clear)

    def ship(self, handler: logging.Handler) -> logging.Handler:
        self.logger.addHandler(handler)
        self.addCleanup(handler.close)
        return handler


class TestRedact(unittest.TestCase):
    def test_what_never_leaves_the_machine(self):
        cases = {
            QUERY_LINE: "secret",
            "Claim the library at /setup?token=Rb2t4tF_fXgugxtk": "Rb2t4tF",
            "POST /api/auth/invite/abcDEF123xyz HTTP/1.1": "abcDEF123xyz",
            "Authorization: Bearer hk_api_0123456789abcdef": "0123456789abcdef",
            "worker token hk_worker_ZZZZZZZZZZZZZZ rejected": "ZZZZZZZZZZZZZZ",
            "openai said: invalid key sk-proj-abcdefghijklmnop1234": "abcdefghijklmnop",
            "api_key=gsk_live_0000111122223333 password=hunter2": "hunter2",
            '{"token": "abc.def.ghi"}': "abc.def.ghi",
        }
        for line, secret in cases.items():
            self.assertNotIn(secret, logship.redact(line), line)

    def test_ordinary_lines_are_untouched(self):
        for line in (
            "asr: 12/12 done in 40s",
            '127.0.0.1:5000 - "GET /api/items/ABC123 HTTP/1.1" 200',
            "tag: hashtags read on 3 reels",
        ):
            self.assertEqual(logship.redact(line), line)


class TestFormats(LoggerCase):
    def record(self, msg: str, **extra: Any) -> logging.LogRecord:
        return self.logger.makeRecord(
            self.logger.name, logging.WARNING, __file__, 1, msg, (), None, extra=extra
        )

    def test_json_lines_parse_and_carry_the_known_fields(self):
        fmt = logship.Shipped("server", "nas", json_lines=True)
        line = json.loads(fmt.format(self.record(QUERY_LINE, stage="asr", user="x")))
        self.assertEqual(line["level"], "warning")
        self.assertEqual((line["process"], line["worker"], line["stage"]), ("server", "nas", "asr"))
        self.assertNotIn("user", line)
        self.assertNotIn("secret", line["msg"])

    def test_rfc5424(self):
        line = logship.Rfc5424("worker", 'desk"top', json_lines=False).format(self.record("hi"))
        self.assertRegex(line, r"^1 \d{4}-\d\d-\d\dT[\d:.]+\+00:00 \S+ hikidashi \d+ \S+ ")
        self.assertIn('[hikidashi@32473 process="worker" worker="desk\\"top"]', line)
        self.assertTrue(line.endswith("WARNING " + self.logger.name + "  hi"))

    def test_rfc3164(self):
        line = logship.Rfc3164("worker", "w", json_lines=False).format(self.record("hi"))
        self.assertRegex(line, r"^[A-Z][a-z]{2} [ \d]\d \d\d:\d\d:\d\d \S+ hikidashi\[\d+\]: ")


class TestSyslog(LoggerCase):
    def test_udp_rfc5424_with_facility_and_level(self):
        rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        rx.bind(("127.0.0.1", 0))
        rx.settimeout(5)
        self.addCleanup(rx.close)
        url = f"udp://127.0.0.1:{rx.getsockname()[1]}"
        with env(HIKIDASHI_SYSLOG_FACILITY="local3"):
            self.ship(logship.syslog_handler(url, "server", "nas"))
        self.logger.error("disk is full; %s", QUERY_LINE)
        data = rx.recv(65536).decode()
        self.assertTrue(data.startswith("<155>1 "), data)  # local3 (19) * 8 + error (3)
        self.assertIn(" hikidashi ", data)
        self.assertNotIn("secret", data)

    def test_tcp_one_line_a_message(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        srv.settimeout(5)
        self.addCleanup(srv.close)
        url = f"tcp://127.0.0.1:{srv.getsockname()[1]}"
        with env(HIKIDASHI_SYSLOG_FORMAT="rfc3164"):
            self.ship(logship.syslog_handler(url, "server", "nas"))
        self.logger.info("one")
        self.logger.info("two")
        conn, _ = srv.accept()
        conn.settimeout(5)
        self.addCleanup(conn.close)
        data = b""
        while data.count(b"\n") < 2:
            data += conn.recv(65536)
        lines = data.decode().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("<134>") and lines[0].endswith("one"), lines[0])
        self.assertNotIn("\x00", data.decode())

    def test_unix_socket(self):
        with tempfile.TemporaryDirectory() as d:
            path = f"{d}/log"
            rx = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            rx.bind(path)
            rx.settimeout(5)
            self.addCleanup(rx.close)
            self.ship(logship.syslog_handler(f"unix://{path}", "server", "nas"))
            self.logger.warning("over the socket")
            self.assertIn(b"over the socket", rx.recv(65536))

    def test_a_bad_setting_says_which(self):
        with self.assertRaisesRegex(ValueError, "HIKIDASHI_SYSLOG"):
            logship.syslog_handler("http://x", "server", "nas")
        with (
            env(HIKIDASHI_SYSLOG_FACILITY="kitchen"),
            self.assertRaisesRegex(ValueError, "FACILITY"),
        ):
            logship.syslog_handler("udp://x", "server", "nas")

    def test_a_receiver_that_is_away_never_raises_and_is_found_again(self):
        port = free_port()
        with mock.patch.object(logship, "_complain") as complain:
            self.ship(logship.syslog_handler(f"tcp://127.0.0.1:{port}", "s", "w"))
            self.logger.error("nobody is listening")
        complain.assert_called()
        srv = socket.socket()
        srv.bind(("127.0.0.1", port))
        srv.listen(1)
        srv.settimeout(5)
        self.addCleanup(srv.close)
        self.logger.error("back again")
        conn, _ = srv.accept()
        self.addCleanup(conn.close)
        conn.settimeout(5)
        self.assertIn(b"back again", conn.recv(65536))


class FakeLoki:
    """A /loki/api/v1/push that keeps what it is sent."""

    received: ClassVar[list[dict[str, Any]]]

    def __init__(self, port: int = 0) -> None:
        got: list[dict[str, Any]] = []
        self.got = got

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers["Content-Length"]))
                got.append(
                    {
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body": json.loads(gzip.decompress(body)),
                    }
                )
                self.send_response(204)
                self.end_headers()

            def log_message(self, *a: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def lines(self) -> list[str]:
        return [v[1] for p in self.got for s in p["body"]["streams"] for v in s["values"]]


class TestLoki(LoggerCase):
    def loki(self, **env_: str) -> tuple[FakeLoki, logship.Loki]:
        fake = FakeLoki()
        self.addCleanup(fake.close)
        with env(**env_):
            handler = logship.loki_handler(f"http://127.0.0.1:{fake.port}", "worker", "desktop")
        self.ship(handler)
        return fake, handler

    def test_batched_gzipped_streams_with_only_the_allowed_labels(self):
        fake, handler = self.loki(
            HIKIDASHI_LOKI_LABELS="env=home,app=override",
            HIKIDASHI_LOKI_TENANT="t1",
            HIKIDASHI_LOKI_TOKEN="lokitoken",
        )
        for i in range(5):
            self.logger.info("line %d", i)
        self.logger.warning(QUERY_LINE)
        handler.flush()
        self.assertEqual(len(fake.got), 1)  # one push for all six
        push = fake.got[0]
        self.assertEqual(push["path"], "/loki/api/v1/push")
        self.assertEqual(push["headers"]["x-scope-orgid"], "t1")
        self.assertEqual(push["headers"]["authorization"], "Bearer lokitoken")
        for stream in push["body"]["streams"]:
            self.assertEqual(set(stream["stream"]), {"app", "process", "worker", "level", "env"})
            self.assertEqual(stream["stream"]["app"], "hikidashi")  # not overridden
        self.assertEqual(len(fake.lines()), 6)
        self.assertFalse(any("secret" in line for line in fake.lines()))

    def test_basic_auth_from_a_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".pw", delete=False) as f:
            f.write("pw\n")
        self.addCleanup(os.unlink, f.name)
        fake, handler = self.loki(HIKIDASHI_LOKI_USER="u", HIKIDASHI_LOKI_PASSWORD_FILE=f.name)
        self.logger.info("x")
        handler.flush()
        self.assertEqual(fake.got[0]["headers"]["authorization"], "Basic dTpwdw==")

    def test_while_loki_is_away_nothing_waits_memory_is_bounded_and_drops_are_counted(self):
        port = free_port()
        handler = logship.Loki(
            f"http://127.0.0.1:{port}", {"app": "hikidashi"}, size=20, every=0.05
        )
        handler.setFormatter(logship.Shipped("s", "w", json_lines=False))
        self.ship(handler)
        before = logship.dropped()
        start = time.monotonic()
        with mock.patch.object(logship, "_complain"):
            for i in range(200):
                self.logger.info("line %d", i)
            self.assertLess(time.monotonic() - start, 0.5)
            self.assertLessEqual(len(handler.queue), 20)
            self.assertGreaterEqual(logship.dropped() - before, 180)

            # Back again: what is queued goes out, newest kept.
            fake = FakeLoki(port)
            self.addCleanup(fake.close)
            deadline = time.monotonic() + 10
            while not fake.got and time.monotonic() < deadline:
                time.sleep(0.05)
        self.assertTrue(fake.got, "nothing arrived once Loki was back")
        self.assertIn("line 199", fake.lines()[-1])

    def test_a_push_that_fails_does_not_log_into_itself(self):
        port = free_port()
        handler = logship.Loki(f"http://127.0.0.1:{port}", {}, every=3600)
        handler.setFormatter(logship.Shipped("s", "w", json_lines=False))
        root = logging.getLogger()
        root.addHandler(handler)  # the worst case: on the root, like install()
        self.addCleanup(root.removeHandler, handler)
        self.addCleanup(handler.close)
        with mock.patch("sys.stderr") as err:
            logging.getLogger("hikidashi.test.loop").warning("one line")
            handler.flush()
        self.assertEqual(len(handler.queue), 0)
        self.assertIn("loki", "".join(str(c) for c in err.write.call_args_list))


class TestInstall(unittest.TestCase):
    LOGGERS = ("hikidashi.test.install", "hikidashi.test.install.access")

    def tearDown(self):
        for name in self.LOGGERS:
            for h in list(logging.getLogger(name).handlers):
                logging.getLogger(name).removeHandler(h)
                h.close()

    def test_off_by_default(self):
        with env():
            self.assertEqual(logship.install("server", "w", self.LOGGERS, logging.Filter()), [])
        self.assertEqual(logging.getLogger(self.LOGGERS[0]).handlers, [])

    def test_both_at_once_once(self):
        fake = FakeLoki()
        self.addCleanup(fake.close)
        settings = {
            "HIKIDASHI_SYSLOG": "udp://127.0.0.1:9",
            "HIKIDASHI_LOKI_URL": f"http://127.0.0.1:{fake.port}",
            "HIKIDASHI_LOG_LEVEL": "warning",
        }
        with env(**settings):
            added = logship.install("server", "w", self.LOGGERS, logging.Filter())
            logship.install("server", "w", self.LOGGERS, logging.Filter())
        self.assertEqual(added, ["syslog", "loki"])
        handlers = logging.getLogger(self.LOGGERS[0]).handlers
        self.assertEqual(len(handlers), 2)
        self.assertTrue(all(h.level == logging.WARNING for h in handlers))

    def test_scrapes_and_health_checks_are_logged_nowhere(self):
        """A filter on the access logger itself: stdout, the file and both shippers."""
        with tempfile.TemporaryDirectory() as d, env():
            logs.install(Path(d))
        filters = [
            f for f in logging.getLogger("uvicorn.access").filters if isinstance(f, logging.Filter)
        ]
        for path in ("/metrics", "/healthz", "/readyz", "/api/items"):
            record = logging.LogRecord(
                "uvicorn.access", logging.INFO, "", 0, '%s - "%s %s"', ("ip", "GET", path), None
            )
            self.assertEqual(all(f.filter(record) for f in filters), path == "/api/items", path)

    def test_json_stdout(self):
        name = self.LOGGERS[0]
        stream = logging.StreamHandler()
        logging.getLogger(name).addHandler(stream)
        with env(HIKIDASHI_LOG_FORMAT="json"):
            logship.install("server", "w", self.LOGGERS, logging.Filter())
        self.assertIsInstance(stream.formatter, logship.Shipped)


if __name__ == "__main__":
    unittest.main()
