"""Invalid source records must not produce a plausible complete average."""
import json
import unittest

from ninova_mcp.gpa import calculate_gpa, calculate_target_gpa, course_key, parse_credit


class GpaNumericValidationTests(unittest.TestCase):
    def test_invalid_failed_course_credit_cannot_inflate_average(self):
        for raw in (None, "", "bad", -2, "-2", True, float("nan"), "NaN", float("inf"), "Infinity"):
            with self.subTest(raw=repr(raw)):
                result = calculate_gpa([
                    {"code": "AAA 101", "credit": 3, "grade": "AA"},
                    {"code": "BBB 102", "credit": raw, "grade": "FF"},
                ])
                self.assertIsNone(result["gpa"])
                self.assertEqual(result["known_courses_gpa"], 4)
                self.assertFalse(result["calculation_complete"])
                self.assertEqual(result["calculation_status"], "incomplete")
                self.assertEqual(result["covered_course_count"], 1)
                self.assertEqual(result["gpa_eligible_course_count"], 2)
                self.assertEqual(result["invalid_credits"][0]["code"], "BBB 102")
                self.assertEqual(result["ff_risk"][0]["code"], "BBB 102")
                self.assertIsNone(result["comment"])
                json.dumps(result, allow_nan=False)

    def test_real_zero_credit_is_preserved(self):
        result = calculate_gpa([
            {"code": "AAA 101", "credit": 3, "grade": "AA"},
            {"code": "BBB 102", "credit": 0, "grade": "FF"},
        ])
        self.assertEqual(result["gpa"], 4)
        self.assertEqual(result["courses"][1]["credit"], 0)
        self.assertEqual(result["invalid_credits"], [])
        self.assertTrue(result["calculation_complete"])
        self.assertEqual(parse_credit("0,0"), 0)
        self.assertEqual(parse_credit("2,5"), 2.5)

    def test_ongoing_or_unknown_grade_exposes_only_graded_subset(self):
        for grade in (None, "", "   ", "XY", False, 0):
            with self.subTest(grade=grade):
                result = calculate_gpa([
                    {"code": "AAA 101", "credit": 3, "grade": "AA"},
                    {"code": "BBB 102", "credit": 3, "grade": grade},
                ])
                self.assertIsNone(result["gpa"])
                self.assertEqual(result["known_courses_gpa"], 4)
                self.assertEqual(result["input_course_count"], 2)
                self.assertEqual(result["covered_course_count"], 1)
                self.assertFalse(result["calculation_complete"])

    def test_nonfinite_backend_provenance_is_safe_for_strict_json(self):
        result = calculate_gpa([
            {"code": "AAA 101", "credit": 3, "grade": "AA", "recorded_credit": float("nan"),
             "plan_credit": float("inf"), "counted_credit": float("-inf")},
            {"code": "BBB 102", "credit": float("inf"), "grade": None,
             "extra": {"source_values": [float("nan")]}},
        ])
        self.assertIsNone(result["courses"][0]["recorded_credit"])
        self.assertIsNone(result["courses"][0]["plan_credit"])
        self.assertIsNone(result["ungraded"][0]["credit"])
        self.assertEqual(result["ungraded"][0]["credit_status"], "invalid")
        self.assertEqual(result["ungraded"][0]["extra"]["source_values"], [None])
        json.dumps(result, allow_nan=False)

    def test_finite_inputs_cannot_overflow_credit_arithmetic(self):
        result = calculate_gpa([{"code": "AAA 101", "credit": 1e308, "grade": "AA"}])
        self.assertFalse(result["calculation_complete"])
        self.assertEqual(result["invalid_credits"][0]["reason"], "credit_arithmetic_overflow")
        json.dumps(result, allow_nan=False)

    def test_unmatched_projection_is_explicit(self):
        result = calculate_gpa([{"code": "AAA 101", "credit": 3, "grade": "BB"}],
                               projected_grades={"BBB102": "AA"})
        self.assertEqual(result["gpa"], 3)
        self.assertFalse(result["projection_complete"])
        self.assertEqual(result["applied_projection_count"], 0)
        self.assertEqual(result["unused_projected_courses"], ["BBB 102"])

    def test_course_key_normalizes_spacing_without_losing_language_suffix(self):
        self.assertEqual(course_key("uck439e"), course_key(" UCK\u00a0439E "))
        self.assertNotEqual(course_key("UCK439"), course_key("UCK439E"))

    def test_supplied_term_average_has_no_academic_standing_claim(self):
        for grade in ("AA", "BB", "CC", "FF"):
            result = calculate_gpa([{"code": "AAA 101", "credit": 3, "grade": grade}])
            self.assertIsNone(result["comment"])
            self.assertEqual(result["average_scope"], "supplied_courses")


class TargetGpaNumericValidationTests(unittest.TestCase):
    def test_invalid_inputs_are_rejected_before_arithmetic(self):
        valid = dict(current_gpa=3, current_credits=60, target_gpa=3.5, future_credits=30)
        for field in valid:
            for raw in (None, True, float("nan"), float("inf"), float("-inf"), "bad"):
                with self.subTest(field=field, raw=repr(raw)), self.assertRaises(ValueError):
                    calculate_target_gpa(**{**valid, field: raw})

    def test_finite_arithmetic_overflow_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "finite numeric range"):
            calculate_target_gpa(current_gpa=4, current_credits=1e308,
                                 target_gpa=4, future_credits=1e308)

    def test_current_target_and_final_zero_point_target_are_distinct(self):
        result = calculate_target_gpa(current_gpa=3.6, current_credits=60,
                                      target_gpa=3.5, future_credits=30)
        self.assertTrue(result["already_at_or_above_target"])
        self.assertFalse(result["target_reached_with_zero_future_points"])
        self.assertGreater(result["required_future_average"], 0)
        json.dumps(result, allow_nan=False)
