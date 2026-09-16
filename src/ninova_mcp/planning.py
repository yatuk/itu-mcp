"""Derived planning helpers built on structured OBS/public data."""

from __future__ import annotations

from datetime import date
from typing import Any

from .parsing import normalize_lookup_text
from .schedule_utils import DAY_ORDER, parse_time_range


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
    coverage = calendar.get("coverage") or {}
    warnings = []
    if start_filter or end_filter:
        covered_start, covered_end = coverage.get("start_date"), coverage.get("end_date")
        if not covered_start or not covered_end:
            warnings.append("The fetched page does not establish complete date coverage for this request.")
        elif not start_filter or not end_filter or start_filter < date.fromisoformat(covered_start) or end_filter > date.fromisoformat(covered_end):
            warnings.append("The requested dates extend beyond the fetched calendar page. An empty result outside its coverage does not establish that no events exist.")
        if coverage.get("complete") is False:
            warnings.append("Source coverage or parsing is incomplete. Date-filtered results may omit events.")
    return {
        **calendar,
        **({"coverage_warning": " ".join(warnings)} if warnings else {}),
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


def find_open_sections(
    schedules: list[dict[str, Any]],
    *,
    min_available_seats: int = 1,
    query: str | None = None,
) -> dict[str, Any]:
    if min_available_seats < 1:
        raise ValueError("min_available_seats must be at least 1")
    query_key = normalize_lookup_text(query) if query else ""
    seen: set[str] = set()
    sections: list[dict[str, Any]] = []
    for schedule in schedules:
        department = schedule.get("department_code")
        for course in schedule.get("courses") or []:
            crn = str(course.get("crn") or "")
            if not crn or crn in seen:
                continue
            seen.add(crn)
            capacity = course.get("capacity")
            enrolled = course.get("enrolled")
            if not isinstance(capacity, (int, float)) or not isinstance(enrolled, (int, float)):
                continue
            available = int(capacity - enrolled)
            if available < min_available_seats:
                continue
            if query_key and query_key not in normalize_lookup_text(
                f"{course.get('code') or ''} {course.get('name') or ''} {course.get('instructor') or ''}"
            ):
                continue
            sections.append({**course, "department_code": department, "available_seats": available})
    sections.sort(key=lambda item: (-int(item["available_seats"]), str(item.get("code") or "")))
    return {
        "count": len(sections),
        "sections": sections,
        "departments_scanned": [schedule.get("department_code") for schedule in schedules],
        "query": query,
        "min_available_seats": min_available_seats,
        "coverage_notice": "Sonuçlar yalnızca department_codes ile taranan resmî programları kapsar.",
    }


def _time_point(value: str) -> int:
    raw = value.strip()
    if ":" not in raw:
        raise ValueError("time must use HH:MM format")
    hour, minute = raw.split(":", 1)
    parsed = int(hour) * 60 + int(minute)
    if not 0 <= parsed < 24 * 60 or not 0 <= int(minute) <= 59:
        raise ValueError("time must use a valid HH:MM value")
    return parsed


def find_empty_classrooms(
    schedules: list[dict[str, Any]],
    *,
    day: str,
    time: str,
    building: str | None = None,
) -> dict[str, Any]:
    day_key = normalize_lookup_text(day)
    if day_key not in DAY_ORDER:
        raise ValueError("day must be a Turkish weekday name")
    point = _time_point(time)
    building_key = normalize_lookup_text(building) if building else ""
    known_rooms: dict[tuple[str, str], dict[str, str]] = {}
    occupied: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for schedule in schedules:
        for course in schedule.get("courses") or []:
            for session in course.get("sessions") or []:
                room = str(session.get("room") or "").strip()
                session_building = str(session.get("building") or "").strip()
                if not room or normalize_lookup_text(room) in {"online", "cevrimici", "-", "--"}:
                    continue
                if normalize_lookup_text(session_building) in {"", "-", "undeclared"}:
                    continue
                if building_key and building_key not in normalize_lookup_text(session_building):
                    continue
                key = (session_building, room)
                known_rooms[key] = {"building": session_building, "room": room}
                if normalize_lookup_text(session.get("day")) != day_key:
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
