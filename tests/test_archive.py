from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from ninova_mcp.archive import (
    course_history,
    diff_term_offerings,
    fill_summary,
    instructor_courses,
    normalize_course_code,
    recommend_course_timing,
    search_courses,
    seasonality,
    split_course_code,
    summarize_section,
    who_taught,
)
from ninova_mcp.archive_client import ItuArchiveClient, ItuArchiveError
from ninova_mcp.graduation import summarize_graduation_plan
from ninova_mcp.prerequisites import (
    evaluate_tree,
    extract_branch_prerequisites,
    parse_prerequisite_expression,
)
from ninova_mcp.server import LOCAL_TOOL_NAMES, NinovaMcpApp

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# One BLG-style history entry: rows are [term, instructor, capacity, enrolled, days].
COURSE_ENTRY = {
    "code": "BLG 102E",
    "name": "Intr to Sci&Eng Comp (C)",
    "terms": ["2025-2026-bahar", "2024-2025-bahar", "2019-2020-yaz"],
    "rows": [
        ["2025-2026-bahar", "Ali Çakmak", 123, 125, "Pazartesi, Perşembe"],
        ["2025-2026-bahar", "Ayşe Tosun", 120, 114, "Pazartesi, Perşembe"],
        ["2024-2025-bahar", "Ali Çakmak", 95, 109, "Pazartesi, Perşembe"],
        ["2019-2020-yaz", "--", 40, 0, "Salı"],
        ["malformed-row"],
    ],
}

INSTRUCTOR_ENTRY = {
    "name": "Ali Çakmak",
    "terms": 2,
    "rows": [
        ["2025-2026-bahar", "BLG 102E", "Intr to Sci&Eng Comp (C)", 123, 125],
        ["2024-2025-bahar", "BLG 102E", "Intr to Sci&Eng Comp (C)", 95, 109],
        ["2024-2025-bahar", "BLG 335E", "Analysis of Algorithms I", 60, 55],
    ],
}


class CourseCodeTests(unittest.TestCase):
    def test_three_digit(self) -> None:
        self.assertEqual(split_course_code("blg 223e"), ("BLG", "223E"))

    def test_four_digit_capstone(self) -> None:
        """The 4-digit design-course codes that used to be rejected outright."""
        self.assertEqual(split_course_code("CEN 4901E"), ("CEN", "4901E"))
        self.assertEqual(normalize_course_code("cen4902e"), "CEN 4902E")

    def test_two_letter_suffix(self) -> None:
        self.assertEqual(split_course_code("FIZ 101EL"), ("FIZ", "101EL"))

    def test_rejects_garbage(self) -> None:
        with self.assertRaises(ValueError):
            split_course_code("not a course")


class ArchiveAggregationTests(unittest.TestCase):
    def test_course_history_groups_by_term(self) -> None:
        result = course_history(COURSE_ENTRY)
        self.assertEqual(result["terms_offered"], 3)
        newest = result["offerings"][0]
        self.assertEqual(newest["term"], "2025-2026-bahar")
        self.assertEqual(newest["section_count"], 2)
        self.assertEqual(newest["instructors"], ["Ali Çakmak", "Ayşe Tosun"])

    def test_course_history_skips_malformed_rows(self) -> None:
        result = course_history(COURSE_ENTRY)
        total = sum(o["section_count"] for o in result["offerings"])
        self.assertEqual(total, 4)

    def test_limit_terms_marks_truncation(self) -> None:
        result = course_history(COURSE_ENTRY, limit_terms=1)
        self.assertEqual(len(result["offerings"]), 1)
        self.assertTrue(result["truncated"])

    def test_seasonality_detects_single_season(self) -> None:
        result = seasonality(["2023-2024-guz", "2022-2023-guz"])
        self.assertEqual(result["only_season"], "Güz")

    def test_seasonality_none_when_mixed(self) -> None:
        result = seasonality(["2023-2024-guz", "2022-2023-bahar"])
        self.assertIsNone(result["only_season"])
        self.assertEqual(result["total_terms"], 2)

    def test_who_taught_ranks_and_drops_placeholder(self) -> None:
        result = who_taught(COURSE_ENTRY)
        names = [i["instructor"] for i in result["instructors"]]
        self.assertNotIn("--", names)
        self.assertEqual(names[0], "Ali Çakmak")
        self.assertEqual(result["instructors"][0]["term_count"], 2)
        self.assertEqual(result["instructors"][0]["latest_term"], "2025-2026-bahar")

    def test_who_taught_fill_ratio(self) -> None:
        result = who_taught(COURSE_ENTRY)
        cakmak = result["instructors"][0]
        # (125/123 + 109/95) / 2 — both sections overfilled past capacity
        self.assertAlmostEqual(cakmak["average_fill_ratio"], 1.082, places=2)

    def test_instructor_courses_newest_first(self) -> None:
        result = instructor_courses(INSTRUCTOR_ENTRY)
        self.assertEqual(result["distinct_courses"], 2)
        self.assertEqual(result["courses"][0]["course_code"], "BLG 102E")
        self.assertEqual(result["courses"][0]["term_count"], 2)

    def test_summarize_section_pairs_parallel_arrays(self) -> None:
        section = {
            "crn": "13494",
            "code": "BLG 223E",
            "name": "Data Structures",
            "instructor": "Yusuf Hüseyin Şahin",
            "buildings": ["BBB", "BBB"],
            "days": ["Salı", "Çarşamba"],
            "times": ["08:30/10:29", "13:30/15:29"],
            "rooms": [],
            "capacity": 80,
            "enrolled": 40,
        }
        result = summarize_section(section)
        self.assertEqual(len(result["sessions"]), 2)
        self.assertEqual(result["sessions"][1]["day"], "Çarşamba")
        self.assertEqual(result["sessions"][1]["time"], "13:30/15:29")
        self.assertIsNone(result["sessions"][0]["room"])
        self.assertEqual(result["fill_ratio"], 0.5)

    def test_fill_summary_flags_full_section(self) -> None:
        quota = {"courses": [{"crn": "30006", "capacity": 33, "enrolled": 35, "filledAt": "x"}]}
        result = fill_summary(quota, "30006")
        self.assertTrue(result["is_full"])
        self.assertGreater(result["fill_ratio"], 1.0)
        self.assertIsNone(fill_summary(quota, "99999"))


CODES_INDEX = [
    ["MAT 202", "Sayısal Yöntemler", "MAT", 27],
    ["MUH 321", "Sayısal Yöntemler", "MUH", 10],
    ["INS 202", "İnşaat Müh. Sayısal Yöntemler", "INS", 3],
    ["BLG 102E", "Intr to Sci&Eng Comp (C)", "BLG", 13],
]


class SearchCoursesTests(unittest.TestCase):
    def test_exact_code_match_ranks_first(self) -> None:
        results = search_courses(CODES_INDEX, "BLG 102E")
        self.assertEqual(results[0]["course_code"], "BLG 102E")

    def test_name_fragment_finds_code(self) -> None:
        """The case the tool exists for: a name with no known code."""
        results = search_courses(CODES_INDEX, "sayısal yöntemler")
        codes = {r["course_code"] for r in results}
        self.assertEqual(codes, {"MAT 202", "MUH 321", "INS 202"})

    def test_exact_and_prefix_code_matches_outrank_name_matches(self) -> None:
        results = search_courses(CODES_INDEX, "sayısal yöntemler")
        # All three are name matches here (rank 3); tie-broken by term_count.
        self.assertEqual(results[0]["course_code"], "MAT 202")

    def test_empty_query_returns_nothing(self) -> None:
        self.assertEqual(search_courses(CODES_INDEX, ""), [])

    def test_no_match_returns_empty(self) -> None:
        self.assertEqual(search_courses(CODES_INDEX, "zzzqqq"), [])

    def test_limit_is_respected(self) -> None:
        results = search_courses(CODES_INDEX, "sayısal yöntemler", limit=1)
        self.assertEqual(len(results), 1)


SECTIONS_TERM_A = [
    {"instructor": "Ali Çakmak", "capacity": 123, "enrolled": 125, "fill_ratio": 1.016},
    {"instructor": "Ayşe Tosun", "capacity": 120, "enrolled": 114, "fill_ratio": 0.95},
]
SECTIONS_TERM_B = [
    {"instructor": "Ali Çakmak", "capacity": 95, "enrolled": 109, "fill_ratio": 1.147},
    {"instructor": "Yaşar Erenler", "capacity": 75, "enrolled": 80, "fill_ratio": 1.067},
]


class DiffTermOfferingsTests(unittest.TestCase):
    def test_instructor_turnover_is_detected(self) -> None:
        result = diff_term_offerings(SECTIONS_TERM_A, SECTIONS_TERM_B)
        self.assertEqual(result["instructors_added"], ["Yaşar Erenler"])
        self.assertEqual(result["instructors_removed"], ["Ayşe Tosun"])

    def test_section_count_delta(self) -> None:
        result = diff_term_offerings(SECTIONS_TERM_A, SECTIONS_TERM_B[:1])
        self.assertEqual(result["section_count_delta"], -1)

    def test_totals_are_summed(self) -> None:
        result = diff_term_offerings(SECTIONS_TERM_A, SECTIONS_TERM_B)
        self.assertEqual(result["first"]["total_capacity"], 243)
        self.assertEqual(result["second"]["total_enrolled"], 189)

    def test_empty_sides_do_not_crash(self) -> None:
        result = diff_term_offerings([], [])
        self.assertEqual(result["section_count_delta"], 0)
        self.assertIsNone(result["first"]["average_fill_ratio"])


class RecommendCourseTimingTests(unittest.TestCase):
    def test_single_season_course(self) -> None:
        summary = seasonality(["2023-2024-guz", "2022-2023-guz"])
        text = recommend_course_timing("CEN 411E", summary, [])
        self.assertIn("yalnızca Güz", text)

    def test_dominant_season_course(self) -> None:
        summary = seasonality(["2023-2024-bahar", "2022-2023-bahar", "2021-2022-guz"])
        text = recommend_course_timing("MAT 210E", summary, [])
        self.assertIn("çoğunlukla Bahar", text)

    def test_instructor_is_included(self) -> None:
        summary = seasonality(["2023-2024-guz"])
        instructors = [{"instructor": "Hayri Turgut Uyar", "term_count": 6, "latest_term": "2022-2023-bahar", "average_fill_ratio": 0.98}]
        text = recommend_course_timing("BLG 102E", summary, instructors)
        self.assertIn("Hayri Turgut Uyar", text)
        self.assertIn("0.98", text)

    def test_no_history_still_produces_a_sentence(self) -> None:
        summary = seasonality([])
        text = recommend_course_timing("ZZZ 999", summary, [])
        self.assertTrue(text)


class ArchiveClientTests(unittest.TestCase):
    def test_rejects_plain_http_base(self) -> None:
        with self.assertRaises(ItuArchiveError):
            ItuArchiveClient(base_url="http://example.com/data")

    def test_offhost_url_is_rejected(self) -> None:
        client = ItuArchiveClient()
        with self.assertRaises(ItuArchiveError):
            client._validate_url("https://evil.example.com/data/index.json")

    def test_allows_configured_host(self) -> None:
        client = ItuArchiveClient(base_url="https://fork.example.com/data")
        client._validate_url("https://fork.example.com/data/index.json")


class PrerequisiteExpressionTests(unittest.TestCase):
    def test_or_group_and_chain_precedence(self) -> None:
        """Parenthesised OR runs joined by Ve must stay grouped."""
        tree = parse_prerequisite_expression(
            "( BLG 322 MIN. DD Veya BLG 322E MIN. DD ) Ve ( MAT 210 MIN. DD )"
        )
        self.assertEqual(tree["type"], "and")
        self.assertEqual(tree["operands"][0]["type"], "or")

    def test_min_grade_is_attached(self) -> None:
        tree = parse_prerequisite_expression("CEN 4901E MIN. BB")
        self.assertEqual(tree, {"type": "course", "code": "CEN 4901E", "min_grade": "BB"})

    def test_evaluate_or_needs_only_one_branch(self) -> None:
        tree = parse_prerequisite_expression("BLG 322 MIN. DD Veya BLG 322E MIN. DD")
        self.assertTrue(evaluate_tree(tree, {"BLG 322E": "CC"})["satisfied"])

    def test_evaluate_enforces_minimum_grade(self) -> None:
        tree = parse_prerequisite_expression("CEN 4901E MIN. BB")
        self.assertFalse(evaluate_tree(tree, {"CEN 4901E": "CC"})["satisfied"])
        self.assertTrue(evaluate_tree(tree, {"CEN 4901E": "BA"})["satisfied"])

    def test_failing_grade_does_not_satisfy(self) -> None:
        tree = parse_prerequisite_expression("BLG 322E MIN. DD")
        verdict = evaluate_tree(tree, {"BLG 322E": "VF"})
        self.assertFalse(verdict["satisfied"])
        self.assertIn("BLG 322E", verdict["missing"])

    def test_missing_course_is_reported(self) -> None:
        tree = parse_prerequisite_expression("MAT 281 MIN. DD Veya MAT 281E MIN. DD")
        verdict = evaluate_tree(tree, {})
        self.assertFalse(verdict["satisfied"])
        self.assertEqual(verdict["missing"], ["MAT 281", "MAT 281E"])

    def test_empty_expression_is_satisfied(self) -> None:
        self.assertTrue(evaluate_tree(parse_prerequisite_expression(""), {})["satisfied"])


class BranchPrerequisiteTableTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parsed = extract_branch_prerequisites(
            fixture("onsart_ara_cen.html"), "https://obs.itu.edu.tr/x", "CEN"
        )

    def test_table_parsed_flag(self) -> None:
        self.assertTrue(self.parsed["table_parsed"])
        self.assertGreater(self.parsed["constrained_course_count"], 0)

    def test_four_digit_course_is_indexed(self) -> None:
        self.assertIn("CEN 4901E", self.parsed["rules"])
        self.assertIn("CEN 4902E", self.parsed["rules"])

    def test_capstone_credit_requirement(self) -> None:
        rule = self.parsed["rules"]["CEN 4901E"]
        self.assertEqual(rule["credit_requirement"], 95.0)

    def test_capstone_minimum_grades(self) -> None:
        rule = self.parsed["rules"]["CEN 4902E"]
        self.assertEqual(rule["minimum_grades"]["CEN 4901E"], "BB")

    def test_turkish_characters_survive_entity_decoding(self) -> None:
        self.assertIn("Mühendisliği", self.parsed["rules"]["CEN 4901E"]["course_name"])

    def test_unparsed_table_is_not_read_as_no_prerequisites(self) -> None:
        empty = extract_branch_prerequisites("<html>nothing</html>", "u", "CEN")
        self.assertFalse(empty["table_parsed"])
        self.assertEqual(empty["rules"], {})


class GraduationSummaryTests(unittest.TestCase):
    PAYLOAD = {
        "mezuniyetimeNeKaldiBilgi": {
            "metKrediTotal": 82,
            "toplamDersSayisi": 55,
            "tamamlananDersSayisi": 37,
            "gpa": 1.89,
            "dersPlaniVM": {
                "gerekliMezuniyetKredisi": 134,
                "gerekliMinGPA": 2,
                "akademikProgramAdiTR": "Bilgisayar Mühendisliği",
            },
            "checkMetMezuniyetList": [
                {
                    "isMet": True, "bransKodu": "BLG 422E", "dersAdi": "Computer Networks",
                    "grupName": "7th Sems. Elect. Course I (MT)", "harfNotu": "CC",
                    "kredisiDec": 2, "sayilanKredi": 3, "donemNo": 7,
                },
                {
                    "isMet": True, "bransKodu": "BLG 252E", "dersAdi": "OOP",
                    "grupName": "", "harfNotu": "BB+", "kredisiDec": 3, "donemNo": 4,
                },
                {
                    "isMet": False, "bransKodu": "", "dersAdi": "8th Sems. Elect. Course I (MT)",
                    "grupName": "8th Sems. Elect. Course I (MT)", "harfNotu": "",
                    "kredisiDec": 2, "donemNo": 8,
                },
                {
                    "isMet": False, "bransKodu": "CEN 4901E", "dersAdi": "Design I",
                    "grupName": "", "harfNotu": "", "kredisiDec": 3, "donemNo": 7,
                },
            ],
            "unusedSinifOgrenciList": [
                {"bransKodu": "MAT 210E", "dersAdi": "Eng Math", "harfNotu": "FF", "kredisi": 4},
            ],
        }
    }

    def setUp(self) -> None:
        self.summary = summarize_graduation_plan(self.PAYLOAD)

    def test_elective_slot_maps_to_real_course(self) -> None:
        """The mapping the raw payload buries in grupName."""
        filled = self.summary["filled_elective_slots"]
        self.assertEqual(len(filled), 1)
        self.assertEqual(filled[0]["slot"], "7th Sems. Elect. Course I (MT)")
        self.assertEqual(filled[0]["course_code"], "BLG 422E")

    def test_open_slots_listed_separately(self) -> None:
        slots = [s["slot"] for s in self.summary["open_elective_slots"]]
        self.assertEqual(slots, ["8th Sems. Elect. Course I (MT)"])

    def test_named_remaining_courses_are_not_slots(self) -> None:
        codes = [c["course_code"] for c in self.summary["remaining_required_courses"]]
        self.assertEqual(codes, ["CEN 4901E"])

    def test_credit_tally(self) -> None:
        self.assertEqual(self.summary["credits_remaining"], 52.0)

    def test_failed_attempts_surfaced(self) -> None:
        self.assertEqual(self.summary["failed_or_unused_attempts"][0]["course_code"], "MAT 210E")


class ArchiveToolRegistrationTests(unittest.TestCase):
    ARCHIVE_TOOLS = [
        "archive_list_terms",
        "archive_course_history",
        "archive_who_taught",
        "archive_instructor_courses",
        "archive_term_sections",
        "archive_fill_rate",
        "archive_search_courses",
        "archive_list_branches",
        "archive_compare_terms",
        "plan_remaining_courses",
    ]

    def test_tools_are_registered_and_implemented(self) -> None:
        app = NinovaMcpApp()
        for name in self.ARCHIVE_TOOLS:
            self.assertIn(name, LOCAL_TOOL_NAMES)
            self.assertTrue(callable(getattr(app, name)))


class ArchiveToolBehaviourTests(unittest.TestCase):
    """Tool-level behaviour with the network stubbed out."""

    INDEX = {
        "currentTerm": "2025-2026 Yaz Dönemi",
        "currentSlug": "2025-2026-yaz",
        "terms": [
            {"slug": "2025-2026-guz", "label": "2025-2026 Güz Dönemi", "sections": 2985},
            {"slug": "2024-2025-guz", "label": "2024-2025 Güz Dönemi", "missing": True},
            {"slug": "2025-2026-yaz", "label": "2025-2026 Yaz Dönemi", "sections": 469},
        ],
    }
    META = {"sections": 2985, "branches": [{"code": "BLG", "sections": 33}]}

    def setUp(self) -> None:
        self.app = NinovaMcpApp()

    def _patch(self, **overrides):
        defaults = {
            "get_index": lambda: self.INDEX,
            "get_term_meta": lambda slug: self.META,
            "get_term_branch": lambda slug, branch: [],
            "get_course_codes": lambda: CODES_INDEX,
        }
        defaults.update(overrides)
        return patch.multiple(
            "ninova_mcp.archive_client.ItuArchiveClient",
            **{k: (lambda _self, *a, _f=v, **kw: _f(*a, **kw)) for k, v in defaults.items()},
        )

    def test_missing_term_is_named_as_such(self) -> None:
        with self._patch():
            result = self.app.archive_term_sections(term="2024-2025-guz", branch="BLG")
        self.assertEqual(result["coverage"], "term_missing")
        self.assertIn("hiçbir kaynakta yok", result["coverage_note"])

    def test_branch_absent_from_term_is_distinguished(self) -> None:
        with self._patch():
            result = self.app.archive_term_sections(term="2025-2026-guz", course_code="CEN 411E")
        self.assertEqual(result["coverage"], "branch_absent_from_term")
        self.assertEqual(result["match_count"], 0)

    def test_covered_but_unmatched_is_distinguished(self) -> None:
        sections = [{"crn": "1", "code": "BLG 223E", "instructor": "X", "days": [], "times": []}]
        with self._patch(get_term_branch=lambda slug, branch: sections):
            result = self.app.archive_term_sections(term="2025-2026-guz", course_code="BLG 322E")
        self.assertEqual(result["coverage"], "covered")
        self.assertEqual(result["match_count"], 0)
        self.assertIn("filtreye", result["coverage_note"])

    def test_unknown_term_raises(self) -> None:
        with self._patch():
            with self.assertRaises(ItuArchiveError):
                self.app.archive_term_sections(term="1999-2000-guz", branch="BLG")

    def test_matching_section_is_returned(self) -> None:
        sections = [
            {
                "crn": "13494", "code": "BLG 223E", "name": "Data Structures",
                "instructor": "Yusuf Hüseyin Şahin", "days": ["Salı"], "times": ["08:30/10:29"],
                "buildings": ["BBB"], "rooms": [], "capacity": 80, "enrolled": 0,
            }
        ]
        with self._patch(get_term_branch=lambda slug, branch: sections):
            result = self.app.archive_term_sections(term="2025-2026-guz", course_code="BLG 223E")
        self.assertEqual(result["match_count"], 1)
        self.assertEqual(result["sections"][0]["crn"], "13494")

    def test_course_missing_from_archive_explains_why(self) -> None:
        with self._patch(get_course_history=lambda branch: {}):
            with self.assertRaises(ItuArchiveError) as ctx:
                self.app.archive_course_history("BLG 999E")
        self.assertIn("arşivde bulunamadı", str(ctx.exception))

    def test_bad_course_code_raises_archive_error(self) -> None:
        with self.assertRaises(ItuArchiveError):
            self.app.archive_course_history("nonsense")

    def test_fill_rate_requires_an_argument(self) -> None:
        with self._patch():
            with self.assertRaises(ItuArchiveError):
                self.app.archive_fill_rate()

    # -- archive_search_courses --------------------------------------

    def test_search_courses_finds_by_name(self) -> None:
        with self._patch():
            result = self.app.archive_search_courses("sayısal yöntemler")
        codes = {m["course_code"] for m in result["matches"]}
        self.assertEqual(codes, {"MAT 202", "MUH 321", "INS 202"})

    def test_search_courses_respects_limit(self) -> None:
        with self._patch():
            result = self.app.archive_search_courses("sayısal yöntemler", limit=1)
        self.assertEqual(result["match_count"], 1)

    # -- archive_list_branches -----------------------------------------

    def test_list_branches_returns_meta_branches(self) -> None:
        with self._patch():
            result = self.app.archive_list_branches("2025-2026-guz")
        self.assertEqual(result["coverage"], "covered")
        self.assertEqual([b["code"] for b in result["branches"]], ["BLG"])

    def test_list_branches_on_missing_term(self) -> None:
        with self._patch():
            result = self.app.archive_list_branches("2024-2025-guz")
        self.assertEqual(result["coverage"], "term_missing")
        self.assertEqual(result["branches"], [])

    def test_list_branches_unknown_term_raises(self) -> None:
        with self._patch():
            with self.assertRaises(ItuArchiveError):
                self.app.archive_list_branches("1999-2000-guz")

    # -- archive_compare_terms ------------------------------------------

    def test_compare_terms_reports_instructor_turnover(self) -> None:
        def by_term(slug, branch):
            return SECTIONS_TERM_A_RAW if slug == "term-a" else SECTIONS_TERM_B_RAW

        index = dict(self.INDEX)
        index["terms"] = [
            {"slug": "term-a", "label": "Term A", "sections": 2},
            {"slug": "term-b", "label": "Term B", "sections": 2},
        ]
        meta = {"sections": 2, "branches": [{"code": "BLG", "sections": 2}]}
        with self._patch(get_index=lambda: index, get_term_meta=lambda slug: meta, get_term_branch=by_term):
            result = self.app.archive_compare_terms("BLG 102E", "term-a", "term-b")
        self.assertTrue(result["comparable"])
        self.assertEqual(result["diff"]["instructors_added"], ["Yaşar Erenler"])
        self.assertEqual(result["diff"]["instructors_removed"], ["Ayşe Tosun"])

    def test_compare_terms_flags_incomparable_coverage(self) -> None:
        index = dict(self.INDEX)
        index["terms"] = [
            {"slug": "2025-2026-guz", "label": "Güz", "sections": 2985},
            {"slug": "2024-2025-guz", "label": "Güz", "missing": True},
        ]
        with self._patch(get_index=lambda: index):
            result = self.app.archive_compare_terms("BLG 102E", "2025-2026-guz", "2024-2025-guz")
        self.assertFalse(result["comparable"])
        self.assertEqual(result["coverage_b"], "term_missing")

    def test_compare_terms_bad_course_code_raises(self) -> None:
        with self._patch():
            with self.assertRaises(ItuArchiveError):
                self.app.archive_compare_terms("nonsense", "2025-2026-guz", "2025-2026-yaz")

    # -- plan_remaining_courses ------------------------------------------

    def test_plan_remaining_courses_combines_seasonality_and_instructors(self) -> None:
        graduation = {
            "mezuniyetimeNeKaldiBilgi": {
                "checkMetMezuniyetList": [
                    {
                        "isMet": False, "bransKodu": "BLG 102E", "dersAdi": "Intr to Sci&Eng Comp (C)",
                        "grupName": "", "harfNotu": "", "kredisiDec": 4, "donemNo": 2,
                    },
                    {
                        "isMet": False, "bransKodu": "", "dersAdi": "7th Sems. Elect. Course (ITB)",
                        "grupName": "7th Sems. Elect. Course (ITB)", "harfNotu": "", "kredisiDec": 3, "donemNo": 7,
                    },
                ],
                "dersPlaniVM": {"gerekliMezuniyetKredisi": 134, "gerekliMinGPA": 2},
            }
        }
        with self._patch(get_course_history=lambda branch: {"BLG 102E": COURSE_ENTRY}), \
             patch.object(self.app.obs, "get_graduation_remaining", return_value=graduation), \
             patch.object(self.app.obs, "default_program_id", return_value=1):
            result = self.app.plan_remaining_courses()
        # summarize_graduation_plan routes the grupName entry into open_elective_slots,
        # not remaining_required_courses, so only the named course reaches this tool.
        self.assertEqual(result["remaining_course_count"], 1)
        self.assertEqual(result["resolved_count"], 1)
        plan = result["plans"][0]
        self.assertEqual(plan["course_code"], "BLG 102E")
        self.assertIn("recommendation", plan)
        self.assertEqual(result["unresolved_courses"], [])

    def test_plan_remaining_courses_reports_archive_gap(self) -> None:
        graduation = {
            "mezuniyetimeNeKaldiBilgi": {
                "checkMetMezuniyetList": [
                    {
                        "isMet": False, "bransKodu": "EEE 211E", "dersAdi": "Basics of Electrical Circuits",
                        "grupName": "", "harfNotu": "", "kredisiDec": 3, "donemNo": 3,
                    },
                ],
                "dersPlaniVM": {"gerekliMezuniyetKredisi": 134, "gerekliMinGPA": 2},
            }
        }
        with self._patch(get_course_history=lambda branch: {}), \
             patch.object(self.app.obs, "get_graduation_remaining", return_value=graduation), \
             patch.object(self.app.obs, "default_program_id", return_value=1):
            result = self.app.plan_remaining_courses()
        self.assertEqual(result["resolved_count"], 0)
        self.assertEqual(result["unresolved_courses"][0]["course_code"], "EEE 211E")
        self.assertIn("arşivde bulunamadı", result["unresolved_courses"][0]["reason"])


SECTIONS_TERM_A_RAW = [
    {
        "crn": "1", "code": "BLG 102E", "instructor": "Ali Çakmak",
        "days": [], "times": [], "buildings": [], "rooms": [], "capacity": 123, "enrolled": 125,
    },
    {
        "crn": "2", "code": "BLG 102E", "instructor": "Ayşe Tosun",
        "days": [], "times": [], "buildings": [], "rooms": [], "capacity": 120, "enrolled": 114,
    },
]
SECTIONS_TERM_B_RAW = [
    {
        "crn": "3", "code": "BLG 102E", "instructor": "Ali Çakmak",
        "days": [], "times": [], "buildings": [], "rooms": [], "capacity": 95, "enrolled": 109,
    },
    {
        "crn": "4", "code": "BLG 102E", "instructor": "Yaşar Erenler",
        "days": [], "times": [], "buildings": [], "rooms": [], "capacity": 75, "enrolled": 80,
    },
]


class ExplainEligibilityArchiveSeasonalityTests(unittest.TestCase):
    """explain_course_eligibility's archive_seasonality field."""

    def setUp(self) -> None:
        self.app = NinovaMcpApp()
        self.branch_rules = extract_branch_prerequisites(
            fixture("onsart_ara_cen.html"), "https://obs.itu.edu.tr/x", "CEN"
        )

    def test_single_season_course_is_flagged(self) -> None:
        entry = {"code": "CEN 354E", "name": "Signal&Systems", "terms": ["2025-2026-bahar", "2024-2025-bahar"], "rows": []}
        with patch.object(self.app.obs_public, "get_branch_prerequisites", return_value=self.branch_rules), \
             patch.object(self.app.archive, "get_course_history", return_value={"CEN 354E": entry}), \
             patch.object(self.app.prereq_crosscheck, "get_course_prerequisite_tree", return_value=None):
            result = self.app.explain_course_eligibility("CEN 354E")
        self.assertIsNotNone(result["archive_seasonality"])
        self.assertEqual(result["archive_seasonality"]["only_season"], "Bahar")
        self.assertIn("yalnızca Bahar", result["archive_seasonality"]["note"])

    def test_multi_season_course_has_no_note(self) -> None:
        entry = {"code": "CEN 354E", "name": "x", "terms": ["2025-2026-bahar", "2025-2026-guz"], "rows": []}
        with patch.object(self.app.obs_public, "get_branch_prerequisites", return_value=self.branch_rules), \
             patch.object(self.app.archive, "get_course_history", return_value={"CEN 354E": entry}), \
             patch.object(self.app.prereq_crosscheck, "get_course_prerequisite_tree", return_value=None):
            result = self.app.explain_course_eligibility("CEN 354E")
        self.assertIsNone(result["archive_seasonality"]["note"])

    def test_archive_failure_is_non_fatal(self) -> None:
        with patch.object(self.app.obs_public, "get_branch_prerequisites", return_value=self.branch_rules), \
             patch.object(self.app.archive, "get_course_history", side_effect=ItuArchiveError("down")), \
             patch.object(self.app.prereq_crosscheck, "get_course_prerequisite_tree", return_value=None):
            result = self.app.explain_course_eligibility("CEN 354E")
        self.assertIn("prerequisite_status", result)
        self.assertIsNone(result["archive_seasonality"])

    def test_course_absent_from_archive_is_none(self) -> None:
        with patch.object(self.app.obs_public, "get_branch_prerequisites", return_value=self.branch_rules), \
             patch.object(self.app.archive, "get_course_history", return_value={}), \
             patch.object(self.app.prereq_crosscheck, "get_course_prerequisite_tree", return_value=None):
            result = self.app.explain_course_eligibility("CEN 354E")
        self.assertIsNone(result["archive_seasonality"])


if __name__ == "__main__":
    unittest.main()
