import tempfile
import unittest
from pathlib import Path

from app import spa


class TestResolve(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "web"
        (self.root / "_app" / "immutable" / "chunks").mkdir(parents=True)
        (self.root / "index.html").write_text("<!doctype html>")
        (self.root / "icon.svg").write_text("<svg/>")
        (self.root / "_app" / "version.json").write_text("{}")
        (self.root / "_app" / "immutable" / "chunks" / "app.abc123.js").write_text("")
        (Path(self.tmp.name) / "secret.txt").write_text("outside the web root")

    def tearDown(self):
        self.tmp.cleanup()

    def resolve(self, path):
        hit = spa.resolve(self.root, path)
        return (hit[0].relative_to(self.root.resolve()).as_posix(), hit[1]) if hit else None

    def test_the_root_is_the_app(self):
        self.assertEqual(self.resolve(""), ("index.html", spa.REVALIDATE))

    def test_client_routes_get_the_app(self):
        for path in ("item/DTestCode02", "logs", "proposals", "recipes"):
            self.assertEqual(self.resolve(path), ("index.html", spa.REVALIDATE), path)

    def test_a_built_file_is_served_as_itself(self):
        self.assertEqual(self.resolve("icon.svg"), ("icon.svg", spa.REVALIDATE))
        self.assertEqual(self.resolve("_app/version.json"), ("_app/version.json", spa.REVALIDATE))

    def test_hashed_chunks_are_cached_forever(self):
        self.assertEqual(
            self.resolve("_app/immutable/chunks/app.abc123.js"),
            ("_app/immutable/chunks/app.abc123.js", spa.IMMUTABLE),
        )

    def test_a_missing_asset_is_a_404_not_the_app(self):
        self.assertIsNone(self.resolve("_app/immutable/chunks/app.old999.js"))
        self.assertIsNone(self.resolve("icon.svgg"))

    def test_backend_paths_never_fall_back_to_the_app(self):
        for path in ("api", "api/nope", "media", "media/thumbs/x.jpg"):
            self.assertIsNone(self.resolve(path), path)

    def test_no_escape_from_the_web_root(self):
        self.assertIsNone(self.resolve("../secret.txt"))
        self.assertIsNone(self.resolve("_app/../../secret.txt"))

    def test_nothing_built_is_nothing_to_serve(self):
        (self.root / "index.html").unlink()
        self.assertIsNone(self.resolve(""))
        self.assertIsNone(self.resolve("item/abc"))


if __name__ == "__main__":
    unittest.main()
