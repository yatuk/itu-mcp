"""``explain_course_eligibility`` and the courses the student is taking now.

Every client is a mock and every code, CRN and name is invented.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

from ninova_mcp.obs_client import ObsError
from ninova_mcp.server import NinovaMcpApp


def leaf(code: str, minimum: str | None = None) -> dict[str, Any]:
    return {"type": "course", "code": code, "min_grade": minimum}


def any_of(*operands: dict[str, Any]) -> dict[str, Any]:
    return {"type": "or", "operands": list(operands)}


def all_of(*operands: dict[str, Any]) -> dict[str, Any]:
    return {"type": "and", "operands": list(operands)}


def met(code: str, grade: str, credit: Any = 3) -> dict[str, Any]:
    return {"isMet": True, "bransKodu": code, "harfNotu": grade, "kredisiDec": credit, "kredisi": str(credit),
            "sayilanKredi": credit, "dersAdi": code, "grupName": "", "donem": "203020"}


def unmet(code: str, credit: Any) -> dict[str, Any]:
    return {"isMet": False, "bransKodu": code, "harfNotu": "", "kredisiDec": credit, "kredisi": str(credit),
            "sayilanKredi": 0, "dersAdi": code, "grupName": "", "donem": None}


def reg(code: str, crn: str, credit: Any = None, grade: Any = None) -> dict[str, Any]:
    branch, number = code.split()
    return {"bransKodu": branch, "dersKodu": number, "crn": crn, "dersAdiEN": code, "dersAdiTR": code,
            "kredi": credit, "harfNotu": grade}


# QAB 301E needs QAB 201E, one of (ZED 110 or ZED 110E), QAB 205E with at least BB, and 20 credits.
TREE = all_of(leaf("QAB 201E"), any_of(leaf("ZED 110"), leaf("ZED 110E")), leaf("QAB 205E", "BB"))


def make_app(registered: Any, *, earned: Any = 12, tree: Any = TREE, credit_requirement: Any = 20,
             rows: list[dict[str, Any]] | None = None) -> SimpleNamespace:
    app = SimpleNamespace(obs=Mock(), obs_public=Mock(), prereq_crosscheck=Mock(), archive=Mock())
    app.obs.default_program_id.return_value = 3
    app.obs.get_graduation_remaining.return_value = {"mezuniyetimeNeKaldiBilgi": {
        "metKrediTotal": earned,
        "checkMetMezuniyetList": rows if rows is not None else [
            met("QAB 101E", "BB", 4), met("QAB 103E", "CC", 4), met("QAB 105E", "AA", 4),
            unmet("QAB 201E", 4), unmet("ZED 110E", 3), unmet("QAB 205E", 3),
        ],
        "unusedSinifOgrenciList": [],
    }}
    app.obs.resolve_semester.return_value = {"akademikDonemId": 95, "donemKodu": "203110"}
    if isinstance(registered, Exception):
        app.obs.list_registered_courses.side_effect = registered
    else:
        app.obs.list_registered_courses.return_value = {"statusCode": 0, "kayitSinifResultList": registered}
    app.obs_public.get_branch_prerequisites.return_value = {
        "url": "https://obs.example/x", "table_parsed": True,
        "rules": {"QAB 301E": {"requirement_tree": tree, "credit_requirement": credit_requirement}},
    }
    app.prereq_crosscheck.get_course_prerequisite_tree.return_value = None
    app.archive.get_course_history.return_value = {}
    return app


def explain(app: SimpleNamespace, **arguments: Any) -> dict[str, Any]:
    return NinovaMcpApp.explain_course_eligibility(app, "QAB 301E", **arguments)


ALL_THREE = [reg("QAB 201E", "71001"), reg("ZED 110E", "71002", "3"), reg("QAB 205E", "71003", "3")]


class InProgressPrerequisiteTests(unittest.TestCase):
    def test_prerequisites_taken_now_are_named_and_the_verdict_is_unchanged(self) -> None:
        result = explain(make_app(ALL_THREE), use_obs_history=True)
        self.assertIs(result["eligible"], False)
        self.assertEqual(result["in_progress_prerequisites"], ["QAB 201E", "QAB 205E", "ZED 110E"])
        self.assertIs(result["eligible_if_in_progress_passed"], True)
        self.assertEqual(result["in_progress_status"], "read")
        # 12 earned, 4 + 3 + 3 being taken: the 20 credit rule is met only afterwards.
        self.assertIs(result["credit_requirement_met"], False)
        self.assertEqual((result["credits_in_progress"], result["credits_if_in_progress_passed"]), (10.0, 22.0))
        self.assertIn("QAB 205E", result["eligible_if_in_progress_passed_note"])
        self.assertIn("BB", result["eligible_if_in_progress_passed_note"])

    def test_passing_what_is_taken_now_may_still_not_be_enough(self) -> None:
        result = explain(make_app([reg("QAB 201E", "71001")]), use_obs_history=True)
        self.assertEqual(result["in_progress_prerequisites"], ["QAB 201E"])
        self.assertIs(result["eligible_if_in_progress_passed"], False)
        self.assertEqual(result["credits_if_in_progress_passed"], 16.0)

    def test_course_already_graded_this_term_is_not_in_progress(self) -> None:
        result = explain(make_app([reg("QAB 201E", "71001", grade="FF")]), use_obs_history=True)
        self.assertEqual(result["in_progress_prerequisites"], [])

    def test_retake_of_a_passed_course_adds_no_credit(self) -> None:
        result = explain(make_app([*ALL_THREE, reg("QAB 103E", "71004", "4")]), use_obs_history=True)
        self.assertEqual(result["credits_in_progress"], 10.0)

    def test_unreadable_registration_leaves_the_question_open(self) -> None:
        for failure in (ObsError("OBS kapalı"), RuntimeError("beklenmeyen")):
            with self.subTest(failure=type(failure).__name__):
                result = explain(make_app(failure), use_obs_history=True)
                self.assertIs(result["eligible"], False)
                self.assertEqual(result["in_progress_status"], "unavailable")
                self.assertEqual(result["in_progress_prerequisites"], [])
                self.assertIsNone(result["eligible_if_in_progress_passed"])
                self.assertIsNone(result["credits_in_progress"])

    def test_without_obs_history_registration_is_not_read(self) -> None:
        app = make_app(ALL_THREE)
        result = explain(app, completed_courses=["QAB 201E:AA"], completed_credits=30)
        self.assertEqual(result["in_progress_status"], "not_requested")
        self.assertIsNone(result["eligible_if_in_progress_passed"])
        app.obs.list_registered_courses.assert_not_called()
        app.obs.resolve_semester.assert_not_called()

    def test_already_eligible_stays_eligible(self) -> None:
        rows = [met("QAB 201E", "BB", 8), met("ZED 110", "CC", 8), met("QAB 205E", "BA", 8)]
        result = explain(make_app([], earned=24, rows=rows), use_obs_history=True)
        self.assertIs(result["eligible"], True)
        self.assertIs(result["eligible_if_in_progress_passed"], True)
        self.assertEqual(result["missing_courses"], [])
        self.assertEqual(result["explanation"], "Tüm ön şartlar karşılanıyor.")


class MissingRequirementWordingTests(unittest.TestCase):
    def test_alternatives_of_one_group_are_one_entry(self) -> None:
        result = explain(make_app([]), use_obs_history=True)
        self.assertEqual(result["missing_courses"], ["QAB 201E", "ZED 110 VEYA ZED 110E", "QAB 205E"])
        self.assertEqual(result["missing_requirements"], [
            {"course": "QAB 201E", "min_grade": None, "state": "not_taken", "in_progress": False},
            {"any_of": ["ZED 110", "ZED 110E"], "state": "unmet", "in_progress": []},
            {"course": "QAB 205E", "min_grade": "BB", "state": "not_taken", "in_progress": False},
        ])

    def test_definitely_unmet_group_is_not_called_unverifiable(self) -> None:
        result = explain(make_app([reg("ZED 110E", "71002", "3")]), use_obs_history=True)
        self.assertNotIn("doğrulana", result["explanation"])
        parts = result["explanation_parts"]
        self.assertEqual(result["explanation"], " ".join(parts))
        self.assertEqual(len(parts), 4)  # three requirements and the credit rule
        self.assertIn("QAB 201E alınmamış.", parts)
        group = next(part for part in parts if "ZED 110" in part)
        self.assertIn("hiçbiri", group)
        self.assertIn("ZED 110 VEYA ZED 110E", group)
        self.assertIn("ZED 110E bu dönem alınıyor", group)
        self.assertTrue(any(part.startswith("Kredi şartı 20") and "15" in part for part in parts))

    def test_low_grade_and_unverifiable_grade_keep_their_own_wording(self) -> None:
        tree = all_of(leaf("QAB 205E", "BB"), any_of(leaf("ZED 110", "CC"), leaf("ZED 110E")))
        result = explain(make_app([], tree=tree, credit_requirement=None),
                         completed_courses=["QAB 205E:CC", "ZED 110:BL"])
        self.assertIs(result["eligible"], False)
        self.assertIn("QAB 205E notu CC, en az BB gerekiyor.", result["explanation_parts"])
        self.assertEqual(result["missing_courses"], ["QAB 205E"])
        self.assertEqual(result["unknown_courses"], ["ZED 110"])
        group = next(part for part in result["explanation_parts"] if "ZED 110" in part)
        self.assertIn("doğrulanamıyor", group)
        self.assertEqual(result["missing_requirements"][1]["state"], "unverified")


if __name__ == "__main__":
    unittest.main()
