"""Tests for the .ics export tools and the small iCalendar writer under them.

The produced text is parsed back (unfolded, unescaped, split into components)
instead of being compared with a golden string, so the checks are about what a
calendar application would read. Schedules are synthetic; names are made up.
"""

from __future__ import annotations

import re
import unittest
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

from ninova_mcp.calendar_export import (
    CALENDAR_EXPORT_TOOLS,
    find_term_dates,
    is_placeholder,
    parse_exam_date,
    parse_exam_time,
    parse_semester_label,
    session_slot,
)
from ninova_mcp.ics import (
    IcsEvent,
    build_calendar,
    escape_text,
    first_weekday_on_or_after,
    fold_line,
    stable_uid,
)
from ninova_mcp.obs_client import ObsError, ObsPublicClient
from ninova_mcp.parsing import extract_academic_calendar
from ninova_mcp.public_client import ItuPublicError
from ninova_mcp.server import (
    DESTRUCTIVE_TOOL_NAMES,
    LOCAL_TOOL_NAMES,
    REMOTE_TOOL_NAMES,
    STATEFUL_TOOL_NAMES,
    NinovaMcpApp,
)

FIXTURES = Path(__file__).parent / "fixtures"
SEMESTER = "2026-2027 Güz Dönemi"
# 2026-09-28 is a Monday.
TERM_START, TERM_END = "2026-09-28", "2027-01-08"


def course(crn: str, code: str, name: str, sessions: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {
        "crn": crn, "code": code, "name": name, "method": "Fiziksel (Yüz yüze)",
        "instructor": "Çağrı Örnekçioğlu", "sessions": sessions, **extra,
    }


def session(day: str | None, clock: str | None, building: str | None = "AAA", room: str | None = "Z-1") -> dict[str, Any]:
    return {"building": building, "day": day, "time": clock, "room": room}


SCHEDULES: dict[str, list[dict[str, Any]]] = {
    "TST": [
        course("10001", "TST 101E", "Sample Course", [
            session("Salı", "08:30/10:29"),
            session("Perşembe", "14:30/16:29", "AAA", "104"),
        ]),
        course("10002", "TST 202E", "Işık, Ses; Görüntü", [session("Çarşamba", "09:30/12:29", "BBB", "2")]),
        course("10003", "TST 499", "Bitirme Çalışması", [session("-", "-/-", "--", "--")]),
        course("10004", "TST 303", "Karma", [
            session("Pazartesi", "13:30/15:29"),
            session("--", "--"),
            session("Cuma", "-/-"),
        ]),
        course("10005", "TST 404", "Haftanın Günleri", [
            session("Pazartesi", "08:30/09:29"), session("Salı", "08:30/09:29"),
            session("Çarşamba", "08:30/09:29"), session("Perşembe", "08:30/09:29"),
            session("Cuma", "08:30/09:29"), session("Cumartesi", "08:30/09:29"),
            session("Pazar", "08:30/09:29"),
        ]),
    ],
    "ABC": [
        course("20001", "ABC 301", "Sayısal Çözümleme", [session("Cuma", "13:30/16:29", "MED", "--")]),
    ],
}

EXAMS: dict[str, dict[str, Any]] = {
    "TST": {
        "url": "https://obs.itu.edu.tr/public/FinalTakvimi/SearchFinalTakvimiByDersBransKodu?DersBransKoduId=1",
        "published": True,
        "exams": [
            {"crn": "10001", "course_code": "TST 101E", "course_name": "Sample Course",
             "date": "11.01.2027", "time": "10:00-12:00", "building": "AAA", "room": "Z-1"},
            {"crn": "10002", "course_code": "TST 202E", "course_name": "Işık, Ses; Görüntü",
             "date": "12.01.2027", "time": "13:30", "building": "BBB", "room": "-"},
            {"crn": "10003", "course_code": "TST 499", "course_name": "Bitirme Çalışması",
             "date": "13.01.2027", "time": "-", "building": "--", "room": "--"},
            {"crn": "10004", "course_code": "TST 303", "course_name": "Karma",
             "date": None, "time": "09:00", "building": "AAA", "room": "1"},
        ],
    },
    "ABC": {"url": "https://obs.itu.edu.tr/public/FinalTakvimi/x", "published": False, "exams": []},
}


class FakeObsPublic:
    """Stands in for ObsPublicClient; counts calls so 'no request' is checkable."""

    def __init__(self, calendar: dict[str, Any] | None = None, semester: str | None = SEMESTER) -> None:
        self.calendar = calendar if calendar is not None else {"events": []}
        self.semester = semester
        self.schedule_calls: list[tuple[str, str]] = []
        self.calendar_calls = 0

    _normalize_program_type = ObsPublicClient._normalize_program_type
    PROGRAM_TYPE_MAP = ObsPublicClient.PROGRAM_TYPE_MAP

    def get_course_schedule(self, program_type: str, department: str) -> dict[str, Any]:
        self.schedule_calls.append((program_type, department))
        if department not in SCHEDULES:
            raise ObsError(f"Bölüm kodu bulunamadı: {department!r}.")
        return {"courses": SCHEDULES[department], "semester": self.semester, "department_code": department}

    def get_academic_calendar(self) -> dict[str, Any]:
        self.calendar_calls += 1
        return self.calendar


class FakeItuPublic:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_final_exam_schedule(self, department_code: str) -> dict[str, Any]:
        self.calls.append(department_code)
        if department_code not in EXAMS:
            raise ItuPublicError(f"Geçersiz bölüm kodu: {department_code}.")
        return EXAMS[department_code]


def make_app(**obs_kwargs: Any) -> tuple[NinovaMcpApp, FakeObsPublic, FakeItuPublic]:
    app = NinovaMcpApp()
    obs, public = FakeObsPublic(**obs_kwargs), FakeItuPublic()
    app._obs_public = obs  # type: ignore[assignment]
    app._itu_public = public  # type: ignore[assignment]
    return app, obs, public


# -- a minimal reader for what the writer produces -----------------------


def unfold(text: str) -> list[str]:
    assert text.endswith("\r\n")
    assert "\n" not in text.replace("\r\n", ""), "bare LF in output"
    assert "\r" not in text.replace("\r\n", ""), "bare CR in output"
    return text.replace("\r\n ", "").split("\r\n")[:-1]


def unescape(value: str) -> str:
    return re.sub(
        r"\\(.)", lambda m: "\n" if m[1] in "nN" else m[1], value
    )


def parse_ics(text: str) -> dict[str, Any]:
    """Return ``{"calendar": props, "timezone": props, "events": [props...]}``."""
    stack: list[tuple[str, dict[str, str]]] = []
    parsed: dict[str, Any] = {"events": []}
    for line in unfold(text):
        name, _, value = line.partition(":")
        if name == "BEGIN":
            stack.append((value, {}))
        elif name == "END":
            kind, props = stack.pop()
            assert kind == value, (kind, value)
            if kind == "VEVENT":
                parsed["events"].append(props)
            elif kind == "VTIMEZONE":
                parsed["timezone"] = props
            elif kind == "STANDARD":
                parsed["standard"] = props
            elif kind == "VCALENDAR":
                parsed["calendar"] = props
        else:
            assert name not in stack[-1][1], f"duplicate property {name}"
            stack[-1][1][name] = value
    assert not stack
    return parsed


class SerializerTests(unittest.TestCase):
    def event(self, **overrides: Any) -> IcsEvent:
        base: dict[str, Any] = {
            "uid": "abc@itu-mcp", "summary": "Deneme",
            "start": datetime(2026, 9, 29, 8, 30), "end": datetime(2026, 9, 29, 10, 29),
        }
        base.update(overrides)
        return IcsEvent(**base)

    def test_structure_and_crlf(self) -> None:
        text = build_calendar([self.event()], name="Takvim", now=datetime(2026, 10, 1, 12, 0, tzinfo=UTC))
        lines = unfold(text)
        self.assertEqual(lines[0], "BEGIN:VCALENDAR")
        self.assertEqual(lines[-1], "END:VCALENDAR")
        parsed = parse_ics(text)
        self.assertEqual(parsed["calendar"]["VERSION"], "2.0")
        self.assertTrue(parsed["calendar"]["PRODID"])
        self.assertEqual(len(parsed["events"]), 1)
        event = parsed["events"][0]
        self.assertEqual(event["UID"], "abc@itu-mcp")
        self.assertEqual(event["DTSTAMP"], "20261001T120000Z")
        self.assertEqual(event["DTSTART;TZID=Europe/Istanbul"], "20260929T083000")
        self.assertEqual(event["DTEND;TZID=Europe/Istanbul"], "20260929T102900")
        self.assertNotIn("RRULE", event)

    def test_timezone_block_is_fixed_utc_plus_three(self) -> None:
        parsed = parse_ics(build_calendar([self.event()], name="Takvim"))
        self.assertEqual(parsed["timezone"]["TZID"], "Europe/Istanbul")
        self.assertEqual(parsed["standard"]["TZOFFSETFROM"], "+0300")
        self.assertEqual(parsed["standard"]["TZOFFSETTO"], "+0300")
        self.assertNotIn("DAYLIGHT", build_calendar([self.event()], name="Takvim"))
        # The VTIMEZONE comes before the first event that refers to it.
        text = build_calendar([self.event()], name="Takvim")
        self.assertLess(text.index("BEGIN:VTIMEZONE"), text.index("BEGIN:VEVENT"))

    def test_writer_does_not_use_the_system_zone_database(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "src" / "ninova_mcp" / "ics.py").read_text(encoding="utf-8")
        self.assertNotIn("zoneinfo", source)

    def test_weekly_rule_until_is_utc_end_of_the_last_local_day(self) -> None:
        event = self.event(weekly_byday="TU", weekly_until=date(2027, 1, 8))
        parsed = parse_ics(build_calendar([event], name="Takvim"))
        # 23:59:59 on 8 January in Istanbul is 20:59:59 UTC the same day.
        self.assertEqual(parsed["events"][0]["RRULE"], "FREQ=WEEKLY;UNTIL=20270108T205959Z;BYDAY=TU")

    def test_event_without_end_has_no_dtend(self) -> None:
        parsed = parse_ics(build_calendar([self.event(end=None)], name="Takvim"))
        self.assertFalse([key for key in parsed["events"][0] if key.startswith("DTEND")])

    def test_escaping_round_trips(self) -> None:
        raw = "Işık, Ses; Görüntü\\Deney\nİkinci satır\r\nÜçüncü"
        self.assertEqual(escape_text("a,b;c\\d\ne"), "a\\,b\\;c\\\\d\\ne")
        parsed = parse_ics(build_calendar([self.event(summary=raw, description=raw, location=raw)], name="A, B"))
        expected = raw.replace("\r\n", "\n")
        event = parsed["events"][0]
        self.assertEqual(unescape(event["SUMMARY"]), expected)
        self.assertEqual(unescape(event["DESCRIPTION"]), expected)
        self.assertEqual(unescape(event["LOCATION"]), expected)
        self.assertEqual(unescape(parsed["calendar"]["X-WR-CALNAME"]), "A, B")

    def test_control_characters_cannot_inject_a_line(self) -> None:
        text = build_calendar([self.event(summary="x\r\nEND:VEVENT\x00\x1b")], name="Takvim")
        self.assertEqual(text.count("END:VEVENT"), 2)  # the real one and the escaped text
        self.assertEqual(len(parse_ics(text)["events"]), 1)
        self.assertNotIn("\x00", text)

    def test_folding_at_75_octets_keeps_multibyte_characters_whole(self) -> None:
        long_text = "Çağdaş Öğretim Üyesi şöyle dedi: ığüşöç " * 12
        text = build_calendar([self.event(description=long_text)], name="Takvim")
        physical = text.split("\r\n")[:-1]
        self.assertTrue(any(line.startswith(" ") for line in physical))
        for line in physical:
            self.assertLessEqual(len(line.encode("utf-8")), 75)
        # Every physical line is valid UTF-8 on its own, and unfolding restores the text.
        for line in text.encode("utf-8").split(b"\r\n"):
            line.decode("utf-8")
        self.assertEqual(unescape(parse_ics(text)["events"][0]["DESCRIPTION"]), long_text)

    def test_fold_line_boundaries(self) -> None:
        self.assertEqual(fold_line("x" * 75), ["x" * 75])
        self.assertEqual(fold_line("x" * 76), ["x" * 75, " x"])
        self.assertEqual(fold_line("x" * 74 + "ş"), ["x" * 74, " ş"])
        self.assertEqual(fold_line(""), [""])

    def test_turkish_characters_are_utf8_not_escaped(self) -> None:
        text = build_calendar([self.event(summary="Çağrı Şöğüı İ")], name="Takvim")
        self.assertIn("SUMMARY:Çağrı Şöğüı İ", text)
        self.assertEqual(text.encode("utf-8").decode("utf-8"), text)

    def test_stable_uid(self) -> None:
        self.assertEqual(stable_uid("class", "t", "10001", 1), stable_uid("class", "t", "10001", 1))
        self.assertNotEqual(stable_uid("class", "t", "10001", 1), stable_uid("class", "t", "10001", 2))
        self.assertRegex(stable_uid("a"), r"^[0-9a-f]{32}@itu-mcp$")

    def test_first_weekday_on_or_after(self) -> None:
        monday = date(2026, 9, 28)
        self.assertEqual(first_weekday_on_or_after(monday, 0), monday)
        self.assertEqual(first_weekday_on_or_after(monday, 6), date(2026, 10, 4))
        self.assertEqual(first_weekday_on_or_after(date(2026, 9, 30), 0), date(2026, 10, 5))


class ParsingHelperTests(unittest.TestCase):
    def test_placeholders(self) -> None:
        for value in (None, "", " ", "-", "--", "---", "-/-", "--/--", " - "):
            self.assertTrue(is_placeholder(value), value)
        for value in ("Salı", "08:30/10:29", "Z-1", "0"):
            self.assertFalse(is_placeholder(value), value)

    def test_turkish_and_english_day_names_map_to_weekdays(self) -> None:
        cases = {
            "Pazartesi": 0, "Salı": 1, "SALI": 1, "sali": 1, "Çarşamba": 2, "carsamba": 2,
            "Perşembe": 3, "Cuma": 4, "Cumartesi": 5, "Pazar": 6, "Tuesday": 1, " Cuma ": 4,
        }
        for day, weekday in cases.items():
            with self.subTest(day=day):
                slot = session_slot({"day": day, "time": "08:30/10:29"})
                self.assertEqual(slot, (weekday, time(8, 30), time(10, 29)))

    def test_unknown_day_or_time_gives_a_reason_never_a_time(self) -> None:
        self.assertEqual(session_slot({"day": "-", "time": "08:30/10:29"}), "unknown_day")
        self.assertEqual(session_slot({"day": None, "time": "08:30/10:29"}), "unknown_day")
        self.assertEqual(session_slot({"day": "Funday", "time": "08:30/10:29"}), "unknown_day")
        for clock in ("-/-", "--", "-", "", None, "08:30", "08:30/", "0830/1029", "25:00/26:00",
                      "08:61/09:00", "10:00/09:00", "09:00/09:00", "08:30/10:29/11:00"):
            with self.subTest(clock=clock):
                self.assertEqual(session_slot({"day": "Salı", "time": clock}), "unknown_time")

    def test_semester_label(self) -> None:
        self.assertEqual(parse_semester_label("2026-2027 Güz Dönemi"), ("2026-2027", "guz"))
        self.assertEqual(parse_semester_label("2025-2026 Bahar Dönemi"), ("2025-2026", "bahar"))
        self.assertEqual(parse_semester_label("2025-2026 Yaz Dönemi"), ("2025-2026", "yaz"))
        self.assertIsNone(parse_semester_label("Bilinmeyen Dönem"))
        self.assertIsNone(parse_semester_label(None))

    def test_exam_date_and_time(self) -> None:
        self.assertEqual(parse_exam_date("20.07.2026"), date(2026, 7, 20))
        self.assertEqual(parse_exam_date("20/07/2026 Pazartesi"), date(2026, 7, 20))
        self.assertEqual(parse_exam_date("2026-07-20"), date(2026, 7, 20))
        self.assertEqual(parse_exam_date("10 Ağustos 2026"), date(2026, 8, 10))
        for bad in (None, "", "-", "31.02.2026", "20.07.2026 - 21.07.2026", "Temmuz", "20.07"):
            with self.subTest(bad=bad):
                self.assertIsNone(parse_exam_date(bad))
        self.assertEqual(parse_exam_time("10:00"), (time(10, 0), None))
        self.assertEqual(parse_exam_time("09:00-11:00"), (time(9, 0), time(11, 0)))
        self.assertEqual(parse_exam_time("09:00 / 11:00"), (time(9, 0), time(11, 0)))
        for bad in (None, "", "-", "--", "25:00", "11:00-09:00", "9", "09:00-10:00-11:00"):
            with self.subTest(bad=bad):
                self.assertIsNone(parse_exam_time(bad))


class TermDateTests(unittest.TestCase):
    def calendar(self, *events: tuple[str, str, str, str]) -> dict[str, Any]:
        return {"events": [
            {"description": description, "start_date": day, "end_date": day,
             "scope": scope, "academic_year": year}
            for description, day, scope, year in events
        ]}

    def test_real_calendar_page_states_the_start_but_not_the_end(self) -> None:
        html = (FIXTURES / "academic_calendar_month_public.html").read_text(encoding="utf-8")
        calendar = extract_academic_calendar(html, "https://www.takvim.sis.itu.edu.tr/x")
        self.assertEqual(
            find_term_dates(calendar, SEMESTER, "LS"),
            {"term_start": "2026-09-28", "term_end": None},
        )
        # A different academic year or season on the same page is not accepted.
        empty = {"term_start": None, "term_end": None}
        self.assertEqual(find_term_dates(calendar, "2025-2026 Güz Dönemi", "LS"), empty)
        self.assertEqual(find_term_dates(calendar, "2026-2027 Bahar Dönemi", "LS"), empty)
        self.assertEqual(find_term_dates(calendar, None, "LS"), empty)

    def test_both_dates_scope_and_typo(self) -> None:
        calendar = self.calendar(
            ("Beginning of Spring Term", "2027-02-08", "undergraduate", "2026-2027"),
            ("End of Sping Term", "2027-05-21", "undergraduate", "2026-2027"),
            ("Beginning of Spring Term", "2027-02-15", "graduate", "2026-2027"),
            ("Beginning of Spring Term for English Preparatory Program", "2027-02-01", "undergraduate", "2026-2027"),
            ("End of Spring Term Exams", "2027-06-11", "undergraduate", "2026-2027"),
        )
        self.assertEqual(
            find_term_dates(calendar, "2026-2027 Bahar Dönemi", "LS"),
            {"term_start": "2027-02-08", "term_end": "2027-05-21"},
        )
        self.assertEqual(
            find_term_dates(calendar, "2026-2027 Bahar Dönemi", "LU"),
            {"term_start": "2027-02-15", "term_end": None},
        )

    def test_conflicting_dates_are_not_resolved_by_guessing(self) -> None:
        calendar = self.calendar(
            ("Beginning of Fall Term", "2026-09-28", "undergraduate", "2026-2027"),
            ("Beginning of Fall Term", "2026-10-05", "undergraduate", "2026-2027"),
        )
        self.assertIsNone(find_term_dates(calendar, SEMESTER, "LS")["term_start"])


class CourseScheduleExportTests(unittest.TestCase):
    def export(self, app: NinovaMcpApp, crns: list[str], **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("department_codes", ["TST"])
        kwargs.setdefault("term_start", TERM_START)
        kwargs.setdefault("term_end", TERM_END)
        return app.export_course_schedule_ics(crns, **kwargs)

    def test_weekly_events_with_turkish_weekday_mapping(self) -> None:
        app, obs, _ = make_app()
        result = self.export(app, ["10005"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["event_count"], 7)
        self.assertEqual(obs.calendar_calls, 0)  # both dates were supplied
        events = parse_ics(result["ics"])["events"]
        seen = {}
        for event in events:
            start = datetime.strptime(event["DTSTART;TZID=Europe/Istanbul"], "%Y%m%dT%H%M%S")
            byday = re.search(r"BYDAY=(\w\w)$", event["RRULE"])[1]
            seen[byday] = start
            # The first occurrence falls on the weekday the rule repeats on.
            self.assertEqual(["MO", "TU", "WE", "TH", "FR", "SA", "SU"][start.weekday()], byday)
            self.assertIn("UNTIL=20270108T205959Z", event["RRULE"])
            self.assertEqual(start.time(), time(8, 30))
        self.assertEqual(seen["MO"].date(), date(2026, 9, 28))
        self.assertEqual(seen["TU"].date(), date(2026, 9, 29))
        self.assertEqual(seen["SU"].date(), date(2026, 10, 4))
        self.assertEqual(sorted(seen), ["FR", "MO", "SA", "SU", "TH", "TU", "WE"])

    def test_event_fields(self) -> None:
        app, _, _ = make_app()
        result = self.export(app, ["10001", "10002"])
        self.assertEqual(result["event_count"], 3)
        self.assertEqual(result["mime_type"], "text/calendar")
        self.assertEqual(result["suggested_filename"], "itu-ders-programi-2026-2027-guz.ics")
        self.assertEqual(result["timezone"], "Europe/Istanbul")
        self.assertIs(result["untrusted_external_content"], True)
        self.assertIn("tatil", result["note"])
        parsed = parse_ics(result["ics"])
        self.assertEqual(unescape(parsed["calendar"]["X-WR-CALNAME"]), "İTÜ Ders Programı 2026-2027 Güz Dönemi")
        by_summary = {unescape(e["SUMMARY"]): e for e in parsed["events"] if "TST 202E" in e["SUMMARY"]}
        event = by_summary["TST 202E Işık, Ses; Görüntü"]
        self.assertIn("\\,", event["SUMMARY"])
        self.assertIn("\\;", event["SUMMARY"])
        self.assertEqual(event["LOCATION"], "BBB 2")
        self.assertEqual(event["DTSTART;TZID=Europe/Istanbul"], "20260930T093000")
        self.assertEqual(event["DTEND;TZID=Europe/Istanbul"], "20260930T122900")
        description = unescape(event["DESCRIPTION"])
        self.assertIn("CRN: 10002", description)
        self.assertIn("Öğretim üyesi: Çağrı Örnekçioğlu", description)
        self.assertIn("Dönem: 2026-2027 Güz Dönemi", description)

    def test_uids_are_stable_across_calls_and_unique_within_one(self) -> None:
        app, _, _ = make_app()
        first = parse_ics(self.export(app, ["10001", "10002", "10005"])["ics"])["events"]
        second = parse_ics(self.export(app, ["10005", "10002", "10001"])["ics"])["events"]
        uids = [event["UID"] for event in first]
        self.assertEqual(len(uids), len(set(uids)))
        self.assertEqual(sorted(uids), sorted(event["UID"] for event in second))
        # The UID does not depend on the room or on the supplied date range.
        moved = parse_ics(self.export(app, ["10002"], term_start="2026-10-05")["ics"])["events"][0]
        self.assertIn(moved["UID"], uids)

    def test_same_slot_twice_gets_distinct_uids(self) -> None:
        app, _, _ = make_app()
        SCHEDULES["TST"].append(course("10009", "TST 505", "Çift Derslik", [
            session("Salı", "08:30/10:29", "AAA", "1"), session("Salı", "08:30/10:29", "AAA", "2"),
        ]))
        try:
            events = parse_ics(self.export(app, ["10009"])["ics"])["events"]
        finally:
            SCHEDULES["TST"].pop()
        self.assertEqual(len({event["UID"] for event in events}), 2)

    def test_unknown_times_are_skipped_and_listed(self) -> None:
        app, _, _ = make_app()
        result = self.export(app, ["10003", "10004"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["event_count"], 1)
        events = parse_ics(result["ics"])["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["DTSTART;TZID=Europe/Istanbul"], "20260928T133000")
        self.assertNotIn("T000000", result["ics"].split("END:VTIMEZONE")[1])
        reasons = sorted((item["crn"], item["reason"]) for item in result["skipped"])
        self.assertEqual(reasons, [("10003", "unknown_day"), ("10004", "unknown_day"), ("10004", "unknown_time")])
        self.assertIn("3 kayıt dışarıda kaldı", result["summary"])

    def test_only_placeholders_gives_no_calendar(self) -> None:
        app, _, _ = make_app()
        result = self.export(app, ["10003"])
        self.assertEqual(result["status"], "no_events")
        self.assertIsNone(result["ics"])
        self.assertEqual(result["event_count"], 0)
        self.assertEqual(result["skipped"][0]["reason"], "unknown_day")

    def test_missing_crns_are_reported(self) -> None:
        app, _, _ = make_app()
        result = self.export(app, ["10001", "20001", "99999"])
        self.assertEqual(result["missing_crns"], ["20001", "99999"])
        self.assertEqual(result["event_count"], 2)
        both = self.export(app, ["10001", "20001"], department_codes=["tst", "ABC"])
        self.assertEqual(both["missing_crns"], [])
        self.assertEqual(both["department_codes"], ["TST", "ABC"])
        location = [e.get("LOCATION") for e in parse_ics(both["ics"])["events"] if "ABC 301" in e["SUMMARY"]]
        self.assertEqual(location, ["MED"])  # the "--" room is dropped, not printed

    def test_meeting_day_after_term_end_is_skipped(self) -> None:
        app, _, _ = make_app()
        result = self.export(app, ["10001"], term_start="2026-09-28", term_end="2026-09-30")
        self.assertEqual(result["event_count"], 1)  # Tuesday fits, Thursday does not
        self.assertEqual(result["skipped"][0]["reason"], "no_occurrence_in_term")

    def test_dates_are_never_invented(self) -> None:
        app, obs, _ = make_app()
        result = app.export_course_schedule_ics(["10001"], ["TST"])
        self.assertEqual(obs.calendar_calls, 1)
        self.assertEqual(result["status"], "term_dates_required")
        self.assertIsNone(result["ics"])
        self.assertEqual(result["event_count"], 0)
        self.assertEqual(result["missing_parameters"], ["term_start", "term_end"])
        self.assertIn("term_start", result["summary"])

    def test_start_from_calendar_end_still_required(self) -> None:
        html = (FIXTURES / "academic_calendar_month_public.html").read_text(encoding="utf-8")
        calendar = extract_academic_calendar(html, "https://www.takvim.sis.itu.edu.tr/x")
        app, _, _ = make_app(calendar=calendar)
        result = app.export_course_schedule_ics(["10001"], ["TST"])
        self.assertEqual(result["status"], "term_dates_required")
        self.assertEqual(result["missing_parameters"], ["term_end"])
        self.assertEqual(result["term_start"], "2026-09-28")
        self.assertEqual(result["term_date_sources"]["term_start"], "academic_calendar")

        complete = app.export_course_schedule_ics(["10001"], ["TST"], term_end=TERM_END)
        self.assertEqual(complete["status"], "ok")
        self.assertEqual(
            complete["term_date_sources"], {"term_start": "academic_calendar", "term_end": "parameter"}
        )
        first = parse_ics(complete["ics"])["events"][0]
        self.assertEqual(first["DTSTART;TZID=Europe/Istanbul"], "20260929T083000")

    def test_unreadable_calendar_is_reported_not_fatal(self) -> None:
        app, obs, _ = make_app()

        def broken() -> dict[str, Any]:
            raise ObsError("Academic calendar returned HTTP 503")

        obs.get_academic_calendar = broken  # type: ignore[method-assign]
        result = app.export_course_schedule_ics(["10001"], ["TST"])
        self.assertEqual(result["status"], "term_dates_required")
        self.assertIn("HTTP 503", result["calendar_note"])

    def test_input_validation_happens_before_any_request(self) -> None:
        app, obs, _ = make_app()
        bad_calls: list[dict[str, Any]] = [
            {"crns": [], "department_codes": ["TST"]},
            {"crns": ["abc"], "department_codes": ["TST"]},
            {"crns": ["123"], "department_codes": ["TST"]},
            {"crns": ["10001; DROP"], "department_codes": ["TST"]},
            {"crns": "10001", "department_codes": ["TST"]},
            {"crns": [str(10000 + n) for n in range(31)], "department_codes": ["TST"]},
            {"crns": ["10001"], "department_codes": []},
            {"crns": ["10001"], "department_codes": ["../x"]},
            {"crns": ["10001"], "department_codes": ["T" * 9]},
            {"crns": ["10001"], "department_codes": [f"A{n:02d}" for n in range(26)]},
            {"crns": ["10001"], "department_codes": ["TST"], "program_type": "XX"},
            {"crns": ["10001"], "department_codes": ["TST"], "term_start": "28.09.2026"},
            {"crns": ["10001"], "department_codes": ["TST"], "term_end": "soon"},
        ]
        for kwargs in bad_calls:
            with self.subTest(kwargs=kwargs), self.assertRaises(ObsError):
                app.export_course_schedule_ics(**kwargs)
        self.assertEqual(obs.schedule_calls, [])
        self.assertEqual(obs.calendar_calls, 0)

    def test_date_range_is_checked(self) -> None:
        app, _, _ = make_app()
        with self.assertRaises(ObsError):
            self.export(app, ["10001"], term_start="2027-01-08", term_end="2026-09-28")
        with self.assertRaises(ObsError):
            self.export(app, ["10001"], term_start="2026-01-01", term_end="2027-01-01")

    def test_duplicate_crns_are_exported_once(self) -> None:
        app, _, _ = make_app()
        self.assertEqual(self.export(app, ["10002", " 10002 ", "10002"])["event_count"], 1)

    def test_program_type_alias_is_normalised(self) -> None:
        app, obs, _ = make_app()
        self.assertEqual(self.export(app, ["10002"], program_type="Lisans")["program_type"], "LS")
        self.assertEqual(obs.schedule_calls, [("LS", "TST")])


class ExamScheduleExportTests(unittest.TestCase):
    def test_single_dated_events(self) -> None:
        app, _, public = make_app()
        result = app.export_exam_schedule_ics(["tst101e", "TST 202E"])
        self.assertEqual(public.calls, ["TST"])  # one request per branch
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["event_count"], 2)
        self.assertEqual(result["suggested_filename"], "itu-final-sinavlari.ics")
        events = {unescape(e["SUMMARY"]): e for e in parse_ics(result["ics"])["events"]}
        first = events["Final: TST 101E Sample Course"]
        self.assertEqual(first["DTSTART;TZID=Europe/Istanbul"], "20270111T100000")
        self.assertEqual(first["DTEND;TZID=Europe/Istanbul"], "20270111T120000")
        self.assertEqual(first["LOCATION"], "AAA Z-1")
        self.assertNotIn("RRULE", first)
        second = events["Final: TST 202E Işık, Ses; Görüntü"]
        self.assertEqual(second["DTSTART;TZID=Europe/Istanbul"], "20270112T133000")
        self.assertFalse([key for key in second if key.startswith("DTEND")])
        self.assertEqual(second["LOCATION"], "BBB")
        self.assertEqual(result["end_time_unknown"], ["TST 202E (10002)"])
        self.assertIn("uydurulmadı", result["note"])

    def test_unknown_date_or_time_is_skipped(self) -> None:
        app, _, _ = make_app()
        result = app.export_exam_schedule_ics(["TST 499", "TST 303", "TST 777"])
        self.assertEqual(result["status"], "no_events")
        self.assertIsNone(result["ics"])
        reasons = {item["code"]: item["reason"] for item in result["skipped"]}
        self.assertEqual(reasons, {"TST 499": "unknown_time", "TST 303": "unknown_date"})
        self.assertEqual(result["not_found"], ["TST 777"])

    def test_unpublished_and_unknown_branches(self) -> None:
        app, _, _ = make_app()
        result = app.export_exam_schedule_ics(["ABC 301", "QQQ 101", "TST 101E"])
        reasons = {item["code"]: item["reason"] for item in result["skipped"]}
        self.assertEqual(reasons["ABC 301"], "exam_schedule_not_published")
        self.assertEqual(reasons["QQQ 101"], "exam_schedule_unavailable")
        self.assertEqual(result["event_count"], 1)

    def test_uids_stable(self) -> None:
        app, _, _ = make_app()
        one = parse_ics(app.export_exam_schedule_ics(["TST 101E", "TST 202E"])["ics"])["events"]
        two = parse_ics(app.export_exam_schedule_ics(["TST 202E", "TST 101E", "tst 101e"])["ics"])["events"]
        self.assertEqual(sorted(e["UID"] for e in one), sorted(e["UID"] for e in two))
        self.assertEqual(len({e["UID"] for e in one}), 2)

    def test_input_validation_happens_before_any_request(self) -> None:
        app, _, public = make_app()
        for bad in ([], ["BLG"], ["../../x"], "BLG 223E", ["TST 101E", "nope"],
                    [f"TST {100 + n}" for n in range(31)]):
            with self.subTest(bad=bad), self.assertRaises(ItuPublicError):
                app.export_exam_schedule_ics(bad)  # type: ignore[arg-type]
        self.assertEqual(public.calls, [])


class RegistrationTests(unittest.TestCase):
    NAMES = ["export_course_schedule_ics", "export_exam_schedule_ics"]

    def test_tools_are_registered_on_both_transports_as_read_only(self) -> None:
        self.assertEqual([tool["name"] for tool in CALENDAR_EXPORT_TOOLS], self.NAMES)
        for name in self.NAMES:
            self.assertIn(name, LOCAL_TOOL_NAMES)
            # Text only, no filesystem access, so nothing to exclude remotely.
            self.assertIn(name, REMOTE_TOOL_NAMES)
            self.assertNotIn(name, STATEFUL_TOOL_NAMES)
            self.assertNotIn(name, DESTRUCTIVE_TOOL_NAMES)

    def test_tools_take_no_path_parameter(self) -> None:
        for tool in CALENDAR_EXPORT_TOOLS:
            for parameter in tool["inputSchema"]["properties"]:
                self.assertNotIn("dir", parameter)
                self.assertNotIn("path", parameter)
                self.assertNotIn("file", parameter)

    def test_result_passes_through_the_registered_tool(self) -> None:
        import asyncio
        import json

        from ninova_mcp.server import build_stdio_server

        app, _, _ = make_app()
        server = build_stdio_server(app)
        raw = asyncio.run(server.call_tool("export_course_schedule_ics", {
            "crns": ["10002"], "department_codes": ["TST"],
            "term_start": TERM_START, "term_end": TERM_END,
        }))
        blocks = raw[0] if isinstance(raw, tuple) else raw
        payload = json.loads(blocks[0].text)
        self.assertEqual(payload["status"], "ok")
        self.assertIs(payload["untrusted_external_content"], True)
        # CRLF survives the JSON round trip.
        self.assertIn("\r\nBEGIN:VEVENT\r\n", payload["ics"])
        self.assertEqual(len(parse_ics(payload["ics"])["events"]), 1)


if __name__ == "__main__":
    unittest.main()
