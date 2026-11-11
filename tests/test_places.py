import unittest
from urllib.parse import unquote_plus

from app.places import extract

PIN = "\U0001f4cd"


def texts(caption):
    return [p.text for p in extract(caption)]


class TestStreetAddresses(unittest.TestCase):
    def test_full_us_address_with_suite(self):
        caption = (
            "Where: @litehousegrilldfw // 11501 Custer Rd, Ste 109, "
            "Frisco, TX 75035\n\nTheir fettuccine alfredo was unreal"
        )
        self.assertIn("11501 Custer Rd, Ste 109, Frisco, TX 75035", texts(caption))

    def test_address_stops_before_the_prose_that_follows(self):
        caption = "706 N Lamar Blvd, Austin, TX 78703 and honestly it slaps"
        found = texts(caption)[0]
        self.assertNotIn("slaps", found)
        self.assertIn("Austin", found)

    def test_several_addresses_in_one_caption(self):
        caption = (
            f"{PIN}Russ & Daughters — 179 E Houston St, New York, NY 10002 "
            f"{PIN}Katz's Delicatessen — 205 E Houston St, New York, NY 10002 "
            f"{PIN}Mei Lai Wah — 41 Mott St, New York, NY 10013"
        )
        found = texts(caption)
        self.assertEqual(len(found), 3)
        self.assertTrue(all("St, New York" in f or "Mott St" in f for f in found))

    def test_venue_name_is_kept_alongside_the_address(self):
        """ "Katz's Delicatessen — 205 E Houston St" beats either half alone."""
        caption = f"{PIN}Katz's Delicatessen — 205 E Houston St, New York, NY 10002"
        self.assertEqual(
            texts(caption), ["Katz's Delicatessen — 205 E Houston St, New York, NY 10002"]
        )


class TestPinnedPlaces(unittest.TestCase):
    def test_a_pinned_place_name(self):
        self.assertEqual(texts(f"{PIN}Monument Valley, Arizona"), ["Monument Valley, Arizona"])

    def test_a_description_after_the_name_is_dropped(self):
        caption = (
            f"{PIN}Palo Duro Canyon — dramatic canyon views, hiking and one "
            "of the most incredible sunrises in the state"
        )
        self.assertEqual(texts(caption), ["Palo Duro Canyon"])

    def test_trailing_flags_and_handles_are_stripped(self):
        self.assertEqual(
            texts(f"{PIN}Mackinac Island, Michigan \U0001f1fa\U0001f1f8"),
            ["Mackinac Island, Michigan"],
        )

    def test_prose_after_a_pin_is_not_a_place(self):
        caption = (
            f"{PIN}At some point everyone experiences loss, disappointment, "
            "betrayal, or a situation that feels impossible to recover from"
        )
        self.assertEqual(texts(caption), [])

    def test_itinerary_markers_are_not_places(self):
        self.assertEqual(texts(f"{PIN}STOP 1 {PIN}STOP 2"), [])

    def test_other_emoji_do_not_act_as_pins(self):
        """A variation selector inside a character class would match everything."""
        caption = "\U0001f31f to learn valuable life lessons every time you open this"
        self.assertEqual(texts(caption), [])

    def test_keycap_emoji_are_not_pins(self):
        self.assertEqual(texts("1️⃣ Krov is the best bakery"), [])


class TestLabels(unittest.TestCase):
    def test_where_label(self):
        self.assertIn("Cedar Park", texts("Where: Cedar Park"))

    def test_handle_prefix_is_stripped(self):
        caption = "Location: @khashoka.USA // 1057 S Sherman St, Richardson, TX"
        self.assertTrue(texts(caption)[0].startswith("1057 S Sherman"))


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
        caption = f"{PIN}Katz's — 205 E Houston St, New York, NY 10002"
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
        d = extract(f"{PIN}Cedar Park")[0].as_dict()
        self.assertEqual(set(d), {"text", "url"})


if __name__ == "__main__":
    unittest.main()
