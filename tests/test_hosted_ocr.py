import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.chat import Chat
from app.ocr import hosted
from app.ocr.reader import OcrError
from app.providers import limits
from app.providers.client import Client, RequestFailed
from app.providers.keys import Key
from app.providers.presets import PRESETS
from tests.fake_openai import KEY, Fake


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg not installed")
class TestHostedOcr(unittest.TestCase):
    def setUp(self):
        self.fake = Fake().start()
        self.addCleanup(self.fake.stop)
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        run = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi"]
        subprocess.run(
            [*run, "-i", "color=c=white:s=1280x720:d=4", str(self.dir / "v.mp4")], check=True
        )
        subprocess.run(
            [*run, "-i", "color=c=red:s=1080x1080", "-frames:v", "1", str(self.dir / "t.jpg")],
            check=True,
        )
        limiter = limits.Limiter(in_flight=2, per_minute=1000)
        client = Client(PRESETS["openai"], Key(KEY), base_url=self.fake.url, limiter=limiter)
        self.ocr = hosted.Hosted(Chat(client, "gpt-4.1-mini"))

    def test_the_cover_and_five_frames_in_one_request(self):
        self.fake.chat_reply = lambda req: {"lines": ["Pizza night", "pizza  night", "200g flour"]}
        row = {"media_path": "v.mp4", "thumb_path": "t.jpg", "duration": 4}
        self.assertEqual(self.ocr(row, self.dir), "Pizza night\n200g flour")
        sent = self.fake.seen("/chat/completions")
        self.assertEqual(len(sent), 1)
        parts = sent[0].json()["messages"][1]["content"]
        images = [p for p in parts if p["type"] == "image_url"]
        self.assertEqual(len(images), 6)
        self.assertTrue(all(p["image_url"]["detail"] == "low" for p in images))
        self.assertEqual(self.ocr.label, "openai:gpt-4.1-mini")

    def test_frames_are_scaled_down(self):
        img = hosted.frame(self.dir / "t.jpg")
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=width", "-of", "csv=p=0", "-"],
            input=img, capture_output=True, check=True,
        )  # fmt: skip
        self.assertEqual(int(out.stdout), hosted.WIDTH)

    def test_a_photo_and_no_text(self):
        self.fake.chat_reply = lambda req: {"lines": []}
        self.assertIsNone(
            self.ocr({"media_path": None, "thumb_path": "t.jpg", "duration": None}, self.dir)
        )

    def test_no_frames(self):
        with self.assertRaises(OcrError):
            self.ocr({"media_path": None, "thumb_path": "gone.jpg", "duration": None}, self.dir)

    def test_a_bad_reply_is_this_request(self):
        self.fake.chat_reply = lambda req: {"text": "Pizza"}
        with self.assertRaises(RequestFailed):
            self.ocr({"media_path": None, "thumb_path": "t.jpg", "duration": None}, self.dir)


if __name__ == "__main__":
    unittest.main()
