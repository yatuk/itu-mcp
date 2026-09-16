from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import requests

from ninova_mcp.obs_client import ObsError
from ninova_mcp.registration_draft import normalize_validation
from ninova_mcp.registration_tools import get_elective_group, validate_plan
from ninova_mcp.server import NinovaMcpApp


def leaf(code: str, minimum: str = "DD") -> dict:
    return {"type": "course", "code": code, "min_grade": minimum}


def course_rule(tree: dict | None = None, credit: float | None = None) -> dict:
    return {"requirement_tree": tree, "credit_requirement": credit, "credit_requirement_text": None}


def verdict(crn: str = "10001", code: str = "BLG 201", eligible: bool = True) -> dict:
    return {
        "crn": crn, "course_code": code, "course_name": "Example Course", "credit": 3,
        "eligible": eligible, "prerequisite_eligible": True if eligible else None,
        "program_eligible": True if eligible else None,
        "credit_eligible": True if eligible else None, "class_eligible": True if eligible else None,
        "source": "obs_crn_validation",
    }


def section(crn: str = "10001", code: str = "BLG 201") -> dict:
    return {"crn": crn, "code": code, "sessions": [{"day": "Monday", "time": "09:00/10:00"}]}


def graduation_payload() -> dict:
    return {
        "statusCode": 0,
        "mezuniyetimeNeKaldiBilgi": {
            "checkMetMezuniyetList": [
                {"bransKodu": "BLG 101", "isMet": True, "harfNotu": "BB", "kredisiDec": 3},
                {"bransKodu": "BLG 201", "isMet": False, "harfNotu": None, "kredisiDec": 3},
                {"bransKodu": "BLG 202", "isMet": False, "harfNotu": None, "kredisiDec": 3},
            ],
            "unusedSinifOgrenciList": [
                {"bransKodu": "MAT 102", "harfNotu": "AA", "isValidAndUnused": True},
                {"bransKodu": "MAT 103", "harfNotu": "FF", "isValidAndUnused": True},
                {"bransKodu": "MAT 104", "harfNotu": "AA", "isValidAndUnused": False},
            ],
            "dersPlaniVM": {
                "gerekliMezuniyetKredisi": 150, "gerekliMinGPA": 2,
                "gerekliStajGunu": 40, "gerekliIngilizceKredi": 45,
            },
            "metKrediTotal": 100, "metIngKrediTotal": 30, "gpa": 3,
            "ogrenciTamamlananStaj": 20,
        },
    }


def make_app() -> SimpleNamespace:
    obs = Mock(spec=["api_get", "list_semesters", "validate_registration_crns", "get_graduation_remaining", "default_program_id"])
    obs.api_get.return_value = {
        "statusCode": 0,
        "kayitZamanKontrolResult": {
            "akademikDonemKodu": "202710", "akademikDonemAdi": "2026-2027 Fall", "sinif": 4,
        },
    }
    obs.list_semesters.return_value = {
        "statusCode": 0,
        "ogrenciDonemListesi": [{"donemKodu": "202710", "akademikDonemAdiEN": "2026-2027 Fall", "akademikDonemAdi": "2026-2027 Fall"}],
    }
    obs.validate_registration_crns.return_value = {"10001": verdict()}
    obs.default_program_id.return_value = 9
    obs.get_graduation_remaining.return_value = graduation_payload()
    public = Mock(spec=["get_course_schedule", "get_branch_prerequisites"])
    public.get_course_schedule.return_value = {
        "semester": "2026-2027 Fall", "url": "https://obs.itu.edu.tr/public/schedule/BLG",
        "courses": [section(), section("19999", "BLG 299")],
    }
    public.get_branch_prerequisites.return_value = {
        "table_parsed": True, "url": "https://obs.itu.edu.tr/public/prerequisites/BLG",
        "rules": {
            "BLG 201": course_rule({"type": "and", "operands": [leaf("BLG 101", "BB"), leaf("MAT 102", "CC")]}, 60),
            "BLG 202": course_rule(leaf("BLG 201", "BB")),
        },
    }
    return SimpleNamespace(obs=obs, obs_public=public)


def elective_group(group_id: int, codes: list[str]) -> dict:
    return {
        "group_id": group_id, "group_name": "Example Elective", "available": True, "complete": True,
        "url": f"https://obs.itu.edu.tr/public/group/{group_id}",
        "courses": [{"course_code": code} for code in codes],
    }


class RegistrationCoordinatorTests(unittest.TestCase):
    def test_invalid_crns_are_rejected_before_authentication_or_public_access(self) -> None:
        class NoNetwork:
            @property
            def obs(self):
                raise AssertionError("Authentication must not be touched for invalid input.")

            @property
            def obs_public(self):
                raise AssertionError("Public network access must not be touched for invalid input.")

        for invalid in ([], ["10001", "10001"], [10001], ["100"], ["100001"], [" 10001"], ["abcde"], ["10001"] * 13):
            with self.subTest(invalid=invalid), self.assertRaises(ObsError):
                NinovaMcpApp.obs_validate_registration_plan(NoNetwork(), invalid)

    def test_selected_plan_joins_passed_and_valid_unused_history_and_matching_evidence(self) -> None:
        app = make_app()
        result = validate_plan(app, ["10001"])
        self.assertEqual(result["status"], "valid")
        app.obs.validate_registration_crns.assert_called_once_with(["10001"])
        app.obs.get_graduation_remaining.assert_called_once_with(9)
        self.assertEqual([item["crn"] for item in result["sections"]], ["10001"])
        self.assertEqual(result["time_conflicts"]["conflict_count"], 0)
        self.assertEqual(result["obs_results"], {"10001": verdict()})
        self.assertEqual(result["term_code"], "202710")
        self.assertEqual(result["eligibility_source"], "independent_obs_crn_validation")
        self.assertIn("https://obs.itu.edu.tr/public/schedule/BLG", result["sources"])
        self.assertIn("https://obs.itu.edu.tr/public/prerequisites/BLG", result["sources"])
        self.assertEqual(result["prerequisite_chains"]["chains"][0]["status"], "preserved_if_requirements_met")
        self.assertEqual(result["graduation_requirements"]["current_progress"]["english_credits_earned"], 30)

    def test_failed_or_invalid_unused_history_does_not_satisfy_prerequisites(self) -> None:
        for code in ("MAT 103", "MAT 104"):
            with self.subTest(code=code):
                app = make_app()
                app.obs_public.get_branch_prerequisites.return_value["rules"]["BLG 201"] = course_rule(leaf(code))
                self.assertEqual(validate_plan(app, ["10001"])["status"], "invalid")

    def test_completed_course_with_unknown_grade_preserves_known_completion(self) -> None:
        app = make_app()
        app.obs.get_graduation_remaining.return_value["mezuniyetimeNeKaldiBilgi"]["checkMetMezuniyetList"][0]["harfNotu"] = None
        app.obs_public.get_branch_prerequisites.return_value["rules"]["BLG 201"] = course_rule(leaf("BLG 101", None))
        self.assertEqual(validate_plan(app, ["10001"])["status"], "valid")
        app.obs_public.get_branch_prerequisites.return_value["rules"]["BLG 201"] = course_rule(leaf("BLG 101", "BB"))
        app.obs.validate_registration_crns.return_value["10001"]["prerequisite_eligible"] = None
        self.assertEqual(validate_plan(app, ["10001"])["status"], "incomplete")

    def test_semester_list_resolves_only_the_registration_term_code(self) -> None:
        app = make_app()
        app.obs.api_get.return_value["kayitZamanKontrolResult"].pop("akademikDonemAdi")
        self.assertEqual(validate_plan(app, ["10001"])["status"], "valid")
        app.obs.list_semesters.return_value["ogrenciDonemListesi"][0]["donemKodu"] = "202610"
        self.assertEqual(validate_plan(app, ["10001"])["status"], "incomplete")

    def test_unavailable_history_remains_incomplete(self) -> None:
        app = make_app()
        app.obs.get_graduation_remaining.side_effect = ObsError("Synthetic read failure.")
        result = validate_plan(app, ["10001"])
        self.assertEqual(result["status"], "incomplete")
        self.assertTrue(any("graduation" in item for item in result["unknowns"]))

    def test_term_credit_limit_rejection_does_not_fail_completed_credit_prerequisites(self) -> None:
        app = make_app()
        app.obs.validate_registration_crns.return_value = normalize_validation({
            "statusCode": 0,
            "ecrnResultList": [{
                "crn": "10001", "statusCode": 1, "dersKodu": "BLG 201",
                "taslakKontrolResultList": [{"resultCode": "VAL05"}],
            }],
        }, ["10001"])
        result = validate_plan(app, ["10001"])
        self.assertEqual(result["status"], "invalid")
        checks = result["course_checks"][0]["checks"]
        self.assertEqual(checks["credit_prerequisites"]["status"], "pass")
        self.assertEqual(checks["credit_prerequisites"]["required"], 60)
        self.assertEqual(checks["credit_prerequisites"]["actual"], 100)
        self.assertEqual(checks["obs_eligibility"]["status"], "fail")
        self.assertIn("term credit limit", checks["obs_eligibility"]["source_reason"])
        self.assertIsNone(result["obs_results"]["10001"]["credit_eligible"])

    def test_official_unknown_crn_rejection_survives_missing_course_identity(self) -> None:
        app = make_app()
        official = normalize_validation({
            "statusCode": 0,
            "ecrnResultList": [{
                "crn": "99999", "statusCode": 1, "dersKodu": None,
                "taslakKontrolResultList": [{"resultCode": "CRNNotFound"}],
            }],
        }, ["99999"])
        app.obs.validate_registration_crns.return_value = official
        result = validate_plan(app, ["99999"])
        self.assertEqual(result["status"], "invalid")
        self.assertFalse(result["valid"])
        self.assertEqual(result["sections"], [])
        self.assertEqual(result["obs_results"], official)
        self.assertFalse(result["obs_results"]["99999"]["eligible"])
        self.assertEqual(result["obs_results"]["99999"]["reasons"][0]["code"], "CRNNotFound")
        self.assertIn("CRN 99999: OBS rejected the requested section.", result["blockers"])
        self.assertTrue(result["unknowns"])
        app.obs_public.get_course_schedule.assert_not_called()

    def test_unavailable_schedule_preserves_course_identity_but_not_conflict_clearance(self) -> None:
        app = make_app()
        app.obs_public.get_course_schedule.side_effect = requests.Timeout("Synthetic timeout.")
        result = validate_plan(app, ["10001"])
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["sections"][0]["code"], "BLG 201")
        self.assertEqual(result["sections"][0]["sessions"], [])
        self.assertEqual(result["time_conflicts"]["status"], "unknown")

    def test_conflicting_course_identity_is_not_joined_to_an_unrelated_timetable(self) -> None:
        app = make_app()
        app.obs_public.get_course_schedule.return_value["courses"] = [section("10001", "BLG 301")]
        result = validate_plan(app, ["10001"])
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["sections"][0]["code"], "BLG 201")
        self.assertEqual(result["sections"][0]["sessions"], [])
        self.assertTrue(any("disagree" in item for item in result["unknowns"]))

    def test_unknown_or_mismatched_schedule_term_does_not_validate(self) -> None:
        for semester in (None, "Unknown semester", "Bilinmeyen Dönem", "2025-2026 Fall"):
            with self.subTest(semester=semester):
                app = make_app()
                app.obs_public.get_course_schedule.return_value["semester"] = semester
                self.assertEqual(validate_plan(app, ["10001"])["status"], "incomplete")

    def test_schedule_warning_variants_and_errors_do_not_validate(self) -> None:
        for key, value in (("parse_warning", "Truncated rows."), ("parse_warnings", ["Truncated rows."]), ("error", "Unreadable schedule.")):
            with self.subTest(key=key):
                app = make_app()
                app.obs_public.get_course_schedule.return_value[key] = value
                self.assertEqual(validate_plan(app, ["10001"])["status"], "incomplete")

    def test_missing_future_rules_leave_chain_analysis_incomplete(self) -> None:
        app = make_app()
        app.obs_public.get_branch_prerequisites.side_effect = ObsError("Synthetic read failure.")
        result = validate_plan(app, ["10001"])
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["prerequisite_chains"]["unknown_courses"], ["BLG 202"])

    def test_groups_come_from_current_plan_and_are_fetched_once_per_distinct_id(self) -> None:
        app = make_app()
        app.obs.validate_registration_crns.return_value["10002"] = verdict("10002", "BLG 301")
        app.obs_public.get_course_schedule.return_value["courses"].append({**section("10002", "BLG 301"), "sessions": [{"day": "Tuesday", "time": "09:00/10:00"}]})
        rows = app.obs.get_graduation_remaining.return_value["mezuniyetimeNeKaldiBilgi"]["checkMetMezuniyetList"]
        rows.extend([
            {"grupName": "Elective One", "grupId": 731, "isMet": False, "kredisiDec": 3},
            {"grupName": "Elective Two", "grupId": 731, "isMet": False, "kredisiDec": 3},
            {"grupName": "Elective Three", "grupId": 947, "isMet": False, "kredisiDec": 3},
        ])
        with patch("ninova_mcp.registration_tools.fetch_elective_group", side_effect=lambda public, gid: elective_group(gid, ["BLG 201", "BLG 301"])) as fetch:
            result = validate_plan(app, ["10001", "10002"])
        self.assertEqual(fetch.call_args_list, [call(app.obs_public, 731), call(app.obs_public, 947)])
        assignments = result["elective_slot_coverage"]["assignments"]
        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments[0]["course_code"], "BLG 301")
        self.assertEqual(len(result["elective_slot_coverage"]["remaining_slots"]), 2)

    def test_failed_group_fetch_cannot_be_read_as_no_eligible_courses(self) -> None:
        app = make_app()
        app.obs.get_graduation_remaining.return_value["mezuniyetimeNeKaldiBilgi"]["checkMetMezuniyetList"].append({"grupName": "Elective", "grupId": 731, "isMet": False})
        with patch("ninova_mcp.registration_tools.fetch_elective_group", side_effect=ObsError("Synthetic read failure.")):
            result = validate_plan(app, ["10001"])
        self.assertEqual(result["status"], "incomplete")
        self.assertFalse(result["elective_slot_coverage"]["remaining_slots"][0]["membership_known"])


class ElectiveCoordinatorTests(unittest.TestCase):
    def test_exam_only_section_is_not_checked_as_a_normal_elective(self) -> None:
        app = make_app()
        app.obs_public.get_course_schedule.return_value["courses"] = [
            {**section(), "method": "Ek Sınav", "capacity": 0},
            section("10002", "BLG 201"),
        ]
        app.obs.validate_registration_crns.return_value = {"10002": verdict("10002")}
        with patch("ninova_mcp.registration_tools.fetch_elective_group", return_value=elective_group(731, ["BLG 201"])):
            result = get_elective_group(app, 731)
        app.obs.validate_registration_crns.assert_called_once_with(["10002"])
        self.assertEqual(result["courses"][0]["exam_only_crns"], ["10001"])
        self.assertTrue(result["courses"][0]["offered_this_term"])
        self.assertEqual(result["eligibility_checked_section_count"], 1)

    def test_each_section_is_checked_independently_even_if_they_conflict(self) -> None:
        app = make_app()
        app.obs_public.get_course_schedule.return_value["courses"] = [section(), section("10002", "BLG 201")]
        app.obs.validate_registration_crns.side_effect = lambda crns: {
            crns[0]: verdict(crns[0], "BLG 201", crns[0] == "10002")
        }
        with patch("ninova_mcp.registration_tools.fetch_elective_group", return_value=elective_group(731, ["BLG 201"])):
            result = get_elective_group(app, 731)
        self.assertEqual(app.obs.validate_registration_crns.call_args_list, [call(["10001"]), call(["10002"])])
        self.assertEqual(result["eligibility_checked_section_count"], 2)
        self.assertTrue(result["courses"][0]["eligibility"]["eligible"])
        self.assertFalse(result["courses"][0]["sections"][0]["eligibility"]["eligible"])
        self.assertTrue(result["courses"][0]["sections"][1]["eligibility"]["eligible"])

    def test_course_identity_mismatch_does_not_prove_elective_eligibility(self) -> None:
        app = make_app()
        app.obs.validate_registration_crns.return_value = {"10001": verdict("10001", "BLG 202")}
        with patch("ninova_mcp.registration_tools.fetch_elective_group", return_value=elective_group(731, ["BLG 201"])):
            result = get_elective_group(app, 731)
        self.assertIsNone(result["courses"][0]["eligibility"]["eligible"])
        self.assertIn("disagree", result["courses"][0]["sections"][0]["eligibility"]["error_reason"])

    def test_authentication_failure_preserves_public_membership_with_unknown_eligibility(self) -> None:
        app = make_app()
        app.obs_public.get_course_schedule.return_value["courses"] = [section(), section("10002", "BLG 201")]
        app.obs.validate_registration_crns.side_effect = ObsError("Synthetic authentication failure.")
        with patch("ninova_mcp.registration_tools.fetch_elective_group", return_value=elective_group(731, ["BLG 201"])):
            result = get_elective_group(app, 731)
        self.assertEqual(app.obs.validate_registration_crns.call_count, 1)
        self.assertEqual(result["courses"][0]["course_code"], "BLG 201")
        self.assertTrue(result["courses"][0]["offered_this_term"])
        self.assertIsNone(result["courses"][0]["eligibility"]["eligible"])
        self.assertTrue(result["errors"])

    def test_group_validation_limit_is_explicit_and_unchecked_sections_stay_unknown(self) -> None:
        app = make_app()
        app.obs_public.get_course_schedule.return_value["courses"] = [section(str(10001 + index)) for index in range(25)]
        app.obs.validate_registration_crns.side_effect = lambda crns: {crns[0]: verdict(crns[0])}
        with patch("ninova_mcp.registration_tools.fetch_elective_group", return_value=elective_group(731, ["BLG 201"])):
            result = get_elective_group(app, 731)
        self.assertEqual(app.obs.validate_registration_crns.call_count, 24)
        self.assertIsNone(result["courses"][0]["sections"][-1]["eligibility"]["eligible"])
        self.assertTrue(any("24" in error for error in result["errors"]))

    def test_mismatched_public_term_is_not_presented_as_a_current_offering(self) -> None:
        app = make_app()
        app.obs_public.get_course_schedule.return_value["semester"] = "2025-2026 Fall"
        with patch("ninova_mcp.registration_tools.fetch_elective_group", return_value=elective_group(731, ["BLG 201"])):
            result = get_elective_group(app, 731)
        self.assertIsNone(result["courses"][0]["offered_this_term"])
        self.assertIsNone(result["courses"][0]["eligibility"]["eligible"])
        self.assertEqual(result["offered_course_count"], 0)
        self.assertEqual(result["schedule_unknown_count"], 1)
        app.obs.validate_registration_crns.assert_not_called()
        self.assertTrue(result["errors"])

    def test_failed_public_schedule_does_not_claim_a_course_is_not_offered(self) -> None:
        app = make_app()
        app.obs_public.get_course_schedule.side_effect = requests.Timeout("Synthetic timeout.")
        with patch("ninova_mcp.registration_tools.fetch_elective_group", return_value=elective_group(731, ["BLG 201"])):
            result = get_elective_group(app, 731)
        self.assertIsNone(result["courses"][0]["offered_this_term"])
        self.assertIsNone(result["courses"][0]["eligibility"]["eligible"])
        app.obs.validate_registration_crns.assert_not_called()


    def test_malformed_optional_semester_list_preserves_verified_period(self) -> None:
        for rows in ("unrecognized", [None, "unrecognized"]):
            app = make_app()
            app.obs.list_semesters.return_value["ogrenciDonemListesi"] = rows
            with self.subTest(rows=rows), patch("ninova_mcp.registration_tools.fetch_elective_group", return_value=elective_group(731, ["BLG 201"])):
                result = get_elective_group(app, 731)
            self.assertTrue(result["courses"][0]["eligibility"]["eligible"])
            self.assertEqual(result["eligibility_checked_section_count"], 1)

    def test_malformed_optional_rows_do_not_hide_a_matching_semester(self) -> None:
        app = make_app()
        app.obs.api_get.return_value["kayitZamanKontrolResult"].pop("akademikDonemAdi")
        app.obs.list_semesters.return_value["ogrenciDonemListesi"].insert(0, None)
        with patch("ninova_mcp.registration_tools.fetch_elective_group", return_value=elective_group(731, ["BLG 201"])):
            result = get_elective_group(app, 731)
        self.assertTrue(result["courses"][0]["eligibility"]["eligible"])


if __name__ == "__main__":
    unittest.main()
