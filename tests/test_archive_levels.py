"""Two terms that cover different study levels are not compared as equals.

The live term holds undergraduate and graduate sections; the historical dumps
that were checked hold the undergraduate level only. A term comparison has to
say so and compare like with like. Synthetic fixtures; invented names.
"""

from __future__ import annotations

import unittest
from typing import Any

from test_archive_datasets import BASE, FakeArchiveSession

from ninova_mcp.archive import section_count_outliers, term_levels
from ninova_mcp.archive_client import ItuArchiveClient
from ninova_mcp.server import NinovaMcpApp

LIVE = "2026-2027-guz"
OLD = "2025-2026-guz"
OLDER = "2023-2024-guz"

INDEX = {
    "currentTerm": "2026-2027 Güz Dönemi",
    "currentSlug": LIVE,
    "stats": {"capacity": 900, "enrolled": 400},
    "terms": [
        {"slug": LIVE, "label": "2026-2027 Güz Dönemi", "source": "itu-archive", "live": True, "sections": 5500},
        {"slug": "2025-2026-yaz", "label": "2025-2026 Yaz Dönemi", "source": "itu-archive", "sections": 281},
        {"slug": "2025-2026-bahar", "label": "2025-2026 Bahar Dönemi", "source": "itu-archive", "sections": 3337},
        {"slug": OLD, "label": "2025-2026 Güz Dönemi", "source": "itu-archive", "sections": 2976},
        {"slug": "2024-2025-bahar", "label": "2024-2025 Bahar Dönemi", "source": "itu-archive", "sections": 3463},
        {"slug": "2024-2025-guz", "label": "2024-2025 Güz Dönemi", "sections": 0, "missing": True},
        {"slug": "2023-2024-yaz", "label": "2023-2024 Yaz Dönemi", "source": "itu-archive", "sections": 1099},
        {"slug": OLDER, "label": "2023-2024 Güz Dönemi", "source": "itu-archive", "sections": 3374},
        {"slug": "2022-2023-guz", "label": "2022-2023 Güz Dönemi", "source": "itu-archive", "sections": 3291},
    ],
}


def section(crn: str, code: str, level: str, instructor: str, capacity: int, enrolled: int) -> dict[str, Any]:
    return {
        "crn": crn, "code": code, "branch": "TST", "level": level, "name": "Sample", "method": "",
        "instructor": instructor, "buildings": [], "days": [], "times": [], "rooms": [],
        "capacity": capacity, "enrolled": enrolled, "reserved": "", "programs": [], "prereq": "", "classReq": "",
    }


LIVE_META = {
    "slug": LIVE, "sections": 5500, "courses": 3920, "stats": {"capacity": 900, "enrolled": 400},
    "branches": [
        {"code": "TST", "levels": ["LS", "LU"], "sections": 4},
        {"code": "ABC", "levels": ["LUI", "OL"], "sections": 2},
    ],
}
OLD_META = {
    "slug": OLD, "sections": 2976, "courses": 1862, "stats": {"capacity": 500, "enrolled": 300},
    "branches": [{"code": "TST", "levels": ["LS"], "sections": 2}, {"code": "ABC", "levels": ["LS"], "sections": 1}],
}
# An even older dump whose meta records no levels at all.
OLDER_META = {"slug": OLDER, "sections": 3374, "branches": [{"code": "TST", "sections": 1}]}

DOCUMENTS: dict[str, Any] = {
    "/index.json": INDEX,
    f"/terms/{LIVE}/meta.json": LIVE_META,
    f"/terms/{LIVE}/branches/TST.json": [
        section("30001", "TST 201E", "LS", "Ada Kurgu", 100, 80),
        section("30002", "TST 201E", "LS", "Mert Sahte", 100, 60),
        section("30003", "TST 501E", "LU", "Ece Yapay", 30, 20),
        section("30004", "TST 301E", "LS", "Ada Kurgu", 50, 25),
        section("30005", "TST 301E", "LU", "Ece Yapay", 20, 10),
    ],
    f"/terms/{OLD}/meta.json": OLD_META,
    f"/terms/{OLD}/branches/TST.json": [
        section("20001", "TST 201E", "LS", "Ada Kurgu", 100, 90),
        section("20002", "TST 301E", "LS", "Ada Kurgu", 50, 50),
    ],
    f"/terms/{OLDER}/meta.json": OLDER_META,
    f"/terms/{OLDER}/branches/TST.json": [
        {key: value for key, value in section("10001", "TST 201E", "", "Ada Kurgu", 100, 70).items() if key != "level"},
    ],
}


def make_app() -> NinovaMcpApp:
    client = ItuArchiveClient(session=FakeArchiveSession(DOCUMENTS), base_url=BASE, cache_ttl=60)  # type: ignore[arg-type]
    client._min_request_interval = 0
    app = NinovaMcpApp()
    app._archive = client
    return app


class TermLevelsTests(unittest.TestCase):
    def test_levels_come_from_the_term_meta(self) -> None:
        self.assertEqual(term_levels(LIVE_META, []), ["LS", "LU", "LUI", "OL"])
        self.assertEqual(term_levels(OLD_META, []), ["LS"])

    def test_sections_are_the_fallback(self) -> None:
        sections = DOCUMENTS[f"/terms/{LIVE}/branches/TST.json"]
        self.assertEqual(term_levels({"branches": [{"code": "TST"}]}, sections), ["LS", "LU"])

    def test_unrecorded_levels_are_an_empty_list(self) -> None:
        self.assertEqual(term_levels(OLDER_META, DOCUMENTS[f"/terms/{OLDER}/branches/TST.json"]), [])
        self.assertEqual(term_levels({}, []), [])


class CompareTermsTests(unittest.TestCase):
    def test_different_coverage_is_flagged_with_the_levels_of_each_term(self) -> None:
        result = make_app().archive_compare_terms("TST 201E", OLD, LIVE)
        self.assertTrue(result["coverage_differs"])
        self.assertEqual(result["levels"], {"term_a": ["LS"], "term_b": ["LS", "LU", "LUI", "OL"]})
        self.assertEqual(result["compared_levels"], ["LS"])
        self.assertIn("LS", result["coverage_note"])
        # The course is undergraduate in both terms: it stays comparable.
        self.assertTrue(result["comparable"])
        self.assertEqual(result["diff"]["section_count_delta"], 1)
        self.assertEqual(result["excluded_sections"], {"term_a": 0, "term_b": 0})

    def test_only_the_shared_level_is_compared(self) -> None:
        result = make_app().archive_compare_terms("TST 301E", OLD, LIVE)
        self.assertTrue(result["comparable"])
        self.assertEqual(result["excluded_sections"], {"term_a": 0, "term_b": 1})
        self.assertEqual(result["diff"]["second"]["section_count"], 1)
        self.assertEqual(result["diff"]["second"]["total_capacity"], 50)
        self.assertEqual(result["diff"]["section_count_delta"], 0)
        self.assertEqual(result["diff"]["instructors_added"], [])
        # Nothing is hidden: the left-out section is still listed, and marked.
        self.assertEqual(len(result["sections_b"]), 2)
        self.assertEqual([row["compared"] for row in result["sections_b"]], [True, False])

    def test_course_at_a_level_the_other_term_never_recorded_is_not_comparable(self) -> None:
        result = make_app().archive_compare_terms("TST 501E", OLD, LIVE)
        self.assertFalse(result["comparable"])
        self.assertEqual(result["excluded_sections"], {"term_a": 0, "term_b": 1})
        self.assertIn("LU", result["note"])
        self.assertIn(OLD, result["note"])
        self.assertIn("söylenemez", result["note"])
        self.assertEqual(result["diff"]["second"]["section_count"], 0)

    def test_same_coverage_is_not_flagged(self) -> None:
        result = make_app().archive_compare_terms("TST 201E", LIVE, LIVE)
        self.assertFalse(result["coverage_differs"])
        self.assertNotIn("coverage_note", result)
        self.assertNotIn("compared", result["sections_a"][0])

    def test_unrecorded_levels_give_only_a_cautious_note(self) -> None:
        result = make_app().archive_compare_terms("TST 201E", OLDER, LIVE)
        self.assertIsNone(result["coverage_differs"])
        self.assertEqual(result["levels"], {"term_a": [], "term_b": ["LS", "LU", "LUI", "OL"]})
        self.assertIn("düzey bilgisi kayıtlı değil", result["coverage_note"])
        self.assertIn("5500", result["coverage_note"])
        self.assertTrue(result["comparable"])
        self.assertEqual(result["diff"]["second"]["section_count"], 2)


class ListTests(unittest.TestCase):
    def test_list_branches_reports_the_levels_of_the_term(self) -> None:
        app = make_app()
        self.assertEqual(app.archive_list_branches(LIVE)["levels"], ["LS", "LU", "LUI", "OL"])
        self.assertEqual(app.archive_list_branches(OLD)["levels"], ["LS"])
        self.assertEqual(app.archive_list_branches(OLDER)["levels"], [])

    def test_outliers_are_measured_against_the_same_season(self) -> None:
        outliers = section_count_outliers(INDEX["terms"])
        self.assertEqual([item["term"] for item in outliers], [LIVE])
        self.assertEqual(outliers[0]["sections"], 5500)
        self.assertEqual(outliers[0]["same_season_median"], 3291)
        self.assertEqual(outliers[0]["ratio"], 1.67)

    def test_summer_terms_and_missing_terms_are_left_out(self) -> None:
        terms = [term["term"] for term in section_count_outliers(INDEX["terms"])]
        self.assertNotIn("2023-2024-yaz", terms)
        self.assertNotIn("2024-2025-guz", terms)

    def test_list_terms_warns_that_section_counts_are_not_comparable(self) -> None:
        result = make_app().archive_list_terms()
        self.assertEqual([item["term"] for item in result["coverage_may_differ"]], [LIVE])
        self.assertIn("düzey", result["coverage_note"])
        self.assertIn("archive_list_branches", result["coverage_note"])

    def test_list_terms_without_an_outlier_says_nothing(self) -> None:
        documents = dict(DOCUMENTS)
        documents["/index.json"] = dict(INDEX, terms=[t for t in INDEX["terms"] if t["slug"] != LIVE])
        client = ItuArchiveClient(session=FakeArchiveSession(documents), base_url=BASE, cache_ttl=60)  # type: ignore[arg-type]
        client._min_request_interval = 0
        app = NinovaMcpApp()
        app._archive = client
        result = app.archive_list_terms()
        self.assertEqual(result["coverage_may_differ"], [])
        self.assertNotIn("coverage_note", result)


if __name__ == "__main__":
    unittest.main()
