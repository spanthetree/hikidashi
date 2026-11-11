import sqlite3
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest import mock

from app.db import open_db
from app.enrich import fetcher, worker
from app.enrich.fetcher import FETCH_V, FetchError, from_info, shard
from app.importer.service import import_export
from tests.fixtures import must, post_record, saved_posts, write_zip

R = "https://www.instagram.com/reel/"

# Trimmed from a real yt-dlp response.
INFO = {
    "id": "AAA111",
    "title": "Video by bay7.garage",
    "description": "brake pads without a press #diy #brakes",
    "channel": "bay7.garage",
    "uploader": "Bay 7 Garage",
    "uploader_id": "10203026324",
    "duration": 64.0,
    "width": 1080,
    "height": 1920,
    "like_count": 32297,
    "comment_count": 513,
    "thumbnail": "https://cdn.example/small.jpg",
    "thumbnails": [
        {"url": "https://cdn.example/small.jpg", "width": 320, "height": 568},
        {"url": "https://cdn.example/big.jpg", "width": 1080, "height": 1920},
    ],
}


class TestFromInfo(unittest.TestCase):
    def test_maps_the_fields_we_keep(self):
        got = from_info(INFO)
        self.assertEqual(got.shortcode, "AAA111")
        self.assertEqual(got.author, "bay7.garage")
        self.assertEqual(got.author_name, "Bay 7 Garage")
        self.assertEqual(got.duration, 64)  # float seconds -> int
        self.assertEqual(got.width, 1080)
        self.assertEqual(got.like_count, 32297)
        self.assertIn("brake pads", must(got.caption))

    def test_prefers_the_largest_thumbnail(self):
        self.assertEqual(from_info(INFO).thumbnail_url, "https://cdn.example/big.jpg")

    def test_falls_back_to_the_single_thumbnail_field(self):
        info = {**INFO, "thumbnails": []}
        self.assertEqual(from_info(info).thumbnail_url, "https://cdn.example/small.jpg")

    def test_tolerates_a_sparse_response(self):
        got = from_info({"id": "BBB222"})
        self.assertEqual(got.shortcode, "BBB222")
        self.assertIsNone(got.duration)
        self.assertIsNone(got.thumbnail_url)

    def test_a_carousel_child_id_never_becomes_the_key(self):
        """yt-dlp reports a child id for carousels; our shortcode must win."""
        got = from_info({**INFO, "id": "CHILD9"})
        self.assertEqual(got.shortcode, "CHILD9")  # informational only

    def test_shard_keeps_directories_small(self):
        self.assertEqual(shard("DcTestCode1"), "dc")
        self.assertEqual(shard(""), "__")


# A carousel's first slide, as `probe` sees it: no formats, and a count.
CAROUSEL = {**INFO, "formats": [], "playlist_count": 3}


def slide(n, *, video=False):
    return {
        "playlist_index": n,
        "formats": [{"url": "v.mp4"}] if video else [],
        "thumbnails": [{"url": f"https://cdn.example/{n}.jpg", "width": 1080, "height": 1350}],
    }


class TestSlides(unittest.TestCase):
    def test_a_carousel_of_photos_is_one_image_a_slide(self):
        self.assertEqual(
            fetcher.slides_from([slide(1), slide(2)]),
            ["https://cdn.example/1.jpg", "https://cdn.example/2.jpg"],
        )

    def test_a_carousel_with_a_video_in_it_is_not_paged_as_photos(self):
        self.assertEqual(fetcher.slides_from([slide(1), slide(2, video=True)]), [])

    def test_one_image_is_not_a_carousel(self):
        self.assertEqual(fetcher.slides_from([slide(1)]), [])
        self.assertFalse(fetcher.is_carousel(INFO))
        self.assertTrue(fetcher.is_carousel(CAROUSEL))
        self.assertFalse(fetcher.is_carousel({**CAROUSEL, "formats": [{"url": "v"}]}))

    def test_a_slide_with_no_image_drops_the_set(self):
        self.assertEqual(fetcher.slides_from([slide(1), {"playlist_index": 2}]), [])


class TestClassify(unittest.TestCase):
    def test_deleted_and_private_posts_are_permanent(self):
        for msg in [
            "ERROR: Video unavailable",
            "ERROR: This post is private",
            "ERROR: Requested content is not available, login required",
        ]:
            with self.subTest(msg=msg):
                self.assertTrue(fetcher._classify(msg).permanent)

    def test_transient_trouble_is_retryable(self):
        for msg in [
            "ERROR: Unable to download: connection timed out",
            "ERROR: HTTP Error 429: rate limit exceeded",
        ]:
            with self.subTest(msg=msg):
                self.assertFalse(fetcher._classify(msg).permanent)

    def test_restricted_audience_is_permanent(self):
        err = fetcher._classify("ERROR: This content isn't available to everyone")
        self.assertTrue(err.permanent)
        self.assertEqual(err.reason, "restricted audience")

    def test_reasons_are_readable_labels_not_raw_fragments(self):
        cases = {
            "ERROR: Video unavailable": "unavailable",
            "ERROR: This post is private": "private account",
            "ERROR: Instagram sent an empty media response. Check if this post "
            "is accessible in your browser without being logged-in": "login required",
            "ERROR: This content is no longer available": "deleted",
        }
        for stderr, label in cases.items():
            with self.subTest(label=label):
                self.assertEqual(fetcher._classify(stderr).reason, label)

    def test_reason_is_short_enough_for_a_column(self):
        self.assertLessEqual(len(fetcher._classify("x" * 500).reason), 200)


class EnrichCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.media = self.dir / "media"
        self.conn = open_db(self.dir / "test.sqlite3")
        zp = write_zip(
            self.dir / "e.zip",
            saved_posts(
                [
                    post_record(
                        R + "AAA111/",
                        timestamp=100,
                        username="bay7.garage",
                        caption="from the export",
                    ),
                    post_record(
                        R + "BBB222/",
                        timestamp=200,
                        username="ramen.lab",
                        caption="also from the export",
                    ),
                ]
            ),
        )
        import_export(self.conn, zp)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def row(self, code="AAA111") -> sqlite3.Row:
        return self.conn.execute("SELECT * FROM item WHERE shortcode = ?", (code,)).fetchone()

    def run_enrich(self, **kw):
        return worker.enrich(self.conn, self.media, concurrency=1, delay=0, **kw)


class TestSuccess(EnrichCase):
    def test_storage_uses_our_shortcode_not_the_reported_id(self):
        with (
            mock.patch.object(
                worker, "probe", return_value=from_info({**INFO, "id": "CAROUSEL_CHILD"})
            ),
            mock.patch.object(worker, "download_thumbnail", return_value=1),
        ):
            self.run_enrich()
        self.assertEqual(self.row()["thumb_path"], "thumbs/aa/AAA111.jpg")

    def test_fetch_fills_metadata_and_saves_a_thumbnail(self):
        def fake_thumb(url, dest, **kw):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"\xff\xd8jpeg")
            return 5

        with (
            mock.patch.object(worker, "probe", return_value=from_info(INFO)),
            mock.patch.object(worker, "download_thumbnail", side_effect=fake_thumb),
        ):
            result = self.run_enrich()

        self.assertEqual(result.fetched, 2)
        self.assertEqual(result.thumbs, 2)

        r = self.row()
        self.assertEqual(r["state"], "fetched")
        self.assertEqual(r["fetch_v"], FETCH_V)
        self.assertEqual(r["thumb_path"], "thumbs/aa/AAA111.jpg")
        self.assertEqual(r["duration"], 64)
        self.assertEqual(r["like_count"], 32297)
        self.assertTrue((self.media / r["thumb_path"]).exists())
        self.assertIsNotNone(r["fetch_meta"])

    def test_the_export_caption_wins_over_the_fetched_one(self):
        """The export is the earlier, more trustworthy source."""
        with (
            mock.patch.object(worker, "probe", return_value=from_info(INFO)),
            mock.patch.object(worker, "download_thumbnail", return_value=1),
        ):
            self.run_enrich()
        self.assertEqual(self.row()["caption"], "from the export")

    def test_a_second_run_has_nothing_to_do(self):
        with (
            mock.patch.object(worker, "probe", return_value=from_info(INFO)),
            mock.patch.object(worker, "download_thumbnail", return_value=1),
        ):
            self.run_enrich()
            again = self.run_enrich()
        self.assertEqual(again.total, 0)

    def test_metadata_survives_a_thumbnail_failure(self):
        with (
            mock.patch.object(worker, "probe", return_value=from_info(INFO)),
            mock.patch.object(worker, "download_thumbnail", side_effect=FetchError("404")),
        ):
            result = self.run_enrich()
        self.assertEqual(result.fetched, 2)
        self.assertEqual(result.thumbs, 0)
        r = self.row()
        self.assertEqual(r["state"], "fetched")
        self.assertIsNone(r["thumb_path"])

    def test_media_download_records_path_and_size(self):
        with (
            mock.patch.object(worker, "probe", return_value=from_info(INFO)),
            mock.patch.object(worker, "download_thumbnail", return_value=1),
            mock.patch.object(worker, "download_media", return_value=2048),
            mock.patch.object(worker, "probe_duration", return_value=64),
        ):
            result = self.run_enrich(with_media=True)
        self.assertEqual(result.media, 2)
        self.assertEqual(result.bytes, 4096)
        self.assertEqual(self.row()["media_path"], "video/aa/AAA111.mp4")
        self.assertEqual(self.row()["bytes"], 2048)

    def test_duration_comes_from_the_file_not_the_metadata(self):
        """Instagram reports no duration, so ffprobe on the download is the source."""
        sparse = from_info({k: v for k, v in INFO.items() if k != "duration"})
        self.assertIsNone(sparse.duration)
        with (
            mock.patch.object(worker, "probe", return_value=sparse),
            mock.patch.object(worker, "download_thumbnail", return_value=1),
            mock.patch.object(worker, "download_media", return_value=10),
            mock.patch.object(worker, "probe_duration", return_value=97),
        ):
            self.run_enrich(with_media=True)
        self.assertEqual(self.row()["duration"], 97)

    def test_duration_stays_null_without_a_media_download(self):
        sparse = from_info({k: v for k, v in INFO.items() if k != "duration"})
        with (
            mock.patch.object(worker, "probe", return_value=sparse),
            mock.patch.object(worker, "download_thumbnail", return_value=1),
        ):
            self.run_enrich()
        self.assertIsNone(self.row()["duration"])


class TestCarousel(EnrichCase):
    URLS: ClassVar[list[str]] = [
        "https://cdn.example/1.jpg",
        "https://cdn.example/2.jpg",
        "https://cdn.example/3.jpg",
    ]

    def slides(self, code="AAA111"):
        return [
            r[0]
            for r in self.conn.execute(
                "SELECT path FROM item_slide WHERE shortcode = ? ORDER BY position", (code,)
            )
        ]

    def test_fetching_a_carousel_saves_every_slide(self):
        with (
            mock.patch.object(worker, "probe", return_value=from_info(CAROUSEL)),
            mock.patch.object(worker, "probe_slides", return_value=self.URLS),
            mock.patch.object(worker, "download_thumbnail", return_value=1) as dl,
        ):
            result = self.run_enrich()
        self.assertEqual(result.slides, 6)
        self.assertEqual(self.slides(), [f"slides/aa/AAA111-{n}.jpg" for n in (1, 2, 3)])
        self.assertIn(
            mock.call(self.URLS[1], self.media / "slides/aa/AAA111-2.jpg"), dl.call_args_list
        )

    def test_a_reel_is_not_asked_for_slides(self):
        with (
            mock.patch.object(worker, "probe", return_value=from_info(INFO)),
            mock.patch.object(worker, "probe_slides") as slides,
            mock.patch.object(worker, "download_thumbnail", return_value=1),
        ):
            self.run_enrich()
        slides.assert_not_called()

    def test_one_slide_that_will_not_download_saves_none(self):
        def flaky(url, dest):
            if url.endswith("/3.jpg"):
                raise FetchError("thumbnail download failed")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"jpg")
            return 3

        with (
            mock.patch.object(worker, "probe", return_value=from_info(CAROUSEL)),
            mock.patch.object(worker, "probe_slides", return_value=self.URLS),
            mock.patch.object(worker, "download_thumbnail", side_effect=flaky),
        ):
            self.run_enrich()
        self.assertEqual(self.slides(), [])
        self.assertEqual(list((self.media / "slides").rglob("*.jpg")), [])
        self.assertEqual(self.row()["state"], "fetched")

    def test_media_only_fills_in_carousels_fetched_before_slides(self):
        with (
            mock.patch.object(worker, "probe", return_value=from_info(CAROUSEL)),
            mock.patch.object(worker, "probe_slides", return_value=[]),
            mock.patch.object(worker, "download_thumbnail", return_value=1),
        ):
            self.run_enrich()
        self.assertEqual(self.slides(), [])

        with (
            mock.patch.object(worker, "probe_slides", return_value=self.URLS),
            mock.patch.object(worker, "download_thumbnail", return_value=1),
            mock.patch.object(worker, "download_media") as mp4,
        ):
            result = worker.fetch_media(self.conn, self.media, concurrency=1, delay=0)
        mp4.assert_not_called()
        self.assertEqual(result.slides, 6)
        self.assertEqual(len(self.slides()), 3)
        self.assertEqual(worker.pending_media(self.conn), [])


class TestFailure(EnrichCase):
    def test_a_deleted_post_fails_permanently_and_is_not_retried(self):
        with mock.patch.object(
            worker, "probe", side_effect=FetchError("unavailable", permanent=True)
        ):
            first = self.run_enrich()
            second = self.run_enrich()

        self.assertEqual(first.failed, 2)
        self.assertEqual(second.total, 0, "a permanent failure should not come back")
        r = self.row()
        self.assertEqual(r["state"], "failed")
        self.assertEqual(r["fail_stage"], "fetch")
        self.assertEqual(r["fail_reason"], "unavailable")
        self.assertEqual(r["attempts"], 1)

    def test_a_transient_failure_is_retried_up_to_the_limit(self):
        with mock.patch.object(worker, "probe", side_effect=FetchError("timeout")):
            for _ in range(worker.MAX_ATTEMPTS + 2):
                self.run_enrich()
        self.assertEqual(self.row()["attempts"], worker.MAX_ATTEMPTS)

    def test_retry_failed_picks_permanent_failures_back_up(self):
        with mock.patch.object(
            worker, "probe", side_effect=FetchError("unavailable", permanent=True)
        ):
            self.run_enrich()
        with (
            mock.patch.object(worker, "probe", return_value=from_info(INFO)),
            mock.patch.object(worker, "download_thumbnail", return_value=1),
        ):
            again = self.run_enrich(retry_failed=True)
        self.assertEqual(again.fetched, 2)
        self.assertEqual(self.row()["state"], "fetched")
        self.assertIsNone(self.row()["fail_reason"])

    def test_an_unexpected_exception_is_recorded_not_raised(self):
        with mock.patch.object(worker, "probe", side_effect=RuntimeError("boom")):
            result = self.run_enrich()
        self.assertEqual(result.failed, 2)
        self.assertIn("RuntimeError", self.row()["fail_reason"])

    def test_a_failed_item_keeps_its_imported_data(self):
        with mock.patch.object(
            worker, "probe", side_effect=FetchError("unavailable", permanent=True)
        ):
            self.run_enrich()
        r = self.row()
        self.assertEqual(r["caption"], "from the export")
        self.assertEqual(r["author"], "bay7.garage")
        self.assertIsNotNone(r["permalink"])


class TestMediaOnly(EnrichCase):
    """--media-only exists because a fetched item is no longer behind on
    fetch_v, so the ordinary pending() query would never return it again."""

    def fetch_first(self):
        with (
            mock.patch.object(worker, "probe", return_value=from_info(INFO)),
            mock.patch.object(worker, "download_thumbnail", return_value=1),
        ):
            self.run_enrich()

    def test_already_fetched_items_are_invisible_to_the_normal_queue(self):
        self.fetch_first()
        self.assertEqual(worker.pending(self.conn), [])
        self.assertEqual(len(worker.pending_media(self.conn)), 2)

    def test_downloads_video_without_refetching_metadata(self):
        self.fetch_first()
        with (
            mock.patch.object(worker, "download_media", return_value=4096) as dl,
            mock.patch.object(worker, "probe_duration", return_value=31),
            mock.patch.object(worker, "probe") as probe,
        ):
            result = worker.fetch_media(self.conn, self.media, concurrency=1, delay=0)

        probe.assert_not_called()
        self.assertEqual(dl.call_count, 2)
        self.assertEqual(result.media, 2)
        r = self.row()
        self.assertEqual(r["media_path"], "video/aa/AAA111.mp4")
        self.assertEqual(r["bytes"], 4096)
        self.assertEqual(r["duration"], 31)

    def test_a_second_pass_has_nothing_left(self):
        self.fetch_first()
        with (
            mock.patch.object(worker, "download_media", return_value=10),
            mock.patch.object(worker, "probe_duration", return_value=5),
        ):
            worker.fetch_media(self.conn, self.media, concurrency=1, delay=0)
            again = worker.fetch_media(self.conn, self.media, concurrency=1, delay=0)
        self.assertEqual(again.total, 0)

    def test_a_photo_post_without_video_keeps_everything_else(self):
        self.fetch_first()
        with mock.patch.object(
            worker, "download_media", side_effect=FetchError("no video formats")
        ):
            result = worker.fetch_media(self.conn, self.media, concurrency=1, delay=0)

        self.assertEqual(result.skipped, 2)
        self.assertEqual(result.media, 0)
        r = self.row()
        self.assertIsNone(r["media_path"])
        self.assertEqual(r["state"], "fetched")  # not demoted to failed
        self.assertEqual(r["thumb_path"], "thumbs/aa/AAA111.jpg")


class TestResume(EnrichCase):
    def test_interrupting_halfway_loses_nothing(self):
        calls = {"n": 0}

        def flaky(url, **kw):
            calls["n"] += 1
            if calls["n"] > 1:
                raise KeyboardInterrupt
            return from_info(INFO)

        with (
            mock.patch.object(worker, "probe", side_effect=flaky),
            mock.patch.object(worker, "download_thumbnail", return_value=1),
        ):
            with self.assertRaises(KeyboardInterrupt):
                self.run_enrich()

        done = self.conn.execute("SELECT COUNT(*) FROM item WHERE state = 'fetched'").fetchone()[0]
        self.assertEqual(done, 1, "the completed item should be committed")
        self.assertEqual(len(worker.pending(self.conn)), 1, "the other should requeue")


if __name__ == "__main__":
    unittest.main()
