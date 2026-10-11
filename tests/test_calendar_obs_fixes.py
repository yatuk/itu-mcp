"""Defects fixed in the calendar export, the registration summary and the public OBS client.

Each test states the correct behaviour. Payloads, codes and ids are invented.
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import MagicMock, Mock

from ninova_mcp.calendar_export import find_term_dates
from ninova_mcp.obs_client import ObsClient, ObsError, ObsPublicClient
from ninova_mcp.registration_status import (
    _class_level,
    compact_registration_summary,
    summarize_registration_status,
)

import test_calendar_export_characterization as export_fakes
import test_registration_status_characterization as status_fakes

AMBIGUOUS = "Multiple matching calendar windows are ambiguous."


def export(crns: list[str], calendar: Any = None, **arguments: Any) -> dict[str, Any]:
    """The outcome of one schedule export and the requests it made, in order."""
    obs = {} if calendar is None else {"calendar": calendar}
    return export_fakes.record(lambda host: host.export_course_schedule_ics(crns, ["TST"], **arguments), **obs)


class TermSpanMessageTests(unittest.TestCase):
    def test_wrong_calendar_dates_are_named_as_calendar_dates(self) -> None:
        outcome = export(["10001"], export_fakes.calendar_with_terms(classes_end="2026-09-01"))
        self.assertEqual(outcome["raised"], "ObsError")
        message = outcome["message"]
        self.assertIn("term_start=2026-09-28 (akademik takvim)", message)
        self.assertIn("term_end=2026-09-01 (akademik takvim)", message)
        self.assertIn("term_start ve term_end (YYYY-AA-GG) olarak açıkça verin", message)

    def test_too_long_calendar_term_is_named_as_calendar_dates(self) -> None:
        outcome = export(["10001"], export_fakes.calendar_with_terms(classes_end="2027-09-01"))
        self.assertIn("en fazla 200 gün", outcome["message"])
        self.assertIn("term_end=2027-09-01 (akademik takvim)", outcome["message"])

    def test_each_date_is_named_by_its_own_source(self) -> None:
        outcome = export(["10001"], export_fakes.calendar_with_terms(), term_start="2027-02-01")
        self.assertIn("term_start=2027-02-01 (parametre)", outcome["message"])
        self.assertIn("term_end=2027-01-08 (akademik takvim)", outcome["message"])

    def test_dates_the_caller_gave_keep_the_plain_message(self) -> None:
        outcome = export(["10001"], term_start="2026-09-28", term_end="2026-09-27")
        self.assertEqual(outcome["message"], "term_end, term_start tarihinden önce olamaz.")
        outcome = export(["10001"], term_start="2026-09-28", term_end="2027-04-17")
        self.assertEqual(outcome["message"], "term_start ile term_end arası en fazla 200 gün olabilir.")


class TermSpanOrderTests(unittest.TestCase):
    def test_a_span_the_caller_gave_is_refused_before_any_request(self) -> None:
        for dates in ({"term_start": "2026-09-28", "term_end": "2026-09-27"},
                      {"term_start": "2026-09-28", "term_end": "2027-04-17"}):
            with self.subTest(dates=dates):
                outcome = export(["10001"], export_fakes.calendar_with_terms(), **dates)
                self.assertEqual(outcome["raised"], "ObsError")
                self.assertEqual(outcome["requests"], [])

    def test_a_valid_span_still_reads_the_schedule_and_not_the_calendar(self) -> None:
        outcome = export(["10001"], export_fakes.calendar_with_terms(), **export_fakes.TERM)
        self.assertEqual(outcome["returned"]["status"], "ok")
        self.assertEqual(outcome["requests"], [["schedule", "LS", "TST"]])

    def test_a_calendar_span_is_checked_right_after_the_calendar_is_read(self) -> None:
        # The calendar is looked up by the semester the schedule names, so the
        # schedule request cannot be avoided here; nothing follows the calendar.
        outcome = export(["10001"], export_fakes.calendar_with_terms(classes_end="2026-09-01"))
        self.assertEqual(outcome["requests"], [["schedule", "LS", "TST"], ["calendar"]])
        self.assertNotIn("returned", outcome)


class MalformedCalendarTests(unittest.TestCase):
    def test_non_dict_rows_are_skipped_in_events_and_terms(self) -> None:
        events = [export_fakes.stated("Beginning of Fall Term", "2026-09-28"), "row", None, 7, ["x"],
                  export_fakes.stated("End of Fall Term", "2027-01-08")]
        calendar = {"terms": ["fall", None, 3], "events": events}
        self.assertEqual(find_term_dates(calendar, export_fakes.SEMESTER, "LS"),
                         {"term_start": "2026-09-28", "term_end": "2027-01-08"})

    def test_export_survives_a_malformed_event(self) -> None:
        calendar = {"events": ["row", *export_fakes.CALENDAR_FROM_EVENTS["events"]]}
        outcome = export(["10001"], calendar)
        self.assertEqual(outcome["returned"]["status"], "ok")
        self.assertEqual(outcome["returned"]["term_start"], "2026-09-28")

    def test_unreadable_calendar_date_means_the_date_is_required(self) -> None:
        for value in ("28.09.2026", "2026-13-40", 20260928, ["2026-09-28"]):
            with self.subTest(value=value):
                outcome = export(["10001"], export_fakes.calendar_with_terms(classes_start=value))
                result = outcome["returned"]
                self.assertEqual(result["status"], "term_dates_required")
                self.assertEqual(result["missing_parameters"], ["term_start"])
                self.assertIsNone(result["term_start"])
                self.assertIsNone(result["term_date_sources"]["term_start"])
                self.assertEqual(result["term_date_sources"]["term_end"], "academic_calendar")
                self.assertIsNone(result["ics"])
                self.assertEqual(
                    result["calendar_note"],
                    f"Akademik takvimdeki tarih okunamadı ve kullanılmadı: term_start={value!r}")

    def test_both_calendar_dates_unreadable(self) -> None:
        outcome = export(["10001"], export_fakes.calendar_with_terms(classes_start="x", classes_end="y"))
        self.assertEqual(outcome["returned"]["missing_parameters"], ["term_start", "term_end"])
        self.assertIn("term_start='x', term_end='y'", outcome["returned"]["calendar_note"])

    def test_unreadable_calendar_date_does_not_matter_when_the_caller_gives_it(self) -> None:
        outcome = export(["10001"], export_fakes.calendar_with_terms(classes_start="28.09.2026"),
                         term_start="2026-09-28")
        self.assertEqual(outcome["returned"]["status"], "ok")
        self.assertNotIn("calendar_note", outcome["returned"])

    def test_non_iso_date_from_the_caller_stays_an_input_error(self) -> None:
        outcome = export(["10001"], term_start="28.09.2026", term_end="2027-01-08")
        self.assertEqual(outcome["raised"], "ObsError")
        self.assertEqual(outcome["message"], "term_start YYYY-AA-GG biçiminde bir tarih olmalı: '28.09.2026'")
        self.assertEqual(outcome["requests"], [])


def summarize(case: dict[str, Any]) -> dict[str, Any]:
    return summarize_registration_status(case["registration"], case["lesson"], case["calendar"], now=case["now"])


def program_of(case: dict[str, Any]) -> dict[str, Any]:
    return summarize(case)["per_program"][0]


class ClassLevelTests(unittest.TestCase):
    def test_numbers_and_digit_strings_from_one_to_four(self) -> None:
        for value, expected in ((1, 1), (3, 3), (4, 4), ("3", 3), (" 2 ", 2), (3.0, 3)):
            with self.subTest(value=value):
                self.assertEqual(_class_level(value), expected)

    def test_labels_still_parse(self) -> None:
        for value, expected in (("3. Sınıf", 3), ("3rd Class", 3), ("4 sinif", 4), ("1st class", 1), ("  2. Sınıf  ", 2)):
            with self.subTest(value=value):
                self.assertEqual(_class_level(value), expected)

    def test_anything_else_is_no_class_level(self) -> None:
        for value in (0, 5, 6, 33, -3, "0", "5", "33", "3.5", 3.5, "3 kredi", "202620", True, False, None, "",
                      "Hazırlık", "5. Sınıf", [3], {"level": 3}):
            with self.subTest(value=value):
                self.assertIsNone(_class_level(value))

    def test_numeric_level_finds_the_class_window(self) -> None:
        for value in (3, "3"):
            with self.subTest(value=value):
                row = program_of(status_fakes.case(status_fakes.status_payload(status_fakes.status_program(level=value))))
                self.assertEqual(row["class_level"], 3)
                self.assertEqual(row["class_label"], "3")
                self.assertEqual(row["registration_window"]["class_levels"], [3])
                self.assertIsNone(row["registration_window_unavailable_reason"])


class IdenticalWindowTests(unittest.TestCase):
    MOMENTS = {"upcoming": "2026-09-16T09:00:00+03:00", "ongoing": "2026-09-16T11:00:00+03:00",
               "past": "2026-09-16T14:00:00+03:00"}

    def test_identical_windows_are_one_window_in_every_state(self) -> None:
        for state, moment in self.MOMENTS.items():
            with self.subTest(state=state):
                row = program_of(status_fakes.with_windows(
                    status_fakes.window(*status_fakes.day(16), 3, description="first"),
                    status_fakes.window(*status_fakes.day(16), 3, description="second"),
                    status_fakes.window(*status_fakes.day(16), 3, description="third"),
                    now=status_fakes.at(moment)))
                self.assertIsNone(row["registration_window_unavailable_reason"])
                self.assertEqual(row["registration_window"]["state"], state)
                self.assertEqual(row["registration_window"]["description"], "first")

    def test_two_different_ongoing_windows_stay_ambiguous(self) -> None:
        row = program_of(status_fakes.with_windows(
            status_fakes.window(*status_fakes.day(16, "09:00", "13:00"), 3),
            status_fakes.window(*status_fakes.day(16, "10:00", "14:00"), 3),
            status_fakes.window(*status_fakes.day(16, "10:00", "14:00"), 3),
            now=status_fakes.at(self.MOMENTS["ongoing"])))
        self.assertIsNone(row["registration_window"])
        self.assertEqual(row["registration_window_unavailable_reason"], AMBIGUOUS)

    def test_same_times_for_different_class_levels_are_different_reopenings(self) -> None:
        row = program_of(status_fakes.with_windows(
            status_fakes.window(*status_fakes.day(17), 3),
            status_fakes.window(*status_fakes.day(16), None),
            status_fakes.window(*status_fakes.day(16), None, class_levels=[1, 2]),
            now=status_fakes.at(self.MOMENTS["ongoing"])))
        self.assertIsNone(row["all_students_reopening"])
        self.assertEqual(row["all_students_reopening_unavailable_reason"], AMBIGUOUS)


class ReopeningReasonTests(unittest.TestCase):
    CASE = status_fakes.with_windows(
        status_fakes.window(*status_fakes.day(17), 3),
        status_fakes.window(*status_fakes.day(19, "10:00", "13:00"), None),
        status_fakes.window(*status_fakes.day(19, "10:00", "14:00"), None))

    def test_ambiguous_reopening_says_why_and_the_reason_survives_the_default_output(self) -> None:
        summary = summarize(self.CASE)
        row = summary["per_program"][0]
        self.assertIsNone(row["all_students_reopening"])
        self.assertEqual(row["all_students_reopening_unavailable_reason"], AMBIGUOUS)
        compact = compact_registration_summary(summary, self.CASE["registration"])["per_program"][0]
        self.assertIsNone(compact["all_students_reopening"])
        self.assertEqual(compact["all_students_reopening_unavailable_reason"], AMBIGUOUS)

    def test_no_reason_when_there_is_a_reopening_or_none_is_published(self) -> None:
        for case in (status_fakes.case(), status_fakes.with_windows(status_fakes.window(*status_fakes.day(17), 3))):
            summary = summarize(case)
            self.assertIsNone(summary["per_program"][0]["all_students_reopening_unavailable_reason"])
            compact = compact_registration_summary(summary, case["registration"])["per_program"][0]
            self.assertNotIn("all_students_reopening_unavailable_reason", compact)
            self.assertIn("all_students_reopening", compact)

    def test_tool_description_names_the_reason_field(self) -> None:
        from ninova_mcp.tool_metadata.registration import REGISTRATION_TOOLS

        tool = next(item for item in REGISTRATION_TOOLS if item["name"] == "obs_get_registration_status")
        self.assertIn("all_students_reopening_unavailable_reason", tool["description"])


class EmptyLessonRowTests(unittest.TestCase):
    WARNING = "The corresponding course-registration program is missing or ambiguous."

    def test_empty_lesson_row_is_no_evidence_of_a_program(self) -> None:
        for status in ("", "Aktif"):
            with self.subTest(status=status):
                row = program_of(status_fakes.case(
                    status_fakes.status_payload(status_fakes.overall_program(durum=status)),
                    status_fakes.lesson_payload({})))
                self.assertIsNone(row["program_match_method"])
                self.assertEqual([item["source_url"] for item in row["enrollment_status_evidence"]],
                                 ["https://obs.itu.edu.tr/api/ogrenci/KayitDurumu"])
                self.assertEqual(row["warnings"], [self.WARNING])
                self.assertIsNone(row["course_registration_term"])

    def test_a_real_sole_lesson_program_is_still_related(self) -> None:
        row = program_of(status_fakes.case(
            status_fakes.status_payload(status_fakes.overall_program()),
            status_fakes.lesson_payload(status_fakes.lesson_program())))
        self.assertEqual(row["program_match_method"], "sole_program_with_university_overall_status")
        self.assertEqual(row["warnings"], [])
        self.assertEqual(len(row["enrollment_status_evidence"]), 2)


DEPARTMENTS = [{"brans_kodu_id": 3, "code": "TST"}, {"brans_kodu_id": 5, "code": None}]


def public_client() -> ObsPublicClient:
    client = ObsPublicClient(session=MagicMock())
    client._get_html = Mock(side_effect=AssertionError("no request expected"))  # type: ignore[method-assign]
    return client


class EmptyDepartmentTests(unittest.TestCase):
    def test_empty_department_is_an_input_error_without_a_request(self) -> None:
        for department in ("", "   ", chr(9)):
            with self.subTest(department=department):
                client = public_client()
                client.list_departments = Mock(return_value=DEPARTMENTS)  # type: ignore[method-assign]
                for call in (lambda: client._resolve_department("LS", department),
                             lambda: client.get_course_schedule("LS", department),
                             lambda: client.get_course_schedule_by_crn("LS", department, "10001")):
                    with self.assertRaises(ObsError) as caught:
                        call()
                    self.assertIn("Bölüm kodu boş olamaz", str(caught.exception))
                    self.assertIn("BLG", str(caught.exception))
                client.list_departments.assert_not_called()

    def test_a_real_code_still_resolves(self) -> None:
        client = public_client()
        client.list_departments = Mock(return_value=DEPARTMENTS)  # type: ignore[method-assign]
        self.assertEqual(client._resolve_department("LS", " tst "), 3)


class NumericCourseCodeTests(unittest.TestCase):
    def client(self) -> ObsPublicClient:
        client = public_client()
        client._build_course_index = Mock(return_value={"tst": 304, "304": 304, "12": 12})  # type: ignore[method-assign]
        return client

    def test_all_digit_input_is_refused_and_the_error_mentions_crn(self) -> None:
        for query in ("30412", "304", "12", " 12345 "):
            with self.subTest(query=query):
                client = self.client()
                with self.assertRaises(ObsError) as caught:
                    client.resolve_course_code(query)
                self.assertIn("CRN", str(caught.exception))
                self.assertIn(repr(query.strip()), str(caught.exception))
                client._build_course_index.assert_not_called()

    def test_a_code_is_never_matched_through_an_id_key(self) -> None:
        client = self.client()
        client.search_courses = Mock(side_effect=ObsError("A full course code is required."))  # type: ignore[method-assign]
        with self.assertRaises(ObsError):
            client.resolve_course_code("304A 101")
        self.assertEqual(client.resolve_course_code("tst 101e")["matched_key"], "tst")
        self.assertEqual(client.resolve_course_code("TST")["source"], "dept_index")


class BranchKeyTests(unittest.TestCase):
    def test_turkish_spellings_share_the_lookup_and_the_cache_entry(self) -> None:
        for first in ("işl", "İŞL", "ISL", "Işl"):
            with self.subTest(first=first):
                client = public_client()
                client._build_course_index = Mock(return_value={"isl": 77, "77": 77})  # type: ignore[method-assign]
                client._get_html = Mock(return_value=("<html></html>", "https://obs.itu.edu.tr/x"))  # type: ignore[method-assign]
                result = client.get_branch_prerequisites(first)
                self.assertEqual(result["brans_kodu_id"], 77)
                self.assertEqual(result["branch"], "ISL")
                for spelling in ("işl", "İŞL", "ISL", "Işl", "  isl "):
                    self.assertIs(client.get_branch_prerequisites(spelling), result)
                client._get_html.assert_called_once()


class DeadAttributeTests(unittest.TestCase):
    def test_the_course_index_lives_only_in_the_cache(self) -> None:
        client = public_client()
        self.assertFalse(hasattr(client, "_course_index"))
        client._get_html = Mock(return_value=("<html></html>", "https://obs.itu.edu.tr/x"))  # type: ignore[method-assign]
        index = client._build_course_index()
        self.assertFalse(hasattr(client, "_course_index"))
        self.assertIs(client._build_course_index(), index)
        client._get_html.assert_called_once()


TERMS = {"ogrenciDonemListesi": [
    {"akademikDonemId": 41, "donemKodu": "204010", "akademikDonemAdi": "2039-2040 Güz Dönemi"},
    {"akademikDonemId": 42, "donemKodu": "204020", "akademikDonemAdi": "2039-2040 Bahar Dönemi"},
]}


class EmptySemesterTests(unittest.TestCase):
    def resolve(self, semester: Any) -> dict[str, Any]:
        obs = ObsClient(ninova_client=MagicMock())
        obs.list_semesters = Mock(return_value=TERMS)  # type: ignore[method-assign]
        return obs.resolve_semester(semester)

    def test_empty_text_means_the_latest_term_like_none(self) -> None:
        latest = self.resolve(None)
        self.assertEqual(latest["akademikDonemId"], 42)
        for semester in ("", "   ", chr(9) + chr(10), '""', "''", ' " " ', "``", "“”"):
            with self.subTest(semester=semester):
                self.assertEqual(self.resolve(semester), latest)

    def test_text_that_names_nothing_is_still_not_found(self) -> None:
        for semester in ("dönemi", "-", "kış"):
            with self.subTest(semester=semester):
                with self.assertRaisesRegex(ObsError, "Semester not found"):
                    self.resolve(semester)
        with self.assertRaisesRegex(ObsError, "Semester must be"):
            self.resolve(False)


if __name__ == "__main__":
    unittest.main()
