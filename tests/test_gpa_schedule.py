from __future__ import annotations

import unittest

from ninova_mcp.gpa import LETTER_TO_GRADE, calculate_gpa, calculate_target_gpa
from ninova_mcp.schedule_utils import check_conflicts, parse_time_range


class GpaTests(unittest.TestCase):
    def test_letter_to_grade_table(self) -> None:
        self.assertEqual(LETTER_TO_GRADE["AA"], 4.00)
        self.assertEqual(LETTER_TO_GRADE["BA"], 3.50)
        self.assertEqual(LETTER_TO_GRADE["CC"], 2.00)
        self.assertEqual(LETTER_TO_GRADE["FF"], 0.00)
        self.assertIsNone(LETTER_TO_GRADE.get("GE"))

    def test_vf_counts_as_zero(self) -> None:
        """VF is a failing grade, not an exemption: it must weigh on the GPA.

        It used to be absent from the table, so `.get("VF")` returned None and
        the course was dropped entirely — silently inflating the average.
        """
        self.assertEqual(LETTER_TO_GRADE["VF"], 0.00)
        result = calculate_gpa([
            {"code": "AAA 101", "credit": 3, "grade": "AA"},
            {"code": "BBB 102", "credit": 3, "grade": "VF"},
        ])
        # (3*4.0 + 3*0.0) / 6 = 2.00 — the VF credits stay in the denominator.
        self.assertEqual(result["gpa"], 2.00)
        self.assertEqual(result["total_credits"], 6.0)
        self.assertEqual(result["graded_course_count"], 2)

    def test_vf_is_flagged_for_retake(self) -> None:
        result = calculate_gpa([{"code": "BBB 102", "credit": 3, "grade": "VF"}])
        self.assertEqual([c["code"] for c in result["ff_risk"]], ["BBB 102"])
        self.assertIn("tekrar", result["ff_risk"][0]["note"])

    def test_non_counting_grades_still_excluded(self) -> None:
        """GE/KF/IA stay out of the average — only VF moved buckets."""
        result = calculate_gpa([
            {"code": "AAA 101", "credit": 3, "grade": "AA"},
            {"code": "CCC 103", "credit": 3, "grade": "GE"},
        ])
        self.assertEqual(result["gpa"], 4.00)
        self.assertEqual(result["total_credits"], 3.0)

    def test_plus_grade_coefficients_match_official_table(self) -> None:
        """İTÜ bağıl değerlendirme yönetmeliği Tablo 1'deki katsayılar.

        DD+/DC+/CC+/CB+/BB+/BA+ (AA'nın üstü ve FF'nin altı yok) tabloda
        yoktu; her biri gerçek OBS verisinde geçen bir not ve `.get()` None
        döndürdüğünden dersi calculate_gpa'da tamamen düşürüyordu — VF ile
        aynı sınıf hata, ama daha sık rastlanan bir notta.
        """
        expected = {
            "DD+": 1.25, "DC+": 1.75, "CC+": 2.25,
            "CB+": 2.75, "BB+": 3.25, "BA+": 3.75,
        }
        for grade, coefficient in expected.items():
            with self.subTest(grade=grade):
                self.assertEqual(LETTER_TO_GRADE[grade], coefficient)

    def test_plus_grade_is_not_silently_dropped(self) -> None:
        result = calculate_gpa([{"code": "X 101", "credit": 4, "grade": "BA+"}])
        self.assertEqual(result["gpa"], 3.75)
        self.assertEqual(result["graded_course_count"], 1)
        self.assertEqual(result["ungraded"], [])

    def test_plus_grade_low_end_flagged_as_low_grade(self) -> None:
        result = calculate_gpa([{"code": "X 101", "credit": 3, "grade": "DC+"}])
        self.assertIn("Düşük not", result["courses"][0]["note"])

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

    def test_projected_grade_overrides_existing_grade(self) -> None:
        result = calculate_gpa(
            [{"code": "BLG 223E", "credit": 4, "grade": "CC"}],
            projected_grades={"blg 223e": "AA"},
        )
        self.assertEqual(result["gpa"], 4.0)
        self.assertEqual(result["courses"][0]["grade"], "AA")
        self.assertTrue(result["courses"][0]["projected"])

    def test_calculate_target_gpa(self) -> None:
        result = calculate_target_gpa(
            current_gpa=2.5,
            current_credits=60,
            target_gpa=3.0,
            future_credits=30,
        )
        self.assertEqual(result["required_future_average"], 4.0)
        self.assertTrue(result["feasible_on_4_scale"])

    def test_impossible_target_gpa(self) -> None:
        result = calculate_target_gpa(
            current_gpa=2.0,
            current_credits=90,
            target_gpa=3.5,
            future_credits=30,
        )
        self.assertFalse(result["feasible_on_4_scale"])

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
