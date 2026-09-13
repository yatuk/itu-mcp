from __future__ import annotations

import unittest

from ninova_mcp.registration_plan import validate_registration_plan


def course(code: str, minimum: str | None = None) -> dict:
    return {"type": "course", "code": code, "min_grade": minimum}


def rule(tree: dict | None = None, **kwargs) -> dict:
    return {"requirement_tree": tree, "credit_requirement": None, **kwargs}


def section(crn: str = "10001", code: str = "BLG 201", time: str = "09:00/10:00") -> dict:
    return {"crn": crn, "code": code, "sessions": [{"day": "Monday", "time": time}]}


def graduation(**kwargs) -> dict:
    return {
        "open_elective_slots": [], "remaining_required_courses": [],
        "credits_required": 150, "credits_earned": 100, "credits_remaining": 50,
        "gpa": 3.0, "required_gpa": 2.0,
        "internship_days_required": 40, "internship_days_done": 20,
        "english_credits_required": 45, "english_credits_earned": 30,
        **kwargs,
    }


def analyze(sections: list[dict] | None = None, **kwargs) -> dict:
    selected = sections if sections is not None else [section()]
    defaults = {
        "completed_courses": {},
        "prerequisite_rules": {item["code"]: rule() for item in selected},
        "eligibility": {item["crn"]: {"eligible": True} for item in selected},
        "graduation": graduation(),
        "elective_groups": {},
        "dependency_rules": {},
    }
    defaults.update(kwargs)
    return validate_registration_plan(selected, **defaults)


class RegistrationPlanTests(unittest.TestCase):
    def test_fully_known_registration_can_pass_with_graduation_work_remaining(self) -> None:
        result = analyze()
        self.assertEqual(result["status"], "valid")
        self.assertTrue(result["valid"])
        self.assertTrue(result["read_only"])
        self.assertEqual(result["graduation_requirements"]["current_progress"]["credits_remaining"], 50)
        self.assertFalse(result["graduation_requirements"]["current_internship_requirement_met"])

    def test_unknown_official_verdict_is_never_valid(self) -> None:
        result = analyze(eligibility={})
        self.assertEqual(result["status"], "incomplete")
        self.assertIsNone(result["valid"])
        self.assertIn("BLG 201: program eligibility is unknown.", result["unknowns"])

    def test_known_blocker_survives_missing_data(self) -> None:
        result = analyze(eligibility={"10001": {"eligible": False}}, graduation=None)
        self.assertEqual(result["status"], "invalid")
        self.assertFalse(result["valid"])
        self.assertTrue(result["unknowns"])

    def test_same_term_prerequisite_does_not_count_as_completed(self) -> None:
        result = analyze(
            [section("10001", "BLG 201"), section("10002", "BLG 202", "10:00/11:00")],
            prerequisite_rules={"BLG 201": rule(), "BLG 202": rule(course("BLG 201", "DD"))},
        )
        self.assertEqual(result["status"], "invalid")
        prerequisite = result["course_checks"][1]["checks"]["prerequisites"]
        self.assertEqual(prerequisite["missing_courses"], ["BLG 201"])

    def test_exact_and_or_and_minimum_grade(self) -> None:
        expression = {"type": "and", "operands": [
            {"type": "or", "operands": [course("BLG 101", "BB"), course("BLG 102", "DD")]},
            course("MAT 101", "CC"),
        ]}
        rules = {"BLG 201": rule(expression)}
        accepted = analyze(prerequisite_rules=rules, completed_courses={"BLG101": "CC", "BLG102": "DD", "MAT101": "CB"})
        self.assertEqual(accepted["status"], "valid")
        rejected = analyze(prerequisite_rules=rules, completed_courses={"BLG101": "CC", "BLG102": "DD", "MAT101": "DD"})
        self.assertEqual(rejected["status"], "invalid")

    def test_unknown_grade_and_non_numeric_pass_do_not_prove_minimum_grade(self) -> None:
        for grade in (None, "BL", "GE", "AA+", "unexpected"):
            with self.subTest(grade=grade):
                result = analyze(
                    prerequisite_rules={"BLG 201": rule(course("BLG 101", "BB"))},
                    completed_courses={"BLG 101": grade},
                )
                self.assertEqual(result["status"], "incomplete")

    def test_failing_grade_does_not_pass_without_explicit_minimum(self) -> None:
        result = analyze(
            prerequisite_rules={"BLG 201": rule(course("BLG 101"))},
            completed_courses={"BLG 101": "FF"},
        )
        self.assertEqual(result["status"], "invalid")

    def test_unavailable_history_is_not_confirmed_empty_history(self) -> None:
        rules = {"BLG 201": rule(course("BLG 101"))}
        self.assertEqual(analyze(prerequisite_rules=rules, completed_courses=None)["status"], "incomplete")
        self.assertEqual(analyze(prerequisite_rules=rules, completed_courses={})["status"], "invalid")

    def test_missing_or_malformed_rule_is_unknown(self) -> None:
        for rules in ({}, {"BLG 201": {}}, {"BLG 201": rule({"type": "or", "operands": []})}, {"BLG 201": rule({"type": "other", "operands": []})}):
            with self.subTest(rules=rules):
                self.assertEqual(analyze(prerequisite_rules=rules)["status"], "incomplete")

    def test_time_conflicts_and_touching_boundaries(self) -> None:
        overlap = analyze([section(), section("10002", "BLG 202", "09:30/10:30")])
        self.assertEqual(overlap["time_conflicts"]["conflict_count"], 1)
        self.assertEqual(overlap["status"], "invalid")
        boundary = analyze([section(), section("10002", "BLG 202", "10:00/11:00")])
        self.assertEqual(boundary["status"], "valid")

    def test_missing_unreadable_or_invalid_times_are_unknown(self) -> None:
        for sessions in ([], [{"day": "Monday", "time": "TBA"}], [{"day": "Monday", "time": "24:00/25:00"}], [{"day": "Monday", "time": "10:90/12:00"}], [{"day": "Unrecognized", "time": "09:00/10:00"}], [None]):
            with self.subTest(sessions=sessions):
                result = analyze([{**section(), "sessions": sessions}])
                self.assertEqual(result["status"], "incomplete")
                self.assertEqual(result["time_conflicts"]["unknown_crns"], ["10001"])

    def test_known_no_meetings_can_pass(self) -> None:
        result = analyze([{**section(), "sessions": [], "schedule_status": "no_meetings"}])
        self.assertEqual(result["status"], "valid")

    def test_missing_crns_duplicates_and_duplicate_course_sections(self) -> None:
        self.assertEqual(analyze(requested_crns=["10001", "99999"])["status"], "incomplete")
        self.assertEqual(analyze(requested_crns=["10001", "10001"])["status"], "invalid")
        self.assertEqual(analyze([section(), section("10002", "BLG 201", "10:00/11:00")])["status"], "invalid")
        self.assertEqual(analyze([])["status"], "invalid")

    def test_explicit_rejection_of_an_unresolved_crn_is_still_a_known_blocker(self) -> None:
        result = analyze([], requested_crns=["99999"], eligibility={"99999": {"eligible": False}})
        self.assertEqual(result["status"], "invalid")
        self.assertFalse(result["valid"])
        self.assertIn("CRN 99999: OBS rejected the requested section.", result["blockers"])
        self.assertTrue(result["unknowns"])
        self.assertEqual(result["resolved_crns"], [])

    def test_only_a_boolean_false_establishes_an_unresolved_crn_rejection(self) -> None:
        for verdict in (None, 0, "false", "", True):
            with self.subTest(verdict=verdict):
                result = analyze([], requested_crns=["99999"], eligibility={"99999": {"eligible": verdict}})
                self.assertEqual(result["status"], "incomplete")
                self.assertEqual(result["blockers"], [])

    def test_credit_and_class_requirements_are_separate(self) -> None:
        credits = analyze(prerequisite_rules={"BLG 201": rule(credit_requirement=120)})
        self.assertEqual(credits["status"], "invalid")
        classes = {"BLG 201": rule(credit_requirement=4, credit_requirement_kind="class")}
        self.assertEqual(analyze(prerequisite_rules=classes, class_year=3)["status"], "invalid")
        self.assertEqual(analyze(prerequisite_rules=classes, class_year=4)["status"], "valid")
        self.assertEqual(analyze(prerequisite_rules=classes)["status"], "incomplete")
        ambiguous = {"BLG 201": rule(credit_requirement=4, credit_requirement_kind="unknown")}
        self.assertEqual(analyze(prerequisite_rules=ambiguous, class_year=4)["status"], "incomplete")

    def test_official_success_can_confirm_unavailable_public_rule(self) -> None:
        verdict = {"10001": {"eligible": True, "prerequisite_eligible": True, "credit_eligible": True, "class_eligible": True}}
        self.assertEqual(analyze(prerequisite_rules={}, eligibility=verdict)["status"], "valid")
        contradictory = analyze(prerequisite_rules={"BLG 201": rule(course("BLG 101"))}, eligibility=verdict)
        self.assertEqual(contradictory["status"], "invalid")

    def test_official_prerequisite_confirmation_keeps_history_uncertainty_separate(self) -> None:
        result = analyze(
            prerequisite_rules={"BLG 201": rule(course("BLG 101", "BB"))},
            completed_courses={"BLG 101": None},
            eligibility={"10001": {"eligible": True, "prerequisite_eligible": True}},
        )
        self.assertEqual(result["status"], "valid")
        prerequisite = result["course_checks"][0]["checks"]["prerequisites"]
        self.assertTrue(prerequisite["satisfied"])
        self.assertEqual(prerequisite["missing_courses"], [])
        self.assertEqual(prerequisite["history_evaluation"]["status"], "unknown")
        self.assertEqual(prerequisite["history_evaluation"]["missing_courses"], ["BLG 101"])

    def test_known_obs_class_denial_is_a_blocker(self) -> None:
        result = analyze(eligibility={"10001": {"eligible": True, "class_eligible": False}})
        self.assertEqual(result["status"], "invalid")

    def test_maximum_slot_matching_avoids_greedy_loss_and_double_counting(self) -> None:
        slots = [{"slot": "Flexible", "group_id": 1}, {"slot": "Specific", "group_id": 2}]
        groups = {"1": ["BLG 201", "BLG 202"], "2": ["BLG 201"]}
        result = analyze(
            [section(), section("10002", "BLG 202", "10:00/11:00")],
            graduation=graduation(open_elective_slots=slots), elective_groups=groups,
        )
        assignments = result["elective_slot_coverage"]["assignments"]
        self.assertEqual({item["slot"]: item["course_code"] for item in assignments}, {"Flexible": "BLG 202", "Specific": "BLG 201"})
        single = analyze(graduation=graduation(open_elective_slots=slots), elective_groups=groups)
        self.assertEqual(len(single["elective_slot_coverage"]["assignments"]), 1)
        self.assertEqual(len(single["elective_slot_coverage"]["remaining_slots"]), 1)

    def test_required_or_already_counted_course_cannot_fill_elective_again(self) -> None:
        slots = [{"slot": "Elective", "group_id": 1}]
        groups = {"1": ["BLG 201"]}
        required = analyze(graduation=graduation(open_elective_slots=slots, remaining_required_courses=[{"course_code": "BLG 201"}]), elective_groups=groups)
        self.assertEqual(required["elective_slot_coverage"]["assignments"], [])
        completed = analyze(graduation=graduation(open_elective_slots=slots), elective_groups=groups, completed_courses={"BLG 201": "CC"})
        self.assertEqual(completed["elective_slot_coverage"]["assignments"], [])

    def test_unknown_slot_membership_is_not_assumed(self) -> None:
        result = analyze(graduation=graduation(open_elective_slots=[{"slot": "Elective"}]))
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["elective_slot_coverage"]["remaining_slots"][0]["membership_known"])

    def test_missing_graduation_progress_and_source_errors_prevent_validity(self) -> None:
        self.assertEqual(analyze(graduation=graduation(gpa=None))["status"], "incomplete")
        result = analyze(source_errors=["The official draft check is unavailable."])
        self.assertEqual(result["status"], "incomplete")
        self.assertIn("The official draft check is unavailable.", result["unknowns"])

    def test_chain_preservation_requires_future_minimum_grade(self) -> None:
        result = analyze(
            graduation=graduation(remaining_required_courses=[{"course_code": "BLG 202"}]),
            dependency_rules={"BLG 202": rule(course("BLG 201", "BB"))},
        )
        chain = result["prerequisite_chains"]["chains"][0]
        self.assertEqual(chain["status"], "preserved_if_requirements_met")
        self.assertEqual(chain["conditional_grade_requirements"], [{"course_code": "BLG 201", "minimum_grade": "BB"}])
        self.assertFalse(chain["current_prerequisites_met"])
        self.assertEqual(chain["timing_impact"], "unknown")

    def test_chain_or_alternative_already_passed_is_not_at_risk(self) -> None:
        expression = {"type": "or", "operands": [course("BLG 201", "BB"), course("MAT 201", "DD")]}
        result = analyze(dependency_rules={"BLG 202": rule(expression)}, completed_courses={"MAT 201": "CC"})
        self.assertEqual(result["prerequisite_chains"]["chains"][0]["status"], "already_satisfied")

    def test_chain_and_with_omitted_prerequisite_remains_blocked(self) -> None:
        expression = {"type": "and", "operands": [course("BLG 201", "BB"), course("MAT 201", "DD")]}
        result = analyze(dependency_rules={"BLG 202": rule(expression)})
        chain = result["prerequisite_chains"]["chains"][0]
        self.assertEqual(chain["status"], "blocked_by_unmet_prerequisites")
        self.assertEqual(chain["deferred_or_unmet_prerequisites"], ["MAT 201"])
        self.assertIn(" AND ", chain["requirement"])

    def test_missing_future_rule_is_reported_as_incomplete(self) -> None:
        result = analyze(graduation=graduation(remaining_required_courses=[{"course_code": "BLG 202"}]))
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["prerequisite_chains"]["unknown_courses"], ["BLG 202"])

    def test_unknown_minimum_grade_is_not_assumed_in_chain(self) -> None:
        result = analyze(dependency_rules={"BLG 202": rule(course("BLG 201", "XY"))})
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["prerequisite_chains"]["chains"][0]["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
