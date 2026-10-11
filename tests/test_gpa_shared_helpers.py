"""The GPA modules answer from the same record and must read it the same way.

``gpa.py`` (term average) and ``gpa_simulation.py`` (cumulative simulation)
were written side by side and once disagreed on a course name, on the credit
of a plan row, on the choice between two credit values, on term order and on
what-if keys. Each of those is now decided once, in ``gpa_common``. Every
class below feeds both tools the same inputs and states the one answer;
``test_gpa_cross_tool_consistency`` does the same for whole student records.

Every course code, term and grade below is invented.
"""

from __future__ import annotations

import copy
import decimal
import unittest
from typing import Any

from ninova_mcp import gpa, gpa_common, gpa_reference, gpa_simulation, gpa_term
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


def plan(met_rows: list[dict[str, Any]], unused_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"checkMetMezuniyetList": met_rows, "unusedSinifOgrenciList": unused_rows or []}


def met_row(code: Any, grade: Any, credit: Any, term: Any = "203120", **extra: Any) -> dict[str, Any]:
    return {"isMet": True, "bransKodu": code, "harfNotu": grade, "kredisiDec": credit, "sayilanKredi": credit,
            "donem": term, "dersAdi": f"Course {code}", **extra}


def unused_row(code: Any, grade: Any, credit: Any, term: Any, **extra: Any) -> dict[str, Any]:
    return {"bransKodu": code, "harfNotu": grade, "kredisi": credit, "donem": term,
            "dersAdi": f"Course {code}", **extra}


class CourseNameTests(unittest.TestCase):
    """One rule for "the same course name", used by both tools."""

    SAME = (
        ("Data-Base", "Database"),
        ("Data-Base", "Data Base"),
        ("R&D Lab", "R and D Lab"),
        ("R&D Lab", "R ve D Lab"),
        ("R & D Lab", "R&D  Lab."),
        ("Object-Oriented Programming", "OBJECT ORIENTED PROGRAMMING"),
        ("Signals and Systems", "Signals & Systems"),
        ("Sinyaller ve Sistemler", "Sinyaller & Sistemler"),
        ("IŞIK", "ışık"),
        ("İstatistik", "istatistik"),
        ("İSTATİSTİK", "Istatistik"),
        ("Diferansiyel Denklemler", "DİFERANSİYEL DENKLEMLER"),
        ("Seçmeli Öğe", "Secmeli Oge"),
        ("  Gadget   theory. ", "GADGET THEORY"),
        ("Physics I", "physics i"),
        ("Physics I", "Physics-I"),
        ("Calculus (II)", "Calculus II"),
        ("straße", "STRASSE"),
    )
    DIFFERENT = (
        ("Analysis of Algorithms I", "Analysis of Algorithms II"),
        ("Analysis of Algorithms I", "Analysis of Algorithms"),
        ("Physics II", "Physics 2"),
        ("Physics I", "Physics 1"),
        ("Calculus 1", "Calculus 2"),
        ("Calculus 1", "Calculus 11"),
        ("Database", "Databases"),
        ("Intro to Widgets", "Intro to Gadgets"),
        ("Widget Lab", "Widget"),
    )

    def test_both_tools_use_the_same_key(self) -> None:
        self.assertIs(gpa._name_key, gpa_common.course_name_key)
        self.assertIs(gpa_simulation._name_key, gpa_common.course_name_key)

    def test_spellings_of_one_name_are_equal(self) -> None:
        for left, right in self.SAME:
            with self.subTest(left=left, right=right):
                self.assertEqual(gpa_common.course_name_key(left), gpa_common.course_name_key(right))
                self.assertTrue(gpa_common.course_name_key(left))

    def test_different_courses_stay_different(self) -> None:
        for left, right in self.DIFFERENT:
            with self.subTest(left=left, right=right):
                self.assertNotEqual(gpa_common.course_name_key(left), gpa_common.course_name_key(right))

    def test_no_name_has_no_key(self) -> None:
        for value in (None, "", "  ", "-", ".,;", 0):
            with self.subTest(value=value):
                self.assertEqual(gpa_common.course_name_key(value), "")

    def test_term_tool_note_and_simulation_candidate_follow_the_same_name_rule(self) -> None:
        for old_name, new_name, same in (("Data-Base Systems", "Database Systems", True),
                                         ("R&D Lab", "R and D Lab", True),
                                         ("Widget Design I", "Widget Design II", False)):
            with self.subTest(old=old_name, new=new_name):
                info = plan([met_row("QQT 210E", "CC", 3, "203120", dersAdi=new_name)],
                            [unused_row("YYT 210", "FF", 3, "203110", dersAdi=old_name)])
                notes = gpa.attempt_notes(
                    gpa.plan_rows(info), code="QQT 210E", term_code="203120", names=set(), credits={3.0})
                attempts = gpa_simulation._read_record(info)[0]
                pairs = [(old.code, new.code) for old, new in gpa_simulation._equivalence_candidates(attempts)]
                self.assertEqual("likely_equivalent" in notes, same)
                self.assertEqual(pairs == [("YYT 210", "QQT 210E")], same)


class PlanRowTests(unittest.TestCase):
    """One reader for the graduation-plan rows, used by both tools."""

    def both(self, row: dict[str, Any]) -> tuple[float | None, float | None]:
        """The credit of a plan row as the term tool and as the simulation read it."""
        info = plan([{"bransKodu": "ZZT 101E", "harfNotu": "AA", "donem": "203120", **row}])
        attempts = gpa_simulation._read_record(info)[0]
        return gpa.plan_rows(info)[0]["credit"], attempts[0].credit

    def test_credit_is_read_the_same_way(self) -> None:
        for row, credit in (
            ({"kredisiDec": 3, "kredisi": "5"}, 3.0),
            ({"kredisiDec": None, "kredisi": "3,5"}, 3.5),
            ({"kredisi": 4}, 4.0),
            ({"kredisiDec": "x", "kredisi": "y"}, None),
            ({}, None),
            # A zero credit is a real zero-credit course, not "no credit".
            ({"kredisiDec": 0}, 0.0),
            ({"kredisiDec": 0, "kredisi": "5"}, 0.0),
            ({"kredisi": "0"}, 0.0),
            # An unreadable kredisiDec does not hide kredisi.
            ({"kredisiDec": "x", "kredisi": "5"}, 5.0),
            ({"kredisiDec": float("nan"), "kredisi": "2"}, 2.0),
            ({"kredisiDec": -1, "kredisi": "2"}, 2.0),
            ({"kredisiDec": True, "kredisi": "2"}, 2.0),
        ):
            with self.subTest(row=row):
                self.assertEqual(self.both(row), (credit, credit))
                self.assertEqual(gpa_common.plan_row_credit(row), credit)

    def test_counted_credit_alone_is_not_a_credit(self) -> None:
        # sayilanKredi is a second candidate where a credit exists, never the credit itself.
        info = plan([{"bransKodu": "ZZT 101E", "harfNotu": "AA", "donem": "203120", "sayilanKredi": 3}])
        attempts, _, warnings, open_terms = gpa_simulation._read_record(info)
        self.assertIsNone(gpa.plan_rows(info)[0]["credit"])
        self.assertEqual(gpa.plan_credit_table(gpa.plan_rows(info), "203120"), {})
        self.assertIsNone(attempts[0].credit)
        self.assertEqual(open_terms, {"203120"})
        self.assertTrue(warnings[0].startswith("attempt_without_credit"))

    def test_zero_credit_course_has_a_plan_credit_and_no_weight(self) -> None:
        rows = gpa.plan_rows(plan([met_row("ZZT 100", "BL", 0), met_row("ZZT 101E", "AA", 3)]))
        self.assertEqual(gpa.plan_credit_table(rows, "203120")["ZZT 100"], {"credit": 0.0, "scope": "term"})
        for grade, expected in (("BL", None), ("AA", 4.0)):
            with self.subTest(grade=grade):
                result = gpa.calculate_gpa([{"code": "ZZT 100", "credit": 0.0, "grade": grade},
                                            {"code": "ZZT 101E", "credit": 3.0, "grade": "AA"}])
                self.assertEqual((result["gpa"], result["total_credits"]), (4.0, 3.0))
                self.assertEqual(result["invalid_credits"], [])
                self.assertEqual(expected, next(
                    (course["coefficient"] for course in result["courses"] if course["code"] == "ZZT 100"), None))

    def test_rows_are_read_alike(self) -> None:
        info = plan(
            [met_row(" zzt101e ", " bb ", 3, 203110), met_row("seminar one", "AA", 2, " 203110 "),
             met_row("", "AA", 3), met_row(None, "AA", 3), met_row("ZZT 102E", 5, 3), met_row("ZZT 103E", None, 3),
             42, None],
            [unused_row("ZZT 104E", "FF", "3,5", "203110"), "x"],
        )
        rows = [row for row in gpa.plan_rows(info) if row["code"]]
        attempts = gpa_simulation._read_record(info)[0]
        graded = [(row["code"], row["term"], row["grade"], row["credit"]) for row in rows if row["grade"]]
        self.assertEqual(sorted(graded), sorted((a.key, a.term, a.grade, a.credit) for a in attempts))
        self.assertEqual(sorted(graded), [
            ("SEMINAR ONE", "203110", "AA", 2.0), ("ZZT 101E", "203110", "BB", 3.0),
            ("ZZT 104E", "203110", "FF", 3.5),
        ])
        # A row without a code is a plan slot: listed without a code by the term tool, no attempt.
        self.assertEqual([row["code"] for row in gpa.plan_rows(info)].count(None), 2)

    def test_course_with_a_code_obs_spells_unusually_is_found_by_both(self) -> None:
        info = plan([met_row("Seminar One", "BB", 2, "203110")])
        table = gpa.plan_credit_table(gpa.plan_rows(info), "203110")
        course, missing = gpa_term.term_course(
            {"bransKodu": "seminar", "dersKodu": "one", "kredi": None, "harfNotu": None},
            credit_table=table, plan_grades={"SEMINAR ONE": "BB"}, plan=gpa.plan_rows(info), term_code="203110")
        self.assertEqual((course["credit"], course["grade"], missing), (2.0, "BB", False))
        self.assertEqual(gpa_simulation._read_record(info)[0][0].key, "SEMINAR ONE")

    def test_two_grades_or_two_credits_in_one_term_are_counted_by_neither(self) -> None:
        for second, what in ((unused_row("ZZT 101E", "FF", 3, "203110"), "grade"),
                             (unused_row("ZZT 101E", "CC", 4, "203110"), "credit")):
            with self.subTest(conflict=what):
                info = plan([met_row("ZZT 101E", "CC", 3, "203110"), met_row("ZZT 102E", "AA", 3, "203110")],
                            [second])
                rows = gpa.plan_rows(info)
                grades = {row["grade"] for row in rows if row["code"] == "ZZT 101E"}
                credit = gpa.plan_credit_table(rows, "203110").get("ZZT 101E")
                # The term tool has no single grade, or no single credit, for the course.
                self.assertTrue(len(grades) > 1 or credit is None)
                attempts, _, warnings, open_terms = gpa_simulation._read_record(info)
                self.assertEqual([attempt.key for attempt in attempts], ["ZZT 102E"])
                self.assertEqual(open_terms, {"203110"})
                self.assertEqual(warnings, [
                    "conflicting_attempts: the record states two grades or two credits for one course in "
                    "one term; these attempts were left out: ZZT 101E (203110)"])

    def test_the_same_row_twice_is_one_attempt(self) -> None:
        info = plan([met_row("ZZT 101E", "CC", 3, "203110"), met_row("ZZT 101E", "CC", 3, "203110")],
                    [unused_row("ZZT 101E", "CC", 3, "203110")])
        attempts, _, warnings, open_terms = gpa_simulation._read_record(info)
        self.assertEqual([(a.key, a.source) for a in attempts], [("ZZT 101E", "plan")])
        self.assertEqual((warnings, open_terms), ([], set()))
        self.assertEqual(gpa.plan_credit_table(gpa.plan_rows(info), "203110")["ZZT 101E"]["credit"], 3.0)


class TermOrderTests(unittest.TestCase):
    """Only numeric term codes are put in order, by both tools."""

    def test_numeric_terms_are_ordered_alike(self) -> None:
        terms = ["203120", " 203010 ", "203110", 203020]
        self.assertEqual(sorted(terms, key=gpa_simulation._term_key),
                         sorted(terms, key=lambda term: gpa._term_order(str(term).strip())))

    def test_term_without_a_numeric_code_is_neither_earlier_nor_later(self) -> None:
        for odd in ("yaz", "2030-2031 Güz", "2031/1"):
            with self.subTest(term=odd):
                self.assertIsNone(gpa_common.term_number(odd))
                # The simulation gives it no place among the dated attempts ...
                self.assertEqual(gpa_simulation._term_key(odd), gpa_simulation._term_key(""))
                # ... and the term tool does not call it earlier or later.
                info = plan([met_row("ZZT 101E", "CC", 3, "203120")], [unused_row("ZZT 101E", "FF", 3, odd)])
                notes = gpa.attempt_notes(gpa.plan_rows(info), code="ZZT 101E", term_code="203120",
                                          names=set(), credits=set())
                self.assertEqual(notes, {"other_terms": [{"term": odd, "grade": "FF"}]})

    def test_requested_term_without_a_numeric_code_orders_nothing(self) -> None:
        info = plan([met_row("ZZT 101E", "CC", 3, "yaz")], [unused_row("ZZT 101E", "FF", 3, "203010")])
        notes = gpa.attempt_notes(gpa.plan_rows(info), code="ZZT 101E", term_code="yaz", names=set(), credits=set())
        self.assertEqual(notes, {"other_terms": [{"term": "203010", "grade": "FF"}]})

    def test_term_code_is_matched_without_surrounding_space(self) -> None:
        info = plan([met_row("ZZT 101E", "CC", 3, " 203120 ")])
        self.assertEqual(gpa.plan_credit_table(gpa.plan_rows(info), 203120)["ZZT 101E"]["scope"], "term")
        self.assertEqual(gpa_simulation._read_record(info)[0][0].term, "203120")


class ProjectionKeyTests(unittest.TestCase):
    """A what-if key names the same course for both tools."""

    REGISTERED = [
        {"bransKodu": "ZZT", "dersKodu": "411E", "crn": "40011", "dersAdiEN": "Course A"},
        {"bransKodu": "ZZT", "dersKodu": "412E", "crn": 40012, "dersAdiEN": "Course B"},
        {"bransKodu": "ZZT", "dersKodu": "413E", "crn": "40013", "dersAdiEN": "Course C"},
        {"bransKodu": "ZZT", "dersKodu": "414E", "crn": "40013", "dersAdiEN": "Course D"},
    ]

    def term_tool(self, projected: dict[str, str]) -> tuple[dict[str, str], list[str]]:
        """Matched course to grade, and the unmatched keys, as obs_calculate_gpa sees them."""
        courses = [{"code": f"{row['bransKodu']} {row['dersKodu']}", "crn": row["crn"]} for row in self.REGISTERED]
        translated, unmatched = gpa.resolve_projection_keys(projected, courses)
        return {key: grade for key, grade in translated.items() if key not in unmatched}, unmatched

    def simulation(self, projected: dict[str, str]) -> tuple[dict[str, str], list[str]]:
        """The same, as obs_simulate_cumulative_gpa sees them."""
        registered = gpa_simulation._read_registered({"kayitSinifResultList": self.REGISTERED})
        grades, _, report = gpa_simulation._assign_grades(
            registered, gpa_simulation.normalize_arguments(projected_grades=projected))
        return grades, report["keys"] if report else []

    def test_keys_are_matched_alike(self) -> None:
        for projected, matched, unmatched in (
            ({"40011": "AA", " zzt  412e ": "BB"}, {"ZZT 411E": "AA", "ZZT 412E": "BB"}, []),
            ({'"40011"': "AA", "'ZZT 412E'": "BB", "`40012`": "BB"}, {"ZZT 411E": "AA", "ZZT 412E": "BB"}, []),
            # A CRN two courses share names neither of them.
            ({"40013": "AA"}, {}, ["40013"]),
            ({"40013": "AA", "ZZT 413E": "BB"}, {"ZZT 413E": "BB"}, ["40013"]),
            ({"49999": "AA", "QQQ 999": "BB"}, {}, ["49999", "QQQ 999"]),
            ({"40011": "AA", "ZZT 411E": "AA"}, {"ZZT 411E": "AA"}, []),
        ):
            with self.subTest(projected=projected):
                self.assertEqual(self.term_tool(projected), (matched, unmatched))
                self.assertEqual(self.simulation(projected), (matched, unmatched))

    def test_two_grades_for_one_course_are_refused_by_both(self) -> None:
        for projected in ({"40011": "AA", "ZZT 411E": "BB"}, {'"40011"': "AA", "zzt411e": "BB"}):
            for ask in (self.term_tool, self.simulation):
                with self.subTest(projected=projected, tool=ask.__name__), self.assertRaises(ValueError):
                    ask(projected)


class CreditChoiceTests(unittest.TestCase):
    """One rule for choosing between kredisi and sayilanKredi, used by both tools."""

    def credit_choice(self, rows: list[tuple[str, str, float, float | None]], official: float | None) -> tuple[Any, Any]:
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
            (report["status"] if report else None, [course["credit"] for course in courses],
             ["credit_choice" in course for course in courses if "credit_candidates" in course]),
            ([choice["decided"] for choice in choices], [attempt.credit for attempt in resolved],
             [choice["basis"] for choice in choices]),
        )

    def assert_same_credits(self, term_tool: Any, simulation: Any) -> None:
        self.assertEqual(term_tool[1], simulation[1])

    def test_one_row_with_two_values(self) -> None:
        rows = [("ZZT 301E", "BB", 4, None), ("ZZT 302E", "CC", 3, None), ("ZZT 455E", "DD", 2, 3)]
        # 2 credits gives 2.22, 3 credits gives 2.10.
        for official, status, credit, decided in (
            (2.1, "resolved_by_official_average", 3, True),
            (2.22, "confirmed_by_official_average", 2, True),
            (2.9, "undetermined", 2, False),
            (None, "no_official_average", 2, False),
        ):
            with self.subTest(official=official):
                term_tool, simulation = self.credit_choice(rows, official)
                self.assertEqual(term_tool[:2], (status, [4, 3, credit]))
                self.assertEqual(simulation[:2], ([decided], [4, 3, credit]))

    def test_two_rows_and_one_combination_reproduces_the_average(self) -> None:
        # Weights (3, 2): (16 + 6 + 2) / 9 = 2.67. No other pair of carried values gives it.
        rows = [("ZZT 301E", "AA", 4, None), ("ZZT 302E", "CC", 2, 3), ("ZZT 455E", "DD", 2, 3)]
        term_tool, simulation = self.credit_choice(rows, 2.67)
        self.assertEqual(term_tool, ("resolved_by_official_average", [4, 3, 2], [True, True]))
        self.assertEqual(simulation[:2], ([True, True], [4, 3, 2]))

    def test_two_rows_and_only_one_of_them_can_be_decided(self) -> None:
        # (12 + 2b) / (3 + a + b) is 2.00 for a = 3 whatever b is: the official
        # average fixes the first row and says nothing about the second.
        rows = [("ZZT 301E", "AA", 3, None), ("ZZT 455E", "FF", 2, 3), ("ZZT 456E", "CC", 2, 3)]
        term_tool, simulation = self.credit_choice(rows, 2.0)
        # Both take 3 for the first row, keep the default of the second and say which is which.
        self.assertEqual(term_tool, ("partly_resolved_by_official_average", [3, 3, 2], [True, False]))
        self.assertEqual(simulation[:2], ([True, False], [3, 3, 2]))
        self.assertEqual(simulation[2][1], "both credits reproduce the official term average")

    def test_two_rows_and_every_combination_reproduces_the_average(self) -> None:
        rows = [("ZZT 301E", "CC", 4, None), ("ZZT 455E", "CC", 2, 3), ("ZZT 456E", "CC", 2, 3)]
        term_tool, simulation = self.credit_choice(rows, 2.0)
        self.assertEqual(term_tool, ("undetermined", [4, 2, 2], [False, False]))
        self.assertEqual(simulation[:2], ([False, False], [4, 2, 2]))

    def test_two_rows_and_no_combination_reproduces_the_average(self) -> None:
        rows = [("ZZT 301E", "AA", 4, None), ("ZZT 455E", "DD", 2, 3), ("ZZT 456E", "CC", 2, 3)]
        term_tool, simulation = self.credit_choice(rows, 3.9)
        self.assertEqual(term_tool, ("undetermined", [4, 2, 2], [False, False]))
        self.assertEqual(simulation, ([False, False], [4, 2, 2],
                                      ["neither credit reproduces the official term average"] * 2))

    def test_too_many_rows_are_not_enumerated_by_either(self) -> None:
        rows = [("ZZT 301E", "AA", 4, None)] + [(f"ZZT 45{n}E", "CC", 2, 3) for n in range(7)]
        term_tool, simulation = self.credit_choice(rows, 2.5)
        self.assertEqual(term_tool[:2], ("too_many_candidates", [4] + [2] * 7))
        self.assertEqual(simulation[:2], ([False] * 7, [4] + [2] * 7))
        self.assertEqual(gpa_simulation._MAX_CREDIT_ROWS_PER_TERM, gpa_common.MAX_AMBIGUOUS_CREDIT_ROWS)

    def test_term_with_an_unknown_grade_or_credit_is_not_fitted_by_either(self) -> None:
        # With 3 credits the elective alone would give 2.10; the term has a course without a grade.
        courses: list[dict[str, Any]] = [
            {"code": "ZZT 301E", "grade": "BB", "credit": 4}, {"code": "ZZT 302E", "grade": "CC", "credit": 3},
            {"code": "ZZT 303E", "grade": None, "credit": 3},
            {"code": "ZZT 455E", "grade": "DD", "credit": 2, "credit_candidates": [2, 3]},
        ]
        self.assertEqual(gpa.reconcile_credit_candidates(courses, 2.1)["status"], "not_comparable")  # type: ignore[index]
        info = plan([met_row("ZZT 301E", "BB", 4), met_row("ZZT 302E", "CC", 3), met_row("ZZT 303E", "", 3),
                     met_row("ZZT 455E", "DD", 2, sayilanKredi=3)])
        attempts, _, _, open_terms = gpa_simulation._read_record(info)
        _, choices, _ = gpa_simulation._resolve_credits(attempts, {"203120": {"term_gpa": 2.1}}, open_terms)
        self.assertEqual([(choice["decided"], choice["chosen"]) for choice in choices], [(False, 2.0)])
        self.assertIn("cannot be rebuilt", choices[0]["basis"])

    def test_course_whose_grade_stays_out_of_the_average_has_no_weight_to_choose(self) -> None:
        # The BL elective cannot be decided and does not keep the other one from being decided.
        courses: list[dict[str, Any]] = [
            {"code": "ZZT 301E", "grade": "BB", "credit": 4}, {"code": "ZZT 302E", "grade": "CC", "credit": 3},
            {"code": "ZZT 455E", "grade": "DD", "credit": 2, "credit_candidates": [2, 3]},
            {"code": "ZZT 456E", "grade": "BL", "credit": 2, "credit_candidates": [2, 3]},
        ]
        report = gpa.reconcile_credit_candidates(courses, 2.1)
        self.assertEqual(report["status"], "partly_resolved_by_official_average")  # type: ignore[index]
        self.assertEqual([course["credit"] for course in courses], [4, 3, 3, 2])
        info = plan([met_row("ZZT 301E", "BB", 4), met_row("ZZT 302E", "CC", 3),
                     met_row("ZZT 455E", "DD", 2, sayilanKredi=3), met_row("ZZT 456E", "BL", 2, sayilanKredi=3)])
        attempts, _, _, open_terms = gpa_simulation._read_record(info)
        resolved, choices, _ = gpa_simulation._resolve_credits(attempts, {"203120": {"term_gpa": 2.1}}, open_terms)
        self.assertEqual([(choice["code"], choice["chosen"]) for choice in choices], [("ZZT 455E", 3.0)])
        self.assertEqual({a.key: a.credit for a in resolved}["ZZT 455E"], 3.0)

    def test_one_tolerance_for_both(self) -> None:
        self.assertEqual(gpa_common.AVERAGE_TOLERANCE, 0.005 + 1e-9)
        self.assertIs(gpa_simulation._matches, gpa_common.reproduces_official)
        for calculated, official, same in (
            (2.1, 2.1, True), (20 / 9, 2.22, True), (2.2249, 2.22, True), (2.2251, 2.22, False),
            # An exact half is accepted whichever way OBS rounded it.
            (2.125, 2.13, True), (2.125, 2.12, True), (2.675, 2.68, True), (2.675, 2.67, True),
            (2.125, 2.14, False), (2.1, 2.11, False), (None, 2.1, False), (2.1, None, False), (0.0, 0, True),
        ):
            with self.subTest(calculated=calculated, official=official):
                self.assertEqual(gpa_common.reproduces_official(calculated, official), same)

    def test_exact_half_is_settled_and_reported_alike(self) -> None:
        # (9 + 3 + 2.5c) / (6 + c): 17 / 8 = 2.125 with 2 credits, 2.17 with 3.
        rows = [("ZZT 301E", "BB", 3, None), ("ZZT 302E", "DD", 3, None), ("ZZT 455E", "CB", 2, 3)]
        for official in (2.13, 2.12):
            with self.subTest(official=official):
                term_tool, simulation = self.credit_choice(rows, official)
                self.assertEqual(term_tool[:2], ("confirmed_by_official_average", [3, 3, 2]))
                self.assertEqual(simulation[:2], ([True], [3, 3, 2]))
        # Both tools report the half rounded up.
        result = gpa.calculate_gpa([{"code": code, "grade": grade, "credit": credit}
                                    for code, grade, credit, _ in rows])
        self.assertEqual(result["gpa"], 2.13)
        self.assertEqual(result["gpa"], gpa_simulation._round2(17 / 8))
        self.assertIs(gpa_simulation._round2, gpa_common.round_average)

    def test_comparison_with_the_official_average_uses_the_same_tolerance(self) -> None:
        rows = [("ZZT 301E", "BB", 3), ("ZZT 302E", "DD", 3), ("ZZT 455E", "CB", 2)]  # 17 / 8 = 2.125
        for official, status in ((2.13, "match"), (2.12, "match"), (2.14, "mismatch"), (2.11, "mismatch")):
            with self.subTest(official=official):
                result = gpa.calculate_gpa([{"code": code, "grade": grade, "credit": credit}
                                            for code, grade, credit in rows])
                gpa_reference.attach_official_reference(
                    result, {"status": "reported", "gpa": official, "credits": 8}, projection=False)
                self.assertEqual(result["comparison_status"], status)
                self.assertEqual("calculation_warning" in result, status == "mismatch")

    def test_the_credit_the_average_ruled_out_still_names_the_course(self) -> None:
        # The failed course has the elective's kredisi (2); the official average weights the
        # elective with its sayilanKredi (3). Both tools still offer the pair as likely the same.
        info = plan(
            [met_row("ZZT 301E", "AA", 4), met_row("ZZT 455E", "CC", 2, sayilanKredi=3, dersAdi="Sprockets")],
            [unused_row("YYT 240", "FF", 2, "203110", dersAdi="Sprockets")],
        )
        attempts, _, _, open_terms = gpa_simulation._read_record(info)
        resolved, choices, _ = gpa_simulation._resolve_credits(attempts, {"203120": {"term_gpa": 3.14}}, open_terms)
        self.assertEqual(choices[0]["chosen"], 3.0)  # 22 / 7
        pairs = [(old.code, new.code) for old, new in gpa_simulation._equivalence_candidates(resolved)]
        self.assertEqual(pairs, [("YYT 240", "ZZT 455E")])
        rows = gpa.plan_rows(info)
        course, _ = gpa_term.term_course(
            {"bransKodu": "ZZT", "dersKodu": "455E", "dersAdiTR": "Sprockets"},
            credit_table=gpa.plan_credit_table(rows, "203120"), plan_grades={}, plan=rows, term_code="203120")
        self.assertEqual([note["code"] for note in course["attempts"]["likely_equivalent"]], ["YYT 240"])


class GradeReadingTests(unittest.TestCase):
    """Only text is a grade; anything else is "no grade yet"."""

    NOT_TEXT: tuple[Any, ...] = (5, 0, 4.0, False, True, ["AA"], {"AA": 1}, b"AA")

    def test_non_text_grade_is_ungraded_not_unrecognized(self) -> None:
        for grade in (*self.NOT_TEXT, None, "", "  "):
            with self.subTest(grade=grade):
                result = gpa.calculate_gpa([{"code": "ZZT 101E", "credit": 3, "grade": grade}])
                self.assertEqual(result["unrecognized_grades"], [])
                self.assertEqual([course["code"] for course in result["ungraded"]], ["ZZT 101E"])
                self.assertEqual(result["calculation_status"], "incomplete")

    def test_text_that_is_no_grade_is_unrecognized(self) -> None:
        for grade in ("ZZ", "5", "FALSE", "A"):
            with self.subTest(grade=grade):
                result = gpa.calculate_gpa([{"code": "ZZT 101E", "credit": 3, "grade": grade}])
                self.assertEqual(result["unrecognized_grades"], [{"code": "ZZT 101E", "grade": grade}])
                self.assertEqual(result["ungraded"], [])

    def test_text_grade_is_normalized_and_the_obs_field_is_read(self) -> None:
        result = gpa.calculate_gpa([
            {"code": "ZZT 101E", "credit": 3, "grade": " bb "},
            {"code": "ZZT 102E", "credit": 3, "grade": 5, "harfNotu": "aa"},
            {"code": "ZZT 103E", "credit": 3, "harfNotu": "CC"},
        ])
        self.assertEqual([course["grade"] for course in result["courses"]], ["BB", "AA", "CC"])
        self.assertEqual(result["gpa"], 3.0)

    def test_projected_grade_still_overrides(self) -> None:
        result = gpa.calculate_gpa([{"code": "ZZT 101E", "credit": 3, "grade": 5}],
                                   projected_grades={"ZZT 101E": "AA"})
        self.assertEqual((result["gpa"], result["ungraded"]), (4.0, []))

    def test_plan_row_grade_is_read_alike(self) -> None:
        for grade in self.NOT_TEXT:
            with self.subTest(grade=grade):
                info = plan([met_row("ZZT 101E", grade, 3)])
                self.assertEqual(gpa.plan_rows(info)[0]["grade"], "")
                self.assertEqual(gpa_simulation._read_record(info)[0], [])

    def test_non_text_registration_grade_falls_back_to_the_plan_grade(self) -> None:
        for grade in (*self.NOT_TEXT, None, "", " "):
            with self.subTest(grade=grade):
                course, _ = gpa_term.term_course(
                    {"bransKodu": "ZZT", "dersKodu": "101E", "kredi": "3", "harfNotu": grade},
                    credit_table={}, plan_grades={"ZZT 101E": "CC"}, plan=[], term_code="203120")
                self.assertEqual((course["grade"], course["grade_source"]), ("CC", "degree_plan"))


if __name__ == "__main__":
    unittest.main()
