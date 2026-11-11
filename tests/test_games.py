"""Naming the video game a reel shows (#117): the question, and the rule that
decides whether the answer is applied, offered, or dropped."""

import json
import unittest

from app.tagging import games
from app.tagging.games import Answer, GameError, decide, grounded, parse, parse_check


def said(kind="one", title="", confidence="high", others=()) -> str:
    return json.dumps(
        {"kind": kind, "title": title, "confidence": confidence, "others": list(others)}
    )


class Fake:
    """A games.Model: a reply for the reel, and a table of titles it knows."""

    def __init__(self, reply: str, known: dict[str, str] | None = None):
        self.reply = reply
        self.known = known or {}
        self.checked: list[str] = []

    def game(self, prompt: str) -> str:
        self.prompt = prompt
        return self.reply

    def check(self, title: str) -> str:
        self.checked.append(title)
        official = self.known.get(title)
        return json.dumps({"known": official is not None, "title": official or ""})


def ask(fake: Fake, caption=None, hashtags=(), transcript=None, ocr=None) -> games.Decision:
    a = games.ask(fake, caption, list(hashtags), transcript, ocr)
    return decide(a, caption, list(hashtags), transcript, ocr)


class TestCandidates(unittest.TestCase):
    def test_a_gaming_or_sim_racing_reel_is_always_asked_about(self):
        self.assertTrue(games.is_candidate("Gaming", "", [], None, None))
        self.assertTrue(games.is_candidate("Sim racing", None, [], None, None))

    def test_a_reel_filed_elsewhere_is_asked_about_when_its_text_reads_like_games(self):
        for text in (
            "my Minecraft Disneyland server has audio",
            "best gameplay of the week",
            "PS5 vs Xbox",
            "speedrunning this level",
        ):
            with self.subTest(text):
                self.assertTrue(games.is_candidate("Technology", None, [], text, None))
        self.assertTrue(games.is_candidate(None, "new setup", ["pcgaming"], None, None))
        self.assertTrue(games.is_candidate(None, None, [], None, "NINTENDO DIRECT"))

    def test_football_board_games_and_car_mods_are_not_cues(self):
        for text in ("game day at the stadium", "board game night", "exhaust mod on my GTI"):
            with self.subTest(text):
                self.assertFalse(games.is_candidate("Automotive", text, [], None, None))


class TestParse(unittest.TestCase):
    def test_one_game(self):
        a = parse(said(title="  Assetto   Corsa ", others=["iRacing"]))
        self.assertEqual(a, Answer("one", "Assetto Corsa", "high", ("iRacing",)))

    def test_several_or_none_keep_no_title(self):
        self.assertEqual(parse(said("several", "Doom")), Answer("several", None, "high"))
        self.assertEqual(parse(said("none", "")), Answer("none", None, "high"))

    def test_one_without_a_title_is_none(self):
        self.assertEqual(parse(said("one", " ")).kind, "none")

    def test_the_game_itself_is_not_one_of_the_others(self):
        self.assertEqual(
            parse(said(title="Starfield", others=["starfield", "Skyrim"])).others, ("Skyrim",)
        )

    def test_case_is_forgiven(self):
        self.assertEqual(parse(said("ONE", "Doom", "High")).confidence, "high")

    def test_malformed_replies_fail_the_reel(self):
        for reply in (
            "not json",
            "[]",
            json.dumps({"kind": "one"}),
            said(kind="maybe"),
            said(confidence="certain"),
            json.dumps({"kind": "one", "title": "x", "confidence": "high", "others": "y"}),
        ):
            with self.subTest(reply), self.assertRaises(GameError):
                parse(reply)

    def test_a_long_title_is_cut(self):
        self.assertEqual(len(parse(said(title="x" * 500)).title or ""), games.MAX_TITLE)

    def test_the_check(self):
        self.assertEqual(
            parse_check('{"known": true, "title": "Gran Turismo 4"}'), "Gran Turismo 4"
        )
        self.assertIsNone(parse_check('{"known": false, "title": "Exodos"}'))
        self.assertIsNone(parse_check('{"known": "yes", "title": "Doom"}'))


class TestGrounded(unittest.TestCase):
    def test_a_title_of_several_words_anywhere_in_the_text(self):
        self.assertTrue(grounded("World of Tanks", None, [], "playing world of tanks", None))
        self.assertTrue(grounded("Gran Turismo 4", "GRAN TURISMO® 4 on a CRT", [], None, None))
        self.assertFalse(grounded("Gran Turismo 4", "Gran Turismo 7 looks great", [], None, None))

    def test_the_main_part_of_a_title_names_it(self):
        title = "The Legend of Zelda: Ocarina of Time"
        self.assertTrue(grounded(title, None, [], "a PC port of ocarina of time", None))
        self.assertTrue(grounded(title, "legend of zelda, on PC", [], None, None))
        self.assertTrue(grounded("Deadstick - Bush Flight Simulator", "Deadstick!", [], None, None))

    def test_a_hashtag_or_the_start_of_one(self):
        self.assertTrue(grounded("Assetto Corsa", None, ["assettocorsa"], None, None))
        self.assertTrue(grounded("Minecraft", None, ["minecraftbuilds"], None, None))
        # Too short to trust as the start of a longer tag.
        self.assertFalse(grounded("Doom", None, ["doomscrolling"], None, None))
        self.assertTrue(grounded("Doom", None, ["doom"], None, None))

    def test_one_word_must_be_written_as_a_name(self):
        self.assertTrue(grounded("Control", "Control has the best lighting", [], None, None))
        self.assertTrue(grounded("Replaced", "REPLACED is a 2.5D platformer", [], None, None))
        self.assertFalse(grounded("Control", "take control of your budget", [], None, None))
        self.assertFalse(grounded("Minecraft", None, [], None, None))

    def test_a_word_inside_another_does_not_count(self):
        self.assertFalse(grounded("Starfield", "Starfields of Kansas", [], None, None))


MINECRAFT = {"Minecraft": "Minecraft"}


class TestDecide(unittest.TestCase):
    def test_sure_when_every_part_holds(self):
        d = ask(Fake(said(title="Minecraft"), MINECRAFT), "a Minecraft server tour")
        self.assertEqual((d.title, d.sure, d.guess), ("Minecraft", True, None))

    def test_the_official_title_is_kept_and_the_reels_spelling_still_counts(self):
        fake = Fake(said(title="Grand Turismo 4"), {"Grand Turismo 4": "Gran Turismo 4"})
        d = ask(fake, "Grand Turismo 4 on a CRT, in 4K")
        self.assertEqual((d.title, d.sure), ("Gran Turismo 4", True))

    def test_a_guess_when_the_text_does_not_name_it(self):
        fake = Fake(said(title="Cyberpunk 2077"), {"Cyberpunk 2077": "Cyberpunk 2077"})
        d = ask(fake, "What little details did you spot?", ["cyberpunk", "gaming"])
        self.assertEqual((d.title, d.sure, d.guess), ("Cyberpunk 2077", False, "Cyberpunk 2077"))

    def test_a_guess_when_the_check_cannot_place_it(self):
        d = ask(Fake(said(title="StarPath")), "Starpath: a co-op space sim")
        self.assertEqual((d.guess, d.sure), ("StarPath", False))

    def test_a_guess_at_medium_confidence(self):
        d = ask(Fake(said(title="Minecraft", confidence="medium"), MINECRAFT), "Minecraft")
        self.assertEqual((d.guess, d.sure), ("Minecraft", False))

    def test_a_guess_when_the_text_names_another_game_too(self):
        fake = Fake(
            said(title="Assetto Corsa", others=["iRacing"]),
            {"Assetto Corsa": "Assetto Corsa", "iRacing": "iRacing"},
        )
        d = ask(fake, "work to race mode", ["simracing", "iracing", "assettocorsa"])
        self.assertEqual((d.guess, d.sure), ("Assetto Corsa", False))

    def test_others_the_text_never_names_or_the_check_cannot_place_do_not_count(self):
        fake = Fake(
            said(title="Assetto Corsa", others=["Cyberpunk 2077", "Nissan", "assettocorsa"]),
            {"Assetto Corsa": "Assetto Corsa", "Cyberpunk 2077": "Cyberpunk 2077"},
        )
        d = ask(fake, "my Nissan drift settings", ["assettocorsa"])
        self.assertTrue(d.sure)
        # The same game spelt as a hashtag is not checked as another.
        self.assertNotIn("assettocorsa", fake.checked)

    def test_no_game_for_several_none_or_a_low_guess(self):
        for reply in (said("several", ""), said("none", ""), said(title="Doom", confidence="low")):
            with self.subTest(reply):
                self.assertEqual(ask(Fake(reply, {"Doom": "Doom"}), "#doom"), games.NO_GAME)

    def test_a_low_guess_is_not_checked(self):
        fake = Fake(said(title="Doom", confidence="low"))
        ask(fake, "doom")
        self.assertEqual(fake.checked, [])

    def test_the_prompt_carries_every_text(self):
        fake = Fake(said("none"))
        games.ask(fake, "cap", ["tag"], "said", "shown")
        for part in ("Caption:\ncap", "#tag", "Transcript:\nsaid", "shown"):
            self.assertIn(part, fake.prompt)


class TestClient(unittest.TestCase):
    def test_the_tagger_client_asks_about_games_too(self):
        from app.tagging import llm

        self.assertIsInstance(llm.Ollama("http://x", "m"), games.Model)


if __name__ == "__main__":
    unittest.main()
