"""Letter grades must use the term endpoint and exact registered course identity."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, call, patch

from ninova_mcp.obs_client import ObsClient, ObsError
from ninova_mcp.obs_grades import MAX_SEMESTER_LOOKUPS, get_class_letter_grades
from ninova_mcp.server import NinovaMcpApp


def semester(identifier: int) -> dict:
    return {"akademikDonemId": identifier, "donemKodu": str(202000 + identifier)}


def registered(class_id: int = 9001, crn: str = "12345", code: str = "201") -> dict:
    return {"sinifId": class_id, "crn": crn, "bransKodu": "BLG", "dersKodu": code}


def grade(crn: str = "12345", letter: str | None = "BB") -> dict:
    return {"crn": crn, "bransKodu": "BLG", "dersKodu": "201", "harfNotu": letter}


def make_obs() -> Mock:
    obs = Mock(spec=["list_semesters", "resolve_semester", "list_registered_courses", "get_letter_grades", "get_midterm_grades"])
    obs.list_semesters.return_value = {"statusCode": 0, "ogrenciDonemListesi": [semester(12), semester(11)]}
    obs.resolve_semester.return_value = semester(11)
    obs.list_registered_courses.side_effect = lambda identifier: {
        "statusCode": 0,
        "kayitSinifResultList": [registered(), registered(9002, "23456", "202")] if str(identifier) == "11" else [],
    }
    obs.get_letter_grades.return_value = {
        "statusCode": 0,
        "sinifHarfNotuResultList": [grade(), grade("23456", "AA")],
        "unrelated_account_information": "This field must not be returned.",
    }
    obs.get_midterm_grades.return_value = {"statusCode": 0, "sinifDonemIciNotListesi": [{"notu": 80}]}
    return obs


def app_with_obs(obs: Mock) -> NinovaMcpApp:
    app = NinovaMcpApp.__new__(NinovaMcpApp)
    app._obs = obs
    return app


class ObsLetterGradeReliabilityTests(unittest.TestCase):
    def test_low_level_endpoint_takes_the_academic_semester_id(self) -> None:
        client = ObsClient(ninova_client=Mock(), base_url="https://obs.itu.edu.tr")
        with patch.object(client, "api_get", return_value={"statusCode": 0}) as read:
            client.get_letter_grades(academic_semester_id=11)
        read.assert_called_once_with("/api/ogrenci/Sinif/SinifHarfNotuListesi/11")

    def test_class_only_lookup_finds_history_and_returns_only_the_exact_crn(self) -> None:
        obs = make_obs()
        result = get_class_letter_grades(obs, {"sinifId": 9001})
        self.assertEqual(obs.list_registered_courses.call_args_list, [call("12"), call("11")])
        obs.get_letter_grades.assert_called_once_with("11")
        self.assertEqual(result["sinifHarfNotuResultList"], [grade()])
        self.assertEqual(result["academic_semester_id"], 11)
        self.assertEqual(result["class_id"], 9001)
        self.assertEqual(result["crn"], "12345")
        self.assertTrue(result["available"])
        self.assertNotIn("unrelated_account_information", result)

    def test_server_keeps_class_id_for_midterms_and_uses_term_id_for_letters(self) -> None:
        obs = make_obs()
        result = app_with_obs(obs).obs_get_course_grades(class_id=9001)
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["letter_grades"]["sinifHarfNotuResultList"]), 1)
        obs.get_letter_grades.assert_called_once_with("11")
        obs.get_midterm_grades.assert_called_once_with(9001)

    def test_code_and_semester_lookup_uses_the_resolved_class(self) -> None:
        obs = make_obs()
        result = app_with_obs(obs).obs_get_course_grades(course="BLG 201", semester="11")
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["letter_grades"]["class_id"], 9001)
        obs.list_semesters.assert_not_called()
        obs.get_letter_grades.assert_called_once_with("11")

    def test_wrong_explicit_semester_does_not_fall_back_to_a_different_attempt(self) -> None:
        obs = make_obs()
        obs.resolve_semester.return_value = semester(12)
        result = app_with_obs(obs).obs_get_course_grades(class_id=9001, semester="12")
        self.assertIsNone(result["letter_grades"])
        self.assertEqual(result["errors"][0]["scope"], "letter_grades")
        self.assertIsNotNone(result["midterm_grades"])
        obs.get_letter_grades.assert_not_called()
        obs.list_semesters.assert_not_called()
        obs.list_registered_courses.assert_called_once_with("12")

    def test_identical_course_codes_in_different_terms_do_not_mix_grades(self) -> None:
        obs = make_obs()
        obs.list_registered_courses.side_effect = lambda identifier: {
            "statusCode": 0,
            "kayitSinifResultList": [registered(9002, "12345")] if str(identifier) == "12" else [registered()],
        }
        obs.get_letter_grades.side_effect = lambda identifier: {
            "statusCode": 0,
            "sinifHarfNotuResultList": [grade(letter="AA" if str(identifier) == "12" else "DD")],
        }
        result = get_class_letter_grades(obs, {"sinifId": 9001})
        self.assertEqual(result["sinifHarfNotuResultList"][0]["harfNotu"], "DD")
        self.assertEqual(result["academic_semester_id"], 11)

    def test_existing_resolved_term_avoids_unrelated_semester_queries(self) -> None:
        obs = make_obs()
        result = get_class_letter_grades(obs, {**registered(), "semester": semester(11)})
        self.assertTrue(result["available"])
        obs.list_semesters.assert_not_called()
        obs.resolve_semester.assert_not_called()
        obs.list_registered_courses.assert_called_once_with("11")

    def test_disagreeing_class_crn_is_rejected_before_reading_grades(self) -> None:
        obs = make_obs()
        with self.assertRaisesRegex(ObsError, "different CRNs"):
            get_class_letter_grades(obs, {**registered(crn="99999"), "semester": semester(11)})
        obs.get_letter_grades.assert_not_called()

    def test_duplicate_course_or_grade_rows_are_not_silently_selected(self) -> None:
        for duplicate_in in ("registered", "letter"):
            with self.subTest(duplicate_in=duplicate_in):
                obs = make_obs()
                if duplicate_in == "registered":
                    obs.list_registered_courses.side_effect = None
                    obs.list_registered_courses.return_value = {"statusCode": 0, "kayitSinifResultList": [registered(), registered()]}
                else:
                    obs.get_letter_grades.return_value["sinifHarfNotuResultList"] = [grade(), grade()]
                with self.assertRaises(ObsError):
                    get_class_letter_grades(obs, {"sinifId": 9001})

    def test_missing_or_unpublished_grade_is_explicit(self) -> None:
        for rows in ([], [grade(letter=None)]):
            with self.subTest(rows=rows):
                obs = make_obs()
                obs.get_letter_grades.return_value["sinifHarfNotuResultList"] = rows
                result = get_class_letter_grades(obs, {"sinifId": 9001})
                self.assertFalse(result["available"])
                self.assertEqual(result["status"], "not_available")
                self.assertEqual(result["sinifHarfNotuResultList"], rows)

    def test_malformed_success_and_failed_business_response_are_not_empty_grades(self) -> None:
        for payload in ({"statusCode": 0}, {"statusCode": False, "sinifHarfNotuResultList": []}, {"statusCode": 1, "sinifHarfNotuResultList": []}, {"statusCode": 0, "sinifHarfNotuResultList": [None]}):
            with self.subTest(payload=payload):
                obs = make_obs()
                obs.get_letter_grades.return_value = payload
                with self.assertRaises(ObsError):
                    get_class_letter_grades(obs, {"sinifId": 9001})

    def test_class_id_without_an_exact_registration_match_stays_partial(self) -> None:
        obs = make_obs()
        result = app_with_obs(obs).obs_get_course_grades(class_id=9999)
        self.assertIsNone(result["letter_grades"])
        self.assertEqual(result["errors"][0]["scope"], "letter_grades")
        obs.get_letter_grades.assert_not_called()
        obs.get_midterm_grades.assert_called_once_with(9999)

    def test_letter_disabled_does_not_resolve_unnecessary_term_context(self) -> None:
        obs = make_obs()
        result = app_with_obs(obs).obs_get_course_grades(class_id=9001, include_letter=False)
        self.assertNotIn("letter_grades", result)
        obs.list_semesters.assert_not_called()
        obs.get_letter_grades.assert_not_called()
        obs.get_midterm_grades.assert_called_once_with(9001)

    def test_historical_search_is_bounded_and_does_not_guess(self) -> None:
        obs = make_obs()
        obs.list_semesters.return_value["ogrenciDonemListesi"] = [semester(index + 1) for index in range(MAX_SEMESTER_LOOKUPS + 5)]
        obs.list_registered_courses.side_effect = None
        obs.list_registered_courses.return_value = {"statusCode": 0, "kayitSinifResultList": []}
        with self.assertRaisesRegex(ObsError, "Provide the class's semester"):
            get_class_letter_grades(obs, {"sinifId": 9001})
        self.assertEqual(obs.list_registered_courses.call_count, MAX_SEMESTER_LOOKUPS)
        obs.get_letter_grades.assert_not_called()

    def test_unreadable_term_does_not_prevent_an_exact_match_in_another_term(self) -> None:
        obs = make_obs()
        obs.list_registered_courses.side_effect = [ObsError("Synthetic unavailable term."), {"statusCode": 0, "kayitSinifResultList": [registered()]}]
        self.assertTrue(get_class_letter_grades(obs, {"sinifId": 9001})["available"])


if __name__ == "__main__":
    unittest.main()
