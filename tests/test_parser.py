import unittest

from app.importer.parser import (
    fix_mojibake,
    parse_collections,
    parse_record,
    parse_saved_posts,
)
from tests.fixtures import legacy_saved_posts, must, post_record, saved_collections, saved_posts

R = "https://www.instagram.com/reel/"


class TestMojibake(unittest.TestCase):
    def test_repairs_instagram_double_encoding(self):
        self.assertEqual(fix_mojibake("cafÃ©"), "café")
        # How a sparkle emoji actually arrives in the export.
        self.assertEqual(fix_mojibake("â¨"), "✨")

    def test_leaves_clean_text_alone(self):
        for text in ["plain ascii", "café", "日本語", "", None]:
            with self.subTest(text=text):
                self.assertEqual(fix_mojibake(text), text)


class TestParseRecord(unittest.TestCase):
    def test_reads_every_field_of_the_real_shape(self):
        rec = post_record(
            R + "DcTestCode1/",
            timestamp=1792327526,
            caption="âbest alfredo",
            username="examplefoodie",
            name="Sam | Example Food",
            hashtags=("examplecityfood", "examplefoodie"),
            fbid="17900000000000001",
        )
        post = must(parse_record(rec))
        self.assertEqual(post.shortcode, "DcTestCode1")
        self.assertEqual(post.author, "examplefoodie")
        self.assertEqual(post.author_name, "Sam | Example Food")
        self.assertEqual(post.saved_at, 1792327526)
        self.assertEqual(post.fbid, "17900000000000001")
        self.assertEqual(post.hashtags, ["examplecityfood", "examplefoodie"])
        self.assertIn("best alfredo", must(post.caption))
        self.assertNotIn("â", must(post.caption))  # mojibake repaired
        self.assertEqual(post.raw, rec)  # kept verbatim for re-parsing

    def test_title_is_never_used_as_the_author(self):
        """Title is present on every record and always empty."""
        rec = post_record(R + "AAA111/", timestamp=1, caption="x", username="realhandle")
        self.assertEqual(must(parse_record(rec)).author, "realhandle")

    def test_two_captions_keeps_the_longest(self):
        rec = post_record(
            R + "AAA111/",
            timestamp=1,
            caption="short one",
            extra_caption="a noticeably longer caption variant",
        )
        self.assertEqual(must(parse_record(rec)).caption, "a noticeably longer caption variant")

    def test_missing_owner_and_hashtags_are_tolerated(self):
        rec = post_record(R + "AAA111/", timestamp=1)
        post = must(parse_record(rec))
        self.assertIsNone(post.author)
        self.assertIsNone(post.caption)
        self.assertEqual(post.hashtags, [])

    def test_reads_a_record_embedded_in_a_collection(self):
        """Collections key the same field list "dict" instead of "label_values"."""
        rec = post_record(R + "AAA111/", timestamp=1, username="slowtravel", caption="kyoto route")
        member = {"title": "", "dict": rec["label_values"]}
        post = must(parse_record(member))
        self.assertEqual(post.shortcode, "AAA111")
        self.assertEqual(post.author, "slowtravel")
        self.assertEqual(post.caption, "kyoto route")

    def test_rejects_anything_without_a_permalink(self):
        self.assertIsNone(parse_record({"label_values": [{"label": "Caption", "value": "x"}]}))
        self.assertIsNone(parse_record({"nope": 1}))
        self.assertIsNone(parse_record("not a dict"))


class TestParseSavedPosts(unittest.TestCase):
    def test_bare_list_in_the_real_shape(self):
        data = saved_posts(
            [
                post_record(R + "AAA111/", timestamp=100, username="bay7.garage", caption="a"),
                post_record(R + "BBB222/", timestamp=200, username="ramen.lab", caption="b"),
            ]
        )
        result = parse_saved_posts(data)
        self.assertEqual(result.strategy, "label-values")
        self.assertEqual([p.shortcode for p in result.posts], ["AAA111", "BBB222"])
        self.assertEqual(result.posts[0].author, "bay7.garage")

    def test_photo_posts_are_kept_alongside_reels(self):
        data = saved_posts(
            [
                post_record("https://www.instagram.com/p/PHOTO1/", timestamp=1, caption="p"),
                post_record(R + "REEL01/", timestamp=2, caption="r"),
            ]
        )
        self.assertEqual([p.shortcode for p in parse_saved_posts(data).posts], ["PHOTO1", "REEL01"])

    def test_counts_records_with_no_usable_url(self):
        data = saved_posts([post_record(R + "AAA111/", timestamp=1)])
        data.append({"fbid": "1", "timestamp": 2, "media": [], "label_values": []})
        result = parse_saved_posts(data)
        self.assertEqual(len(result.posts), 1)
        self.assertEqual(result.unparsed, 1)

    def test_still_reads_the_older_documented_shape(self):
        result = parse_saved_posts(legacy_saved_posts([("creator", R + "OLD111/", 9)]))
        self.assertEqual(result.strategy, "string-map")
        self.assertEqual(result.posts[0].shortcode, "OLD111")
        self.assertEqual(result.posts[0].author, "creator")

    def test_falls_back_when_meta_reshapes_the_file_again(self):
        data = {"some_future_key": [{"whatever": {"href": R + "ZZZ999/", "timestamp": 1730000000}}]}
        result = parse_saved_posts(data)
        self.assertEqual(result.strategy, "generic-walk")
        self.assertEqual(result.posts[0].shortcode, "ZZZ999")


class TestParseCollections(unittest.TestCase):
    def test_extracts_names_members_and_embedded_records(self):
        data = saved_collections(
            {
                "Travel": [
                    post_record(R + "AAA111/", timestamp=1, username="slowtravel", caption="kyoto")
                ],
                "Recipes": [
                    post_record(R + "BBB222/", timestamp=2, username="crumb"),
                    post_record(R + "CCC333/", timestamp=3, username="ramen"),
                ],
            }
        )
        pairs, names, embedded = parse_collections(data)

        self.assertEqual(names, {"Travel", "Recipes"})
        self.assertIn(("Travel", "AAA111"), pairs)
        self.assertIn(("Recipes", "CCC333"), pairs)
        self.assertEqual(len(pairs), 3)

        by_code = {p.shortcode: p for p in embedded}
        self.assertEqual(by_code["AAA111"].author, "slowtravel")
        self.assertEqual(by_code["AAA111"].caption, "kyoto")

    def test_a_reel_is_counted_once_per_collection(self):
        data = saved_collections({"Travel": [post_record(R + "AAA111/", timestamp=1)]})
        pairs, _, _ = parse_collections(data)
        self.assertEqual(pairs, [("Travel", "AAA111")])

    def test_empty_collection_still_reports_its_name(self):
        pairs, names, embedded = parse_collections(saved_collections({"Someday": []}))
        self.assertEqual(names, {"Someday"})
        self.assertEqual(pairs, [])
        self.assertEqual(embedded, [])


if __name__ == "__main__":
    unittest.main()
