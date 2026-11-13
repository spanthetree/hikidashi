"""The API reads one person's library and nothing else (#12).

Needs the web dependencies (fastapi, httpx, python-multipart); skipped without
them, like the semantic search tests without sqlite-vec.
"""

import dataclasses
import importlib.util
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from app import config
from app.accounts import User
from app.db import open_db
from app.importer.service import import_export
from tests.fixtures import must, post_record, saved_collections, saved_posts, write_zip

HAS_WEB = all(importlib.util.find_spec(m) for m in ("fastapi", "httpx", "multipart"))
R = "https://www.instagram.com/reel/"

ALICE = User(id=1, username="local", is_admin=True)
BOB = User(id=2, username="bob")


def settings_in(root: Path) -> config.Settings:
    return dataclasses.replace(
        config.Settings.from_env(),
        db_path=root / "t.sqlite3",
        media_dir=root / "media",
        imports_dir=root / "imports",
        logs_dir=root / "logs",
        run_dir=root / "run",
    )


main: Any = None  # app.api.main, imported in setUpModule
_IMPORTED = tempfile.TemporaryDirectory()


def setUpModule():
    """Import the app with its log and data in a temporary folder: importing it
    opens the log file, and the real one may not even be writable."""
    global main
    if HAS_WEB:
        with mock.patch.object(config, "settings", settings_in(Path(_IMPORTED.name))):
            from app.api import main


def tearDownModule():
    _IMPORTED.cleanup()


def export(path, reels, groups=None):
    records = [
        post_record(R + code + "/", timestamp=ts, username="x", caption=caption)
        for code, ts, caption in reels
    ]
    groups = groups and {
        g: [post_record(R + code + "/", timestamp=1, username="x") for code in codes]
        for g, codes in groups.items()
    }
    return write_zip(path, saved_posts(records), saved_collections(groups) if groups else None)


@unittest.skipUnless(HAS_WEB, "the web dependencies are not installed")
class ApiCase(unittest.TestCase):
    """Alice (user 1) and Bob. Both saved SHARED; each saved one reel the
    other did not. Every reel has a thumbnail on disk."""

    def setUp(self):
        from fastapi.testclient import TestClient

        self.main = main
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.settings = settings_in(self.dir)
        patcher = mock.patch.object(main, "settings", self.settings)
        patcher.start()
        self.addCleanup(patcher.stop)

        conn = open_db(self.settings.db_path)
        conn.execute(
            "INSERT INTO user (id, username, created_at) VALUES (2, 'bob', ?)", (int(time.time()),)
        )
        import_export(
            conn,
            export(
                self.dir / "a.zip",
                [
                    ("SHARED", 200, "shared pasta night"),
                    ("ALICE1", 100, "brake pads"),
                ],
                {"Cars": ["ALICE1"]},
            ),
            user_id=1,
        )
        import_export(
            conn,
            export(
                self.dir / "b.zip",
                [
                    ("SHARED", 150, "shared pasta night"),
                    ("BOBBY1", 300, "secret ramen spot"),
                ],
                {"Food": ["SHARED"]},
            ),
            user_id=2,
        )
        for code in ("SHARED", "ALICE1", "BOBBY1"):
            thumb = self.settings.media_dir / "thumbs" / f"{code}.jpg"
            thumb.parent.mkdir(parents=True, exist_ok=True)
            thumb.write_bytes(b"0123456789")
            conn.execute(
                "UPDATE item SET thumb_path = ? WHERE shortcode = ?", (f"thumbs/{code}.jpg", code)
            )
        conn.close()

        self.client = TestClient(main.app)
        self.as_user(ALICE)
        self.addCleanup(main.app.dependency_overrides.clear)
        self.addCleanup(self.tmp.cleanup)

    def as_user(self, user):
        self.main.app.dependency_overrides[self.main.current_user] = lambda: user

    def codes(self, **params):
        r = self.client.get("/api/items", params=params)
        self.assertEqual(r.status_code, 200, r.text)
        return {i["shortcode"] for i in r.json()["items"]}


class TestIsolation(ApiCase):
    def test_the_grid_is_your_library(self):
        self.assertEqual(self.codes(), {"SHARED", "ALICE1"})
        self.as_user(BOB)
        self.assertEqual(self.codes(), {"SHARED", "BOBBY1"})

    def test_search_finds_only_yours(self):
        self.assertEqual(self.codes(q="ramen"), set())
        self.as_user(BOB)
        self.assertEqual(self.codes(q="ramen"), {"BOBBY1"})

    def test_someone_elses_reel_is_not_there_at_all(self):
        for method, path, body in (
            ("GET", "/api/items/BOBBY1", None),
            ("PUT", "/api/items/BOBBY1/tag", {"name": "Food"}),
            ("DELETE", "/api/items/BOBBY1/tag", None),
            ("PUT", "/api/items/BOBBY1/game", {"name": "Doom"}),
            ("DELETE", "/api/items/BOBBY1/game", None),
            ("PUT", "/api/items/BOBBY1/recipe", {"title": "x"}),
            ("DELETE", "/api/items/BOBBY1/recipe", None),
            ("DELETE", "/api/items/BOBBY1", None),
            ("POST", "/api/items/BOBBY1/retry", None),
            ("GET", "/media/thumbs/BOBBY1.jpg", None),
        ):
            r = self.client.request(method, path, json=body)
            self.assertEqual(r.status_code, 404, f"{method} {path}")
        self.as_user(BOB)
        self.assertEqual(self.client.get("/api/items/BOBBY1").status_code, 200)

    def test_more_like_this_is_of_your_reels_only(self):
        self.assertEqual(self.client.get("/api/items/BOBBY1/similar").status_code, 404)
        r = self.client.get("/api/items/SHARED/similar")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), {"items": []})  # no vectors yet

    def test_rediscover(self):
        conn = open_db(self.settings.db_path)
        # ALICE1 saved on 13 Nov 2020, 23:30 UTC: the 14th in Tokyo.
        conn.execute("UPDATE user_item SET saved_at = 1605310200 WHERE shortcode = 'ALICE1'")
        conn.close()

        def strips(md, tz=0):
            r = self.client.get("/api/rediscover", params={"md": md, "tz": tz})
            self.assertEqual(r.status_code, 200, r.text)
            return {k: [i["shortcode"] for i in v] for k, v in r.json().items()}

        self.assertEqual(strips("11-13")["on_this_day"], ["ALICE1"])
        self.assertEqual(strips("11-13", -540)["on_this_day"], [])
        self.assertEqual(strips("11-14", -540)["on_this_day"], ["ALICE1"])
        # Never opened, oldest first; opening one is yours alone.
        self.assertEqual(strips("01-01")["never_opened"], ["SHARED", "ALICE1"])
        self.assertEqual(self.client.post("/api/items/SHARED/opened").status_code, 204)
        self.assertEqual(strips("01-01")["never_opened"], ["ALICE1"])
        self.assertEqual(self.client.post("/api/items/BOBBY1/opened").status_code, 404)
        self.as_user(BOB)
        self.assertEqual(strips("01-01")["never_opened"], ["SHARED", "BOBBY1"])

    def test_shuffle_picks_from_the_filters(self):
        r = self.client.get("/api/items", params={"sort": "random", "limit": 1, "q": "brake"})
        self.assertEqual([i["shortcode"] for i in r.json()["items"]], ["ALICE1"])

    def test_copies_and_collapsing_them(self):
        conn = open_db(self.settings.db_path)
        conn.execute("UPDATE item SET dup_of = 'ALICE1' WHERE shortcode = 'SHARED'")
        conn.close()
        cards = {i["shortcode"]: i["copies"] for i in self.client.get("/api/items").json()["items"]}
        self.assertEqual(cards, {"SHARED": 2, "ALICE1": 2})
        detail = self.client.get("/api/items/ALICE1").json()
        self.assertEqual([c["shortcode"] for c in detail["copies"]], ["SHARED"])
        # Off until you turn it on; then the first you saved stands for both.
        self.assertFalse(self.client.get("/api/me").json()["collapse_copies"])
        r = self.client.put("/api/me", json={"collapse_copies": True})
        self.assertTrue(r.json()["collapse_copies"])
        self.assertEqual(self.codes(), {"ALICE1"})
        # Bob has only one of them: nothing to collapse, and no copies.
        self.as_user(BOB)
        self.assertEqual(self.client.get("/api/items/SHARED").json()["copies"], [])
        self.assertEqual(self.codes(), {"SHARED", "BOBBY1"})
        self.assertEqual(self.client.post("/api/items/SHARED/not-copy/ALICE1").status_code, 404)
        self.as_user(ALICE)
        r = self.client.post("/api/items/SHARED/not-copy/ALICE1")
        self.assertEqual(r.json(), {"copies": []})

    def test_save_time_collections_and_tag_are_yours(self):
        self.client.put("/api/items/SHARED/tag", json={"name": "Weeknight"})
        mine = self.client.get("/api/items/SHARED").json()
        self.assertEqual(
            (mine["saved_at"], mine["collections"], mine["tag"]), (200, [], "Weeknight")
        )
        self.as_user(BOB)
        theirs = self.client.get("/api/items/SHARED").json()
        self.assertEqual((theirs["saved_at"], theirs["collections"]), (150, ["Food"]))
        self.assertNotEqual(theirs["tag"], "Weeknight")
        self.assertEqual(self.codes(tag="Weeknight"), set())
        self.assertNotIn("Weeknight", {t["name"] for t in self.client.get("/api/tags").json()})

    def test_collections_and_stats_are_yours(self):
        self.assertEqual(self.client.get("/api/collections").json(), [{"name": "Cars", "count": 1}])
        self.assertEqual(self.client.get("/api/stats").json()["total"], 2)
        self.assertEqual(self.codes(collection="Food"), set())

    def test_videos_or_images(self):
        conn = open_db(self.settings.db_path)
        conn.execute(
            "UPDATE item SET permalink = 'https://www.instagram.com/p/ALICE1/' "
            "WHERE shortcode = 'ALICE1'"
        )
        conn.close()
        self.assertEqual(self.codes(media="video"), {"SHARED"})
        self.assertEqual(self.codes(media="image"), {"ALICE1"})
        self.assertEqual(self.codes(media="image", collection="Cars"), {"ALICE1"})
        self.assertEqual(self.client.get("/api/items", params={"media": "gif"}).status_code, 422)

    def test_recipe_corrections_are_yours(self):
        self.client.put("/api/items/SHARED/recipe", json={"title": "Nonna's pasta"})
        self.assertEqual(
            self.client.get("/api/items/SHARED").json()["recipe"]["title"], "Nonna's pasta"
        )
        self.as_user(BOB)
        self.assertIsNone(self.client.get("/api/items/SHARED").json()["recipe"])
        self.assertEqual(self.client.get("/api/recipes/facets").json()["total"], 0)

    def test_your_shopping_list_is_yours(self):
        for code in ("SHARED", "ALICE1"):
            self.client.put(
                f"/api/items/{code}/recipe",
                json={"title": code, "ingredients": [{"item": "onion", "quantity": "2"}]},
            )
        r = self.client.post("/api/shopping/recipes", json={"shortcodes": ["SHARED", "BOBBY1"]})
        self.assertEqual([x["shortcode"] for x in r.json()["recipes"]], ["SHARED"])
        self.client.put("/api/shopping/ticks", json={"line": "onion", "ticked": True})
        listed = self.client.get("/api/shopping").json()["lines"]
        self.assertEqual(
            [(x["name"], x["amounts"], x["ticked"]) for x in listed], [("onion", ["2"], True)]
        )
        self.assertTrue(self.client.get("/api/items/SHARED").json()["listed"])
        self.assertEqual(self.client.get("/api/stats").json()["shopping"], 1)

        # Everything the Recipes view shows: here, the collection Cars.
        r = self.client.post(
            "/api/shopping/recipes/matching", params={"recipes": "true", "collection": "Cars"}
        )
        self.assertEqual({x["shortcode"] for x in r.json()["recipes"]}, {"SHARED", "ALICE1"})
        self.assertEqual(r.json()["lines"][0]["amounts"], ["4"])
        self.assertFalse(r.json()["lines"][0]["ticked"])

        self.as_user(BOB)
        self.assertEqual(self.client.get("/api/shopping").json(), {"recipes": [], "lines": []})
        self.assertFalse(self.client.get("/api/items/SHARED").json()["listed"])
        self.as_user(ALICE)
        self.client.delete("/api/shopping/recipes/ALICE1")
        self.assertEqual(len(self.client.get("/api/shopping").json()["recipes"]), 1)
        self.assertEqual(self.client.delete("/api/shopping").json()["recipes"], [])

    def test_the_log_is_for_admins(self):
        self.assertEqual(self.client.get("/api/logs").status_code, 200)
        self.as_user(BOB)
        self.assertEqual(self.client.get("/api/logs").status_code, 403)


class TestGames(ApiCase):
    """The game a reel shows (#117): the pass's, a guess to confirm, or yours."""

    def setUp(self):
        super().setUp()
        conn = open_db(self.settings.db_path)
        conn.execute("UPDATE item SET game_guess = 'Cyberpunk 2077' WHERE shortcode = 'SHARED'")
        conn.execute("UPDATE item SET game = 'Gran Turismo 4' WHERE shortcode = 'ALICE1'")
        conn.close()

    def game(self, code):
        return self.client.get(f"/api/items/{code}").json()["game"]

    def test_a_sure_game_is_shown_and_a_guess_is_asked_about(self):
        self.assertEqual(
            self.game("ALICE1"), {"game": "Gran Turismo 4", "yours": False, "guess": None}
        )
        self.assertEqual(
            self.game("SHARED"), {"game": None, "yours": False, "guess": "Cyberpunk 2077"}
        )

    def test_confirming_is_yours_alone(self):
        r = self.client.put("/api/items/SHARED/game", json={"name": " Cyberpunk  2077 "})
        self.assertEqual(r.json(), {"game": "Cyberpunk 2077", "yours": True, "guess": None})
        self.assertEqual(self.codes(q="cyberpunk"), {"SHARED"})
        self.as_user(BOB)
        self.assertEqual(self.game("SHARED")["guess"], "Cyberpunk 2077")
        self.assertEqual(self.codes(q="cyberpunk"), set())

    def test_saying_none_then_taking_it_back(self):
        r = self.client.put("/api/items/ALICE1/game", json={"name": None})
        self.assertEqual(r.json(), {"game": None, "yours": True, "guess": None})
        r = self.client.delete("/api/items/ALICE1/game")
        self.assertEqual(r.json()["game"], "Gran Turismo 4")

    def test_a_blank_or_long_title_is_refused(self):
        for name in ("   ", "x" * 200):
            r = self.client.put("/api/items/SHARED/game", json={"name": name})
            self.assertEqual(r.status_code, 400, name)


class TestLabels(ApiCase):
    """Tags, games and hashtags, several at once: a reel must have them all."""

    def setUp(self):
        super().setUp()
        conn = open_db(self.settings.db_path)
        for code, tags, game in (
            ("SHARED", '["pasta", "dinner"]', None),
            ("ALICE1", '["brakes", "dinner"]', "Gran Turismo 4"),
        ):
            conn.execute(
                "UPDATE item SET clean_hashtags = ?, game = ? WHERE shortcode = ?",
                (tags, game, code),
            )
        conn.close()
        self.client.put("/api/items/SHARED/tag", json={"name": "Food"})
        self.client.put("/api/items/ALICE1/tag", json={"name": "Gaming"})

    def q(self, **params):
        r = self.client.get("/api/items", params=params)
        self.assertEqual(r.status_code, 200, r.text)
        return {i["shortcode"] for i in r.json()["items"]}

    def test_every_label_must_match(self):
        self.assertEqual(self.q(hashtag="dinner"), {"SHARED", "ALICE1"})
        self.assertEqual(self.q(hashtag=["dinner", "pasta"]), {"SHARED"})
        self.assertEqual(self.q(tag="Gaming", hashtag="dinner"), {"ALICE1"})
        self.assertEqual(self.q(tag="Gaming", game="gran turismo 4"), {"ALICE1"})
        self.assertEqual(self.q(tag=["Food", "Gaming"]), set(), "a reel has one tag")
        self.assertEqual(self.q(game="Gran Turismo 4", hashtag="pasta"), set())

    def test_a_label_given_twice_or_blank_counts_once(self):
        self.assertEqual(self.q(hashtag=["pasta", "pasta", " "]), {"SHARED"})

    def test_a_game_is_the_one_you_see(self):
        self.client.put("/api/items/ALICE1/game", json={"name": None})
        self.assertEqual(self.q(game="Gran Turismo 4"), set())
        self.client.put("/api/items/SHARED/game", json={"name": "Overcooked"})
        self.assertEqual(self.q(game="overcooked"), {"SHARED"})
        self.as_user(BOB)
        self.assertEqual(self.q(game="overcooked"), set())


class TestMedia(ApiCase):
    def test_your_thumbnail_with_byte_ranges(self):
        r = self.client.get("/media/thumbs/SHARED.jpg", headers={"Range": "bytes=2-4"})
        self.assertEqual((r.status_code, r.content), (206, b"234"))
        self.assertIn("private", r.headers["cache-control"])

    def test_the_slides_of_your_carousel(self):
        conn = open_db(self.settings.db_path)
        for code in ("SHARED", "BOBBY1"):
            rel = f"slides/{code}-1.jpg"
            (self.settings.media_dir / "slides").mkdir(exist_ok=True)
            (self.settings.media_dir / rel).write_bytes(b"jpg")
            conn.execute("INSERT INTO item_slide VALUES (?, 0, ?)", (code, rel))
        conn.close()
        self.assertEqual(
            self.client.get("/api/items/SHARED").json()["slides"], ["/media/slides/SHARED-1.jpg"]
        )
        self.assertEqual(self.client.get("/api/items/ALICE1").json()["slides"], [])
        self.assertEqual(self.client.get("/media/slides/SHARED-1.jpg").status_code, 200)
        self.assertEqual(self.client.get("/media/slides/BOBBY1-1.jpg").status_code, 404)

    def test_a_photo_is_not_a_reel_missing_its_video(self):
        conn = open_db(self.settings.db_path)
        for code, meta in (
            ("SHARED", '{"id": "SHARED"}'),
            ("ALICE1", '{"id": "ALICE1", "formats": [{"format_id": "1"}]}'),
        ):
            conn.execute("UPDATE item SET fetch_meta = ? WHERE shortcode = ?", (meta, code))
        conn.commit()
        conn.close()
        self.assertTrue(self.client.get("/api/items/SHARED").json()["photo"])
        self.assertFalse(self.client.get("/api/items/ALICE1").json()["photo"])
        self.as_user(BOB)
        # Never fetched: nothing is known, so it may yet have a video.
        self.assertFalse(self.client.get("/api/items/BOBBY1").json()["photo"])

    def test_nothing_outside_the_media_root(self):
        (self.dir / "secret.txt").write_text("no")
        # The client collapses a bare `..` itself, so that one never reaches the
        # media route (it lands on the web app); only the encoded one does.
        self.assertNotEqual(self.client.get("/media/../secret.txt").text, "no")
        self.assertEqual(self.client.get("/media/%2e%2e/secret.txt").status_code, 404)


class TestPlaces(ApiCase):
    """SHARED names Mabel's and Bangor; BOBBY1, Bob's, names a ramen shop.
    Mabel's is on the map; Bangor was found, but too unsurely to show."""

    DELI = "Mabel's Deli — 118 Water St, New York, NY 10005"

    def setUp(self):
        super().setUp()
        from app.places import worker
        from app.places.geocoder import Located

        conn = open_db(self.settings.db_path)
        conn.execute(
            "UPDATE item SET caption = ? WHERE shortcode = 'SHARED'",
            (f"pasta night \U0001f4cd{self.DELI} \U0001f4cdBangor, Maine",),
        )
        conn.execute(
            "UPDATE item SET caption = ? WHERE shortcode = 'BOBBY1'",
            ("\U0001f4cdRamen Ichiraku, Konoha",),
        )
        worker.read_all(conn)
        worker.store(conn, self.DELI, Located(40.72, -73.99, "Mabel's", "restaurant", 1.0, "q"))
        worker.store(conn, "Bangor, Maine", Located(43.6, -70.2, "Bangor", "city", 0.3, "q"))
        worker.store(conn, "Ramen Ichiraku, Konoha", Located(35, 139, "Ichiraku", "food", 1, "q"))
        conn.close()

    def test_the_places_filter_is_reels_that_name_one(self):
        self.assertEqual(self.codes(places="true"), {"SHARED"})
        self.assertEqual(self.codes(places="true", q="brake"), set())
        self.assertEqual(self.client.get("/api/stats").json()["places"], 1)

    def test_places_are_counted_by_their_tag(self):
        self.assertEqual(self.client.get("/api/stats").json()["place_tags"], {})
        conn = open_db(self.settings.db_path)
        conn.execute("UPDATE item SET primary_tag = 'Food' WHERE shortcode IN ('SHARED', 'BOBBY1')")
        conn.close()
        self.assertEqual(
            self.client.get("/api/stats").json()["place_tags"],
            {"Food": 1},
            "Bob's ramen is not yours",
        )
        body = self.client.get("/api/places", params={"places": "true", "tag": "Travel"}).json()
        self.assertEqual(body["items"], [], "a tag narrows the map")

    def test_the_map_has_your_confident_pins(self):
        body = self.client.get("/api/places", params={"places": "true"}).json()
        self.assertEqual((body["total"], body["named"]), (1, 1))
        [item] = body["items"]
        self.assertEqual(item["shortcode"], "SHARED")
        self.assertEqual(
            [(p["text"], p["lat"], p["lng"]) for p in item["places"]], [(self.DELI, 40.72, -73.99)]
        )

    def test_the_map_counts_what_it_leaves_out(self):
        body = self.client.get("/api/places").json()
        self.assertEqual((body["total"], body["named"], len(body["items"])), (2, 1, 1))

    def test_every_filter_narrows_the_map(self):
        body = self.client.get("/api/places", params={"collection": "Cars"}).json()
        self.assertEqual((body["total"], body["items"]), (1, []))
        self.as_user(BOB)
        body = self.client.get("/api/places", params={"q": "ramen"}).json()
        self.assertEqual([i["shortcode"] for i in body["items"]], ["BOBBY1"])

    def test_the_detail_has_coordinates_where_sure(self):
        places = self.client.get("/api/items/SHARED").json()["places"]
        self.assertEqual(
            [(p["text"], p["lat"]) for p in places], [(self.DELI, 40.72), ("Bangor, Maine", None)]
        )

    def test_a_typed_place_comes_from_the_cache_then_the_geocoder(self):
        self.assertEqual(
            self.client.get("/api/geocode", params={"q": self.DELI}).json()["lat"], 40.72
        )
        r = self.client.get("/api/geocode", params={"q": "Osaka"})
        self.assertEqual(r.status_code, 503, "no geocoder is set up")

        geo = dataclasses.replace(self.settings, geocoder="https://geo.example")
        with (
            mock.patch.object(self.main, "settings", geo),
            mock.patch(
                "app.places.geocoder.Nominatim.search",
                return_value=[{"lat": "34.69", "lon": "135.5", "display_name": "Osaka"}],
            ) as search,
        ):
            self.assertEqual(
                self.client.get("/api/geocode", params={"q": "Osaka"}).json(),
                {"lat": 34.69, "lng": 135.5, "label": "Osaka"},
            )
            self.client.get("/api/geocode", params={"q": "Osaka"})
        search.assert_called_once()


class TestRemove(ApiCase):
    def test_removing_a_shared_reel_leaves_it_for_the_other(self):
        r = self.client.delete("/api/items/SHARED")
        self.assertEqual(r.json(), {"shortcode": "SHARED", "purged": False})
        self.assertEqual(self.codes(), {"ALICE1"})
        self.assertEqual(self.client.get("/api/items/SHARED").status_code, 404)
        self.as_user(BOB)
        self.assertEqual(self.client.get("/media/thumbs/SHARED.jpg").status_code, 200)

    def test_the_last_one_out_deletes_the_reel_and_its_media(self):
        self.client.delete("/api/items/SHARED")
        self.as_user(BOB)
        r = self.client.delete("/api/items/SHARED")
        self.assertEqual(r.json(), {"shortcode": "SHARED", "purged": True})
        self.assertFalse((self.settings.media_dir / "thumbs" / "SHARED.jpg").exists())
        self.assertTrue((self.settings.media_dir / "thumbs" / "BOBBY1.jpg").exists())


class TestRetry(ApiCase):
    def fail_item(self, code, stage="fetch"):
        conn = open_db(self.settings.db_path)
        conn.execute(
            """UPDATE item SET state = 'failed', fail_stage = ?,
                        fail_reason = 'timeout', attempts = 3 WHERE shortcode = ?""",
            (stage, code),
        )
        conn.close()

    def test_anyone_who_saved_it_can_retry_a_failed_fetch(self):
        from app.enrich import worker
        from app.enrich.fetcher import Fetched, FetchError

        self.fail_item("SHARED")
        self.as_user(BOB)  # Alice's import may have failed it; Bob retries
        with (
            mock.patch.object(
                worker, "probe", return_value=Fetched("SHARED", caption="x", duration=9)
            ),
            mock.patch.object(worker, "download_media", side_effect=FetchError("no")),
        ):
            r = self.client.post("/api/items/SHARED/retry")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["state"], r.json()["fail_stage"]), ("fetched", None))
        self.as_user(ALICE)
        self.assertEqual(self.client.get("/api/items/SHARED").json()["state"], "fetched")

    def test_only_a_failed_fetch(self):
        self.assertEqual(self.client.post("/api/items/SHARED/retry").status_code, 409)
        self.fail_item("SHARED", stage="asr")
        self.assertEqual(self.client.post("/api/items/SHARED/retry").status_code, 409)


class TestImport(ApiCase):
    def test_uploads_go_to_your_library_and_your_folder(self):
        self.as_user(BOB)
        path = export(self.dir / "export.zip", [("NEWONE", 500, "new")])
        with path.open("rb") as fh:
            r = self.client.post("/api/import", files={"file": ("export.zip", fh)})
        self.assertEqual(r.json()["inserted"], 1)
        self.assertTrue((self.settings.imports_dir / "2" / "export.zip").exists())
        self.assertIn("NEWONE", self.codes())
        self.as_user(ALICE)
        self.assertNotIn("NEWONE", self.codes())

    def test_an_upload_wakes_the_locator(self):
        locator = mock.Mock()
        with mock.patch.object(self.main, "_locator", locator):
            path = export(self.dir / "export.zip", [("NEWONE", 500, "new")])
            with path.open("rb") as fh:
                self.client.post("/api/import", files={"file": ("export.zip", fh)})
        locator.wake.assert_called_once()

    def test_no_geocoder_no_locator(self):
        self.main.start_locator()
        self.assertIsNone(self.main._locator)


class TestPipeline(ApiCase):
    def fail_all(self, stage="enrich"):
        from app.jobs.queue import MAX_ATTEMPTS, LocalJobs

        conn = open_db(self.settings.db_path)
        w = LocalJobs(conn, "desktop", retry_after=0)
        w.check_in(stages=("enrich", "asr"), started_at=time.time(), problems={"asr": "no GPU"})
        for _ in range(MAX_ATTEMPTS):
            w.release(stage, w.claim(stage, 10))
        conn.close()

    def test_for_admins_only(self):
        self.assertEqual(self.client.get("/api/pipeline").status_code, 200)
        self.as_user(BOB)
        self.assertEqual(self.client.get("/api/pipeline").status_code, 403)
        self.assertEqual(
            self.client.post("/api/pipeline/retry", json={"stage": "enrich"}).status_code, 403
        )

    def test_stages_failures_and_workers(self):
        self.fail_all()
        body = self.client.get("/api/pipeline").json()
        enrich = body["stages"][0]
        self.assertEqual((enrich["stage"], enrich["failed"], enrich["ready"]), ("enrich", 3, 0))
        self.assertEqual(
            {f["permalink"] for f in enrich["failures"]},
            {R + c + "/" for c in ("SHARED", "ALICE1", "BOBBY1")},
        )
        [w] = body["workers"]
        self.assertEqual((w["name"], w["state"]), ("desktop", "running"))
        self.assertEqual(w["problems"], {"asr": "no GPU"})
        self.assertEqual(body["overall"], "problem")

    def test_retry_offers_them_again_and_wakes_the_workers(self):
        from app.jobs import woken_at

        self.fail_all()
        r = self.client.post("/api/pipeline/retry", json={"stage": "enrich"})
        self.assertEqual(r.status_code, 200, r.text)
        enrich = r.json()["stages"][0]
        self.assertEqual((enrich["failed"], enrich["ready"]), (0, 3))
        self.assertGreater(woken_at(self.settings.run_dir), 0)
        r = self.client.post("/api/pipeline/retry", json={"stage": "places"})
        self.assertEqual(r.status_code, 400)

    def fail_fetches(self):
        from app.enrich.fetcher import FETCH_V
        from app.enrich.worker import MAX_ATTEMPTS

        conn = open_db(self.settings.db_path)
        for code, reason, attempts, fetch_v in (
            ("SHARED", "restricted audience", 1, FETCH_V),  # permanent
            ("ALICE1", "timeout", MAX_ATTEMPTS, 0),  # out of tries
            ("BOBBY1", "rate-limited", 1, 0),  # still trying
        ):
            conn.execute(
                """UPDATE item SET state = 'failed', fail_stage = 'fetch',
                     fail_reason = ?, attempts = ?, fetch_v = ? WHERE shortcode = ?""",
                (reason, attempts, fetch_v, code),
            )
        conn.commit()
        conn.close()

    def test_failed_fetches_with_why(self):
        self.fail_fetches()
        fetch = self.client.get("/api/pipeline").json()["fetch"]
        self.assertEqual((fetch["failed"], fetch["retryable"]), (3, 1))
        got = {
            f["shortcode"]: (f["reason"], f["permanent"], f["retrying"]) for f in fetch["failures"]
        }
        self.assertEqual(
            got,
            {
                "SHARED": ("restricted audience", True, False),
                "ALICE1": ("timeout", False, False),
                "BOBBY1": ("rate-limited", False, True),
            },
        )
        self.assertEqual(
            {f["permalink"] for f in fetch["failures"]},
            {R + c + "/" for c in ("SHARED", "ALICE1", "BOBBY1")},
        )

    def test_retry_fetch_leaves_the_permanent_ones(self):
        from app.enrich.worker import pending

        self.fail_fetches()
        r = self.client.post("/api/pipeline/retry", json={"stage": "fetch"})
        self.assertEqual(r.status_code, 200, r.text)
        fetch = r.json()["fetch"]
        self.assertEqual((fetch["failed"], fetch["retryable"]), (3, 0))
        failures = {f["shortcode"]: f for f in fetch["failures"]}
        self.assertEqual(failures["ALICE1"]["attempts"], 0)
        self.assertTrue(failures["ALICE1"]["retrying"])
        self.assertTrue(failures["SHARED"]["permanent"])
        conn = open_db(self.settings.db_path)
        self.assertEqual({r["shortcode"] for r in pending(conn)}, {"ALICE1", "BOBBY1"})
        conn.close()

    def test_the_workers_log(self):
        log = self.settings.logs_dir / "worker.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("2026-10-25 12:00:00,000 INFO    hikidashi.worker  asr: 4/4\n")
        r = self.client.get("/api/logs", params={"log": "worker"})
        self.assertEqual(r.json()["lines"], [log.read_text().strip()])
        self.assertEqual(self.client.get("/api/logs", params={"log": "x"}).status_code, 422)
        # Under Docker the server-worker writes one of its own (#86).
        other = self.settings.logs_dir / "server-worker.log"
        other.write_text("2026-10-25 12:00:00,000 INFO    hikidashi.worker  embed: 4/4\n")
        r = self.client.get("/api/logs", params={"log": "server-worker"})
        self.assertEqual(r.json()["lines"], [other.read_text().strip()])

    def test_the_sidebar_hears_how_the_workers_are(self):
        self.assertEqual(self.client.get("/api/stats").json()["worker"], "none")
        self.as_user(BOB)
        self.assertIsNone(self.client.get("/api/stats").json()["worker"])

    def test_an_upload_wakes_the_workers(self):
        from app.jobs import woken_at

        path = export(self.dir / "export.zip", [("NEWONE", 500, "new")])
        with path.open("rb") as fh:
            self.client.post("/api/import", files={"file": ("export.zip", fh)})
        self.assertGreater(woken_at(self.settings.run_dir), 0)


@unittest.skipUnless(importlib.util.find_spec("argon2"), "argon2-cffi not installed")
class SignInCase(ApiCase):
    """HIKIDASHI_AUTH=local, with real sessions: nobody is signed in to start."""

    def setUp(self):
        super().setUp()
        self.main.app.dependency_overrides.clear()
        self.settings = dataclasses.replace(self.settings, auth="local")
        patcher = mock.patch.object(self.main, "settings", self.settings)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.main._failures.clear()

    def claim(self, client=None):
        return (client or self.client).post(
            "/api/auth/setup",
            json={"token": self.main.SETUP_TOKEN, "username": "alice", "password": "alice's pw"},
        )

    def bob(self):
        """Bob, signed in on a client of his own, by an invite from Alice."""
        from fastapi.testclient import TestClient

        self.claim()
        path = self.client.post("/api/invites").json()["path"]
        bob = TestClient(self.main.app)
        conn = open_db(self.settings.db_path)
        conn.execute("DELETE FROM user WHERE id = 2")  # the fixture's Bob
        conn.close()
        r = bob.post(
            f"/api{path.replace('/invite/', '/auth/invite/')}",
            json={"username": "bob", "password": "bob's pw!"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        return bob


class TestSignIn(SignInCase):
    def test_nothing_without_signing_in(self):
        self.assertEqual(self.client.get("/api/auth").json(), {"mode": "local", "setup": True})
        for path in (
            "/api/items",
            "/api/items/SHARED",
            "/media/thumbs/SHARED.jpg",
            "/api/me",
            "/api/logs",
        ):
            self.assertEqual(self.client.get(path).status_code, 401, path)

    def test_claiming_the_library_takes_the_token_from_the_log(self):
        r = self.client.post(
            "/api/auth/setup",
            json={"token": "guess", "username": "mallory", "password": "mallory's pw"},
        )
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.claim().json()["username"], "alice")
        self.assertEqual(self.codes(), {"SHARED", "ALICE1"})  # the old library
        self.assertEqual(self.claim().status_code, 409)  # once only

    def test_sign_in_and_out(self):
        self.claim()
        self.client.post("/api/auth/logout")
        self.assertEqual(self.client.get("/api/items").status_code, 401)
        r = self.client.post(
            "/api/auth/login", json={"username": "alice", "password": "alice's pw"}
        )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["set-cookie"].startswith("hk_session="))
        self.assertIn("httponly", r.headers["set-cookie"].lower())
        self.assertIn("samesite=lax", r.headers["set-cookie"].lower())
        self.assertEqual(self.client.get("/api/me").json()["username"], "alice")

    def test_guessing_is_throttled(self):
        self.claim()
        for _ in range(self.main.FAILURES_ALLOWED):
            r = self.client.post("/api/auth/login", json={"username": "alice", "password": "guess"})
            self.assertEqual(r.status_code, 401)
        r = self.client.post(
            "/api/auth/login", json={"username": "alice", "password": "alice's pw"}
        )
        self.assertEqual(r.status_code, 429)

    def test_an_invited_person_starts_with_an_empty_library(self):
        bob = self.bob()
        self.assertEqual(bob.get("/api/items").json()["total"], 0)
        self.assertEqual(bob.get("/api/items/SHARED").status_code, 404)
        self.assertEqual(bob.get("/api/users").status_code, 403)
        self.assertEqual(bob.post("/api/invites").status_code, 403)

    def test_a_disabled_account_is_signed_out(self):
        bob = self.bob()
        bob_id = bob.get("/api/me").json()["id"]
        self.client.put(f"/api/users/{bob_id}", json={"disabled": True})
        self.assertEqual(bob.get("/api/items").status_code, 401)

    def test_a_new_password_signs_you_out_elsewhere(self):
        from fastapi.testclient import TestClient

        self.claim()
        elsewhere = TestClient(self.main.app)
        elsewhere.post("/api/auth/login", json={"username": "alice", "password": "alice's pw"})
        r = self.client.put(
            "/api/me/password", json={"current": "alice's pw", "new": "a better pw"}
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.client.get("/api/me").status_code, 200)
        self.assertEqual(elsewhere.get("/api/me").status_code, 401)

    def test_your_handle(self):
        self.claim()
        r = self.client.put("/api/me", json={"ig_handle": "@Alice.Eats"})
        self.assertEqual(r.json()["ig_handle"], "alice.eats")
        r = self.client.put("/api/me", json={"ig_handle": "no spaces"})
        self.assertEqual(r.status_code, 400)


class TestCrossSite(SignInCase):
    def test_another_site_cannot_change_anything(self):
        self.claim()
        for headers in (
            {"Origin": "https://evil.example"},
            {"Sec-Fetch-Site": "cross-site"},
            {"Sec-Fetch-Site": "same-site"},
        ):
            r = self.client.delete("/api/items/SHARED", headers=headers)
            self.assertEqual(r.status_code, 403, headers)
        self.assertIn("SHARED", self.codes())

    def test_this_site_and_scripts_can(self):
        self.claim()
        r = self.client.put(
            "/api/items/SHARED/tag",
            json={"name": "Food"},
            headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.delete("/api/items/SHARED/tag").status_code, 200)

    def test_without_accounts_too(self):
        self.settings = dataclasses.replace(self.settings, auth="none")
        with mock.patch.object(self.main, "settings", self.settings):
            r = self.client.post("/api/items/SHARED/retry", headers={"Origin": "https://x.y"})
        self.assertEqual(r.status_code, 403)


class TestTokens(SignInCase):
    """HIKIDASHI_AUTH=local, from a script: `Authorization: Bearer` (#103)."""

    def setUp(self):
        from fastapi.testclient import TestClient

        super().setUp()
        self.claim()
        self.anon = TestClient(self.main.app)  # no cookie

    def token(self, username, *scopes, kind="api"):
        from app import accounts

        conn = open_db(self.settings.db_path)
        user = accounts.by_name(conn, username)
        assert user is not None
        if kind == "worker":
            token, _ = accounts.make_worker_token(conn, user, "desktop")
        else:
            token, _ = accounts.make_token(conn, user, "script", scopes)
        conn.close()
        return token

    def call(self, method, path, token, **kw):
        return self.anon.request(method, path, headers={"Authorization": f"Bearer {token}"}, **kw)

    def test_a_token_reads_its_owners_library_only(self):
        token = self.token("bob", "read:library")
        r = self.call("GET", "/api/items", token)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual({i["shortcode"] for i in r.json()["items"]}, {"SHARED", "BOBBY1"})
        self.assertEqual(self.call("GET", "/api/items/ALICE1", token).status_code, 404)
        self.assertEqual(self.call("GET", "/media/thumbs/BOBBY1.jpg", token).status_code, 200)
        self.assertEqual(self.call("GET", "/media/thumbs/ALICE1.jpg", token).status_code, 404)

    def test_wrong_and_missing_tokens_are_told_what_is_wanted(self):
        r = self.anon.get("/api/items")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.headers["www-authenticate"], 'Bearer realm="Hikidashi"')
        token = self.token("bob", "read:library")
        r = self.call("GET", "/api/items", token[:-1] + "x")
        self.assertEqual(r.status_code, 401)
        self.assertIn('error="invalid_token"', r.headers["www-authenticate"])

    def test_never_from_the_url(self):
        token = self.token("bob", "read:library")
        for name in ("access_token", "token"):
            self.assertEqual(self.anon.get(f"/api/items?{name}={token}").status_code, 401)

    def test_reading_is_not_writing(self):
        token = self.token("bob", "read:library")
        r = self.call("PUT", "/api/items/SHARED/tag", token, json={"name": "Food"})
        self.assertEqual(r.status_code, 403)
        self.assertIn('error="insufficient_scope"', r.headers["www-authenticate"])
        self.assertIn('scope="write:library"', r.headers["www-authenticate"])
        token = self.token("bob", "write:library")
        r = self.call("PUT", "/api/items/SHARED/tag", token, json={"name": "Food"})
        self.assertEqual(r.status_code, 200, r.text)

    def test_a_users_token_never_reaches_admin(self):
        token = self.token("bob", "write:library")
        for path in ("/api/users", "/api/pipeline", "/api/logs", "/api/worker-tokens"):
            self.assertEqual(self.call("GET", path, token).status_code, 403, path)

    def test_an_admins_token_reaches_only_what_it_was_given(self):
        token = self.token("alice", "read:admin")
        self.assertEqual(self.call("GET", "/api/users", token).status_code, 200)
        self.assertEqual(self.call("GET", "/api/items", token).status_code, 403)
        r = self.call("PUT", "/api/users/2", token, json={"disabled": True})
        self.assertEqual(r.status_code, 403)
        token = self.token("alice", "read:library", "write:admin")
        r = self.call("PUT", "/api/users/2", token, json={"disabled": True})
        self.assertEqual(r.status_code, 200, r.text)

    def test_admin_parts_of_library_routes_need_read_admin(self):
        library = self.token("alice", "read:library")
        both = self.token("alice", "read:library", "read:admin")
        self.assertIsNone(self.call("GET", "/api/stats", library).json()["worker"])
        self.assertIsNotNone(self.call("GET", "/api/stats", both).json()["worker"])
        path = "/api/export/library.json?user=bob"
        self.assertEqual(self.call("GET", path, library).status_code, 403)
        self.assertEqual(self.call("GET", path, both).status_code, 200)

    def test_a_token_cannot_touch_the_account_itself(self):
        token = self.token("alice", "write:library", "write:admin")
        for method, path, body in (
            ("PUT", "/api/me/password", {"current": "alice's pw", "new": "a new password"}),
            ("POST", "/api/me/sign-out-everywhere", None),
            ("POST", "/api/worker-tokens", {"name": "x"}),
            ("DELETE", "/api/worker-tokens/1", None),
        ):
            r = self.call(method, path, token, json=body)
            self.assertEqual(r.status_code, 403, path)
            self.assertIn("a token cannot", r.json()["detail"], path)

    def test_worker_and_api_tokens_never_stand_in_for_each_other(self):
        worker = self.token("alice", kind="worker")
        api = self.token("alice", "write:library", "write:admin")
        r = self.call("GET", "/api/items", worker)
        self.assertEqual(r.status_code, 401)
        self.assertIn("worker token", r.json()["detail"])
        claim = {"worker": "w", "stage": "asr", "limit": 1}
        self.assertEqual(self.call("POST", "/api/worker/claim", api, json=claim).status_code, 401)

    def test_the_session_comes_first(self):
        bob = self.bob()  # a new Bob, by invite: his library is empty
        token = self.token("alice", "read:library")
        self.assertEqual(self.call("GET", "/api/items", token).json()["total"], 2)
        r = bob.get("/api/items", headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(r.json()["total"], 0)

    def test_made_on_your_account_shown_once(self):
        r = self.client.post(
            "/api/me/tokens", json={"name": "monitor", "scopes": ["read:library"], "days": 7}
        )
        self.assertEqual(r.status_code, 200, r.text)
        made = r.json()
        self.assertTrue(made["token"].startswith("hk_api_"))
        [listed] = made["tokens"]
        self.assertEqual((listed["name"], listed["scopes"]), ("monitor", ["read:library"]))
        self.assertEqual(listed["expires_at"] - listed["created_at"], 7 * 86400)
        again = self.client.get("/api/me/tokens").json()
        self.assertNotIn(made["token"], repr(again))
        self.assertEqual(again["max_days"], 30)
        self.assertEqual(self.call("GET", "/api/items", made["token"]).status_code, 200)

    def test_only_admins_are_offered_the_admin_scopes(self):
        self.assertIn("write:admin", self.client.get("/api/me/tokens").json()["scopes"])
        bob = self.bob()
        self.assertEqual(
            bob.get("/api/me/tokens").json()["scopes"],
            ["read:library", "write:library", "read:ai", "write:ai"],
        )
        r = bob.post("/api/me/tokens", json={"name": "x", "scopes": ["read:admin"]})
        self.assertEqual(r.status_code, 400)
        for days in (0, 31):
            r = bob.post(
                "/api/me/tokens", json={"name": "x", "scopes": ["read:library"], "days": days}
            )
            self.assertEqual(r.status_code, 400, days)

    def test_you_revoke_your_own_and_an_admin_anyones(self):
        bob = self.bob()
        made = bob.post("/api/me/tokens", json={"name": "x", "scopes": ["read:library"]}).json()
        self.assertEqual(self.client.delete(f"/api/me/tokens/{made['id']}").status_code, 404)
        [listed] = self.client.get("/api/tokens").json()
        self.assertEqual((listed["owner"], listed["name"]), ("bob", "x"))
        self.assertNotIn(made["token"], repr(listed))
        self.assertEqual(bob.get("/api/tokens").status_code, 403)
        self.assertEqual(self.client.delete(f"/api/tokens/{made['id']}").json(), [])
        self.assertEqual(self.call("GET", "/api/items", made["token"]).status_code, 401)
        made = bob.post("/api/me/tokens", json={"name": "y", "scopes": ["read:library"]}).json()
        self.assertEqual(bob.delete(f"/api/me/tokens/{made['id']}").json()["tokens"], [])

    def test_worker_tokens_are_not_listed_or_revoked_as_api_tokens(self):
        worker = self.client.post("/api/worker-tokens", json={"name": "desktop"}).json()
        self.assertEqual(self.client.get("/api/tokens").json(), [])
        self.assertEqual(self.client.delete(f"/api/tokens/{worker['id']}").status_code, 404)
        self.assertEqual(self.client.get("/api/me/tokens").json()["tokens"], [])

    def test_a_token_lists_its_owners_tokens_but_makes_none(self):
        token = self.token("bob", "read:library")
        self.assertEqual(len(self.call("GET", "/api/me/tokens", token).json()["tokens"]), 1)
        token = self.token("bob", "write:library")
        r = self.call(
            "POST", "/api/me/tokens", token, json={"name": "x", "scopes": ["read:library"]}
        )
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.call("DELETE", "/api/me/tokens/1", token).status_code, 403)

    def test_none_mode_needs_no_token(self):
        self.settings = dataclasses.replace(self.settings, auth="none")
        with mock.patch.object(self.main, "settings", self.settings):
            self.assertEqual(self.anon.get("/api/items").status_code, 200)
            self.assertEqual(self.call("GET", "/api/items", "hk_api_nonsense").status_code, 200)


class TestProxy(ApiCase):
    """HIKIDASHI_AUTH=proxy: a proxy in front names the user in a header."""

    def setUp(self):
        super().setUp()
        self.main.app.dependency_overrides.clear()
        self.settings = dataclasses.replace(
            self.settings,
            auth="proxy",
            proxy_header="Remote-User",
            trusted_proxies=("10.0.0.0/8",),
            proxy_creates=False,
        )
        patcher = mock.patch.object(self.main, "settings", self.settings)
        patcher.start()
        self.addCleanup(patcher.stop)
        conn = open_db(self.settings.db_path)
        conn.execute("UPDATE user SET username = 'alice' WHERE id = 1")
        conn.close()

    def from_address(self, address):
        """A client whose requests arrive from `address`."""
        from fastapi.testclient import TestClient

        app = self.main.app

        async def at(scope, receive, send):
            if scope["type"] == "http":
                scope = {**scope, "client": (address, 40000)}
            await app(scope, receive, send)

        return TestClient(at)

    def get(self, address, user, path="/api/items"):
        return self.from_address(address).get(path, headers={"Remote-User": user})

    def test_the_proxy_says_who(self):
        r = self.get("10.1.2.3", "alice")
        self.assertEqual(r.json()["total"], 2)
        self.assertEqual(self.get("10.1.2.3", "bob").json()["total"], 2)
        self.assertEqual(self.get("10.1.2.3", "bob", "/api/items/ALICE1").status_code, 404)

    def test_a_script_past_the_proxy_brings_a_token(self):
        from app import accounts

        conn = open_db(self.settings.db_path)
        token, _ = accounts.make_token(
            conn, must(accounts.by_name(conn, "bob")), "s", ["read:library"]
        )
        conn.close()
        auth = {"Authorization": f"Bearer {token}"}
        r = self.from_address("192.168.1.5").get("/api/items/BOBBY1", headers=auth)
        self.assertEqual(r.status_code, 200, r.text)
        # The proxy's word comes first when it gives one.
        r = self.from_address("10.1.2.3").get(
            "/api/items/BOBBY1", headers={**auth, "Remote-User": "alice"}
        )
        self.assertEqual(r.status_code, 404)

    def test_a_header_from_anywhere_else_is_ignored(self):
        self.assertEqual(self.get("192.168.1.5", "alice").status_code, 401)
        self.assertEqual(self.from_address("10.1.2.3").get("/api/items").status_code, 401)

    def test_a_name_with_no_account(self):
        self.assertEqual(self.get("10.1.2.3", "carol").status_code, 403)
        self.settings = dataclasses.replace(self.settings, proxy_creates=True)
        with mock.patch.object(self.main, "settings", self.settings):
            r = self.get("10.1.2.3", "carol", "/api/me")
        self.assertEqual((r.status_code, r.json()["username"]), (200, "carol"))
        self.assertFalse(r.json()["is_admin"])


if __name__ == "__main__":
    unittest.main()
