"""Exports (#56): recipes as schema.org JSON-LD and the bundles recipe managers
import, run against the seeded library (tests/seed.py). The seed's accounts need
argon2-cffi; skipped without it."""

import dataclasses
import gzip
import importlib.util
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any, ClassVar
from unittest import mock

from app import export
from app.db import open_db
from app.export import recipes as fmt
from tests import seed

HAS_ARGON2 = importlib.util.find_spec("argon2") is not None


@unittest.skipUnless(HAS_ARGON2, "argon2-cffi not installed")
class SeededLibrary(unittest.TestCase):
    seed: ClassVar[seed.Seed]
    tmp: ClassVar[tempfile.TemporaryDirectory[str]]
    conn: ClassVar[sqlite3.Connection]

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.seed = seed.build(Path(cls.tmp.name))
        cls.conn = open_db(cls.seed.db)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def reels(self, name="alice"):
        return export.library(self.conn, self.seed.users[name])

    def carbonara(self, name="alice") -> dict[str, Any]:
        return next(r for r in self.reels(name) if r["shortcode"] == seed.SHARED.code)


class TestLibrary(SeededLibrary):
    def test_only_their_own_reels(self):
        self.assertEqual({r["shortcode"] for r in self.reels()}, {r.code for r in seed.ALICE_REELS})
        self.assertEqual(
            {r["shortcode"] for r in self.reels("bob")}, {r.code for r in seed.BOB_REELS}
        )

    def test_newest_save_first(self):
        saved = [r["saved_at"] for r in self.reels()]
        self.assertEqual(saved, sorted(saved, reverse=True))

    def test_what_each_reel_carries(self):
        r = self.carbonara()
        self.assertEqual(r["author"], "chef.marco")
        self.assertEqual(r["kind"], "reel")
        self.assertEqual(r["collections"], ["Dinners"])
        self.assertIn("pasta", r["hashtags"])
        self.assertEqual(r["recipe"]["title"], "Carbonara")

    def test_one_reel(self):
        uid = self.seed.users["bob"]
        self.assertEqual(len(export.library(self.conn, uid, seed.SHARED.code)), 1)
        self.assertEqual(export.library(self.conn, uid, "ALICEramen01"), [])

    def test_a_hidden_recipe_is_not_one(self):
        from app.recipes import service as recipes

        uid = self.seed.users["bob"]
        found = recipes.current(self.conn, seed.SHARED.code, user_id=uid)
        recipes.save_user(
            self.conn,
            seed.SHARED.code,
            recipes.from_dict(found or {}),
            hidden=True,
            user_id=uid,
        )
        try:
            self.assertIsNone(self.carbonara("bob")["recipe"])
            self.assertIsNotNone(self.carbonara("alice")["recipe"])
        finally:
            recipes.revert(self.conn, seed.SHARED.code, user_id=uid)


class TestHelpers(unittest.TestCase):
    def test_duration(self):
        self.assertEqual(fmt.duration(20), "PT20M")
        self.assertEqual(fmt.duration(90), "PT1H30M")
        self.assertEqual(fmt.duration(seconds=45), "PT45S")
        self.assertIsNone(fmt.duration(0))

    def test_ingredient_line(self):
        line = export.ingredient_line(
            {"item": "pecorino", "quantity": "50", "unit": "g", "note": "finely grated"}
        )
        self.assertEqual(line, "50 g pecorino, finely grated")
        self.assertEqual(export.ingredient_line({"item": "salt"}), "salt")

    def test_first_line_drops_hashtags_and_trims(self):
        self.assertEqual(
            export.first_line("#pasta Weeknight carbonara #dinner\nmore"), "Weeknight carbonara"
        )
        self.assertEqual(export.first_line("x" * 100, 10), "xxxxxxxxx…")
        self.assertIsNone(export.first_line("#only #tags"))

    def test_slug(self):
        self.assertEqual(fmt.slug("Weeknight Carbonara!"), "weeknight-carbonara")
        self.assertEqual(fmt.slug("???"), "recipe")

    def test_script_tag_cannot_be_closed_early(self):
        tag = fmt.script_tag({"name": "</script><script>alert(1)</script>"})
        self.assertEqual(tag.count("</script>"), 1)
        self.assertTrue(tag.endswith("</script>"))


class TestJsonLd(SeededLibrary):
    def test_a_schema_org_recipe(self):
        doc = fmt.jsonld(self.carbonara(), base_url="https://rv.example/")
        self.assertEqual(doc["@context"], "https://schema.org")
        self.assertEqual(doc["@type"], "Recipe")
        self.assertEqual(doc["name"], "Carbonara")
        self.assertEqual(doc["url"], seed.R + seed.SHARED.code + "/")
        self.assertEqual(doc["totalTime"], "PT20M")
        self.assertEqual(doc["recipeYield"], "2 servings")
        self.assertEqual(doc["recipeIngredient"][0], "200 g spaghetti")
        self.assertIn("50 g pecorino, finely grated", doc["recipeIngredient"])
        self.assertEqual(
            [s["text"] for s in doc["recipeInstructions"]],
            [
                "Boil the pasta.",
                "Crisp the guanciale.",
                "Off the heat, toss with the eggs and cheese.",
            ],
        )
        self.assertTrue(
            all(s["@type"] == "HowToStep" and s["name"] for s in doc["recipeInstructions"])
        )
        self.assertEqual(doc["author"]["url"], "https://www.instagram.com/chef.marco/")
        self.assertEqual(
            doc["image"], [f"https://rv.example/media/thumbs/sh/{seed.SHARED.code}.png"]
        )
        self.assertNotIn("keywords", doc)  # hashtags would become tags in Mealie
        self.assertEqual(doc["video"]["@type"], "VideoObject")
        self.assertEqual(doc["video"]["embedUrl"], doc["url"])
        self.assertEqual(doc["video"]["duration"], "PT2S")
        self.assertTrue(doc["video"]["contentUrl"].startswith("https://rv.example/media/"))
        self.assertIsInstance(doc["description"], str)

    def test_no_base_url_no_links_to_this_server(self):
        doc = fmt.jsonld(self.carbonara())
        self.assertNotIn("image", doc)
        self.assertNotIn("contentUrl", doc["video"])

    def test_graph(self):
        doc = fmt.graph(export.recipes_of(self.reels()))
        self.assertEqual(doc["@context"], "https://schema.org")
        self.assertEqual([d["@type"] for d in doc["@graph"]], ["Recipe"])
        self.assertNotIn("@context", doc["@graph"][0])

    def test_folder_of_files(self):
        with tempfile.TemporaryDirectory() as out:
            n = fmt.write_files(export.recipes_of(self.reels()), Path(out))
            files = list(Path(out).iterdir())
            self.assertEqual(n, 1)
            self.assertEqual([f.name for f in files], [f"carbonara-{seed.SHARED.code}.json"])
            self.assertEqual(json.loads(files[0].read_text())["name"], "Carbonara")


class TestBundles(SeededLibrary):
    def test_nextcloud_cookbook_zip(self):
        buf = io.BytesIO()
        self.assertEqual(fmt.nextcloud(export.recipes_of(self.reels()), self.seed.media, buf), 1)
        with zipfile.ZipFile(buf) as z:
            names = sorted(z.namelist())
            self.assertEqual(names, ["recipes/Carbonara/full.png", "recipes/Carbonara/recipe.json"])
            doc = json.loads(z.read("recipes/Carbonara/recipe.json"))
        self.assertEqual(doc["recipeYield"], 2)
        # What Tandoor's importer reads without checking.
        for key in ("name", "description", "recipeYield", "recipeIngredient", "recipeInstructions"):
            self.assertIn(key, doc)
        self.assertEqual(doc["name"], "Carbonara")

    def test_names_are_distinct_and_folder_safe(self):
        r = self.carbonara()
        twins = [
            {**r, "recipe": {**r["recipe"], "title": "Pasta (quick) / easy"}},
            {**r, "recipe": {**r["recipe"], "title": "Pasta (quick) / easy"}},
            {**r, "author": None, "recipe": {**r["recipe"], "title": "Pasta (quick) / easy"}},
        ]
        names = fmt._names(twins)
        self.assertEqual(
            names, ["Pasta quick easy", "Pasta quick easy by chef.marco", "Pasta quick easy 2"]
        )

    def test_a_recipe_with_no_servings_still_has_a_yield(self):
        r = self.carbonara()
        r = {**r, "recipe": {**r["recipe"], "servings": None}}
        buf = io.BytesIO()
        fmt.nextcloud([r], self.seed.media, buf)
        with zipfile.ZipFile(buf) as z:
            self.assertEqual(json.loads(z.read("recipes/Carbonara/recipe.json"))["recipeYield"], 1)

    def test_paprika(self):
        buf = io.BytesIO()
        self.assertEqual(fmt.paprika(export.recipes_of(self.reels()), self.seed.media, buf), 1)
        with zipfile.ZipFile(buf) as z:
            self.assertEqual(z.namelist(), ["Carbonara.paprikarecipe"])
            doc = json.loads(gzip.decompress(z.read("Carbonara.paprikarecipe")))
        self.assertEqual(doc["name"], "Carbonara")
        self.assertEqual(doc["ingredients"].splitlines()[0], "200 g spaghetti")
        self.assertEqual(doc["directions"].split("\n\n")[0], "Boil the pasta.")
        self.assertEqual(doc["servings"], "2 servings")
        self.assertEqual(doc["total_time"], "20 min")
        self.assertEqual(doc["source_url"], seed.R + seed.SHARED.code + "/")
        self.assertEqual(doc["categories"], ["Dinners"])
        self.assertTrue(doc["photo_data"])
        for key in ("notes", "nutritional_info", "source", "prep_time", "cook_time"):
            self.assertIsInstance(doc[key], str)

    def test_paprika_is_the_same_bytes_each_time(self):
        def once():
            buf = io.BytesIO()
            fmt.paprika(export.recipes_of(self.reels()), self.seed.media, buf)
            with zipfile.ZipFile(buf) as z:
                return z.read("Carbonara.paprikarecipe")

        self.assertEqual(once(), once())


class TestCommand(SeededLibrary):
    def run_export(self, *argv):
        from app.config import settings
        from app.export import __main__ as command

        patched = dataclasses.replace(settings, db_path=self.seed.db, media_dir=self.seed.media)
        with mock.patch.object(command, "settings", patched):
            return command.main(list(argv))

    def test_recipes_to_a_folder_and_a_file(self):
        with tempfile.TemporaryDirectory() as out:
            self.assertEqual(self.run_export("recipes", out), 0)
            self.assertEqual(len(list(Path(out).glob("*.json"))), 1)
            one = Path(out) / "all.json"
            self.assertEqual(self.run_export("recipes", str(one)), 0)
            self.assertEqual(len(json.loads(one.read_text())["@graph"]), 1)

    def test_bundles_into_a_folder_are_named_for_the_user(self):
        with tempfile.TemporaryDirectory() as out:
            self.assertEqual(
                self.run_export("recipes", "--format", "paprika", "--user", "bob", out), 0
            )
            self.assertTrue((Path(out) / "hikidashi-bob.paprikarecipes").is_file())
            self.assertEqual(self.run_export("recipes", "--format", "nextcloud", out), 0)
            self.assertTrue((Path(out) / "hikidashi-alice.zip").is_file())

    def test_markdown(self):
        with tempfile.TemporaryDirectory() as out:
            self.assertEqual(self.run_export("markdown", "--with-media", "--user", "bob", out), 0)
            self.assertEqual(len(list((Path(out) / "reels").glob("*.md"))), len(seed.BOB_REELS))
            self.assertTrue(any((Path(out) / "media").glob("*.mp4")))

    def test_json_and_csv(self):
        with tempfile.TemporaryDirectory() as out:
            path = Path(out) / "deep" / "library.csv"
            self.assertEqual(self.run_export("csv", str(path)), 0)
            self.assertTrue(path.read_text().startswith("shortcode,"))
            with mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                self.assertEqual(self.run_export("json", "--user", "bob", "-"), 0)
            self.assertEqual(len(json.loads(stdout.getvalue())), len(seed.BOB_REELS))

    def test_no_such_user(self):
        with tempfile.TemporaryDirectory() as out:
            self.assertEqual(self.run_export("recipes", "--user", "carol", out), 1)


class TestMarkdown(SeededLibrary):
    def vault(self, **kw):
        from app.export import markdown

        out = Path(tempfile.mkdtemp(dir=self.tmp.name))
        return out, markdown.write(self.reels(), out, self.seed.media, **kw)

    def test_the_layout(self):
        out, result = self.vault()
        self.assertTrue((out / "Hikidashi.md").is_file())
        self.assertEqual(
            {p.stem for p in (out / "reels").iterdir()}, {r.code for r in seed.ALICE_REELS}
        )
        self.assertTrue((out / "collections" / "Japan trip.md").is_file())
        self.assertTrue((out / "authors" / "@wander.jp.md").is_file())
        self.assertFalse((out / "media").exists())
        self.assertEqual(result.skipped, [])

    def test_a_reels_note(self):
        from app.export.markdown import MARKER

        out, _ = self.vault()
        note = (out / "reels" / f"{seed.SHARED.code}.md").read_text()
        head = note.split("---")[1]
        self.assertIn(f'shortcode: "{seed.SHARED.code}"', head)
        self.assertIn('author: "chef.marco"', head)
        self.assertIn('  - "Dinners"', head)
        self.assertIn('saved_at: "', head)
        self.assertIn(f'permalink: "{seed.R}{seed.SHARED.code}/"', head)
        self.assertIn("# Carbonara", note)
        self.assertIn("[[authors/@chef.marco|@chef.marco]]", note)
        self.assertIn("[[collections/Dinners|Dinners]]", note)
        self.assertIn("- [ ] 200 g spaghetti", note)
        self.assertIn("1. Boil the pasta.", note)
        self.assertIn("## Transcript", note)
        self.assertTrue(note.endswith(MARKER + "\n"))

    def test_index_notes_link_their_reels(self):
        out, _ = self.vault()
        japan = (out / "collections" / "Japan trip.md").read_text()
        self.assertIn("[[reels/ALICEtokyo01|", japan)
        self.assertIn("[[reels/ALICEkyoto01|", japan)
        self.assertIn("2 reels.", japan)
        home = (out / "Hikidashi.md").read_text()
        self.assertIn("[[collections/Japan trip|Japan trip]] (2)", home)

    def test_a_rerun_keeps_your_notes_and_changes_nothing_else(self):
        from app.export import markdown

        out, _ = self.vault()
        note = out / "reels" / f"{seed.SHARED.code}.md"
        note.write_text(note.read_text().replace("# Carbonara", "# Mine") + "My notes.\n")
        again = markdown.write(self.reels(), out, self.seed.media)
        self.assertEqual(again.written, [f"reels/{seed.SHARED.code}.md"])
        self.assertEqual(again.kept, 1)
        text = note.read_text()
        self.assertIn("# Carbonara", text)
        self.assertNotIn("# Mine", text)
        self.assertTrue(text.endswith(markdown.MARKER + "\nMy notes.\n"))
        third = markdown.write(self.reels(), out, self.seed.media)
        self.assertEqual(third.written, [])

    def test_a_note_with_no_marker_is_left_alone(self):
        from app.export import markdown

        out, _ = self.vault()
        note = out / "reels" / "ALICEramen01.md"
        note.write_text("all mine")
        again = markdown.write(self.reels(), out, self.seed.media)
        self.assertEqual(again.skipped, [str(note)])
        self.assertEqual(note.read_text(), "all mine")

    def test_notes_for_reels_you_removed_stay(self):
        from app.export import markdown

        out, _ = self.vault()
        (out / "reels" / "GONEreel0001.md").write_text("old")
        again = markdown.write(self.reels(), out, self.seed.media)
        self.assertEqual(again.stale, ["GONEreel0001.md"])
        self.assertTrue((out / "reels" / "GONEreel0001.md").exists())

    def test_thumbnails_and_videos(self):
        out, result = self.vault(thumbs=True, videos=True)
        media = {p.name for p in (out / "media").iterdir()}
        self.assertIn(f"{seed.SHARED.code}.png", media)
        self.assertIn(f"{seed.SHARED.code}.mp4", media)
        self.assertNotIn("ALICEfail001.png", media)  # never fetched
        note = (out / "reels" / f"{seed.SHARED.code}.md").read_text()
        self.assertIn(f"![[media/{seed.SHARED.code}.mp4|320]]", note)
        self.assertGreater(result.media, 0)

    def test_links_rather_than_copies(self):
        out, _ = self.vault(thumbs=True, symlink=True)
        thumb = out / "media" / f"{seed.SHARED.code}.png"
        self.assertTrue(thumb.is_symlink())
        self.assertTrue(thumb.resolve().is_relative_to(self.seed.media.resolve()))


class TestMarkdownText(unittest.TestCase):
    def test_caption_text_stays_text(self):
        from app.export.markdown import text

        self.assertEqual(text("# not a heading"), "\\# not a heading")
        self.assertEqual(text("50%% off"), "50% % off")
        self.assertEqual(text("a <b> c"), "a \\<b> c")
        self.assertEqual(text("line one\nline two"), "line one  \nline two")

    def test_names_obsidian_accepts(self):
        from app.export.markdown import file_name, link

        self.assertEqual(file_name("Recipes: 2024/25?"), "Recipes- 2024-25-")
        self.assertEqual(link("tags/A", "a|b]]"), "[[tags/A|ab]]")

    def test_frontmatter_values_cannot_change_type(self):
        from app.export.markdown import frontmatter

        fm = frontmatter({"a": "2024-01-01", "b": "yes", "c": ["no"], "d": None, "e": True})
        self.assertEqual(fm, '---\na: "2024-01-01"\nb: "yes"\nc:\n  - "no"\ne: true\n---')


class TestTables(SeededLibrary):
    def test_json(self):
        from app.export.table import write_json

        buf = io.StringIO()
        write_json(self.reels(), buf)
        got = json.loads(buf.getvalue())
        self.assertEqual(len(got), len(seed.ALICE_REELS))
        carbonara = next(r for r in got if r["shortcode"] == seed.SHARED.code)
        self.assertEqual(carbonara["recipe"]["ingredients"][0]["item"], "spaghetti")
        self.assertEqual(carbonara["collections"], ["Dinners"])
        self.assertTrue(carbonara["saved_at"].endswith("Z"))
        self.assertNotIn("thumb_path", carbonara)
        self.assertIsNone(next(r for r in got if r["shortcode"] == "ALICEramen01")["recipe"])

    def test_csv(self):
        import csv

        from app.export.table import COLUMNS, write_csv

        buf = io.StringIO(newline="")
        write_csv(self.reels(), buf)
        rows = list(csv.DictReader(io.StringIO(buf.getvalue(), newline="")))
        self.assertEqual(tuple(rows[0].keys()), COLUMNS)
        self.assertEqual(len(rows), len(seed.ALICE_REELS))
        carbonara = next(r for r in rows if r["shortcode"] == seed.SHARED.code)
        self.assertEqual(carbonara["hashtags"], "pasta; recipe")
        self.assertEqual(carbonara["recipe_title"], "Carbonara")
        self.assertEqual(carbonara["recipe_ingredients"].splitlines()[0], "200 g spaghetti")


HAS_WEB = all(importlib.util.find_spec(m) for m in ("fastapi", "httpx", "multipart"))


@unittest.skipUnless(HAS_WEB, "the web dependencies are not installed")
class ApiCase(SeededLibrary):
    """The API on the seeded library, as Alice or Bob."""

    def setUp(self):
        from fastapi.testclient import TestClient

        from app.accounts import User
        from tests import test_api
        from tests.test_api import settings_in

        if test_api.main is None:  # imported here, not run: its setUpModule has not
            test_api.setUpModule()
            self.addClassCleanup(test_api.tearDownModule)
        main = self.main = test_api.main
        root = Path(self.seed.root)
        settings = dataclasses.replace(
            settings_in(root), db_path=self.seed.db, media_dir=self.seed.media, origin=None
        )
        patcher = mock.patch.object(main, "settings", settings)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.users = {
            name: User(id=uid, username=name, is_admin=name == "alice")
            for name, uid in self.seed.users.items()
        }
        self.client = TestClient(main.app)
        self.as_user("alice")
        self.addCleanup(main.app.dependency_overrides.clear)

    def as_user(self, name):
        user = self.users[name]
        self.main.app.dependency_overrides[self.main.current_user] = lambda: user


class TestSheetJsonLd(ApiCase):
    def test_a_recipe_comes_with_its_json_ld(self):
        doc = self.client.get(f"/api/items/{seed.SHARED.code}").json()["jsonld"]
        self.assertEqual(doc["@type"], "Recipe")
        self.assertEqual(doc["name"], "Carbonara")
        # Links back to this server, as the browser reached it.
        self.assertTrue(doc["image"][0].startswith("http://testserver/media/"))

    def test_no_recipe_no_json_ld(self):
        self.assertIsNone(self.client.get("/api/items/ALICEramen01").json()["jsonld"])

    def test_a_correction_is_what_it_carries(self):
        url = f"/api/items/{seed.SHARED.code}/recipe"
        self.as_user("bob")
        r = self.client.put(url, json={"title": "Bob's carbonara", "steps": ["Cook."]})
        self.assertEqual(r.status_code, 200, r.text)
        self.addCleanup(self.client.delete, url)
        self.assertEqual(
            self.client.get(f"/api/items/{seed.SHARED.code}").json()["jsonld"]["name"],
            "Bob's carbonara",
        )
        self.as_user("alice")
        self.assertEqual(
            self.client.get(f"/api/items/{seed.SHARED.code}").json()["jsonld"]["name"], "Carbonara"
        )


class TestDownloads(ApiCase):
    def get(self, kind, **params):
        r = self.client.get(f"/api/export/{kind}", params=params)
        self.assertEqual(r.status_code, 200, r.text)
        return r

    def test_each_is_an_attachment_named_for_you(self):
        for kind in self.main.EXPORTS:
            r = self.get(kind, shortcode=seed.SHARED.code if kind == "note.md" else None)
            self.assertIn('filename="hikidashi-alice-', r.headers["content-disposition"])
            self.assertEqual(r.headers["cache-control"], "no-store")

    def test_recipes(self):
        doc = self.get("recipes.json").json()
        self.assertEqual([d["name"] for d in doc["@graph"]], ["Carbonara"])
        with zipfile.ZipFile(io.BytesIO(self.get("recipes-nextcloud.zip").content)) as z:
            self.assertIn("recipes/Carbonara/recipe.json", z.namelist())
        with zipfile.ZipFile(io.BytesIO(self.get("recipes.paprikarecipes").content)) as z:
            self.assertEqual(z.namelist(), ["Carbonara.paprikarecipe"])

    def test_markdown_with_and_without_images(self):
        with zipfile.ZipFile(io.BytesIO(self.get("markdown.zip").content)) as z:
            names = z.namelist()
        self.assertIn(f"Hikidashi/reels/{seed.SHARED.code}.md", names)
        self.assertIn("Hikidashi/Hikidashi.md", names)
        self.assertFalse(any("/media/" in n for n in names))
        with zipfile.ZipFile(io.BytesIO(self.get("markdown.zip", images="true").content)) as z:
            self.assertIn(f"Hikidashi/media/{seed.SHARED.code}.png", z.namelist())

    def test_only_your_library(self):
        self.as_user("bob")
        got = {r["shortcode"] for r in self.get("library.json").json()}
        self.assertEqual(got, {r.code for r in seed.BOB_REELS})
        rows = self.get("library.csv").text.splitlines()
        self.assertTrue(rows[0].startswith("shortcode,"))
        self.assertFalse(any("ALICE" in line for line in rows))

    def test_what_the_filters_choose(self):
        got = self.get("library.json", collection="Japan trip").json()
        self.assertEqual({r["shortcode"] for r in got}, {"ALICEtokyo01", "ALICEkyoto01"})
        r = self.get("library.csv", collection="Japan trip")
        self.assertIn("hikidashi-alice-japan-trip-library.csv", r.headers["content-disposition"])
        doc = self.get("recipes.json", recipes="true", collection="Dinners").json()
        self.assertEqual([d["name"] for d in doc["@graph"]], ["Carbonara"])
        self.assertEqual(self.get("library.json", q="ramen").json()[0]["shortcode"], "ALICEramen01")

    def test_one_reel(self):
        doc = self.get("recipes.json", shortcode=seed.SHARED.code).json()
        self.assertEqual((doc["@type"], doc["name"]), ("Recipe", "Carbonara"))
        note = self.get("note.md", shortcode=seed.SHARED.code)
        self.assertIn(
            f"hikidashi-alice-{seed.SHARED.code}-note.md", note.headers["content-disposition"]
        )
        self.assertIn("# Carbonara", note.text)
        self.assertIn("- [ ] 200 g spaghetti", note.text)
        with zipfile.ZipFile(
            io.BytesIO(self.get("recipes.paprikarecipes", shortcode=seed.SHARED.code).content)
        ) as z:
            self.assertEqual(z.namelist(), ["Carbonara.paprikarecipe"])

    def test_one_reel_must_be_yours_and_have_what_is_asked(self):
        self.as_user("bob")
        r = self.client.get("/api/export/note.md", params={"shortcode": "ALICEramen01"})
        self.assertEqual(r.status_code, 404)
        r = self.client.get("/api/export/recipes.json", params={"shortcode": "BOBbudget001"})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(self.client.get("/api/export/note.md").status_code, 400)

    def test_videos_in_the_vault(self):
        with zipfile.ZipFile(io.BytesIO(self.get("markdown.zip", videos="true").content)) as z:
            names = z.namelist()
        self.assertIn(f"Hikidashi/media/{seed.SHARED.code}.mp4", names)
        self.assertIn(f"Hikidashi/media/{seed.SHARED.code}.png", names)

    def test_an_admin_can_export_anyones_library(self):
        r = self.get("library.json", user="bob")
        self.assertEqual({x["shortcode"] for x in r.json()}, {x.code for x in seed.BOB_REELS})
        self.assertIn("hikidashi-bob-library.json", r.headers["content-disposition"])
        options = self.client.get("/api/export/options", params={"user": "bob"}).json()
        self.assertEqual(options["user"], "bob")
        self.assertEqual(options["total"], len(seed.BOB_REELS))
        self.assertEqual(
            self.client.get("/api/export/library.json", params={"user": "carol"}).status_code, 404
        )

    def test_nobody_else_can(self):
        self.as_user("bob")
        r = self.client.get("/api/export/library.json", params={"user": "alice"})
        self.assertEqual(r.status_code, 403)
        r = self.client.get("/api/export/options", params={"user": "alice"})
        self.assertEqual(r.status_code, 403)
        # Their own name is fine.
        self.assertEqual(self.get("library.json", user="bob").status_code, 200)

    def test_options(self):
        options = self.client.get("/api/export/options").json()
        self.assertEqual(options["total"], len(seed.ALICE_REELS))
        self.assertEqual(options["recipes"], 1)
        self.assertIn({"name": "Japan trip", "count": 2}, options["collections"])
        self.assertTrue(all(t["count"] for t in options["tags"]))

    def test_no_such_export(self):
        self.assertEqual(self.client.get("/api/export/everything.tar").status_code, 404)

    def test_the_temporary_file_is_gone_after(self):
        import glob
        import tempfile as tf

        before = set(glob.glob(f"{tf.gettempdir()}/hikidashi-export-*"))
        self.get("library.csv")
        self.assertEqual(set(glob.glob(f"{tf.gettempdir()}/hikidashi-export-*")), before)


if __name__ == "__main__":
    unittest.main()
