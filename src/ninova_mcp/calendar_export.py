"""Export class meetings and final exams as iCalendar (.ics) text.

Two tools, both built on data the server already reads without credentials:

``export_course_schedule_ics`` turns the weekly meetings of the given CRNs
(public OBS course schedule) into weekly recurring events between the first
and last day of classes. ``export_exam_schedule_ics`` turns the public final
exam schedule rows of the given course codes into single dated events.

Nothing here invents a date or a time. A meeting whose day or time is a
placeholder is skipped and reported. The first and last day of classes come
from the caller, or from the academic calendar when it states them for the
term of the schedule; otherwise the tool returns
``status="term_dates_required"`` and no calendar. The tools return text only
and never touch the filesystem, so they are safe on the remote transport.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any, NamedTuple

from .ics import (
    BYDAY_CODES,
    TZID,
    IcsEvent,
    build_calendar,
    first_weekday_on_or_after,
    stable_uid,
)
from .obs_client import ObsError, ObsPublicClient
from .public_client import ItuPublicClient, ItuPublicError

MAX_CRNS = 30
MAX_DEPARTMENTS = 25
MAX_EXAM_COURSE_CODES = 30
MAX_TERM_DAYS = 200
ICS_MIME_TYPE = "text/calendar"

_CRN_PATTERN = re.compile(r"[0-9]{4,5}")
_DEPARTMENT_PATTERN = re.compile(r"[A-Z0-9]{2,5}")
_TIME_RANGE_PATTERN = re.compile(r"\d{1,2}:\d{2}\s*/\s*\d{1,2}:\d{2}")
_CLOCK_PATTERN = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?![\d:])")
_NUMERIC_DATE_PATTERN = re.compile(r"(?<!\d)(\d{1,2})[./](\d{1,2})[./](\d{4})(?!\d)")
_ISO_DATE_PATTERN = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_SEMESTER_PATTERN = re.compile(r"(\d{4})\s*-\s*(\d{4})\s+(guz|bahar|yaz)\b")
# OBS writes these where a value has not been assigned yet.
_PLACEHOLDER_PATTERN = re.compile(r"[\s\-/.:]*")

_SEASON_TERMS = {
    "guz": ("fall", r"fall"),
    # The official calendar has carried the misspelling "Sping".
    "bahar": ("spring", r"spr?ing"),
    "yaz": ("summer", r"summer"),
}
_SEASON_SLUGS = {"guz": "guz", "bahar": "bahar", "yaz": "yaz"}
_PROGRAM_SCOPES = {"LS": "undergraduate", "ÖL": "undergraduate", "LU": "graduate", "LUİ": "graduate"}

HOLIDAY_NOTE = (
    "Haftalık tekrar, derslerin ilk ve son günü arasındaki her haftayı kapsar. Resmî "
    "tatiller, ara tatil ve telafi dersleri hesaba katılmadı; o günleri takvimden elle silin."
)


def is_placeholder(value: Any) -> bool:
    """True for empty values and OBS filler such as ``-``, ``--`` or ``-/-``."""
    return value is None or _PLACEHOLDER_PATTERN.fullmatch(str(value)) is not None


def _clock(hour: int, minute: int) -> time | None:
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return time(hour, minute)
    return None


def session_slot(session: dict[str, Any]) -> tuple[int, time, time] | str:
    """Resolve a schedule session to ``(weekday, start, end)``.

    Returns a reason string instead when the day or the time is unknown.
    Day names go through the archive's Turkish/English day table and the
    time through ``schedule_utils.parse_time_range``, the same parsing the
    conflict checker uses, after a strict shape check: ``parse_time_range``
    reads ``-/-`` as midnight, which must never become an event.
    """
    from .archive import normalize_day
    from .schedule_utils import DAY_ORDER, parse_time_range

    raw_day, raw_time = session.get("day"), session.get("time")
    if is_placeholder(raw_day):
        return "unknown_day"
    try:
        weekday = DAY_ORDER.get(normalize_day(str(raw_day)))
    except ValueError:
        weekday = None
    if weekday is None:
        return "unknown_day"
    if is_placeholder(raw_time) or not _TIME_RANGE_PATTERN.fullmatch(str(raw_time).strip()):
        return "unknown_time"
    minutes = parse_time_range(str(raw_time))
    if minutes is None:
        return "unknown_time"
    start = _clock(*divmod(minutes[0], 60))
    end = _clock(*divmod(minutes[1], 60))
    if start is None or end is None or end <= start:
        return "unknown_time"
    return weekday, start, end


def _place(*parts: Any) -> str | None:
    known = [str(part).strip() for part in parts if not is_placeholder(part)]
    return " ".join(known) or None


def parse_semester_label(label: str | None) -> tuple[str, str] | None:
    """``"2026-2027 Güz Dönemi"`` -> ``("2026-2027", "guz")``."""
    from .parsing import normalize_lookup_text

    match = _SEMESTER_PATTERN.search(normalize_lookup_text(label or ""))
    if not match:
        return None
    return f"{match[1]}-{match[2]}", match[3]


def _stated_term_dates(
    calendar: dict[str, Any], academic_year: str, season: str, scope: str,
) -> dict[str, str | None]:
    """Class dates from the calendar's ``terms`` list, when it has exactly one such term."""
    stated = [
        term for term in calendar.get("terms") or []
        if isinstance(term, dict) and term.get("academic_year") == academic_year
        and term.get("term") == _SEASON_TERMS[season][0] and term.get("scope") == scope
    ]
    if len(stated) != 1:
        return {"term_start": None, "term_end": None}
    return {
        "term_start": stated[0].get("classes_start") or None,
        "term_end": stated[0].get("classes_end") or None,
    }


def _term_boundary_patterns(season: str) -> dict[str, re.Pattern[str]]:
    """The whole description of an event that states where a term begins or ends."""
    season_pattern = _SEASON_TERMS[season][1]
    unit = r"(?:term|semester|school)" if season == "yaz" else r"(?:term|semester)"
    return {
        "term_start": re.compile(rf"beginning of (?:the )?{season_pattern} {unit}"),
        "term_end": re.compile(rf"end of (?:the )?{season_pattern} {unit}"),
    }


def _event_term_dates(
    calendar: dict[str, Any], academic_year: str, season: str, scope: str,
) -> dict[str, set[str]]:
    """Every date the calendar's events give for the start and for the end of the term."""
    from .parsing import normalize_lookup_text

    patterns = _term_boundary_patterns(season)
    candidates: dict[str, set[str]] = {"term_start": set(), "term_end": set()}
    for event in calendar.get("events") or []:
        if event.get("scope") != scope or event.get("academic_year") != academic_year:
            continue
        description = normalize_lookup_text(event.get("description") or "").strip(" .")
        for key, pattern in patterns.items():
            if not pattern.fullmatch(description):
                continue
            value = event.get("start_date") if key == "term_start" else event.get("end_date")
            if value:
                candidates[key].add(str(value))
    return candidates


def find_term_dates(
    calendar: dict[str, Any],
    semester_label: str | None,
    program_type: str,
) -> dict[str, str | None]:
    """Look for the first and last day of classes in the fetched calendar.

    Only an event that states it outright is accepted: "Beginning of Fall
    Term" / "End of Fall Term" for the same academic year and the same level
    (undergraduate or graduate) as the schedule. The calendar's own ``terms``
    list is read first; the event scan below covers a calendar without one.
    A date the calendar does not state stays ``None`` and the caller has to
    supply it. Conflicting dates count as not found.
    """
    parsed = parse_semester_label(semester_label)
    scope = _PROGRAM_SCOPES.get(program_type)
    if parsed is None or scope is None:
        return {"term_start": None, "term_end": None}
    academic_year, season = parsed
    found = _stated_term_dates(calendar, academic_year, season, scope)
    if found["term_start"] and found["term_end"]:
        return found
    for key, values in _event_term_dates(calendar, academic_year, season, scope).items():
        if len(values) == 1 and not found[key]:
            found[key] = next(iter(values))
    return found


def parse_exam_date(raw: Any) -> date | None:
    """Read one explicit exam date; anything ambiguous is ``None``."""
    from .academic_calendar import parse_event_dates

    text = str(raw or "").strip()
    if not text:
        return None
    try:
        numeric = {date(int(y), int(m), int(d)) for d, m, y in _NUMERIC_DATE_PATTERN.findall(text)}
        iso = {date(int(y), int(m), int(d)) for y, m, d in _ISO_DATE_PATTERN.findall(text)}
    except ValueError:
        return None
    found = numeric | iso
    if len(found) == 1:
        return next(iter(found))
    if found:
        return None
    parsed = parse_event_dates(text)
    if parsed and parsed["start_date"] == parsed["end_date"]:
        return date.fromisoformat(parsed["start_date"])
    return None


def parse_exam_time(raw: Any) -> tuple[time, time | None] | None:
    """``"10:00"`` -> start only; ``"10:00-12:00"`` -> start and end."""
    if is_placeholder(raw):
        return None
    clocks = [_clock(int(h), int(m)) for h, m in _CLOCK_PATTERN.findall(str(raw))]
    if not clocks or len(clocks) > 2 or any(clock is None for clock in clocks):
        return None
    start = clocks[0]
    end = clocks[1] if len(clocks) == 2 else None
    if start is None or (end is not None and end <= start):
        return None
    return start, end


def _iso_date(value: str, name: str) -> date:
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise ObsError(f"{name} YYYY-AA-GG biçiminde bir tarih olmalı: {value!r}") from exc


def _clean_list(
    values: Any,
    name: str,
    maximum: int,
    error: type[Exception] = ObsError,
) -> list[str]:
    if isinstance(values, str) or not isinstance(values, (list, tuple)):
        raise error(f"{name} bir liste olmalı.")
    cleaned = list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
    if not cleaned:
        raise error(f"{name} boş olamaz.")
    if len(cleaned) > maximum:
        raise error(f"{name} en fazla {maximum} öğe alabilir.")
    return cleaned


def _valid_crns(crns: Any) -> list[str]:
    targets = _clean_list(crns, "crns", MAX_CRNS)
    invalid = [crn for crn in targets if not _CRN_PATTERN.fullmatch(crn)]
    if invalid:
        raise ObsError(f"Geçersiz CRN: {', '.join(invalid)}. CRN 4 veya 5 haneli bir sayıdır.")
    return targets


def _valid_department_codes(department_codes: Any) -> list[str]:
    departments = [code.upper() for code in _clean_list(department_codes, "department_codes", MAX_DEPARTMENTS)]
    departments = list(dict.fromkeys(departments))
    invalid = [code for code in departments if not _DEPARTMENT_PATTERN.fullmatch(code)]
    if invalid:
        raise ObsError(f"Geçersiz bölüm kodu: {', '.join(invalid)}. 'BLG' gibi olmalı.")
    return departments


def _sections_by_crn(
    obs_public: ObsPublicClient, level: str, departments: list[str],
) -> tuple[dict[str, dict[str, Any]], str | None]:
    """Every section of the scanned departments by CRN, and the semester they belong to.

    A CRN listed by two departments keeps the row of the first one scanned.
    """
    by_crn: dict[str, dict[str, Any]] = {}
    semester: str | None = None
    for code in departments:
        schedule = obs_public.get_course_schedule(level, code)
        semester = semester or schedule.get("semester")
        for course in schedule.get("courses") or []:
            by_crn.setdefault(str(course.get("crn") or "").strip(), course)
    return by_crn, semester


class _TermDates(NamedTuple):
    start: date | None
    end: date | None
    # Where each day came from: "parameter", "academic_calendar" or None.
    sources: dict[str, str | None]
    calendar_note: str | None


def _resolve_term_dates_without_inventing(
    obs_public: ObsPublicClient, given_start: date | None, given_end: date | None,
    semester: str | None, level: str,
) -> _TermDates:
    """First and last day of classes: the caller's, else what the academic calendar states.

    A date is never invented. The calendar is read only when the caller left
    a day out, and it fills that day only when it states it for this term
    (``find_term_dates``). A day nobody states stays ``None``, and the export
    then answers ``term_dates_required`` instead of a calendar. A calendar
    that cannot be read is reported in ``calendar_note`` and fills nothing.
    """
    start, end = given_start, given_end
    sources: dict[str, str | None] = {
        "term_start": "parameter" if start else None,
        "term_end": "parameter" if end else None,
    }
    if start is not None and end is not None:
        return _TermDates(start, end, sources, None)
    calendar_note = None
    try:
        found = find_term_dates(obs_public.get_academic_calendar(), semester, level)
    except ObsError as exc:
        found = {"term_start": None, "term_end": None}
        calendar_note = f"Akademik takvim okunamadı: {exc}"
    if start is None and found["term_start"]:
        start, sources["term_start"] = date.fromisoformat(found["term_start"]), "academic_calendar"
    if end is None and found["term_end"]:
        end, sources["term_end"] = date.fromisoformat(found["term_end"]), "academic_calendar"
    return _TermDates(start, end, sources, calendar_note)


def _term_dates_required(result: dict[str, Any], term: _TermDates) -> dict[str, Any]:
    """The answer when a day of the term is unknown: no calendar, and which day to ask for."""
    missing = [name for name, day in (("term_start", term.start), ("term_end", term.end)) if day is None]
    result.update({
        "status": "term_dates_required",
        "ics": None,
        "event_count": 0,
        "missing_parameters": missing,
        "summary": (
            f"Takvim üretilmedi: {' ve '.join(missing)} belirlenemedi. Akademik takvim "
            "bu dönemin ders başlangıç ya da bitiş gününü açıkça belirtmiyor. Tarih "
            "uydurulmadı; derslerin ilk ve son gününü term_start ve term_end "
            "(YYYY-AA-GG) olarak verin."
        ),
    })
    return result


def _check_term_span(start_day: date, end_day: date) -> None:
    if end_day < start_day:
        raise ObsError("term_end, term_start tarihinden önce olamaz.")
    if (end_day - start_day).days > MAX_TERM_DAYS:
        raise ObsError(f"term_start ile term_end arası en fazla {MAX_TERM_DAYS} gün olabilir.")


def _first_meeting(
    session: dict[str, Any], start_day: date, end_day: date,
) -> tuple[int, time, time, date] | str:
    """Weekday, hours and first date of a meeting, or the reason it cannot be placed."""
    slot = session_slot(session)
    if isinstance(slot, str):
        return slot
    weekday, start_clock, end_clock = slot
    first = first_weekday_on_or_after(start_day, weekday)
    if first > end_day:
        return "no_occurrence_in_term"
    return weekday, start_clock, end_clock, first


def _class_uid(seen_uids: dict[str, int], *key: object) -> str:
    """A stable UID; a repeat of the same key in one export gets its own."""
    uid = stable_uid("class", *key)
    # Two rooms at the same hour share every identifying field.
    repeat = seen_uids.get(uid, 0)
    seen_uids[uid] = repeat + 1
    return stable_uid("class", *key, repeat) if repeat else uid


def _class_details(crn: str, course: dict[str, Any], semester: str | None) -> str:
    details = [f"CRN: {crn}"]
    if not is_placeholder(course.get("instructor")):
        details.append(f"Öğretim üyesi: {course['instructor']}")
    if not is_placeholder(course.get("method")):
        details.append(f"Öğretim yöntemi: {course['method']}")
    if semester:
        details.append(f"Dönem: {semester}")
    return "\n".join(details)


@dataclass
class _ClassMeetings:
    """What the selected sections turned into, and what was left out and why."""

    events: list[IcsEvent] = field(default_factory=list)
    listed: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    missing_crns: list[str] = field(default_factory=list)
    seen_uids: dict[str, int] = field(default_factory=dict)


def _add_section_meetings(
    found: _ClassMeetings, crn: str, course: dict[str, Any],
    semester: str | None, start_day: date, end_day: date,
) -> None:
    """One weekly recurring event per meeting of a section that has a real day and time."""
    code = str(course.get("code") or "").strip()
    title = " ".join(part for part in (code, str(course.get("name") or "").strip()) if part)
    sessions = course.get("sessions") or []
    if not sessions:
        found.skipped.append({"crn": crn, "code": code, "reason": "no_sessions"})
    for session in sessions:
        meeting = _first_meeting(session, start_day, end_day)
        if isinstance(meeting, str):
            found.skipped.append({
                "crn": crn, "code": code, "reason": meeting,
                "day": session.get("day"), "time": session.get("time"),
            })
            continue
        weekday, start_clock, end_clock, first = meeting
        found.events.append(IcsEvent(
            uid=_class_uid(found.seen_uids, semester or start_day.isoformat(), crn, weekday,
                           start_clock.strftime("%H:%M"), end_clock.strftime("%H:%M")),
            summary=title or f"CRN {crn}",
            start=datetime.combine(first, start_clock),
            end=datetime.combine(first, end_clock),
            location=_place(session.get("building"), session.get("room")),
            description=_class_details(crn, course, semester),
            weekly_byday=BYDAY_CODES[weekday],
            weekly_until=end_day,
        ))
        found.listed.append({
            "crn": crn, "code": code, "day": session.get("day"),
            "time": session.get("time"), "first_date": first.isoformat(),
        })


def _collect_class_meetings(
    targets: list[str], by_crn: dict[str, dict[str, Any]],
    semester: str | None, start_day: date, end_day: date,
) -> _ClassMeetings:
    found = _ClassMeetings()
    for crn in targets:
        course = by_crn.get(crn)
        if course is None:
            found.missing_crns.append(crn)
            continue
        _add_section_meetings(found, crn, course, semester, start_day, end_day)
    return found


def _wanted_exam_codes(course_codes: Any) -> dict[str, list[str]]:
    """The requested course codes in canonical spelling, grouped by branch."""
    from .archive import split_course_code

    wanted: dict[str, list[str]] = {}
    for raw in _clean_list(course_codes, "course_codes", MAX_EXAM_COURSE_CODES, ItuPublicError):
        try:
            branch, number = split_course_code(raw)
        except ValueError as exc:
            raise ItuPublicError(str(exc)) from exc
        codes = wanted.setdefault(branch, [])
        if f"{branch} {number}" not in codes:
            codes.append(f"{branch} {number}")
    return wanted


@dataclass
class _Exams:
    """The exams found so far, and what was left out and why."""

    events: list[IcsEvent] = field(default_factory=list)
    listed: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    not_found: list[str] = field(default_factory=list)
    end_time_unknown: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    seen_uids: set[str] = field(default_factory=set)


def _published_exam_rows(
    itu_public: ItuPublicClient, branch: str, codes: list[str], found: _Exams,
) -> list[dict[str, Any]] | None:
    """The exam rows of a branch, or None (with the reason recorded) when there is no schedule.

    A schedule that is not published yet is not the same as having no exam.
    """
    try:
        schedule = itu_public.get_final_exam_schedule(branch)
    except ItuPublicError as exc:
        found.skipped.extend(
            {"code": code, "reason": "exam_schedule_unavailable", "detail": str(exc)}
            for code in codes
        )
        return None
    if schedule.get("url"):
        found.sources.append(str(schedule["url"]))
    if not schedule.get("published"):
        found.skipped.extend({"code": code, "reason": "exam_schedule_not_published"} for code in codes)
        return None
    return schedule.get("exams") or []


def _exam_event(
    uid: str, code: str, crn: str, row: dict[str, Any],
    day: date, start_clock: time, end_clock: time | None,
) -> IcsEvent:
    """A single dated event; without a published end time it has a start only."""
    name = str(row.get("course_name") or "").strip()
    details = [f"CRN: {crn}"] if crn else []
    if end_clock is None:
        details.append("Bitiş saati kaynakta yok.")
    return IcsEvent(
        uid=uid,
        summary=" ".join(part for part in ("Final:", code, name) if part),
        start=datetime.combine(day, start_clock),
        end=datetime.combine(day, end_clock) if end_clock else None,
        location=_place(row.get("building"), row.get("room")),
        description="\n".join(details) or None,
    )


def _add_exam(found: _Exams, code: str, row: dict[str, Any]) -> None:
    """One event for an exam row whose date and start time are both readable."""
    crn = str(row.get("crn") or "").strip()
    day = parse_exam_date(row.get("date"))
    clocks = parse_exam_time(row.get("time"))
    if day is None or clocks is None:
        found.skipped.append({
            "code": code, "crn": crn or None,
            "reason": "unknown_date" if day is None else "unknown_time",
            "date": row.get("date"), "time": row.get("time"),
        })
        return
    start_clock, end_clock = clocks
    uid = stable_uid("exam", crn, code, day.isoformat(), start_clock.strftime("%H:%M"))
    if uid in found.seen_uids:
        return
    found.seen_uids.add(uid)
    if end_clock is None:
        found.end_time_unknown.append(f"{code} ({crn})" if crn else code)
    found.events.append(_exam_event(uid, code, crn, row, day, start_clock, end_clock))
    found.listed.append({
        "code": code, "crn": crn or None, "date": day.isoformat(),
        "time": row.get("time"),
    })


def _add_exams_of_course(found: _Exams, code: str, exam_rows: list[dict[str, Any]]) -> None:
    key = code.replace(" ", "")
    rows = [
        row for row in exam_rows
        if str(row.get("course_code") or "").replace(" ", "").upper() == key
    ]
    if not rows:
        found.not_found.append(code)
    for row in rows:
        _add_exam(found, code, row)


class CalendarExportToolsMixin:
    """Tool methods mixed into ``NinovaMcpApp``, which supplies the clients."""

    obs_public: ObsPublicClient
    itu_public: ItuPublicClient

    def export_course_schedule_ics(
        self,
        crns: list[str | int],
        department_codes: list[str],
        program_type: str = "LS",
        term_start: str | None = None,
        term_end: str | None = None,
    ) -> dict[str, Any]:
        """Weekly class meetings of the given CRNs as an iCalendar document."""
        # Everything is validated before the first request goes out.
        targets = _valid_crns(crns)
        departments = _valid_department_codes(department_codes)
        level = self.obs_public._normalize_program_type(program_type)
        given_start = _iso_date(term_start, "term_start") if term_start else None
        given_end = _iso_date(term_end, "term_end") if term_end else None

        by_crn, semester = _sections_by_crn(self.obs_public, level, departments)
        term = _resolve_term_dates_without_inventing(self.obs_public, given_start, given_end, semester, level)
        result: dict[str, Any] = {
            "kind": "course_schedule",
            "semester": semester,
            "program_type": level,
            "department_codes": departments,
            "term_start": term.start.isoformat() if term.start else None,
            "term_end": term.end.isoformat() if term.end else None,
            "term_date_sources": term.sources,
            "timezone": TZID,
            "source": "obs.itu.edu.tr/public/DersProgram",
            "untrusted_external_content": True,
        }
        if term.calendar_note:
            result["calendar_note"] = term.calendar_note
        if term.start is None or term.end is None:
            return _term_dates_required(result, term)
        _check_term_span(term.start, term.end)

        meetings = _collect_class_meetings(targets, by_crn, semester, term.start, term.end)
        result.update({
            "event_count": len(meetings.events),
            "events": meetings.listed,
            "skipped": meetings.skipped,
            "missing_crns": meetings.missing_crns,
            "note": HOLIDAY_NOTE,
        })
        return _finish(result, meetings.events, name=f"İTÜ Ders Programı {semester or ''}".strip(),
                       filename=_filename("itu-ders-programi", semester))

    def export_exam_schedule_ics(self, course_codes: list[str]) -> dict[str, Any]:
        """Public final exam dates of the given courses as an iCalendar document."""
        wanted = _wanted_exam_codes(course_codes)
        found = _Exams()
        for branch, codes in wanted.items():
            exam_rows = _published_exam_rows(self.itu_public, branch, codes, found)
            if exam_rows is None:
                continue
            for code in codes:
                _add_exams_of_course(found, code, exam_rows)

        result: dict[str, Any] = {
            "kind": "exam_schedule",
            "course_codes": [code for codes in wanted.values() for code in codes],
            "timezone": TZID,
            "event_count": len(found.events),
            "events": found.listed,
            "skipped": found.skipped,
            "not_found": found.not_found,
            "end_time_unknown": found.end_time_unknown,
            "source": "obs.itu.edu.tr/public/FinalTakvimi",
            "source_urls": found.sources,
            "untrusted_external_content": True,
        }
        if found.end_time_unknown:
            result["note"] = (
                "Bitiş saati yayınlanmamış sınavlar yalnızca başlangıç saatiyle yazıldı; "
                "süre uydurulmadı."
            )
        return _finish(result, found.events, name="İTÜ Final Sınavları", filename="itu-final-sinavlari.ics")


def _filename(prefix: str, semester: str | None) -> str:
    parsed = parse_semester_label(semester)
    if parsed is None:
        return f"{prefix}.ics"
    return f"{prefix}-{parsed[0]}-{_SEASON_SLUGS[parsed[1]]}.ics"


def _finish(result: dict[str, Any], events: list[IcsEvent], *, name: str, filename: str) -> dict[str, Any]:
    """Attach the ICS text (or the reason there is none) and a one-line summary."""
    left_out = (
        len(result.get("skipped") or [])
        + len(result.get("missing_crns") or [])
        + len(result.get("not_found") or [])
    )
    if events:
        result["status"] = "ok"
        result["ics"] = build_calendar(events, name=name)
        result["mime_type"] = ICS_MIME_TYPE
        result["suggested_filename"] = filename
        result["summary"] = f"{len(events)} etkinlik yazıldı" + (
            f", {left_out} kayıt dışarıda kaldı (skipped, missing_crns, not_found alanlarına bakın)."
            if left_out else "."
        )
        result["save_note"] = (
            "Bu araç dosya yazmaz. ics alanındaki metni .ics uzantılı bir dosyaya (UTF-8) "
            "kaydedip takvim uygulamasına aktarın."
        )
    else:
        result["status"] = "no_events"
        result["ics"] = None
        result["summary"] = (
            "Takvim üretilmedi: yazılabilecek etkinlik yok. Nedenleri skipped, missing_crns "
            "ve not_found alanlarında."
        )
    return result


_CRN_ITEMS = {"type": "string", "pattern": "^[0-9]{4,5}$"}

CALENDAR_EXPORT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "export_course_schedule_ics",
        "title": "Export Course Schedule (.ics)",
        "description": (
            "Build an iCalendar (.ics) document from the weekly meetings of the given CRNs, "
            "read from the public OBS course schedule (no login). Each meeting becomes a "
            "weekly recurring event in Europe/Istanbul time between the first and last day "
            "of classes. Those two dates are taken from term_start/term_end; when omitted "
            "they are read from the official academic calendar (\"Beginning/End of Fall "
            "Term\" for that academic year and level) and 'term_date_sources' says where "
            "each came from. If the calendar does not state one, the result is "
            "status='term_dates_required' with no calendar: ask the user for the dates "
            "instead of guessing. Meetings whose day or time is a "
            "placeholder ('-', '--') are listed in 'skipped' and never given a time; CRNs "
            "not found in the scanned departments are in 'missing_crns'. Holidays are not "
            "excluded from the recurrence. Returns the text in 'ics'; it does not write a file."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "crns": {
                    "type": "array",
                    "items": _CRN_ITEMS,
                    "minItems": 1,
                    "maxItems": MAX_CRNS,
                    "description": "CRNs to export, e.g. ['12469', '12486'].",
                },
                "department_codes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": MAX_DEPARTMENTS,
                    "description": "Department codes whose schedules hold these CRNs, e.g. ['BLG', 'MAT'].",
                },
                "program_type": {
                    "type": "string",
                    "default": "LS",
                    "description": "Program type (default: 'LS' / Lisans).",
                },
                "term_start": {
                    "type": "string",
                    "description": "First day of classes, YYYY-MM-DD.",
                },
                "term_end": {
                    "type": "string",
                    "description": "Last day of classes, YYYY-MM-DD.",
                },
            },
            "required": ["crns", "department_codes"],
            "additionalProperties": False,
        },
    },
    {
        "name": "export_exam_schedule_ics",
        "title": "Export Exam Schedule (.ics)",
        "description": (
            "Build an iCalendar (.ics) document from the current public OBS final exam "
            "schedule (no login), filtered to the given course codes. Each exam is a single "
            "dated event in Europe/Istanbul time. An exam whose date or time is missing or "
            "unreadable is listed in 'skipped'; a code with no row is in 'not_found'; a "
            "branch whose schedule is not published yet is skipped with "
            "'exam_schedule_not_published', which is not the same as having no exam. When "
            "only a start time is published the event has no end time ('end_time_unknown'). "
            "Returns the text in 'ics'; it does not write a file."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "course_codes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": MAX_EXAM_COURSE_CODES,
                    "description": "Exact course codes, e.g. ['BLG 223E', 'MAT 201E'].",
                },
            },
            "required": ["course_codes"],
            "additionalProperties": False,
        },
    },
]
