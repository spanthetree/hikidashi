import contextlib
import dataclasses
import importlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from app import cli
from app.config import settings

ROOT = Path(__file__).resolve().parent.parent


class FakeModule:
    def __init__(self, code=0):
        self.code = code
        self.calls = []

    def main(self, argv):
        self.calls.append(argv)
        return self.code


class TestDispatch(unittest.TestCase):
    def run_main(self, argv, code=0):
        fake = FakeModule(code)
        with mock.patch.object(importlib, "import_module", return_value=fake) as imp:
            got = cli.main(argv)
        return got, fake, imp

    def test_flags_pass_through_unchanged(self):
        code, fake, imp = self.run_main(["tag", "--llm", "--force", "--limit", "5"])
        self.assertEqual(code, 0)
        imp.assert_called_once_with("app.tagging.__main__")
        self.assertEqual(fake.calls, [["--llm", "--force", "--limit", "5"]])

    def test_each_command_names_its_module(self):
        for name, module in [
            ("enrich", "app.enrich"),
            ("transcribe", "app.asr"),
            ("ocr", "app.ocr"),
            ("recipe", "app.recipes"),
            ("embed", "app.embed"),
            ("places", "app.places"),
            ("export", "app.export"),
            ("worker", "app.jobs"),
        ]:
            _, _, imp = self.run_main([name])
            imp.assert_called_once_with(module + ".__main__")

    def test_import_gets_a_program_name_like_sys_argv(self):
        _, fake, _ = self.run_main(["import", "export.zip"])
        self.assertEqual(fake.calls, [["hikidashi import", "export.zip"]])

    def test_exit_code_propagates(self):
        code, _, _ = self.run_main(["embed"], code=130)
        self.assertEqual(code, 130)

    def test_help_unknown_and_nothing(self):
        with mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            self.assertEqual(cli.main(["--help"]), 0)
            self.assertEqual(cli.main([]), 2)
            self.assertEqual(cli.main(["frobnicate"]), 2)


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name)
        patched = dataclasses.replace(settings, run_dir=self.run_dir)
        self.patches: list[Any] = [
            mock.patch.object(cli, "settings", patched),
            mock.patch("sys.stdout"),
            mock.patch("sys.stderr"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def run_pipeline(self, argv, codes=None, tesseract=True):
        codes = codes or {}
        ran = []

        def stage(name, flags):
            ran.append([name, *flags])
            return codes.get(name, 0)

        which = "/usr/bin/tesseract" if tesseract else None
        with (
            mock.patch.object(cli, "run_stage", side_effect=stage),
            mock.patch.object(shutil, "which", return_value=which),
        ):
            code = cli.main(["pipeline", *argv])
        return code, ran

    def test_runs_every_stage_in_order(self):
        code, ran = self.run_pipeline([])
        self.assertEqual(code, 0)
        self.assertEqual(
            ran,
            [
                ["enrich", "--media"],
                ["transcribe"],
                ["ocr"],
                ["tag", "--llm"],
                ["recipe"],
                ["embed"],
                ["places"],
            ],
        )

    def test_imports_first_when_given_an_export(self):
        _, ran = self.run_pipeline(["export.zip"])
        self.assertEqual(ran[0], ["import", "export.zip"])
        self.assertEqual(len(ran), 8)

    def test_stops_at_the_first_failure_with_its_code(self):
        code, ran = self.run_pipeline([], codes={"transcribe": 1})
        self.assertEqual(code, 1)
        self.assertEqual(ran, [["enrich", "--media"], ["transcribe"]])

    def test_skips_ocr_without_tesseract(self):
        code, ran = self.run_pipeline([], tesseract=False)
        self.assertEqual(code, 0)
        self.assertNotIn(["ocr"], ran)
        self.assertEqual(ran[-1], ["places"])

    def test_a_second_pipeline_does_not_start(self):
        with cli.pipeline_lock(self.run_dir):
            code, ran = self.run_pipeline([])
        self.assertEqual(code, 1)
        self.assertEqual(ran, [])

    def test_steps_runs_only_those_in_pipeline_order(self):
        code, ran = self.run_pipeline(["export.zip", "--steps", "places,enrich"])
        self.assertEqual(code, 0)
        self.assertEqual(ran, [["import", "export.zip"], ["enrich", "--media"], ["places"]])

    def test_steps_refuses_an_unknown_one(self):
        with self.assertRaises(SystemExit):
            self.run_pipeline(["--steps", "enrich,fold"])

    def test_stage_runs_its_module_in_a_child(self):
        done: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess([], 3)
        with mock.patch.object(subprocess, "run", return_value=done) as run:
            self.assertEqual(cli.run_stage("tag", ["--llm"]), 3)
        self.assertEqual(run.call_args.args[0][1:], ["-m", "app.tagging", "--llm"])


class TestForAServer(unittest.TestCase):
    """A desktop with HIKIDASHI_SERVER set, running stages by hand (#86)."""

    def run_cli(self, *argv):
        patched = dataclasses.replace(settings, server="http://nas.lan:8080", worker_name="desktop")
        calls = []

        def worker(args):
            calls.append(args)
            return 0

        with (
            mock.patch.object(cli, "settings", patched),
            mock.patch("app.jobs.__main__.main", side_effect=worker),
            mock.patch.object(cli, "run_command") as local,
            mock.patch("sys.stdout"),
            mock.patch("sys.stderr"),
        ):
            code = cli.main(list(argv))
        local.assert_not_called()  # never the empty database here
        return code, calls

    def test_a_stage_is_claimed_from_the_server(self):
        for argv, stages in [
            (["transcribe"], "asr"),
            (["ocr"], "ocr"),
            (["tag", "--llm"], "tag"),
            (["recipe"], "recipe"),
            (["pipeline"], "asr,ocr,tag,recipe"),
        ]:
            code, calls = self.run_cli(*argv)
            self.assertEqual(code, 0)
            # Its own name, so it never takes back an always-on worker's reels.
            self.assertEqual(
                calls,
                [
                    [
                        "--once",
                        "--stages",
                        stages,
                        "--name",
                        "desktop-once",
                        "--log",
                        "worker-once.log",
                    ]
                ],
            )

    def test_the_servers_commands_are_refused(self):
        for cmd in ("import", "enrich", "embed", "places", "user", "token", "export"):
            code, calls = self.run_cli(cmd)
            self.assertEqual((code, calls), (2, []), cmd)

    def test_a_flag_the_worker_cannot_take_is_refused(self):
        code, calls = self.run_cli("transcribe", "--limit", "5")
        self.assertEqual((code, calls), (2, []))


class TestLock(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name) / "run"

    def tearDown(self):
        self.tmp.cleanup()

    def test_held_lock_names_its_holder(self):
        with cli.pipeline_lock(self.run_dir):
            with self.assertRaises(cli.Busy) as caught:
                with cli.pipeline_lock(self.run_dir):
                    pass
            self.assertEqual(str(caught.exception), str(os.getpid()))
            self.assertEqual(cli.pipeline_holder(self.run_dir), str(os.getpid()))

    def test_released_on_exit_and_on_error(self):
        with self.assertRaises(RuntimeError):
            with cli.pipeline_lock(self.run_dir):
                raise RuntimeError
        self.assertIsNone(cli.pipeline_holder(self.run_dir))


class TestModelsOnDisk(unittest.TestCase):
    def test_whisper_names_map_onto_their_repos(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = Path(tmp)
            (models / "models--Systran--faster-distil-whisper-small.en/snapshots/abc").mkdir(
                parents=True
            )
            self.assertTrue(cli.whisper_on_disk(models, "distil-small.en"))
            self.assertFalse(cli.whisper_on_disk(models, "distil-large-v3"))
            self.assertFalse(cli.whisper_on_disk(models, "small.en"))

    def test_embedder(self):
        with tempfile.TemporaryDirectory() as tmp:
            models = Path(tmp)
            self.assertFalse(cli.embedder_on_disk(models, "google/embeddinggemma-300m"))
            (
                models / "fastembed/models--onnx-community--embeddinggemma-300m-ONNX/snapshots/abc"
            ).mkdir(parents=True)
            self.assertTrue(cli.embedder_on_disk(models, "google/embeddinggemma-300m"))


class TestStatusWorkers(unittest.TestCase):
    def lines(self, conn, now):
        import io

        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            cli.print_workers(conn, now=now)
        return out.getvalue()

    def test_off_looks_different_from_broken(self):
        from app.db import open_db
        from app.jobs.queue import STALE, LocalJobs

        with tempfile.TemporaryDirectory() as d:
            conn = open_db(Path(d) / "db.sqlite3")
            self.assertIn("none has checked in", self.lines(conn, 0))
            t = 1_000_000.0
            up = LocalJobs(conn, "desktop", clock=lambda: t)
            up.check_in(stages=("asr",), started_at=t, doing="asr", problems={"tag": "no Ollama"})
            off = LocalJobs(conn, "nas", clock=lambda: t)
            off.check_in(stages=(), started_at=t)
            off.check_out()
            out = self.lines(conn, t + 5)
            self.assertRegex(out, r"desktop +running · asr · checked in 5 s ago, transcribing")
            self.assertIn("tag cannot run: no Ollama", out)
            self.assertRegex(out, r"nas +stopped 5 s ago")
            out = self.lines(conn, t + STALE + 60)
            self.assertRegex(out, r"desktop +not responding: last checked in 3 min ago")
            self.assertNotIn("no Ollama", out)
            conn.close()

    def test_embedding_from_captions_is_said(self):
        import io
        import time

        from app.db import open_db
        from app.jobs.queue import LocalJobs

        def queue_lines(conn):
            out = io.StringIO()
            with mock.patch("sys.stdout", out):
                cli.print_queue(conn)
            return out.getvalue()

        with tempfile.TemporaryDirectory() as d:
            conn = open_db(Path(d) / "db.sqlite3")
            nas = LocalJobs(conn, "nas")
            nas.check_in(stages=("enrich", "embed"), started_at=time.time())
            self.assertIn("embedded from their captions", queue_lines(conn))
            LocalJobs(conn, "desktop").check_in(
                stages=("asr", "ocr", "tag"), started_at=time.time()
            )
            self.assertNotIn("captions", queue_lines(conn))
            conn.close()

    def test_the_server_image_leaves_the_models_to_the_workers(self):
        # No faster-whisper and no OLLAMA_URL, as in the app container (#86):
        # no profile resolved here, and no Ollama asked.
        import io
        import os

        with tempfile.TemporaryDirectory() as d:
            patched = dataclasses.replace(
                settings,
                db_path=Path(d) / "db.sqlite3",
                run_dir=Path(d) / "run",
                models_dir=Path(d) / "models",
                media_dir=Path(d) / "media",
                imports_dir=Path(d) / "imports",
                logs_dir=Path(d) / "logs",
            )
            env = {k: v for k, v in os.environ.items() if k != "OLLAMA_URL"}
            out = io.StringIO()
            with (
                mock.patch.object(cli, "settings", patched),
                mock.patch.object(cli, "server_up", return_value=False),
                mock.patch.object(cli, "installed", side_effect=lambda m: m == "fastembed"),
                mock.patch.dict(os.environ, env, clear=True),
                mock.patch("app.tagging.llm.Ollama") as ollama,
                mock.patch("sys.stdout", out),
            ):
                self.assertEqual(cli.main(["status"]), 0)
            self.assertIn("embedder    google/embeddinggemma-300m", out.getvalue())
            self.assertIn("Whisper and Ollama are the workers'", out.getvalue())
            self.assertNotIn("profile", out.getvalue())
            ollama.assert_not_called()

    def test_ago(self):
        self.assertEqual(
            [cli.ago(s) for s in (5, 600, 7200, 3 * 86400)], ["5 s", "10 min", "2 h", "3 days"]
        )


# A stand-in for docker or uv: records its arguments, one call per line. The
# docker one answers `container inspect` from STUB_CONTAINER (absent, stopped
# or running), and for the container's mounts with STUB_DATA.
STUB = """#!/bin/sh
echo "$(basename "$0") $*" >>"$STUB_LOG"
if [ "$(basename "$0")" = docker ] && [ "$1" = container ]; then
    case $* in *Mounts*) echo "${STUB_DATA:-}"; exit 0 ;; esac
    case ${STUB_CONTAINER:-absent} in
        absent) exit 1 ;;
        running) echo true ;;
        *) echo false ;;
    esac
fi
exit 0
"""

SYSTEM_PATH = "/usr/bin:/bin"


@unittest.skipUnless(shutil.which("sh"), "needs a POSIX sh")
class TestWrapper(unittest.TestCase):
    """./hikidashi, run for real against fake docker and uv on PATH."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copy(ROOT / "hikidashi", self.root / "hikidashi")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "calls"
        self.log.touch()

    def tearDown(self):
        self.tmp.cleanup()

    def stub(self, *names):
        for name in names:
            path = self.bin / name
            path.write_text(STUB)
            path.chmod(0o755)

    def run_wrapper(self, *args, **env):
        full = {
            "PATH": f"{self.bin}:{SYSTEM_PATH}",
            "STUB_LOG": str(self.log),
            "HOME": self.tmp.name,
            **env,
        }
        proc = subprocess.run(
            ["sh", str(self.root / "hikidashi"), *args],
            capture_output=True,
            text=True,
            env=full,
            stdin=subprocess.DEVNULL,
            timeout=30,
        )
        return proc

    def calls(self):
        return [
            line
            for line in self.log.read_text().splitlines()
            if not line.startswith("docker container inspect")
        ]

    # mode selection

    def test_uv_flag(self):
        self.stub("uv", "docker")
        proc = self.run_wrapper("--uv", "enrich", "--media", STUB_CONTAINER="running")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.calls(), ["uv run hikidashi enrich --media"])

    def test_docker_flag(self):
        self.stub("uv", "docker")
        proc = self.run_wrapper("--docker", "tag", "--llm", STUB_CONTAINER="running")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # No terminal here, as under cron: exec needs -T.
        self.assertEqual(self.calls(), ["docker compose exec -T worker hikidashi tag --llm"])

    def test_mode_from_the_environment(self):
        self.stub("uv", "docker")
        self.run_wrapper("embed", HIKIDASHI_MODE="uv", STUB_CONTAINER="running")
        self.assertEqual(self.calls(), ["uv run hikidashi embed"])

    def test_flag_beats_the_environment(self):
        self.stub("uv", "docker")
        self.run_wrapper("--uv", "embed", HIKIDASHI_MODE="docker")
        self.assertEqual(self.calls(), ["uv run hikidashi embed"])

    def test_auto_picks_docker_when_the_container_exists(self):
        self.stub("uv", "docker")
        self.run_wrapper("embed", STUB_CONTAINER="running")
        self.assertEqual(self.calls(), ["docker compose exec -T app hikidashi embed"])

    def test_auto_picks_uv_when_there_is_no_container(self):
        self.stub("uv", "docker")
        self.run_wrapper("embed")
        self.assertEqual(self.calls(), ["uv run hikidashi embed"])

    @unittest.skipIf(shutil.which("uv", path=SYSTEM_PATH), "uv is installed system-wide")
    def test_auto_picks_docker_without_uv(self):
        self.stub("docker")
        self.run_wrapper("embed", STUB_CONTAINER="running")
        self.assertEqual(self.calls(), ["docker compose exec -T app hikidashi embed"])

    def test_bad_mode(self):
        self.stub("uv", "docker")
        proc = self.run_wrapper("embed", HIKIDASHI_MODE="podman")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("uv or docker", proc.stderr)

    def test_unknown_command(self):
        self.stub("uv", "docker")
        proc = self.run_wrapper("--uv", "frobnicate")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(self.calls(), [])

    # docker

    def test_docker_lifecycle(self):
        self.stub("docker")
        for args, want in [
            (["start"], "docker compose up -d"),
            (["start", "--build", "--ai"], "docker compose --profile ai up -d --build"),
            (["stop"], "docker compose --profile ai stop"),
            (["restart"], "docker compose up -d --force-recreate"),
            (["logs"], "docker compose logs app"),
            (["logs", "-f"], "docker compose logs -f app"),
            (["logs", "worker", "-f"], "docker compose logs -f worker"),
            (["logs", "server-worker"], "docker compose logs server-worker"),
        ]:
            self.log.write_text("")
            proc = self.run_wrapper("--docker", *args)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(self.calls(), [want])

    def test_docker_import_path_becomes_the_container_path(self):
        self.stub("docker")
        (self.root / "data/imports").mkdir(parents=True)
        (self.root / "data/imports/export.zip").touch()
        proc = self.run_wrapper(
            "--docker", "import", "data/imports/export.zip", STUB_CONTAINER="running"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            self.calls(), ["docker compose exec -T app hikidashi import /data/imports/export.zip"]
        )

    def test_docker_import_from_outside_data_is_refused(self):
        self.stub("docker")
        (self.root / "export.zip").touch()
        proc = self.run_wrapper(
            "--docker", "pipeline", str(self.root / "export.zip"), STUB_CONTAINER="running"
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("data/imports", proc.stderr)
        self.assertEqual(self.calls(), [])

    def test_docker_export_goes_under_data_made_if_need_be(self):
        self.stub("docker")
        proc = self.run_wrapper(
            "--docker",
            "export",
            "markdown",
            "--user",
            "bob",
            "--thumbs",
            "data/exports/vault",
            STUB_CONTAINER="running",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((self.root / "data/exports/vault").is_dir())
        self.assertEqual(
            self.calls(),
            [
                "docker compose exec -T app hikidashi export markdown --user bob --thumbs "
                "/data/exports/vault"
            ],
        )

    def test_docker_export_to_a_file_makes_its_folder(self):
        self.stub("docker")
        proc = self.run_wrapper(
            "--docker",
            "export",
            "recipes",
            "--format",
            "paprika",
            "data/exports/mine.paprikarecipes",
            STUB_CONTAINER="running",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((self.root / "data/exports").is_dir())
        self.assertFalse((self.root / "data/exports/mine.paprikarecipes").exists())
        self.assertEqual(
            self.calls(),
            [
                "docker compose exec -T app hikidashi export recipes --format paprika "
                "/data/exports/mine.paprikarecipes"
            ],
        )

    def test_docker_paths_follow_hikidashi_data(self):
        self.stub("docker")
        data = self.root / "pool/appdata/hikidashi"
        (data / "imports").mkdir(parents=True)
        (data / "imports/export.zip").touch()
        proc = self.run_wrapper(
            "--docker",
            "import",
            str(data / "imports/export.zip"),
            STUB_CONTAINER="running",
            HIKIDASHI_DATA=str(data),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = self.run_wrapper(
            "--docker",
            "export",
            "json",
            str(data / "exports/library.json"),
            STUB_CONTAINER="running",
            HIKIDASHI_DATA=str(data),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((data / "exports").is_dir())
        self.assertEqual(
            self.calls(),
            [
                "docker compose exec -T app hikidashi import /data/imports/export.zip",
                "docker compose exec -T app hikidashi export json /data/exports/library.json",
            ],
        )

    def test_docker_paths_follow_the_containers_mount_over_the_environment(self):
        # As when HIKIDASHI_DATA is in .env, which compose reads and this
        # script does not.
        self.stub("docker")
        data = self.root / "pool"
        (data / "imports").mkdir(parents=True)
        (data / "imports/export.zip").touch()
        (self.root / "data/imports").mkdir(parents=True)
        (self.root / "data/imports/export.zip").touch()
        proc = self.run_wrapper(
            "--docker",
            "import",
            str(data / "imports/export.zip"),
            STUB_CONTAINER="running",
            STUB_DATA=str(data),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = self.run_wrapper(
            "--docker",
            "import",
            "data/imports/export.zip",
            STUB_CONTAINER="running",
            STUB_DATA=str(data),
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn(f"outside {data.resolve()}", proc.stderr)
        self.assertEqual(
            self.calls(), ["docker compose exec -T app hikidashi import /data/imports/export.zip"]
        )

    def test_docker_export_outside_data_is_refused(self):
        self.stub("docker")
        proc = self.run_wrapper(
            "--docker", "export", "markdown", str(self.root / "vault"), STUB_CONTAINER="running"
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("data/exports", proc.stderr)
        self.assertFalse((self.root / "vault").exists())
        self.assertEqual(self.calls(), [])

    def test_docker_worker_runs_in_the_app_container(self):
        self.stub("docker")
        proc = self.run_wrapper("--docker", "worker", "--once", STUB_CONTAINER="running")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.calls(), ["docker compose exec -T app hikidashi worker --once"])

    def test_docker_stages_run_where_their_tools_are(self):
        # The worker image has ASR, OCR and Ollama's URL; the server image the
        # rest (#86).
        self.stub("docker")
        for cmd, service in [
            ("transcribe", "worker"),
            ("ocr", "worker"),
            ("tag", "worker"),
            ("recipe", "worker"),
            ("enrich", "app"),
            ("embed", "app"),
            ("places", "app"),
            ("user", "app"),
            ("token", "app"),
        ]:
            self.log.write_text("")
            proc = self.run_wrapper("--docker", cmd, STUB_CONTAINER="running")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(self.calls(), [f"docker compose exec -T {service} hikidashi {cmd}"])

    def test_docker_pipeline_runs_in_three_parts(self):
        self.stub("docker")
        (self.root / "data/imports").mkdir(parents=True)
        (self.root / "data/imports/export.zip").touch()
        proc = self.run_wrapper(
            "--docker", "pipeline", "data/imports/export.zip", STUB_CONTAINER="running"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            self.calls(),
            [
                "docker compose exec -T app hikidashi pipeline /data/imports/export.zip "
                "--steps=enrich",
                "docker compose exec -T worker hikidashi pipeline "
                "--steps=transcribe,ocr,tag,recipe",
                "docker compose exec -T app hikidashi pipeline --steps=embed,places",
            ],
        )

    def test_a_server_without_a_worker_says_where_stages_run(self):
        self.stub("docker")
        # The app's container runs; there is no worker container at all.
        stub = self.bin / "docker"
        stub.write_text(
            STUB.replace(
                "case ${STUB_CONTAINER:-absent} in",
                "case $* in *hikidashi-worker*) exit 1 ;; esac\n"
                "    case ${STUB_CONTAINER:-absent} in",
            )
        )
        proc = self.run_wrapper("--docker", "transcribe", STUB_CONTAINER="running")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("no worker container here", proc.stderr)
        self.assertIn("HIKIDASHI_SERVER", proc.stderr)

    def test_logs_takes_only_follow_and_a_log(self):
        self.stub("docker")
        proc = self.run_wrapper("--docker", "logs", "ollama")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("worker", proc.stderr)

    def test_docker_command_needs_the_container_running(self):
        self.stub("docker")
        proc = self.run_wrapper("--docker", "embed", STUB_CONTAINER="stopped")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("./hikidashi start", proc.stderr)

    # uv

    def test_uv_stop_when_not_running(self):
        self.stub("uv")
        proc = self.run_wrapper("--uv", "stop")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("not running", proc.stdout)

    def test_uv_stale_pidfile_is_cleared(self):
        self.stub("uv")
        run = self.root / "data/run"
        run.mkdir(parents=True)
        # Our own shell: alive, but not a uvicorn, so the pid has been reused.
        (run / "server.pid").write_text(f"{os.getpid()}\n")
        proc = self.run_wrapper("--uv", "stop")
        self.assertIn("not running", proc.stdout)
        self.assertFalse((run / "server.pid").exists())

    def test_uv_status_when_not_running(self):
        self.stub("uv")
        proc = self.run_wrapper("--uv", "status")
        self.assertEqual(proc.returncode, 3)
        self.assertIn("not running", proc.stdout)
        self.assertEqual(self.calls(), ["uv run hikidashi status"])

    def test_uv_start_starts_the_worker_beside_the_server_and_stop_stops_both(self):
        # A uv that keeps running, as uvicorn and the worker do, under its own
        # command line: the wrapper knows its processes by it.
        path = self.bin / "uv"
        path.write_text(
            '#!/bin/sh\necho "uv $*" >>"$STUB_LOG"\n'
            'case $2 in uvicorn | hikidashi) [ "$3" = status ] ||\n'
            "  while :; do sleep 1; done ;;\nesac\n"
        )
        path.chmod(0o755)
        (self.root / "app/web").mkdir(parents=True)
        (self.root / "app/web/index.html").touch()
        run = self.root / "data/run"
        self.addCleanup(self.kill_from, run / "server.pid", run / "worker.pid")

        proc = self.run_wrapper("--uv", "start", HIKIDASHI_LOCAL_STAGES="asr,ocr")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("worker started", proc.stdout)
        self.assertIn("stages: asr,ocr", proc.stdout)
        calls = self.calls()
        self.assertIn("uv run hikidashi worker", calls)
        self.assertTrue(any(c.startswith("uv run uvicorn app.api.main:app") for c in calls))

        proc = self.run_wrapper("--uv", "status")
        self.assertEqual(proc.returncode, 0)
        self.assertRegex(proc.stdout, r"worker +running \(pid \d+\)")

        proc = self.run_wrapper("--uv", "start")
        self.assertIn("worker already running", proc.stdout)

        proc = self.run_wrapper("--uv", "stop", HIKIDASHI_STOP_TIMEOUT="5")
        self.assertIn("worker stopped", proc.stdout)
        self.assertIn("server stopped", proc.stdout)
        self.assertFalse((run / "worker.pid").exists())

    def kill_from(self, *pidfiles):
        for f in pidfiles:
            with contextlib.suppress(OSError, ValueError):
                os.killpg(int(f.read_text()), 9)

    # .env under uv

    def env_uv(self):
        """A uv that logs the env file it was handed, and that keeps running."""
        path = self.bin / "uv"
        path.write_text(
            '#!/bin/sh\necho "uv $* [${UV_ENV_FILE:-}]" >>"$STUB_LOG"\n'
            'case "$2 $3" in "uvicorn "* | "hikidashi worker")\n'
            "  while :; do sleep 1; done ;;\nesac\n"
        )
        path.chmod(0o755)

    def test_uv_commands_read_dot_env(self):
        # Compose reads .env, uv does not: without this, HIKIDASHI_AUTH=local
        # there would start a library with no sign-in under uv.
        self.env_uv()
        (self.root / ".env").write_text("HIKIDASHI_AUTH=local\n")
        proc = self.run_wrapper("--uv", "embed")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.calls(), ["uv run hikidashi embed [.env]"])

    def test_uv_without_dot_env_or_with_its_own_file(self):
        self.env_uv()
        self.run_wrapper("--uv", "embed")
        (self.root / ".env").touch()
        self.run_wrapper("--uv", "embed", UV_ENV_FILE="other.env")
        self.assertEqual(
            self.calls(), ["uv run hikidashi embed []", "uv run hikidashi embed [other.env]"]
        )

    def test_docker_leaves_dot_env_to_compose(self):
        self.stub("docker")
        self.env_uv()
        (self.root / ".env").touch()
        self.run_wrapper("--docker", "embed", STUB_CONTAINER="running")
        self.assertNotIn("UV_ENV_FILE", (self.root / "calls").read_text())

    def test_uv_start_takes_its_own_settings_from_dot_env_under_the_shells(self):
        self.env_uv()
        (self.root / "app/web").mkdir(parents=True)
        (self.root / "app/web/index.html").touch()
        (self.root / ".env").write_text(
            "# a comment\n"
            "HIKIDASHI_PORT=9000\n"
            'export HIKIDASHI_PORT="9123"  # the last one counts\n'
            "HIKIDASHI_LOGS=elsewhere/logs\n"
            "HIKIDASHI_HOST=127.0.0.1\n"
            "HIKIDASHI_LOCAL_STAGES=tag,recipe\n"
        )
        run = self.root / "data/run"
        self.addCleanup(self.kill_from, run / "server.pid", run / "worker.pid")

        proc = self.run_wrapper("--uv", "start", HIKIDASHI_HOST="0.0.0.0")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("at http://0.0.0.0:9123", proc.stdout)
        self.assertIn("stages: tag,recipe", proc.stdout)
        self.assertIn("log: elsewhere/logs/server.log", proc.stdout)
        self.assertTrue((self.root / "elsewhere/logs/server.out").exists())
        self.assertIn(
            "uv run uvicorn app.api.main:app --host 0.0.0.0 --port 9123 --workers 1 [.env]",
            self.calls(),
        )
        self.run_wrapper("--uv", "stop", HIKIDASHI_STOP_TIMEOUT="5")

    def test_uv_start_refuses_docker_flags(self):
        self.stub("uv")
        proc = self.run_wrapper("--uv", "start", "--ai")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
