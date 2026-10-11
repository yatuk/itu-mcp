"""Day abbreviations, and branch lists that name every unknown code.

Synthetic fixtures; every name is invented.
"""

from __future__ import annotations

import unittest
from typing import Any

from test_archive_datasets import BASE, FakeArchiveSession

from ninova_mcp.app_archive import MAX_TERM_SECTION_BRANCHES, split_branch_codes
from ninova_mcp.archive import normalize_day, search_sections
from ninova_mcp.archive_client import ItuArchiveClient, ItuArchiveError
from ninova_mcp.server import NinovaMcpApp

TERM = "2026-2027-guz"
# More than twenty branches, so a list cut at 20 would be noticed.
TERM_BRANCHES = [f"B{chr(65 + i // 26)}{chr(65 + i % 26)}" for i in range(28)] + ["TST", "ABC"]

INDEX = {
    "currentTerm": "2026-2027 Güz Dönemi",
    "currentSlug": TERM,
    "stats": {"capacity": 100, "enrolled": 60},
    "terms": [
        {"slug": TERM, "label": "2026-2027 Güz Dönemi", "source": "itu-archive"},
        {"slug": "2024-2025-guz", "label": "2024-2025 Güz Dönemi", "missing": True},
    ],
}


def section(crn: str, code: str, instructor: str) -> dict[str, Any]:
    return {
        "crn": crn, "code": code, "branch": code.split()[0], "level": "LS", "name": "Sample",
        "method": "", "instructor": instructor, "buildings": [], "days": [], "times": [], "rooms": [],
        "capacity": 50, "enrolled": 30, "reserved": "", "programs": [], "prereq": "", "classReq": "",
    }


DOCUMENTS: dict[str, Any] = {
    "/index.json": INDEX,
    f"/terms/{TERM}/meta.json": {
        "slug": TERM, "sections": 400, "courses": 200, "stats": {"capacity": 100, "enrolled": 60},
        "branches": [{"code": code, "levels": ["LS"], "sections": 2} for code in TERM_BRANCHES],
    },
    f"/terms/{TERM}/branches/TST.json": [section("1", "TST 101E", "Ada Kurgu"), section("2", "TST 102E", "Mert Sahte")],
    f"/terms/{TERM}/branches/ABC.json": [section("3", "ABC 201", "Ada Kurgu")],
    f"/terms/{TERM}/search.json": [
        ["1", "TST 101E", "Sample", "TST", "Ada Kurgu", "Salı 08:30/10:29 | Perşembe 14:30/16:29", 50, 30, "LS", "", []],
        ["2", "TST 102E", "Sample", "TST", "Mert Sahte", "Çarşamba 09:30/12:29", 50, 30, "LS", "", []],
        ["3", "ABC 201", "Sample", "ABC", "Ada Kurgu", "Cumartesi 09:30/12:29", 50, 30, "LS", "", []],
    ],
}


def make_app() -> tuple[NinovaMcpApp, FakeArchiveSession]:
    session = FakeArchiveSession(DOCUMENTS)
    client = ItuArchiveClient(session=session, base_url=BASE, cache_ttl=60)  # type: ignore[arg-type]
    client._min_request_interval = 0
    app = NinovaMcpApp()
    app._archive = client
    return app, session


class DayTests(unittest.TestCase):
    EXPECTED = {
        "pazartesi": ["Pazartesi", "Pzt", "pzt", "Monday", "Mon", "MON", "mon."],
        "sali": ["Salı", "sali", "Sal", "Tuesday", "Tue", "tue", "Tues"],
        "carsamba": ["Çarşamba", "Çar", "car", "Wednesday", "Wed"],
        "persembe": ["Perşembe", "Per", "Thursday", "Thu", "Thur", "Thurs"],
        "cuma": ["Cuma", "Cum", "Friday", "Fri"],
        "cumartesi": ["Cumartesi", "Cmt", "Saturday", "Sat"],
        "pazar": ["Pazar", "Paz", "Sunday", "Sun"],
    }

    def test_full_names_and_abbreviations_in_both_languages(self) -> None:
        for expected, spellings in self.EXPECTED.items():
            for raw in spellings:
                with self.subTest(day=raw):
                    self.assertEqual(normalize_day(raw), expected)

    def test_error_lists_both_languages(self) -> None:
        for bad in ("", "Tu", "T", "Funday", "Sa", "günaydın"):
            with self.subTest(day=bad), self.assertRaises(ValueError) as caught:
                normalize_day(bad)
            message = str(caught.exception)
            for word in ("Salı", "Tuesday", "Sal", "Tue", "Cumartesi", "Saturday"):
                self.assertIn(word, message)

    def test_search_sections_accepts_tue(self) -> None:
        rows = DOCUMENTS[f"/terms/{TERM}/search.json"]
        self.assertEqual([s["crn"] for s in search_sections(rows, day="Tue")["sections"]], ["1"])
        self.assertEqual([s["crn"] for s in search_sections(rows, day="Sal")["sections"]], ["1"])
        self.assertEqual([s["crn"] for s in search_sections(rows, day="Sat")["sections"]], ["3"])
        self.assertEqual([s["crn"] for s in search_sections(rows, day="Cmt")["sections"]], ["3"])

    def test_search_sections_tool(self) -> None:
        app, _ = make_app()
        self.assertEqual([s["crn"] for s in app.archive_search_sections(day="Tue")["sections"]], ["1"])
        with self.assertRaises(ItuArchiveError) as caught:
            app.archive_search_sections(day="Tu")
        self.assertIn("Tuesday", str(caught.exception))
        self.assertIn("Salı", str(caught.exception))


class BranchListTests(unittest.TestCase):
    def test_split_accepts_commas_semicolons_and_spaces(self) -> None:
        self.assertEqual(split_branch_codes("blg, MAT;fiz  kim"), ["BLG", "MAT", "FIZ", "KIM"])
        self.assertEqual(split_branch_codes("BLG, blg"), ["BLG"])
        self.assertEqual(split_branch_codes(None), [])

    def test_every_malformed_code_is_named(self) -> None:
        with self.assertRaises(ItuArchiveError) as caught:
            split_branch_codes("KIM, ../x, B1G, TOOLONG")
        message = str(caught.exception)
        for bad in ("'../x'", "'B1G'", "'TOOLONG'"):
            self.assertIn(bad, message)
        self.assertNotIn("'KIM'", message)

    def test_too_many_branches_are_refused(self) -> None:
        with self.assertRaises(ItuArchiveError):
            split_branch_codes(", ".join(TERM_BRANCHES[: MAX_TERM_SECTION_BRANCHES + 1]))

    def test_single_branch_call_is_unchanged(self) -> None:
        app, _ = make_app()
        result = app.archive_term_sections(term=TERM, branch="tst")
        self.assertEqual(result["branch"], "TST")
        self.assertEqual(result["branches"], ["TST"])
        self.assertEqual(result["coverage"], "covered")
        self.assertEqual(result["match_count"], 2)
        self.assertNotIn("unknown_branches", result)
        self.assertNotIn("branch_coverage", result)

    def test_multi_branch_call_reads_every_branch(self) -> None:
        app, _ = make_app()
        result = app.archive_term_sections(term=TERM, branch="TST, ABC", instructor="ada")
        self.assertEqual(result["coverage"], "covered")
        self.assertEqual(result["branch_section_count"], 3)
        self.assertEqual([row["crn"] for row in result["sections"]], ["1", "3"])
        self.assertEqual(result["branch_coverage"], {"TST": "covered", "ABC": "covered"})

    def test_every_unknown_branch_is_named(self) -> None:
        app, _ = make_app()
        result = app.archive_term_sections(term=TERM, branch="TST, XYZ, QQQ, ABC, ZZ")
        self.assertEqual(result["coverage"], "partly_covered")
        self.assertEqual(result["unknown_branches"], ["XYZ", "QQQ", "ZZ"])
        for code in ("XYZ", "QQQ", "ZZ"):
            self.assertIn(code, result["coverage_note"])
        self.assertEqual(result["match_count"], 3)

    def test_valid_codes_are_listed_in_full_with_their_count(self) -> None:
        app, _ = make_app()
        result = app.archive_term_sections(term=TERM, branch="XYZ")
        self.assertEqual(result["coverage"], "branch_absent_from_term")
        self.assertEqual(result["unknown_branches"], ["XYZ"])
        self.assertEqual(result["term_branch_count"], 30)
        self.assertEqual(result["term_branches"], sorted(TERM_BRANCHES))
        self.assertIn("30 branş", result["coverage_note"])

    def test_missing_term_with_several_branches(self) -> None:
        app, _ = make_app()
        result = app.archive_term_sections(term="2024-2025-guz", branch="TST, ABC")
        self.assertEqual(result["coverage"], "term_missing")
        self.assertNotIn("unknown_branches", result)


if __name__ == "__main__":
    unittest.main()
