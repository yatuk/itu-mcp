"""Characterization of the two .ics export tools and the term-date lookup.

Each scenario calls a tool against fakes and records the whole result (the
complete ``ics`` text included), or the exception, together with the requests
the fakes saw, in order. The recording was taken from the code before it was
restructured (``fixtures/characterization/calendar_export.json.gz``). The
writer's clock is frozen so DTSTAMP is the same on every run; UIDs are hashes
of the event's own data and need no pinning.
"""

from __future__ import annotations

import unittest
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime
from typing import Any
from unittest.mock import patch

from ninova_mcp import ics
from ninova_mcp.calendar_export import CalendarExportToolsMixin, find_term_dates
from ninova_mcp.obs_client import ObsError, ObsPublicClient
from ninova_mcp.public_client import ItuPublicError

import characterization_golden as golden

GOLDEN_FILE = "calendar_export.json.gz"
SEMESTER = "2026-2027 Güz Dönemi"
# 2026-09-28 is a Monday.
TERM = {"term_start": "2026-09-28", "term_end": "2027-01-08"}


class FrozenClock(datetime):
    """``datetime`` whose ``now`` is fixed; everything else is the real class."""

    @classmethod
    def now(cls, tz: Any = None) -> datetime:  # type: ignore[override]
        return datetime(2026, 10, 11, 9, 30, 15, tzinfo=tz)


def session(day: Any, clock: Any, building: Any = "AAA", room: Any = "Z-1") -> dict[str, Any]:
    return {"building": building, "day": day, "time": clock, "room": room}


def course(crn: Any, code: Any, name: Any, sessions: Any, **extra: Any) -> dict[str, Any]:
    return {"crn": crn, "code": code, "name": name, "method": "Fiziksel (Yüz yüze)",
            "instructor": "Çağrı Örnekçioğlu", "sessions": sessions, **extra}


SCHEDULES: dict[str, list[dict[str, Any]]] = {
    "TST": [
        course("10001", "TST 101E", "Sample Course", [
            session("Salı", "08:30/10:29"), session("Perşembe", "14:30/16:29", "AAA", "104")]),
        course("10002", "TST 202E", "Işık, Ses; Görüntü", [session("Çarşamba", "09:30/12:29", "BBB", "2")]),
        course("10003", "TST 499", "Bitirme Çalışması", [session("-", "-/-", "--", "--")]),
        course("10004", "TST 303", "Karma", [
            session("Pazartesi", "13:30/15:29"), session("--", "--"), session("Cuma", "-/-"),
            session("Funday", "10:30/11:29"), session("Salı", "12:30/11:29"), session("Salı", "25:00/26:00"),
            session("Salı", "9.30-10.30"), session(None, None)]),
        course("10005", "TST 404", "Haftanın Günleri", [
            session("Pazartesi", "08:30/09:29"), session("Tuesday", "08:30/09:29"),
            session("Çarşamba", "08:30/09:29"), session("Perşembe", "08:30/09:29"),
            session("Cuma", "08:30/09:29"), session("Cumartesi", "08:30/09:29"),
            session("Pazar", "08:30/09:29")]),
        # The same hour in two and then three rooms.
        course("10006", "TST 505", "Laboratuvar", [
            session("Salı", "13:30/15:29", "LAB", "1"), session("Salı", "13:30/15:29", "LAB", "2"),
            session("Salı", "13:30/15:29", "LAB", "3")]),
        course("10007", "TST 606", "Oturumsuz", []),
        course("10008", "TST 707", "Oturum Listesi Yok", None),
        course("10009", "", "", [session("Cuma", "10:30/12:29", "-", "--")], instructor="--", method="-"),
        course(" 10010 ", None, None, [session("Cuma", "15:30/17:29", None, "B-2")], instructor=None, method=None),
        course("10011", "", "Yalnızca Ad", [session("Çarşamba", "15:30/17:29", "CCC", None)]),
        course(10012, "TST 808", "Sayı CRN", [session("Pazartesi", "10:30/11:29")]),
    ],
    "ABC": [
        course("20001", "ABC 301", "Sayısal Çözümleme", [session("Cuma", "13:30/16:29", "MED", "--")]),
        # Also listed under TST: the first department scanned wins.
        course("10001", "ABC 999", "Shadow Listing", [session("Pazar", "08:30/09:29")]),
    ],
    "EMP": [],
}


def calendar_with_terms(**overrides: Any) -> dict[str, Any]:
    fall = {"academic_year": "2026-2027", "term": "fall", "scope": "undergraduate",
            "classes_start": "2026-09-28", "classes_end": "2027-01-08", **overrides}
    return {"terms": [
        fall,
        {**fall, "scope": "graduate", "classes_start": "2026-10-05", "classes_end": "2027-01-15"},
        {**fall, "term": "spring", "classes_start": "2027-02-08", "classes_end": "2027-05-21"},
    ], "events": []}


def stated(description: str, start: Any, end: Any = None, **fields: Any) -> dict[str, Any]:
    return {"description": description, "start_date": start, "end_date": end or start,
            "scope": "undergraduate", "academic_year": "2026-2027", **fields}


CALENDAR_FROM_EVENTS = {"events": [
    stated("Beginning of Fall Term", "2026-09-28"),
    stated("End of the Fall Semester.", "2027-01-08"),
    stated("Beginning of Fall Term", "2026-10-05", scope="graduate"),
    stated("End of Fall Term", "2026-01-09", academic_year="2025-2026"),
    stated("Beginning of Sping Term", "2027-02-08"),
    stated("End of Spring Term", "2027-05-21"),
    stated("Beginning of Summer School", "2027-07-05"),
    stated("End of Summer School", "2027-08-13"),
    stated("Last day before the end of fall term", "2026-12-31"),
]}


class FakeObsPublic:
    """Stands in for ``ObsPublicClient`` and writes every request to a log."""

    _normalize_program_type = ObsPublicClient._normalize_program_type
    PROGRAM_TYPE_MAP = ObsPublicClient.PROGRAM_TYPE_MAP

    def __init__(self, log: list[Any], calendar: Any = None, semester: Any = SEMESTER,
                 semesters: dict[str, Any] | None = None) -> None:
        self.log = log
        self.calendar = {"events": []} if calendar is None else calendar
        self.semester = semester
        self.semesters = semesters or {}

    def get_course_schedule(self, program_type: str, department: str) -> dict[str, Any]:
        self.log.append(["schedule", program_type, department])
        if department not in SCHEDULES:
            raise ObsError(f"Bölüm kodu bulunamadı: {department!r}.")
        semester = self.semesters.get(department, self.semester)
        result: dict[str, Any] = {"courses": deepcopy(SCHEDULES[department]), "department_code": department}
        if semester != "absent":
            result["semester"] = semester
        return result

    def get_academic_calendar(self) -> dict[str, Any]:
        self.log.append(["calendar"])
        if isinstance(self.calendar, Exception):
            raise self.calendar
        return deepcopy(self.calendar)


EXAM_URL = "https://obs.itu.edu.tr/public/FinalTakvimi/SearchFinalTakvimiByDersBransKodu?DersBransKoduId="


def exam(crn: Any, code: Any, name: Any, date_: Any, time_: Any, building: Any = "AAA", room: Any = "Z-1") -> dict[str, Any]:
    return {"crn": crn, "course_code": code, "course_name": name, "date": date_, "time": time_,
            "building": building, "room": room}


EXAMS: dict[str, Any] = {
    "TST": {"url": EXAM_URL + "1", "published": True, "exams": [
        exam("10001", "TST 101E", "Sample Course", "11.01.2027", "10:00-12:00"),
        # A second section of the same course, and an exact repeat of the first row.
        exam("10021", "TST 101E", "Sample Course", "11.01.2027", "10:00-12:00", "BBB", "3"),
        exam("10001", "TST 101E", "Sample Course", "11.01.2027", "10:00-12:00"),
        exam("10002", "tst202e", "Işık, Ses; Görüntü", "2027-01-12", "13:30", "BBB", "-"),
        exam("10003", "TST 499", "Bitirme Çalışması", "13.01.2027", "-", "--", "--"),
        exam("10004", "TST 303", "Karma", None, "09:00", "AAA", "1"),
        exam(None, "TST 404", None, "14/01/2027", "09:00 - 11:30", None, None),
        exam("", "TST 505", "", "15.01.2027", "16:00-15:00"),
        exam("10006", "TST 606", "İki Tarih", "15.01.2027 16.01.2027", "10:00"),
        exam("10007", "TST 707", "Üç Saat", "15.01.2027", "10:00 11:00 12:00"),
        exam("10008", "TST 808", "Geçersiz Tarih", "31.02.2027", "10:00"),
        exam("10009", "TST 909", "Geçersiz Saat", "15.01.2027", "24:30"),
        exam("10010", "TST 1001E", "Yazıyla Tarih", "18 January 2027", "08:30-10:30"),
        exam(10011, "TST 111", "Sayı CRN", "19.01.2027", "08:30"),
        exam("10012", "TST 112", "Okunmayan Tarih", "ocak sonu", "08:30"),
    ]},
    "ABC": {"url": EXAM_URL + "2", "published": False, "exams": [
        exam("20001", "ABC 301", "Sayısal Çözümleme", "11.01.2027", "10:00")]},
    "NOU": {"published": True, "exams": [exam("30001", "NOU 100", "Adressiz", "20.01.2027", "10:00-11:00")]},
    "NUL": {"url": "", "published": True, "exams": None},
    "BARE": {},
}


class FakeItuPublic:
    def __init__(self, log: list[Any]) -> None:
        self.log = log

    def get_final_exam_schedule(self, department_code: str) -> dict[str, Any]:
        self.log.append(["exams", department_code])
        if department_code not in EXAMS:
            raise ItuPublicError(f"Geçersiz bölüm kodu: {department_code}.")
        return deepcopy(EXAMS[department_code])


class Host(CalendarExportToolsMixin):
    """The mixin with fakes in place of the two clients; nothing else is used."""

    def __init__(self, log: list[Any], **obs: Any) -> None:
        self.obs_public = FakeObsPublic(log, **obs)  # type: ignore[assignment]
        self.itu_public = FakeItuPublic(log)  # type: ignore[assignment]


def record(call: Callable[[Host], Any], **obs: Any) -> dict[str, Any]:
    log: list[Any] = []
    host = Host(log, **obs)
    with patch.object(ics, "datetime", FrozenClock):
        recorded = golden.outcome(lambda: call(host))
    recorded["requests"] = log
    return recorded


def schedule(crns: Any, departments: Any = ("TST",), dates: dict[str, Any] | None = TERM,
             obs: dict[str, Any] | None = None, **kwargs: Any) -> Callable[[], dict[str, Any]]:
    departments = list(departments) if isinstance(departments, tuple) else departments
    arguments = {**(dates or {}), **kwargs}
    return lambda: record(lambda host: host.export_course_schedule_ics(crns, departments, **arguments), **(obs or {}))


def exams(codes: Any) -> Callable[[], dict[str, Any]]:
    return lambda: record(lambda host: host.export_exam_schedule_ics(codes))


def schedule_scenarios() -> dict[str, Callable[[], dict[str, Any]]]:
    every = ["10001", "10002", "10003", "10004", "10005", "10006", "10007", "10008", "10009", "10010",
             "10011", "10012", "20001", "99999"]
    with_terms = {"calendar": calendar_with_terms()}
    return {
        # Dates given by the caller.
        "two_departments_every_course": schedule(every, ("TST", "ABC")),
        "one_course": schedule(["10001"]),
        "crn_listed_by_two_departments_first_scan_wins": schedule(["10001"], ("ABC", "TST")),
        "integer_and_padded_crns": schedule([10001, " 10002 ", "10001"]),
        "five_digit_and_four_digit_crns": schedule(["1000", "10001"]),
        "same_slot_in_three_rooms": schedule(["10006"]),
        "only_placeholders": schedule(["10003", "10007", "10008"]),
        "only_missing_crns": schedule(["55555", "66666"], ("EMP",)),
        "course_without_code_or_name": schedule(["10009", "10010", "10011", "10012"]),
        "lowercase_and_repeated_departments": schedule(["20001"], [" abc ", "ABC", "tst"]),
        "program_type_alias": schedule(["10001"], program_type="lisans"),
        "program_type_graduate": schedule(["10001"], program_type="LU"),
        "short_term_leaves_some_days_out": schedule(
            ["10005"], dates={"term_start": "2026-09-30", "term_end": "2026-10-02"}),
        "term_of_one_day": schedule(["10005"], dates={"term_start": "2026-09-28", "term_end": "2026-09-28"}),
        "term_of_exactly_the_longest_span": schedule(
            ["10001"], dates={"term_start": "2026-09-28", "term_end": "2027-04-16"}),
        "dates_with_surrounding_spaces": schedule(
            ["10001"], dates={"term_start": " 2026-09-28 ", "term_end": "2027-01-08\n"}),
        "no_semester_label": schedule(["10001", "10006"], obs={"semester": None}),
        "semester_key_absent": schedule(["10001"], obs={"semester": "absent"}),
        "unreadable_semester_label": schedule(["10001"], obs={"semester": "Güz 2026"}),
        "spring_semester_label": schedule(["10001"], obs={"semester": "2026-2027 Bahar Dönemi"}),
        "semester_comes_from_the_first_department_that_has_one": schedule(
            ["10001", "20001"], ("TST", "ABC"),
            obs={"semesters": {"TST": None, "ABC": "2026-2027 Yaz Dönemi"}}),
        "calendar_is_not_read_when_both_dates_are_given": schedule(["10001"], obs=with_terms),

        # Dates from the academic calendar.
        "both_dates_from_calendar_terms": schedule(["10001"], dates=None, obs=with_terms),
        "graduate_dates_from_calendar_terms": schedule(["10001"], dates=None, obs=with_terms, program_type="LU"),
        "both_dates_from_calendar_events": schedule(["10001"], dates=None, obs={"calendar": CALENDAR_FROM_EVENTS}),
        "start_given_end_from_calendar": schedule(["10001"], dates={"term_start": "2026-10-05"}, obs=with_terms),
        "end_given_start_from_calendar": schedule(["10001"], dates={"term_end": "2026-12-25"}, obs=with_terms),
        "blank_dates_count_as_not_given": schedule(["10001"], dates={"term_start": "", "term_end": ""}, obs=with_terms),
        "calendar_states_only_the_start": schedule(
            ["10001"], dates=None, obs={"calendar": calendar_with_terms(classes_end=None)}),
        "calendar_states_only_the_end": schedule(
            ["10001"], dates=None, obs={"calendar": calendar_with_terms(classes_start="")}),
        "calendar_states_neither": schedule(["10001"], dates=None),
        "calendar_states_neither_one_given": schedule(["10001"], dates={"term_end": "2027-01-08"}),
        "calendar_unreadable": schedule(["10001"], dates=None, obs={"calendar": ObsError("Academic calendar returned HTTP 503")}),
        "calendar_unreadable_one_date_given": schedule(
            ["10001"], dates={"term_start": "2026-09-28"}, obs={"calendar": ObsError("zaman aşımı")}),
        "calendar_failure_of_another_kind_is_not_caught": schedule(
            ["10001"], dates=None, obs={"calendar": RuntimeError("unexpected")}),
        "no_semester_label_means_no_calendar_dates": schedule(
            ["10001"], dates=None, obs={**with_terms, "semester": None}),
        "calendar_term_date_unreadable": schedule(
            ["10001"], dates=None, obs={"calendar": calendar_with_terms(classes_start="28.09.2026")}),
        "calendar_dates_in_the_wrong_order": schedule(
            ["10001"], dates=None, obs={"calendar": calendar_with_terms(classes_end="2026-09-01")}),
        "calendar_dates_too_far_apart": schedule(
            ["10001"], dates=None, obs={"calendar": calendar_with_terms(classes_end="2027-09-01")}),
        "missing_crn_with_dates_required": schedule(["99999"], dates=None),

        # Refused input: nothing may be requested.
        "crns_is_text": schedule("10001"),
        "crns_is_none": schedule(None),
        "crns_is_empty": schedule([]),
        "crns_are_blank": schedule(["", "  "]),
        "too_many_crns": schedule([str(10000 + index) for index in range(31)]),
        "exactly_the_most_crns": schedule([str(10000 + index) for index in range(30)]),
        "invalid_crns": schedule(["10001", "123", "ABCDE", "123456", "１２３４５"]),
        "departments_is_text": schedule(["10001"], "TST"),
        "departments_is_empty": schedule(["10001"], []),
        "too_many_departments": schedule(["10001"], [f"D{index:02d}" for index in range(26)]),
        "invalid_departments": schedule(["10001"], ["TST", "B", "TOOLONG", "T-T", "çğ"]),
        "invalid_program_type": schedule(["10001"], program_type="XX"),
        "invalid_start_date": schedule(["10001"], dates={"term_start": "28.09.2026", "term_end": "2027-01-08"}),
        "invalid_end_date": schedule(["10001"], dates={"term_start": "2026-09-28", "term_end": "2027-02-30"}),
        "invalid_crn_is_reported_before_invalid_department": schedule(["1"], ["!"], program_type="XX"),
        "invalid_department_is_reported_before_invalid_program_type": schedule(["10001"], ["!"], program_type="XX"),
        "invalid_program_type_is_reported_before_invalid_date": schedule(
            ["10001"], dates={"term_start": "x"}, program_type="XX"),

        # Refused after the schedules were read.
        "end_before_start": schedule(["10001"], dates={"term_start": "2026-09-28", "term_end": "2026-09-27"}),
        "span_one_day_too_long": schedule(["10001"], dates={"term_start": "2026-09-28", "term_end": "2027-04-17"}),
        "unknown_department_stops_the_scan": schedule(["10001"], ("TST", "ZZZ", "ABC")),
    }


def exam_scenarios() -> dict[str, Callable[[], dict[str, Any]]]:
    return {
        "every_row_kind": exams([
            "TST 101E", "TST 202E", "TST 499", "TST 303", "TST 404", "TST 505", "TST 606", "TST 707",
            "TST 808", "TST 909", "TST 1001E", "TST 111", "TST 112", "TST 999"]),
        "one_exam_with_end_time": exams(["TST 101E"]),
        "one_exam_without_end_time": exams(["TST 202E"]),
        "spelling_variants_are_one_code": exams(["tst101e", "TST 101E", " TST101E ", "Tst 101e"]),
        "several_branches_keep_request_order": exams(["NOU 100", "TST 101E", "ABC 301", "NOU 200", "TST 202E"]),
        "unpublished_branch": exams(["ABC 301", "ABC 302"]),
        "unknown_branch": exams(["ZZZ 101", "ZZZ 102"]),
        "branch_without_url": exams(["NOU 100"]),
        "branch_with_null_exam_list": exams(["NUL 100"]),
        "branch_answer_without_fields": exams(["BARE 100"]),
        "only_codes_without_rows": exams(["TST 998", "TST 999"]),
        "only_unreadable_rows": exams(["TST 499", "TST 303"]),
        "codes_is_text": exams("TST 101E"),
        "codes_is_none": exams(None),
        "codes_is_empty": exams([]),
        "codes_are_blank": exams([" ", ""]),
        "too_many_codes": exams([f"TST {100 + index}" for index in range(31)]),
        "exactly_the_most_codes": exams([f"TST {100 + index}" for index in range(30)]),
        "unreadable_code": exams(["TST 101E", "not a code"]),
        "unreadable_code_number_only": exams(["101"]),
    }


def lookup(calendar: Any, label: Any = SEMESTER, program_type: str = "LS") -> Callable[[], dict[str, Any]]:
    return lambda: golden.outcome(lambda: find_term_dates(deepcopy(calendar), label, program_type))


def term_date_scenarios() -> dict[str, Callable[[], dict[str, Any]]]:
    terms = calendar_with_terms()
    return {
        "terms_fall_undergraduate": lookup(terms),
        "terms_fall_graduate": lookup(terms, program_type="LU"),
        "terms_fall_evening_and_graduate_evening": lambda: [
            lookup(terms, program_type="ÖL")(), lookup(terms, program_type="LUİ")()],
        "terms_spring": lookup(terms, "2026-2027 Bahar Yarıyılı"),
        "terms_summer_not_stated": lookup(terms, "2026-2027 Yaz Dönemi"),
        "terms_other_year": lookup(terms, "2025-2026 Güz Dönemi"),
        "label_unreadable": lookup(terms, "Güz"),
        "label_missing": lookup(terms, None),
        "program_type_unknown": lookup(terms, program_type="XX"),
        "term_stated_twice_is_not_used": lookup({"terms": [*terms["terms"], terms["terms"][0]], "events": []}),
        "term_stated_twice_falls_back_to_events": lookup(
            {"terms": [*terms["terms"], terms["terms"][0]], "events": CALENDAR_FROM_EVENTS["events"]}),
        "terms_hold_non_dicts": lookup({"terms": ["fall", None, terms["terms"][0]]}),
        "terms_null_and_events_null": lookup({"terms": None, "events": None}),
        "empty_calendar": lookup({}),
        "terms_start_only_events_supply_the_end": lookup(
            {"terms": calendar_with_terms(classes_end=None)["terms"], "events": CALENDAR_FROM_EVENTS["events"]}),
        "terms_start_is_kept_over_a_different_event_start": lookup(
            {"terms": calendar_with_terms(classes_start="2026-09-21", classes_end="")["terms"],
             "events": CALENDAR_FROM_EVENTS["events"]}),
        "events_fall": lookup(CALENDAR_FROM_EVENTS),
        "events_fall_graduate_states_only_the_start": lookup(CALENDAR_FROM_EVENTS, program_type="LU"),
        "events_spring_with_the_calendar_typo": lookup(CALENDAR_FROM_EVENTS, "2026-2027 Bahar Dönemi"),
        "events_summer_school": lookup(CALENDAR_FROM_EVENTS, "2026-2027 Yaz Öğretimi"),
        "events_other_year": lookup(CALENDAR_FROM_EVENTS, "2025-2026 Güz Dönemi"),
        "events_conflicting_starts": lookup({"events": [
            stated("Beginning of Fall Term", "2026-09-28"), stated("Beginning of the Fall Semester", "2026-09-21"),
            stated("End of Fall Term", "2027-01-08")]}),
        "events_same_date_twice_is_one_date": lookup({"events": [
            stated("Beginning of Fall Term", "2026-09-28"), stated("BEGINNING OF FALL TERM ", "2026-09-28"),
            stated("End of Fall Term", "2027-01-04", "2027-01-08")]}),
        "events_without_dates_or_description": lookup({"events": [
            stated("Beginning of Fall Term", None), stated("End of Fall Term", "2027-01-08", end_date=""),
            {"scope": "undergraduate", "academic_year": "2026-2027"},
            {"scope": "undergraduate", "academic_year": "2026-2027", "description": None}]}),
        "events_school_wording_only_counts_for_summer": lookup({"events": [
            stated("Beginning of Fall School", "2026-09-28"), stated("End of Fall School", "2027-01-08")]}),
        "events_hold_a_non_dict": lookup({"events": [stated("Beginning of Fall Term", "2026-09-28"), "row"]}),
    }


class CalendarExportCharacterizationTests(unittest.TestCase):
    maxDiff = None
    recorded: dict[str, Any]
    actual: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        cls.actual = {}
        for prefix, group in (("schedule", schedule_scenarios()), ("exams", exam_scenarios()),
                              ("term_dates", term_date_scenarios())):
            cls.actual.update({f"{prefix}:{name}": call() for name, call in group.items()})
        if golden.updating():
            golden.save(GOLDEN_FILE, cls.actual)
        cls.recorded = golden.load(GOLDEN_FILE)

    def results(self, prefix: str) -> dict[str, dict[str, Any]]:
        return {name: value for name, value in self.actual.items() if name.startswith(prefix + ":")}

    def test_every_scenario_is_recorded(self) -> None:
        self.assertEqual(list(self.recorded), list(self.actual))
        self.assertGreater(len(self.actual), 100)

    def test_outcomes_match_the_recording(self) -> None:
        for name, actual in self.actual.items():
            with self.subTest(scenario=name):
                self.assertEqual(golden.serialize(actual), golden.serialize(self.recorded.get(name)))

    def test_a_second_run_gives_the_same_text(self) -> None:
        again = schedule_scenarios()["two_departments_every_course"]()
        self.assertEqual(again["returned"]["ics"], self.actual["schedule:two_departments_every_course"]["returned"]["ics"])
        self.assertIn("DTSTAMP:20261011T093015Z\r\n", again["returned"]["ics"])

    def test_refused_input_reaches_no_client(self) -> None:
        tools = {**self.results("schedule"), **self.results("exams")}
        refused = [name for name, value in tools.items() if "raised" in value and not value["requests"]]
        self.assertGreaterEqual(len(refused), 20)
        for name in (
            "schedule:crns_is_text", "schedule:invalid_crns", "schedule:invalid_departments",
            "schedule:invalid_program_type", "schedule:invalid_start_date", "schedule:invalid_end_date",
            "exams:codes_is_text", "exams:unreadable_code", "exams:too_many_codes",
        ):
            self.assertIn(name, refused)

    def test_the_scenarios_reach_every_status_and_skip_reason(self) -> None:
        statuses, reasons, sources = set(), set(), set()
        for name, value in {**self.results("schedule"), **self.results("exams")}.items():
            result = value.get("returned")
            if result is None:
                continue
            statuses.add((name.split(":")[0], result["status"]))
            reasons.update(item["reason"] for item in result.get("skipped", []))
            sources.update((result.get("term_date_sources") or {}).values())
        self.assertEqual(statuses, {
            ("schedule", "ok"), ("schedule", "no_events"), ("schedule", "term_dates_required"),
            ("exams", "ok"), ("exams", "no_events")})
        self.assertEqual(reasons, {
            "no_sessions", "unknown_day", "unknown_time", "no_occurrence_in_term",
            "exam_schedule_unavailable", "exam_schedule_not_published", "unknown_date"})
        self.assertEqual(sources, {None, "parameter", "academic_calendar"})

    def test_no_calendar_text_without_both_term_dates(self) -> None:
        required = [value["returned"] for value in self.results("schedule").values()
                    if value.get("returned", {}).get("status") == "term_dates_required"]
        self.assertGreaterEqual(len(required), 7)
        for result in required:
            self.assertIsNone(result["ics"])
            self.assertEqual(result["event_count"], 0)
            self.assertNotIn("events", result)
            for name in result["missing_parameters"]:
                self.assertIsNone(result[name])
                self.assertIsNone(result["term_date_sources"][name])


if __name__ == "__main__":
    unittest.main()
