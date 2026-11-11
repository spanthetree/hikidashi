"""The CI workflows (.github/workflows/): pins that have to move together.

Stdlib only, so the YAML is read as text.
"""

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / ".github" / "workflows" / "tests.yml"
PACKAGE = ROOT / "frontend" / "package.json"


class TestE2E(unittest.TestCase):
    def test_the_playwright_image_matches_the_package(self):
        # The image's browsers are built for one Playwright release. With the
        # package on another, every test fails to find its browser (#131).
        tag = re.search(r"container: mcr\.microsoft\.com/playwright:v([\d.]+)-", TESTS.read_text())
        assert tag, "the e2e job runs in mcr.microsoft.com/playwright"
        package = json.loads(PACKAGE.read_text())["devDependencies"]["@playwright/test"]
        self.assertEqual(tag.group(1), package)

    def test_no_browser_install_from_apt(self):
        self.assertNotIn("--with-deps", TESTS.read_text())


if __name__ == "__main__":
    unittest.main()
