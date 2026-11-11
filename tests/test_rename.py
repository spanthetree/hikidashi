"""What keeps an install from before the rename to Hikidashi working (#121)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app import accounts
from app.config import _db_path

ROOT = Path(__file__).resolve().parent.parent


class LegacySettingsTest(unittest.TestCase):
    def run_app(self, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        code = "import app, os; print(os.environ.get('HIKIDASHI_PORT'))"
        names = ("REELVAULT_", "HIKIDASHI_")
        clean = {k: v for k, v in os.environ.items() if not k.startswith(names)}
        return subprocess.run(
            [sys.executable, "-c", code],
            env=clean | env,
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )

    def test_an_old_name_stands_in_for_the_new(self) -> None:
        out = self.run_app({"REELVAULT_PORT": "9001"})
        self.assertEqual(out.stdout.strip(), "9001")
        self.assertIn("REELVAULT_PORT", out.stderr)

    def test_the_new_name_wins(self) -> None:
        out = self.run_app({"REELVAULT_PORT": "9001", "HIKIDASHI_PORT": "9002"})
        self.assertEqual(out.stdout.strip(), "9002")
        self.assertEqual(out.stderr, "")


class LegacyDatabaseTest(unittest.TestCase):
    def test_the_old_file_while_there_is_no_new_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            new, old = Path(tmp) / "hikidashi.sqlite3", Path(tmp) / "reelvault.sqlite3"
            self.assertEqual(_db_path(new), new)
            old.touch()
            self.assertEqual(_db_path(new), old)
            new.touch()
            self.assertEqual(_db_path(new), new)

    def test_a_path_set_by_hand_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "reelvault.sqlite3").touch()
            mine = Path(tmp) / "library.sqlite3"
            self.assertEqual(_db_path(mine), mine)


class LegacyTokenTest(unittest.TestCase):
    def test_new_tokens_read_hk(self) -> None:
        self.assertTrue(accounts._new_token(accounts.API).startswith("hk_api_"))
        self.assertTrue(accounts._new_token(accounts.WORKER).startswith("hk_wkr_"))

    def test_old_prefixes_still_check_out(self) -> None:
        for kind, old in ((accounts.API, "rv_api_"), (accounts.WORKER, "rv_wkr_")):
            token = accounts._new_token(kind)
            legacy = old + token[len("hk_xxx_") :]
            self.assertTrue(accounts.well_formed(legacy))
            mistyped = legacy[:-1] + ("0" if legacy[-1] != "0" else "1")
            self.assertFalse(accounts.well_formed(mistyped))


if __name__ == "__main__":
    unittest.main()
