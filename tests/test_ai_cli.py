"""`hikidashi ai` (#125): every UI action, on the host and through --server,
with the same refusals; no key ever printed; exit codes."""

import contextlib
import dataclasses
import io
import json
import os
import unittest
from unittest import mock

from app import config as app_config
from app.ai import __main__ as cli
from app.ai import config
from tests.fake_openai import KEY, Fake
from tests.test_ai_settings import CLEAN, OPENAI, AiCase


class CliCase(AiCase):
    def setUp(self):
        super().setUp()
        s = dataclasses.replace(app_config.settings, db_path=self.dir / "t.sqlite3")
        p = mock.patch("app.config.settings", s)
        p.start()
        self.addCleanup(p.stop)

    def run_cli(self, *argv: str, stdin: str = "") -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with (
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
            mock.patch("sys.stdin", io.StringIO(stdin)),
        ):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()


class TestLocal(CliCase):
    def test_set_up_and_switch_on(self):
        self.assertEqual(self.run_cli("set", "all", "--user", "bob", "--provider", "openai")[0], 0)
        code, out, err = self.run_cli(
            "key", "set", "openai", "--user", "bob", "--stdin", stdin=OPENAI + "\n"
        )
        self.assertEqual(code, 0, err)
        self.assertNotIn(OPENAI, out + err)
        self.assertIn("sk-…0123", out)
        code, out, err = self.run_cli("on", "--user", "bob", "--backlog", "--yes")
        self.assertEqual(code, 3, out)
        self.assertIn("budget", err)
        self.assertEqual(self.run_cli("budget", "4", "--user", "bob")[0], 0)
        code, out, err = self.run_cli("on", "--user", "bob", "--new-only", "--yes")
        self.assertEqual(code, 0, err)
        self.assertIn("2 reels waiting", out)
        self.assertTrue(config.load(self.conn, 2, self.dir).switch.on)
        code, out, _ = self.run_cli("status", "--user", "bob", "--json")
        self.assertTrue(json.loads(out)[0]["on"])
        self.assertEqual(self.run_cli("off", "--user", "bob")[0], 0)
        self.assertFalse(config.load(self.conn, 2, self.dir).switch.on)
        code, out, _ = self.run_cli("show", "--user", "bob", "--json")
        self.assertNotIn(OPENAI, out)
        self.assertEqual(json.loads(out)["keys"]["openai"]["masked"], "sk-…0123")

    def test_a_backlog_needs_a_choice_without_a_terminal(self):
        self.ready(*self._bob())
        code, _, err = self.run_cli("on", "--user", "bob", "--yes")
        self.assertEqual(code, 2)
        self.assertIn("--backlog", err)

    def _bob(self):
        from tests.test_ai_settings import BOB

        return BOB, 2

    def test_the_server_api_is_for_admins_and_needs_for(self):
        # On the host the server's API is the first admin's to act on; --user
        # and --server-api do not go together.
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.run_cli("show", "--user", "bob", "--server-api")[0], 2)
        code, _, err = self.run_cli("on", "--server-api", "--yes")
        self.assertEqual(code, 2)

    def test_env_locked(self):
        with mock.patch.dict(os.environ, {"HIKIDASHI_LLM_MODEL": "x"}):
            code, out, err = self.run_cli("set", "tag", "--server-api", "--model", "y")
            self.assertEqual(code, 3)
            self.assertIn("HIKIDASHI_LLM_MODEL", err)
            code, out, _ = self.run_cli("show", "--server-api")
            self.assertIn("set by environment: HIKIDASHI_LLM_MODEL", out)

    def test_usage_errors(self):
        self.assertEqual(self.run_cli("set", "asr", "--provider", "nope")[0], 2)
        self.assertEqual(self.run_cli("set", "asr")[0], 2)
        self.assertEqual(self.run_cli("budget", "lots")[0], 2)
        self.assertEqual(self.run_cli("usage", "--month", "2026-13")[0], 2)

    def test_key_is_never_an_argument(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.run_cli("key", "set", "openai", OPENAI)[0], 2)

    def test_test_connection_and_models(self):
        fake = Fake().start()
        self.addCleanup(fake.stop)
        fake.chat_reply = lambda req: "OK"
        self.run_cli("set", "all", "--provider", "compatible", "--base-url", fake.url)
        self.run_cli("set", "all", "--model", "gpt-4.1-mini")
        self.run_cli("set", "asr", "--model", "whisper-1")
        self.run_cli("key", "set", "compatible", "--stdin", stdin=KEY)
        code, out, _ = self.run_cli("test")
        self.assertEqual(code, 0, out)
        self.assertIn("asr      OK (whisper-1)", out)
        code, out, _ = self.run_cli("models", "compatible")
        self.assertIn("whisper-1", out)
        fake.key = "other"
        code, out, _ = self.run_cli("test", "tag")
        self.assertEqual(code, 1)
        self.assertIn("key rejected", out)
        self.assertNotIn(KEY, out)

    def test_usage_report(self):
        code, out, _ = self.run_cli("usage", "--all")
        self.assertEqual(code, 0)
        self.assertIn("USD (estimated; prices checked", out)


# --- through --server -------------------------------------------------------

from tests import test_api as base  # noqa: E402


def setUpModule():
    base.setUpModule()


@unittest.skipUnless(base.HAS_WEB, "the web dependencies are not installed")
class TestRemote(base.SignInCase):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, CLEAN, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.claim()
        from app import accounts
        from app.db import open_db

        conn = open_db(self.settings.db_path)
        alice = accounts.by_name(conn, "alice")
        assert alice is not None
        self.ai, _ = accounts.make_token(
            conn, alice, "cli", ["read:ai", "write:ai", "read:admin", "write:admin"]
        )
        self.lib, _ = accounts.make_token(conn, alice, "cli", ["read:library"])
        conn.close()

        from fastapi.testclient import TestClient

        anon = TestClient(self.main.app)

        def send(remote, method, path, body=None):
            r = anon.request(
                method, path, json=body, headers={"Authorization": f"Bearer {remote.token}"}
            )
            if r.status_code >= 400:
                code = (
                    3
                    if r.status_code in (401, 403)
                    else 2
                    if r.status_code in (400, 404, 409, 422)
                    else 1
                )
                raise cli.Failed(f"{r.status_code}: {r.json().get('detail')}", code)
            return r.json()

        p = mock.patch.object(cli.Remote, "_send", send)
        p.start()
        self.addCleanup(p.stop)

    def run_cli(
        self, *argv: str, token: str | None = None, stdin: str = ""
    ) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with (
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
            mock.patch("sys.stdin", io.StringIO(stdin)),
            mock.patch.dict(os.environ, {"HIKIDASHI_API_TOKEN": token or self.ai}),
        ):
            code = cli.main([*argv, "--server", "http://server.invalid"])
        return code, out.getvalue(), err.getvalue()

    def test_the_same_actions_and_refusals(self):
        self.assertEqual(self.run_cli("set", "all", "--provider", "openai")[0], 0)
        code, out, _ = self.run_cli("key", "set", "openai", "--stdin", stdin=OPENAI)
        self.assertEqual(code, 0)
        self.assertNotIn(OPENAI, out)
        code, _, err = self.run_cli("on", "--new-only", "--yes")
        self.assertEqual(code, 3)
        self.assertIn("budget", err)
        self.run_cli("budget", "3")
        self.assertEqual(self.run_cli("on", "--new-only", "--yes")[0], 0)
        code, out, _ = self.run_cli("status", "--json")
        self.assertTrue(json.loads(out)[0]["on"])
        self.assertEqual(self.run_cli("set", "tag", "--server-api", "--provider", "groq")[0], 0)
        self.assertEqual(self.run_cli("usage", "--all")[0], 0)

    def test_a_token_without_the_ai_scope(self):
        code, _, err = self.run_cli("show", token=self.lib)
        self.assertEqual(code, 3)
        self.assertIn("read:ai", err)

    def test_no_user_flag_with_server(self):
        self.assertEqual(self.run_cli("show", "--user", "bob")[0], 2)


if __name__ == "__main__":
    unittest.main()
