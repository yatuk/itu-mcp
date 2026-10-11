"""Public schedule tools against a trimmed copy of the real OBS schedule page.

Covers reservation quotas, placeholder cells, "Ek Sınav" rows, the query
matcher of the open-section finder and the day/time input of the empty
classroom finder.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import unittest

from ninova_mcp.parsing import extract_course_schedule_table
from ninova_mcp.planning import (
    filter_academic_calendar,
    find_empty_classrooms,
    find_open_sections,
)
from ninova_mcp.schedule_utils import (
    check_conflicts,
    format_time_point,
    normalize_day,
    parse_time_point,
)

FIXTURES = Path(__file__).parent / "fixtures"


def live_schedule() -> dict[str, Any]:
    html = (FIXTURES / "ders_program_reservation.html").read_text(encoding="utf-8")
    schedule = extract_course_schedule_table(html, "https://obs.itu.edu.tr/test", "https://obs.itu.edu.tr")
    schedule["department_code"] = "BLG"
    return schedule


def by_crn(schedule: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {course["crn"]: course for course in schedule["courses"]}


class ReservationParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.courses = by_crn(live_schedule())

    def test_reservation_segments_are_structured_and_raw_text_is_kept(self) -> None:
        course = self.courses["12476"]
        self.assertEqual(course["reservation"], "END_LS/2/0 | ENDE_LS/3/0 | Diğer/95/95")
        self.assertEqual(course["reservations"], [
            {"program": "END_LS", "general_pool": False, "quota": 2, "enrolled": 0, "free": 2},
            {"program": "ENDE_LS", "general_pool": False, "quota": 3, "enrolled": 0, "free": 3},
            {"program": "Diğer", "general_pool": True, "quota": 95, "enrolled": 95, "free": 0},
        ])

    def test_enrolled_above_quota_is_clamped_at_zero(self) -> None:
        segments = {item["program"]: item for item in self.courses["12513"]["reservations"]}
        self.assertEqual(segments["CEN_LS"]["enrolled"], 10)
        self.assertEqual(segments["CEN_LS"]["free"], 0)
        self.assertEqual(segments["Diğer"]["free"], 4)

    def test_no_reservation_is_null_with_an_empty_list(self) -> None:
        course = self.courses["12462"]
        self.assertIsNone(course["reservation"])
        self.assertEqual(course["reservations"], [])
        self.assertNotIn("reservation_unparsed", course)

    def test_unknown_reservation_shape_is_flagged_not_guessed(self) -> None:
        html = (FIXTURES / "ders_program_reservation.html").read_text(encoding="utf-8")
        html = html.replace("CEN_LS/8/0 | Diğer/62/35", "CEN_LS: 8 kişi")
        course = by_crn(extract_course_schedule_table(html, "https://obs.itu.edu.tr/t", "https://obs.itu.edu.tr"))["12554"]
        self.assertEqual(course["reservation"], "CEN_LS: 8 kişi")
        self.assertEqual(course["reservations"], [])
        self.assertIs(course["reservation_unparsed"], True)


class PlaceholderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.courses = by_crn(live_schedule())

    def test_link_text_is_not_reported_as_a_credit_prerequisite(self) -> None:
        project = self.courses["12586"]
        self.assertIsNone(project["credit_prerequisite"])
        self.assertIs(project["has_credit_prerequisite"], True)
        self.assertIn("DersOnsartList", project["credit_prerequisite_detail_url"])
        self.assertIs(project["has_prerequisites"], True)
        plain = self.courses["12462"]
        self.assertIsNone(plain["credit_prerequisite"])
        self.assertIs(plain["has_credit_prerequisite"], False)

    def test_section_without_time_or_place_is_unscheduled(self) -> None:
        project = self.courses["12586"]
        self.assertEqual(project["sessions"], [])
        self.assertIs(project["unscheduled"], True)
        self.assertIs(project["location_announced_later"], False)

    def test_placeholder_room_and_building_become_null(self) -> None:
        no_room = self.courses["12513"]
        self.assertEqual(no_room["sessions"], [
            {"building": "BBB", "day": "Pazartesi", "time": "14:30/16:29", "room": None},
        ])
        self.assertIs(no_room["unscheduled"], False)
        self.assertIs(no_room["location_announced_later"], True)
        undeclared = self.courses["15823"]
        self.assertIsNone(undeclared["sessions"][0]["building"])
        self.assertIsNone(undeclared["sessions"][0]["room"])
        self.assertIs(undeclared["location_announced_later"], True)

    def test_normal_section_keeps_its_sessions(self) -> None:
        course = self.courses["12469"]
        self.assertEqual(len(course["sessions"]), 2)
        self.assertEqual(course["sessions"][1], {"building": "BBB", "day": "Perşembe", "time": "14:30/16:29", "room": "Z-19"})
        self.assertIs(course["unscheduled"], False)
        self.assertIs(course["location_announced_later"], False)
        self.assertEqual(course["section_kind"], "regular")

    def test_no_placeholder_string_is_left_in_structured_fields(self) -> None:
        placeholders = {"-", "--", "-/-", "undeclared", "Detay"}
        for course in self.courses.values():
            values = [course["reservation"], course["credit_prerequisite"], *course["eligible_programs"]]
            for session in course["sessions"]:
                values.extend(session.values())
            self.assertFalse(placeholders & {value for value in values if isinstance(value, str)}, course["crn"])

    def test_extra_exam_rows_are_tagged(self) -> None:
        self.assertEqual(self.courses["15446"]["section_kind"], "extra_exam")
        self.assertEqual(self.courses["15446"]["method"], "Ek Sınav")
        self.assertIs(self.courses["15446"]["unscheduled"], True)

    def test_dash_in_eligible_programs_is_dropped(self) -> None:
        html = (FIXTURES / "ders_program_reservation.html").read_text(encoding="utf-8")
        self.assertIn(">BLG_LS, BLGE_LS</a>", html)
        html = html.replace(">BLG_LS, BLGE_LS</a>", ">-</a>")
        course = by_crn(extract_course_schedule_table(html, "https://obs.itu.edu.tr/t", "https://obs.itu.edu.tr"))["12462"]
        self.assertEqual(course["eligible_programs"], [])


class OpenSectionSeatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schedules = [live_schedule()]

    def sections(self, **kwargs: Any) -> dict[str, dict[str, Any]]:
        return {item["crn"]: item for item in find_open_sections(self.schedules, **kwargs)["sections"]}

    def test_seats_reserved_for_other_programmes_are_not_offered(self) -> None:
        # capacity 100, enrolled 95: all five free seats belong to END_LS and ENDE_LS.
        result = find_open_sections(self.schedules)
        self.assertNotIn("12476", {item["crn"] for item in result["sections"]})
        self.assertEqual(result["excluded"]["reserved_for_other_programs"], 1)

    def test_general_pool_is_what_counts_without_a_programme(self) -> None:
        section = self.sections()["12513"]
        self.assertEqual(section["available_seats"], 4)
        self.assertEqual(section["general_pool_free_seats"], 4)
        self.assertEqual(section["total_free_seats"], 13)
        self.assertEqual(section["seat_basis"], "general_pool")
        self.assertEqual(section["reserved_free_seats"], [
            {"program": "CES_LS", "free": 8},
            {"program": "BLG_LS", "free": 3},
        ])

    def test_programme_gets_its_own_quota_plus_the_general_pool(self) -> None:
        for code in ("CES_LS", "ces", "CES"):
            with self.subTest(code=code):
                section = self.sections(program_code=code)["12513"]
                self.assertEqual(section["available_seats"], 12)
                self.assertEqual(section["own_program_free_seats"], 8)
                self.assertEqual(section["seat_basis"], "own_program_plus_general_pool")
        # CEN_LS already exceeds its quota: only the general pool is left.
        self.assertEqual(self.sections(program_code="CEN_LS")["12513"]["available_seats"], 4)

    def test_programme_reaches_a_section_whose_general_pool_is_full(self) -> None:
        section = self.sections(program_code="END")["12476"]
        self.assertEqual(section["available_seats"], 2)
        self.assertEqual(section["general_pool_free_seats"], 0)

    def test_available_seats_never_exceed_the_real_free_seats(self) -> None:
        # 12578: capacity 90, enrolled 89; two programmes are over quota.
        section = self.sections(program_code="EHB")["12578"]
        self.assertEqual(section["total_free_seats"], 1)
        self.assertEqual(section["available_seats"], 1)
        for item in find_open_sections(self.schedules, program_code="EHB")["sections"]:
            self.assertGreaterEqual(item["available_seats"], 0)
            self.assertLessEqual(item["available_seats"], item["total_free_seats"])

    def test_programme_outside_the_eligible_list_is_not_offered_the_section(self) -> None:
        result = find_open_sections(self.schedules, program_code="MAT_LS")
        self.assertEqual(result["sections"], [])
        self.assertGreater(result["excluded"]["program_not_eligible"], 0)

    def test_unparsed_reservation_falls_back_to_the_plain_difference_with_a_flag(self) -> None:
        course = {
            "crn": "9", "code": "TST 101", "name": "Deneme", "capacity": 50, "enrolled": 40,
            "reservation": "bilinmeyen biçim", "reservations": [], "reservation_unparsed": True,
            "sessions": [{"day": "Salı", "time": "09:30/11:29", "building": "BBB", "room": "1"}],
        }
        section = find_open_sections([{"department_code": "TST", "courses": [course]}])["sections"][0]
        self.assertEqual(section["available_seats"], 10)
        self.assertEqual(section["seat_basis"], "capacity_minus_enrolled")
        self.assertIs(section["seat_estimate_fallback"], True)

    def test_unscheduled_project_sections_sort_after_scheduled_ones(self) -> None:
        sections = find_open_sections(self.schedules)["sections"]
        crns = [item["crn"] for item in sections]
        self.assertEqual(crns[-1], "12586")  # 197 free seats, but no time or room
        self.assertIs(sections[-1]["unscheduled"], True)
        scheduled = [item["available_seats"] for item in sections if not item["unscheduled"]]
        self.assertEqual(scheduled, sorted(scheduled, reverse=True))

    def test_extra_exam_rows_are_never_listed(self) -> None:
        result = find_open_sections(self.schedules)
        self.assertNotIn("extra_exam", {item.get("section_kind") for item in result["sections"]})
        self.assertEqual(result["excluded"]["extra_exam"], 1)

    def test_plain_dicts_without_reservation_data_still_work(self) -> None:
        schedules = [{"department_code": "BLG", "courses": [
            {"crn": "1", "code": "BLG 1", "capacity": 40, "enrolled": 35, "sessions": [{"day": "Salı", "time": "09:30/10:29"}]},
        ]}]
        section = find_open_sections(schedules)["sections"][0]
        self.assertEqual(section["available_seats"], 5)
        self.assertEqual(section["total_free_seats"], 5)


class OpenSectionQueryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schedules = [live_schedule()]

    def test_compact_code_matches_like_the_spaced_one(self) -> None:
        spaced = find_open_sections(self.schedules, query="BLG 337E")
        self.assertEqual(spaced["count"], 1)
        for query in ("blg337e", "BLG337E", "blg 337e", "Blg337"):
            with self.subTest(query=query):
                result = find_open_sections(self.schedules, query=query)
                self.assertEqual([s["crn"] for s in result["sections"]], [s["crn"] for s in spaced["sections"]])

    def test_name_and_instructor_still_match(self) -> None:
        self.assertEqual(find_open_sections(self.schedules, query="ethics")["count"], 1)
        self.assertEqual(find_open_sections(self.schedules, query="ece yalın")["count"], 1)

    def test_query_that_matches_nothing_says_so(self) -> None:
        result = find_open_sections(self.schedules, query="mat210e")
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["matched_section_count"], 0)
        self.assertEqual(result["no_result_reason"], "query_matched_no_section")
        self.assertIn("message", result)

    def test_query_that_matches_only_full_sections_says_so(self) -> None:
        result = find_open_sections(self.schedules, query="blg223e")
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["matched_section_count"], 1)
        self.assertEqual(result["no_result_reason"], "matching_sections_full")

    def test_query_whose_free_seats_are_reserved_says_so(self) -> None:
        result = find_open_sections(self.schedules, query="BLG 231E")
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["no_result_reason"], "free_seats_reserved_for_other_programs")

    def test_result_with_sections_has_no_reason(self) -> None:
        self.assertNotIn("no_result_reason", find_open_sections(self.schedules))


class DayAndTimeInputTests(unittest.TestCase):
    def test_days_in_turkish_english_and_abbreviations(self) -> None:
        cases = {
            "Salı": "sali", "sali": "sali", "SALI": "sali", "Sal": "sali", "Tuesday": "sali", "tue": "sali",
            "Pazartesi": "pazartesi", "PAZARTESİ": "pazartesi", "Pzt": "pazartesi", "monday": "pazartesi", "Mon": "pazartesi",
            "Çarşamba": "carsamba", "carsamba": "carsamba", "Çar": "carsamba", "Wed": "carsamba", "wednesday": "carsamba",
            "Perşembe": "persembe", "per": "persembe", "Thu": "persembe", "Thursday": "persembe",
            "Cuma": "cuma", "Fri": "cuma", "friday": "cuma",
            "Cumartesi": "cumartesi", "Cmt": "cumartesi", "Sat": "cumartesi",
            "Pazar": "pazar", "Sun": "pazar", "sunday": "pazar", " Paz. ": "pazar",
        }
        for raw, expected in cases.items():
            with self.subTest(day=raw):
                self.assertEqual(normalize_day(raw), expected)

    def test_unknown_day_lists_the_valid_values(self) -> None:
        with self.assertRaises(ValueError) as caught:
            normalize_day("someday")
        message = str(caught.exception)
        for name in ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar", "Monday"):
            self.assertIn(name, message)

    def test_time_formats(self) -> None:
        cases = {"10": 600, "10.00": 600, "10:00": 600, "9:45": 585, "0945": 585, "09.45": 585, " 14:30 ": 870, "0": 0, "23:59": 1439}
        for raw, expected in cases.items():
            with self.subTest(time=raw):
                self.assertEqual(parse_time_point(raw), expected)
        self.assertEqual(format_time_point(585), "09:45")

    def test_bad_time_lists_the_accepted_formats(self) -> None:
        for raw in ("25:00", "10:75", "sabah", "", "10:00-12:00"):
            with self.subTest(time=raw):
                with self.assertRaises(ValueError) as caught:
                    parse_time_point(raw)
                self.assertIn("10:00", str(caught.exception))
                self.assertIn("0945", str(caught.exception))


class EmptyClassroomTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schedules = [live_schedule()]

    def test_english_day_and_bare_hour_are_accepted(self) -> None:
        reference = find_empty_classrooms(self.schedules, day="Salı", time="09:00")
        self.assertEqual(reference["occupied_room_count"], 1)
        for day, clock in (("Tuesday", "9"), ("Sal", "09.00"), ("sali", "0900"), ("TUE", "9:00")):
            with self.subTest(day=day, time=clock):
                result = find_empty_classrooms(self.schedules, day=day, time=clock)
                self.assertEqual(result["occupied_rooms"], reference["occupied_rooms"])
                self.assertEqual(result["day_normalized"], "Salı")
                self.assertEqual(result["time_normalized"], "09:00")

    def test_placeholder_rooms_are_not_rooms(self) -> None:
        result = find_empty_classrooms(self.schedules, day="Pazartesi", time="15:00")
        rooms = {(item["building"], item["room"]) for item in result["empty_rooms"] + result["occupied_rooms"]}
        self.assertEqual(result["known_room_count"], len(rooms))
        for building, room in rooms:
            self.assertNotIn(building, {None, "-", "undeclared"})
            self.assertNotIn(room, {None, "-", "--"})

    def test_errors_name_the_valid_values(self) -> None:
        with self.assertRaises(ValueError) as caught:
            find_empty_classrooms(self.schedules, day="Blursday", time="10:00")
        self.assertIn("Pazartesi", str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            find_empty_classrooms(self.schedules, day="Salı", time="öğlen")
        self.assertIn("HH:MM", str(caught.exception))


class ConflictCheckTests(unittest.TestCase):
    def test_sessions_with_null_fields_do_not_break_the_check(self) -> None:
        courses = by_crn(live_schedule())
        result = check_conflicts([courses["12476"], courses["12578"], courses["12586"], courses["15823"]])
        self.assertEqual(result["conflict_count"], 1)
        self.assertEqual({result["conflicts"][0]["course_a"]["crn"], result["conflicts"][0]["course_b"]["crn"]}, {"12476", "12578"})
        self.assertEqual(result["unscheduled_crns"], ["12586"])


class CalendarFilterLeftoverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calendar = {
            "event_count": 3,
            "coverage": {"scope": "academic_year", "start_date": "2026-09-01", "end_date": "2027-08-31", "complete": True},
            "events": [
                {"description": "Final Exams", "description_tr": "Yarıyıl Sonu Sınavları", "category": "exam",
                 "start_date": "2027-01-11", "end_date": "2027-01-24"},
                {"description": "Final Registration", "description_tr": "Kesin Kayıt", "category": "registration",
                 "start_date": "2026-09-07", "end_date": "2026-09-09"},
                {"description": "Beginning of Fall Term", "description_tr": "Güz Yarıyılı Başlangıcı", "category": "semester",
                 "start_date": "2026-09-21", "end_date": "2026-09-21"},
            ],
        }

    def test_single_bound_inside_coverage_does_not_warn(self) -> None:
        self.assertNotIn("coverage_warning", filter_academic_calendar(self.calendar, date_from="2026-10-01"))
        self.assertNotIn("coverage_warning", filter_academic_calendar(self.calendar, date_to="2027-02-01"))

    def test_single_bound_outside_coverage_warns(self) -> None:
        self.assertIn("coverage_warning", filter_academic_calendar(self.calendar, date_from="2026-01-01"))
        self.assertIn("coverage_warning", filter_academic_calendar(self.calendar, date_to="2028-01-01"))
        self.assertIn("coverage_warning", filter_academic_calendar(self.calendar, date_from="2027-09-15"))

    def test_open_end_still_warns_when_only_one_month_was_read(self) -> None:
        month = {**self.calendar, "coverage": {"scope": "displayed_month", "start_date": "2026-09-01", "end_date": "2026-09-30", "complete": True}}
        self.assertIn("coverage_warning", filter_academic_calendar(month, date_from="2026-09-10"))

    def test_exam_word_does_not_return_registration_rows(self) -> None:
        result = filter_academic_calendar(self.calendar, query="final")
        self.assertEqual([event["description"] for event in result["events"]], ["Final Exams"])
        self.assertIn("query_note", result)

    def test_registration_rows_stay_when_asked_for(self) -> None:
        result = filter_academic_calendar(self.calendar, query="final", category="registration")
        self.assertEqual([event["description"] for event in result["events"]], ["Final Registration"])
        result = filter_academic_calendar(self.calendar, query="final registration")
        self.assertEqual([event["description"] for event in result["events"]], ["Final Registration"])


if __name__ == "__main__":
    unittest.main()
