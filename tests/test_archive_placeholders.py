"""Placeholder cells are not data.

``-``, ``--``, ``- -``, ``-/-``, ``undeclared`` and the link text ``Detay`` stand
for "nothing here" in the archive. They must not be counted as an instructor,
shown as a prerequisite note, or pass for a catalog description. Synthetic
fixtures; every name is invented.
"""

from __future__ import annotations

import unittest
from typing import Any

from test_archive_datasets import BASE, FakeArchiveSession

from ninova_mcp.archive import (
    catalog_entry,
    clean_list,
    clean_placeholder,
    course_history,
    diff_term_offerings,
    is_empty_record,
    summarize_exam,
    summarize_search_row,
    summarize_section,
    who_taught,
)
from ninova_mcp.archive_client import ItuArchiveClient, ItuArchiveError
from ninova_mcp.server import NinovaMcpApp

PLACEHOLDERS = ["", " ", "-", " - ", "--", "- -", "-  -", "-/-", " -/- ", "undeclared", "Undeclared",
                "UNDECLARED", "Detay", "detay", " DETAY "]

ENTRY = {
    "code": "TST 201E",
    "name": "Sample Systems",
    "rows": [
        ["2026-2027-guz", "-", 50, 10, "Salı"],
        ["2026-2027-guz", "Ada Kurgu", 50, 40, "Cuma"],
        ["2025-2026-bahar", "--", 50, 20, "Salı"],
        ["2025-2026-bahar", " - ", 50, 20, "Salı"],
        ["2025-2026-bahar", "", 50, 20, "Salı"],
    ],
}

RAW_SECTION = {
    "crn": "30001", "code": "TST 201E", "branch": "TST", "level": "LS", "name": "Sample Systems",
    "method": "Fiziksel (Yüz yüze)", "instructor": "-", "buildings": ["AAA", "--"],
    "days": ["Salı", "Cuma"], "times": ["08:30/10:29", "10:30/12:29"], "rooms": ["Z-1", "-"],
    "capacity": 50, "enrolled": 10, "reserved": "-",
    "programs": ["TST_LS", "TST_LS", "TSTE_LS", "TST_LS", "-", "TSTE_LS"],
    "prereq": "Detay", "classReq": "-",
}

INDEX = {
    "currentTerm": "2026-2027 Güz Dönemi",
    "currentSlug": "2026-2027-guz",
    "stats": {"capacity": 100, "enrolled": 50},
    "terms": [
        {
            "provenance": {"provider": "Örnek Kaynak", "endpoint": "https://example.invalid/x",
                           "lastSuccessfulAt": "2026-10-05T09:05:14Z"},
            "slug": "2026-2027-guz", "label": "2026-2027 Güz Dönemi", "source": "itu-archive",
            "live": True, "sections": 2,
        },
        {
            "provenance": {"provider": "", "endpoint": "", "lastSuccessfulAt": ""},
            "slug": "2025-2026-bahar", "label": "2025-2026 Bahar Dönemi", "source": "itu-archive",
            "sections": 3,
        },
    ],
}

META = {"sections": 2, "courses": 1, "branches": [{"code": "TST", "levels": ["LS"], "sections": 2}],
        "stats": {"capacity": 100, "enrolled": 50}}

DOCUMENTS: dict[str, Any] = {
    "/index.json": INDEX,
    "/history/courses/TST.json": {"TST 201E": ENTRY},
    "/history/names.json": [
        ["-", "_", 20, 800],
        ["--", "_", 9, 15],
        ["Ada Kurgu", "a", 2, 2],
        ["Ada-Su Kurgu", "a", 1, 1],
    ],
    "/history/instructors/a.json": {
        "Ada Kurgu": {"name": "Ada Kurgu", "terms": 1,
                      "rows": [["2026-2027-guz", "TST 201E", "Sample Systems", 50, 40]]},
    },
    "/history/instructors/_.json": {
        "-": {"name": "-", "terms": 20, "rows": [["2026-2027-guz", "TST 201E", "Sample Systems", 50, 10]]},
    },
    "/terms/2026-2027-guz/meta.json": META,
    "/terms/2026-2027-guz/branches/TST.json": [
        RAW_SECTION,
        dict(RAW_SECTION, crn="30002", instructor="Ada Kurgu", enrolled=40),
    ],
    "/terms/2025-2026-bahar/meta.json": META,
    "/terms/2025-2026-bahar/branches/TST.json": [
        dict(RAW_SECTION, crn="20001", instructor="--", enrolled=20),
        dict(RAW_SECTION, crn="20002", instructor="Mert Sahte", enrolled=20),
    ],
    "/catalog/TST.json": {
        "TST 201E": {
            "code": "TST 201E", "name": "Örnek Sistemler", "nameEn": "Sample Systems",
            "language": "English", "credits": {"theory": 3, "practice": 0, "lab": 0, "local": 3, "ects": 5},
            "description": "- -", "outcomes": None, "weeklyTopics": None, "textbooks": ["-"],
            "sourceUrl": "https://example.invalid/katalog",
        },
    },
}


def make_app() -> NinovaMcpApp:
    client = ItuArchiveClient(session=FakeArchiveSession(DOCUMENTS), base_url=BASE, cache_ttl=60)  # type: ignore[arg-type]
    client._min_request_interval = 0
    app = NinovaMcpApp()
    app._archive = client
    return app


class NormalizerTests(unittest.TestCase):
    def test_placeholders_become_none(self) -> None:
        for raw in PLACEHOLDERS:
            with self.subTest(value=raw):
                self.assertIsNone(clean_placeholder(raw))

    def test_real_values_are_kept_and_stripped(self) -> None:
        self.assertEqual(clean_placeholder(" Ada Kurgu "), "Ada Kurgu")
        self.assertEqual(clean_placeholder("Ada-Su Kurgu"), "Ada-Su Kurgu")
        self.assertEqual(clean_placeholder("TST 101 MIN DD veya TST 102 MIN DD"), "TST 101 MIN DD veya TST 102 MIN DD")
        self.assertEqual(clean_placeholder("Detaylı Tasarım"), "Detaylı Tasarım")
        self.assertEqual(clean_placeholder(0), 0)
        self.assertIsNone(clean_placeholder(None))

    def test_lists_lose_placeholders_and_repeats_in_order(self) -> None:
        self.assertEqual(clean_list(["B", "A", "B", "-", "", "A", "C"]), ["B", "A", "C"])
        self.assertEqual(clean_list(["-"]), [])
        self.assertIsNone(clean_list("- -"))

    def test_empty_record(self) -> None:
        self.assertTrue(is_empty_record({"provider": "", "endpoint": "", "lastSuccessfulAt": ""}))
        self.assertFalse(is_empty_record({"provider": "X", "endpoint": ""}))
        self.assertFalse(is_empty_record("x"))


class InstructorTests(unittest.TestCase):
    def test_course_history_lists_only_people(self) -> None:
        offerings = {o["term"]: o for o in course_history(ENTRY)["offerings"]}
        self.assertEqual(offerings["2026-2027-guz"]["instructors"], ["Ada Kurgu"])
        self.assertEqual(offerings["2026-2027-guz"]["unannounced_section_count"], 1)
        self.assertEqual(offerings["2025-2026-bahar"]["instructors"], [])
        self.assertEqual(offerings["2025-2026-bahar"]["unannounced_section_count"], 3)
        self.assertEqual(
            [row["instructor"] for row in offerings["2026-2027-guz"]["sections"]], [None, "Ada Kurgu"]
        )

    def test_who_taught_never_ranks_a_placeholder(self) -> None:
        result = who_taught(ENTRY)
        self.assertEqual([item["instructor"] for item in result["instructors"]], ["Ada Kurgu"])
        self.assertEqual(result["unannounced_section_count"], 4)

    def test_diff_does_not_report_a_dash_as_a_new_instructor(self) -> None:
        side_a = [{"instructor": "Mert Sahte", "capacity": 50, "enrolled": 20, "fill_ratio": 0.4},
                  {"instructor": "--", "capacity": 50, "enrolled": 20, "fill_ratio": 0.4}]
        side_b = [{"instructor": "-", "capacity": 50, "enrolled": 10, "fill_ratio": 0.2},
                  {"instructor": "Ada Kurgu", "capacity": 50, "enrolled": 40, "fill_ratio": 0.8}]
        diff = diff_term_offerings(side_a, side_b)
        self.assertEqual(diff["instructors_added"], ["Ada Kurgu"])
        self.assertEqual(diff["instructors_removed"], ["Mert Sahte"])
        self.assertEqual(diff["second"]["instructors"], ["Ada Kurgu"])
        self.assertEqual(diff["second"]["unannounced_section_count"], 1)
        self.assertEqual(diff["first"]["unannounced_section_count"], 1)

    def test_compare_terms_tool(self) -> None:
        result = make_app().archive_compare_terms("TST 201E", "2025-2026-bahar", "2026-2027-guz")
        self.assertEqual(result["diff"]["instructors_added"], ["Ada Kurgu"])
        self.assertEqual(result["diff"]["instructors_removed"], ["Mert Sahte"])

    def test_who_taught_tool(self) -> None:
        result = make_app().archive_who_taught("TST 201E")
        self.assertEqual([item["instructor"] for item in result["instructors"]], ["Ada Kurgu"])

    def test_instructor_courses_refuses_the_placeholder(self) -> None:
        app = make_app()
        for raw in ("-", "--", " - "):
            with self.subTest(instructor=raw), self.assertRaises(ItuArchiveError) as caught:
                app.archive_instructor_courses(raw)
            self.assertIn("öğretim üyesi adı değil", str(caught.exception))

    def test_instructor_courses_never_offers_the_placeholder_as_a_match(self) -> None:
        app = make_app()
        result = app.archive_instructor_courses("kurgu")
        self.assertTrue(result["ambiguous"])
        self.assertEqual([m["instructor"] for m in result["matches"]], ["Ada Kurgu", "Ada-Su Kurgu"])
        self.assertEqual(app.archive_instructor_courses("Ada Kurgu")["instructor"], "Ada Kurgu")


class SectionSummaryTests(unittest.TestCase):
    def test_placeholder_fields_are_none(self) -> None:
        row = summarize_section(RAW_SECTION)
        self.assertIsNone(row["instructor"])
        self.assertIsNone(row["prerequisite_note"])
        self.assertIsNone(row["class_requirement"])
        self.assertIsNone(row["reserved"])
        self.assertEqual(row["method"], "Fiziksel (Yüz yüze)")
        self.assertEqual(row["sessions"][1]["building"], None)
        self.assertEqual(row["sessions"][1]["room"], None)
        self.assertEqual(row["sessions"][0]["room"], "Z-1")

    def test_real_notes_are_kept(self) -> None:
        row = summarize_section(dict(
            RAW_SECTION, prereq="TST 101 MIN DD", classReq="3. Sınıf", reserved="TST_LS/40/12 | Diğer/10/0",
        ))
        self.assertEqual(row["prerequisite_note"], "TST 101 MIN DD")
        self.assertEqual(row["class_requirement"], "3. Sınıf")
        self.assertEqual(row["reserved"], "TST_LS/40/12 | Diğer/10/0")

    def test_programs_are_unique_in_first_seen_order(self) -> None:
        self.assertEqual(summarize_section(RAW_SECTION)["programs"], ["TST_LS", "TSTE_LS"])

    def test_search_row(self) -> None:
        row = summarize_search_row(
            ["30001", "TST 201E", "Sample Systems", "TST", "-", "Salı 08:30/10:29", 50, 10, "LS", "",
             ["TST_LS", "TST_LS", "TSTE_LS"], "--"]
        )
        self.assertIsNone(row["instructor"])
        self.assertIsNone(row["method"])
        self.assertEqual(row["programs"], ["TST_LS", "TSTE_LS"])
        self.assertIsNone(row["sessions"][0]["location"])

    def test_exam_row(self) -> None:
        row = summarize_exam({"crn": "1", "code": "TST 201E", "instructor": "--", "place": "-", "type": "Final"})
        self.assertIsNone(row["instructor"])
        self.assertIsNone(row["place"])
        self.assertEqual(row["exam_type"], "Final")

    def test_term_sections_tool(self) -> None:
        result = make_app().archive_term_sections(term="2026-2027-guz", branch="TST")
        first = result["sections"][0]
        self.assertIsNone(first["instructor"])
        self.assertIsNone(first["prerequisite_note"])
        self.assertEqual(first["programs"], ["TST_LS", "TSTE_LS"])


class CatalogTests(unittest.TestCase):
    def test_placeholder_fields_are_missing(self) -> None:
        result = catalog_entry(DOCUMENTS["/catalog/TST.json"]["TST 201E"])
        self.assertIsNone(result["description"])
        self.assertEqual(result["textbooks"], [])
        self.assertEqual(
            result["missing_fields"], ["description", "outcomes", "weekly_topics", "textbooks"]
        )
        self.assertEqual(result["credits"]["ects"], 5)
        self.assertEqual(result["course_name_en"], "Sample Systems")

    def test_real_fields_are_untouched(self) -> None:
        result = catalog_entry({
            "code": "TST 1", "name": "Ad", "nameEn": "Name", "language": "Türkçe", "credits": {"ects": 5},
            "description": "Gerçek bir tanım - tireli.", "outcomes": ["Bir", "İki"],
            "weeklyTopics": ["Hafta 1 · Giriş"], "textbooks": ["Ders Notları", "-"], "sourceUrl": "https://x.invalid",
        })
        self.assertEqual(result["missing_fields"], [])
        self.assertEqual(result["description"], "Gerçek bir tanım - tireli.")
        self.assertEqual(result["textbooks"], ["Ders Notları"])

    def test_catalog_tool(self) -> None:
        result = make_app().archive_course_catalog("TST 201E")
        self.assertIn("description", result["missing_fields"])
        self.assertIn("textbooks", result["missing_fields"])
        self.assertIsNone(result["description"])


class ListTermsTests(unittest.TestCase):
    def test_empty_provenance_is_dropped_and_a_real_one_kept(self) -> None:
        terms = {term["slug"]: term for term in make_app().archive_list_terms()["terms"]}
        self.assertNotIn("provenance", terms["2025-2026-bahar"])
        self.assertEqual(terms["2026-2027-guz"]["provenance"]["provider"], "Örnek Kaynak")
        self.assertEqual(terms["2025-2026-bahar"]["sections"], 3)


if __name__ == "__main__":
    unittest.main()
