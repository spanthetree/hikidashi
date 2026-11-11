"""The NAS install files in deploy/ (#87): checked here, installed by hand.

Stdlib only, so the YAML is read as text: it is one flat file whose lines
these tests can find.
"""

import json
import re
import tomllib
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UNRAID = ROOT / "deploy" / "unraid"
TRUENAS = ROOT / "deploy" / "truenas" / "hikidashi.yaml"
IMAGES = {"hikidashi-server", "hikidashi-worker", "hikidashi-worker-cuda"}
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def templates() -> dict[str, ET.Element]:
    return {
        p.name: ET.parse(p).getroot()
        for p in sorted(UNRAID.glob("*.xml"))
        if p.name != "ca_profile.xml"
    }


class TestUnraid(unittest.TestCase):
    def test_four_templates(self):
        self.assertEqual(
            sorted(templates()),
            [
                "hikidashi-server-worker.xml",
                "hikidashi-worker-cuda.xml",
                "hikidashi-worker.xml",
                "hikidashi.xml",
            ],
        )

    def test_every_template_mounts_the_same_data(self):
        # The containers meet in /data's SQLite file and nowhere else, so a
        # worker on another path has a database of its own, and no reels.
        for name, t in templates().items():
            data = [c for c in t.iter("Config") if c.get("Target") == "/data"]
            self.assertEqual(len(data), 1, name)
            self.assertEqual(data[0].get("Default"), "/mnt/cache/appdata/hikidashi", name)
            self.assertNotIn("/mnt/user", data[0].text or "", name)

    def test_every_template_runs_as_nobody_and_restarts(self):
        for name, t in templates().items():
            params = t.findtext("ExtraParams") or ""
            self.assertIn("--user 99:100", params, name)
            self.assertIn("--restart=unless-stopped", params, name)

    def test_images_are_published_ones(self):
        for name, t in templates().items():
            repo = t.findtext("Repository") or ""
            image, _, tag = repo.removeprefix("ghcr.io/spanthetree/").partition(":")
            self.assertIn(image, IMAGES, name)
            self.assertEqual(tag, "latest", name)

    def test_template_urls_name_their_own_file(self):
        for name, t in templates().items():
            self.assertTrue((t.findtext("TemplateURL") or "").endswith(f"/deploy/unraid/{name}"))

    def test_the_server_worker_runs_what_compose_runs(self):
        t = templates()["hikidashi-server-worker.xml"]
        self.assertEqual(t.findtext("PostArgs"), "hikidashi worker --log server-worker.log")
        compose = (ROOT / "docker-compose.yml").read_text()
        self.assertIn('command: ["hikidashi", "worker", "--log", "server-worker.log"]', compose)

    def test_the_server_probes_a_route_that_needs_no_sign_in(self):
        params = templates()["hikidashi.xml"].findtext("ExtraParams") or ""
        self.assertIn("/api/auth", params)

    def test_the_cuda_worker_has_the_card(self):
        t = templates()["hikidashi-worker-cuda.xml"]
        self.assertIn("--runtime=nvidia", t.findtext("ExtraParams") or "")
        targets = {c.get("Target") for c in t.iter("Config")}
        self.assertIn("NVIDIA_VISIBLE_DEVICES", targets)

    def test_profile(self):
        root = ET.parse(UNRAID / "ca_profile.xml").getroot()
        self.assertEqual(root.tag, "CommunityApplications")
        self.assertTrue((root.findtext("Profile") or "").strip())


# The stack folders, and the checkout's files each one includes.
LAYOUTS = {
    "everything": ("docker-compose.yml",),
    "server-only": ("docker-compose.yml", "docker-compose.server-only.yml"),
}


class TestComposeManager(unittest.TestCase):
    """The stack folders (#111). The plugin passes every *compose*.yml in a
    stack's folder with -f, so each holds one, and reads no COMPOSE_FILE."""

    def included(self, layout: str) -> list[Path]:
        stack = UNRAID / "compose-manager" / layout
        text = (stack / "docker-compose.yml").read_text()
        paths = re.findall(r"^\s+- (\S+\.yml)$", text, re.MULTILINE)
        return [(stack / p).resolve() for p in paths]

    def test_one_compose_file_per_stack(self):
        for layout in LAYOUTS:
            stack = UNRAID / "compose-manager" / layout
            self.assertEqual([p.name for p in stack.glob("*compose*.yml")], ["docker-compose.yml"])

    def test_each_includes_the_checkouts_files(self):
        for layout, files in LAYOUTS.items():
            self.assertEqual(self.included(layout), [ROOT / f for f in files])

    def test_never_the_remote_worker(self):
        for layout in LAYOUTS:
            self.assertNotIn(ROOT / "docker-compose.worker.yml", self.included(layout))

    def test_compose_env_sets_no_compose_file(self):
        lines = (UNRAID / "compose.env").read_text().splitlines()
        self.assertFalse([ln for ln in lines if ln.lstrip("# ").startswith("COMPOSE_FILE=")])


class TestTrueNAS(unittest.TestCase):
    def setUp(self):
        self.text = TRUENAS.read_text()
        self.lines = [ln for ln in self.text.splitlines() if not ln.lstrip().startswith("#")]

    def test_no_build(self):
        # Install via YAML has no checkout to build from.
        self.assertFalse([ln for ln in self.lines if ln.strip().startswith("build:")])

    def test_images_follow_latest(self):
        """The server and the worker must share a release, so all three images
        name the same tag, and it is never a version to bump."""
        images = re.findall(r"image: ghcr\.io/spanthetree/([\w-]+):(\S+)", "\n".join(self.lines))
        self.assertEqual(len(images), 3)
        for image, tag in images:
            self.assertIn(image, IMAGES)
            self.assertEqual(tag, "latest")

    def test_every_service_shares_the_dataset_as_apps(self):
        body = "\n".join(self.lines)
        self.assertEqual(body.count("- /mnt/tank/apps/hikidashi:/data"), 3)
        self.assertEqual(body.count('user: "568:568"'), 3)


class TestVersion(unittest.TestCase):
    """The version is written once, in pyproject.toml. The release workflow
    reads it there; nothing else carries a copy to bump."""

    def test_uv_lock_agrees(self):
        # Stale after a bump until `uv lock` runs.
        lock = tomllib.loads((ROOT / "uv.lock").read_text())
        ours = [p for p in lock["package"] if p["name"] == "hikidashi"]
        self.assertEqual([p.get("version") for p in ours], [VERSION], "run `uv lock`")

    def test_no_other_copy(self):
        for name in ("frontend/package.json", "frontend/package-lock.json"):
            with self.subTest(name):
                data = json.loads((ROOT / name).read_text())
                self.assertNotIn("version", data)
                self.assertNotIn("version", data.get("packages", {}).get("", {}))
        for name in ("README.md", "deploy/truenas/hikidashi.yaml"):
            with self.subTest(name):
                self.assertNotIn(VERSION, (ROOT / name).read_text())


class TestCompose(unittest.TestCase):
    """The checkout's compose files pull the images the release workflow
    publishes (#77), and build only as a fallback."""

    PUBLISHED = re.compile(
        r"image: ghcr\.io/spanthetree/(?:\$\{HIKIDASHI_WORKER_IMAGE:-)?([\w-]+?)\}?"
        r":\$\{HIKIDASHI_VERSION:-latest\}"
    )

    def images(self, name: str) -> list[str]:
        return self.PUBLISHED.findall((ROOT / name).read_text())

    def test_the_single_host_stack(self):
        self.assertEqual(
            self.images("docker-compose.yml"),
            ["hikidashi-server", "hikidashi-server", "hikidashi-worker"],
        )

    def test_the_remote_worker(self):
        self.assertEqual(self.images("docker-compose.worker.yml"), ["hikidashi-worker-cuda"])

    def test_every_image_is_one_the_workflow_publishes(self):
        workflow = (ROOT / ".github" / "workflows" / "images.yml").read_text()
        published = set(re.findall(r"- image: ([\w-]+)", workflow))
        self.assertEqual(published, IMAGES)

    def test_no_local_only_images(self):
        for name in ("docker-compose.yml", "docker-compose.worker.yml"):
            text = (ROOT / name).read_text()
            self.assertNotIn(":local", text, name)
            self.assertNotIn("pull_policy: never", text, name)


if __name__ == "__main__":
    unittest.main()
