import logging
import tempfile
import unittest
from pathlib import Path

from app import logs


class TestTail(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "server.log"

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_file_is_no_lines(self):
        self.assertEqual(logs.tail(self.path), [])

    def test_the_last_lines_oldest_first(self):
        self.path.write_text("".join(f"line {i}\n" for i in range(10)))
        self.assertEqual(logs.tail(self.path, 3), ["line 7", "line 8", "line 9"])
        self.assertEqual(len(logs.tail(self.path, 50)), 10)

    def test_reads_backwards_across_chunks_without_a_partial_line(self):
        self.path.write_text("".join(f"line {i:04}\n" for i in range(2000)))
        got = logs.tail(self.path, 100, chunk=64)
        self.assertEqual(got, [f"line {i:04}" for i in range(1900, 2000)])

    def test_bad_bytes_do_not_break_the_page(self):
        self.path.write_bytes(b"ok\n\xff\xfe broken\n")
        self.assertEqual(logs.tail(self.path)[0], "ok")


class TestInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        for name in logs.LOGGERS:
            logger = logging.getLogger(name)
            for h in self.handlers(name):
                logger.removeHandler(h)
                h.close()
        self.tmp.cleanup()

    def handlers(self, name):
        return [
            h
            for h in logging.getLogger(name).handlers
            if getattr(h, "baseFilename", "").startswith(self.tmp.name)
        ]

    def test_writes_uvicorn_lines_and_twice_is_once(self):
        path = logs.install(self.dir)
        logs.install(self.dir)
        self.assertEqual(len(self.handlers("uvicorn")), 1)
        self.assertIs(self.handlers("uvicorn")[0], self.handlers("uvicorn.access")[0])
        logging.getLogger("uvicorn.error").warning("embedder did not load")
        self.assertIn("WARNING", logs.tail(path)[-1])
        self.assertIn("embedder did not load", logs.tail(path)[-1])

    def test_the_log_pages_own_polling_and_thumbnails_are_not_logged(self):
        path = logs.install(self.dir)
        access = logging.getLogger("uvicorn.access")
        access.info('%s - "%s %s HTTP/%s" %d', "1.2.3.4", "GET", "/api/logs?lines=1000", "1.1", 200)
        access.info('%s - "%s %s HTTP/%s" %d', "1.2.3.4", "GET", "/media/thumbs/a.jpg", "1.1", 200)
        access.info('%s - "%s %s HTTP/%s" %d', "1.2.3.4", "GET", "/api/items", "1.1", 200)
        text = "\n".join(logs.tail(path))
        self.assertNotIn("/api/logs", text)
        self.assertNotIn("/media/", text)
        self.assertIn("/api/items", text)


if __name__ == "__main__":
    unittest.main()
