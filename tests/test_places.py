import unittest
from urllib.parse import unquote_plus

from app.places import extract

PIN = "\U0001f4cd"


def texts(caption):
    return [p.text for p in extract(caption)]


class TestStreetAddresses(unittest.TestCase):
    def test_full_us_address_with_suite(self):
        caption = (
            "Where: @examplegrill // 8800 Preston Rd, Ste 240, "
            "Frisco, TX 75034\n\nThe pasta alone is worth the drive"
        )
        self.assertIn("8800 Preston Rd, Ste 240, Frisco, TX 75034", texts(caption))

    def test_address_stops_before_the_prose_that_follows(self):
        caption = "1420 S Congress Ave, Austin, TX 78704 and honestly it slaps"
        found = texts(caption)[0]
        self.assertNotIn("slaps", found)
        self.assertIn("Austin", found)

    def test_several_addresses_in_one_caption(self):
        caption = (
            f"{PIN}Pearl & Sons — 140 Water St, New York, NY 10005 "
            f"{PIN}Mabel's Deli — 118 Water St, New York, NY 10005 "
            f"{PIN}Golden Bun — 27 Pell St, New York, NY 10038"
        )
        found = texts(caption)
        self.assertEqual(len(found), 3)
        self.assertTrue(all("St, New York" in f or "Pell St" in f for f in found))

    def test_venue_name_is_kept_alongside_the_address(self):
        """ "Mabel's Deli — 118 Water St" beats either half alone."""
        caption = f"{PIN}Mabel's Deli — 118 Water St, New York, NY 10005"
        self.assertEqual(texts(caption), ["Mabel's Deli — 118 Water St, New York, NY 10005"])


class TestPinnedPlaces(unittest.TestCase):
    def test_a_pinned_place_name(self):
        self.assertEqual(texts(f"{PIN}Monument Valley, Arizona"), ["Monument Valley, Arizona"])

    def test_a_description_after_the_name_is_dropped(self):
        caption = (
            f"{PIN}Cedar Hollow Canyon — red rock walls, a long hike and the "
            "best sunrise of the trip"
        )
        self.assertEqual(texts(caption), ["Cedar Hollow Canyon"])

    def test_trailing_flags_and_handles_are_stripped(self):
        self.assertEqual(
            texts(f"{PIN}Mackinac Island, Michigan \U0001f1fa\U0001f1f8"),
            ["Mackinac Island, Michigan"],
        )

    def test_prose_after_a_pin_is_not_a_place(self):
        caption = (
            f"{PIN}Sooner or later every plan falls apart, and the trip you "
            "remember is the one that went sideways"
        )
        self.assertEqual(texts(caption), [])

    def test_itinerary_markers_are_not_places(self):
        self.assertEqual(texts(f"{PIN}STOP 1 {PIN}STOP 2"), [])

    def test_other_emoji_do_not_act_as_pins(self):
        """A variation selector inside a character class would match everything."""
        caption = "\U0001f31f to pick up one new trick every time you open this"
        self.assertEqual(texts(caption), [])

    def test_keycap_emoji_are_not_pins(self):
        self.assertEqual(texts("1️⃣ Krov is the best bakery"), [])


class TestLabels(unittest.TestCase):
    def test_where_label(self):
        self.assertIn("Oak Hollow", texts("Where: Oak Hollow"))

    def test_handle_prefix_is_stripped(self):
        caption = "Location: @example.usa // 2200 N Oak St, Plano, TX"
        self.assertTrue(texts(caption)[0].startswith("2200 N Oak"))


class TestHygiene(unittest.TestCase):
    def test_hashtags_are_never_part_of_a_place(self):
        found = texts(f"{PIN}Cannon Beach, Oregon #travel #oregoncoast")
        self.assertEqual(found, ["Cannon Beach, Oregon"])

    def test_urls_and_bare_handles_are_rejected(self):
        self.assertEqual(texts(f"{PIN}https://example.com/menu"), [])
        self.assertEqual(texts(f"{PIN}@someplace"), [])

    def test_nothing_from_an_empty_or_missing_caption(self):
        self.assertEqual(extract(None), [])
        self.assertEqual(extract(""), [])
        self.assertEqual(extract("just a normal caption with no venue"), [])

    def test_duplicates_and_contained_matches_collapse(self):
        caption = f"{PIN}Mabel's — 118 Water St, New York, NY 10005"
        self.assertEqual(len(texts(caption)), 1)

    def test_results_cap(self):
        caption = " ".join(f"{PIN}Place {c}, Texas" for c in "ABCDEFGHIJKLMNOP")
        self.assertLessEqual(len(texts(caption)), 12)


class TestMapsUrl(unittest.TestCase):
    def test_url_round_trips_the_text(self):
        place = extract(f"{PIN}Monument Valley, Arizona")[0]
        self.assertTrue(place.url.startswith("https://www.google.com/maps/search/"))
        self.assertIn("Monument Valley, Arizona", unquote_plus(place.url))

    def test_as_dict_shape(self):
        d = extract(f"{PIN}Oak Hollow")[0].as_dict()
        self.assertEqual(set(d), {"text", "url"})


if __name__ == "__main__":
    unittest.main()
