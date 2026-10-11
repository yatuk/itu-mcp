"""Cafeteria menu: checked controls, the selectable group and the service mapping (synthetic data)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from ninova_mcp.parsing import extract_cafeteria_menu
from ninova_mcp.server import NinovaMcpApp

PAGE = "https://portal.itu.edu.tr/apps/default/"
FOOD_LIST = (
    '<ul data-placement="food-list">'
    '<li class="lunch-menu__list-item">Sentetik Corba <i class="icon-warning" data-food-id="11"></i></li>'
    '<li class="lunch-menu__list-item">Sentetik Pilav</li>'
    '<li><b>Seçmeli 4. Çeşit</b><ul class="secmeli-yemek">'
    '<li class="lunch-menu__list-item">Sentetik Tatli <i class="icon-warning" data-food-id="12"></i></li>'
    '<li class="lunch-menu__list-item">Sentetik Salata</li></ul></li></ul>'
)


def menu(html: str) -> dict:
    return extract_cafeteria_menu(html, PAGE)


class CheckedControlTests(unittest.TestCase):
    def test_every_spelling_of_the_checked_attribute_counts(self) -> None:
        for attribute in ("checked", 'checked="checked"', 'checked=""', 'checked="true"', "CHECKED"):
            with self.subTest(attribute=attribute):
                result = menu(f'<input type="radio" id="radio-ogle"><input type="radio" id="radio-aksam" {attribute}>'
                              f'<input type="checkbox" id="checkbox-vejeteryan-vegan" {attribute}>')
                self.assertEqual(result["meal_type"], "Akşam Yemeği")
                self.assertTrue(result["vegetarian_selected"])
                lunch = menu(f'<input type="radio" id="radio-ogle" {attribute}><input type="radio" id="radio-aksam">')
                self.assertEqual(lunch["meal_type"], "Öğle Yemeği")

    def test_unchecked_controls(self) -> None:
        result = menu('<input type="radio" id="radio-ogle"><input type="radio" id="radio-aksam">'
                      '<input type="checkbox" id="checkbox-vejeteryan-vegan">')
        self.assertIsNone(result["meal_type"])
        self.assertTrue(result["vegetarian_available"])
        self.assertFalse(result["vegetarian_selected"])

    def test_the_title_still_wins_over_the_checked_radio(self) -> None:
        result = menu('<span data-placement="food-title">Akşam Yemeği Menüsü</span>'
                      '<input type="radio" id="radio-ogle" checked>')
        self.assertEqual(result["meal_type"], "Akşam Yemeği Menüsü")


class MealTypeTests(unittest.TestCase):
    def test_empty_title_and_no_checked_radio_is_none(self) -> None:
        for title in ("", "   ", "&nbsp;"):
            with self.subTest(title=title):
                result = menu(f'<span data-placement="food-title">{title}</span><input type="radio" id="radio-ogle">')
                self.assertIsNone(result["meal_type"])

    def test_empty_title_falls_back_to_the_checked_radio(self) -> None:
        result = menu('<span data-placement="food-title"></span><input type="radio" id="radio-aksam" checked>')
        self.assertEqual(result["meal_type"], "Akşam Yemeği")


class SelectableGroupTests(unittest.TestCase):
    def test_options_are_listed_once_inside_the_group(self) -> None:
        result = menu(FOOD_LIST)
        self.assertEqual(result["items"], [
            {"name": "Sentetik Corba", "food_id": "11", "has_allergen_info": True},
            {"name": "Sentetik Pilav", "food_id": None, "has_allergen_info": False},
            {"name": "Seçmeli 4. Çeşit", "options": ["Sentetik Tatli", "Sentetik Salata"], "is_selectable_group": True},
        ])
        self.assertEqual(result["item_count"], 3)
        names = [item["name"] for item in result["items"]]
        self.assertNotIn("Sentetik Tatli", names)
        self.assertNotIn("Sentetik Salata", names)

    def test_a_sub_list_that_is_not_a_group_keeps_its_dishes(self) -> None:
        # No title, or a list of another class: the lines stay plain dishes.
        for html in (
            '<ul data-placement="food-list"><li class="lunch-menu__list-item">A</li>'
            '<ul class="secmeli-yemek"><li class="lunch-menu__list-item">B</li></ul></ul>',
            '<ul data-placement="food-list"><li class="lunch-menu__list-item">A</li>'
            '<b>Seçmeli</b><ul class="baska"><li class="lunch-menu__list-item">B</li></ul></ul>',
        ):
            with self.subTest(html=html):
                result = menu(html)
                self.assertEqual([item["name"] for item in result["items"]], ["A", "B"])
                self.assertEqual(result["item_count"], 2)

    def test_a_dish_after_the_group_is_kept(self) -> None:
        html = FOOD_LIST[:-len("</ul>")] + '<li class="lunch-menu__list-item">Sentetik Meyve</li></ul>'
        names = [item["name"] for item in menu(html)["items"]]
        self.assertEqual(names, ["Sentetik Corba", "Sentetik Pilav", "Sentetik Meyve", "Seçmeli 4. Çeşit"])


class ServiceMappingTests(unittest.TestCase):
    """The tool reads the JSON service; each food of the reply is listed once."""

    def test_selectable_options_are_not_repeated_or_counted_twice(self) -> None:
        foods = [
            {"ObjectId": 1, "FoodName": "Sentetik Corba", "FoodType": "corba", "TotalWeight": 150},
            {"ObjectId": 2, "FoodName": "Sentetik Pilav", "FoodType": "pilav", "TotalWeight": 300},
            {"ObjectId": 3, "FoodName": "Sentetik Tatli", "FoodType": "secmeli-4-cesit", "TotalWeight": 250},
            {"ObjectId": 4, "FoodName": "Sentetik Salata", "FoodType": "secmeli-4-cesit", "TotalWeight": 80},
        ]
        app = NinovaMcpApp.__new__(NinovaMcpApp)
        payload = {"StatusCode": 0, "MenuTotalKcal": 700, "FoodList": foods}
        with patch.object(app, "_get_portal_json", return_value=payload):
            result = app.get_cafeteria_menu(date="2031-03-04", meal="lunch")
        self.assertEqual([item["name"] for item in result["items"]],
                         ["Sentetik Corba", "Sentetik Pilav", "Sentetik Tatli", "Sentetik Salata"])
        self.assertEqual(result["count"], 4)
        # The total is the service's own figure; nothing is added up here.
        self.assertEqual(result["menu_total_kcal"], 700)


if __name__ == "__main__":
    unittest.main()
