"""Whether a term's capacity and enrolment were captured is a term-wide question.

Two gaps are covered here. A small branch whose sections were all empty in a
past term is not a term without enrolment when the term as a whole has
students. And a dump that holds 0 for every capacity never captured the
capacities: that 0 is a placeholder, not a section without seats. The fixtures
are synthetic; every name is invented.
"""

from __future__ import annotations

import unittest
from typing import Any

from test_archive_datasets import BASE, FakeArchiveSession

from ninova_mcp import app_archive
from ninova_mcp.archive_client import ItuArchiveClient
from ninova_mcp.archive_tools import ARCHIVE_DATASET_TOOLS
from ninova_mcp.server import NinovaMcpApp
from ninova_mcp.tool_metadata.archive import ARCHIVE_HISTORY_TOOLS

LIVE = "2026-2027-guz"
EMPTY = "2025-2026-guz"      # capacity captured, enrolment not
QUIET = "2024-2025-bahar"    # everything captured; one small branch is empty
NOSTATS = "2023-2024-guz"    # meta.json without term-wide totals
BLANK = "2022-2023-guz"      # neither capacity nor enrolment captured
BLANK2 = "2021-2022-guz"     # the same, one year earlier

TERMS = (LIVE, EMPTY, QUIET, NOSTATS, BLANK, BLANK2)

INDEX = {
    "currentTerm": "2026-2027 Güz Dönemi",
    "currentSlug": LIVE,
    "stats": {"sections": 4, "capacity": 1000, "enrolled": 500},
    "terms": [
        {"slug": slug, "label": slug, "source": "itu-archive", "sections": 3, **({"live": True} if slug == LIVE else {})}
        for slug in TERMS
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
            [QUIET, "Ada Kurgu", 100, 90, "Cuma"],
            # An extra-exam row: a section that really has no seats.
            [QUIET, "Mert Sahte", 0, 0, "Salı"],
            [BLANK, "Ada Kurgu", 0, 0, "Cuma"],
            [BLANK, "Mert Sahte", 0, 0, "Salı"],
        ],
    },
    # Offered only in the two terms where nothing was captured.
    "TST 900": {
        "code": "TST 900",
        "name": "Sample Seminar",
        "rows": [
            [BLANK, "Ece Yapay", 0, 0, "Salı"],
            [BLANK, "-", 0, 0, "Cuma"],
            [BLANK2, "Ece Yapay", 0, 0, "Salı"],
        ],
    },
}

# A small branch: every section of QUIET, EMPTY and LIVE has 0 enrolled.
HISTORY_ZRO = {
    "ZRO 101": {
        "code": "ZRO 101",
        "name": "Küçük Ders",
        "rows": [
            [LIVE, "Ece Yapay", 10, 0, "Çarşamba"],
            [EMPTY, "Ece Yapay", 10, 0, "Çarşamba"],
            [QUIET, "Ece Yapay", 10, 0, "Çarşamba"],
            [QUIET, "Kaan Örnek", 15, 0, "Cuma"],
            [NOSTATS, "Ece Yapay", 10, 0, "Çarşamba"],
        ],
    },
}

# A branch whose only section of a captured past term has 0 capacity and 0 enrolled.
HISTORY_ARK = {
    "ARK 210E": {
        "code": "ARK 210E",
        "name": "Sample Studio",
        "rows": [[QUIET, "Kaan Örnek", 0, 0, "Cuma"], [LIVE, "Kaan Örnek", 30, 4, "Cuma"]],
    },
}


def section(crn: str, code: str, instructor: str, capacity: int, enrolled: int) -> dict[str, Any]:
    return {
        "crn": crn, "code": code, "branch": code.split()[0], "level": "LS", "name": "Sample",
        "method": "", "instructor": instructor, "buildings": ["AAA"], "days": ["Salı"],
        "times": ["08:30/10:29"], "rooms": ["1"], "capacity": capacity, "enrolled": enrolled,
        "reserved": "", "programs": ["TST_LS"], "prereq": "", "classReq": "",
    }


def meta(slug: str, capacity: int | None, enrolled: int | None) -> dict[str, Any]:
    document: dict[str, Any] = {
        "slug": slug, "sections": 3, "courses": 2,
        "branches": [{"code": "TST", "levels": ["LS"], "sections": 3}],
    }
    if capacity is not None:
        document["stats"] = {"sections": 3, "capacity": capacity, "enrolled": enrolled}
    return document


def search_row(crn: str, code: str, instructor: str, capacity: int, enrolled: int) -> list[Any]:
    return [crn, code, "Sample", code.split()[0], instructor, "Salı 08:30/10:29", capacity, enrolled,
            "LS", "", ["TST_LS"]]


DOCUMENTS: dict[str, Any] = {
    "/index.json": INDEX,
    "/history/courses/TST.json": HISTORY_TST,
    "/history/courses/ZRO.json": HISTORY_ZRO,
    "/history/courses/ARK.json": HISTORY_ARK,
    f"/terms/{LIVE}/meta.json": meta(LIVE, 1000, 500),
    f"/terms/{EMPTY}/meta.json": meta(EMPTY, 240, 0),
    f"/terms/{QUIET}/meta.json": meta(QUIET, 900, 400),
    f"/terms/{NOSTATS}/meta.json": meta(NOSTATS, None, None),
    f"/terms/{BLANK}/meta.json": meta(BLANK, 0, 0),
    f"/terms/{BLANK2}/meta.json": meta(BLANK2, 0, 0),
    f"/terms/{LIVE}/branches/TST.json": [
        section("30001", "TST 201E", "Deniz Uydurma", 50, 0),
        section("30002", "TST 201E", "Ada Kurgu", 50, 40),
    ],
    f"/terms/{EMPTY}/branches/TST.json": [
        section("20001", "TST 201E", "Deniz Uydurma", 80, 0),
        section("20002", "TST 201E", "Ada Kurgu", 100, 0),
    ],
    f"/terms/{QUIET}/branches/TST.json": [
        section("15001", "TST 201E", "Ada Kurgu", 100, 90),
        section("15002", "TST 201E", "Mert Sahte", 0, 0),
    ],
    f"/terms/{BLANK}/branches/TST.json": [
        section("10001", "TST 201E", "Ada Kurgu", 0, 0),
        section("10002", "TST 201E", "Mert Sahte", 0, 0),
        section("10003", "TST 900", "Ece Yapay", 0, 0),
        section("10004", "TST 900", "-", 0, 0),
    ],
    f"/terms/{BLANK2}/branches/TST.json": [section("90001", "TST 900", "Ece Yapay", 0, 0)],
    f"/terms/{BLANK}/search.json": [
        search_row("10001", "TST 201E", "Ada Kurgu", 0, 0),
        search_row("10003", "TST 900", "Ece Yapay", 0, 0),
        search_row("10009", "ABC 101", "Kaan Örnek", 0, 0),
    ],
    f"/terms/{QUIET}/search.json": [
        search_row("15001", "TST 201E", "Ada Kurgu", 100, 90),
        search_row("15002", "TST 201E", "Mert Sahte", 0, 0),
    ],
}


def make_app(documents: dict[str, Any] | None = None) -> tuple[NinovaMcpApp, FakeArchiveSession]:
    session = FakeArchiveSession(DOCUMENTS if documents is None else documents)
    client = ItuArchiveClient(session=session, base_url=BASE, cache_ttl=60)  # type: ignore[arg-type]
    client._min_request_interval = 0
    app = NinovaMcpApp()
    app._archive = client
    return app, session


def meta_requests(session: FakeArchiveSession) -> list[str]:
    return [path for path in session.requested if path.endswith("/meta.json")]


def offerings(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {offering["term"]: offering for offering in result["offerings"]}


class TermWideEnrolmentDecisionTests(unittest.TestCase):
    """Item 1: a past term is judged by the whole term, not by one branch file."""

    def test_empty_small_branch_of_a_captured_past_term_is_not_flagged(self) -> None:
        app, _ = make_app()
        result = app.archive_fill_rate(course_code="ZRO 101")
        self.assertNotIn(QUIET, result["enrolment_not_captured_terms"])
        quiet = offerings(result)[QUIET]
        self.assertFalse(quiet["enrolment_not_captured"])
        self.assertEqual([row["enrolled"] for row in quiet["sections"]], [0, 0])
        self.assertEqual([row["fill_ratio"] for row in quiet["sections"]], [0.0, 0.0])

    def test_the_genuine_zeros_count_in_the_average(self) -> None:
        app, _ = make_app()
        result = app.archive_who_taught("ZRO 101")
        ranked = {item["instructor"]: item for item in result["instructors"]}
        # Kaan taught only in QUIET, where his section really was empty.
        self.assertEqual(ranked["Kaan Örnek"]["average_fill_ratio"], 0.0)
        self.assertEqual(ranked["Kaan Örnek"]["fill_ratio_section_count"], 1)

    def test_a_term_without_enrolment_anywhere_is_still_flagged(self) -> None:
        app, _ = make_app()
        result = app.archive_course_history("ZRO 101")
        self.assertIn(EMPTY, result["enrolment_not_captured_terms"])
        self.assertTrue(offerings(result)[EMPTY]["enrolment_not_captured"])
        self.assertIsNone(offerings(result)[EMPTY]["sections"][0]["enrolled"])

    def test_basis_is_reported_and_falls_back_without_term_totals(self) -> None:
        app, _ = make_app()
        result = app.archive_course_history("ZRO 101")
        # NOSTATS has a meta.json without totals: only the branch file can decide.
        self.assertEqual(result["capture_basis"], "branch")
        self.assertEqual(result["capture_basis_branch_terms"], [NOSTATS])
        self.assertIn(NOSTATS, result["enrolment_not_captured_terms"])
        self.assertIn(NOSTATS, result["capture_basis_note"])

    def test_basis_is_term_when_every_decision_was_term_wide(self) -> None:
        app, _ = make_app()
        result = app.archive_course_history("TST 201E")
        self.assertEqual(result["capture_basis"], "term")
        self.assertNotIn("capture_basis_branch_terms", result)
        self.assertNotIn("capture_basis_note", result)

    def test_only_all_zero_terms_of_the_course_cost_a_read_and_only_once(self) -> None:
        app, session = make_app()
        app.archive_course_history("TST 201E")
        # QUIET has enrolment in the branch file and LIVE is settled by the index.
        self.assertEqual(
            sorted(meta_requests(session)),
            sorted(f"/terms/{term}/meta.json" for term in (EMPTY, BLANK)),
        )
        app.archive_who_taught("TST 201E")
        app.archive_fill_rate(course_code="TST 201E")
        self.assertEqual(len(meta_requests(session)), 2)

    def test_terms_the_course_never_ran_in_are_not_read(self) -> None:
        app, session = make_app()
        # The branch file is all zero in BLANK2 too, but TST 201E has no row there.
        app.archive_course_history("TST 201E")
        self.assertNotIn(f"/terms/{BLANK2}/meta.json", session.requested)

    def test_a_failed_read_falls_back_to_the_branch_rule_and_is_not_repeated(self) -> None:
        documents = {key: value for key, value in DOCUMENTS.items() if key != f"/terms/{QUIET}/meta.json"}
        app, session = make_app(documents)
        result = app.archive_fill_rate(course_code="ZRO 101")
        self.assertIn(QUIET, result["enrolment_not_captured_terms"])
        self.assertEqual(result["capture_basis"], "branch")
        self.assertIn(QUIET, result["capture_basis_branch_terms"])
        app.archive_fill_rate(course_code="ZRO 101")
        self.assertEqual(session.requested.count(f"/terms/{QUIET}/meta.json"), 1)

    def test_extra_reads_per_call_are_capped(self) -> None:
        cap = getattr(app_archive, "MAX_CAPTURE_META_READS", 4)
        terms = [f"{year}-{year + 1}-guz" for year in range(2000, 2000 + cap + 2)]
        documents: dict[str, Any] = {
            "/index.json": {
                "currentSlug": LIVE, "stats": INDEX["stats"],
                "terms": [{"slug": slug, "label": slug, "source": "itu-archive"} for slug in (LIVE, *terms)],
            },
            "/history/courses/MNY.json": {
                "MNY 101": {
                    "code": "MNY 101", "name": "Çok Dönemli Ders",
                    "rows": [[term, "Ece Yapay", 10, 0, "Salı"] for term in terms],
                },
            },
        }
        for term in terms:
            documents[f"/terms/{term}/meta.json"] = meta(term, 900, 400)
        app, session = make_app(documents)

        first = app.archive_course_history("MNY 101", limit_terms=30)
        self.assertEqual(len(meta_requests(session)), cap)
        # The newest terms are settled first; the two oldest fall back.
        self.assertEqual(first["capture_basis"], "branch")
        self.assertEqual(sorted(first["capture_basis_branch_terms"]), terms[:2])
        self.assertEqual(sorted(first["enrolment_not_captured_terms"]), terms[:2])

        second = app.archive_course_history("MNY 101", limit_terms=30)
        self.assertEqual(len(meta_requests(session)), cap + 2)
        self.assertEqual(second["capture_basis"], "term")
        self.assertEqual(second["enrolment_not_captured_terms"], [])


class CapacityNotCapturedTests(unittest.TestCase):
    """Item 2: capacity 0 everywhere in a term is a placeholder, not a measurement."""

    def test_history_reports_no_capacity_for_the_blank_term(self) -> None:
        app, _ = make_app()
        result = app.archive_course_history("TST 201E")
        blank = offerings(result)[BLANK]
        self.assertTrue(blank["capacity_not_captured"])
        self.assertTrue(blank["enrolment_not_captured"])
        for row in blank["sections"]:
            self.assertIsNone(row["capacity"])
            self.assertIsNone(row["enrolled"])
            self.assertIsNone(row["fill_ratio"])
        self.assertEqual(result["capacity_not_captured_terms"], [BLANK])
        self.assertEqual(result["enrolment_not_captured_terms"], [EMPTY, BLANK])

    def test_one_short_data_quality_note_per_term(self) -> None:
        app, _ = make_app()
        by_term = offerings(app.archive_course_history("TST 201E"))
        self.assertIn("Kontenjan ve kayıtlı", by_term[BLANK]["data_quality"])
        self.assertIn("Kayıtlı sayısı", by_term[EMPTY]["data_quality"])
        self.assertNotIn("Kontenjan", by_term[EMPTY]["data_quality"])
        self.assertLess(len(by_term[BLANK]["data_quality"]), 90)
        self.assertNotIn("data_quality", by_term[QUIET])
        self.assertNotIn("data_quality", by_term[LIVE])

    def test_capacity_captured_but_enrolment_not_keeps_its_capacity(self) -> None:
        app, _ = make_app()
        empty = offerings(app.archive_course_history("TST 201E"))[EMPTY]
        self.assertFalse(empty["capacity_not_captured"])
        self.assertTrue(empty["enrolment_not_captured"])
        self.assertEqual([row["capacity"] for row in empty["sections"]], [80, 100])

    def test_a_single_zero_capacity_section_in_a_captured_term_stays_zero(self) -> None:
        app, _ = make_app()
        quiet = offerings(app.archive_course_history("TST 201E"))[QUIET]
        self.assertFalse(quiet["capacity_not_captured"])
        self.assertEqual([row["capacity"] for row in quiet["sections"]], [100, 0])
        self.assertEqual([row["enrolled"] for row in quiet["sections"]], [90, 0])
        # No seats: a ratio cannot be formed, and is not reported as 0.
        self.assertEqual([row["fill_ratio"] for row in quiet["sections"]], [0.9, None])

    def test_lone_zero_capacity_section_of_a_small_branch_is_judged_term_wide(self) -> None:
        app, _ = make_app()
        result = app.archive_course_history("ARK 210E")
        quiet = offerings(result)[QUIET]
        self.assertFalse(quiet["capacity_not_captured"])
        self.assertFalse(quiet["enrolment_not_captured"])
        self.assertEqual(quiet["sections"][0]["capacity"], 0)
        self.assertEqual(quiet["sections"][0]["enrolled"], 0)
        self.assertEqual(result["capacity_not_captured_terms"], [])
        self.assertEqual(result["capture_basis"], "term")

    def test_fill_rate_leaves_the_blank_term_out_of_every_number(self) -> None:
        app, _ = make_app()
        result = app.archive_fill_rate(course_code="TST 201E")
        # LIVE 0.0 and 0.8, QUIET 0.9. EMPTY and BLANK add nothing.
        self.assertEqual(result["sections_with_quota_data"], 3)
        self.assertEqual(result["average_fill_ratio"], round((0.0 + 0.8 + 0.9) / 3, 3))
        self.assertEqual(result["capacity_not_captured_terms"], [BLANK])
        self.assertEqual({row["capacity"] for row in offerings(result)[BLANK]["sections"]}, {None})

    def test_fill_rate_term_filter_keeps_the_flags_of_that_term_only(self) -> None:
        app, _ = make_app()
        result = app.archive_fill_rate(course_code="TST 201E", term=BLANK)
        self.assertEqual(result["capacity_not_captured_terms"], [BLANK])
        self.assertEqual(result["enrolment_not_captured_terms"], [BLANK])
        self.assertIsNone(result["average_fill_ratio"])
        self.assertIsNone(result["max_fill_ratio"])
        self.assertIsNone(result["always_fills"])

    def test_who_taught_names_the_terms(self) -> None:
        app, _ = make_app()
        result = app.archive_who_taught("TST 201E")
        self.assertEqual(result["capacity_not_captured_terms"], [BLANK])
        ranked = {item["instructor"]: item for item in result["instructors"]}
        # Ada: 0.8 (LIVE) and 0.9 (QUIET) only.
        self.assertEqual(ranked["Ada Kurgu"]["average_fill_ratio"], 0.85)
        self.assertEqual(ranked["Ada Kurgu"]["fill_ratio_section_count"], 2)
        # Mert: QUIET has no seats, BLANK was not captured. No ratio at all.
        self.assertIsNone(ranked["Mert Sahte"]["average_fill_ratio"])

    def test_term_sections_of_a_blank_term(self) -> None:
        app, _ = make_app()
        result = app.archive_term_sections(term=BLANK, branch="TST")
        self.assertTrue(result["capacity_not_captured"])
        self.assertTrue(result["enrolment_not_captured"])
        self.assertEqual({row["capacity"] for row in result["sections"]}, {None})
        self.assertEqual({row["fill_ratio"] for row in result["sections"]}, {None})
        self.assertIn("Kontenjan ve kayıtlı", result["data_quality"])

    def test_term_sections_of_a_captured_term_keep_a_real_zero(self) -> None:
        app, _ = make_app()
        result = app.archive_term_sections(term=QUIET, branch="TST")
        self.assertFalse(result["capacity_not_captured"])
        self.assertEqual([row["capacity"] for row in result["sections"]], [100, 0])
        self.assertNotIn("data_quality", result)

    def test_search_sections_of_a_blank_term(self) -> None:
        app, _ = make_app()
        result = app.archive_search_sections(term=BLANK, course_code="TST")
        self.assertTrue(result["capacity_not_captured"])
        self.assertTrue(result["enrolment_not_captured"])
        self.assertEqual({row["capacity"] for row in result["sections"]}, {None})
        self.assertIn("Kontenjan ve kayıtlı", result["data_quality"])

    def test_search_sections_of_a_captured_term_keep_a_real_zero(self) -> None:
        app, _ = make_app()
        result = app.archive_search_sections(term=QUIET, instructor="mert")
        self.assertFalse(result["capacity_not_captured"])
        self.assertEqual(result["sections"][0]["capacity"], 0)
        self.assertNotIn("data_quality", result)

    def test_compare_terms_does_not_compare_capacity_with_a_blank_term(self) -> None:
        app, _ = make_app()
        result = app.archive_compare_terms("TST 201E", BLANK, QUIET)
        first, second = result["diff"]["first"], result["diff"]["second"]
        self.assertIsNone(first["total_capacity"])
        self.assertTrue(first["capacity_not_captured"])
        self.assertTrue(first["enrolment_not_captured"])
        self.assertIn("Kontenjan ve kayıtlı", first["data_quality"])
        self.assertEqual(second["total_capacity"], 100)
        self.assertFalse(second["capacity_not_captured"])
        self.assertNotIn("data_quality", second)
        self.assertIsNone(result["diff"]["capacity_delta"])
        comparison = result["capacity_comparison"]
        self.assertFalse(comparison["comparable"])
        self.assertEqual(comparison["not_captured_terms"], [BLANK])
        self.assertIn(BLANK, comparison["reason"])
        # Sections and instructors are still compared.
        self.assertEqual(result["diff"]["section_count_delta"], 0)
        self.assertEqual(result["diff"]["instructors_added"], [])
        self.assertEqual(first["instructors"], ["Ada Kurgu", "Mert Sahte"])
        self.assertEqual({row["capacity"] for row in result["sections_a"]}, {None})

    def test_compare_two_captured_terms_compares_capacity(self) -> None:
        app, _ = make_app()
        result = app.archive_compare_terms("TST 201E", QUIET, LIVE)
        self.assertTrue(result["capacity_comparison"]["comparable"])
        self.assertIsNone(result["capacity_comparison"]["reason"])
        self.assertEqual(result["diff"]["capacity_delta"], 0)

    def test_compare_with_enrolment_only_missing_still_compares_capacity(self) -> None:
        app, _ = make_app()
        result = app.archive_compare_terms("TST 201E", EMPTY, LIVE)
        self.assertTrue(result["capacity_comparison"]["comparable"])
        self.assertEqual(result["diff"]["capacity_delta"], -80)
        self.assertFalse(result["enrolment_comparison"]["comparable"])
        self.assertFalse(result["diff"]["first"]["capacity_not_captured"])

    def test_descriptions_explain_the_flag(self) -> None:
        described = {tool["name"]: tool["description"] for tool in (*ARCHIVE_HISTORY_TOOLS, *ARCHIVE_DATASET_TOOLS)}
        for name in (
            "archive_course_history", "archive_who_taught", "archive_fill_rate",
            "archive_term_sections", "archive_compare_terms", "archive_search_sections",
        ):
            self.assertIn("capacity_not_captured", described[name], name)
            self.assertIn("enrolment_not_captured", described[name], name)
        for name in ("archive_course_history", "archive_who_taught", "archive_fill_rate"):
            self.assertIn("capture_basis", described[name], name)


# Counts the tools work out themselves. A 0 here is a result, never a source cell.
COMPUTED_COUNTS = frozenset({
    "sections_with_quota_data",    # how many sections had a usable ratio
    "fill_ratio_section_count",    # the same, per instructor
    "summer_terms",                # seasonality: number of Yaz records
    "unannounced_section_count",   # sections without an announced instructor
})


def numeric_fields(value: Any, path: str = "") -> list[tuple[str, Any]]:
    """Every int or float in a result, with the path that leads to it."""
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [(path, value)]
    if isinstance(value, dict):
        return [item for key, child in value.items() for item in numeric_fields(child, f"{path}.{key}")]
    if isinstance(value, list):
        return [item for index, child in enumerate(value) for item in numeric_fields(child, f"{path}[{index}]")]
    return []


class NothingCapturedTests(unittest.TestCase):
    """A term with only placeholders must not put a single 0 into a result."""

    def results(self) -> dict[str, dict[str, Any]]:
        app, _ = make_app()
        return {
            "archive_fill_rate": app.archive_fill_rate(course_code="TST 900"),
            "archive_who_taught": app.archive_who_taught("TST 900"),
            "archive_course_history": app.archive_course_history("TST 900"),
            "archive_compare_terms": app.archive_compare_terms("TST 900", BLANK2, BLANK),
            "archive_term_sections": app.archive_term_sections(term=BLANK, branch="TST"),
            "archive_search_sections": app.archive_search_sections(term=BLANK, course_code="TST"),
        }

    def test_no_zero_where_the_source_held_only_placeholders(self) -> None:
        for name, result in self.results().items():
            found = numeric_fields(result)
            self.assertTrue(found, name)
            zeros = [
                path for path, number in found
                if number == 0 and path.rsplit(".", 1)[-1] not in COMPUTED_COUNTS
            ]
            self.assertEqual(zeros, [], name)

    def test_every_count_ratio_and_average_is_empty(self) -> None:
        results = self.results()
        fill = results["archive_fill_rate"]
        self.assertIsNone(fill["average_fill_ratio"])
        self.assertIsNone(fill["max_fill_ratio"])
        self.assertIsNone(fill["always_fills"])
        self.assertEqual(fill["sections_with_quota_data"], 0)
        for offering in fill["offerings"]:
            for row in offering["sections"]:
                self.assertEqual((row["capacity"], row["enrolled"], row["fill_ratio"]), (None, None, None))

        taught = results["archive_who_taught"]["instructors"][0]
        self.assertIsNone(taught["average_fill_ratio"])
        self.assertEqual(taught["section_count"], 2)

        diff = results["archive_compare_terms"]["diff"]
        for side in (diff["first"], diff["second"]):
            self.assertIsNone(side["total_capacity"])
            self.assertIsNone(side["total_enrolled"])
            self.assertIsNone(side["average_fill_ratio"])
        for key in ("capacity_delta", "enrolled_delta", "average_fill_ratio_delta"):
            self.assertIsNone(diff[key])
        self.assertEqual(diff["section_count_delta"], 1)
        self.assertEqual(results["archive_compare_terms"]["capacity_comparison"]["not_captured_terms"], [BLANK2, BLANK])

        for name in ("archive_term_sections", "archive_search_sections"):
            for row in results[name]["sections"]:
                self.assertEqual((row["capacity"], row["enrolled"], row["fill_ratio"]), (None, None, None), name)


class ListTermsFlagTests(unittest.TestCase):
    def test_live_term_gets_its_flags_from_the_index_totals(self) -> None:
        app, session = make_app()
        by_slug = {term["slug"]: term for term in app.archive_list_terms()["terms"]}
        self.assertIs(by_slug[LIVE]["enrolment_not_captured"], False)
        self.assertIs(by_slug[LIVE]["capacity_not_captured"], False)
        # The index holds no totals for the other terms: nothing is claimed, nothing is fetched.
        self.assertNotIn("enrolment_not_captured", by_slug[BLANK])
        self.assertNotIn("capacity_not_captured", by_slug[BLANK])
        self.assertEqual(session.requested, ["/index.json"])

    def test_per_term_totals_in_the_index_are_used_when_present(self) -> None:
        documents = dict(DOCUMENTS)
        documents["/index.json"] = {
            **INDEX,
            "terms": [
                {**term, **({"stats": {"capacity": 0, "enrolled": 0}} if term["slug"] == BLANK else {}),
                 **({"stats": {"capacity": 240, "enrolled": 0}} if term["slug"] == EMPTY else {})}
                for term in INDEX["terms"]
            ],
        }
        app, session = make_app(documents)
        by_slug = {term["slug"]: term for term in app.archive_list_terms()["terms"]}
        self.assertIs(by_slug[BLANK]["capacity_not_captured"], True)
        self.assertIs(by_slug[BLANK]["enrolment_not_captured"], True)
        self.assertIs(by_slug[EMPTY]["capacity_not_captured"], False)
        self.assertIs(by_slug[EMPTY]["enrolment_not_captured"], True)
        self.assertEqual(session.requested, ["/index.json"])
        # The history tools use the same totals instead of reading meta.json.
        app.archive_course_history("TST 201E")
        self.assertEqual(meta_requests(session), [])


if __name__ == "__main__":
    unittest.main()
