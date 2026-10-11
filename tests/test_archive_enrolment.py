"""Enrolment that the archive never captured must not be read as zero.

Some term dumps were taken before registration opened: every section of the
term has ``enrolled == 0``. Those zeros are not measurements. The fixtures are
synthetic; every name is invented.
"""

from __future__ import annotations

import unittest
from typing import Any

from test_archive_datasets import BASE, FakeArchiveSession

from ninova_mcp.archive import (
    course_history,
    diff_term_offerings,
    enrolment_captured,
    enrolment_uncaptured_terms,
    search_sections,
    stats_enrolment_captured,
    summarize_section,
    who_taught,
)
from ninova_mcp.archive_client import ItuArchiveClient
from ninova_mcp.server import NinovaMcpApp

LIVE = "2026-2027-guz"
EMPTY = "2025-2026-guz"
OLD = "2023-2024-guz"

INDEX = {
    "currentTerm": "2026-2027 Güz Dönemi",
    "currentSlug": LIVE,
    "stats": {"sections": 4, "capacity": 1000, "enrolled": 500},
    "terms": [
        {"slug": LIVE, "label": "2026-2027 Güz Dönemi", "source": "itu-archive", "live": True},
        {"slug": EMPTY, "label": "2025-2026 Güz Dönemi", "source": "itu-archive"},
        {"slug": OLD, "label": "2023-2024 Güz Dönemi", "source": "itu-archive"},
    ],
}

# rows are [term, instructor, capacity, enrolled, days]
HISTORY_TST = {
    "TST 201E": {
        "code": "TST 201E",
        "name": "Sample Systems",
        "rows": [
            [LIVE, "Deniz Uydurma", 50, 0, "Salı"],
            [LIVE, "Ada Kurgu", 50, 40, "Cuma"],
            [EMPTY, "Deniz Uydurma", 80, 0, "Salı"],
            [EMPTY, "Ada Kurgu", 100, 0, "Cuma"],
            [OLD, "Ada Kurgu", 100, 90, "Cuma"],
            [OLD, "Mert Sahte", 100, 0, "Salı"],
        ],
    },
    "TST 305E": {
        "code": "TST 305E",
        "name": "Sample Networks",
        "rows": [
            [EMPTY, "Mert Sahte", 60, 0, "Pazartesi"],
            [OLD, "Mert Sahte", 60, 30, "Pazartesi"],
        ],
    },
}

# A small branch whose only section of the live term is still empty.
HISTORY_ZRO = {
    "ZRO 101": {
        "code": "ZRO 101",
        "name": "Küçük Ders",
        "rows": [[LIVE, "Ece Yapay", 10, 0, "Çarşamba"]],
    },
}


def section(crn: str, code: str, instructor: str, capacity: int, enrolled: int) -> dict[str, Any]:
    return {
        "crn": crn, "code": code, "branch": code.split()[0], "level": "LS", "name": "Sample",
        "method": "", "instructor": instructor, "buildings": ["AAA"], "days": ["Salı"],
        "times": ["08:30/10:29"], "rooms": ["1"], "capacity": capacity, "enrolled": enrolled,
        "reserved": "", "programs": ["TST_LS"], "prereq": "", "classReq": "",
    }


def meta(slug: str, enrolled: int | None) -> dict[str, Any]:
    document: dict[str, Any] = {
        "slug": slug, "sections": 3, "courses": 2,
        "branches": [{"code": "TST", "levels": ["LS"], "sections": 3}],
    }
    if enrolled is not None:
        document["stats"] = {"sections": 3, "capacity": 240, "enrolled": enrolled}
    return document


DOCUMENTS: dict[str, Any] = {
    "/index.json": INDEX,
    "/history/courses/TST.json": HISTORY_TST,
    "/history/courses/ZRO.json": HISTORY_ZRO,
    f"/terms/{EMPTY}/meta.json": meta(EMPTY, 0),
    f"/terms/{EMPTY}/branches/TST.json": [
        section("20001", "TST 201E", "Deniz Uydurma", 80, 0),
        section("20002", "TST 201E", "Ada Kurgu", 100, 0),
        section("20003", "TST 305E", "Mert Sahte", 60, 0),
    ],
    f"/terms/{LIVE}/meta.json": meta(LIVE, 500),
    f"/terms/{LIVE}/branches/TST.json": [
        section("30001", "TST 201E", "Deniz Uydurma", 50, 0),
        section("30002", "TST 201E", "Ada Kurgu", 50, 40),
    ],
    # No term-wide totals here: the branch file alone has to decide.
    f"/terms/{OLD}/meta.json": meta(OLD, None),
    f"/terms/{OLD}/branches/TST.json": [
        section("10001", "TST 201E", "Ada Kurgu", 100, 90),
        section("10002", "TST 201E", "Mert Sahte", 100, 0),
    ],
    f"/terms/{EMPTY}/search.json": [
        ["20001", "TST 201E", "Sample Systems", "TST", "Deniz Uydurma", "Salı 08:30/10:29", 80, 0,
         "LS", "", ["TST_LS"]],
        ["20009", "ABC 101", "Başka Ders", "ABC", "Ece Yapay", "Cuma 08:30/10:29", 40, 0,
         "LS", "", ["ABC_LS"]],
    ],
    f"/terms/{LIVE}/search.json": [
        ["30001", "TST 201E", "Sample Systems", "TST", "Deniz Uydurma", "Salı 08:30/10:29", 50, 0,
         "LS", "", ["TST_LS"], "AAA 1"],
        ["30002", "TST 201E", "Sample Systems", "TST", "Ada Kurgu", "Cuma 08:30/10:29", 50, 40,
         "LS", "", ["TST_LS"], "AAA 2"],
    ],
}


def make_app(documents: dict[str, Any] | None = None) -> NinovaMcpApp:
    session = FakeArchiveSession(DOCUMENTS if documents is None else documents)
    client = ItuArchiveClient(session=session, base_url=BASE, cache_ttl=60)  # type: ignore[arg-type]
    client._min_request_interval = 0
    app = NinovaMcpApp()
    app._archive = client
    return app


class CaptureRuleTests(unittest.TestCase):
    def test_all_zero_rows_are_not_captured(self) -> None:
        self.assertIs(enrolment_captured([(80, 0), (100, 0)]), False)

    def test_zero_capacity_everywhere_is_not_captured_either(self) -> None:
        self.assertIs(enrolment_captured([(0, 0), (0, 0)]), False)

    def test_one_enrolled_student_makes_the_zeros_real(self) -> None:
        self.assertIs(enrolment_captured([(80, 0), (0, 3)]), True)

    def test_no_rows_decide_nothing(self) -> None:
        self.assertIsNone(enrolment_captured([]))

    def test_term_totals_decide_for_the_whole_term(self) -> None:
        self.assertIs(stats_enrolment_captured({"capacity": 170198, "enrolled": 0}), False)
        self.assertIs(stats_enrolment_captured({"capacity": 10, "enrolled": 4}), True)
        self.assertIsNone(stats_enrolment_captured(None))
        self.assertIsNone(stats_enrolment_captured({"capacity": 10}))

    def test_decision_is_per_term_over_the_whole_branch_file(self) -> None:
        self.assertEqual(enrolment_uncaptured_terms(HISTORY_TST), [EMPTY])

    def test_a_term_known_to_be_captured_is_never_returned(self) -> None:
        self.assertEqual(enrolment_uncaptured_terms(HISTORY_ZRO), [LIVE])
        self.assertEqual(enrolment_uncaptured_terms(HISTORY_ZRO, captured_terms=[LIVE]), [])


class HistoryAggregationTests(unittest.TestCase):
    def entry(self) -> dict[str, Any]:
        return {**HISTORY_TST["TST 201E"], "enrolment_uncaptured_terms": [EMPTY]}

    def test_uncaptured_offering_has_no_enrolment_and_is_marked(self) -> None:
        history = course_history(self.entry())
        by_term = {offering["term"]: offering for offering in history["offerings"]}
        self.assertTrue(by_term[EMPTY]["enrolment_not_captured"])
        for row in by_term[EMPTY]["sections"]:
            self.assertIsNone(row["enrolled"])
            self.assertIsNone(row["fill_ratio"])
            self.assertIsNotNone(row["capacity"])
        self.assertEqual(history["enrolment_not_captured_terms"], [EMPTY])

    def test_empty_section_in_a_captured_term_stays_zero(self) -> None:
        history = course_history(self.entry())
        by_term = {offering["term"]: offering for offering in history["offerings"]}
        self.assertFalse(by_term[OLD]["enrolment_not_captured"])
        self.assertEqual([row["fill_ratio"] for row in by_term[OLD]["sections"]], [0.9, 0.0])
        self.assertEqual([row["enrolled"] for row in by_term[LIVE]["sections"]], [0, 40])

    def test_without_the_marker_nothing_is_guessed(self) -> None:
        history = course_history(HISTORY_TST["TST 201E"])
        self.assertEqual(history["enrolment_not_captured_terms"], [])
        self.assertFalse(any(offering["enrolment_not_captured"] for offering in history["offerings"]))

    def test_who_taught_average_skips_the_uncaptured_term(self) -> None:
        ranked = {item["instructor"]: item for item in who_taught(self.entry())["instructors"]}
        # Ada: 0.8 (live) and 0.9 (old); the 0 of the uncaptured term is not a third value.
        self.assertEqual(ranked["Ada Kurgu"]["average_fill_ratio"], 0.85)
        self.assertEqual(ranked["Ada Kurgu"]["fill_ratio_section_count"], 2)
        self.assertEqual(ranked["Ada Kurgu"]["term_count"], 3)
        # Deniz's live section is genuinely empty and still counts.
        self.assertEqual(ranked["Deniz Uydurma"]["average_fill_ratio"], 0.0)
        self.assertEqual(ranked["Deniz Uydurma"]["fill_ratio_section_count"], 1)

    def test_who_taught_has_no_average_from_uncaptured_terms_alone(self) -> None:
        entry = {
            "code": "TST 900", "name": "X", "enrolment_uncaptured_terms": [EMPTY],
            "rows": [[EMPTY, "Ece Yapay", 40, 0, "Salı"]],
        }
        only = who_taught(entry)["instructors"][0]
        self.assertIsNone(only["average_fill_ratio"])
        self.assertEqual(only["section_count"], 1)


class SectionSummaryTests(unittest.TestCase):
    def test_uncaptured_section_has_no_enrolment(self) -> None:
        row = summarize_section(section("1", "TST 201E", "Ada Kurgu", 80, 0), enrolment_captured=False)
        self.assertEqual(row["capacity"], 80)
        self.assertIsNone(row["enrolled"])
        self.assertIsNone(row["fill_ratio"])

    def test_captured_empty_section_is_zero(self) -> None:
        row = summarize_section(section("1", "TST 201E", "Ada Kurgu", 80, 0))
        self.assertEqual(row["enrolled"], 0)
        self.assertEqual(row["fill_ratio"], 0.0)

    def test_term_wide_index_without_any_enrolment(self) -> None:
        found = search_sections(DOCUMENTS[f"/terms/{EMPTY}/search.json"], course_code="TST")
        self.assertTrue(found["enrolment_not_captured"])
        self.assertIsNone(found["sections"][0]["enrolled"])
        self.assertIsNone(found["sections"][0]["fill_ratio"])

    def test_term_wide_index_with_enrolment_keeps_zero(self) -> None:
        found = search_sections(DOCUMENTS[f"/terms/{LIVE}/search.json"], instructor="deniz")
        self.assertFalse(found["enrolment_not_captured"])
        self.assertEqual(found["sections"][0]["enrolled"], 0)
        self.assertEqual(found["sections"][0]["fill_ratio"], 0.0)


class DiffTests(unittest.TestCase):
    def test_uncaptured_side_keeps_capacity_but_not_enrolment(self) -> None:
        side_a = [summarize_section(section("1", "TST 201E", "Ada Kurgu", 180, 0), enrolment_captured=False)]
        side_b = [summarize_section(section("2", "TST 201E", "Ada Kurgu", 100, 60))]
        diff = diff_term_offerings(side_a, side_b, enrolment_captured_a=False)
        self.assertEqual(diff["first"]["total_capacity"], 180)
        self.assertIsNone(diff["first"]["total_enrolled"])
        self.assertIsNone(diff["first"]["average_fill_ratio"])
        self.assertTrue(diff["first"]["enrolment_not_captured"])
        self.assertFalse(diff["enrolment_comparable"])
        self.assertIsNone(diff["enrolled_delta"])
        self.assertIsNone(diff["average_fill_ratio_delta"])
        self.assertEqual(diff["capacity_delta"], -80)

    def test_two_captured_sides_are_comparable(self) -> None:
        side_a = [summarize_section(section("1", "TST 201E", "Ada Kurgu", 100, 50))]
        side_b = [summarize_section(section("2", "TST 201E", "Ada Kurgu", 100, 60))]
        diff = diff_term_offerings(side_a, side_b)
        self.assertTrue(diff["enrolment_comparable"])
        self.assertEqual(diff["enrolled_delta"], 10)
        self.assertEqual(diff["average_fill_ratio_delta"], 0.1)


class ToolTests(unittest.TestCase):
    def test_fill_rate_excludes_the_uncaptured_term_from_every_number(self) -> None:
        result = make_app().archive_fill_rate(course_code="TST 201E")
        self.assertEqual(result["enrolment_not_captured_terms"], [EMPTY])
        # 0.0 and 0.8 (live), 0.9 and 0.0 (old). The two zeros of EMPTY are gone.
        self.assertEqual(result["sections_with_quota_data"], 4)
        self.assertEqual(result["average_fill_ratio"], 0.425)
        by_term = {offering["term"]: offering for offering in result["offerings"]}
        self.assertTrue(by_term[EMPTY]["enrolment_not_captured"])
        self.assertEqual({row["enrolled"] for row in by_term[EMPTY]["sections"]}, {None})
        self.assertIn("0 değil", by_term[EMPTY]["data_quality"])
        self.assertNotIn("enrolment_note", result)

    def test_course_history_marks_the_term(self) -> None:
        result = make_app().archive_course_history("TST 305E")
        self.assertEqual(result["enrolment_not_captured_terms"], [EMPTY])
        by_term = {offering["term"]: offering for offering in result["offerings"]}
        self.assertIn("Kayıtlı sayısı", by_term[EMPTY]["data_quality"])
        self.assertNotIn("data_quality", by_term[OLD])

    def test_who_taught_tool_uses_the_branch_wide_decision(self) -> None:
        result = make_app().archive_who_taught("TST 305E")
        self.assertEqual(result["instructors"][0]["average_fill_ratio"], 0.5)
        self.assertEqual(result["enrolment_not_captured_terms"], [EMPTY])

    def test_live_term_of_a_small_branch_is_not_flagged(self) -> None:
        result = make_app().archive_fill_rate(course_code="ZRO 101")
        self.assertEqual(result["enrolment_not_captured_terms"], [])
        self.assertEqual(result["average_fill_ratio"], 0.0)
        self.assertNotIn("data_quality", result["offerings"][0])
        self.assertEqual(result["capture_basis"], "term")

    def test_entry_handed_to_the_planner_carries_the_decision(self) -> None:
        _, entry = make_app()._archive_course_entry("TST 201E")
        self.assertEqual(entry["enrolment_uncaptured_terms"], [EMPTY])
        ranked = {item["instructor"]: item for item in who_taught(entry)["instructors"]}
        self.assertEqual(ranked["Ada Kurgu"]["average_fill_ratio"], 0.85)

    def test_term_sections_of_an_uncaptured_term(self) -> None:
        result = make_app().archive_term_sections(term=EMPTY, branch="TST")
        self.assertTrue(result["enrolment_not_captured"])
        self.assertEqual({row["enrolled"] for row in result["sections"]}, {None})
        self.assertEqual({row["fill_ratio"] for row in result["sections"]}, {None})
        self.assertIn("Kayıtlı sayısı", result["data_quality"])
        # Capacity was captured in this term and is kept.
        self.assertFalse(result["capacity_not_captured"])
        self.assertEqual([row["capacity"] for row in result["sections"]], [80, 100, 60])

    def test_term_sections_falls_back_to_the_branch_file(self) -> None:
        result = make_app().archive_term_sections(term=OLD, branch="TST")
        self.assertFalse(result["enrolment_not_captured"])
        self.assertEqual([row["enrolled"] for row in result["sections"]], [90, 0])
        self.assertNotIn("data_quality", result)

    def test_term_totals_overrule_an_all_zero_branch_file(self) -> None:
        documents = dict(DOCUMENTS)
        documents[f"/terms/{LIVE}/branches/TST.json"] = [section("30001", "TST 201E", "Deniz Uydurma", 50, 0)]
        result = make_app(documents).archive_term_sections(term=LIVE, branch="TST")
        self.assertFalse(result["enrolment_not_captured"])
        self.assertEqual(result["sections"][0]["enrolled"], 0)

    def test_compare_terms_does_not_compare_enrolment_with_an_uncaptured_term(self) -> None:
        result = make_app().archive_compare_terms("TST 201E", EMPTY, LIVE)
        self.assertTrue(result["comparable"])
        self.assertEqual(result["diff"]["first"]["total_capacity"], 180)
        self.assertIsNone(result["diff"]["first"]["total_enrolled"])
        self.assertIsNone(result["diff"]["first"]["average_fill_ratio"])
        self.assertEqual(result["diff"]["second"]["total_enrolled"], 40)
        self.assertEqual(result["diff"]["section_count_delta"], 0)
        comparison = result["enrolment_comparison"]
        self.assertFalse(comparison["comparable"])
        self.assertEqual(comparison["not_captured_terms"], [EMPTY])
        self.assertIn(EMPTY, comparison["reason"])
        self.assertEqual({row["enrolled"] for row in result["sections_a"]}, {None})

    def test_compare_two_captured_terms(self) -> None:
        result = make_app().archive_compare_terms("TST 201E", OLD, LIVE)
        self.assertTrue(result["enrolment_comparison"]["comparable"])
        self.assertIsNone(result["enrolment_comparison"]["reason"])
        self.assertEqual(result["diff"]["enrolled_delta"], -50)

    def test_search_sections_tool_says_so(self) -> None:
        result = make_app().archive_search_sections(term=EMPTY, course_code="TST")
        self.assertTrue(result["enrolment_not_captured"])
        self.assertIn("Kayıtlı sayısı", result["data_quality"])
        self.assertIsNone(result["sections"][0]["enrolled"])


if __name__ == "__main__":
    unittest.main()
