"""The term average and the cumulative simulation must tell one story.

``obs_calculate_gpa`` and ``obs_simulate_cumulative_gpa`` read the same OBS
record. For every past term of several invented students this module asks
both and compares what they say: the term average, the credit each course
was weighted with, which of two credit values the official average settled,
and which courses they take for the same course under another code.

A failure names the student, the term, the course and the two values. When
one appears, the two tools have drifted apart: fix the shared rule in
``gpa_common``, do not adjust one tool to the other here.

Every student, course code, name, CRN, term and grade below is invented.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, Mock

from ninova_mcp.gpa_common import course_key, parse_credit, round_average, term_number
from ninova_mcp.gpa_simulation import rebuild_record, simulate_cumulative_gpa
from ninova_mcp.grading import GRADE_VALUES, LETTER_TO_GRADE
from ninova_mcp.obs_client import ObsClient
from ninova_mcp.server import NinovaMcpApp

NEXT_TERM = "203310"


@dataclass(frozen=True)
class Taken:
    """One course in one term, as the student's record holds it.

    ``credit`` is ``kredisi`` and ``counted`` is ``sayilanKredi`` where the
    row carries another value. ``weight`` is what the university really
    weighted the course with; the two tools never see it, only the official
    averages built from it. ``course`` is the true identity of a course whose
    code changed. ``used`` is False for a row of the unused list.
    """

    term: str
    code: str
    name: str
    grade: str
    credit: Any
    counted: Any = None
    weight: float | None = None
    course: str | None = None
    used: bool = True

    @property
    def true_weight(self) -> float:
        if self.weight is not None:
            return self.weight
        return parse_credit(self.credit) or 0.0

    @property
    def coefficient(self) -> float | None:
        return LETTER_TO_GRADE.get(self.grade)


def average(rows: list[Taken]) -> float | None:
    counted = [(row.true_weight, row.coefficient) for row in rows if row.coefficient is not None]
    credits = sum(weight for weight, _ in counted)
    return round_average(sum(weight * value for weight, value in counted) / credits) if credits else None


@dataclass(frozen=True)
class Student:
    name: str
    record: tuple[Taken, ...]
    # Official term averages that differ from the true ones, to build a term no credit explains.
    stated_term_average: tuple[tuple[str, float], ...] = ()

    @property
    def terms(self) -> list[str]:
        return sorted({row.term for row in self.record})

    def official_rows(self) -> list[dict[str, Any]]:
        rows = []
        stated = dict(self.stated_term_average)
        for term in self.terms:
            in_term = [row for row in self.record if row.term == term]
            latest: dict[str, Taken] = {}
            for row in sorted(self.record, key=lambda row: row.term):
                if row.term <= term and row.coefficient is not None:
                    latest[row.course or row.code] = row
            rows.append({
                "akademikDonemKodu": term,
                "donemlikNotOrtalamasi": stated.get(term, average(in_term) or 0),
                "genelNotOrtalamasi": average(list(latest.values())) or 0,
                "verilenKredi": sum(row.true_weight for row in in_term if (row.coefficient or 0) >= 1),
            })
        return rows

    def status(self) -> dict[str, Any]:
        return {"statusCode": 0, "kayitDurumuList": [{
            "akademikProgramAdi": "Sample Program", "kayitDurumuDonemList": self.official_rows()}]}

    def graduation_info(self) -> dict[str, Any]:
        met_rows, unused_rows = [], []
        for row in self.record:
            if row.used:
                plan_row = {
                    "isMet": True, "bransKodu": row.code, "dersAdi": row.name, "harfNotu": row.grade,
                    "donem": row.term, "kredisi": str(row.credit), "grupName": "",
                    "sayilanKredi": row.credit if row.counted is None else row.counted,
                }
                if not isinstance(row.credit, str):  # a decimal-comma credit comes without kredisiDec
                    plan_row["kredisiDec"] = row.credit
                met_rows.append(plan_row)
            else:
                unused_rows.append({"bransKodu": row.code, "dersAdi": row.name, "harfNotu": row.grade,
                                    "donem": row.term, "kredisi": row.credit, "isValidAndUnused": False})
        return {"gpa": self.official_rows()[-1]["genelNotOrtalamasi"],
                "checkMetMezuniyetList": met_rows, "unusedSinifOgrenciList": unused_rows}

    def registered(self, term: str) -> list[dict[str, Any]]:
        """The registered-course rows of a term: no credit and no grade, as OBS returns them."""
        rows = []
        for index, row in enumerate(taken for taken in self.record if taken.term == term):
            branch, _, number = row.code.partition(" ")
            rows.append({"bransKodu": branch, "dersKodu": number, "crn": str(50000 + index),
                         "dersAdiTR": row.name, "dersAdiEN": row.name, "kredi": None, "harfNotu": None})
        return rows

    def app(self) -> NinovaMcpApp:
        """An app whose OBS client never leaves the process."""
        app = NinovaMcpApp.__new__(NinovaMcpApp)
        obs = ObsClient(ninova_client=MagicMock())
        ids = {index + 1: term for index, term in enumerate(self.terms)}
        obs.list_semesters = Mock(return_value={"ogrenciDonemListesi": [  # type: ignore[method-assign]
            {"akademikDonemId": term_id, "donemKodu": term, "akademikDonemAdi": f"Term {term}"}
            for term_id, term in ids.items()]})
        obs.list_registered_courses = Mock(side_effect=lambda term_id: {  # type: ignore[method-assign]
            "statusCode": 0, "kayitSinifResultList": self.registered(ids[term_id])})
        obs.default_program_id = Mock(return_value=1)  # type: ignore[method-assign]
        obs.get_graduation_remaining = Mock(  # type: ignore[method-assign]
            return_value={"mezuniyetimeNeKaldiBilgi": self.graduation_info()})
        obs.get_registration_status = Mock(return_value=self.status())  # type: ignore[method-assign]
        app._obs = obs
        return app


def taken(term: str, code: str, grade: str, credit: Any, **extra: Any) -> Taken:
    return Taken(term=term, code=code, name=extra.pop("name", f"Course {code}"), grade=grade, credit=credit, **extra)


STUDENTS = (
    # Retakes of a failed and of a passed course, a code change with the same
    # name, a zero-credit pass/fail row, a decimal-comma credit, a term with
    # every course failed.
    Student("retakes and a code change", (
        taken("203010", "ZZT 101E", "BB", 4),
        taken("203010", "ZZT 103E", "FF", 3, used=False),
        taken("203010", "YYT 105", "FF", 3, name="Gadget Theory", course="gadget", used=False),
        taken("203010", "ZZT 100", "BL", 0),
        taken("203020", "ZZT 103E", "CC", 3),
        taken("203020", "ZZT 102E", "DC", "3,5"),
        taken("203020", "QQT 105", "CB", 3, name="Gadget  theory.", course="gadget"),
        taken("203020", "ZZT 104E", "DD", 2, used=False),
        taken("203110", "ZZT 104E", "BA", 2),
        taken("203110", "ZZT 201E", "DD", 4),
        taken("203120", "ZZT 205", "FF", 2, used=False),
        taken("203120", "ZZT 207E", "VF", 3, used=False),
        taken("203210", "ZZT 205", "CC", 2),
        taken("203210", "ZZT 207E", "FD", 3, used=False),
    )),
    # Two elective rows with two credit values in one term; the official
    # average is reproduced by one combination only: (3, 2) gives 24 / 9 = 2.67.
    Student("two electives, both decided", (
        taken("203010", "ZZT 111E", "CC", 3),
        taken("203010", "YYT 240", "FF", 2, name="Elective Sprockets", used=False),
        taken("203020", "ZZT 301E", "AA", 4),
        taken("203020", "ZZT 302E", "CC", 2, counted=3, weight=3, name="Elective Sprockets"),
        taken("203020", "ZZT 455E", "DD", 2, counted=3, weight=2),
    )),
    # (12 + 2b) / (3 + a + b) is 2.00 for a = 3 whatever b is: the official
    # average fixes the first elective and says nothing about the second.
    Student("two electives, one decided", (
        taken("203010", "ZZT 111E", "BB", 3),
        taken("203020", "ZZT 301E", "AA", 3),
        taken("203020", "ZZT 455E", "FF", 2, counted=3, weight=3),
        taken("203020", "ZZT 456E", "CC", 2, counted=3, weight=3),
    )),
    # Two electives with the same grade are interchangeable: (2, 3, 3) and (3, 2, 3) both give
    # 29 / 12 = 2.42. Only the third is fixed, and the defaults (2, 2, 3) give 2.45, not 2.42.
    Student("three electives, the interchangeable two stay open", (
        taken("203010", "ZZT 111E", "BB", 3),
        taken("203020", "ZZT 301E", "AA", 4),
        taken("203020", "ZZT 454E", "CC", 2, counted=3, weight=2),
        taken("203020", "ZZT 455E", "CC", 2, counted=3, weight=3),
        taken("203020", "ZZT 456E", "DD", 2, counted=3, weight=3),
    )),
    # Every course of the term has the same grade: every combination gives 2.00.
    Student("two electives, both credits reproduce", (
        taken("203010", "ZZT 111E", "BB", 3),
        taken("203020", "ZZT 301E", "CC", 4),
        taken("203020", "ZZT 455E", "CC", 2, counted=3, weight=3),
        taken("203020", "ZZT 456E", "CC", 2, counted=3, weight=2),
    )),
    # The official term average is one no combination of the carried values gives.
    Student("two electives, no credit reproduces", (
        taken("203010", "ZZT 111E", "BB", 3),
        taken("203020", "ZZT 301E", "AA", 4),
        taken("203020", "ZZT 455E", "DD", 2, counted=3),
        taken("203020", "ZZT 456E", "CC", 2, counted=3),
    ), stated_term_average=(("203020", 3.9),)),
    # A failed elective with two credit values in a term where everything was failed.
    Student("an elective in an all-failed term", (
        taken("203010", "ZZT 111E", "FF", 3, used=False),
        taken("203010", "ZZT 455E", "FF", 2, counted=3, weight=3),
        taken("203020", "ZZT 111E", "CB", 3),
    )),
    # An exact half: 17 / 8 = 2.125, which OBS states as 2.13.
    Student("an average that is an exact half", (
        taken("203010", "ZZT 111E", "BB", 3),
        taken("203010", "ZZT 112E", "DD", 3),
        taken("203010", "ZZT 455E", "CB", 2, counted=3),
    )),
)


def decided_by_term_tool(result: dict[str, Any]) -> dict[str, bool]:
    """Course key to "the official average settled its credit", from obs_calculate_gpa."""
    ambiguity = result.get("credit_ambiguity")
    if not ambiguity:
        return {}
    every = ambiguity["status"] in ("resolved_by_official_average", "confirmed_by_official_average")
    marked = {course_key(course["code"]) for course in result["courses"] if "credit_choice" in course}
    return {course_key(entry["code"]): every or course_key(entry["code"]) in marked
            for entry in ambiguity["courses"]}


class CrossToolConsistencyTests(unittest.TestCase):
    def test_both_tools_agree_on_every_past_term(self) -> None:
        for student in STUDENTS:
            app = student.app()
            record = rebuild_record(student.graduation_info(), student.status())
            simulation = simulate_cumulative_gpa(
                student.graduation_info(), student.status(), {"kayitSinifResultList": []}, term_code=NEXT_TERM)
            for term in student.terms:
                with self.subTest(student=student.name, term=term):
                    self.compare_term(student, term, app.obs_calculate_gpa(term), record, simulation)

    def compare_term(self, student: Student, term: str, result: dict[str, Any], record: Any,
                     simulation: dict[str, Any]) -> None:
        where = f"{student.name}, term {term}"
        attempts = {attempt.key: attempt for attempt in record.attempts if attempt.term == term}

        # 1. The credit of every course.
        listed = result["courses"] + result["excluded_courses"]
        self.assertEqual(
            sorted(course_key(course["code"]) for course in listed), sorted(attempts),
            f"{where}: the two tools do not see the same courses")
        self.assertEqual(result["ungraded"], [], f"{where}: the term tool left a course ungraded")
        for course in listed:
            attempt = attempts[course_key(course["code"])]
            self.assertEqual(
                parse_credit(course["credit"]), attempt.credit,
                f"{where}, {course['code']}: obs_calculate_gpa weights it with {course['credit']}, "
                f"the simulation with {attempt.credit}")

        # 2. The term average.
        counted = [(a.credit, a.coefficient) for a in attempts.values()
                   if a.coefficient is not None and a.credit is not None]
        credits = sum(credit for credit, _ in counted)
        implied = round_average(sum(credit * value for credit, value in counted) / credits) if credits else None
        self.assertEqual(
            result["gpa"], implied,
            f"{where}: obs_calculate_gpa says {result['gpa']}, the simulation's record gives {implied}")

        # 3. Which credits the official average settled.
        choices = {course_key(choice["code"]): choice["decided"]
                   for choice in record.credit_choices if choice["term"] == term}
        by_term_tool = decided_by_term_tool(result)
        for code in sorted(set(choices) | set(by_term_tool)):
            self.assertEqual(
                by_term_tool.get(code), choices.get(code),
                f"{where}, {code}: credit settled by the official average? obs_calculate_gpa says "
                f"{by_term_tool.get(code)}, the simulation says {choices.get(code)}")

        # 4. Which earlier failed course is taken for the same course under another code.
        noted = {
            (note["code"], course_key(course["code"]))
            for course in result["courses"]
            for note in (course.get("attempts") or {}).get("likely_equivalent", [])
            if (term_number(note["term"]) or 0) < int(term)
        }
        paired = {
            (course_key(item["old_code"]), course_key(item["new_code"]))
            for item in simulation["equivalences"] + simulation.get("equivalence_candidates_not_applied", [])
            if item["new_term"] == term
        }
        for old, new in sorted(noted ^ paired):
            self.assertEqual(
                (old, new) in noted, (old, new) in paired,
                f"{where}, {new}: is the failed {old} the same course? obs_calculate_gpa says "
                f"{(old, new) in noted}, the simulation says {(old, new) in paired}")

    def test_the_students_cover_what_they_are_meant_to(self) -> None:
        """The comparison above is only worth something if the situations really occur."""
        seen: dict[str, Any] = {}
        for student in STUDENTS:
            app = student.app()
            simulation = simulate_cumulative_gpa(
                student.graduation_info(), student.status(), {"kayitSinifResultList": []}, term_code=NEXT_TERM)
            seen[student.name] = {
                "model": simulation["current"]["model_status"],
                "equivalences": [(item["old_code"], item["new_code"]) for item in simulation["equivalences"]],
                "not_applied": [(item["old_code"], item["new_code"])
                                for item in simulation.get("equivalence_candidates_not_applied", [])],
                "choices": [(choice["code"], choice["chosen"], choice["decided"])
                            for choice in simulation.get("credit_choices", [])],
                "terms": {term: app.obs_calculate_gpa(term) for term in student.terms},
            }

        def status(name: str, term: str) -> str:
            return seen[name]["terms"][term]["credit_ambiguity"]["status"]

        retakes = seen["retakes and a code change"]
        self.assertEqual(retakes["model"], "reproduced")
        self.assertEqual(retakes["equivalences"], [("YYT 105", "QQT 105")])
        # 12 / 10, then 3 CC + 3.5 DC + 3 CB + 2 DD over 11.5, and a term of failures only.
        self.assertEqual([retakes["terms"][term]["gpa"] for term in ("203010", "203020", "203120")],
                         [1.2, 1.8, 0.0])
        self.assertEqual(retakes["terms"]["203120"]["official_term_gpa"], 0)
        self.assertEqual(retakes["terms"]["203010"]["excluded_courses"][0]["credit"], 0.0)

        both = seen["two electives, both decided"]
        self.assertEqual(both["choices"], [("ZZT 302E", 3.0, True), ("ZZT 455E", 2.0, True)])
        self.assertEqual(status("two electives, both decided", "203020"), "resolved_by_official_average")
        self.assertEqual(both["terms"]["203020"]["gpa"], 2.67)
        self.assertEqual(both["not_applied"], [("YYT 240", "ZZT 302E")])

        one = seen["two electives, one decided"]
        self.assertEqual(one["choices"], [("ZZT 455E", 3.0, True), ("ZZT 456E", 2.0, False)])
        self.assertEqual(status("two electives, one decided", "203020"), "partly_resolved_by_official_average")

        open_two = seen["three electives, the interchangeable two stay open"]
        self.assertEqual(open_two["choices"],
                         [("ZZT 454E", 2.0, False), ("ZZT 455E", 2.0, False), ("ZZT 456E", 3.0, True)])
        term = open_two["terms"]["203020"]
        self.assertEqual(term["credit_ambiguity"]["status"], "partly_resolved_by_official_average")
        self.assertEqual((term["gpa"], term["official_term_gpa"], term["comparison_status"]),
                         (2.45, 2.42, "mismatch"))
        # The settled credit is no longer offered as one of two possible values.
        self.assertEqual(term["mismatch_ambiguous_credit_courses"], ["ZZT 454E", "ZZT 455E"])
        self.assertNotIn("ZZT 456E", term["calculation_warning"])

        self.assertEqual(seen["two electives, both credits reproduce"]["choices"],
                         [("ZZT 455E", 2.0, False), ("ZZT 456E", 2.0, False)])
        self.assertEqual(status("two electives, both credits reproduce", "203020"), "undetermined")
        self.assertEqual(seen["two electives, no credit reproduces"]["choices"],
                         [("ZZT 455E", 2.0, False), ("ZZT 456E", 2.0, False)])
        self.assertEqual(status("two electives, no credit reproduces", "203020"), "undetermined")
        self.assertEqual(seen["an elective in an all-failed term"]["choices"], [("ZZT 455E", 2.0, False)])
        self.assertEqual(status("an elective in an all-failed term", "203010"), "undetermined")

        half = seen["an average that is an exact half"]
        self.assertEqual(half["terms"]["203010"]["gpa"], 2.13)
        self.assertEqual(half["choices"], [("ZZT 455E", 2.0, True)])
        self.assertEqual(status("an average that is an exact half", "203010"), "confirmed_by_official_average")
        self.assertEqual((GRADE_VALUES["BB"] * 3 + GRADE_VALUES["DD"] * 3 + GRADE_VALUES["CB"] * 2) / 8, 2.125)


if __name__ == "__main__":
    unittest.main()
