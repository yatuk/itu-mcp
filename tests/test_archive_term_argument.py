"""Every archive tool reads its term argument through one parser.

The slug, the OBS term id (text or integer) and the label the archive itself
prints are all accepted; a term next to a course code in ``archive_fill_rate``
is a filter, not a silently ignored argument. Synthetic fixtures only.
"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any

from mcp.server.fastmcp import FastMCP
from test_archive_datasets import BASE, FakeArchiveSession

from ninova_mcp.archive import TERM_FORMS_HELP, nearest_terms, parse_term, resolve_term
from ninova_mcp.archive_client import ItuArchiveClient, ItuArchiveError
from ninova_mcp.server import NinovaMcpApp, register_tools
from ninova_mcp.tool_registry import TOOLS

INDEX = {
    "currentTerm": "2026-2027 Güz Dönemi",
    "currentSlug": "2026-2027-guz",
    "stats": {"capacity": 100, "enrolled": 60},
    "terms": [
        {"slug": "2026-2027-guz", "label": "2026-2027 Güz Dönemi", "source": "itu-archive"},
        {"slug": "2025-2026-yaz", "label": "2025-2026 Yaz Dönemi", "source": "itu-archive"},
        {"slug": "2025-2026-bahar", "label": "2025-2026 Bahar Dönemi", "source": "itu-archive"},
        {"slug": "2025-2026-guz", "label": "2025-2026 Güz Dönemi", "source": "itu-archive"},
        {"slug": "2024-2025-guz", "label": "2024-2025 Güz Dönemi", "missing": True},
        {"slug": "2023-2024-guz", "label": "2023-2024 Güz Dönemi", "source": "itu-archive"},
    ],
}

HISTORY = {
    "TST 210E": {
        "code": "TST 210E",
        "name": "Sample Analysis",
        "rows": [
            ["2026-2027-guz", "Ada Kurgu", 100, 60, "Salı"],
            ["2025-2026-bahar", "Ada Kurgu", 100, 100, "Salı"],
            ["2025-2026-bahar", "Mert Sahte", 50, 25, "Cuma"],
            ["2023-2024-guz", "Mert Sahte", 80, 20, "Cuma"],
        ],
    },
}

SECTION = {
    "crn": "40001", "code": "TST 210E", "branch": "TST", "level": "LS", "name": "Sample Analysis",
    "method": "", "instructor": "Ada Kurgu", "buildings": ["AAA"], "days": ["Salı"],
    "times": ["08:30/10:29"], "rooms": ["1"], "capacity": 100, "enrolled": 60, "reserved": "",
    "programs": ["TST_LS"], "prereq": "", "classReq": "",
}

META = {"slug": "x", "sections": 1, "courses": 1, "branches": [{"code": "TST", "levels": ["LS"], "sections": 1}]}

DOCUMENTS: dict[str, Any] = {
    "/index.json": INDEX,
    "/history/courses/TST.json": HISTORY,
    "/terms/2026-2027-guz/meta.json": META,
    "/terms/2026-2027-guz/branches/TST.json": [SECTION],
    "/terms/2025-2026-bahar/meta.json": META,
    "/terms/2025-2026-bahar/branches/TST.json": [dict(SECTION, crn="40002", enrolled=100)],
    "/terms/2026-2027-guz/search.json": [
        ["40001", "TST 210E", "Sample Analysis", "TST", "Ada Kurgu", "Salı 08:30/10:29", 100, 60,
         "LS", "", ["TST_LS"], "AAA 1"],
    ],
    "/quota/2026-2027-guz.json": {
        "slug": "2026-2027-guz", "snapshots": 3, "first": "2026-09-01T00:30:46Z",
        "last": "2026-10-05T09:43:32Z",
        "courses": [{"crn": "40001", "capacity": 100, "enrolled": 60}],
    },
    "/exams/2025-2026-yaz.json": {"term": "2025-2026 Yaz Dönemi", "exams": []},
    "/grades/TST.json": [
        {"code": "TST 210E", "term": "2025-2026 Yaz Dönemi", "donem": "202630", "total": 3,
         "grades": {"CC": 3}},
    ],
}


def make_app() -> tuple[NinovaMcpApp, FakeArchiveSession]:
    session = FakeArchiveSession(DOCUMENTS)
    client = ItuArchiveClient(session=session, base_url=BASE, cache_ttl=60)  # type: ignore[arg-type]
    client._min_request_interval = 0
    app = NinovaMcpApp()
    app._archive = client
    return app, session


class ParseTermTests(unittest.TestCase):
    ACCEPTED = {
        "2025-2026-guz": "2025-2026-guz",
        " 2025-2026-GUZ ": "2025-2026-guz",
        "202610": "2025-2026-guz",
        202610: "2025-2026-guz",
        202620: "2025-2026-bahar",
        "202630": "2025-2026-yaz",
        "202710": "2026-2027-guz",
        "2026-2027 Güz Dönemi": "2026-2027-guz",
        "2025-2026 Güz": "2025-2026-guz",
        "2025-2026 guz donemi": "2025-2026-guz",
        "2025-2026 GÜZ DÖNEMİ": "2025-2026-guz",
        "2025-2026 Fall": "2025-2026-guz",
        "2025-2026 fall semester": "2025-2026-guz",
        "2025-2026 Spring Semester": "2025-2026-bahar",
        "2025-2026 Bahar Dönemi": "2025-2026-bahar",
        "2025-2026 Yaz": "2025-2026-yaz",
        "2025-2026 Summer": "2025-2026-yaz",
        "2025/2026 Güz": "2025-2026-guz",
        "2025-26 güz": "2025-2026-guz",
        "Güz 2025-2026": "2025-2026-guz",
        "Fall 2025-2026": "2025-2026-guz",
    }
    REJECTED = [
        None, "", "   ", True, 3.5, "güz", "2025", "2025-2026", "2025-2027-guz", "2025-2026-kis",
        "202640", "20261", "2026101", 20261, "../2025-2026-guz", "2025-2026-guz/..",
        "2025-2026-guz.json", "geçen dönem", "2025-2026 Güz Dönemi ve sonrası",
    ]

    def test_accepted_spellings(self) -> None:
        for raw, slug in self.ACCEPTED.items():
            with self.subTest(term=raw):
                self.assertEqual(parse_term(raw), slug)

    def test_everything_else_is_refused_with_the_accepted_forms(self) -> None:
        for raw in self.REJECTED:
            with self.subTest(term=raw):
                with self.assertRaises(ValueError) as caught:
                    parse_term(raw)
                self.assertIn("2025-2026-guz", str(caught.exception))
                self.assertIn("202610", str(caught.exception))
                self.assertIn(TERM_FORMS_HELP, str(caught.exception))

    def test_nearest_terms_are_the_closest_in_time(self) -> None:
        slugs = [entry["slug"] for entry in INDEX["terms"]]
        self.assertEqual(
            nearest_terms(slugs, "2024-2025-bahar", limit=3),
            ["2025-2026-bahar", "2025-2026-guz", "2024-2025-guz"],
        )
        self.assertEqual(nearest_terms(slugs, None, limit=2), ["2026-2027-guz", "2025-2026-yaz"])

    def test_unknown_term_names_the_nearest_ones_and_the_forms(self) -> None:
        with self.assertRaises(ValueError) as caught:
            resolve_term("2019-2020 Güz", INDEX["terms"])
        message = str(caught.exception)
        self.assertIn("Dönem arşivde yok", message)
        self.assertIn("2019-2020-guz olarak okundu", message)
        self.assertIn("2023-2024-guz", message)
        self.assertIn(TERM_FORMS_HELP, message)

    def test_unparseable_term_names_terms_too(self) -> None:
        with self.assertRaises(ValueError) as caught:
            resolve_term("bu dönem", INDEX["terms"])
        self.assertIn("en yeni dönemler: 2026-2027-guz", str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            resolve_term("2024 kış", INDEX["terms"])
        self.assertIn("en yakın dönemler", str(caught.exception))
        self.assertIn("2024-2025-guz", str(caught.exception))


class ToolTermTests(unittest.TestCase):
    def test_term_sections_accepts_the_label_the_archive_prints(self) -> None:
        app, _ = make_app()
        label = app.archive_list_terms()["current_term"]
        result = app.archive_term_sections(term=label, branch="TST")
        self.assertEqual(result["term"], "2026-2027-guz")
        self.assertEqual(result["match_count"], 1)

    def test_term_sections_accepts_the_obs_code_as_integer(self) -> None:
        app, session = make_app()
        result = app.archive_term_sections(term=202710, branch="TST")
        self.assertEqual(result["term"], "2026-2027-guz")
        self.assertIn("/terms/2026-2027-guz/branches/TST.json", session.requested)

    def test_list_branches_accepts_other_spellings(self) -> None:
        app, _ = make_app()
        for raw in ("2026-2027-guz", "202710", 202710, "2026-2027 Fall", "2026-2027 güz dönemi"):
            with self.subTest(term=raw):
                self.assertEqual(app.archive_list_branches(raw)["term"], "2026-2027-guz")

    def test_compare_terms_accepts_mixed_spellings(self) -> None:
        app, _ = make_app()
        result = app.archive_compare_terms("TST 210E", "2025-2026 Bahar Dönemi", 202710)
        self.assertEqual((result["term_a"], result["term_b"]), ("2025-2026-bahar", "2026-2027-guz"))
        self.assertEqual(result["diff"]["second"]["total_enrolled"], 60)

    def test_search_sections_and_exam_schedule_and_grades(self) -> None:
        app, _ = make_app()
        self.assertEqual(
            app.archive_search_sections(term="2026-2027 Güz Dönemi", course_code="TST")["term"],
            "2026-2027-guz",
        )
        self.assertEqual(app.archive_exam_schedule(term=202630)["term"], "2025-2026-yaz")
        grades = app.archive_grade_distribution("TST 210E", term="2025-2026 Yaz Dönemi")
        self.assertEqual(grades["term_filter"], "2025-2026-yaz")
        self.assertEqual(grades["term_count"], 1)
        self.assertEqual(app.archive_grade_distribution("TST 210E", term=202630)["term_count"], 1)

    def test_unknown_term_error_is_the_same_everywhere(self) -> None:
        app, session = make_app()
        calls = [
            lambda: app.archive_term_sections(term="2019-2020-guz", branch="TST"),
            lambda: app.archive_list_branches("2019-2020 Güz"),
            lambda: app.archive_compare_terms("TST 210E", 202010, "2026-2027-guz"),
            lambda: app.archive_search_sections(term="2019-2020-guz", course_code="TST"),
            lambda: app.archive_exam_schedule(term="2019-2020-guz"),
            lambda: app.archive_fill_rate(course_code="TST 210E", term="2019-2020-guz"),
            lambda: app.archive_fill_rate(crn="40001", term=202010),
        ]
        for index, call in enumerate(calls):
            with self.subTest(call=index):
                with self.assertRaises(ItuArchiveError) as caught:
                    call()
                self.assertIn("Dönem arşivde yok", str(caught.exception))
                self.assertIn("2023-2024-guz", str(caught.exception))
                self.assertIn("202610", str(caught.exception))
        self.assertFalse([path for path in session.requested if "2019-2020" in path])

    def test_garbage_term_is_refused_before_any_path_is_built(self) -> None:
        app, session = make_app()
        for bad in ("../index", "2026-2027-guz/../..", "guz", "202640"):
            with self.subTest(term=bad), self.assertRaises(ItuArchiveError) as caught:
                app.archive_term_sections(term=bad, branch="TST")
            self.assertIn("Kabul edilen biçimler", str(caught.exception))
        self.assertEqual([path for path in session.requested if path != "/index.json"], [])


class FillRateTermTests(unittest.TestCase):
    def test_course_code_with_a_term_is_filtered_to_that_term(self) -> None:
        app, _ = make_app()
        for raw in ("2025-2026-bahar", "202620", 202620, "2025-2026 Bahar Dönemi", "2025-2026 Spring"):
            with self.subTest(term=raw):
                result = app.archive_fill_rate(course_code="TST 210E", term=raw)
                self.assertEqual(result["term_filter"], "2025-2026-bahar")
                self.assertEqual([o["term"] for o in result["offerings"]], ["2025-2026-bahar"])
                self.assertEqual(result["sections_with_quota_data"], 2)
                self.assertEqual(result["average_fill_ratio"], 0.75)
                self.assertIn("2025-2026-bahar", result["term_filter_note"])
                # The whole history is still described, and labelled as such.
                self.assertEqual(result["terms_offered"], 3)

    def test_without_a_term_every_term_is_returned(self) -> None:
        app, _ = make_app()
        result = app.archive_fill_rate(course_code="TST 210E")
        self.assertIsNone(result["term_filter"])
        self.assertNotIn("term_filter_note", result)
        self.assertEqual(len(result["offerings"]), 3)

    def test_term_in_which_the_course_has_no_record(self) -> None:
        app, _ = make_app()
        result = app.archive_fill_rate(course_code="TST 210E", term="2025-2026 Yaz")
        self.assertEqual(result["offerings"], [])
        self.assertEqual(result["coverage"], "term_absent_for_course")
        self.assertIsNone(result["average_fill_ratio"])
        self.assertEqual(
            result["available_terms"], ["2026-2027-guz", "2025-2026-bahar", "2023-2024-guz"]
        )

    def test_crn_as_integer_and_term_as_obs_code(self) -> None:
        app, session = make_app()
        result = app.archive_fill_rate(crn=40001, term=202710)
        self.assertEqual(result["crn"], "40001")
        self.assertEqual(result["term"], "2026-2027-guz")
        self.assertEqual(result["quota"]["enrolled"], 60)
        self.assertIn("/quota/2026-2027-guz.json", session.requested)

    def test_crn_without_a_term_uses_the_current_term(self) -> None:
        app, _ = make_app()
        self.assertEqual(app.archive_fill_rate(crn="40001")["term"], "2026-2027-guz")


class SchemaTests(unittest.TestCase):
    """Term and CRN parameters take a JSON integer as well as a string."""

    # Every archive tool parameter that carries a term or a CRN.
    EXPECTED = {
        "archive_term_sections": {"term"},
        "archive_fill_rate": {"crn", "term"},
        "archive_list_branches": {"term"},
        "archive_compare_terms": {"term_a", "term_b"},
        "archive_grade_distribution": {"term"},
        "archive_search_sections": {"term"},
        "archive_exam_schedule": {"term"},
    }

    @staticmethod
    def is_term_or_crn(name: str) -> bool:
        return name == "crn" or name == "term" or name.startswith("term_")

    def archive_tools(self) -> dict[str, dict[str, Any]]:
        return {tool["name"]: tool for tool in TOOLS if tool["name"].startswith("archive_")}

    def test_no_archive_tool_with_a_term_or_crn_is_missed(self) -> None:
        found = {
            name: {prop for prop in tool["inputSchema"]["properties"] if self.is_term_or_crn(prop)}
            for name, tool in self.archive_tools().items()
        }
        self.assertEqual({name: props for name, props in found.items() if props}, self.EXPECTED)

    def test_declared_metadata_accepts_string_and_integer(self) -> None:
        for name, tool in self.archive_tools().items():
            for prop, schema in tool["inputSchema"]["properties"].items():
                if self.is_term_or_crn(prop):
                    with self.subTest(tool=name, parameter=prop):
                        self.assertEqual(set(schema["type"]), {"string", "integer"})

    def test_published_schema_accepts_string_and_integer(self) -> None:
        names = sorted(self.archive_tools())
        server = FastMCP("archive-term-types")
        register_tools(server, NinovaMcpApp.__new__(NinovaMcpApp), names)
        published = {tool.name: tool for tool in asyncio.run(server.list_tools())}
        self.assertEqual(set(published), set(names))
        for name, tool in published.items():
            for prop, schema in tool.inputSchema["properties"].items():
                if self.is_term_or_crn(prop):
                    with self.subTest(tool=name, parameter=prop):
                        options = {option.get("type") for option in schema.get("anyOf", [])}
                        self.assertLessEqual({"string", "integer"}, options)

    def test_published_and_declared_parameters_match(self) -> None:
        names = sorted(self.archive_tools())
        server = FastMCP("archive-parameters")
        register_tools(server, NinovaMcpApp.__new__(NinovaMcpApp), names)
        declared = self.archive_tools()
        for tool in asyncio.run(server.list_tools()):
            with self.subTest(tool=tool.name):
                self.assertEqual(
                    set(tool.inputSchema["properties"]),
                    set(declared[tool.name]["inputSchema"]["properties"]),
                )


if __name__ == "__main__":
    unittest.main()
