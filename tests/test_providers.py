import io
import json
import logging
import tempfile
import unittest
from pathlib import Path
from typing import Any

from app.providers import Check, limits, test
from app.providers import keys as keys_mod
from app.providers.client import (
    Client,
    Outage,
    RequestFailed,
    Unusable,
    Wait,
    multipart,
)
from app.providers.keys import Key, from_env, mask
from app.providers.presets import COMPATIBLE, PRESETS, STAGES, get
from app.providers.schema import strict, validate
from app.recipes import llm as recipe_llm
from app.tagging import games
from app.tagging import llm as tag_llm
from tests.fake_openai import KEY, Fake, completion

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "level": {"type": "string", "enum": ["high", "low"]},
    },
    "required": ["summary"],
}


class Clock:
    def __init__(self):
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.now += s


def client(fake: Fake, preset: str = "openai", **kw) -> Client:
    clock = Clock()
    limiter = limits.Limiter(in_flight=4, per_minute=1000, clock=clock, sleep=clock.sleep)
    return Client(
        PRESETS[preset], Key(KEY), base_url=fake.url, limiter=limiter, sleep=clock.sleep, **kw
    )


class TestKeys(unittest.TestCase):
    def test_a_key_prints_masked_everywhere(self):
        k = Key("sk-proj-abcdefghijklmnop1234")
        for shown in (
            repr(k),
            str(k),
            f"{k}",
            f"{k!r}",
            json.dumps(str(k), ensure_ascii=False),
        ):
            self.assertNotIn("abcdefghijklmnop", shown)
            self.assertIn("…1234", shown)
        self.assertEqual(k.reveal(), "sk-proj-abcdefghijklmnop1234")

    def test_mask(self):
        self.assertEqual(mask("sk-abcdefghijklmnopabcd"), "sk-…abcd")
        self.assertEqual(mask("gsk_abcdefghijklmnopwxyz"), "gsk_…wxyz")
        self.assertEqual(mask("short"), "…")

    def test_scrub(self):
        k = Key("sk-abcdefghijklmnop9999")
        self.assertEqual(k.scrub("bad key sk-abcdefghijklmnop9999!"), "bad key sk-…9999!")

    def test_a_key_is_not_pickled(self):
        import pickle

        with self.assertRaises(TypeError):
            pickle.dumps(Key("sk-abcdefghijklmnop9999"))

    def test_from_env_and_file(self):
        self.assertIsNone(from_env("X_KEY", {}))
        self.assertEqual(from_env("X_KEY", {"X_KEY": " sk-1 "}), Key("sk-1"))
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "secret"
            f.write_text("gsk_fromfile\n")
            self.assertEqual(from_env("X_KEY", {"X_KEY_FILE": str(f)}), Key("gsk_fromfile"))
            with self.assertRaises(ValueError):
                from_env("X_KEY", {"X_KEY_FILE": str(Path(d) / "missing")})

    def test_the_key_is_never_in_logs(self):
        k = Key("sk-abcdefghijklmnopqrstu")
        out = io.StringIO()
        handler = logging.StreamHandler(out)
        log = logging.getLogger("test.keys")
        log.addHandler(handler)
        try:
            log.warning("using %s and %r", k, k)
        finally:
            log.removeHandler(handler)
        self.assertNotIn(k.reveal(), out.getvalue())
        self.assertNotIn(k.reveal(), keys_mod.__doc__ or "")


class TestPresets(unittest.TestCase):
    def test_each_preset_has_a_default_model_for_what_it_can_do(self):
        for p in PRESETS.values():
            self.assertTrue(p.checked, p.name)
            if p.name == COMPATIBLE:
                continue
            for stage in STAGES:
                if p.does(stage):
                    self.assertTrue(p.model(stage), (p.name, stage))

    def test_stage_needs(self):
        self.assertTrue(get("openai").does("ocr"))
        self.assertEqual(get("groq").model("asr"), "whisper-large-v3-turbo")
        with self.assertRaises(ValueError):
            get("nope")


class TestSchema(unittest.TestCase):
    def test_strict_requires_everything_and_no_extras(self):
        s = strict(SCHEMA)
        self.assertEqual(s["required"], ["summary", "keywords", "level"])
        self.assertIs(s["additionalProperties"], False)
        self.assertNotIn("maxItems", s["properties"]["keywords"])
        self.assertIn("maxItems", SCHEMA["properties"]["keywords"])  # a copy

    def test_the_stages_schemas_all_go_strict(self):
        for sc in (
            tag_llm.schema(),
            games.schema(),
            games.check_schema(),
            recipe_llm.check_schema(),
            recipe_llm.extract_schema(),
        ):
            s = strict(sc)

            def walk(node):
                if node.get("type") == "object":
                    self.assertEqual(set(node["required"]), set(node["properties"]))
                    self.assertFalse(node["additionalProperties"])
                    for v in node["properties"].values():
                        walk(v)
                if node.get("type") == "array":
                    walk(node["items"])

            walk(s)

    def test_validate(self):
        self.assertEqual(validate({"summary": "x", "keywords": ["a"] * 9}, SCHEMA), [])
        self.assertTrue(validate({"keywords": []}, SCHEMA))
        self.assertTrue(validate({"summary": 3}, SCHEMA))
        self.assertTrue(validate({"summary": "x", "level": "mid"}, SCHEMA))
        self.assertTrue(validate({"summary": "x", "keywords": [1]}, SCHEMA))
        self.assertTrue(validate([], SCHEMA))
        self.assertTrue(validate(True, {"type": "integer"}))
        self.assertEqual(validate(3, {"type": "integer"}), [])


class TestLimits(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(limits.seconds("20"), 20)
        self.assertEqual(limits.seconds("6m0s"), 360)
        self.assertAlmostEqual(limits.seconds("2m59.56s") or 0, 179.56)
        self.assertAlmostEqual(limits.seconds("450ms") or 0, 0.45)
        self.assertEqual(limits.seconds("1h2m"), 3720)
        self.assertIsNone(limits.seconds("soon"))
        self.assertIsNone(limits.seconds(""))

    def test_told(self):
        self.assertEqual(limits.told({"Retry-After": "7"}), 7)
        self.assertEqual(
            limits.told(
                {"x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "2s"}
            ),
            2,
        )
        self.assertIsNone(limits.told({"x-ratelimit-remaining-requests": "5"}))

    def test_per_minute(self):
        c = Clock()
        lim = limits.Limiter(in_flight=1, per_minute=2, clock=c, sleep=c.sleep)
        for _ in range(3):
            with lim.slot():
                pass
        self.assertGreaterEqual(c.now - 1000.0, 60.0)

    def test_hold(self):
        c = Clock()
        lim = limits.Limiter(in_flight=1, per_minute=100, clock=c, sleep=c.sleep)
        lim.hold(30)
        self.assertEqual(lim.held_for(), 30)
        with lim.slot():
            pass
        self.assertGreaterEqual(c.now, 1030.0)


class FakeCase(unittest.TestCase):
    def setUp(self):
        self.fake = Fake().start()
        self.addCleanup(self.fake.stop)


class TestChat(FakeCase):
    def test_strict_json_schema_is_asked_for_and_checked(self):
        self.fake.chat_reply = lambda req: {"summary": "pizza", "keywords": [], "level": "high"}
        r = client(self.fake).chat("gpt-4.1-mini", "sys", "hi", max_tokens=50, schema=SCHEMA)
        self.assertEqual(json.loads(r.text)["summary"], "pizza")
        sent = self.fake.seen("/chat/completions")[0].json()
        self.assertEqual(sent["response_format"]["type"], "json_schema")
        self.assertTrue(sent["response_format"]["json_schema"]["strict"])
        self.assertEqual(sent["max_completion_tokens"], 50)
        self.assertEqual(sent["temperature"], 0)
        self.assertEqual(r.usage.input_tokens, 120)
        self.assertEqual(r.usage.cached_tokens, 64)

    def test_json_object_puts_the_schema_in_the_prompt(self):
        self.fake.chat_reply = lambda req: '```json\n{"summary": "x"}\n```'
        r = client(self.fake, COMPATIBLE).chat("m", "sys", "hi", max_tokens=5, schema=SCHEMA)
        self.assertEqual(json.loads(r.text), {"summary": "x"})
        sent = self.fake.seen("/chat/completions")[0].json()
        self.assertEqual(sent["response_format"], {"type": "json_object"})
        self.assertIn('"summary"', sent["messages"][0]["content"])
        self.assertTrue(sent["messages"][0]["content"].startswith("sys"))
        self.assertIn("max_tokens", sent)

    def test_a_reply_that_does_not_fit_is_this_request_failing(self):
        self.fake.chat_reply = lambda req: {"keywords": "pizza"}
        with self.assertRaises(RequestFailed) as cm:
            client(self.fake).chat("m", "s", "u", max_tokens=5, schema=SCHEMA)
        self.assertIn("schema", str(cm.exception))
        self.fake.chat_reply = lambda req: "not json"
        with self.assertRaises(RequestFailed):
            client(self.fake).chat("m", "s", "u", max_tokens=5, schema=SCHEMA)

    def test_a_refusal_and_a_cut_reply(self):
        self.fake.then("/chat/completions", 200, completion("", refusal="I can't help"))
        with self.assertRaises(RequestFailed) as cm:
            client(self.fake).chat("m", "s", "u", max_tokens=5, schema=SCHEMA)
        self.assertIn("refused", str(cm.exception))
        self.fake.then("/chat/completions", 200, completion('{"summ', finish="length"))
        with self.assertRaises(RequestFailed) as cm:
            client(self.fake).chat("m", "s", "u", max_tokens=5, schema=SCHEMA)
        self.assertIn("cap", str(cm.exception))

    def test_a_bad_key_is_unusable_and_never_echoed(self):
        k = Key("sk-wrongwrongwrongwrong1234")
        self.fake.then(
            "/chat/completions",
            401,
            {"error": {"message": f"Incorrect API key provided: {k.reveal()}"}},
        )
        c = client(self.fake)
        c.key = k
        with self.assertRaises(Unusable) as cm:
            c.chat("m", "s", "u", max_tokens=5)
        self.assertEqual(cm.exception.why, "key")
        self.assertNotIn(k.reveal(), str(cm.exception))
        self.assertNotIn(k.reveal(), repr(c))

    def test_insufficient_quota_is_unusable(self):
        self.fake.then(
            "/chat/completions",
            429,
            {"error": {"message": "You exceeded your current quota", "code": "insufficient_quota"}},
        )
        with self.assertRaises(Unusable) as cm:
            client(self.fake).chat("m", "s", "u", max_tokens=5)
        self.assertEqual(cm.exception.why, "quota")

    def test_a_missing_model(self):
        self.fake.then(
            "/chat/completions",
            404,
            {"error": {"message": "The model `x` does not exist", "code": "model_not_found"}},
        )
        with self.assertRaises(Unusable) as cm:
            client(self.fake).chat("x", "s", "u", max_tokens=5)
        self.assertEqual(cm.exception.why, "model")

    def test_a_short_429_is_waited_out(self):
        self.fake.chat_reply = lambda req: "ok"
        self.fake.then(
            "/chat/completions", 429, {"error": {"message": "slow down"}}, {"Retry-After": "3"}
        )
        c = client(self.fake)
        self.assertEqual(c.chat("m", "s", "u", max_tokens=5).text, "ok")
        self.assertGreaterEqual(sum(c.sleep.__self__.slept), 3)  # type: ignore[attr-defined]

    def test_a_long_429_and_a_daily_cap_are_wait(self):
        self.fake.then(
            "/chat/completions", 429, {"error": {"message": "slow"}}, {"Retry-After": "120"}
        )
        with self.assertRaises(Wait) as cm:
            client(self.fake).chat("m", "s", "u", max_tokens=5)
        self.assertEqual(cm.exception.seconds, 120)
        self.assertFalse(cm.exception.daily)
        self.fake.then(
            "/audio/transcriptions",
            429,
            {"error": {"message": "Rate limit reached on audio seconds per day (ASD)"}},
            {"Retry-After": "5"},
        )
        with self.assertRaises(Wait) as cm:
            client(self.fake, "groq").transcribe("whisper-large-v3-turbo", b"x")
        self.assertTrue(cm.exception.daily)

    def test_a_5xx_is_tried_again_then_an_outage(self):
        self.fake.chat_reply = lambda req: "ok"
        self.fake.then("/chat/completions", 503, {"error": {"message": "busy"}})
        self.assertEqual(client(self.fake).chat("m", "s", "u", max_tokens=5).text, "ok")
        for _ in range(3):
            self.fake.then("/chat/completions", 500, b"oops")
        with self.assertRaises(Outage):
            client(self.fake).chat("m", "s", "u", max_tokens=5)

    def test_unreachable_is_an_outage(self):
        c = client(self.fake)
        self.fake.stop()
        with self.assertRaises(Outage):
            c.chat("m", "s", "u", max_tokens=5)

    def test_a_400_is_this_request(self):
        self.fake.then("/chat/completions", 400, {"error": {"message": "image too large"}})
        with self.assertRaises(RequestFailed):
            client(self.fake).chat("m", "s", "u", max_tokens=5)

    def test_learns_a_model_that_rejects_temperature(self):
        self.fake.chat_reply = lambda req: "ok"
        self.fake.then(
            "/chat/completions",
            400,
            {"error": {"message": "Unsupported value: 'temperature' does not support 0"}},
        )
        c = client(self.fake)
        self.assertEqual(c.chat("m", "s", "u", max_tokens=5).text, "ok")
        self.assertEqual(c.chat("m", "s", "u", max_tokens=5).text, "ok")
        sent = [r.json() for r in self.fake.seen("/chat/completions")]
        self.assertIn("temperature", sent[0])
        self.assertNotIn("temperature", sent[1])
        self.assertNotIn("temperature", sent[2])

    def test_images(self):
        from app.providers.client import image_part

        self.fake.chat_reply = lambda req: "text"
        r = client(self.fake).chat(
            "m", "s", [{"type": "text", "text": "read"}, image_part(b"jpg")], max_tokens=5
        )
        self.assertEqual(r.usage.images, 1)
        sent = self.fake.seen("/chat/completions")[0].json()
        self.assertTrue(
            sent["messages"][1]["content"][1]["image_url"]["url"].startswith(
                "data:image/jpeg;base64,"
            )
        )


class TestTranscribe(FakeCase):
    def test_verbose_json(self):
        t = client(self.fake).transcribe("whisper-1", b"audio", segments=True, language="en")
        self.assertEqual(t.text, self.fake.transcript)
        self.assertEqual(len(t.segments or []), 2)
        self.assertEqual(t.language, "english")
        self.assertEqual(t.usage.audio_seconds, 6.0)
        form = self.fake.seen("/audio/transcriptions")[0].form()
        self.assertEqual(form["response_format"], "verbose_json")
        self.assertEqual(form["language"], "en")
        self.assertEqual(form["model"], "whisper-1")

    def test_plain_json(self):
        t = client(self.fake).transcribe("gpt-4o-mini-transcribe", b"audio")
        self.assertIsNone(t.segments)
        self.assertNotIn("language", self.fake.seen("/audio/transcriptions")[0].form())

    def test_multipart(self):
        ctype, body = multipart({"a": "1"}, {"file": ("x.flac", "audio/flac", b"\x00\x01")})
        self.assertIn("boundary=", ctype)
        self.assertIn(b'filename="x.flac"', body)
        self.assertIn(b"\x00\x01", body)


class TestModelsAndTest(FakeCase):
    def test_models(self):
        self.assertIn("whisper-1", client(self.fake).models())

    def test_test_connection(self):
        self.fake.chat_reply = lambda req: "OK"
        c = client(self.fake)
        self.assertEqual(test(c, "tag", "gpt-4.1-mini"), Check(True, "OK"))
        self.assertTrue(test(c, "asr", "whisper-1").ok)
        self.assertTrue(test(c, "ocr", "gpt-4.1-mini").ok)
        self.fake.key = "something-else-entirely"
        self.assertEqual(test(c, "tag", "gpt-4.1-mini").said, "key rejected")
        self.fake.then(
            "/chat/completions", 404, {"error": {"message": "no", "code": "model_not_found"}}
        )
        self.assertEqual(test(c, "tag", "x").said, "model not available to this key")
        self.fake.then(
            "/chat/completions", 429, {"error": {"code": "insufficient_quota", "message": "q"}}
        )
        self.assertEqual(test(c, "tag", "x").said, "quota exhausted")


if __name__ == "__main__":
    unittest.main()
