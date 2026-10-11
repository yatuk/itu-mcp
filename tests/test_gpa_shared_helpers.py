"""The GPA modules hold several helpers that look alike. Which ones are the same?

``gpa.py`` (term average) and ``gpa_simulation.py`` (cumulative simulation)
were written side by side. Each pair below is fed the same inputs. The pairs
that agree on every input share one implementation; the pairs that differ are
kept apart on purpose and the cases where they differ are pinned here, so a
later merge has to decide which behaviour is meant.

Every course code, term and grade below is invented.
"""

from __future__ import annotations

import copy
import decimal
import unittest
from typing import Any

from ninova_mcp import gpa, gpa_reference, gpa_simulation
from ninova_mcp.grading import LETTER_TO_GRADE

NUMBERS: tuple[Any, ...] = (
    None, True, False, 0, -0.0, 3, 3.5, -2, "3", "3,5", " 3 ", "\t3,5\n", "", "  ", "abc", "NaN", "inf",
    "-Infinity", "1e400", "1_000", "0x10", "+4", "3 ,5", "3.5.1", "1,234,5", float("nan"), float("inf"),
    -float("inf"), 10**400, 10**5000, decimal.Decimal("2.5"), [], {}, (1,), b"3",
)


def described(value: Any) -> str:
    return type(value).__name__ if isinstance(value, int) and not isinstance(value, bool) and value > 10**9 \
        else repr(value)


class SameHelperTests(unittest.TestCase):
    """Pairs that agree on every input: one implementation serves both names."""

    def test_number_readers_agree(self) -> None:
        for value in NUMBERS:
            with self.subTest(value=described(value)):
                expected = gpa_reference._number(value)
                found = gpa_simulation._number(value)
                self.assertEqual(repr(found), repr(expected))

    def test_credit_is_a_nonnegative_number(self) -> None:
        for value in NUMBERS:
            with self.subTest(value=described(value)):
                number = gpa_reference._number(value)
                expected = number if number is not None and number >= 0 else None
                self.assertEqual(repr(gpa.parse_credit(value)), repr(expected))

    def test_grade_lists_agree(self) -> None:
        counted = [grade for grade, value in LETTER_TO_GRADE.items() if value is not None]
        self.assertEqual(list(gpa_simulation.GRADED), counted)
        self.assertEqual(list(gpa._COUNTED_GRADES), counted)
        self.assertEqual(list(gpa_simulation.UNGRADED),
                         sorted(grade for grade, value in LETTER_TO_GRADE.items() if value is None))

    def test_projected_grade_is_read_and_refused_the_same_way(self) -> None:
        grades: tuple[Any, ...] = ("AA", " bb ", "ba+", "BL", "bz", "", "  ", "A", "A+", "ZZ", None, 4, 4.0, True,
                                   ["AA"], {"AA": 1}, b"AA")
        for key in ("ZZT 101E", "40011", 40011, "it's"):
            for value in grades:
                with self.subTest(key=key, value=value):
                    outcomes = []
                    for read in (
                        lambda: gpa.validate_projected_grades({key: value})[str(key)],
                        lambda: gpa_simulation._grade_argument(value, f"projected_grades[{str(key)!r}]"),
                    ):
                        try:
                            outcomes.append(("grade", read()))
                        except ValueError as exc:
                            outcomes.append(("error", str(exc)))
                    self.assertEqual(outcomes[0], outcomes[1])


class DifferentHelperTests(unittest.TestCase):
    """Pairs that look alike and are not: kept separate, differences pinned."""

    def test_course_name_keys_agree_on_plain_names_only(self) -> None:
        for name in ("Intro to Widgets", "Şeçmeli Öğe İ", "IŞIK", "straße", None, "  x  "):
            with self.subTest(name=name):
                self.assertEqual(gpa._name_key(name), gpa_simulation._name_key(name).replace(" ", ""))

        def same(key: Any, left: str, right: str) -> bool:
            return key(left) == key(right)

        # The term tool drops every separator; the simulation keeps word boundaries.
        self.assertTrue(same(gpa._name_key, "Data-Base", "Database"))
        self.assertFalse(same(gpa_simulation._name_key, "Data-Base", "Database"))
        # The simulation reads "&" as "and"; the term tool drops it.
        self.assertTrue(same(gpa_simulation._name_key, "R&D Lab", "R and D Lab"))
        self.assertFalse(same(gpa._name_key, "R&D Lab", "R and D Lab"))

    def test_plan_row_credit_differs_for_zero_and_for_an_unreadable_decimal_field(self) -> None:
        def term_tool(row: dict[str, Any]) -> float | None:
            return gpa.plan_rows({"checkMetMezuniyetList": [{"bransKodu": "ZZT 101E", **row}]})[0]["credit"]

        for row in ({"kredisiDec": 3, "kredisi": "5"}, {"kredisiDec": None, "kredisi": "3,5"}, {"kredisi": 4},
                    {"kredisiDec": "x", "kredisi": "y"}, {}):
            with self.subTest(row=row):
                self.assertEqual(term_tool(row), gpa_simulation._row_credit(row))
        # A zero credit is "no credit" for the term tool and a real 0 for the simulation.
        self.assertIsNone(term_tool({"kredisiDec": 0}))
        self.assertEqual(gpa_simulation._row_credit({"kredisiDec": 0}), 0)
        # An unreadable kredisiDec hides kredisi from the term tool only.
        self.assertIsNone(term_tool({"kredisiDec": "x", "kredisi": "5"}))
        self.assertEqual(gpa_simulation._row_credit({"kredisiDec": "x", "kredisi": "5"}), 5)
        # A zero kredisiDec hides kredisi from both.
        self.assertIsNone(term_tool({"kredisiDec": 0, "kredisi": "5"}))
        self.assertEqual(gpa_simulation._row_credit({"kredisiDec": 0, "kredisi": "5"}), 0)

    def credit_choice(self, rows: list[tuple[str, str, float, float | None]], official: float) -> tuple[Any, Any]:
        """The credits each module settles on for the same term: (term tool, simulation)."""
        courses = []
        attempts = []
        for code, grade, credit, counted in rows:
            entry: dict[str, Any] = {"code": code, "grade": grade, "credit": credit}
            if counted is not None:
                entry.update(plan_credit=credit, counted_credit=counted, credit_candidates=[credit, counted])
            courses.append(entry)
            attempts.append(gpa_simulation._Attempt(
                key=code, code=code, name=code, term="203120", grade=grade, credit=credit, alt_credit=counted))
        report = gpa.reconcile_credit_candidates(courses, official)
        resolved, choices, _ = gpa_simulation._resolve_credits(
            copy.deepcopy(attempts), {"203120": {"term_gpa": official}})
        return (
            (report["status"] if report else None, [course["credit"] for course in courses]),
            ([choice["decided"] for choice in choices], [attempt.credit for attempt in resolved]),
        )

    def test_credit_choice_agrees_for_one_row_with_two_values(self) -> None:
        rows = [("ZZT 301E", "BB", 4, None), ("ZZT 302E", "CC", 3, None), ("ZZT 455E", "DD", 2, 3)]
        # 2 credits gives 2.22, 3 credits gives 2.10.
        for official, status, credit, decided in (
            (2.1, "resolved_by_official_average", 3, True),
            (2.22, "confirmed_by_official_average", 2, True),
            (2.9, "undetermined", 2, False),
        ):
            with self.subTest(official=official):
                term_tool, simulation = self.credit_choice(rows, official)
                self.assertEqual(term_tool, (status, [4, 3, credit]))
                self.assertEqual(simulation, ([decided], [4, 3, credit]))

    def test_credit_choice_differs_when_only_one_of_two_rows_can_be_decided(self) -> None:
        # (12 + 2b) / (3 + a + b) is 2.00 for a = 3 whatever b is: the official
        # average fixes the first row and says nothing about the second.
        rows = [("ZZT 301E", "AA", 3, None), ("ZZT 455E", "FF", 2, 3), ("ZZT 456E", "CC", 2, 3)]
        term_tool, simulation = self.credit_choice(rows, 2.0)
        # The term tool wants one combination for all rows together and keeps both defaults.
        self.assertEqual(term_tool, ("undetermined", [3, 2, 2]))
        # The simulation decides row by row and takes 3 for the first one.
        self.assertEqual(simulation, ([True, False], [3, 3, 2]))


if __name__ == "__main__":
    unittest.main()
