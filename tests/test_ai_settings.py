"""Hosted AI settings (#125): saved per user and for the server, keys
encrypted and write-only, env precedence, the switch's rules, the loose rule."""

import importlib.util
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from app.accounts import User
from app.ai import config, eligible, secrets, service
from app.ai.service import Invalid, NotAllowed
from app.db import open_db
from app.providers.keys import Key

HAS_CRYPTO = importlib.util.find_spec("cryptography") is not None

ADMIN = User(id=1, username="local", is_admin=True)
BOB = User(id=2, username="bob")
CAROL = User(id=3, username="carol")
OPENAI = "sk-proj-abcdefghijklmnopqrstuvwxyz0123"

# The server's settings come from env first; none of it from the real one.
CLEAN = {k: v for k, v in os.environ.items() if not k.startswith(("HIKIDASHI_", "OPENAI", "GROQ"))}


def seed(conn):
    now = int(time.time())
    for uid, name in ((2, "bob"), (3, "carol")):
        conn.execute(
            "INSERT INTO user (id, username, created_at) VALUES (?, ?, ?)", (uid, name, now)
        )
    for code, savers in (("A1", (1,)), ("B1", (2,)), ("S1", (2, 3)), ("C1", (3,))):
        conn.execute(
            """INSERT INTO item (shortcode, permalink, fetch_v, media_path, state, saved_at)
               VALUES (?, ?, 99, ?, 'fetched', ?)""",
            (code, f"https://x/{code}", f"{code}.mp4", now),
        )
        for u in savers:
            conn.execute("INSERT INTO user_item (user_id, shortcode) VALUES (?, ?)", (u, code))


@unittest.skipUnless(HAS_CRYPTO, "cryptography is not installed")
class AiCase(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, CLEAN, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.conn = open_db(self.dir / "t.sqlite3")
        seed(self.conn)

    def ready(self, actor, owner, *, budget=5.0):
        service.set_stage(self.conn, actor, owner, "all", self.dir, provider="openai")
        service.set_key(self.conn, actor, owner, "openai", OPENAI, self.dir)
        service.set_budget(self.conn, actor, owner, budget, self.dir)


class TestSecrets(AiCase):
    def test_sealed_and_back(self):
        token = secrets.seal(Key(OPENAI), self.dir)
        self.assertNotIn(OPENAI, token)
        self.assertEqual(secrets.unseal(token, self.dir).reveal(), OPENAI)
        self.assertEqual(oct((self.dir / "secret.key").stat().st_mode & 0o777), "0o600")

    def test_a_lost_secret_key_asks_for_the_keys_again(self):
        service.set_key(self.conn, BOB, 2, "openai", OPENAI, self.dir)
        (self.dir / "secret.key").unlink()
        api = config.load(self.conn, 2, self.dir)
        self.assertIsNone(api.key("openai"))
        self.assertIn("enter it again", api.keys["openai"].problem or "")

    def test_the_env_secret(self):
        with mock.patch.dict(os.environ, {"HIKIDASHI_SECRET_KEY": "correct horse"}):
            token = secrets.seal(Key(OPENAI), self.dir)
            self.assertFalse((self.dir / "secret.key").exists())
            self.assertEqual(secrets.unseal(token, self.dir).reveal(), OPENAI)
        with self.assertRaises(secrets.Unreadable):
            secrets.unseal(token, self.dir)


class TestSettings(AiCase):
    def test_no_plaintext_key_anywhere(self):
        service.set_key(self.conn, BOB, 2, "openai", OPENAI, self.dir)
        dump = "\n".join(self.conn.iterdump())
        self.assertNotIn(OPENAI, dump)
        shown = service.show(self.conn, 2, self.dir)
        self.assertNotIn(OPENAI, repr(shown))
        self.assertEqual(shown["keys"]["openai"]["masked"], "sk-…0123")

    def test_set_and_show(self):
        service.set_stage(self.conn, BOB, 2, "asr", self.dir, provider="groq")
        service.set_stage(self.conn, BOB, 2, "asr", self.dir, model="whisper-large-v3")
        api = config.load(self.conn, 2, self.dir)
        self.assertEqual(api.stages["asr"].provider.value, "groq")
        self.assertEqual(api.model("asr"), "whisper-large-v3")
        # Another provider drops the model chosen for the last.
        service.set_stage(self.conn, BOB, 2, "asr", self.dir, provider="openai")
        self.assertEqual(config.load(self.conn, 2, self.dir).model("asr"), "gpt-transcribe")

    def test_the_base_url_env_never_redirects_a_preset(self):
        with mock.patch.dict(os.environ, {"HIKIDASHI_OPENAI_BASE_URL": "http://lan:8000/v1"}):
            service.set_stage(self.conn, ADMIN, None, "asr", self.dir, provider="groq")
            api = config.load(self.conn, None, self.dir)
            self.assertEqual(api.client("asr").base_url, "https://api.groq.com/openai/v1")
            service.set_stage(self.conn, ADMIN, None, "tag", self.dir, provider="compatible")
            self.assertEqual(
                config.load(self.conn, None, self.dir).client("tag").base_url, "http://lan:8000/v1"
            )

    def test_a_provider_without_a_stage_cannot_take_it(self):
        import dataclasses

        from app.providers import presets

        textonly = dataclasses.replace(presets.PRESETS["groq"], can=frozenset({"chat"}))
        with mock.patch.dict(presets.PRESETS, {"groq": textonly}):
            with self.assertRaises(Invalid):
                service.set_stage(self.conn, BOB, 2, "ocr", self.dir, provider="groq")
            service.set_stage(self.conn, BOB, 2, "all", self.dir, provider="groq")
            api = config.load(self.conn, 2, self.dir)
            self.assertEqual(api.stages["tag"].provider.value, "groq")
            self.assertIsNone(api.stages["ocr"].provider.value)

    def test_who_may_change_what(self):
        with self.assertRaises(NotAllowed):
            service.set_stage(self.conn, BOB, None, "asr", self.dir, provider="openai")
        with self.assertRaises(NotAllowed):
            service.set_key(self.conn, BOB, 3, "openai", OPENAI, self.dir)
        with self.assertRaises(NotAllowed):
            service.set_key(self.conn, ADMIN, 2, "openai", OPENAI, self.dir)

    def test_env_wins_for_the_server_and_locks_the_field(self):
        with mock.patch.dict(
            os.environ, {"HIKIDASHI_LLM_MODEL": "gpt-4.1-nano", "OPENAI_API_KEY": OPENAI}
        ):
            api = config.load(self.conn, None, self.dir)
            self.assertEqual(api.stages["tag"].model.source, "env:HIKIDASHI_LLM_MODEL")
            self.assertEqual(api.keys["openai"].source, "env:OPENAI_API_KEY")
            with self.assertRaises(NotAllowed) as cm:
                service.set_stage(self.conn, ADMIN, None, "tag", self.dir, model="other")
            self.assertIn("HIKIDASHI_LLM_MODEL", str(cm.exception))
            with self.assertRaises(NotAllowed):
                service.set_key(self.conn, ADMIN, None, "openai", OPENAI, self.dir)
            # A user's own API never reads the server's env.
            self.assertIsNone(config.load(self.conn, 2, self.dir).key("openai"))

    def test_env_never_switches_it_on(self):
        with mock.patch.dict(
            os.environ,
            {
                "HIKIDASHI_ASR_PROVIDER": "openai",
                "HIKIDASHI_OCR_PROVIDER": "openai",
                "HIKIDASHI_LLM_PROVIDER": "openai",
                "OPENAI_API_KEY": OPENAI,
                "HIKIDASHI_HOSTED_BUDGET": "5",
            },
        ):
            api = config.load(self.conn, None, self.dir)
            self.assertEqual(api.problems(), [])
            self.assertFalse(api.switch.on)
            self.assertEqual(config.switched_on(self.conn), [])

    def test_audit_never_holds_a_key(self):
        service.set_key(self.conn, BOB, 2, "openai", OPENAI, self.dir)
        rows = [tuple(r) for r in self.conn.execute("SELECT action, detail FROM ai_audit")]
        self.assertIn(("key set openai", "sk-…0123"), rows)


class TestSwitch(AiCase):
    def test_not_on_without_all_four_stages_and_a_budget(self):
        service.set_stage(self.conn, BOB, 2, "asr", self.dir, provider="openai")
        service.set_key(self.conn, BOB, 2, "openai", OPENAI, self.dir)
        with self.assertRaises(NotAllowed) as cm:
            service.turn_on(self.conn, BOB, 2, self.dir)
        self.assertIn("ocr: no provider set", str(cm.exception))
        service.set_stage(self.conn, BOB, 2, "all", self.dir, provider="openai")
        with self.assertRaises(NotAllowed) as cm:
            service.turn_on(self.conn, BOB, 2, self.dir)
        self.assertIn("budget", str(cm.exception))
        service.set_budget(self.conn, BOB, 2, 3, self.dir)
        self.assertTrue(service.turn_on(self.conn, BOB, 2, self.dir).switch.on)
        self.assertEqual(config.use(self.conn, 2), "mine")

    def test_the_server_api_is_the_system_default_for_everyone(self):
        self.ready(ADMIN, None)
        api = service.turn_on(self.conn, ADMIN, None, self.dir)
        self.assertEqual((api.switch.on, api.switch.scope, api.switch.by), (True, "all", 1))
        self.assertEqual(service.choice(self.conn, None), "mine")
        with self.assertRaises(Invalid):
            service.turn_on(self.conn, ADMIN, None, self.dir, scope="some")

    def test_removing_a_users_key_turns_them_off(self):
        self.ready(BOB, 2)
        service.turn_on(self.conn, BOB, 2, self.dir)
        service.remove_key(self.conn, ADMIN, 2, "openai", self.dir)
        api = config.load(self.conn, 2, self.dir)
        self.assertFalse(api.switch.on)
        self.assertEqual(api.switch.reason, "an admin removed a key")

    def test_a_change_that_breaks_it_turns_it_off(self):
        self.ready(BOB, 2)
        service.turn_on(self.conn, BOB, 2, self.dir)
        service.set_budget(self.conn, BOB, 2, None, self.dir)
        self.assertFalse(config.load(self.conn, 2, self.dir).switch.on)

    def test_the_system_default_follows_the_admin(self):
        self.assertEqual(service.choice(self.conn, None), "workers")
        self.assertEqual(service.applies(self.conn, 2, self.dir), "workers")
        self.ready(ADMIN, None)
        service.set_choice(self.conn, ADMIN, None, "mine", self.dir)
        # Chosen, and not yet started: still workers.
        self.assertEqual(service.applies(self.conn, 2, self.dir), "workers")
        service.turn_on(self.conn, ADMIN, None, self.dir)
        self.assertEqual(service.applies(self.conn, 2, self.dir), "server")
        self.assertEqual(service.applies(self.conn, 1, self.dir), "server")
        # The old name of the default reads as it.
        self.assertEqual(service.set_use(self.conn, BOB, 2, "server", self.dir), "default")
        service.set_use(self.conn, BOB, 2, "workers", self.dir)
        self.assertEqual(service.applies(self.conn, 2, self.dir), "workers")
        # The admin chooses workers: the server's API goes off for everyone.
        service.set_choice(self.conn, ADMIN, None, "workers", self.dir)
        self.assertFalse(config.load(self.conn, None, self.dir).switch.on)
        self.assertEqual(service.applies(self.conn, 3, self.dir), "workers")
        with self.assertRaises(Invalid):
            service.set_choice(self.conn, ADMIN, None, "default", self.dir)
        with self.assertRaises(NotAllowed):
            service.set_choice(self.conn, BOB, None, "mine", self.dir)

    def test_without_cryptography_a_key_is_a_clear_error(self):
        from app.ai import secrets as sec

        with mock.patch.object(sec, "_fernet", side_effect=sec.NoCipher()):
            with self.assertRaises(service.AiError) as cm:
                service.set_key(self.conn, BOB, 2, "openai", OPENAI, self.dir)
        self.assertEqual(cm.exception.status, 503)
        self.assertIn("cryptography", str(cm.exception))


class TestLooseRule(AiCase):
    def codes(self, owner):
        return set(eligible.waiting(self.conn, config.load(self.conn, owner, self.dir)))

    def test_a_users_api_takes_their_reels(self):
        self.ready(BOB, 2)
        service.turn_on(self.conn, BOB, 2, self.dir)
        self.assertEqual(self.codes(2), {"B1", "S1"})

    def test_server_just_me(self):
        self.ready(ADMIN, None)
        service.turn_on(self.conn, ADMIN, None, self.dir, scope="me")
        self.assertEqual(self.codes(None), {"A1"})

    def test_server_all_users_and_workers_only(self):
        self.ready(ADMIN, None)
        service.turn_on(self.conn, ADMIN, None, self.dir, scope="all")
        self.assertEqual(self.codes(None), {"A1", "B1", "S1", "C1"})
        service.set_use(self.conn, BOB, 2, "workers", self.dir)
        # S1 is Carol's too, and she relies on the server: still taken.
        self.assertEqual(self.codes(None), {"A1", "S1", "C1"})
        service.set_use(self.conn, CAROL, 3, "workers", self.dir)
        self.assertEqual(self.codes(None), {"A1"})

    def test_only_new_reels_leaves_the_backlog(self):
        self.ready(BOB, 2)
        self.assertEqual(service.backlog(self.conn, 2, self.dir)["reels"], 2)
        service.turn_on(self.conn, BOB, 2, self.dir, backlog=False)
        self.assertEqual(self.codes(2), set())

    def test_me_to_all_shows_only_the_new_backlog(self):
        self.ready(ADMIN, None)
        service.turn_on(self.conn, ADMIN, None, self.dir, scope="me")
        self.assertEqual(
            service.backlog(self.conn, None, self.dir, actor=1, scope="all")["reels"], 3
        )

    def test_leased_and_errored_reels_are_out(self):
        self.ready(BOB, 2)
        service.turn_on(self.conn, BOB, 2, self.dir)
        self.conn.execute(
            """INSERT INTO job_lease (shortcode, stage, worker, expires_at)
               VALUES ('B1', 'asr', 'w', ?)""",
            (int(time.time()) + 300,),
        )
        for _ in range(eligible.MAX_ERRORS):
            eligible.count_error(self.conn, "api:user:2", "S1", "bad", 0)
        self.assertEqual(self.codes(2), set())

    def test_grace(self):
        eligible.mark_seen(self.conn, "api:server", ["A1"], 1000)
        eligible.mark_seen(self.conn, "api:server", ["A1", "B1"], 1050)
        self.assertEqual(
            eligible.after_grace(self.conn, "api:server", ["A1", "B1"], 1061, 60), ["A1"]
        )


# --- the routes ---------------------------------------------------------------

from tests import test_api as base  # noqa: E402


def setUpModule():
    base.setUpModule()


@unittest.skipUnless(HAS_CRYPTO and base.HAS_WEB, "the web dependencies are not installed")
class TestRoutes(base.SignInCase):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, CLEAN, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.claim()

    def test_a_user_sets_their_own_and_never_sees_the_key_again(self):
        bob = self.bob()
        r = bob.put("/api/me/providers/stages/all", json={"provider": "openai"})
        self.assertEqual(r.status_code, 200, r.text)
        r = bob.put("/api/me/providers/keys/openai", json={"key": OPENAI})
        self.assertEqual(r.status_code, 200, r.text)
        for got in (r, bob.get("/api/me/providers")):
            self.assertNotIn(OPENAI, got.text)
            self.assertEqual(got.json()["keys"]["openai"]["masked"], "sk-…0123")
        r = bob.post("/api/me/providers/switch", json={"on": True})
        self.assertEqual(r.status_code, 403)
        self.assertIn("budget", r.json()["detail"])
        bob.put("/api/me/providers/budget", json={"usd": 2})
        r = bob.get("/api/me/providers/backlog")
        self.assertEqual(r.json()["reels"], 0)
        r = bob.post("/api/me/providers/switch", json={"on": True})
        self.assertTrue(r.json()["switch"]["on"])
        self.assertEqual(r.json()["applies"], "mine")

    def test_the_server_api_is_admin_only(self):
        bob = self.bob()
        for method, path in (
            ("GET", "/api/admin/settings/providers"),
            ("PUT", "/api/admin/settings/providers/stages/asr"),
            ("GET", "/api/admin/ai/users"),
        ):
            r = bob.request(method, path, json={"provider": "openai"})
            self.assertEqual(r.status_code, 403, (path, r.text))
        r = self.client.put("/api/admin/settings/providers/stages/asr", json={"provider": "groq"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["stages"]["asr"]["provider"]["value"], "groq")

    def test_an_admin_sees_whether_a_user_has_a_key_and_can_only_remove_it(self):
        bob = self.bob()
        bob.put("/api/me/providers/keys/openai", json={"key": OPENAI})
        r = self.client.get("/api/admin/ai/users")
        row = next(u for u in r.json() if u["username"] == "bob")
        self.assertEqual(row["keys"], ["openai"])
        self.assertNotIn(OPENAI, r.text)
        r = self.client.delete(f"/api/admin/ai/users/{row['id']}/keys/openai")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(next(u for u in r.json() if u["username"] == "bob")["keys"], [])

    def test_a_token_needs_the_ai_scope(self):
        from app import accounts

        conn = open_db(self.settings.db_path)
        alice = accounts.by_name(conn, "alice")
        assert alice is not None
        lib, _ = accounts.make_token(conn, alice, "s", ["read:library", "write:library"])
        ai, _ = accounts.make_token(conn, alice, "s", ["read:ai"])
        conn.close()
        from fastapi.testclient import TestClient

        anon = TestClient(self.main.app)
        h = {"Authorization": f"Bearer {lib}"}
        self.assertEqual(anon.get("/api/me/providers", headers=h).status_code, 403)
        h = {"Authorization": f"Bearer {ai}"}
        self.assertEqual(anon.get("/api/me/providers", headers=h).status_code, 200)
        r = anon.put("/api/me/providers/budget", headers=h, json={"usd": 3})
        self.assertEqual(r.status_code, 403)

    def test_the_pipeline_shows_the_apis_and_each_user_their_own(self):
        r = self.client.get("/api/pipeline")
        self.assertEqual(r.json()["apis"][0]["name"], "api:server")
        bob = self.bob()
        r = bob.get("/api/me/pipeline")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["applies"], "workers")
        self.assertEqual(bob.get("/api/pipeline").status_code, 403)

    def test_requeue(self):
        r = self.client.post("/api/admin/ai/requeue", json={"stage": "tag", "made_by": "cpu"})
        self.assertEqual(r.json(), {"reels": 0})
        r = self.client.post("/api/admin/ai/requeue", json={"stage": "nope", "made_by": "cpu"})
        self.assertEqual(r.status_code, 400)

    def test_every_change_answers_with_the_whole_page(self):
        """The page replaces what it shows with each answer: one missing a
        field crashed it (0.23.0's /use had no `spent`)."""
        want = {"stages", "keys", "switch", "spent", "history", "choice", "choices", "applies"}
        bob = self.bob()
        for method, path, body in (
            ("GET", "/api/me/providers", None),
            ("PUT", "/api/me/providers/use", {"use": "mine"}),
            ("PUT", "/api/me/providers/stages/asr", {"provider": "groq"}),
            ("PUT", "/api/me/providers/budget", {"usd": 2}),
            ("PUT", "/api/me/providers/language", {"code": "ja"}),
            ("PUT", "/api/me/providers/keys/groq", {"key": "gsk_abcdefghijklmnop1234"}),
            ("DELETE", "/api/me/providers/keys/groq", None),
            ("POST", "/api/me/providers/switch", {"on": False}),
            ("PUT", "/api/me/providers/use", {"use": "workers"}),
        ):
            r = bob.request(method, path, json=body)
            self.assertEqual(r.status_code, 200, (path, r.text))
            self.assertLessEqual(want, set(r.json()), path)
        r = self.client.put("/api/admin/settings/providers/use", json={"use": "mine"})
        self.assertLessEqual(want - {"applies"}, set(r.json()))

    def test_bad_input_is_400(self):
        r = self.client.put("/api/me/providers/stages/asr", json={"provider": "nope"})
        self.assertEqual(r.status_code, 400)
        r = self.client.put("/api/me/providers/budget", json={"usd": -1})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
