from __future__ import annotations

import unittest

from ninova_mcp.gpa import LETTER_TO_GRADE, calculate_gpa
from ninova_mcp.schedule_utils import check_conflicts, parse_time_range


class GpaTests(unittest.TestCase):
    def test_letter_to_grade_table(self) -> None:
        self.assertEqual(LETTER_TO_GRADE["AA"], 4.00)
        self.assertEqual(LETTER_TO_GRADE["BA"], 3.50)
        self.assertEqual(LETTER_TO_GRADE["CC"], 2.00)
        self.assertEqual(LETTER_TO_GRADE["FF"], 0.00)
        self.assertIsNone(LETTER_TO_GRADE.get("GE"))

    def test_calculate_gpa_basic(self) -> None:
        courses = [
            {"code": "MAT 103E", "credit": 5, "grade": "AA"},
            {"code": "FIZ 101E", "credit": 4, "grade": "BB"},
            {"code": "BLG 101E", "credit": 3, "grade": "CC"},
        ]
        result = calculate_gpa(courses)
        # (5*4.0 + 4*3.0 + 3*2.0) / 12 = (20+12+6)/12 = 3.17
        self.assertEqual(result["gpa"], 3.17)
        self.assertEqual(result["total_credits"], 12.0)
        self.assertEqual(result["graded_course_count"], 3)

    def test_calculate_gpa_with_projected(self) -> None:
        courses = [
            {"code": "BLG 223E", "credit": 4, "grade": None},
            {"code": "BLG 101E", "credit": 3, "grade": "BB"},
        ]
        result = calculate_gpa(courses, projected_grades={"BLG 223E": "AA"})
        # (4*4.0 + 3*3.0) / 7 = (16+9)/7 = 3.57
        self.assertEqual(result["gpa"], 3.57)
        self.assertEqual(result["total_credits"], 7.0)

    def test_calculate_gpa_ff_risk(self) -> None:
        courses = [
            {"code": "BLG 101E", "credit": 3, "grade": "FF"},
            {"code": "BLG 223E", "credit": 4, "grade": "BB"},
        ]
        result = calculate_gpa(courses)
        # FF counts in GPA with coefficient 0.0: (3*0 + 4*3) / 7 = 12/7 = 1.71
        self.assertEqual(result["gpa"], 1.71)
        self.assertEqual(result["total_credits"], 7.0)
        # FF should NOT appear in ff_risk (it's already handled in GPA calculation)
        self.assertIn("BLG 101E", [c["code"] for c in result["courses"]])

    def test_calculate_gpa_skip_ge_kf(self) -> None:
        courses = [
            {"code": "STA 201", "credit": 2, "grade": "GE"},
            {"code": "BLG 101E", "credit": 3, "grade": "AA"},
        ]
        result = calculate_gpa(courses)
        # GE not counted
        self.assertEqual(result["gpa"], 4.0)
        self.assertEqual(result["total_credits"], 3.0)

    def test_calculate_gpa_empty(self) -> None:
        result = calculate_gpa([])
        self.assertIsNone(result["gpa"])
        self.assertEqual(result["total_credits"], 0.0)


class ScheduleConflictTests(unittest.TestCase):
    def test_parse_time_range(self) -> None:
        self.assertEqual(parse_time_range("09:30/12:29"), (570, 749))
        self.assertEqual(parse_time_range("14:00/17:29"), (840, 1049))
        self.assertIsNone(parse_time_range(""))
        self.assertIsNone(parse_time_range("no-slash"))

    def test_no_conflicts(self) -> None:
        courses = [
            {"crn": "111", "code": "A", "sessions": [{"day": "Pazartesi", "time": "09:30/12:29"}]},
            {"crn": "222", "code": "B", "sessions": [{"day": "Pazartesi", "time": "13:30/15:29"}]},
        ]
        result = check_conflicts(courses)
        self.assertTrue(result["ok"])
        self.assertEqual(result["conflict_count"], 0)

    def test_conflict_detected(self) -> None:
        courses = [
            {"crn": "111", "code": "A", "sessions": [{"day": "Salı", "time": "09:30/12:29"}]},
            {"crn": "222", "code": "B", "sessions": [{"day": "Salı", "time": "10:00/13:00"}]},
        ]
        result = check_conflicts(courses)
        self.assertFalse(result["ok"])
        self.assertEqual(result["conflict_count"], 1)
        self.assertEqual(result["conflicts"][0]["course_a"]["crn"], "111")
        self.assertEqual(result["conflicts"][0]["course_b"]["crn"], "222")

    def test_same_course_no_conflict(self) -> None:
        # A course with multiple sessions (lecture + lab) — same CRN, no conflict
        courses = [
            {"crn": "111", "code": "A", "sessions": [
                {"day": "Çarşamba", "time": "09:30/12:29"},
                {"day": "Çarşamba", "time": "13:30/15:29"},
            ]},
        ]
        result = check_conflicts(courses)
        self.assertTrue(result["ok"])

    def test_multi_session_conflict(self) -> None:
        courses = [
            {"crn": "111", "code": "A", "sessions": [
                {"day": "Perşembe", "time": "09:30/12:29"},
                {"day": "Perşembe", "time": "13:30/15:29"},
            ]},
            {"crn": "222", "code": "B", "sessions": [
                {"day": "Perşembe", "time": "13:00/14:00"},
            ]},
        ]
        result = check_conflicts(courses)
        self.assertEqual(result["conflict_count"], 1)

    def test_different_days_no_conflict(self) -> None:
        courses = [
            {"crn": "111", "code": "A", "sessions": [{"day": "Pazartesi", "time": "09:30/12:29"}]},
            {"crn": "222", "code": "B", "sessions": [{"day": "Cuma", "time": "09:30/12:29"}]},
        ]
        result = check_conflicts(courses)
        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
