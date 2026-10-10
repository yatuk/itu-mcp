"""Tests for the archive tools built on the extra datasets.

Grades, catalog, reverse prerequisites, term-wide search, exam schedules and
scraper status. No network: a fake ``requests`` session serves small synthetic
documents by path and answers 404 for everything else, so the real
``ItuArchiveClient`` (path building, allowlist, optional-404 handling) runs
underneath every tool call. Names in the fixtures are made up.
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from typing import Any

import requests

from ninova_mcp.archive import (
    SEARCH_ROW_FIELDS,
    catalog_entry,
    grade_distribution,
    normalize_day,
    related_course_codes,
    search_sections,
    status_summary,
    summarize_search_row,
    term_slug_from_donem,
)
from ninova_mcp.archive_client import (
    ItuArchiveClient,
    ItuArchiveError,
    branch_path_segment,
    term_path_segment,
)
from ninova_mcp.archive_tools import ARCHIVE_DATASET_TOOLS, PREREQUISITE_RULE_TOOL
from ninova_mcp.server import (
    LOCAL_TOOL_NAMES,
    REMOTE_TOOL_NAMES,
    STATEFUL_TOOL_NAMES,
    NinovaMcpApp,
)

BASE = "https://itu-ders.com/data"

INDEX = {
    "currentTerm": "2026-2027 Güz Dönemi",
    "currentSlug": "2026-2027-guz",
    "terms": [
        {"slug": "2026-2027-guz", "label": "2026-2027 Güz Dönemi", "source": "itu-archive"},
        {"slug": "2025-2026-yaz", "label": "2025-2026 Yaz Dönemi", "source": "itu-archive"},
        {"slug": "2025-2026-bahar", "label": "2025-2026 Bahar Dönemi", "source": "itu-archive"},
        {"slug": "2024-2025-guz", "label": "2024-2025 Güz Dönemi", "missing": True},
    ],
}

GRADES_TST = [
    {
        "code": "TST 101E", "term": "2023-2024 Güz Dönemi", "donem": "202410", "total": 20,
        "grades": {"AA": 4, "BA+": 6, "BA": 5, "FF": 5},
        "sourceUrl": "https://obs.itu.edu.tr/public/DersNotDagilimi/x?dersNo=101&yil=2024",
    },
    {
        # Published total (30) is larger than the counts (27), and there is no FF.
        "code": "TST 101E", "term": "2024-2025 Bahar Dönemi", "donem": "202520", "total": 30,
        "grades": {"AA": 9, "BB+": 9, "VF": 9},
        "sourceUrl": "https://obs.itu.edu.tr/public/DersNotDagilimi/x?dersNo=101&yil=2025",
    },
    {
        # The Turkish sibling carries the very same numbers for 202410.
        "code": "TST 101", "term": "2023-2024 Güz Dönemi", "donem": "202410", "total": 20,
        "grades": {"AA": 4, "BA+": 6, "BA": 5, "FF": 5},
        "sourceUrl": "https://obs.itu.edu.tr/public/DersNotDagilimi/x?dersNo=101&yil=2024",
    },
    {
        "code": "TST 101", "term": "2025-2026 Yaz Dönemi", "donem": "202630", "total": 3,
        "grades": {"CC": 3},
        "sourceUrl": "https://obs.itu.edu.tr/public/DersNotDagilimi/x?dersNo=101&yil=2026",
    },
]

CATALOG_TST = {
    "TST 101E": {
        "code": "TST 101E",
        "name": "Örnek Ders",
        "nameEn": "Sample Course",
        "language": "English",
        "credits": {"theory": 3, "practice": 0, "lab": 0, "local": 3, "ects": 5},
        "description": "Uydurma bir ders tanımı.",
        "outcomes": ["Birinci çıktı", "İkinci çıktı"],
        "weeklyTopics": ["Hafta 1 · Giriş", "Hafta 2 · Devam"],
        "weeklyPlan": [{"week": 1, "topic": "Giriş", "outcomes": "1"}],
        "textbooks": ["Ders Notları"],
        "sourceUrl": "https://obs.itu.edu.tr/public/DersKatalog/x?dersNo=101",
    },
    "TST 101": {
        "code": "TST 101",
        "name": "Örnek Ders",
        "nameEn": "Sample Course",
        "language": "Türkçe",
        "credits": {"theory": 3, "practice": 0, "lab": 0, "local": 3, "ects": 5},
        "description": "Uydurma bir ders tanımı.",
        "outcomes": None,
        "textbooks": [],
        "sourceUrl": "https://obs.itu.edu.tr/public/DersKatalog/x?dersNo=101",
    },
}

REVERSE = {
    "TST 101E": ["TST 202E", "ABC 301", "TST 202E"],
    "TST 101": ["TST 202"],
}

# [crn, code, name, branch, instructor, when, capacity, enrolled, level, method, programs, where]
SEARCH_ROWS = [
    ["10001", "TST 101E", "Sample Course", "TST", "Çağrı Örnekçioğlu",
     "Salı 08:30/10:29 | Perşembe 14:30/16:29", 80, 40, "LS", "Fiziksel (Yüz yüze)",
     ["TST_LS"], "AAA Z-1 | AAA 104"],
    ["10002", "TST 202E", "İşletme Yönetimi", "TST", "Işık Denemeci",
     "Çarşamba 09:30/12:29", 50, 50, "LS", "Fiziksel (Yüz yüze)", ["TST_LS"], "BBB 2"],
    ["10003", "ABC 301", "Sayısal Çözümleme", "ABC", "Çağrı Örnekçioğlu",
     "Salı 13:30/16:29", 0, 12, "LU", "Sanal (Çevrimiçi/Online)", [], "MED --"],
    # Older dumps stop after ``programs`` and may have no schedule at all.
    ["10004", "ABC 499", "Bitirme Çalışması", "ABC", "Işık Denemeci", "", 10, 3, "LS", "", []],
    ["short", "row"],
]

EXAMS_YAZ = {
    "term": "2025-2026 Yaz Dönemi",
    "slug": "2025-2026-yaz",
    "scrapedAt": "2026-08-09T10:49:10Z",
    "exams": [
        {
            "crn": "30001", "code": "TST 101E", "branch": "TST", "name": "Sample Course",
            "instructor": "Çağrı Örnekçioğlu", "type": "Final Sınavı", "place": "AAA Z-1",
            "day": "Pazartesi", "time": "09:00-11:00", "date": "10 Ağustos 2026",
        },
        {
            "crn": "30002", "code": "TST 202E", "branch": "TST", "name": "İşletme Yönetimi",
            "instructor": "Işık Denemeci", "type": "Final Sınavı", "place": "BBB 2",
            "day": "Salı", "time": "12:00-14:00", "date": "11 Ağustos 2026",
        },
        {
            "crn": "30003", "code": "ABC 301", "branch": "ABC", "name": "Sayısal Çözümleme",
            "instructor": "Çağrı Örnekçioğlu", "type": "Ek Sınav", "place": "MED",
            "day": "Salı", "time": "15:00-17:00", "date": "11 Ağustos 2026",
        },
    ],
}

STATUS = {
    "durationSec": 170,
    "failedBranches": None,
    "lastRunAt": "2026-10-05T09:06:52Z",
    "lastSuccessAt": "2026-10-05T09:06:52Z",
    "mode": "tam",
    "partial": False,
    "prevSections": 5412,
    "schemaVersion": 1,
    "sections": 5500,
    "sources": {"courses": {"provider": "OBS", "lastSuccessfulAt": "2026-10-05T09:05:14Z"}},
}

DOCUMENTS: dict[str, Any] = {
    "/index.json": INDEX,
    "/grades/TST.json": GRADES_TST,
    "/catalog/TST.json": CATALOG_TST,
    "/prereq/reverse.json": REVERSE,
    "/terms/2026-2027-guz/search.json": SEARCH_ROWS,
    "/exams/2025-2026-yaz.json": EXAMS_YAZ,
    "/status.json": STATUS,
}


class FakeArchiveSession:
    """Serves ``DOCUMENTS`` by path; any other path is a 404, like GitHub Pages."""

    def __init__(self, documents: dict[str, Any]) -> None:
        self.documents = documents
        self.requested: list[str] = []

    def request(self, method: str, url: str, **kwargs: object) -> requests.Response:
        del kwargs
        assert method == "GET"
        assert url.startswith(BASE + "/"), url
        path = url[len(BASE):]
        self.requested.append(path)
        response = requests.Response()
        response.url = url
        if path in self.documents:
            response.status_code = 200
            response._content = json.dumps(self.documents[path]).encode("utf-8")
        else:
            response.status_code = 404
            response._content = b"<html>not found</html>"
        return response


def make_app(documents: dict[str, Any] | None = None) -> tuple[NinovaMcpApp, FakeArchiveSession]:
    session = FakeArchiveSession(DOCUMENTS if documents is None else documents)
    client = ItuArchiveClient(session=session, base_url=BASE, cache_ttl=60)  # type: ignore[arg-type]
    client._min_request_interval = 0
    app = NinovaMcpApp()
    app._archive = client
    return app, session


class RegistrationTests(unittest.TestCase):
    NEW_TOOLS = [
        "archive_grade_distribution",
        "archive_course_catalog",
        "archive_course_unlocks",
        "archive_search_sections",
        "archive_exam_schedule",
        "archive_status",
    ]

    def test_all_six_are_registered_and_callable(self) -> None:
        app = NinovaMcpApp()
        self.assertEqual([tool["name"] for tool in ARCHIVE_DATASET_TOOLS], self.NEW_TOOLS)
        for name in self.NEW_TOOLS:
            self.assertIn(name, LOCAL_TOOL_NAMES)
            self.assertTrue(callable(getattr(app, name)))

    def test_read_only_and_available_remotely(self) -> None:
        for name in self.NEW_TOOLS:
            self.assertNotIn(name, STATEFUL_TOOL_NAMES)
            self.assertIn(name, REMOTE_TOOL_NAMES)

    def test_prerequisite_pointer_names_a_real_tool(self) -> None:
        self.assertIn(PREREQUISITE_RULE_TOOL, LOCAL_TOOL_NAMES)


class PathSegmentTests(unittest.TestCase):
    def test_branch_is_upper_cased(self) -> None:
        self.assertEqual(branch_path_segment(" blg "), "BLG")

    def test_bad_branches_are_rejected(self) -> None:
        for bad in ["", "B", "BLGXX", "../BLG", "BL/G", "BLG.json", "BL%2F", "BLG?x=1", "B1G", "ÇEV"]:
            with self.subTest(branch=bad), self.assertRaises(ItuArchiveError):
                branch_path_segment(bad)

    def test_term_slug_is_accepted(self) -> None:
        self.assertEqual(term_path_segment("2025-2026-GUZ"), "2025-2026-guz")

    def test_bad_term_slugs_are_rejected(self) -> None:
        for bad in ["", "2025-2026", "2025-2026-kis", "../2025-2026-guz", "2025-2026-guz/..",
                    "2025-2026-guz.json", "25-26-guz", "2025-2026-güz"]:
            with self.subTest(term=bad), self.assertRaises(ItuArchiveError):
                term_path_segment(bad)

    def test_client_never_requests_a_rejected_path(self) -> None:
        app, session = make_app()
        with self.assertRaises(ItuArchiveError):
            app.archive.get_grades("../index")
        with self.assertRaises(ItuArchiveError):
            app.archive.get_term_search("2026-2027-guz/../..")
        with self.assertRaises(ItuArchiveError):
            app.archive.get_exams("..")
        self.assertEqual(session.requested, [])


class GradeDistributionTests(unittest.TestCase):
    def test_donem_to_slug(self) -> None:
        self.assertEqual(term_slug_from_donem("202410"), "2023-2024-guz")
        self.assertEqual(term_slug_from_donem("202520"), "2024-2025-bahar")
        self.assertEqual(term_slug_from_donem(202630), "2025-2026-yaz")
        self.assertIsNone(term_slug_from_donem("202440"))
        self.assertIsNone(term_slug_from_donem("guz"))

    def test_normal_result(self) -> None:
        app, session = make_app()
        result = app.archive_grade_distribution("tst101e")
        self.assertEqual(session.requested, ["/grades/TST.json"])
        self.assertEqual(result["course_code"], "TST 101E")
        self.assertEqual(result["coverage"], "covered")
        self.assertEqual(result["term_count"], 2)
        self.assertEqual([t["term"] for t in result["terms"]], ["2024-2025-bahar", "2023-2024-guz"])
        guz = result["terms"][1]
        self.assertEqual(guz["term_label"], "2023-2024 Güz Dönemi")
        self.assertEqual(guz["total"], 20)
        self.assertFalse(guz["total_mismatch"])
        self.assertNotIn("total_difference", guz)
        self.assertEqual(guz["percentages"], {"AA": 20.0, "BA+": 30.0, "BA": 25.0, "FF": 25.0})
        self.assertIn("dersNo=101&yil=2024", guz["source_url"])
        self.assertTrue(result["untrusted_external_content"])

    def test_plus_grades_kept_and_absent_grades_not_zero_filled(self) -> None:
        app, _ = make_app()
        bahar = app.archive_grade_distribution("TST 101E")["terms"][0]
        self.assertEqual(list(bahar["grades"]), ["AA", "BB+", "VF"])
        self.assertNotIn("FF", bahar["grades"])
        self.assertNotIn("FF", bahar["percentages"])

    def test_total_mismatch_is_flagged_and_both_numbers_kept(self) -> None:
        app, _ = make_app()
        result = app.archive_grade_distribution("TST 101E")
        bahar = result["terms"][0]
        self.assertTrue(bahar["total_mismatch"])
        self.assertEqual(bahar["total"], 30)
        self.assertEqual(bahar["counted_total"], 27)
        self.assertEqual(bahar["total_difference"], 3)
        # Percentages are shares of what was actually counted.
        self.assertEqual(bahar["percentages"]["AA"], 33.3)
        self.assertEqual(result["mismatch_term_count"], 1)
        self.assertIn("total_mismatch_note", result)

    def test_language_variants_are_not_merged(self) -> None:
        app, _ = make_app()
        english = app.archive_grade_distribution("TST 101E")
        turkish = app.archive_grade_distribution("TST 101")
        self.assertEqual(english["term_count"], 2)
        self.assertEqual([t["term"] for t in turkish["terms"]], ["2025-2026-yaz", "2023-2024-guz"])
        self.assertEqual(english["related_codes"], [{"course_code": "TST 101", "term_count": 2}])
        self.assertIn("related_codes_note", english)
        # Identical numbers under both codes are pointed out, term by term.
        self.assertEqual(english["terms"][1]["same_counts_as"], ["TST 101"])
        self.assertNotIn("same_counts_as", english["terms"][0])

    def test_term_filter(self) -> None:
        app, _ = make_app()
        result = app.archive_grade_distribution("TST 101E", term="2023-2024-guz")
        self.assertEqual(result["term_count"], 1)
        self.assertEqual(result["terms"][0]["donem"], "202410")
        self.assertEqual(result["mismatch_term_count"], 0)

    def test_term_without_record_is_not_an_empty_success(self) -> None:
        app, _ = make_app()
        result = app.archive_grade_distribution("TST 101E", term="2019-2020-guz")
        self.assertEqual(result["coverage"], "term_absent_for_course")
        self.assertEqual(result["terms"], [])
        self.assertEqual(result["available_terms"], ["2024-2025-bahar", "2023-2024-guz"])

    def test_unknown_course(self) -> None:
        app, _ = make_app()
        result = app.archive_grade_distribution("TST 999")
        self.assertEqual(result["coverage"], "course_absent_from_grades")
        self.assertEqual(result["terms"], [])
        self.assertIn("2023-2024-guz", result["coverage_note"])

    def test_missing_branch_file_is_never_recorded_not_empty(self) -> None:
        app, session = make_app()
        result = app.archive_grade_distribution("QQQ 101")
        self.assertEqual(session.requested, ["/grades/QQQ.json"])
        self.assertEqual(result["coverage"], "branch_grades_missing")
        self.assertIn("kaydedilmemiş", result["coverage_note"])

    def test_input_rejection(self) -> None:
        app, session = make_app()
        with self.assertRaises(ItuArchiveError):
            app.archive_grade_distribution("not a course")
        with self.assertRaises(ItuArchiveError):
            app.archive_grade_distribution("TST 101E", term="2023-2024")
        with self.assertRaises(ItuArchiveError):
            app.archive_grade_distribution("ÇEV 101")
        self.assertEqual(session.requested, [])

    def test_pure_function_tolerates_junk_records(self) -> None:
        records = [None, "x", {"code": "TST 101E"}, {"code": "TST 101E", "total": None, "grades": {"AA": "çok"}}]
        result = grade_distribution(records, "TST 101E")
        self.assertEqual(result["term_count"], 2)
        for entry in result["terms"]:
            self.assertTrue(entry["total_mismatch"])
            self.assertEqual(entry["percentages"], {})
        self.assertEqual(result["terms"][1]["grades"], {"AA": "çok"})


class CourseCatalogTests(unittest.TestCase):
    def test_normal_result(self) -> None:
        app, session = make_app()
        result = app.archive_course_catalog("tst 101e")
        self.assertEqual(session.requested, ["/catalog/TST.json"])
        self.assertEqual(result["coverage"], "covered")
        self.assertEqual(result["course_code"], "TST 101E")
        self.assertEqual(result["course_name"], "Örnek Ders")
        self.assertEqual(result["course_name_en"], "Sample Course")
        self.assertEqual(result["language"], "English")
        self.assertEqual(result["credits"]["ects"], 5)
        self.assertEqual(result["outcomes"], ["Birinci çıktı", "İkinci çıktı"])
        self.assertEqual(result["weekly_topics"], ["Hafta 1 · Giriş", "Hafta 2 · Devam"])
        self.assertEqual(result["textbooks"], ["Ders Notları"])
        self.assertIn("dersNo=101", result["source_url"])
        self.assertEqual(result["missing_fields"], [])
        self.assertEqual(result["related_codes"], ["TST 101"])

    def test_missing_fields_are_named_not_invented(self) -> None:
        app, _ = make_app()
        result = app.archive_course_catalog("TST 101")
        self.assertIsNone(result["outcomes"])
        self.assertIsNone(result["weekly_topics"])
        self.assertEqual(result["missing_fields"], ["outcomes", "weekly_topics", "textbooks"])
        self.assertEqual(result["language"], "Türkçe")

    def test_unknown_course(self) -> None:
        app, _ = make_app()
        result = app.archive_course_catalog("TST 999")
        self.assertEqual(result["coverage"], "course_absent_from_catalog")
        self.assertNotIn("description", result)

    def test_missing_branch_file(self) -> None:
        app, _ = make_app()
        result = app.archive_course_catalog("QQQ 101")
        self.assertEqual(result["coverage"], "branch_catalog_missing")

    def test_input_rejection(self) -> None:
        app, session = make_app()
        with self.assertRaises(ItuArchiveError):
            app.archive_course_catalog("101")
        self.assertEqual(session.requested, [])

    def test_catalog_entry_passes_long_text_through(self) -> None:
        long_text = "a" * 5000
        self.assertEqual(catalog_entry({"code": "X 1", "description": long_text})["description"], long_text)


class CourseUnlocksTests(unittest.TestCase):
    def test_normal_result(self) -> None:
        app, session = make_app()
        result = app.archive_course_unlocks("TST 101E")
        self.assertEqual(session.requested, ["/prereq/reverse.json"])
        self.assertEqual(result["coverage"], "covered")
        self.assertEqual(result["unlocks"], ["ABC 301", "TST 202E"])
        self.assertEqual(result["unlock_count"], 2)
        self.assertEqual(result["depth"], "direct")

    def test_caveat_and_pointer_to_the_rule_tool(self) -> None:
        app, _ = make_app()
        result = app.archive_course_unlocks("TST 101E")
        self.assertEqual(result["rule_tool"], "explain_course_eligibility")
        self.assertIn("explain_course_eligibility", result["caveat"])
        self.assertIn("Ve/Veya", result["caveat"])
        self.assertIn("en düşük harf notu", result["caveat"])
        self.assertIn("yalnızca bir sonraki düzey", result["caveat"])

    def test_variants_are_not_merged(self) -> None:
        app, _ = make_app()
        result = app.archive_course_unlocks("TST 101")
        self.assertEqual(result["unlocks"], ["TST 202"])
        self.assertEqual(result["related_codes"], ["TST 101E"])

    def test_unknown_course(self) -> None:
        app, _ = make_app()
        result = app.archive_course_unlocks("TST 999")
        self.assertEqual(result["coverage"], "not_listed_as_prerequisite")
        self.assertEqual(result["unlocks"], [])
        self.assertIn("coverage_note", result)
        self.assertIn("caveat", result)

    def test_never_fetches_the_full_graph(self) -> None:
        app, session = make_app()
        app.archive_course_unlocks("TST 101E")
        self.assertNotIn("/prereq/graph.json", session.requested)

    def test_input_rejection(self) -> None:
        app, session = make_app()
        with self.assertRaises(ItuArchiveError):
            app.archive_course_unlocks("")
        self.assertEqual(session.requested, [])


class SearchSectionsTests(unittest.TestCase):
    def test_column_mapping(self) -> None:
        self.assertEqual(len(SEARCH_ROW_FIELDS), 12)
        row = summarize_search_row(SEARCH_ROWS[0])
        self.assertEqual(row["crn"], "10001")
        self.assertEqual(row["course_code"], "TST 101E")
        self.assertEqual(row["course_name"], "Sample Course")
        self.assertEqual(row["branch"], "TST")
        self.assertEqual(row["instructor"], "Çağrı Örnekçioğlu")
        self.assertEqual(row["capacity"], 80)
        self.assertEqual(row["enrolled"], 40)
        self.assertEqual(row["fill_ratio"], 0.5)
        self.assertEqual(row["level"], "LS")
        self.assertEqual(row["method"], "Fiziksel (Yüz yüze)")
        self.assertEqual(row["programs"], ["TST_LS"])
        self.assertEqual(row["sessions"], [
            {"day": "Salı", "time": "08:30/10:29", "location": "AAA Z-1"},
            {"day": "Perşembe", "time": "14:30/16:29", "location": "AAA 104"},
        ])

    def test_eleven_column_row_from_an_older_dump(self) -> None:
        row = summarize_search_row(SEARCH_ROWS[3])
        self.assertEqual(row["sessions"], [])
        self.assertIsNone(row["method"])
        self.assertEqual(row["programs"], [])

    def test_defaults_to_current_term(self) -> None:
        app, session = make_app()
        result = app.archive_search_sections(course_code="TST 101E")
        self.assertIn("/terms/2026-2027-guz/search.json", session.requested)
        self.assertEqual(result["term"], "2026-2027-guz")
        self.assertTrue(result["term_defaulted"])
        self.assertEqual(result["coverage"], "covered")
        self.assertEqual(result["term_section_count"], 4)
        self.assertEqual(result["match_count"], 1)
        self.assertFalse(result["truncated"])
        self.assertEqual(result["sections"][0]["crn"], "10001")

    def test_code_fragment_ignores_case_and_spaces(self) -> None:
        app, _ = make_app()
        for fragment in ["tst", "TST 2", "tst202", "202e"]:
            with self.subTest(fragment=fragment):
                crns = [s["crn"] for s in app.archive_search_sections(course_code=fragment)["sections"]]
                self.assertIn("10002", crns)

    def test_turkish_characters_in_course_name(self) -> None:
        app, _ = make_app()
        for query in ["isletme", "İŞLETME", "işletme yönetimi", "Isletme"]:
            with self.subTest(query=query):
                result = app.archive_search_sections(course_name=query)
                self.assertEqual([s["crn"] for s in result["sections"]], ["10002"])
        result = app.archive_search_sections(course_name="sayisal cozumleme")
        self.assertEqual([s["crn"] for s in result["sections"]], ["10003"])

    def test_turkish_characters_in_instructor(self) -> None:
        app, _ = make_app()
        for query in ["cagri ornekcioglu", "ÇAĞRI", "örnekçioğlu"]:
            with self.subTest(query=query):
                result = app.archive_search_sections(instructor=query)
                self.assertEqual([s["crn"] for s in result["sections"]], ["10001", "10003"])
        # Dotless ı and dotted i fold together, in both directions.
        for query in ["isik", "IŞIK", "ışık"]:
            with self.subTest(query=query):
                result = app.archive_search_sections(instructor=query)
                self.assertEqual([s["crn"] for s in result["sections"]], ["10002", "10004"])

    def test_day_filter_matches_any_session(self) -> None:
        app, _ = make_app()
        for query in ["Salı", "sali", "SALI", "tuesday"]:
            with self.subTest(day=query):
                result = app.archive_search_sections(day=query)
                self.assertEqual([s["crn"] for s in result["sections"]], ["10001", "10003"])
        result = app.archive_search_sections(day="persembe")
        self.assertEqual([s["crn"] for s in result["sections"]], ["10001"])
        result = app.archive_search_sections(day="Çarşamba")
        self.assertEqual([s["crn"] for s in result["sections"]], ["10002"])

    def test_pazar_does_not_match_pazartesi(self) -> None:
        self.assertEqual(normalize_day("Pazar"), "pazar")
        rows = [["1", "TST 1", "x", "TST", "y", "Pazartesi 08:30/10:29", 1, 1]]
        self.assertEqual(search_sections(rows, day="pazar")["match_count"], 0)
        self.assertEqual(search_sections(rows, day="pazartesi")["match_count"], 1)

    def test_filters_combine_with_and(self) -> None:
        app, _ = make_app()
        result = app.archive_search_sections(instructor="örnekçioğlu", day="salı", course_code="abc")
        self.assertEqual([s["crn"] for s in result["sections"]], ["10003"])
        self.assertEqual(result["filters"], {"instructor": "örnekçioğlu", "day": "salı", "course_code": "abc"})

    def test_limit_reports_total_and_truncation(self) -> None:
        app, _ = make_app()
        result = app.archive_search_sections(course_code="T", limit=1)
        self.assertEqual(result["match_count"], 2)
        self.assertEqual(len(result["sections"]), 1)
        self.assertTrue(result["truncated"])

    def test_no_match_in_a_covered_term(self) -> None:
        app, _ = make_app()
        result = app.archive_search_sections(course_name="zzzqqq")
        self.assertEqual(result["coverage"], "covered")
        self.assertEqual(result["match_count"], 0)
        self.assertIn("filtreye", result["coverage_note"])

    def test_missing_search_file(self) -> None:
        app, session = make_app()
        result = app.archive_search_sections(term="2025-2026-bahar", course_code="TST")
        self.assertIn("/terms/2025-2026-bahar/search.json", session.requested)
        self.assertEqual(result["coverage"], "search_index_missing")
        self.assertEqual(result["sections"], [])
        self.assertIn("kaydedilmemiş", result["coverage_note"])

    def test_term_marked_missing_in_the_index(self) -> None:
        app, session = make_app()
        result = app.archive_search_sections(term="2024-2025-guz", course_code="TST")
        self.assertEqual(result["coverage"], "term_missing")
        self.assertNotIn("/terms/2024-2025-guz/search.json", session.requested)

    def test_input_rejection(self) -> None:
        app, session = make_app()
        with self.assertRaises(ItuArchiveError):
            app.archive_search_sections()
        with self.assertRaises(ItuArchiveError):
            app.archive_search_sections(course_code="   ")
        with self.assertRaises(ItuArchiveError):
            app.archive_search_sections(term="../../index", course_code="TST")
        with self.assertRaises(ItuArchiveError):
            app.archive_search_sections(term="1999-2000-guz", course_code="TST")
        with self.assertRaises(ItuArchiveError):
            app.archive_search_sections(day="someday")
        self.assertFalse([path for path in session.requested if "search.json" in path and "2026" not in path])


class ExamScheduleTests(unittest.TestCase):
    def test_normal_result(self) -> None:
        app, session = make_app()
        result = app.archive_exam_schedule("2025-2026-yaz")
        self.assertIn("/exams/2025-2026-yaz.json", session.requested)
        self.assertEqual(result["coverage"], "covered")
        self.assertEqual(result["exam_count"], 3)
        self.assertEqual(result["match_count"], 3)
        self.assertEqual(result["scraped_at"], "2026-08-09T10:49:10Z")
        self.assertEqual(result["exams"][0], {
            "crn": "30001", "course_code": "TST 101E", "course_name": "Sample Course",
            "branch": "TST", "instructor": "Çağrı Örnekçioğlu", "exam_type": "Final Sınavı",
            "date": "10 Ağustos 2026", "day": "Pazartesi", "time": "09:00-11:00", "place": "AAA Z-1",
        })

    def test_branch_filter(self) -> None:
        app, _ = make_app()
        result = app.archive_exam_schedule("2025-2026-yaz", branch="tst")
        self.assertEqual(result["branch"], "TST")
        self.assertEqual([e["crn"] for e in result["exams"]], ["30001", "30002"])

    def test_course_code_filter_is_exact(self) -> None:
        app, _ = make_app()
        result = app.archive_exam_schedule("2025-2026-yaz", course_code="tst101e")
        self.assertEqual([e["crn"] for e in result["exams"]], ["30001"])
        result = app.archive_exam_schedule("2025-2026-yaz", course_code="TST 101")
        self.assertEqual(result["coverage"], "covered")
        self.assertEqual(result["match_count"], 0)
        self.assertIn("filtreye", result["coverage_note"])

    def test_limit(self) -> None:
        app, _ = make_app()
        result = app.archive_exam_schedule("2025-2026-yaz", limit=2)
        self.assertEqual(result["match_count"], 3)
        self.assertEqual(len(result["exams"]), 2)
        self.assertTrue(result["truncated"])

    def test_missing_file_means_never_recorded(self) -> None:
        app, session = make_app()
        result = app.archive_exam_schedule("2026-2027-guz", branch="TST")
        self.assertIn("/exams/2026-2027-guz.json", session.requested)
        self.assertEqual(result["coverage"], "exam_schedule_not_recorded")
        self.assertIsNone(result["exam_count"])
        self.assertEqual(result["exams"], [])
        self.assertIn("hiç kaydedilmemiş", result["coverage_note"])

    def test_input_rejection(self) -> None:
        app, session = make_app()
        with self.assertRaises(ItuArchiveError):
            app.archive_exam_schedule("2025-2026-yaz/../..")
        with self.assertRaises(ItuArchiveError):
            app.archive_exam_schedule("1999-2000-guz")
        with self.assertRaises(ItuArchiveError):
            app.archive_exam_schedule("2025-2026-yaz", branch="../TST")
        with self.assertRaises(ItuArchiveError):
            app.archive_exam_schedule("2025-2026-yaz", course_code="nonsense")
        self.assertFalse([path for path in session.requested if path.startswith("/exams/")])


class ArchiveStatusTests(unittest.TestCase):
    def test_normal_result(self) -> None:
        app, session = make_app()
        result = app.archive_status()
        self.assertEqual(session.requested, ["/status.json"])
        self.assertEqual(result["last_run_at"], "2026-10-05T09:06:52Z")
        self.assertEqual(result["last_success_at"], "2026-10-05T09:06:52Z")
        self.assertEqual(result["sections"], 5500)
        self.assertEqual(result["previous_sections"], 5412)
        self.assertIs(result["partial"], False)
        self.assertEqual(result["failed_branches"], [])
        self.assertIsInstance(result["data_age_days"], float)

    def test_data_age_is_computed_from_last_success(self) -> None:
        now = datetime(2026, 10, 11, 21, 6, 52, tzinfo=timezone.utc)
        self.assertEqual(status_summary(STATUS, now=now)["data_age_days"], 6.5)

    def test_old_data_is_reported_without_warning_or_error(self) -> None:
        stale = dict(STATUS, lastSuccessAt="2025-01-01T00:00:00Z", partial=True, failedBranches=["TST"])
        app, _ = make_app(dict(DOCUMENTS, **{"/status.json": stale}))
        result = app.archive_status()
        self.assertGreater(result["data_age_days"], 300)
        self.assertEqual(result["failed_branches"], ["TST"])
        self.assertIs(result["partial"], True)
        for key in result:
            self.assertNotIn("warning", key)
            self.assertNotIn("stale", key)
        self.assertIn("hata", result["note"])
        self.assertIn("değildir", result["note"])

    def test_unparseable_timestamp_gives_no_age(self) -> None:
        self.assertIsNone(status_summary({"lastSuccessAt": "dün"})["data_age_days"])
        self.assertIsNone(status_summary({})["data_age_days"])

    def test_missing_status_file_is_an_error(self) -> None:
        app, _ = make_app({})
        with self.assertRaises(ItuArchiveError):
            app.archive_status()


class RelatedCodeTests(unittest.TestCase):
    def test_same_number_different_suffix(self) -> None:
        known = ["BLG 212", "BLG 212E", "BLG 2120", "BLG 213E", "MAT 212", "junk"]
        self.assertEqual(related_course_codes("BLG 212E", known), ["BLG 212"])
        self.assertEqual(related_course_codes("BLG 212", known), ["BLG 212E"])
        self.assertEqual(related_course_codes("nonsense", known), [])


if __name__ == "__main__":
    unittest.main()
