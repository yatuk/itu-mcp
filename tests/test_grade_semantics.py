"""Synthetic regression cases for consistent grade and course-code interpretation."""
from __future__ import annotations

from functools import wraps
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from ninova_mcp.gpa import calculate_gpa
from ninova_mcp.graduation import completed_course_history
from ninova_mcp.prerequisites import evaluate_tree
from ninova_mcp.registration_plan import _evaluate, validate_registration_plan
from ninova_mcp.registration_tools import _history
from ninova_mcp.server import NinovaMcpApp


def leaf(minimum="DD", code="BLG 101E"):
    return {"type": "course", "code": code, "min_grade": minimum}


def cases(names, rows):
    """Run each synthetic case under unittest without an extra test dependency."""
    names = names.split(",")

    def decorate(fn):
        @wraps(fn)
        def check(self):
            for row in rows:
                values = row if isinstance(row, tuple) else (row,)
                with self.subTest(**dict(zip(names, values))):
                    fn(self, *values)
        return check
    return decorate


class GradeSemanticsTests(unittest.TestCase):
    @cases("grade", ["BA+", "BB+", "CB+", "CC+", "DC+", "DD+"])
    def test_every_plus_grade_satisfies_dd_in_both_evaluators_and_future_chain(self, grade):
        completed = {"BLG 101E": grade}
        assert evaluate_tree(leaf(), completed)["satisfied"] is True
        verdict = _evaluate(leaf(), completed)
        assert verdict == {"satisfied": True, "missing_courses": [], "unknown_courses": []}
        result = validate_registration_plan(
            [{"crn": "10001", "code": "BLG 202E", "sessions": []}],
            completed_courses=completed,
            graduation={"remaining_required_courses": [{"course_code": "BLG 201E"}]},
            dependency_rules={"BLG 201E": {"requirement_tree": leaf()}},
        )
        chain = result["prerequisite_chains"]["chains"][0]
        assert chain["status"] == "already_satisfied"
        assert chain["deferred_or_unmet_prerequisites"] == []
        assert chain["unverified_prerequisites"] == []


    @cases("grade,minimum,expected", [
        ("BB", "BB+", False), ("BB+", "BB+", True), ("BA", "BB+", True),
        ("AA+", "DD", None), ("BL", "BB", None), (None, "BB", None),
        ("AA", "XY", None), ("FF", None, False), ("BZ", None, False),
        ("E", None, False), (None, None, True), ("BL", None, True), ("BL", "BL", True),
    ])
    def test_minimum_and_unknown_grade_semantics_match(self, grade, minimum, expected):
        completed = {"BLG 101E": grade}
        assert evaluate_tree(leaf(minimum), completed)["satisfied"] is expected
        result = _evaluate(leaf(minimum), completed)
        assert result["satisfied"] is expected
        if expected is None:
            assert result["missing_courses"] == []
            assert result["unknown_courses"] == ["BLG 101E"]


    @cases("kind,known_grade,expected", [
        ("or", "AA", True), ("or", "FF", None),
        ("and", "AA", None), ("and", "FF", False),
    ])
    def test_unknown_grade_respects_and_or_truth_tables(self, kind, known_grade, expected):
        expression = {"type": kind, "operands": [leaf(), leaf(code="MAT 101E")]}
        completed = {"BLG 101E": known_grade, "MAT 101E": "unexpected"}
        assert evaluate_tree(expression, completed)["satisfied"] is expected
        assert _evaluate(expression, completed)["satisfied"] is expected


    @cases("reverse", [False, True])
    def test_shared_history_chooses_plus_grade_and_includes_only_valid_unused_attempts(self, reverse):
        rows = [
            {"bransKodu": "blg101e", "harfNotu": "BA+", "isMet": True},
            {"bransKodu": "BLG 101E", "harfNotu": "BB", "isMet": True},
            {"bransKodu": "BLG 102E", "harfNotu": "E", "isMet": True},
            {"bransKodu": "BLG 103E", "harfNotu": "FF", "isMet": True},
        ]
        info = {"checkMetMezuniyetList": list(reversed(rows)) if reverse else rows,
                "unusedSinifOgrenciList": [
                    {"bransKodu": "mat101e", "harfNotu": "BB+", "isValidAndUnused": True},
                    {"bransKodu": "MAT 102E", "harfNotu": "AA", "isValidAndUnused": False},
                    {"bransKodu": "MAT 103E", "harfNotu": "BZ", "isValidAndUnused": True},
                ]}
        payload = {"mezuniyetimeNeKaldiBilgi": info}
        expected = {"BLG 101E": "BA+", "MAT 101E": "BB+"}
        assert completed_course_history(info) == expected
        assert _history(payload)[1] == expected
        app = SimpleNamespace(obs=Mock(), obs_public=Mock(), prereq_crosscheck=Mock(), archive=Mock())
        app.obs.get_graduation_remaining.return_value = payload
        app.obs_public.get_branch_prerequisites.return_value = {
            "rules": {"BLG 201E": {"requirement_tree": {"type": "and", "operands": [leaf("BA+"), leaf("BB+", "MAT 101E")]}}},
        }
        app.prereq_crosscheck.get_course_prerequisite_tree.return_value = None
        app.archive.get_course_history.return_value = {}
        result = NinovaMcpApp.explain_course_eligibility(app, "BLG201E", use_obs_history=True)
        assert result["completed_grades"] == expected
        assert result["eligible"] is True


    @cases("grade", ["BL", "BZ", "T", "E", "M"])
    def test_excluded_grades_do_not_dilute_gpa_even_with_positive_credit(self, grade):
        result = calculate_gpa([
            {"code": "BLG 101E", "credit": 3, "grade": "AA"},
            {"code": "MAT 101E", "credit": 3, "grade": grade},
        ])
        assert result["gpa"] == 4.0
        assert result["total_credits"] == 3
        assert result["graded_course_count"] == 1


    def test_projection_normalizes_spaces_case_and_suffix_without_merging_language_variants(self):
        result = calculate_gpa([
            {"code": "BLG 101E", "credit": 3, "grade": "BB"},
            {"code": "BLG 101", "credit": 3, "grade": "CC"},
        ], projected_grades={"blg101 e": "aa", "MAT101E": "BA+"})
        assert result["gpa"] == 3.0
        assert result["courses"][0]["projected"] is True
        assert result["courses"][1]["projected"] is False
        assert result["unused_projected_courses"] == ["MAT 101E"]


    def test_conflicting_or_unrecognized_projection_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Conflicting"):
            calculate_gpa([], projected_grades={"BLG101E": "AA", "BLG 101E": "BB"})
        with self.assertRaisesRegex(ValueError, "recognized"):
            calculate_gpa([], projected_grades={"BLG101E": "AA+"})


    def test_unknown_actual_grade_is_reported_separately_from_known_exclusions(self):
        result = calculate_gpa([{"code": "BLG 101E", "credit": 3, "grade": "XY"}])
        assert result["gpa"] is None
        assert result["unrecognized_grades"] == [{"code": "BLG 101E", "grade": "XY"}]

    def test_gpa_keeps_credit_provenance_without_substituting_counted_credit(self):
        result = calculate_gpa([{
            "code": "BLG 101E", "credit": 2.5, "grade": "AA",
            "recorded_credit": None, "plan_credit": 2.5, "counted_credit": 3.0,
            "credit_source": "degree_plan", "grade_source": "degree_plan",
        }])
        row = result["courses"][0]
        assert row["credit"] == 2.5
        assert row["counted_credit"] == 3.0
        assert row["credit_source"] == "degree_plan"
        assert row["recorded_credit"] is None
        assert result["total_credits"] == 2.5

    @cases("credits,expected", [(None, None), (60, None), (30, False)])
    def test_unknown_grade_and_known_credit_failure_keep_distinct_verdicts(self, credits, expected):
        app = SimpleNamespace(obs_public=Mock(), prereq_crosscheck=Mock(), archive=Mock())
        app.obs_public.get_branch_prerequisites.return_value = {
            "rules": {"BLG 201E": {"requirement_tree": leaf("BB"), "credit_requirement": 60}},
        }
        app.prereq_crosscheck.get_course_prerequisite_tree.return_value = None
        app.archive.get_course_history.return_value = {}
        result = NinovaMcpApp.explain_course_eligibility(
            app, "BLG201E", completed_courses=["BLG101E:BL"], completed_credits=credits,
        )
        assert result["eligible"] is expected
        assert result["missing_courses"] == []
        assert result["unknown_courses"] == ["BLG 101E"]
