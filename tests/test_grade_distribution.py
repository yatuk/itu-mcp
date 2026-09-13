"""Validate public OBS chart data without executing its JavaScript."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import unittest
from unittest.mock import Mock, patch

from ninova_mcp.grade_distribution import get_grade_distribution, parse_grade_distribution
from ninova_mcp.obs_client import ObsError, ObsPublicClient
from ninova_mcp.server import NinovaMcpApp


SOURCE_URL = "https://obs.itu.edu.tr/public/DersNotDagilimi/NotDagilimiSearch?bransKodu=UZB&dersNo=438&yil=2026"
TITLE = "UZB 438 - UZB 438E, Robotik Kontrol Sistemleri / Robotic Control Systems Not Dağılımı"
# Exact public chart counts observed on September 13, 2026. The fall term
# announces 43 students although its published grade rows sum to 42.
SPRING = [("AA", 34, 64.15), ("BA+", 3, 5.66), ("BA", 3, 5.66),
          ("BB+", 1, 1.89), ("BB", 4, 7.55), ("CB+", 1, 1.89),
          ("CB", 1, 1.89), ("CC+", 1, 1.89), ("CC", 0, 0.0),
          ("DC+", 1, 1.89), ("DC", 1, 1.89), ("DD+", 1, 1.89),
          ("DD", 0, 0.0), ("FF", 2, 3.77), ("VF", 0, 0.0)]
FALL = [("AA", 14, 32.56), ("BA+", 10, 23.26), ("BA", 6, 13.95),
        ("BB+", 4, 9.30), ("BB", 2, 4.65), ("CB+", 1, 2.33),
        ("CB", 3, 6.98), ("CC+", 0, 0.0), ("CC", 1, 2.33),
        ("DC+", 0, 0.0), ("DC", 0, 0.0), ("DD+", 0, 0.0),
        ("DD", 0, 0.0), ("FF", 1, 2.33), ("VF", 0, 0.0)]


def term(code: str, name: str, total: int, grades: list[tuple]) -> dict:
    return {
        "DonemKodu": code, "YilAdi": "2025-2026", "DonemTipAdi": name,
        "ToplamAciklananOgrenci": total,
        "Dagilim": [{"HarfNotu": grade, "Sayi": count, "Yuzde": percentage}
                    for grade, count, percentage in grades],
    }


OFFICIAL_DATA = [term("202620", "Bahar Dönemi", 53, SPRING), term("202610", "Güz Dönemi", 43, FALL)]
NOT_FOUND_HTML = "<h4>İlgili ders koduna, numarasına ve yılına ait ders not dağılımı bulunamamıştır.</h4>"


def page(data: object, title: str = TITLE) -> str:
    return (
        f"<h3>{title}</h3>"
        '<table><tr><th>Grade</th><th>Count</th></tr><tr><td>AA</td><td>0</td></tr></table>'
        f"<script>const ALL_DATA = {json.dumps(data)}; const unrelated = true;</script>"
    )


OFFICIAL_HTML = page(OFFICIAL_DATA)


class GradeDistributionParserTests(unittest.TestCase):
    def parse(self, data: object, *, title: str = TITLE, year: int = 2026) -> dict:
        return parse_grade_distribution(page(data, title), SOURCE_URL, "UZB 438E", year)

    def test_official_chart_counts_override_zero_table_placeholders(self) -> None:
        result = self.parse(OFFICIAL_DATA)
        spring, fall = result["terms"]
        self.assertEqual(spring["announced_student_count"], 53)
        self.assertEqual(spring["counts_by_grade"]["AA"], 34)
        self.assertEqual(spring["counts_by_grade"]["BB"], 4)
        self.assertEqual(spring["counts_by_grade"]["BA+"], 3)
        self.assertEqual(spring["academic_year"], "2025-2026")
        self.assertEqual(fall["announced_student_count"], 43)
        self.assertEqual(fall["counts_by_grade"]["AA"], 14)
        self.assertEqual(fall["counts_by_grade"]["BB"], 2)
        self.assertTrue(spring["consistent"])

    def test_official_fall_total_disagreement_remains_visible(self) -> None:
        fall = self.parse(OFFICIAL_DATA)["terms"][1]
        self.assertEqual(fall["counted_student_count"], 42)
        self.assertEqual(fall["announced_student_count"], 43)
        self.assertFalse(fall["consistent"])
        self.assertEqual(fall["status"], "incomplete")
        self.assertTrue(fall["warnings"])

    def test_combined_language_scope_is_preserved(self) -> None:
        result = self.parse(OFFICIAL_DATA)
        self.assertEqual(result["requested_course_code"], "UZB 438E")
        self.assertEqual(result["reported_course_codes"], ["UZB 438", "UZB 438E"])
        self.assertEqual(result["aggregation_scope"], "combined_course_codes")
        self.assertIn("language-specific", result["scope_note"])
        self.assertEqual(result["course_name"], "Robotik Kontrol Sistemleri / Robotic Control Systems")
        self.assertTrue(result["untrusted_external_content"])

    def test_wrong_course_is_rejected_even_when_chart_is_empty(self) -> None:
        for data in (OFFICIAL_DATA, []):
            with self.subTest(empty=not data), self.assertRaises(ObsError):
                self.parse(data, title="BLG 223E, Data Structures Not Dağılımı")

    def test_course_heading_is_required_even_when_chart_is_empty(self) -> None:
        for data in (OFFICIAL_DATA, []):
            with self.subTest(empty=not data), self.assertRaises(ObsError):
                self.parse(data, title="Temporary service page")

    def test_wrong_term_year_and_duplicate_terms_are_rejected(self) -> None:
        for change in ("202520", 202620, "20262", True):
            data = deepcopy(OFFICIAL_DATA)
            data[0]["DonemKodu"] = change
            with self.subTest(code=change), self.assertRaises(ObsError):
                self.parse(data)
        with self.assertRaises(ObsError):
            self.parse([OFFICIAL_DATA[0], OFFICIAL_DATA[0]])

    def test_conflicting_academic_year_label_is_rejected(self) -> None:
        data = deepcopy(OFFICIAL_DATA)
        data[0]["YilAdi"] = "2024-2025"
        with self.assertRaises(ObsError):
            self.parse(data)

    def test_counts_require_nonnegative_integers(self) -> None:
        for value in (True, False, 34.0, "34", -1, None):
            for target in ("total", "grade"):
                data = deepcopy(OFFICIAL_DATA)
                if target == "total":
                    data[0]["ToplamAciklananOgrenci"] = value
                else:
                    data[0]["Dagilim"][0]["Sayi"] = value
                with self.subTest(value=value, target=target), self.assertRaises(ObsError):
                    self.parse(data)

    def test_invalid_percentages_are_rejected(self) -> None:
        for value in (True, "64.15", -1, 101, float("nan"), float("inf"), None):
            data = deepcopy(OFFICIAL_DATA)
            data[0]["Dagilim"][0]["Yuzde"] = value
            with self.subTest(value=value), self.assertRaises(ObsError):
                self.parse(data)

    def test_percentage_mismatch_is_incomplete_without_replacing_source_values(self) -> None:
        data = deepcopy(OFFICIAL_DATA[:1])
        data[0]["Dagilim"][0]["Yuzde"] = 40.0
        result = self.parse(data)["terms"][0]
        self.assertEqual(result["counted_student_count"], 53)
        self.assertFalse(result["consistent"])
        self.assertEqual(result["grades"][0]["percentage"], 40.0)
        self.assertEqual(result["status"], "incomplete")

    def test_duplicate_or_malformed_grade_rows_are_rejected(self) -> None:
        changes = [None, "AA", {"HarfNotu": "AA", "Sayi": 0, "Yuzde": 0},
                   {"HarfNotu": "AA++", "Sayi": 0, "Yuzde": 0}]
        for entry in changes:
            data = deepcopy(OFFICIAL_DATA)
            data[0]["Dagilim"].append(entry)
            with self.subTest(entry=entry), self.assertRaises(ObsError):
                self.parse(data)

    def test_missing_or_malformed_chart_structure_is_rejected(self) -> None:
        for data in ({}, [None], [{"DonemKodu": "202620"}], "[]"):
            with self.subTest(data=data), self.assertRaises(ObsError):
                self.parse(data)
        for html in ("<h3>OBS maintenance</h3>", "<script>const ALL_DATA = [;</script>",
                     "<script>const ALL_DATA = (() => { throw new Error('must not execute'); })();</script>"):
            with self.subTest(html=html), self.assertRaises(ObsError):
                parse_grade_distribution(html, SOURCE_URL, "UZB 438E", 2026)

    def test_explicit_not_found_page_is_an_empty_distribution(self) -> None:
        result = parse_grade_distribution(NOT_FOUND_HTML, SOURCE_URL, "UZB 999E", 2026)
        self.assertEqual(result["terms"], [])
        self.assertEqual(result["aggregation_scope"], "unknown")

    def test_zero_announced_term_is_distinct_from_missing_distribution(self) -> None:
        result = self.parse([term("202620", "Bahar Dönemi", 0, [])])["terms"][0]
        self.assertEqual(result["status"], "no_announced_grades")
        self.assertTrue(result["consistent"])
        self.assertEqual(result["announced_student_count"], 0)


class GradeDistributionClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.public = Mock(spec=ObsPublicClient)
        self.public._get_html.return_value = (OFFICIAL_HTML, SOURCE_URL)

    def test_exact_public_route_preserves_course_suffix(self) -> None:
        result = get_grade_distribution(self.public, "uzb438e", year=2026)
        self.public._get_html.assert_called_once_with(
            "/public/DersNotDagilimi/NotDagilimiSearch",
            params={"bransKodu": "UZB", "dersNo": "438E", "yil": 2026},
        )
        self.assertEqual(len(result["terms"]), 2)
        self.assertTrue(result["available"])
        self.assertFalse(result["complete"])

    def test_term_selection_keeps_available_terms_and_uses_ending_year(self) -> None:
        result = get_grade_distribution(self.public, "UZB438E", term_code="202620")
        self.assertEqual(self.public._get_html.call_args.kwargs["params"]["yil"], 2026)
        self.assertEqual([row["term_code"] for row in result["terms"]], ["202620"])
        self.assertEqual(len(result["available_terms"]), 2)
        self.assertTrue(result["complete"])
        self.assertEqual(result["requested_term_code"], "202620")

    def test_missing_selected_term_does_not_become_zero_announced_students(self) -> None:
        result = get_grade_distribution(self.public, "UZB438E", term_code="202630")
        self.assertFalse(result["available"])
        self.assertFalse(result["has_announced_grades"])
        self.assertEqual(result["terms"], [])
        self.assertEqual(len(result["available_terms"]), 2)
        self.assertIn("does not establish that nobody took", result["note"])

    def test_explicit_zero_announced_term_is_still_available(self) -> None:
        self.public._get_html.return_value = (page([term("202620", "Bahar Dönemi", 0, [])]), SOURCE_URL)
        result = get_grade_distribution(self.public, "UZB438E", year=2026)
        self.assertTrue(result["available"])
        self.assertFalse(result["has_announced_grades"])
        self.assertEqual(result["terms"][0]["status"], "no_announced_grades")

    def test_default_year_uses_current_istanbul_calendar_year(self) -> None:
        with patch("ninova_mcp.grade_distribution.datetime") as clock:
            clock.now.return_value = datetime(2026, 9, 13, 12, 0)
            result = get_grade_distribution(self.public, "UZB438E")
        self.assertEqual(result["year"], 2026)
        self.assertTrue(all(str(call.args[0]) == "Europe/Istanbul" for call in clock.now.call_args_list))

    def test_invalid_input_is_rejected_before_public_request(self) -> None:
        cases = [
            {"course_code": "UZB"}, {"course_code": "../438"}, {"course_code": None}, {"course_code": 438},
            {"course_code": "UZB438E", "year": True}, {"course_code": "UZB438E", "year": "2026"},
            {"course_code": "UZB438E", "year": 1800}, {"course_code": "UZB438E", "year": 2101},
            {"course_code": "UZB438E", "term_code": 202620}, {"course_code": "UZB438E", "term_code": "2026"},
            {"course_code": "UZB438E", "year": 2025, "term_code": "202620"},
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(ObsError):
                get_grade_distribution(self.public, **kwargs)
        self.public._get_html.assert_not_called()

    def test_tool_needs_no_authenticated_client(self) -> None:
        app = NinovaMcpApp()
        app._obs_public = self.public
        with patch.object(NinovaMcpApp, "obs", property(lambda self: (_ for _ in ()).throw(AssertionError("Authentication is forbidden.")))):
            result = app.obs_get_grade_distribution("UZB438E", year=2026, term_code="202620")
        self.assertEqual(result["terms"][0]["counts_by_grade"]["AA"], 34)


if __name__ == "__main__":
    unittest.main()
