"""``plan_remaining_courses`` separates what is being taken now from what is left to plan.

OBS and the archive are fakes; every code, CRN and name is invented.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from ninova_mcp.archive_client import ItuArchiveError
from ninova_mcp.obs_client import ObsError
from ninova_mcp.server import TOOLS, NinovaMcpApp


def plan_row(code: str, name: str, credit: Any, semester: int, *, met: bool = False, group: str = "",
             grade: str = "") -> dict[str, Any]:
    return {"isMet": met, "bransKodu": code, "dersAdi": name, "grupName": group, "harfNotu": grade,
            "kredisiDec": credit, "kredisi": str(credit), "donemNo": semester,
            "sayilanKredi": credit if met else 0}


GRADUATION = {"mezuniyetimeNeKaldiBilgi": {
    "metKrediTotal": 4,
    "dersPlaniVM": {"gerekliMezuniyetKredisi": 30, "gerekliMinGPA": 2},
    "checkMetMezuniyetList": [
        plan_row("QAB 101E", "Intro to Gizmos", 4, 1, met=True, grade="BB"),
        plan_row("QAB 103E", "Gizmo Lab", 3, 2),                 # registered under the same code
        plan_row("ZED 105", "Sprocket Theory", 3, 2),            # registered as WYX 105, same name and credit
        plan_row("QAB 201E", "Gizmo Systems", 4, 3),             # not registered, in the archive
        plan_row("QAB 203E", "Gizmo Signals", 3, 3),             # not registered, not in the archive
        plan_row("ZED 207", "Sprocket Design", 2, 4),            # same name registered with another credit
        plan_row("", "5th Sem. Elective (TM)", 3, 5, group="5th Sem. Elective (TM)"),
        plan_row("", "6th Sem. Elective (ITB)", 2.5, 6, group="6th Sem. Elective (ITB)"),
    ],
    "unusedSinifOgrenciList": [],
}}


def reg(branch: str, number: str, crn: str, name: str, credit: Any = None, grade: Any = None) -> dict[str, Any]:
    return {"bransKodu": branch, "dersKodu": number, "crn": crn, "dersAdiEN": name, "dersAdiTR": name,
            "kredi": credit, "harfNotu": grade}


REGISTERED = {"statusCode": 0, "kayitSinifResultList": [
    reg("QAB", "103E", "61001", "Gizmo Lab"),
    reg("WYX", "105", "61002", "sprocket  THEORY", "3"),
    reg("WYX", "207", "61003", "Sprocket Design", "4"),
    reg("NOP", "110", "61004", "Free Elective Topic", "3"),
]}
ENTRY = {
    "code": "QAB 201E", "name": "Gizmo Systems",
    "terms": ["2030-2031-guz", "2029-2030-guz", "2028-2029-guz"],
    "rows": [["2030-2031-guz", "Örnek Hoca", 60, 55, "Salı"], ["2029-2030-guz", "Örnek Hoca", 60, 60, "Salı"],
             ["2028-2029-guz", "Başka Hoca", 60, 30, "Salı"]],
}


class FakeArchive:
    def __init__(self) -> None:
        self.requested: list[str] = []

    def get_course_history(self, branch: str) -> dict[str, Any]:
        self.requested.append(branch)
        return {"QAB 201E": ENTRY} if branch == "QAB" else {}

    def get_course_codes(self) -> list[list[Any]]:
        return [["QAB 201E", "Gizmo Systems", "QAB", 3], ["WYX 203E", "Gizmo Signals", "WYX", 7]]


class PlanRemainingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        env = patch.dict(os.environ, {"NINOVA_STATE_DIR": self._tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.app = NinovaMcpApp()
        self.obs = MagicMock()
        self.obs.default_program_id.return_value = 7
        self.obs.get_graduation_remaining.return_value = GRADUATION
        self.obs.resolve_semester.return_value = {"akademikDonemId": 93, "donemKodu": "203110"}
        self.obs.list_registered_courses.return_value = REGISTERED
        self.app._obs = self.obs
        self.archive = FakeArchive()
        self.app._archive = self.archive  # type: ignore[assignment]

    def test_courses_taken_now_are_separated_from_the_ones_to_plan(self) -> None:
        result = self.app.plan_remaining_courses()
        self.assertEqual(result["remaining_course_count"], 5)
        self.assertEqual((result["in_progress_count"], result["to_plan_count"]), (2, 3))
        self.assertEqual(result["registered_courses_status"], "read")
        taking = {item["course_code"]: item for item in result["in_progress"]}
        self.assertEqual(set(taking), {"QAB 103E", "ZED 105"})
        self.assertEqual(taking["QAB 103E"], {
            "course_code": "QAB 103E", "course_name": "Gizmo Lab", "credit": 3.0, "semester": "2. yarıyıl",
            "in_progress": True, "crn": "61001", "matched_by": "code",
        })
        self.assertEqual(taking["ZED 105"]["registered_as"], "WYX 105")
        self.assertEqual(taking["ZED 105"]["matched_by"], "name_and_credit")
        self.assertEqual(taking["ZED 105"]["crn"], "61002")
        self.obs.list_registered_courses.assert_called_once_with(93)

    def test_recommendations_only_for_courses_not_in_progress(self) -> None:
        result = self.app.plan_remaining_courses()
        self.assertEqual([plan["course_code"] for plan in result["plans"]], ["QAB 201E"])
        self.assertEqual(result["resolved_count"], 1)
        self.assertIn("yalnızca Güz", result["plans"][0]["recommendation"])
        self.assertFalse(result["plans"][0]["in_progress"])
        self.assertEqual([item["course_code"] for item in result["unresolved_courses"]], ["QAB 203E", "ZED 207"])
        for item in result["in_progress"]:
            self.assertNotIn("recommendation", item)
        # No archive lookup for a course that is being taken now.
        self.assertEqual(sorted(set(self.archive.requested)), ["QAB", "ZED"])

    def test_missing_course_reason_points_at_the_code_it_runs_under(self) -> None:
        reasons = {item["course_code"]: item["reason"] for item in self.app.plan_remaining_courses()["unresolved_courses"]}
        self.assertIn("WYX 203E", reasons["QAB 203E"])
        self.assertNotIn("hiç açılmamış dersler yer almaz", reasons["QAB 203E"])

    def test_same_name_with_another_credit_is_not_called_in_progress(self) -> None:
        result = self.app.plan_remaining_courses()
        self.assertNotIn("ZED 207", [item["course_code"] for item in result["in_progress"]])
        others = {item["code"]: item for item in result["other_registered_courses"]}
        self.assertEqual(set(others), {"WYX 207", "NOP 110"})
        self.assertEqual(others["NOP 110"]["crn"], "61004")

    def test_open_elective_slots_are_part_of_the_plan(self) -> None:
        result = self.app.plan_remaining_courses()
        self.assertEqual(result["open_elective_slots"], [
            {"slot": "5th Sem. Elective (TM)", "credit": 3.0, "semester": "5. yarıyıl"},
            {"slot": "6th Sem. Elective (ITB)", "credit": 2.5, "semester": "6. yarıyıl"},
        ])
        self.assertEqual((result["open_elective_slot_count"], result["open_elective_credits"]), (2, 5.5))
        self.assertEqual(result["credits"], {"in_progress": 6.0, "to_plan": 9.0, "open_elective_slots": 5.5})

    def test_unreadable_registered_courses_degrade_to_a_note(self) -> None:
        for failure in (ObsError("OBS kapalı"), RuntimeError("beklenmeyen")):
            with self.subTest(failure=type(failure).__name__):
                self.obs.list_registered_courses.side_effect = failure
                result = self.app.plan_remaining_courses()
                self.assertEqual(result["registered_courses_status"], "unavailable")
                self.assertIn("registered_courses_note", result)
                self.assertEqual((result["in_progress_count"], result["to_plan_count"]), (0, 5))
                self.assertEqual(result["in_progress"], [])
                self.assertEqual(result["open_elective_slot_count"], 2)

    def test_registered_course_that_already_has_a_grade_is_not_in_progress(self) -> None:
        graded = {"statusCode": 0, "kayitSinifResultList": [reg("QAB", "103E", "61001", "Gizmo Lab", grade="FF")]}
        self.obs.list_registered_courses.return_value = graded
        result = self.app.plan_remaining_courses()
        self.assertEqual(result["in_progress"], [])
        self.assertEqual(result["to_plan_count"], 5)

    def test_archive_failure_for_one_course_is_reported_per_course(self) -> None:
        with patch.object(self.archive, "get_course_history", side_effect=ItuArchiveError("arşiv kapalı")):
            result = self.app.plan_remaining_courses()
        self.assertEqual(result["resolved_count"], 0)
        self.assertEqual(len(result["unresolved_courses"]), 3)

    def test_description_tells_the_lists_apart(self) -> None:
        description = next(tool for tool in TOOLS if tool["name"] == "plan_remaining_courses")["description"]
        for field in ("in_progress", "plans", "open_elective_slots"):
            self.assertIn(field, description)


if __name__ == "__main__":
    unittest.main()
