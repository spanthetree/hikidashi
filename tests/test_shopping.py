"""The shopping list (#48): amounts, aisles, and several recipes merged into
one list."""

import tempfile
import unittest
from pathlib import Path

from app.db import open_db
from app.library import remove
from app.recipes import service, shopping
from app.recipes.aisles import aisle_of, is_staple
from app.recipes.amounts import amount, number, written
from app.recipes.llm import Ingredient, Recipe
from tests.fixtures import must


class TestAmounts(unittest.TestCase):
    def test_numbers(self):
        for text, want in (
            ("2", 2),
            ("1.5", 1.5),
            ("1/2", 0.5),
            ("1 1/2", 1.5),
            ("1½", 1.5),
            ("½", 0.5),
            ("a half", 0.5),
            ("half a", 0.5),
            ("one and a half", 1.5),
            ("two", 2),
            ("a quarter", 0.25),
        ):
            with self.subTest(text=text):
                self.assertAlmostEqual(must(number(text)), want)
        self.assertIsNone(number("some"))

    def test_quantities_as_the_data_has_them(self):
        for quantity, unit, low, high, got_unit, text in (
            ("2 tsp", None, 2, None, "tsp", "2"),
            ("3 Tbsp", None, 3, None, "tbsp", "3"),
            ("1T", None, 1, None, "tbsp", "1"),
            ("1t", None, 1, None, "tsp", "1"),
            ("2C", None, 2, None, "cup", "2"),
            ("8g", None, 8, None, "g", "8"),
            ("100ml (1/2 cup)", None, 100, None, "ml", "100"),
            ("1 cup milk", None, 1, None, "cup", "1"),
            ("1/2 tsp", "teaspoon", 0.5, None, "tsp", "1/2"),
            ("2", "tbsp", 2, None, "tbsp", "2"),
            ("1/2 - 1 cup flour", None, 0.5, 1, "cup", "1/2 - 1"),
            ("2-3", None, 2, 3, None, "2-3"),
            ("a half cup", None, 0.5, None, "cup", "a half"),
            ("two teaspoons", None, 2, None, "tsp", "two"),
            ("a pinch", None, 1, None, "pinch", "a"),
            ("大さじ1", None, 1, None, "tbsp", "1"),
            ("1", "each", 1, None, None, "1"),
        ):
            with self.subTest(quantity=quantity):
                a = must(amount(quantity, unit))
                self.assertEqual((a.low, a.high, a.unit, a.text), (low, high, got_unit, text))

    def test_what_has_no_number(self):
        for quantity in (
            "to taste",
            "a splash",
            "a little bit",
            "Juice of 1",
            "apple",
            "an onion",
            "",
            None,
        ):
            with self.subTest(quantity=quantity):
                self.assertIsNone(amount(quantity))

    def test_written(self):
        self.assertEqual(written(must(amount("1 1/2 cups"))), "1½ cups")
        self.assertEqual(written(must(amount("1/3 cup"))), "⅓ cup")
        self.assertEqual(written(must(amount("1200 g"))), "1.2 kg")
        self.assertEqual(written(must(amount("2-3"))), "2-3")
        self.assertEqual(written(must(amount("24 oz"))), "1½ lb")
        self.assertEqual(written(must(amount("2 cloves"))), "2 cloves")


class TestAisles(unittest.TestCase):
    def test_the_noun_decides(self):
        for item, aisle in (
            ("chicken broth", "pantry"),
            ("garlic powder", "spices"),
            ("garlic cloves", "produce"),
            ("garlic", "produce"),
            ("olive oil", "condiments"),
            ("ground beef", "meat"),
            ("boneless chicken thighs", "meat"),
            ("cream cheese", "dairy"),
            ("peanut butter", "condiments"),
            ("coconut milk", "pantry"),
            ("red pepper flakes", "spices"),
            ("red bell peppers", "produce"),
            ("canned tomatoes", "pantry"),
            ("roma tomatoes", "produce"),
            ("eggs", "dairy"),
            ("flour tortillas", "bakery"),
            ("all-purpose flour", "pantry"),
            ("red wine", "drinks"),
            ("lemon juice", "produce"),
            ("frozen peas", "frozen"),
            ("zippies out", "other"),
        ):
            with self.subTest(item=item):
                self.assertEqual(aisle_of(item), aisle)

    def test_staples(self):
        for item in (
            "salt",
            "kosher salt",
            "black pepper",
            "freshly cracked pepper",
            "water",
            "olive oil",
            "vegetable oil",
            "Salt & pepper",
        ):
            with self.subTest(item=item):
                self.assertTrue(is_staple(item))
        for item in ("sesame oil", "garlic salt", "bell pepper", "coconut water"):
            with self.subTest(item=item):
                self.assertFalse(is_staple(item))


def ings(*rows):
    return [Ingredient(item=i, quantity=q, unit=u) for i, q, u in rows]


class TestShoppingList(unittest.TestCase):
    """Two recipes in user 1's library, and a third reel that is not."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = conn = open_db(Path(self.tmp.name) / "t.sqlite3")
        conn.execute("INSERT INTO user (id, username, created_at) VALUES (2, 'bob', 0)")
        for code in ("PASTA1", "SOUP01", "BOBS01"):
            conn.execute(
                "INSERT INTO item (shortcode, permalink) VALUES (?, ?)",
                (code, f"https://www.instagram.com/reel/{code}/"),
            )
        conn.executemany(
            "INSERT INTO user_item (user_id, shortcode) VALUES (?, ?)",
            [(1, "PASTA1"), (1, "SOUP01"), (2, "BOBS01"), (2, "PASTA1")],
        )
        service.save_user(
            self.conn,
            "PASTA1",
            Recipe(
                "Pasta",
                2,
                ingredients=ings(
                    ("onion", "1", None),
                    ("garlic cloves", "2", None),
                    ("butter", "50g", None),
                    ("salt", "to taste", None),
                    ("parmesan", None, None),
                ),
            ),
        )
        service.save_user(
            self.conn,
            "SOUP01",
            Recipe(
                "Soup",
                4,
                ingredients=ings(
                    ("onions", "2", None),
                    ("garlic clove", "1 large", None),
                    ("butter", "0.1 kg", None),
                    ("butter", "2 tbsp", None),
                    ("chicken broth", "1 l", None),
                ),
            ),
        )
        service.save_user(
            self.conn,
            "BOBS01",
            Recipe("Bob's", ingredients=ings(("caviar", "1 tin", None))),
            user_id=2,
        )

    def lines(self, user_id=1):
        return {ln["key"]: ln for ln in shopping.view(self.conn, user_id=user_id)["lines"]}

    def test_merged_by_name_and_summed_when_units_agree(self):
        self.assertEqual(shopping.add(self.conn, ["PASTA1", "SOUP01"]), 2)
        lines = self.lines()
        self.assertEqual(lines["onion"]["amounts"], ["3"])
        self.assertEqual(lines["onion"]["recipes"], ["PASTA1", "SOUP01"])
        self.assertEqual(lines["garlic clove"]["amounts"], ["2", "1 large"])
        self.assertEqual(lines["butter"]["amounts"], ["150 g", "2 tbsp"])
        self.assertEqual(lines["salt"]["amounts"], ["to taste"])
        self.assertTrue(lines["salt"]["staple"])
        self.assertEqual(lines["parmesan"]["amounts"], [])
        self.assertEqual(
            (lines["chicken broth"]["aisle"], lines["butter"]["aisle"]), ("pantry", "dairy")
        )
        # In aisle order: produce first, the pantry after the dairy.
        keys = list(lines)
        self.assertLess(keys.index("onion"), keys.index("butter"))
        self.assertLess(keys.index("butter"), keys.index("chicken broth"))

    def test_only_recipes_you_can_see(self):
        self.assertEqual(shopping.add(self.conn, ["BOBS01", "NOSUCH", "PASTA1", "PASTA1"]), 1)
        self.assertEqual(shopping.add(self.conn, ["PASTA1"]), 0)
        self.assertNotIn("caviar", self.lines())
        self.assertEqual(shopping.view(self.conn, user_id=2)["recipes"], [])

    def test_ticks_are_kept_and_come_off_when_more_is_needed(self):
        shopping.add(self.conn, ["PASTA1"])
        shopping.tick(self.conn, "onion", True)
        shopping.tick(self.conn, "parmesan", True)
        self.assertTrue(self.lines()["onion"]["ticked"])
        shopping.add(self.conn, ["SOUP01"])
        self.assertFalse(self.lines()["onion"]["ticked"])
        self.assertTrue(self.lines()["parmesan"]["ticked"])
        shopping.tick(self.conn, "parmesan", False)
        self.assertFalse(self.lines()["parmesan"]["ticked"])

    def test_dropping_a_recipe_drops_its_lines_and_their_ticks(self):
        shopping.add(self.conn, ["PASTA1", "SOUP01"])
        shopping.tick(self.conn, "parmesan", True)
        shopping.tick(self.conn, "onion", True)
        shopping.drop(self.conn, "PASTA1")
        lines = self.lines()
        self.assertNotIn("parmesan", lines)
        self.assertEqual(lines["onion"]["amounts"], ["2"])
        self.assertTrue(lines["onion"]["ticked"])
        shopping.add(self.conn, ["PASTA1"])
        self.assertFalse(self.lines()["parmesan"]["ticked"])

    def test_a_corrected_or_hidden_recipe_changes_the_list(self):
        shopping.add(self.conn, ["PASTA1"])
        service.save_user(
            self.conn, "PASTA1", Recipe("Pasta", ingredients=ings(("onion", "4", None)))
        )
        self.assertEqual(self.lines()["onion"]["amounts"], ["4"])
        service.save_user(self.conn, "PASTA1", Recipe("Pasta"), hidden=True)
        self.assertEqual(shopping.view(self.conn)["lines"], [])

    def test_out_of_your_library_is_off_your_list(self):
        shopping.add(self.conn, ["PASTA1", "SOUP01"])
        service.save_user(self.conn, "PASTA1", Recipe("Bob's pasta"), user_id=2)
        shopping.add(self.conn, ["PASTA1"], user_id=2)
        self.conn.execute("BEGIN")
        remove(self.conn, 1, "SOUP01")
        self.conn.execute("COMMIT")
        self.assertEqual(shopping.listed(self.conn), {"PASTA1"})
        self.conn.execute("BEGIN")
        remove(self.conn, 1, "PASTA1")
        self.conn.execute("COMMIT")
        self.assertEqual(shopping.listed(self.conn), set())
        self.assertEqual(shopping.listed(self.conn, user_id=2), {"PASTA1"})

    def test_clear(self):
        shopping.add(self.conn, ["PASTA1"])
        shopping.tick(self.conn, "onion", True)
        shopping.clear(self.conn)
        self.assertEqual(shopping.view(self.conn), {"recipes": [], "lines": []})
        self.assertIsNone(self.conn.execute("SELECT 1 FROM shopping_tick").fetchone())


if __name__ == "__main__":
    unittest.main()
