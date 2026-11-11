import io
import json
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from typing import ClassVar
from unittest import mock

from app.db import open_db
from app.importer.service import import_export
from app.places import PLACE_V, worker
from app.places.geocoder import (
    CONFIDENT,
    GEO_V,
    GeocodeError,
    Nominatim,
    Region,
    confidence,
    locate,
    queries,
)
from app.places.service import Locator, locating, places_lock
from tests.fixtures import must, post_record, saved_posts, write_zip

R = "https://www.instagram.com/reel/"
PIN = "\U0001f4cd"


def hit(lat, lon, importance=0.5, rank=30, name="somewhere", kind="restaurant"):
    return {
        "lat": str(lat),
        "lon": str(lon),
        "importance": importance,
        "place_rank": rank,
        "display_name": name,
        "type": kind,
    }


class TestQueries(unittest.TestCase):
    def test_the_place_as_written_comes_first(self):
        self.assertEqual(queries("Bangor, Maine"), ["Bangor, Maine"])

    def test_handles_and_asides_are_dropped(self):
        self.assertEqual(
            queries("Mackinac Island, Michigan @example.travel")[0], "Mackinac Island, Michigan"
        )
        self.assertEqual(queries("Big Sur (@example.coast)"), ["Big Sur"])
        self.assertEqual(queries("Queenstown → Wanaka (2 nights)")[0], "Queenstown → Wanaka")

    def test_an_address_is_tried_without_its_venue_and_then_the_venue(self):
        tries = queries("Mabel's Deli — 118 Water St, New York, NY 10005")
        self.assertEqual(tries[1], "118 Water St, New York, NY 10005")
        self.assertEqual(tries[-1], "Mabel's Deli")

    def test_a_label_is_not_a_venue(self):
        self.assertEqual(
            queries("Location: 200 North Pine Street, Mendocino, CA"),
            [
                "Location: 200 North Pine Street, Mendocino, CA",
                "200 North Pine Street, Mendocino, CA",
            ],
        )

    def test_a_town_is_never_cut_down_to_its_state(self):
        self.assertEqual(queries("Hot Springs, Arkansas"), ["Hot Springs, Arkansas"])

    def test_an_address_is_tried_without_its_suite(self):
        self.assertIn(
            "2200 N Oak St, Plano, TX 75074",
            queries("2200 N Oak St, Ste 130, Plano, TX 75074"),
        )
        self.assertIn("4400 W Camelback Rd", queries("4400 W Camelback Rd Unit A-102"))

    def test_a_venue_is_dropped_ahead_of_a_street_number(self):
        self.assertIn(
            "2-8-5 Ebisu, Shibuya, Tokyo",
            queries("Hinoki Grill, 2-8-5 Ebisu, Shibuya, Tokyo"),
        )


class TestConfidence(unittest.TestCase):
    def test_one_answer_is_certain(self):
        self.assertEqual(confidence([hit(43.66, -70.26)]), 1.0)

    def test_answers_that_agree_are_certain(self):
        """A park and its visitor centre, a few km apart."""
        self.assertEqual(confidence([hit(29.3, -103.2), hit(29.33, -103.25)]), 1.0)

    def test_answers_far_apart_are_a_guess(self):
        """ "Springfield": one in Illinois, others across the country."""
        spread = [hit(39.8, -89.6), hit(40.7, -74.0), hit(41.9, -87.6), hit(34.0, -118.2)]
        self.assertLess(confidence(spread), CONFIDENT)

    def test_a_famous_answer_outweighs_obscure_ones(self):
        famous = [
            hit(43.66, -70.26, importance=0.7),
            hit(45.5, -122.7, importance=0.02),
            hit(38.1, -85.7, importance=0.02),
        ]
        self.assertGreaterEqual(confidence(famous), CONFIDENT)

    def test_a_country_is_no_place_for_a_pin(self):
        self.assertLess(confidence([hit(36, 138, rank=4, kind="country")]), CONFIDENT)

    def test_nothing_is_no_confidence(self):
        self.assertEqual(confidence([]), 0.0)


class TestLocate(unittest.TestCase):
    def test_falls_back_to_the_next_query(self):
        asked = []

        def search(q):
            asked.append(q)
            return [hit(30.27, -98.87, name="310 West Main Street")] if q.startswith("310") else []

        found = must(
            locate("TICKET CHECK-IN LOCATION // 310 W Main St, Fredericksburg, TX 78624", search)
        )
        self.assertEqual(found.query, "310 W Main St, Fredericksburg, TX 78624")
        self.assertEqual((found.lat, found.lng), (30.27, -98.87))
        self.assertEqual(len(asked), 2)

    def test_nothing_found(self):
        self.assertIsNone(locate("Where to stay", lambda q: []))


# Beaches and bays from Northland to Canterbury, up to 900 km apart.
NZ = [
    (-36.83, 174.42),
    (-36.95, 174.47),
    (-35.2, 174.2),
    (-43.57, 172.77),
    (-39.09, 177.91),
    (-42.8, 173.3),
]


class TestRegion(unittest.TestCase):
    def test_needs_two_sure_places(self):
        self.assertIsNone(Region.of(NZ[:1]))
        self.assertIsNotNone(Region.of(NZ[:2]))

    def test_one_place_far_away_does_not_move_it(self):
        region = must(Region.of([*NZ, (35.9, -86.9)]))  # and one in Tennessee
        self.assertTrue(region.holds(-41.3, 174.8))  # Wellington
        self.assertFalse(region.holds(35.9, -86.9))

    def test_a_country_wide_list_is_no_region(self):
        """Oregon, Hawaii, Maine, New York: the best of the US, all over it."""
        self.assertIsNone(
            Region.of(
                [(45.9, -123.96), (22.17, -159.65), (43.66, -70.26), (44.5, -73.46), (37.0, -114.5)]
            )
        )

    def test_its_box_is_west_north_east_south(self):
        west, north, east, south = map(float, must(Region.of(NZ)).box.split(","))
        self.assertLess(west, 172.77)
        self.assertGreater(east, 174.47)
        self.assertGreater(north, -35.2)
        self.assertLess(south, -43.57)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class TestNominatim(unittest.TestCase):
    def test_asks_politely_and_no_faster_than_the_delay(self):
        sent = []

        def opener(req, timeout):
            sent.append(req)
            return FakeResponse(json.dumps([hit(1, 2)]).encode())

        clock = iter([0.0, 0.0, 0.2, 1.2])
        with (
            mock.patch.object(time, "monotonic", side_effect=lambda: next(clock)),
            mock.patch.object(time, "sleep") as sleep,
        ):
            n = Nominatim("https://geo.example/", delay=1.0, opener=opener)
            n.search("Osaka")
            n.search("Kyoto")
        sleep.assert_called_once()
        self.assertAlmostEqual(sleep.call_args[0][0], 0.8)
        self.assertTrue(sent[0].full_url.startswith("https://geo.example/search?q=Osaka"))
        self.assertIn("Hikidashi", sent[0].get_header("User-agent"))

    def test_a_box_bounds_the_search(self):
        sent = []

        def opener(req, timeout):
            sent.append(req.full_url)
            return FakeResponse(b"[]")

        Nominatim("https://geo.example", delay=0, opener=opener).search("Mercer Bay", box="1,2,3,4")
        self.assertIn("viewbox=1%2C2%2C3%2C4", sent[0])
        self.assertIn("bounded=1", sent[0])

    def test_an_unreachable_server_is_an_error(self):
        def opener(req, timeout):
            raise urllib.error.URLError("refused")

        with self.assertRaises(GeocodeError):
            Nominatim("https://geo.example", delay=0, opener=opener).search("x")

    def test_an_answer_that_is_not_a_list_is_an_error(self):
        for body in (b"<html>", b'{"error": "rate limited"}'):

            def opener(req, timeout, body=body):
                return FakeResponse(body)

            with self.assertRaises(GeocodeError):
                Nominatim("https://geo.example", delay=0, opener=opener).search("x")


class WorkerCase(unittest.TestCase):
    """Three reels: two name Mabel's, one names nothing."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.conn = open_db(root / "t.sqlite3")
        deli = f"{PIN}Mabel's Deli — 118 Water St, New York, NY 10005"
        records = [
            post_record(R + "A/", timestamp=3, caption=f"Pastrami {deli} {PIN}Bangor, Maine"),
            post_record(R + "B/", timestamp=2, caption=f"Again {deli}"),
            post_record(R + "C/", timestamp=1, caption="no place here"),
        ]
        import_export(self.conn, write_zip(root / "e.zip", saved_posts(records)), user_id=1)

    def places(self, code):
        return [
            r[0]
            for r in self.conn.execute(
                "SELECT text FROM item_place WHERE shortcode = ? ORDER BY seq", (code,)
            )
        ]


class TestRead(WorkerCase):
    def test_reads_every_caption_once(self):
        self.assertEqual(worker.read_all(self.conn), 3)
        self.assertEqual(
            self.places("A"),
            ["Mabel's Deli — 118 Water St, New York, NY 10005", "Bangor, Maine"],
        )
        self.assertEqual(self.places("C"), [])
        self.assertEqual(self.conn.execute("SELECT MIN(place_v) FROM item").fetchone()[0], PLACE_V)
        self.assertEqual(worker.read_all(self.conn), 0)

    def test_a_reel_with_no_caption_yet_waits_for_one(self):
        self.conn.execute("UPDATE item SET caption = NULL WHERE shortcode = 'B'")
        self.assertEqual(worker.read_all(self.conn), 2)
        self.assertEqual(worker.summary(self.conn)["unread"], 0)
        self.conn.execute(
            "UPDATE item SET caption = 'Fetched later. ' || ? WHERE shortcode = 'B'",
            (f"{PIN}Bangor, Maine",),
        )
        self.assertEqual(worker.read_all(self.conn), 1, "enrich brought a caption: read it")
        self.assertEqual(self.places("B"), ["Bangor, Maine"])

    def test_force_reads_again_without_doubling(self):
        worker.read_all(self.conn)
        self.assertEqual(worker.read_all(self.conn, force=True), 3)
        self.assertEqual(len(self.places("A")), 2)


class TestLocateAll(WorkerCase):
    def setUp(self):
        super().setUp()
        worker.read_all(self.conn)

    def test_a_place_two_reels_name_is_looked_up_once(self):
        pending = worker.pending(self.conn)
        self.assertEqual(len(pending), 2)
        self.assertTrue(pending[0].startswith("Mabel"), "the most-named goes first")

    def test_stores_what_it_finds_and_what_it_does_not(self):
        def search(q):
            return [hit(43.66, -70.26)] if q == "Bangor, Maine" else []

        result = worker.locate_all(self.conn, search)
        self.assertEqual((result.located, result.missing), (1, 1))
        row = self.conn.execute("SELECT * FROM geocode WHERE text = 'Bangor, Maine'").fetchone()
        self.assertEqual((row["lat"], row["lng"], row["geo_v"]), (43.66, -70.26, GEO_V))
        self.assertEqual(worker.pending(self.conn), [], "a miss is not asked again")

        s = worker.summary(self.conn)
        self.assertEqual(
            (s["reels"], s["places"], s["located"], s["missing"], s["waiting"]), (2, 2, 1, 1, 0)
        )

    def test_an_unsure_answer_is_kept_but_counted_apart(self):
        spread = [hit(39.8, -89.6), hit(40.7, -74.0), hit(41.9, -87.6), hit(34.0, -118.2)]
        result = worker.locate_all(self.conn, lambda q: spread)
        self.assertEqual(result.approximate, 2)
        self.assertEqual(worker.summary(self.conn)["approximate"], 2)

    def test_a_server_error_stops_the_run_and_loses_nothing(self):
        calls = []

        def search(q):
            calls.append(q)
            if len(calls) > 1:
                raise GeocodeError("down")
            return [hit(40.72, -73.99)]

        with self.assertRaises(GeocodeError):
            worker.locate_all(self.conn, search)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM geocode").fetchone()[0], 1)
        self.assertEqual(worker.pending(self.conn), ["Bangor, Maine"])

    def test_a_removed_reel_takes_its_places_with_it(self):
        from app import library

        library.remove(self.conn, 1, "B")
        self.assertEqual(self.places("B"), [])


class TestRefine(unittest.TestCase):
    """One reel lists five New Zealand spots. OSM's "Barnett Cave" is a farm in
    Tennessee; "Oke Bay" is unsure, with candidates around the world."""

    SPOTS: ClassVar[list[str]] = [
        "Muriwai Beach",
        "Karekare",
        "Oke Bay",
        "Taylors Mistake",
        "Mahia Beach",
        "Cathedral Gully",
        "Barnett Cave",
        "Mercer Bay",
    ]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.conn = open_db(root / "t.sqlite3")
        caption = " ".join(f"{PIN}{s}" for s in self.SPOTS)
        import_export(
            self.conn,
            write_zip(
                root / "e.zip", saved_posts([post_record(R + "NZ/", timestamp=1, caption=caption)])
            ),
            user_id=1,
        )
        worker.read_all(self.conn)
        answers = {
            "Muriwai Beach": [hit(*NZ[0])],
            "Karekare": [hit(*NZ[1])],
            "Taylors Mistake": [hit(*NZ[3])],
            "Mahia Beach": [hit(*NZ[4])],
            "Cathedral Gully": [hit(*NZ[5])],
            "Barnett Cave": [hit(35.9, -86.9)],
            "Oke Bay": [hit(-35.2, 174.2), hit(50, 0), hit(40, -100)],
            "Mercer Bay": [hit(-54.2, -36.5)],
        }
        worker.locate_all(self.conn, lambda q: answers.get(q, []))

    def where(self, text):
        return self.conn.execute("SELECT * FROM geocode WHERE text = ?", (text,)).fetchone()

    def test_moves_an_outlier_found_inside_the_region(self):
        asked = []

        def search(q, box=None):
            asked.append((q, box))
            return (
                [hit(-43.55, 172.7, name="Barnett Park Cave Track")] if q == "Barnett Cave" else []
            )

        result = worker.refine(self.conn, search)
        row = self.where("Barnett Cave")
        self.assertEqual((row["lat"], row["label"]), (-43.55, "Barnett Park Cave Track"))
        self.assertGreaterEqual(row["confidence"], CONFIDENT)
        self.assertTrue(all(box for _, box in asked), "every second look is bounded")
        self.assertNotIn("Muriwai Beach", [q for q, _ in asked], "sure and inside: left alone")
        self.assertEqual(result.moved, 1)

    def test_an_outlier_with_no_match_inside_comes_off_the_map(self):
        result = worker.refine(self.conn, lambda q, box=None: [])
        self.assertEqual(result.doubted, 2)  # Tennessee and South Georgia
        self.assertLess(self.where("Barnett Cave")["confidence"], CONFIDENT)
        self.assertEqual(self.where("Barnett Cave")["lat"], 35.9)

    def test_an_unsure_place_inside_the_region_is_made_sure(self):
        worker.refine(
            self.conn, lambda q, box=None: [hit(-35.21, 174.21)] if q == "Oke Bay" else []
        )
        self.assertGreaterEqual(self.where("Oke Bay")["confidence"], CONFIDENT)

    def test_nothing_is_looked_at_twice(self):
        worker.refine(self.conn, lambda q, box=None: [])
        asked = []

        def ask(q, box=None):
            asked.append(q)
            return []

        worker.refine(self.conn, ask)
        self.assertEqual(asked, [])


class TestLocator(WorkerCase):
    def setUp(self):
        super().setUp()
        self.db = Path(self.tmp.name) / "t.sqlite3"
        self.run_dir = Path(self.tmp.name) / "run"
        self.asked = []

    def search(self, q, box=None):
        self.asked.append(q)
        return [hit(43.66, -70.26)] if q == "Bangor, Maine" else []

    def locator(self, search=None, **kw):
        return Locator(
            lambda: open_db(self.db),
            search or self.search,
            self.run_dir,
            every=kw.pop("every", 300),
            **kw,
        )

    def test_a_pass_reads_and_looks_up_what_is_new_then_nothing(self):
        found = self.locator()
        self.assertEqual(found.run_once(), 2)
        self.assertEqual(worker.summary(self.conn)["located"], 1)
        self.asked.clear()
        self.assertEqual(found.run_once(), 0)
        self.assertEqual(self.asked, [], "nothing new: the geocoder is not asked")

    def test_a_new_reel_is_located_on_the_next_pass(self):
        found = self.locator()
        found.run_once()
        self.conn.execute(
            "UPDATE item SET caption = ?, place_v = 0 WHERE shortcode = 'C'",
            (f"Lobster rolls {PIN}Bar Harbor, Maine",),
        )
        self.asked.clear()
        self.assertEqual(found.run_once(), 1)
        self.assertEqual(self.asked[0], "Bar Harbor, Maine")

    def test_one_locator_at_a_time(self):
        with places_lock(self.run_dir):
            self.assertTrue(locating(self.run_dir))
            self.assertEqual(self.locator().run_once(), 0)
            self.assertEqual(self.asked, [])
            self.assertEqual(self.places("A")[1], "Bangor, Maine", "reading needs no lock")
        self.assertFalse(locating(self.run_dir))
        self.assertEqual(self.locator().run_once(), 2)

    def test_a_failing_geocoder_is_asked_less_and_less_often(self):
        def down(q, box=None):
            raise GeocodeError("down")

        found = self.locator(down, backoff=(60, 200))
        self.assertEqual([found.step() for _ in range(4)], [60, 120, 200, 200])
        found.search = self.search
        self.assertEqual(found.step(), 300, "back to the usual wait once it answers")
        self.assertEqual(found.failures, 0)

    def test_a_wake_starts_a_pass_without_waiting(self):
        passed = threading.Event()

        def search(q, box=None):
            passed.set()
            return []

        found = self.locator(search, every=3600)
        found.run_once()  # the first pass, at startup
        self.conn.execute(
            "UPDATE item SET caption = ?, place_v = 0 WHERE shortcode = 'C'",
            (f"{PIN}Bar Harbor, Maine",),
        )
        passed.clear()
        found.start()
        self.addCleanup(found.stop)
        self.assertTrue(passed.wait(5), "a thread's first pass runs at once")
        passed.clear()
        self.conn.execute(
            "UPDATE item SET caption = ?, place_v = 0 WHERE shortcode = 'C'",
            (f"{PIN}Acadia, Maine",),
        )
        found.wake()
        self.assertTrue(passed.wait(5), "woken, it looks again within the hour")


if __name__ == "__main__":
    unittest.main()
