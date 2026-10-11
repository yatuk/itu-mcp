"""Derived planning helpers built on structured OBS/public data."""

from __future__ import annotations

from datetime import date
import re
from typing import Any

from .parsing import normalize_lookup_text
from .schedule_utils import (
    DAY_DISPLAY_NAMES,
    day_key,
    format_time_point,
    normalize_day,
    parse_time_point,
    parse_time_range,
)


def _course_matches_query(course: dict[str, Any], key: str, *, include_instructor: bool = False) -> bool:
    """Match a folded query against a section's name, code and optionally instructor.

    The code is compared with spaces removed on both sides, so ``mat210e``,
    ``MAT 210E`` and ``mat 210`` all reach ``MAT 210E``.
    """
    if key in normalize_lookup_text(course.get("name")):
        return True
    compact = key.replace(" ", "")
    if compact and compact in normalize_lookup_text(course.get("code")).replace(" ", ""):
        return True
    return include_instructor and key in normalize_lookup_text(course.get("instructor"))


def filter_course_schedule(
    schedule: dict[str, Any],
    *,
    query: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict[str, Any]:
    """Filter names/codes and page sections without altering the cached schedule."""
    key = normalize_lookup_text(query) if query is not None else None
    if key == "":
        raise ValueError("query must contain a course name or code")
    if limit is not None and (type(limit) is not int or not 1 <= limit <= 100):
        raise ValueError("limit must be an integer from 1 to 100")
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a non-negative integer")
    courses = schedule.get("courses") or []
    matches = courses if key is None else [
        course for course in courses if _course_matches_query(course, key)
    ]
    page_limit = limit if limit is not None else 50
    page = matches[offset:offset + page_limit]
    return {
        **schedule,
        "courses": page,
        "count": len(page),
        "total_course_count": len(courses),
        "matched_count": len(matches),
        "filters": {"query": query},
        "limit": page_limit,
        "offset": offset,
        "next_offset": offset + len(page) if offset + len(page) < len(matches) else None,
    }


# A query made only of these words asks for exams, so "final" must not return
# the "Final Registration" rows that happen to contain the same word.
_EXAM_QUERY_WORDS = re.compile(
    r"(?:final\w*|sinav\w*|exams?|examinations?|vize\w*|midterms?|butunleme\w*|make\s*-?\s*up|mazeret\w*)"
)


def _is_exam_query(query: str) -> bool:
    words = normalize_lookup_text(query).split()
    return bool(words) and all(_EXAM_QUERY_WORDS.fullmatch(word) for word in words)


def filter_academic_calendar(
    calendar: dict[str, Any],
    *,
    date_from: str | None = None,
    date_to: str | None = None,
    category: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    from .academic_calendar import calendar_query_matches

    start_filter = date.fromisoformat(date_from) if date_from else None
    end_filter = date.fromisoformat(date_to) if date_to else None
    if start_filter and end_filter and start_filter > end_filter:
        raise ValueError("date_from cannot be after date_to")
    category_key = normalize_lookup_text(category) if category else ""
    events: list[dict[str, Any]] = []
    for event in calendar.get("events") or []:
        start_raw = event.get("start_date")
        end_raw = event.get("end_date") or start_raw
        if start_filter or end_filter:
            if not start_raw:
                continue
            event_start = date.fromisoformat(start_raw)
            event_end = date.fromisoformat(end_raw)
            if start_filter and event_end < start_filter:
                continue
            if end_filter and event_start > end_filter:
                continue
        if category_key and normalize_lookup_text(event.get("category")) != category_key:
            continue
        if query and not calendar_query_matches(event, query):
            continue
        events.append(event)
    query_note = None
    if query and not category_key and _is_exam_query(query):
        exam_rows = [event for event in events if event.get("category") == "exam"]
        dropped = [event for event in events if event.get("category") == "registration"]
        if exam_rows and dropped:
            events = [event for event in events if event.get("category") != "registration"]
            query_note = (
                f"The query asks for exams, so {len(dropped)} registration row(s) containing the same "
                "word were left out. Pass category='registration' to see them."
            )
    coverage = calendar.get("coverage") or {}
    warnings = []
    if start_filter or end_filter:
        covered_start, covered_end = coverage.get("start_date"), coverage.get("end_date")
        if not covered_start or not covered_end:
            warnings.append("The fetched page does not establish complete date coverage for this request.")
        else:
            first, last = date.fromisoformat(covered_start), date.fromisoformat(covered_end)
            # A bound that was given must lie inside the coverage. An open
            # end means "as far as the calendar goes", which is a complete
            # answer for a whole academic year but not for a single month.
            whole_year = coverage.get("scope") == "academic_year"
            outside = any(
                not first <= bound <= last if bound is not None else not whole_year
                for bound in (start_filter, end_filter)
            )
            if outside:
                warnings.append("The requested dates extend beyond the fetched calendar page. An empty result outside its coverage does not establish that no events exist.")
        if coverage.get("complete") is False:
            warnings.append("Source coverage or parsing is incomplete. Date-filtered results may omit events.")
    return {
        **calendar,
        **({"coverage_warning": " ".join(warnings)} if warnings else {}),
        **({"query_note": query_note} if query_note else {}),
        "total_event_count": calendar.get("event_count", len(calendar.get("events") or [])),
        "event_count": len(events),
        "events": events,
        "filters": {
            "date_from": date_from,
            "date_to": date_to,
            "category": category,
            "query": query,
        },
    }


def _program_matches(program: Any, wanted: str) -> bool:
    """``CEN`` and ``CEN_LS`` both name the reservation segment ``CEN_LS``."""
    value = str(program or "").strip().upper()
    if not value or not wanted:
        return False
    return value == wanted or ("_" not in wanted and value.split("_", 1)[0] == wanted)


def section_seat_breakdown(course: dict[str, Any], program_code: str | None = None) -> dict[str, Any] | None:
    """Split a section's free seats into the general pool and reserved quotas.

    OBS publishes ``capacity`` and ``enrolled`` for the whole section, and a
    reservation line that divides the capacity between named programmes and
    the general pool ("Diğer"). A free seat inside another programme's quota
    cannot be taken, so ``available_seats`` is:

    - without ``program_code``: the free seats of the general pool;
    - with it: that programme's remaining quota plus the general pool.

    It never exceeds ``capacity - enrolled``: a programme that went over its
    quota has taken seats from the others. Returns None when capacity or
    enrolled is not a number.
    """
    capacity, enrolled = course.get("capacity"), course.get("enrolled")
    if isinstance(capacity, bool) or isinstance(enrolled, bool):
        return None
    if not isinstance(capacity, (int, float)) or not isinstance(enrolled, (int, float)):
        return None
    total_free = max(0, int(capacity - enrolled))
    wanted = str(program_code or "").strip().upper()
    segments = [item for item in course.get("reservations") or [] if isinstance(item, dict)]
    eligible = [str(item).strip().upper() for item in course.get("eligible_programs") or [] if str(item).strip()]
    program_eligible: bool | None = None
    if wanted and eligible:
        program_eligible = any(_program_matches(item, wanted) for item in eligible)

    breakdown: dict[str, Any] = {"total_free_seats": total_free}
    if not segments:
        # No reservation line, or one this parser could not read.
        fallback = bool(course.get("reservation_unparsed"))
        breakdown.update({
            "general_pool_free_seats": None if fallback else total_free,
            "reserved_free_seats": [],
            "seat_basis": "capacity_minus_enrolled",
            "available_seats": total_free,
        })
        if fallback:
            breakdown["seat_estimate_fallback"] = True
            breakdown["seat_note"] = (
                "Rezervasyon satırı çözümlenemedi; boş yer kontenjan eksi yazılan olarak verildi ve "
                "bir kısmı belirli programlara ayrılmış olabilir."
            )
    else:
        def free_of(item: dict[str, Any]) -> int:
            return max(0, int(item.get("free") or 0))

        general = sum(free_of(item) for item in segments if item.get("general_pool"))
        reserved = [item for item in segments if not item.get("general_pool")]
        own = sum(free_of(item) for item in reserved if _program_matches(item.get("program"), wanted))
        breakdown.update({
            "general_pool_free_seats": min(total_free, general),
            "reserved_free_seats": [
                {"program": item.get("program"), "free": free_of(item)}
                for item in reserved if free_of(item) > 0
            ],
        })
        if wanted:
            breakdown["own_program_free_seats"] = min(total_free, own)
            breakdown["seat_basis"] = "own_program_plus_general_pool"
            breakdown["available_seats"] = min(total_free, own + general)
        else:
            breakdown["seat_basis"] = "general_pool"
            breakdown["available_seats"] = min(total_free, general)
    if wanted and not segments:
        breakdown["own_program_free_seats"] = 0
    if program_eligible is not None:
        breakdown["program_eligible"] = program_eligible
        if not program_eligible:
            breakdown["available_seats"] = 0
    return breakdown


def find_open_sections(
    schedules: list[dict[str, Any]],
    *,
    min_available_seats: int = 1,
    query: str | None = None,
    program_code: str | None = None,
) -> dict[str, Any]:
    """List sections with seats the caller can actually take.

    Seats reserved for other programmes are not counted (see
    ``section_seat_breakdown``). Sections with a day and time come first,
    ordered by available seats; sections without a timetable (project and
    graduation-work sections with a nominal capacity) come last.
    """
    if isinstance(min_available_seats, bool) or not isinstance(min_available_seats, int) or min_available_seats < 1:
        raise ValueError("min_available_seats must be an integer of at least 1")
    query_key = normalize_lookup_text(query) if query else ""
    wanted = str(program_code or "").strip().upper()
    if program_code is not None and not re.fullmatch(r"[A-ZÇĞİÖŞÜ0-9]{2,10}(?:_[A-ZÇĞİÖŞÜ0-9]{1,6})?", wanted):
        raise ValueError(
            f"program_code çözümlenemedi: {program_code!r}. 'CEN_LS' ya da 'CEN' gibi bir program kodu verin."
        )
    seen: set[str] = set()
    sections: list[dict[str, Any]] = []
    matched = 0
    excluded = {
        "full": 0,
        "reserved_for_other_programs": 0,
        "program_not_eligible": 0,
        "below_min_available_seats": 0,
        "extra_exam": 0,
        "unknown_capacity": 0,
    }
    for schedule in schedules:
        department = schedule.get("department_code")
        for course in schedule.get("courses") or []:
            crn = str(course.get("crn") or "")
            if not crn or crn in seen:
                continue
            seen.add(crn)
            if query_key and not _course_matches_query(course, query_key, include_instructor=True):
                continue
            matched += 1
            if course.get("section_kind") == "extra_exam":
                excluded["extra_exam"] += 1
                continue
            seats = section_seat_breakdown(course, wanted or None)
            if seats is None:
                excluded["unknown_capacity"] += 1
                continue
            available = seats["available_seats"]
            if available < min_available_seats:
                if seats["total_free_seats"] <= 0:
                    excluded["full"] += 1
                elif seats.get("program_eligible") is False:
                    excluded["program_not_eligible"] += 1
                elif available <= 0:
                    excluded["reserved_for_other_programs"] += 1
                else:
                    excluded["below_min_available_seats"] += 1
                continue
            sessions = course.get("sessions") or []
            unscheduled = course.get("unscheduled")
            if unscheduled is None:
                unscheduled = not any(
                    day_key(session.get("day")) and parse_time_range(str(session.get("time") or ""))
                    for session in sessions
                )
            sections.append({**course, "department_code": department, **seats, "unscheduled": bool(unscheduled)})
    sections.sort(key=lambda item: (
        bool(item["unscheduled"]), -int(item["available_seats"]), str(item.get("code") or ""), str(item.get("crn") or ""),
    ))
    result: dict[str, Any] = {
        "count": len(sections),
        "sections": sections,
        "departments_scanned": [schedule.get("department_code") for schedule in schedules],
        "query": query,
        "program_code": wanted or None,
        "min_available_seats": min_available_seats,
        "matched_section_count": matched,
        "excluded": excluded,
        "seat_note": (
            "available_seats öğrencinin alabileceği yerdir: program_code verilmediyse genel "
            "kontenjanın (Diğer) boş yeri, verildiyse o programın kalan rezervasyonu artı genel "
            "kontenjan. total_free_seats kontenjan eksi yazılandır; reserved_free_seats başka "
            "programlara ayrılmış boş yerleri gösterir. Günü ve saati olmayan (unscheduled) şubeler "
            "sona sıralanır."
        ),
        "coverage_notice": "Sonuçlar yalnızca department_codes ile taranan resmî programları kapsar.",
    }
    if not sections:
        scanned = ", ".join(str(code) for code in result["departments_scanned"] if code)
        if query_key and not matched:
            reason = "query_matched_no_section"
            message = (
                f"'{query}' sorgusu taranan bölümlerde ({scanned}) hiçbir şubeyle eşleşmedi. Ders kodu "
                "başka bir bölüm kodunun altındaysa o kodu department_codes içine ekleyin."
            )
        elif excluded["program_not_eligible"] and not (excluded["reserved_for_other_programs"] or excluded["below_min_available_seats"]):
            reason = "program_not_eligible"
            message = f"Eşleşen {matched} şubede boş yer var ama {wanted} programı bu şubeleri alabilen programlar arasında değil."
        elif excluded["reserved_for_other_programs"]:
            reason = "free_seats_reserved_for_other_programs"
            message = (
                f"Eşleşen {matched} şubenin {excluded['reserved_for_other_programs']} tanesinde boş yer var "
                "ama bu yerler başka programlara ayrılmış. Kendi program kodunuzu program_code ile verin."
            )
        elif excluded["below_min_available_seats"]:
            reason = "below_min_available_seats"
            message = f"Eşleşen şubelerde boş yer var ama hiçbiri {min_available_seats} yere ulaşmıyor."
        elif matched and matched == excluded["extra_exam"]:
            reason = "only_extra_exam_sections"
            message = "Eşleşen satırların tamamı ek sınav satırı; kayıt yapılabilecek şube yok."
        elif matched:
            reason = "matching_sections_full"
            message = f"Eşleşen {matched} şubenin hepsi dolu."
        else:
            reason = "no_sections_scanned"
            message = "Taranan programlarda şube bulunamadı."
        result["no_result_reason"] = reason
        result["message"] = message
    return result


_NO_ROOM = {"", "online", "cevrimici", "online/cevrimici", "-", "--", "undeclared"}


def find_empty_classrooms(
    schedules: list[dict[str, Any]],
    *,
    day: str,
    time: str,
    building: str | None = None,
) -> dict[str, Any]:
    day_folded = normalize_day(day)
    point = parse_time_point(time)
    building_key = normalize_lookup_text(building) if building else ""
    known_rooms: dict[tuple[str, str], dict[str, str]] = {}
    occupied: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for schedule in schedules:
        for course in schedule.get("courses") or []:
            for session in course.get("sessions") or []:
                room = str(session.get("room") or "").strip()
                session_building = str(session.get("building") or "").strip()
                if normalize_lookup_text(room) in _NO_ROOM:
                    continue
                if normalize_lookup_text(session_building) in _NO_ROOM:
                    continue
                if building_key and building_key not in normalize_lookup_text(session_building):
                    continue
                key = (session_building, room)
                known_rooms[key] = {"building": session_building, "room": room}
                if day_key(session.get("day")) != day_folded:
                    continue
                time_range = parse_time_range(str(session.get("time") or ""))
                if time_range and time_range[0] <= point <= time_range[1]:
                    occupied.setdefault(key, []).append(
                        {"crn": course.get("crn"), "code": course.get("code"), "time": session.get("time")}
                    )
    empty = [value for key, value in known_rooms.items() if key not in occupied]
    empty.sort(key=lambda item: (item["building"], item["room"]))
    return {
        "day": day,
        "time": time,
        "day_normalized": DAY_DISPLAY_NAMES[day_folded],
        "time_normalized": format_time_point(point),
        "building_filter": building,
        "known_room_count": len(known_rooms),
        "occupied_room_count": len(occupied),
        "empty_room_count": len(empty),
        "empty_rooms": empty,
        "occupied_rooms": [
            {**known_rooms[key], "courses": courses} for key, courses in occupied.items()
        ],
        "departments_scanned": [schedule.get("department_code") for schedule in schedules],
        "coverage_notice": "Boşluk tahmini yalnızca taranan bölüm programlarında görülen dersliklere dayanır; rezervasyonları kapsamaz.",
    }
