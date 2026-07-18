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
    start_filter = date.fromisoformat(date_from) if date_from else None
    end_filter = date.fromisoformat(date_to) if date_to else None
    if start_filter and end_filter and start_filter > end_filter:
        raise ValueError("date_from cannot be after date_to")
    category_key = normalize_lookup_text(category) if category else ""
    query_key = normalize_lookup_text(query) if query else ""
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
        if query_key and query_key not in normalize_lookup_text(
            f"{event.get('description') or ''} {event.get('date') or ''}"
        ):
            continue
        events.append(event)
    return {
        **calendar,
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
                if not room or normalize_lookup_text(room) in {"online", "cevrimici", "-"}:
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


def explain_course_eligibility(
    prerequisite_data: dict[str, Any],
    *,
    completed_courses: list[str],
    completed_credits: float | None = None,
    class_year: int | None = None,
) -> dict[str, Any]:
    completed = {normalize_lookup_text(code) for code in completed_courses}
    prerequisites = prerequisite_data.get("prerequisites") or []
    groups: dict[str, list[dict[str, Any]]] = {}
    for index, item in enumerate(prerequisites):
        group = str(item.get("group") or f"requirement-{index + 1}")
        groups.setdefault(group, []).append(item)

    group_results: list[dict[str, Any]] = []
    for group, options in groups.items():
        evaluated = [
            {**option, "completed": normalize_lookup_text(option.get("code")) in completed}
            for option in options
        ]
        # OBS group numbers represent alternatives inside a group; separate
        # groups are cumulative. Preserve the raw type so callers can audit it.
        satisfied = any(option["completed"] for option in evaluated)
        group_results.append({"group": group, "satisfied": satisfied, "options": evaluated, "logic": "OR"})

    credit_requirement = prerequisite_data.get("credit_prerequisite")
    requirement_key = normalize_lookup_text(credit_requirement) if credit_requirement else ""
    credit_satisfied: bool | None = None
    class_satisfied: bool | None = None
    credit_required = False
    class_required = False
    if credit_requirement:
        import re

        credit_match = re.search(
            r"(\d+(?:[.,]\d+)?)\s*(?:basarilmis\s+|basarilan\s+)?kredi",
            requirement_key,
        )
        class_match = re.search(r"(\d+)\s*(?:inci|nci|uncu|\.?)?\s*sinif", requirement_key)
        credit_required = credit_match is not None
        class_required = class_match is not None
        if credit_match and completed_credits is not None:
            credit_satisfied = completed_credits >= float(credit_match.group(1).replace(",", "."))
        if class_match and class_year is not None:
            class_satisfied = class_year >= int(class_match.group(1))

    checks: list[bool] = [bool(group["satisfied"]) for group in group_results]
    if credit_satisfied is not None:
        checks.append(credit_satisfied)
    if class_satisfied is not None:
        checks.append(class_satisfied)
    unknown_requirements = (
        (credit_required and credit_satisfied is None)
        or (class_required and class_satisfied is None)
    )
    if any(not check for check in checks):
        eligible: bool | None = False
    elif unknown_requirements:
        eligible = None
    else:
        eligible = True
    return {
        "eligible": eligible,
        "eligibility_status": (
            "eligible" if eligible is True else "ineligible" if eligible is False else "unknown"
        ),
        "prerequisite_groups": group_results,
        "credit_requirement": credit_requirement,
        "credit_requirement_satisfied": credit_satisfied,
        "class_requirement_satisfied": class_satisfied,
        "completed_credits": completed_credits,
        "class_year": class_year,
        "missing_groups": [group for group in group_results if not group["satisfied"]],
        "interpretation_notice": "Sonuç OBS önşart gruplarını grup içinde VEYA, gruplar arasında VE olarak yorumlar; resmî kayıt kararı OBS'nindir.",
    }
