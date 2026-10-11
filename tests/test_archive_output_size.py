"""Archive results carry data; what never changes is said once, in the description.

Grade distributions return counts for the most recent terms and add
percentages only on request. Constant explanatory notes are not repeated on
every call. The fixtures are synthetic; every name is invented.
"""

from __future__ import annotations

import unittest
from typing import Any

from test_archive_datasets import BASE, FakeArchiveSession

from ninova_mcp.archive_client import ItuArchiveClient, ItuArchiveError
from ninova_mcp.server import TOOLS, NinovaMcpApp

# OBS term ids, newest first: 2026 Yaz back to 2023-2024 Güz.
DONEMS = ["202630", "202620", "202610", "202530", "202520", "202510", "202430", "202420", "202410"]
SLUGS = [
    "2025-2026-yaz", "2025-2026-bahar", "2025-2026-guz", "2024-2025-yaz", "2024-2025-bahar",
    "2024-2025-guz", "2023-2024-yaz", "2023-2024-bahar", "2023-2024-guz",
]


def grade_record(donem: str, total: int | None = None) -> dict[str, Any]:
    return {
        "code": "TST 101E", "term": f"dönem {donem}", "donem": donem,
        "total": 20 if total is None else total,
        "grades": {"AA": 4, "BA": 6, "CC": 5, "FF": 5},
        "sourceUrl": f"https://obs.itu.edu.tr/public/DersNotDagilimi/x?donem={donem}",
    }


GRADES = [grade_record(donem) for donem in reversed(DONEMS)]

DOCUMENTS: dict[str, Any] = {
    "/index.json": {
        "currentTerm": "2026-2027 Güz Dönemi",
        "currentSlug": "2026-2027-guz",
        "terms": [{"slug": "2026-2027-guz", "label": "2026-2027 Güz Dönemi", "source": "itu-archive"}],
    },
    "/grades/TST.json": GRADES,
    "/status.json": {"lastRunAt": "2026-10-05T09:05:14Z", "lastSuccessAt": "2026-10-05T09:05:14Z"},
    "/prereq/reverse.json": {"TST 101E": ["TST 202E"]},
}


def make_app(documents: dict[str, Any] | None = None) -> NinovaMcpApp:
    session = FakeArchiveSession(DOCUMENTS if documents is None else documents)
    client = ItuArchiveClient(session=session, base_url=BASE, cache_ttl=60)  # type: ignore[arg-type]
    client._min_request_interval = 0
    app = NinovaMcpApp()
    app._archive = client
    return app


def tool(name: str) -> dict[str, Any]:
    return next(item for item in TOOLS if item["name"] == name)


class GradeDistributionSizeTests(unittest.TestCase):
    def test_default_returns_the_six_most_recent_terms(self) -> None:
        result = make_app().archive_grade_distribution("TST 101E")
        self.assertEqual([entry["term"] for entry in result["terms"]], SLUGS[:6])
        self.assertEqual(result["term_count"], 6)
        self.assertEqual(result["total_term_count"], 9)
        self.assertTrue(result["truncated"])
        # Every term is still named, so the caller knows what to ask for.
        self.assertEqual(result["available_terms"], SLUGS)

    def test_limit_terms_returns_more(self) -> None:
        result = make_app().archive_grade_distribution("TST 101E", limit_terms=30)
        self.assertEqual([entry["term"] for entry in result["terms"]], SLUGS)
        self.assertEqual(result["total_term_count"], 9)
        self.assertFalse(result["truncated"])

    def test_limit_terms_can_narrow(self) -> None:
        result = make_app().archive_grade_distribution("TST 101E", limit_terms=2)
        self.assertEqual([entry["term"] for entry in result["terms"]], SLUGS[:2])
        self.assertTrue(result["truncated"])

    def test_an_older_term_is_reached_with_the_term_filter(self) -> None:
        result = make_app().archive_grade_distribution("TST 101E", term="2023-2024-guz")
        self.assertEqual([entry["term"] for entry in result["terms"]], ["2023-2024-guz"])
        self.assertEqual(result["total_term_count"], 1)
        self.assertFalse(result["truncated"])

    def test_counts_by_default_percentages_on_request(self) -> None:
        app = make_app()
        plain = app.archive_grade_distribution("TST 101E")
        for entry in plain["terms"]:
            self.assertEqual(entry["grades"], {"AA": 4, "BA": 6, "CC": 5, "FF": 5})
            self.assertNotIn("percentages", entry)
        asked = app.archive_grade_distribution("TST 101E", include_percentages=True)
        for entry in asked["terms"]:
            self.assertEqual(entry["percentages"], {"AA": 20.0, "BA": 30.0, "CC": 25.0, "FF": 25.0})

    def test_no_static_note_in_the_result(self) -> None:
        result = make_app().archive_grade_distribution("TST 101E")
        self.assertEqual(sorted(key for key in result if key.endswith("note")), [])

    def test_mismatch_note_only_when_there_is_a_mismatch(self) -> None:
        records = [grade_record(DONEMS[0], total=25), *GRADES[:-1]]
        result = make_app({**DOCUMENTS, "/grades/TST.json": records}).archive_grade_distribution("TST 101E")
        self.assertEqual(result["mismatch_term_count"], 1)
        self.assertIn("1 dönemde", result["total_mismatch_note"])

    def test_missing_branch_file_has_the_same_shape(self) -> None:
        result = make_app().archive_grade_distribution("QQQ 101")
        self.assertEqual(result["coverage"], "branch_grades_missing")
        self.assertEqual((result["term_count"], result["total_term_count"], result["truncated"]), (0, 0, False))

    def test_bad_limit_is_refused(self) -> None:
        with self.assertRaises(ItuArchiveError):
            make_app().archive_grade_distribution("TST 101E", limit_terms="çok")  # type: ignore[arg-type]

    def test_new_arguments_are_typed_and_described(self) -> None:
        schema = tool("archive_grade_distribution")["inputSchema"]["properties"]
        self.assertEqual(schema["include_percentages"]["type"], "boolean")
        self.assertIs(schema["include_percentages"]["default"], False)
        self.assertTrue(schema["include_percentages"]["description"])
        self.assertEqual(schema["limit_terms"]["type"], "integer")
        self.assertEqual(schema["limit_terms"]["default"], 6)
        self.assertEqual(schema["limit_terms"]["minimum"], 1)
        self.assertTrue(schema["limit_terms"]["description"])

    def test_description_says_what_the_notes_used_to_say(self) -> None:
        description = tool("archive_grade_distribution")["description"]
        for phrase in ("include_percentages", "limit_terms", "total_term_count", "counted_total", "not zero"):
            self.assertIn(phrase, description)


class StaticNoteTests(unittest.TestCase):
    """Notes that read the same on every call are gone from the results."""

    def test_list_terms_has_no_constant_note(self) -> None:
        result = make_app().archive_list_terms()
        self.assertNotIn("note", result)
        self.assertIn("OBS publishes only the active term", tool("archive_list_terms")["description"])

    def test_status_has_no_constant_note(self) -> None:
        self.assertNotIn("note", make_app().archive_status())

    def test_unlocks_has_no_constant_caveat(self) -> None:
        result = make_app().archive_course_unlocks("TST 101E")
        self.assertNotIn("caveat", result)
        self.assertEqual(result["unlocks"], ["TST 202E"])


if __name__ == "__main__":
    unittest.main()
