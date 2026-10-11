"""Dashboard course codes, sections and start/end date cells (synthetic data)."""

from __future__ import annotations

import unittest
from pathlib import Path

from ninova_mcp import parsing
from ninova_mcp.parsing import extract_courses, extract_named_table, parse_html_page, summarize_dashboard

BASE = "https://ninova.itu.edu.tr"
PAGE = BASE + "/Kampus1"
FIXTURE = (Path(__file__).parent / "fixtures" / "ninova_dashboard_sections.html").read_text(encoding="utf-8")


class DashboardCourseCodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.courses = extract_courses(FIXTURE, PAGE, BASE)

    def test_code_comes_from_the_course_element_not_from_a_section_label(self) -> None:
        self.assertEqual(
            [course["code"] for course in self.courses],
            ["ZZT 322E", "ZZT 500", "ZZT 500", "QQQ 201"],
        )
        for course in self.courses:
            self.assertNotIn("code_uncertain", course)

    def test_two_sections_of_one_course_are_told_apart(self) -> None:
        first, second = self.courses[1], self.courses[2]
        self.assertEqual(first["title"], second["title"])
        self.assertEqual(first["section"], "2031 Exemption and Abroad")
        self.assertEqual(second["section"], "ZZT 2030 Summer Internship")
        self.assertNotEqual(first["url"], second["url"])
        self.assertIsNone(first["crn"])

    def test_crn_is_a_separate_field(self) -> None:
        self.assertEqual(self.courses[0]["crn"], "30101")
        self.assertEqual(self.courses[0]["section"], "CRN: 30101")
        self.assertEqual(self.courses[3]["crn"], "30303")

    def test_a_code_without_a_letter_suffix_is_left_as_the_page_shows_it(self) -> None:
        self.assertEqual(self.courses[3]["code"], "QQQ 201")

    def test_a_year_in_a_section_label_is_never_reported_as_a_code(self) -> None:
        html = (
            '<ul><li><a href="/Sinif/41002.500201"><span>2031 Exemption and Abroad</span></a>'
            '<a href="/Sinif/41002.500202"><span>ZZT 2030 Summer Internship</span></a></li></ul>'
        )
        courses = extract_courses(html, PAGE, BASE)
        self.assertEqual(len(courses), 2)
        for course in courses:
            self.assertIsNone(course["code"])
            self.assertTrue(course["code_uncertain"])

    def test_a_code_read_from_a_section_label_is_marked_uncertain(self) -> None:
        html = (
            '<ul><li><a href="/Sinif/41004.500401">ZZT 105E Ek Sube</a>'
            '<a href="/Sinif/41004.500402">Ikinci Sube</a></li></ul>'
        )
        course = extract_courses(html, PAGE, BASE)[0]
        self.assertEqual(course["code"], "ZZT 105E")
        self.assertEqual(course["code_source"], "section_label")
        self.assertTrue(course["code_uncertain"])


class DashboardDateRangeTests(unittest.TestCase):
    def test_start_and_end_of_a_submission_window_are_separate(self) -> None:
        page = parse_html_page(PAGE, FIXTURE, base_url=BASE)
        row = summarize_dashboard(page, html=FIXTURE, base_url=BASE)["recent_assignments"][0]
        self.assertEqual(row["Teslim Tarihi"], "25 Şubat 00:00 - 31 Aralık 18:00")
        self.assertEqual(
            row["date_ranges"]["Teslim Tarihi"],
            {"start": "25 Şubat 00:00", "end": "31 Aralık 18:00", "raw": "25 Şubat - 31 Aralık 00:00 18:00"},
        )

    def test_a_single_date_cell_is_unchanged(self) -> None:
        row = extract_named_table(FIXTURE, "Son Ödevler")[1]
        self.assertEqual(row["Teslim Tarihi"], "03 Mart 23:59")
        self.assertNotIn("date_ranges", row)

    def test_split_date_range_reads_both_orders(self) -> None:
        split = parsing.split_date_range
        expected = {"start": "25 Şubat 00:00", "end": "31 Aralık 18:00"}
        for raw in ("25 Şubat - 31 Aralık 00:00 18:00", "25 Şubat 00:00 - 31 Aralık 18:00"):
            with self.subTest(raw=raw):
                result = split(raw)
                self.assertEqual({key: result[key] for key in expected}, expected)
                self.assertEqual(result["raw"], raw)

    def test_split_date_range_adds_iso_values_only_when_a_year_is_present(self) -> None:
        result = parsing.split_date_range("25 Şubat 2031 - 31 Aralık 2031 00:00 18:00")
        self.assertEqual(result["start_iso"], "2031-02-25T00:00:00+03:00")
        self.assertEqual(result["end_iso"], "2031-12-31T18:00:00+03:00")
        self.assertNotIn("start_iso", parsing.split_date_range("25 Şubat - 31 Aralık 00:00 18:00"))

    def test_text_that_is_not_a_date_range_is_left_alone(self) -> None:
        for raw in ("Rapor 1 - Rapor 2 10:00 11:00", "03 Mart 23:59", "12 Deneme - 14 Deneme 10:00 11:00", ""):
            with self.subTest(raw=raw):
                self.assertIsNone(parsing.split_date_range(raw))


if __name__ == "__main__":
    unittest.main()
