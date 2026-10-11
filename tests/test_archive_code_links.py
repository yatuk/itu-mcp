"""Archive: the same course under another department code, and name search hints.

The index and the history entries are invented. The archive client is a fake,
so no request leaves the process.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from typing import Any
from unittest.mock import patch

from ninova_mcp.archive import course_search_hint, same_course_other_branches, search_courses
from ninova_mcp.archive_client import ItuArchiveError
from ninova_mcp.server import NinovaMcpApp

# [code, name, branch, term_count]
CODES = [
    ["QAB 411E", "Gizmo Engineering", "QAB", 11],
    ["WYX 411E", "Gizmo  engineering.", "WYX", 1],
    ["ZED 411E", "Sprocket Analytics", "ZED", 3],
    ["QAB 411", "Aygıt Mühendisliği", "QAB", 4],
    ["NOP 411E", "Gizmo Engineering", "NOP", 2],
    ["QAB 412E", "Gizmo Engineering", "QAB", 5],
    ["QAB 336E", "Analysis of Gadgets II", "QAB", 10],
    ["WYX 336E", "Analysis of Gadgets II", "WYX", 2],
    ["ZED 205", "Aygıt Tasarımına Giriş", "ZED", 6],
    ["ZED 207", "Çark Analizi", "ZED", 2],
    ["short"],
]
WYX_ENTRY = {
    "code": "WYX 411E", "name": "Gizmo Engineering", "terms": ["2031-2032-guz"],
    "rows": [["2031-2032-guz", "Örnek Hoca", 80, 49, "Pazartesi"]],
}


class FakeArchive:
    def __init__(self) -> None:
        self.codes: list[list[Any]] = CODES
        self.history: dict[str, dict[str, Any]] = {"WYX": {"WYX 411E": WYX_ENTRY}, "QAB": {
            "QAB 411": {"code": "QAB 411", "name": "Aygıt Mühendisliği", "terms": [], "rows": []},
        }}
        self.codes_fail = False
        self.calls: list[str] = []

    def get_course_history(self, branch: str) -> dict[str, Any]:
        self.calls.append(f"history:{branch}")
        return self.history.get(branch, {})

    def get_course_codes(self) -> list[list[Any]]:
        self.calls.append("codes")
        if self.codes_fail:
            raise ItuArchiveError("dizin okunamadı")
        return self.codes

    def get_index(self) -> dict[str, Any]:
        return {"currentSlug": "2031-2032-guz"}


class OtherBranchCandidateTests(unittest.TestCase):
    def test_same_number_and_name_under_another_branch(self) -> None:
        found = same_course_other_branches("WYX 411E", "Gizmo Engineering", CODES)
        self.assertEqual([item["course_code"] for item in found], ["QAB 411E", "NOP 411E"])
        self.assertEqual(found[0], {
            "course_code": "QAB 411E", "course_name": "Gizmo Engineering", "branch": "QAB",
            "term_count": 11, "evidence": {"same_number": True, "same_name": True},
        })

    def test_other_number_other_name_and_own_branch_are_left_out(self) -> None:
        codes = {item["course_code"] for item in same_course_other_branches("QAB 411E", "gizmo engineering", CODES)}
        self.assertEqual(codes, {"WYX 411E", "NOP 411E"})  # not QAB 412E, QAB 411, ZED 411E

    def test_list_is_capped_and_ranked_by_term_count(self) -> None:
        many = [[f"A{chr(65 + i)}C 101", "Common Seminar", f"A{chr(65 + i)}C", i + 1] for i in range(12)]
        found = same_course_other_branches("QAB 101", "Common Seminar", many, limit=3)
        self.assertEqual([item["term_count"] for item in found], [12, 11, 10])

    def test_unusable_input_gives_nothing(self) -> None:
        self.assertEqual(same_course_other_branches("nonsense", "Gizmo Engineering", CODES), [])
        self.assertEqual(same_course_other_branches("WYX 411E", "", CODES), [])
        self.assertEqual(same_course_other_branches("WYX 411E", "Gizmo Engineering", None), [])


class ArchiveToolTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        env = patch.dict(os.environ, {"NINOVA_STATE_DIR": self._tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.app = NinovaMcpApp()
        self.archive = FakeArchive()
        self.app._archive = self.archive  # type: ignore[assignment]


class OtherBranchToolTests(ArchiveToolTestCase):
    def test_three_tools_report_the_candidates_without_merging(self) -> None:
        for call in (
            lambda: self.app.archive_who_taught("wyx411e"),
            lambda: self.app.archive_course_history("WYX 411E"),
            lambda: self.app.archive_fill_rate(course_code="WYX 411E"),
        ):
            self.archive.calls.clear()
            result = call()
            with self.subTest(keys=sorted(result)[:3]):
                self.assertEqual(result["course_code"], "WYX 411E")
                codes = [item["course_code"] for item in result["same_course_other_codes"]]
                self.assertEqual(codes, ["QAB 411E", "NOP 411E"])
                self.assertIn("QAB 411E", result["same_course_other_codes_note"])
                # One index read on top of the branch file, nothing per candidate.
                self.assertEqual(self.archive.calls, ["history:WYX", "codes"])
        self.assertEqual(len(self.app.archive_who_taught("WYX 411E")["instructors"]), 1)
        self.assertEqual(self.app.archive_course_history("WYX 411E")["terms_offered"], 1)

    def test_no_candidate_adds_nothing(self) -> None:
        self.archive.codes = [row for row in CODES if row[0] in ("WYX 411E", "ZED 411E")]
        result = self.app.archive_who_taught("WYX 411E")
        self.assertNotIn("same_course_other_codes", result)
        self.assertNotIn("same_course_other_codes_note", result)

    def test_unreadable_index_does_not_fail_the_tool(self) -> None:
        self.archive.codes_fail = True
        result = self.app.archive_course_history("WYX 411E")
        self.assertEqual(result["terms_offered"], 1)
        self.assertNotIn("same_course_other_codes", result)

    def test_missing_course_names_the_codes_it_may_be_under(self) -> None:
        with self.assertRaises(ItuArchiveError) as caught:
            self.app.archive_who_taught("WYX 336E")
        message = str(caught.exception)
        self.assertIn("WYX 336E", message)
        self.assertIn("QAB 336E", message)
        self.assertIn("Analysis of Gadgets II", message)
        self.assertNotIn("hiç açılmamış dersler yer almaz", message)

    def test_missing_course_names_a_sibling_of_its_own_branch(self) -> None:
        with self.assertRaises(ItuArchiveError) as caught:
            self.app.archive_course_history("QAB 411E")
        self.assertIn("QAB 411", str(caught.exception).replace("QAB 411E", ""))

    def test_missing_course_is_matched_by_a_name_the_caller_knows(self) -> None:
        self.archive.codes = [row for row in CODES if row[0] != "WYX 336E"]
        with self.assertRaises(ItuArchiveError) as caught:
            self.app._archive_course_entry("WYX 336E")
        self.assertNotIn("QAB 336E", str(caught.exception))  # the number alone proves nothing
        with self.assertRaises(ItuArchiveError) as caught:
            self.app._archive_course_entry("WYX 336E", "analysis of gadgets II")
        self.assertIn("QAB 336E", str(caught.exception))

    def test_missing_course_without_any_lead_keeps_a_plain_message(self) -> None:
        self.archive.codes_fail = True
        with self.assertRaises(ItuArchiveError) as caught:
            self.app.archive_course_history("ZED 999")
        self.assertIn("arşivde bulunamadı", str(caught.exception))


class SearchTests(unittest.TestCase):
    def test_code_is_matched_without_spacing_or_case(self) -> None:
        for query in ("qab411e", "QAB411E", " qab  411e ", "Qab 411E"):
            with self.subTest(query=query):
                self.assertEqual(search_courses(CODES, query)[0]["course_code"], "QAB 411E")
        self.assertEqual({row["course_code"] for row in search_courses(CODES, "qab41")},
                         {"QAB 411E", "QAB 411", "QAB 412E"})

    def test_name_is_matched_without_accents_or_case(self) -> None:
        for query in ("aygit muhendisligi", "AYGIT MÜHENDİSLİĞİ", "aygıt mühendisliği"):
            with self.subTest(query=query):
                self.assertEqual([row["course_code"] for row in search_courses(CODES, query)], ["QAB 411"])

    def test_words_may_come_in_another_order_or_with_other_punctuation(self) -> None:
        self.assertEqual([row["course_code"] for row in search_courses(CODES, "gadgets analysis")],
                         ["QAB 336E", "WYX 336E"])
        self.assertEqual(search_courses(CODES, "gizmo engineering")[0]["course_code"], "QAB 411E")
        self.assertIn("WYX 411E", [row["course_code"] for row in search_courses(CODES, "gizmo engineering")])

    def test_hint_says_the_index_holds_one_name_per_course(self) -> None:
        self.assertIsNone(course_search_hint(CODES, "gizmo", search_courses(CODES, "gizmo")))
        for query in ("çark mühendisliği", "aygit analizi"):
            with self.subTest(query=query):
                self.assertEqual(search_courses(CODES, query), [])
                hint = course_search_hint(CODES, query, [])
                assert hint is not None
                self.assertTrue(hint["query_looks_turkish"])
                self.assertIn("İngilizce", hint["hint"])
                self.assertIn("ders kodu", hint["hint"])
        english = course_search_hint(CODES, "quantum widgets", [])
        assert english is not None
        self.assertFalse(english["query_looks_turkish"])
        self.assertTrue(english["hint"])
        self.assertIsNone(course_search_hint(CODES, "  ", []))


class SearchToolTests(ArchiveToolTestCase):
    def test_zero_matches_come_with_the_hint(self) -> None:
        result = self.app.archive_search_courses("çark mühendisliği")
        self.assertEqual(result["match_count"], 0)
        self.assertTrue(result["query_looks_turkish"])
        self.assertIn("İngilizce", result["hint"])

    def test_matches_come_without_it(self) -> None:
        result = self.app.archive_search_courses("qab411e")
        self.assertEqual(result["matches"][0]["course_code"], "QAB 411E")
        self.assertNotIn("hint", result)


if __name__ == "__main__":
    unittest.main()
