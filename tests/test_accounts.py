"""Accounts, sessions and invites (#12). Needs argon2-cffi; skipped without it."""

import dataclasses
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import accounts, config
from app.accounts import AccountError
from app.db import open_db
from tests.fixtures import must

HAS_ARGON2 = importlib.util.find_spec("argon2") is not None


@unittest.skipUnless(HAS_ARGON2, "argon2-cffi not installed")
class AccountsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "t.sqlite3"
        self.conn = open_db(self.path)
        self.owner = accounts.claim(self.conn, "alice", "correct horse")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()


class TestSetup(unittest.TestCase):
    def test_a_new_library_needs_claiming(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = open_db(Path(tmp) / "t.sqlite3")
            self.assertTrue(accounts.needs_setup(conn))
            self.assertEqual(accounts.local(conn).username, "local")
            conn.close()


class TestPasswords(AccountsCase):
    def test_claiming_names_the_owner(self):
        self.assertEqual(
            (self.owner.id, self.owner.username, self.owner.is_admin), (1, "alice", True)
        )
        self.assertFalse(accounts.needs_setup(self.conn))

    def test_sign_in(self):
        self.assertEqual(accounts.authenticate(self.conn, "alice", "correct horse"), self.owner)
        self.assertEqual(
            accounts.authenticate(self.conn, "ALICE", "correct horse"), self.owner
        )  # usernames ignore case
        self.assertIsNone(accounts.authenticate(self.conn, "alice", "wrong horse"))
        self.assertIsNone(accounts.authenticate(self.conn, "nobody", "correct horse"))

    def test_an_account_with_no_password_cannot_sign_in(self):
        accounts.create(self.conn, "bob", None)
        self.assertIsNone(accounts.authenticate(self.conn, "bob", ""))

    def test_a_disabled_account_cannot_sign_in(self):
        bob = accounts.create(self.conn, "bob", "bobs password")
        accounts.update(self.conn, self.owner, bob.id, disabled=True)
        self.assertIsNone(accounts.authenticate(self.conn, "bob", "bobs password"))

    def test_the_rules(self):
        with self.assertRaisesRegex(AccountError, "at least 8"):
            accounts.create(self.conn, "bob", "short")
        with self.assertRaisesRegex(AccountError, "taken"):
            accounts.create(self.conn, "Alice", "long enough")
        with self.assertRaisesRegex(AccountError, "Usernames"):
            accounts.create(self.conn, "bob smith", "long enough")

    def test_handles_are_stored_as_instagram_shows_them(self):
        accounts.set_handle(self.conn, 1, " @Ramen.Lab_ ")
        self.assertEqual(must(accounts.get(self.conn, 1)).ig_handle, "ramen.lab_")
        accounts.set_handle(self.conn, 1, "")
        self.assertIsNone(must(accounts.get(self.conn, 1)).ig_handle)
        with self.assertRaises(AccountError):
            accounts.set_handle(self.conn, 1, "not a handle!")


class TestSessions(AccountsCase):
    def test_a_session_signs_in_until_it_ends(self):
        token = accounts.start_session(self.conn, 1)
        self.assertEqual(accounts.session_user(self.conn, token), self.owner)
        accounts.end_session(self.conn, token)
        self.assertIsNone(accounts.session_user(self.conn, token))

    def test_only_a_hash_is_kept(self):
        token = accounts.start_session(self.conn, 1)
        stored = self.conn.execute("SELECT token_hash FROM session").fetchone()[0]
        self.assertNotIn(token, stored)

    def test_expired_and_made_up_tokens_sign_in_nobody(self):
        token = accounts.start_session(self.conn, 1)
        self.conn.execute("UPDATE session SET expires_at = 0")
        self.assertIsNone(accounts.session_user(self.conn, token))
        self.assertIsNone(accounts.session_user(self.conn, "made-up"))
        self.assertIsNone(accounts.session_user(self.conn, None))

    def test_signing_out_everywhere_but_here(self):
        here, there = (accounts.start_session(self.conn, 1) for _ in range(2))
        accounts.end_sessions(self.conn, 1, keep=here)
        self.assertIsNotNone(accounts.session_user(self.conn, here))
        self.assertIsNone(accounts.session_user(self.conn, there))

    def test_disabling_someone_signs_them_out(self):
        bob = accounts.create(self.conn, "bob", "bobs password")
        token = accounts.start_session(self.conn, bob.id)
        accounts.update(self.conn, self.owner, bob.id, disabled=True)
        self.assertIsNone(accounts.session_user(self.conn, token))


class TestInvites(AccountsCase):
    def test_an_invite_makes_one_account(self):
        token, _ = accounts.invite(self.conn, self.owner)
        self.assertTrue(accounts.invite_open(self.conn, token))
        bob = accounts.accept(self.conn, token, "bob", "bobs password")
        self.assertFalse(bob.is_admin)
        with self.assertRaisesRegex(AccountError, "expired or has been used"):
            accounts.accept(self.conn, token, "carol", "carols password")
        self.assertIsNone(accounts.by_name(self.conn, "carol"))

    def test_a_refused_account_leaves_the_invite_open(self):
        token, _ = accounts.invite(self.conn, self.owner)
        with self.assertRaises(AccountError):
            accounts.accept(self.conn, token, "alice", "long enough")
        self.assertTrue(accounts.invite_open(self.conn, token))

    def test_expired_and_revoked_invites_are_shut(self):
        token, _ = accounts.invite(self.conn, self.owner)
        self.conn.execute("UPDATE invite SET expires_at = 0")
        self.assertFalse(accounts.invite_open(self.conn, token))
        token, _ = accounts.invite(self.conn, self.owner)
        accounts.revoke_invite(self.conn, accounts.invites(self.conn)[0]["id"])
        self.assertFalse(accounts.invite_open(self.conn, token))


class TestTokens(AccountsCase):
    def setUp(self):
        super().setUp()
        self.bob = accounts.create(self.conn, "bob", "bobs password")

    def test_shown_once_kept_as_a_hash(self):
        token, token_id = accounts.make_token(self.conn, self.bob, "script", ["read:library"])
        self.assertRegex(token, r"^hk_api_[0-9A-Za-z]{36}$")
        stored = self.conn.execute("SELECT token_hash FROM api_token").fetchone()[0]
        self.assertNotIn(token, stored)
        [listed] = accounts.tokens(self.conn, user_id=self.bob.id)
        self.assertEqual(
            (listed["id"], listed["name"], listed["scopes"], listed["owner"]),
            (token_id, "script", ["read:library"], "bob"),
        )
        self.assertNotIn(token, repr(listed))

    def test_a_token_is_its_owner(self):
        token, _ = accounts.make_token(self.conn, self.bob, "script", ["read:library"])
        found = must(accounts.find_token(self.conn, token))
        self.assertEqual((found.kind, found.user), ("api", self.bob))
        self.assertIsNotNone(self.conn.execute("SELECT last_used_at FROM api_token").fetchone()[0])

    def test_scopes_are_picked_and_writing_includes_reading(self):
        token, _ = accounts.make_token(self.conn, self.owner, "t", ["write:library", "read:admin"])
        found = must(accounts.find_token(self.conn, token))
        self.assertTrue(found.grants("library", write=False))
        self.assertTrue(found.grants("library", write=True))
        self.assertTrue(found.grants("admin", write=False))
        self.assertFalse(found.grants("admin", write=True))
        token, _ = accounts.make_token(self.conn, self.bob, "t", ["read:library"])
        self.assertFalse(must(accounts.find_token(self.conn, token)).grants("library", write=True))

    def test_the_scope_rules(self):
        for scopes, why in (
            ([], "at least one"),
            (["read:everything"], "no scope called read:everything"),
            (["read:admin"], "Only an admin"),
        ):
            with self.assertRaisesRegex(AccountError, why):
                accounts.make_token(self.conn, self.bob, "t", scopes)
        self.assertEqual(
            accounts.check_scopes(self.owner, ["write:admin", "read:library", "read:library"]),
            "read:library write:admin",
        )

    def test_names_and_lives(self):
        for name in ("", "   ", "x" * 65):
            with self.assertRaisesRegex(AccountError, "Name it"):
                accounts.make_token(self.conn, self.bob, name, ["read:library"])
        for days in (0, 31):
            with self.assertRaisesRegex(AccountError, "1 to 30 days"):
                accounts.make_token(self.conn, self.bob, "t", ["read:library"], days)
        accounts.make_token(self.conn, self.bob, "  my   script ", ["read:library"], 7)
        [t] = accounts.tokens(self.conn)
        self.assertEqual(t["name"], "my script")
        self.assertEqual(t["expires_at"] - t["created_at"], 7 * 86400)

    def test_expired_revoked_mistyped_and_made_up_tokens_are_nobody(self):
        token, token_id = accounts.make_token(self.conn, self.bob, "t", ["read:library"])
        mistyped = token[:-1] + ("0" if token[-1] != "0" else "1")
        self.assertFalse(accounts.well_formed(mistyped))
        self.assertIsNone(accounts.find_token(self.conn, mistyped))
        self.assertIsNone(accounts.find_token(self.conn, "hk_api_made-up"))
        self.assertIsNone(accounts.find_token(self.conn, ""))
        self.conn.execute("UPDATE api_token SET expires_at = 1")
        self.assertIsNone(accounts.find_token(self.conn, token))
        self.assertEqual(accounts.tokens(self.conn), [])  # cleared out
        token, token_id = accounts.make_token(self.conn, self.bob, "t", ["read:library"])
        self.assertTrue(accounts.revoke_token(self.conn, token_id, user_id=self.bob.id))
        self.assertIsNone(accounts.find_token(self.conn, token))

    def test_nobody_revokes_someone_elses_but_an_admin(self):
        _, token_id = accounts.make_token(self.conn, self.bob, "t", ["read:library"])
        self.assertFalse(accounts.revoke_token(self.conn, token_id, user_id=self.owner.id))
        self.assertEqual(len(accounts.tokens(self.conn)), 1)
        self.assertTrue(accounts.revoke_token(self.conn, token_id))

    def test_a_disabled_account_loses_its_tokens(self):
        token, _ = accounts.make_token(self.conn, self.bob, "t", ["read:library"])
        accounts.update(self.conn, self.owner, self.bob.id, disabled=True)
        self.assertIsNone(accounts.find_token(self.conn, token))
        accounts.update(self.conn, self.owner, self.bob.id, disabled=False)
        self.assertIsNone(accounts.find_token(self.conn, token))  # gone, not paused

    def test_admin_scopes_stop_with_admin(self):
        carol = accounts.create(self.conn, "carol", "carols password", is_admin=True)
        token, _ = accounts.make_token(self.conn, carol, "t", ["read:library", "read:admin"])
        accounts.update(self.conn, self.owner, carol.id, is_admin=False)
        found = must(accounts.find_token(self.conn, token))
        self.assertFalse(found.grants("admin", write=False))
        self.assertTrue(found.grants("library", write=False))


class TestWorkerTokens(AccountsCase):
    def test_admins_only_and_they_never_expire(self):
        bob = accounts.create(self.conn, "bob", "bobs password")
        with self.assertRaisesRegex(AccountError, "Only an admin"):
            accounts.make_worker_token(self.conn, bob, "desktop")
        token, _ = accounts.make_worker_token(self.conn, self.owner, "desktop")
        self.assertRegex(token, r"^hk_wkr_[0-9A-Za-z]{36}$")
        self.assertTrue(accounts.worker_token_ok(self.conn, token))
        self.assertIsNone(self.conn.execute("SELECT expires_at FROM api_token").fetchone()[0])

    def test_the_two_kinds_never_stand_in_for_each_other(self):
        worker, _ = accounts.make_worker_token(self.conn, self.owner, "desktop")
        api, _ = accounts.make_token(self.conn, self.owner, "t", ["write:library", "write:admin"])
        self.assertFalse(accounts.worker_token_ok(self.conn, api))
        found = must(accounts.find_token(self.conn, worker))
        self.assertFalse(found.grants("library", write=False))
        self.assertFalse(found.grants("admin", write=False))

    def test_a_worker_token_stops_when_its_owner_is_no_admin(self):
        carol = accounts.create(self.conn, "carol", "carols password", is_admin=True)
        token, _ = accounts.make_worker_token(self.conn, carol, "desktop")
        accounts.update(self.conn, self.owner, carol.id, is_admin=False)
        self.assertFalse(accounts.worker_token_ok(self.conn, token))

    def test_listed_apart_from_api_tokens(self):
        accounts.make_worker_token(self.conn, self.owner, "desktop")
        accounts.make_token(self.conn, self.owner, "script", ["read:library"])
        [w] = accounts.worker_tokens(self.conn)
        self.assertEqual((w["name"], w["created_by"]), ("desktop", "alice"))
        self.assertTrue(accounts.any_worker_token(self.conn))


class TestAdmin(AccountsCase):
    def test_nobody_changes_their_own_account(self):
        with self.assertRaisesRegex(AccountError, "your own"):
            accounts.update(self.conn, self.owner, self.owner.id, disabled=True)

    def test_there_is_always_an_admin_who_can_sign_in(self):
        bob = accounts.create(self.conn, "bob", None, is_admin=True)  # no password
        with self.assertRaisesRegex(AccountError, "no admin"):
            accounts.update(self.conn, bob, self.owner.id, disabled=True)
        self.assertIsNotNone(accounts.get(self.conn, self.owner.id))  # rolled back

    def test_everyone_with_their_library_size(self):
        accounts.create(self.conn, "bob", None)
        rows = {u["username"]: u for u in accounts.everyone(self.conn)}
        self.assertEqual((rows["bob"]["reels"], rows["bob"]["has_password"]), (0, False))


class CliCase(AccountsCase):
    CLI = "app.accounts.__main__"

    def run_cli(self, *argv, stdin=""):
        cli = importlib.import_module(self.CLI)
        out = io.StringIO()
        here = dataclasses.replace(
            config.settings,
            db_path=self.path,
            **{  # type: ignore[arg-type]  # all of these are Paths
                k: Path(self.tmp.name) / k
                for k in ("media_dir", "imports_dir", "models_dir", "logs_dir", "run_dir")
            },
        )
        with (
            mock.patch.object(cli, "settings", here),
            mock.patch("sys.stdin", io.StringIO(stdin)),
            mock.patch("sys.stdout", out),
            mock.patch("sys.stderr", out),
        ):
            code = cli.main(list(argv))
        return code, out.getvalue()


class TestCli(CliCase):
    def test_add_and_passwd_read_the_password_from_stdin(self):
        self.assertEqual(self.run_cli("add", "bob", stdin="bobs password\n")[0], 0)
        self.assertIsNotNone(accounts.authenticate(self.conn, "bob", "bobs password"))
        self.run_cli("passwd", "bob", stdin="a new password\n")
        self.assertIsNotNone(accounts.authenticate(self.conn, "bob", "a new password"))

    def test_invite_prints_a_link(self):
        code, out = self.run_cli("invite", "--url", "https://reels.example.com/")
        self.assertEqual(code, 0)
        token = out.split("/invite/")[1].split()[0]
        self.assertTrue(accounts.invite_open(self.conn, token))

    def test_invite_without_an_admin_is_explained(self):
        self.conn.execute("UPDATE user SET is_admin = 0")
        code, out = self.run_cli("invite")
        self.assertEqual(code, 1)
        self.assertIn("no admin to invite on behalf of", out)

    def test_mistakes_are_explained(self):
        code, out = self.run_cli("passwd", "nobody", stdin="whatever1\n")
        self.assertEqual(code, 1)
        self.assertIn("no account called nobody", out)

    def test_disabling_someone_revokes_their_tokens(self):
        bob = accounts.create(self.conn, "bob", "bobs password")
        token, _ = accounts.make_token(self.conn, bob, "t", ["read:library"])
        self.assertEqual(self.run_cli("disable", "bob")[0], 0)
        self.assertEqual(accounts.tokens(self.conn), [])
        self.assertIsNone(accounts.find_token(self.conn, token))


class TestTokenCli(CliCase):
    CLI = "app.tokens.__main__"

    def made(self, *argv):
        code, out = self.run_cli("make", *argv)
        self.assertEqual(code, 0, out)
        return out.splitlines()[0]  # the token, alone on stdout, before the note

    def test_an_api_token_is_the_first_admins_and_reads_by_default(self):
        token = self.made("monitor")
        found = must(accounts.find_token(self.conn, token))
        self.assertEqual((found.kind, found.user.username), ("api", "alice"))
        self.assertEqual(found.scopes, {"read:library"})

    def test_scopes_life_and_owner_are_picked(self):
        accounts.create(self.conn, "bob", "bobs password")
        token = self.made("script", "--user", "bob", "--scope", "write:library", "--days", "3")
        found = must(accounts.find_token(self.conn, token))
        self.assertEqual((found.user.username, found.scopes), ("bob", {"write:library"}))
        [t] = accounts.tokens(self.conn)
        self.assertEqual(t["expires_at"] - t["created_at"], 3 * 86400)
        code, out = self.run_cli("make", "x", "--user", "bob", "--scope", "read:admin")
        self.assertEqual(code, 1)
        self.assertIn("Only an admin", out)

    def test_a_worker_token(self):
        token = self.made("desktop", "--worker")
        self.assertTrue(token.startswith("hk_wkr_"))
        self.assertTrue(accounts.worker_token_ok(self.conn, token))
        code, out = self.run_cli("make", "desktop", "--worker", "--scope", "read:library")
        self.assertEqual(code, 1)
        self.assertIn("no scopes", out)

    def test_list_and_revoke(self):
        token = self.made("desktop", "--worker")
        self.made("monitor")
        code, out = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertIn("desktop", out)
        self.assertIn("read:library", out)
        self.assertNotIn(token, out)
        worker_id = next(t["id"] for t in accounts.tokens(self.conn) if t["name"] == "desktop")
        self.assertEqual(self.run_cli("revoke", str(worker_id))[0], 0)
        self.assertFalse(accounts.worker_token_ok(self.conn, token))
        code, out = self.run_cli("revoke", str(worker_id))
        self.assertEqual(code, 1)
        self.assertIn(f"no token {worker_id}", out)

    def test_no_admin_yet(self):
        self.conn.execute("UPDATE user SET is_admin = 0")
        code, out = self.run_cli("make", "monitor")
        self.assertEqual(code, 1)
        self.assertIn("no admin yet", out)


if __name__ == "__main__":
    unittest.main()
