from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from ninova_mcp.planning import (
    explain_course_eligibility,
    filter_academic_calendar,
    find_empty_classrooms,
    find_open_sections,
)
from ninova_mcp.public_client import ItuPublicClient, ItuPublicError
from ninova_mcp.public_parsing import (
    extract_building_codes,
    extract_degree_plan_detail,
    extract_degree_plan_list,
    extract_directory_results,
    extract_final_exam_schedule,
    extract_library_account,
    extract_library_record,
    extract_library_search_results,
    extract_shuttle_schedule,
    extract_sports_facility_hours,
)
from ninova_mcp.server import LOCAL_TOOL_NAMES, NinovaMcpApp


FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class PublicParserTests(unittest.TestCase):
    def test_final_exam(self) -> None:
        result = extract_final_exam_schedule(fixture("final_exam_result.html"), "https://obs.itu.edu.tr/result")
        self.assertTrue(result["published"])
        self.assertEqual(result["exams"][0]["course_code"], "BLG 223E")
        self.assertEqual(result["exams"][0]["room"], "210")

    def test_final_exam_unpublished_is_not_parse_failure(self) -> None:
        result = extract_final_exam_schedule("<b>Bu branş koduna bağlı sınıfların final sınav programları yayınlanmamıştır.</b>", "https://obs.itu.edu.tr/result")
        self.assertFalse(result["published"])
        self.assertNotIn("parse_warning", result)

    def test_directory(self) -> None:
        result = extract_directory_results(fixture("directory_results.html"), "https://rehber.itu.edu.tr/Rehber/Search")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["people"][0]["full_name"], "Ornek Kisi")
        self.assertTrue(result["people"][0]["detail_url"].startswith("https://rehber.itu.edu.tr/"))

    def test_campus_service_parsers(self) -> None:
        html = fixture("campus_services.html")
        buildings = extract_building_codes(html, "https://obs.itu.edu.tr/buildings", "bilgisayar")
        self.assertEqual(buildings["locations"][0]["code"], "BBB")
        shuttle = extract_shuttle_schedule(html, "https://sks.itu.edu.tr/mekik-servis")
        self.assertGreaterEqual(shuttle["schedule_count"], 1)
        self.assertGreaterEqual(shuttle["stop_list_count"], 1)
        sports = extract_sports_facility_hours(html, "https://sks.itu.edu.tr/sports", "yüzme")
        self.assertEqual(sports["facilities"][0]["weekday"]["opens"], "08:00")

    def test_degree_plan(self) -> None:
        html = fixture("degree_plan.html")
        plans = extract_degree_plan_list(html, "https://obs.itu.edu.tr/public/DersPlan/list")
        self.assertEqual(plans["plans"][0]["plan_id"], 2340)
        detail = extract_degree_plan_detail(html, "https://obs.itu.edu.tr/public/DersPlan/DersPlanDetay/2340")
        self.assertEqual(detail["course_count"], 1)
        self.assertEqual(detail["courses"][0]["credit"], 1.5)

    def test_library_parsers(self) -> None:
        html = fixture("library_pages.html")
        search = extract_library_search_results(html, "https://divit.library.itu.edu.tr/search/Y")
        self.assertEqual(search["records"][0]["record_id"], "b1179767")
        record = extract_library_record(html, "https://divit.library.itu.edu.tr/record=b1179767")
        self.assertEqual(record["copy_count"], 1)
        account = extract_library_account(html, "https://divit.library.itu.edu.tr/patroninfo")
        self.assertEqual(account["loans"][0]["loan_id"], "loan-1")


class PlanningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schedules = [{
            "department_code": "BLG",
            "courses": [
                {"crn": "1", "code": "BLG 1", "capacity": 40, "enrolled": 35, "sessions": [{"day": "Pazartesi", "time": "09:30/10:29", "building": "BBB", "room": "101"}]},
                {"crn": "2", "code": "BLG 2", "capacity": 30, "enrolled": 30, "sessions": [{"day": "Pazartesi", "time": "11:30/12:29", "building": "BBB", "room": "102"}]},
            ],
        }]

    def test_open_sections(self) -> None:
        result = find_open_sections(self.schedules)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["sections"][0]["available_seats"], 5)

    def test_empty_classrooms(self) -> None:
        result = find_empty_classrooms(self.schedules, day="Pazartesi", time="09:45", building="BBB")
        self.assertEqual(result["empty_room_count"], 1)
        self.assertEqual(result["empty_rooms"][0]["room"], "102")

    def test_calendar_filters(self) -> None:
        calendar = {"event_count": 2, "events": [
            {"description": "Final exams", "category": "exam", "start_date": "2026-07-20", "end_date": "2026-07-25"},
            {"description": "Registration", "category": "registration", "start_date": "2026-08-01", "end_date": "2026-08-02"},
        ]}
        result = filter_academic_calendar(calendar, date_from="2026-07-22", date_to="2026-07-30", category="exam")
        self.assertEqual(result["event_count"], 1)
        self.assertEqual(result["total_event_count"], 2)

    def test_eligibility_groups(self) -> None:
        data = {"prerequisites": [
            {"code": "BLG 101E", "group": "1"},
            {"code": "BLG 102E", "group": "1"},
            {"code": "MAT 103E", "group": "2"},
        ]}
        result = explain_course_eligibility(data, completed_courses=["BLG 102E", "MAT 103E"])
        self.assertTrue(result["eligible"])

    def test_eligibility_credit_and_class_requirement(self) -> None:
        data = {
            "prerequisites": [],
            "credit_prerequisite": "En az 60 kredi ve 3. sınıf",
        }
        missing = explain_course_eligibility(
            data,
            completed_courses=[],
            completed_credits=55,
            class_year=3,
        )
        self.assertFalse(missing["eligible"])
        self.assertFalse(missing["credit_requirement_satisfied"])
        eligible = explain_course_eligibility(
            data,
            completed_courses=[],
            completed_credits=65,
            class_year=3,
        )
        self.assertTrue(eligible["eligible"])
        self.assertTrue(eligible["class_requirement_satisfied"])


class PublicClientSafetyTests(unittest.TestCase):
    def test_exact_host_allowlist_rejects_suffix_confusion(self) -> None:
        client = ItuPublicClient()
        with self.assertRaises(ItuPublicError):
            client._validate_url("https://obs.itu.edu.tr.evil.example/path")

    def test_public_obs_does_not_construct_authenticated_client(self) -> None:
        app = NinovaMcpApp()
        _ = app.obs_public
        self.assertIsNone(app._client)

    def test_final_client_uses_verified_route(self) -> None:
        landing = '<select id="DersBransKoduId"><option value="3">BLG</option></select>'
        client = ItuPublicClient()
        with patch.object(client, "_get_text", side_effect=[
            (landing, "https://obs.itu.edu.tr/public/FinalTakvimi/FinalTakvimiByDersBransKodu"),
            (fixture("final_exam_result.html"), "https://obs.itu.edu.tr/public/FinalTakvimi/SearchFinalTakvimiByDersBransKodu?DersBransKoduId=3"),
        ]):
            result = client.get_final_exam_schedule("blg")
        self.assertEqual(result["department_id"], 3)
        self.assertTrue(result["untrusted_external_content"])

    def test_new_tools_are_registered(self) -> None:
        expected = {
            "get_public_exam_schedule", "get_personal_exam_calendar", "search_itu_directory",
            "get_shuttle_schedule", "search_campus_locations", "get_sports_facility_hours",
            "get_itu_announcements", "library_search", "library_get_item",
            "library_check_availability", "library_get_account", "library_list_loans",
            "library_renew_loan", "library_reserve_item", "find_open_course_sections",
            "find_empty_classrooms", "list_degree_faculties", "list_degree_programs", "build_degree_plan",
            "explain_course_eligibility", "calculate_target_gpa",
        }
        self.assertTrue(expected.issubset(set(LOCAL_TOOL_NAMES)))
        for name in expected:
            self.assertTrue(hasattr(NinovaMcpApp, name), name)


if __name__ == "__main__":
    unittest.main()
