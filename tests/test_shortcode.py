import unittest

from app.importer.shortcode import extract, is_permalink


class TestShortcode(unittest.TestCase):
    def test_accepts_every_post_url_form(self):
        cases = {
            "https://www.instagram.com/reel/DQx7k2hZ/": "DQx7k2hZ",
            "https://instagram.com/p/Cab-1_x/": "Cab-1_x",
            "http://www.instagram.com/tv/XYZ123/": "XYZ123",
            "https://www.instagram.com/reels/AbC_dEf/": "AbC_dEf",
            "https://www.instagram.com/reel/DQx7k2hZ": "DQx7k2hZ",
            "https://www.instagram.com/reel/DQx7k2hZ/?igsh=abc123": "DQx7k2hZ",
            "https://www.instagram.com/en/reel/DQx7k2hZ/": "DQx7k2hZ",
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(extract(url), expected)

    def test_rejects_non_post_urls(self):
        for url in [
            None,
            "",
            "https://www.instagram.com/some.user/",
            "https://www.instagram.com/stories/someone/123/",
            "https://example.com/reel/abc/",
        ]:
            with self.subTest(url=url):
                self.assertIsNone(extract(url))
                self.assertFalse(is_permalink(url))


if __name__ == "__main__":
    unittest.main()
