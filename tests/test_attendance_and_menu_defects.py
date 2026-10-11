"""OBS "no attendance yet" answers and repeated cafeteria allergens (synthetic data)."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from ninova_mcp.attendance_summary import summarize_obs_attendance
from ninova_mcp.server import NinovaMcpApp

SEMESTER = {"akademikDonemId": 77, "akademikDonemKodu": "203110"}
CLASS_ROW = {
    "sinifId": 900123, "crn": "30101", "bransKodu": "ZZT", "dersKodu": "322E",
    "dersAdiTR": "Sentetik Mimari", "dersAdiEN": "Synthetic Architecture",
    "ogretimUyesi": "Sentetik Kisi", "kredi": 3, "akts": 5, "derslik": "Z-1",
}
NO_RECORD = {
    "sinifOgrenciYoklama": None, "statusCode": 0,
    "resultCode": "SCCSinifOgrenciYoklamaYok", "resultMessage": "İşlem başarılı.",
}


def obs_app(attendance: object) -> NinovaMcpApp:
    app = NinovaMcpApp.__new__(NinovaMcpApp)
    app._obs = Mock()
    app._obs.resolve_semester.return_value = SEMESTER
    app._obs.list_registered_courses.return_value = {"kayitSinifResultList": [CLASS_ROW]}
    app._obs.get_attendance.return_value = attendance
    return app


class NoAttendanceRecordedTests(unittest.TestCase):
    def test_summary_says_no_attendance_was_recorded(self) -> None:
        summary = summarize_obs_attendance(NO_RECORD, course=CLASS_ROW)
        self.assertFalse(summary["available"])
        self.assertEqual(summary["status"], "no_attendance_recorded")
        self.assertNotIn("başarılı", summary["message"])
        self.assertIn("yoklama", summary["message"].lower())
        self.assertEqual(summary["result_code"], "SCCSinifOgrenciYoklamaYok")

    def test_summary_keeps_only_a_short_class_reference(self) -> None:
        summary = summarize_obs_attendance(NO_RECORD, course=CLASS_ROW)
        self.assertEqual(
            summary["course"],
            {"sinifId": 900123, "crn": "30101", "code": "ZZT 322E", "title": "Sentetik Mimari"},
        )

    def test_empty_week_list_also_means_nothing_was_recorded(self) -> None:
        payload = {"sinifOgrenciYoklama": {"yoklamaHaftaListe": []}, "statusCode": 0,
                   "resultCode": "SCCSinifOgrenciYoklamaYok", "resultMessage": "İşlem başarılı."}
        summary = summarize_obs_attendance(payload, course=CLASS_ROW)
        self.assertEqual(summary["status"], "no_attendance_recorded")
        self.assertFalse(summary["available"])

    def test_other_empty_answers_do_not_repeat_the_generic_success_text(self) -> None:
        payload = {**NO_RECORD, "resultCode": "SCCBaskaBirDurum"}
        summary = summarize_obs_attendance(payload, course=CLASS_ROW)
        self.assertEqual(summary["status"], "unavailable")
        self.assertNotIn("başarılı", summary["message"])
        self.assertEqual(summary["result_code"], "SCCBaskaBirDurum")

    def test_missing_payload_is_unavailable(self) -> None:
        summary = summarize_obs_attendance(None, course={"sinifId": 5, "source": "class_id"})
        self.assertEqual(summary["status"], "unavailable")
        self.assertEqual(summary["course"], {"sinifId": 5})

    def test_recorded_attendance_is_marked_ok(self) -> None:
        payload = {"sinifOgrenciYoklama": {"katilim": "8 / 10 (%80)", "yoklamaHaftaListe": [{}]}, "statusCode": 0}
        summary = summarize_obs_attendance(payload, course=CLASS_ROW)
        self.assertTrue(summary["available"])
        self.assertEqual(summary["status"], "ok")
        self.assertEqual(summary["course"]["code"], "ZZT 322E")
        self.assertEqual(summary["present"], 8)

    def test_tool_keeps_its_top_level_fields(self) -> None:
        result = obs_app(NO_RECORD).obs_get_attendance(course="ZZT 322E")
        # The stock success envelope is left out; the code that says "nothing recorded" stays.
        self.assertEqual(
            result["attendance"], {"sinifOgrenciYoklama": None, "resultCode": "SCCSinifOgrenciYoklamaYok"},
        )
        self.assertEqual(
            obs_app(NO_RECORD).obs_get_attendance(course="ZZT 322E", include_raw=True)["attendance"], NO_RECORD,
        )
        self.assertIsNone(result["error"])
        self.assertEqual(result["class"]["sinifId"], 900123)
        self.assertEqual(result["class"]["ogretimUyesi"], "Sentetik Kisi")
        self.assertEqual(result["summary"]["status"], "no_attendance_recorded")
        self.assertNotIn("ogretimUyesi", result["summary"]["course"])


MILK = {"SideEffectName": "Süt ve süt ürünleri (laktoz dahil)",
        "SideEffectNameEN": "Milk and products thereof (including lactose)"}
EGG = {"SideEffectName": "Yumurta ve yumurta ürünleri", "SideEffectNameEN": "Egg and products thereof "}
GLUTEN = {"SideEffectName": "Gluten içeren tahıllar", "SideEffectNameEN": "Cereals containing gluten"}


class CafeteriaAllergenTests(unittest.TestCase):
    def menu(self, *foods: dict) -> dict:
        app = NinovaMcpApp.__new__(NinovaMcpApp)
        payload = {"StatusCode": 0, "MenuTotalKcal": 900, "FoodList": list(foods)}
        with patch.object(app, "_get_portal_json", return_value=payload):
            return app.get_cafeteria_menu(date="2031-03-04", meal="lunch")

    def test_repeated_allergens_are_listed_once_in_their_first_position(self) -> None:
        result = self.menu(
            {"ObjectId": 1, "FoodName": "Sentetik Corba", "FoodSideEffectInformationList": [MILK, MILK]},
            {"ObjectId": 2, "FoodName": "Sentetik Borek",
             "FoodSideEffectInformationList": [GLUTEN, MILK, EGG, {**MILK, "SideEffectName": " süt ve süt ürünleri  (laktoz dahil) "}]},
        )
        self.assertEqual([entry["name"] for entry in result["items"][0]["allergens"]], [MILK["SideEffectName"]])
        self.assertEqual(
            [entry["name"] for entry in result["items"][1]["allergens"]],
            [GLUTEN["SideEffectName"], MILK["SideEffectName"], EGG["SideEffectName"]],
        )

    def test_allergen_names_are_stripped(self) -> None:
        result = self.menu({"ObjectId": 3, "FoodName": "Sentetik Omlet", "FoodSideEffectInformationList": [EGG]})
        self.assertEqual(
            result["items"][0]["allergens"],
            [{"name": "Yumurta ve yumurta ürünleri", "name_en": "Egg and products thereof"}],
        )

    def test_entry_without_any_name_is_dropped(self) -> None:
        result = self.menu({"ObjectId": 4, "FoodName": "Sentetik Pilav",
                            "FoodSideEffectInformationList": [{"SideEffectName": " ", "SideEffectNameEN": None}, "x"]})
        self.assertEqual(result["items"][0]["allergens"], [])


if __name__ == "__main__":
    unittest.main()
