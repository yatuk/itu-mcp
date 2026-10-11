"""Summary fields that were blank or misleading in a live read."""

from __future__ import annotations

import unittest

from ninova_mcp.registration_status import summarize_registration_status

from test_registration_status import CALENDAR, NOW, records

STATUS_URL = "https://obs.itu.edu.tr/api/ogrenci/KayitDurumu"
LESSON_URL = "https://obs.itu.edu.tr/api/ogrenci/DersKayitDurumu"


def row(registration, lesson):
    return summarize_registration_status(registration, lesson, CALENDAR, now=NOW)["per_program"][0]


def with_summer(registration):
    # A summer term with only failed courses reports a 0.0 term average.
    registration["kayitDurumuList"][0]["kayitDurumuDonemList"].insert(0, {
        "akademikDonemKodu": "202630", "akademikDonemAdi": "2025-2026 Yaz", "sinifSeviye": "3. Sınıf",
        "donemlikNotOrtalamasi": 0.0, "genelNotOrtalamasi": 2.3, "verilenKredi": 0.0,
    })
    return registration


class EnrollmentStatusTests(unittest.TestCase):
    def test_blank_university_overall_status_falls_back_to_the_other_endpoint(self) -> None:
        registration, lesson = records()
        registration["kayitDurumuList"][0].update(
            akademikProgramAdi="", akademikProgramAdiEN="", fakulteAdi="",
            akademikBolumAdi="Üniversite Geneli", akademikBolumAdiEN="University Overall", durum="")
        result = row(registration, lesson)
        self.assertTrue(result["active"])
        self.assertEqual(result["enrollment_status"], "Aktif")
        self.assertEqual(result["enrollment_status_source"], LESSON_URL)
        self.assertEqual([item["value"] for item in result["enrollment_status_evidence"]], ["", "Aktif"])

    def test_own_status_is_preferred_and_sourced(self) -> None:
        registration, lesson = records()
        result = row(registration, lesson)
        self.assertEqual(result["enrollment_status"], "Aktif")
        self.assertEqual(result["enrollment_status_source"], STATUS_URL)

    def test_whitespace_only_status_counts_as_blank(self) -> None:
        registration, lesson = records()
        registration["kayitDurumuList"][0]["durum"] = "  "
        result = row(registration, lesson)
        self.assertEqual(result["enrollment_status"], "Aktif")
        self.assertEqual(result["enrollment_status_source"], LESSON_URL)

    def test_no_evidence_anywhere_stays_empty_without_a_source(self) -> None:
        registration, lesson = records()
        registration["kayitDurumuList"][0]["durum"] = ""
        lesson["dersKayitDurumuList"][0]["durum"] = ""
        result = row(registration, lesson)
        self.assertEqual(result["enrollment_status"], "")
        self.assertIsNone(result["enrollment_status_source"])
        self.assertIsNone(result["active"])

    def test_conflicting_evidence_is_not_resolved_by_the_fallback(self) -> None:
        registration, lesson = records()
        lesson["dersKayitDurumuList"][0]["durum"] = "Pasif"
        result = row(registration, lesson)
        self.assertEqual(result["enrollment_status"], "Aktif")
        self.assertEqual(result["enrollment_status_source"], STATUS_URL)
        self.assertIsNone(result["active"])


class LastRegularTermTests(unittest.TestCase):
    def test_summer_latest_term_is_accompanied_by_the_last_regular_term(self) -> None:
        registration, lesson = records()
        result = row(with_summer(registration), lesson)
        self.assertEqual(result["latest_academic_term"]["code"], "202630")
        self.assertEqual(result["official_term_gpa"], 0.0)
        self.assertEqual(result["official_cumulative_gpa"], 2.3)
        self.assertEqual(result["class_level"], 3)
        self.assertTrue(result["latest_term_is_summer"])
        regular = result["last_regular_term"]
        self.assertEqual(regular["code"], "202620")
        self.assertEqual(regular["name"], "2025-2026 Bahar")
        self.assertEqual(regular["academic_term"], "spring")
        self.assertEqual(regular["gpa"], 2.5)
        self.assertEqual(regular["cumulative_gpa"], 2.4)
        self.assertEqual(regular["class_label"], "3. Sınıf")
        self.assertIn("summer", result["last_regular_term_note"])

    def test_regular_latest_term_is_its_own_last_regular_term(self) -> None:
        registration, lesson = records()
        result = row(registration, lesson)
        self.assertFalse(result["latest_term_is_summer"])
        self.assertEqual(result["last_regular_term"]["code"], "202620")
        self.assertEqual(result["last_regular_term"]["gpa"], result["official_term_gpa"])
        self.assertIsNone(result["last_regular_term_note"])

    def test_summer_is_recognized_by_code_when_the_name_has_no_season(self) -> None:
        registration, lesson = records()
        with_summer(registration)
        registration["kayitDurumuList"][0]["kayitDurumuDonemList"][0]["akademikDonemAdi"] = "Unnamed term"
        result = row(registration, lesson)
        self.assertTrue(result["latest_term_is_summer"])
        self.assertEqual(result["last_regular_term"]["code"], "202620")

    def test_only_summer_terms_gives_no_regular_term(self) -> None:
        registration, lesson = records()
        with_summer(registration)
        terms = registration["kayitDurumuList"][0]["kayitDurumuDonemList"]
        registration["kayitDurumuList"][0]["kayitDurumuDonemList"] = terms[:1]
        result = row(registration, lesson)
        self.assertIsNone(result["last_regular_term"])
        self.assertTrue(result["last_regular_term_note"])

    def test_conflicting_records_for_the_regular_term_are_not_guessed(self) -> None:
        registration, lesson = records()
        with_summer(registration)
        terms = registration["kayitDurumuList"][0]["kayitDurumuDonemList"]
        terms.append({**terms[1], "donemlikNotOrtalamasi": 3.9})
        result = row(registration, lesson)
        self.assertIsNone(result["last_regular_term"])
        self.assertIn("conflicting", result["last_regular_term_note"])

    def test_unreadable_term_list_gives_no_regular_term(self) -> None:
        registration, lesson = records()
        registration["kayitDurumuList"][0]["kayitDurumuDonemList"] = [{"akademikDonemKodu": "unknown"}]
        result = row(registration, lesson)
        self.assertIsNone(result["last_regular_term"])
        self.assertIsNone(result["latest_term_is_summer"])


if __name__ == "__main__":
    unittest.main()
