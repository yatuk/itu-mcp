"""A bad what-if grade is named: key, value and the valid grades. Invented data."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from ninova_mcp.gpa import calculate_gpa
from ninova_mcp.server import NinovaMcpApp

COURSES = [{"code": "QAB 102E", "credit": 3, "grade": None}, {"code": "QAB 104", "credit": 4, "grade": None}]


class ProjectedGradeErrorTests(unittest.TestCase):
    def test_error_names_the_key_the_value_and_the_valid_grades(self) -> None:
        with self.assertRaises(ValueError) as caught:
            calculate_gpa(COURSES, projected_grades={"QAB 104": "BB", "QAB 102E": "XX"})
        message = str(caught.exception)
        for part in ("'QAB 102E'", "'XX'", "AA", "BA+", "FF", "VF", "BL"):
            self.assertIn(part, message)
        self.assertNotIn("QAB 104", message)

    def test_other_bad_values_are_named_too(self) -> None:
        for value in (3, None, "", "B+", ["AA"]):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "projected_grades.'QAB 104'"):
                calculate_gpa(COURSES, projected_grades={"QAB 104": value})  # type: ignore[dict-item]
        with self.assertRaisesRegex(ValueError, "course code or a CRN"):
            calculate_gpa(COURSES, projected_grades={"  ": "AA"})

    def test_lowercase_and_padded_grades_are_accepted(self) -> None:
        result = calculate_gpa(COURSES, projected_grades={"qab102e": "bb", "QAB 104": " ba+ "})
        self.assertEqual(result["gpa"], round((3 * 3.0 + 4 * 3.75) / 7, 2))

    def test_tool_rejects_a_bad_grade_before_obs_is_contacted(self) -> None:
        app = NinovaMcpApp()
        app._obs = MagicMock()
        with self.assertRaisesRegex(ValueError, "'XX'"):
            app.obs_calculate_gpa(projected_grades={"QAB 102E": "XX"})
        app._obs.resolve_semester.assert_not_called()
        app._obs.list_registered_courses.assert_not_called()


if __name__ == "__main__":
    unittest.main()
