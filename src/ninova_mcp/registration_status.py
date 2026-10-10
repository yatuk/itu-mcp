"""Summarize official registration records without inferring personal clearance."""
from __future__ import annotations

from datetime import datetime
import re
from typing import Any
from zoneinfo import ZoneInfo

from .academic_calendar import TIMEZONE
from .parsing import normalize_lookup_text
from .registration_tools import _term_key


_STATUS_SOURCE = "https://obs.itu.edu.tr/api/ogrenci/KayitDurumu"
_LESSON_SOURCE = "https://obs.itu.edu.tr/api/ogrenci/DersKayitDurumu"


def _program_key(row: dict[str, Any]) -> tuple[str, ...] | None:
    parts = tuple(normalize_lookup_text(str(row.get(tr) or row.get(en) or "")) for tr, en in (
        ("akademikProgramAdi", "akademikProgramAdiEN"),
        ("akademikBolumAdi", "akademikBolumAdiEN"),
        ("fakulteAdi", "fakulteAdiEN"),
    ))
    return parts if all(parts) else None


def _rows(payload: Any, key: str) -> list[dict[str, Any]] | None:
    if not isinstance(payload, dict) or type(payload.get("statusCode")) is not int or payload["statusCode"] != 0:
        return None
    rows = payload.get(key)
    return rows if isinstance(rows, list) and all(isinstance(row, dict) for row in rows) else None


def _latest(rows: Any) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(rows, list) or not rows:
        return None, "No term records were provided."
    if any(not isinstance(row, dict) or not re.fullmatch(r"\d{6}", str(row.get("akademikDonemKodu") or "")) for row in rows):
        return None, "Term records contain an unknown term identity."
    code = max(str(row["akademikDonemKodu"]) for row in rows)
    candidates = [row for row in rows if str(row["akademikDonemKodu"]) == code]
    if any(row != candidates[0] for row in candidates[1:]):
        return None, "The latest term has conflicting records."
    return candidates[0], None


def _term(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    keys = {key for field in ("akademikDonemAdi", "akademikDonemAdiEN") if (key := _term_key(row.get(field)))}
    identity = next(iter(keys)) if len(keys) == 1 else None
    return {"code": str(row["akademikDonemKodu"]), "name": row.get("akademikDonemAdi"),
            "name_en": row.get("akademikDonemAdiEN"), "academic_year": identity[0] if identity else None,
            "academic_term": identity[1] if identity else None}


def _active(row: dict[str, Any]) -> bool | None:
    text = normalize_lookup_text(str(row.get("durum") or ""))
    if re.fullmatch(r"(?:aktif|active)(?: ogrenci| student)?", text):
        return True
    if any(word in text for word in ("inactive", "pasif", "mezun", "graduated", "kaydi sil", "ilisigi kes")):
        return False
    return None


def _university_overall(row: dict[str, Any]) -> bool:
    labels = {normalize_lookup_text(str(row.get(field) or '')) for field in
              ('akademikBolumAdi', 'akademikBolumAdiEN')}
    return bool(labels & {'universite geneli', 'university overall'}) and not any(
        row.get(field) for field in ('akademikProgramAdi', 'akademikProgramAdiEN', 'fakulteAdi', 'fakulteAdiEN'))


def _choose_window(events: list[dict[str, Any]], now: datetime) -> tuple[dict[str, Any] | None, str | None]:
    candidates = []
    for event in events:
        try:
            start, end = datetime.fromisoformat(event["start_at"]), datetime.fromisoformat(event["end_at"])
            if start.tzinfo is None or end.tzinfo is None or end <= start:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        state = "upcoming" if now < start else "past" if now >= end else "ongoing"
        candidates.append((start, end, state, event))
    if not candidates:
        return None, "No matching event with explicit opening and closing times is available in the fetched calendar."
    ongoing = [item for item in candidates if item[2] == "ongoing"]
    upcoming = [item for item in candidates if item[2] == "upcoming"]
    preferred = ongoing or upcoming or candidates
    # An ongoing event takes priority, then the next opening, then the latest expired one.
    chosen_time = min(x[0] for x in preferred) if ongoing or upcoming else max(x[1] for x in preferred)
    nearest = [x for x in preferred if (x[0] if ongoing or upcoming else x[1]) == chosen_time]
    if len({(x[0], x[1]) for x in nearest}) > 1 or len(ongoing) > 1:
        return None, "Multiple matching calendar windows are ambiguous."
    start, end, state, event = nearest[0]
    return {"start_at": start.isoformat(), "end_at": end.isoformat(), "timezone": TIMEZONE,
            "state": state, "source": "class_calendar", "source_url": event.get("source_url"),
            "description": event.get("description"), "registration_kind": event.get("registration_kind"),
            "class_levels": event.get("class_levels") or [], "academic_year": event.get("academic_year"),
            "academic_term": event.get("academic_term"), "personal_eligibility_verified": False,
            "notice": "This is the published undergraduate class calendar. It does not confirm personal registration clearance or program-specific exceptions."}, None


def summarize_registration_status(
    registration: Any, lesson_registration: Any, calendar: dict[str, Any] | None,
    *, now: datetime | None = None, calendar_error: str | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(ZoneInfo(TIMEZONE))
    if now.tzinfo is None:
        raise ValueError("now must include a timezone")
    programs = _rows(registration, "kayitDurumuList")
    lesson_programs = _rows(lesson_registration, "dersKayitDurumuList")
    errors = []
    if programs is None:
        errors.append("Registration status did not provide a successful, recognized program list.")
    if lesson_programs is None:
        errors.append("Course-registration status did not provide a successful, recognized program list.")
    if calendar_error:
        errors.append(calendar_error)
    results = []
    for index, program in enumerate(programs or []):
        latest, latest_error = _latest(program.get("kayitDurumuDonemList"))
        label = str((latest or {}).get("sinifSeviye") or "")
        class_match = re.fullmatch(r"([1-4])(?:st|nd|rd|th|\.)?\s*(?:sinif|class)", normalize_lookup_text(label))
        level = int(class_match[1]) if class_match else None
        key = _program_key(program)
        matches = [item for item in lesson_programs or [] if key is not None and _program_key(item) == key]
        matching = matches[0] if len(matches) == 1 else None
        match_method = 'exact_program_labels' if matching else None
        overall = _university_overall(program)
        if matching is None and overall and len(programs or []) == 1 and len(lesson_programs or []) == 1:
            # The actual status API can return a University Overall row, with
            # the named degree present only in DersKayitDurumu. Relate it only
            # to a sole program and keep its aggregate source explicit.
            matching = lesson_programs[0]
            match_method = 'sole_program_with_university_overall_status'
        lesson_latest, lesson_error = _latest(matching.get("dersKayitDurumuDonemList")) if matching else (None, "The corresponding course-registration program is missing or ambiguous.")
        source_term, target_term = _term(latest), _term(lesson_latest)
        # KayitDurumu may leave the label blank while the exactly matched
        # DersKayitDurumu program explicitly says Aktif. Do not infer AS or
        # another undocumented code, and preserve disagreement as unknown.
        activity = {_active(row) for row in (program, matching) if row is not None} - {None}
        is_active = next(iter(activity)) if len(activity) == 1 else None
        display_program = matching if overall and matching else program
        program_labels = " ".join(str(display_program.get(field) or "") for field in ("akademikProgramAdi", "akademikProgramAdiEN"))
        graduate = bool(re.search(r"\b(?:lisansustu|yuksek lisans|master|masters|doctoral|doctorate|phd|doktora|hazirlik|preparatory)\b", normalize_lookup_text(program_labels)))
        window = reopening = None
        window_reason = None
        if is_active is not True:
            window_reason = "The program is inactive or its active status is not established."
        elif graduate:
            window_reason = "An undergraduate class window is not applicable to this program type."
        elif level is None:
            window_reason = "The latest official class level is unavailable."
        elif not target_term or not target_term["academic_year"] or not target_term["academic_term"]:
            window_reason = "The course-registration term cannot be matched to an explicit academic year and season."
        elif calendar is None:
            window_reason = "The official calendar could not be read."
        else:
            matching_events = [event for event in calendar.get("events") or [] if isinstance(event, dict)
                and event.get("scope") == "undergraduate" and event.get("category") == "registration"
                and event.get("academic_year") == target_term["academic_year"]
                and event.get("academic_term") == target_term["academic_term"]]
            window, window_reason = _choose_window([event for event in matching_events
                if event.get("registration_kind") == "class_window" and event.get("class_levels") == [level]], now)
            reopening, _ = _choose_window([event for event in matching_events
                if event.get("registration_kind") == "all_students_reopening"], now)
        results.append({
            "program_index": index, "program_name": display_program.get("akademikProgramAdi"),
            "program_name_en": display_program.get("akademikProgramAdiEN"), "department": display_program.get("akademikBolumAdi"),
            "program_match_method": match_method,
            "academic_status_scope": "university_overall" if overall else "program",
            "enrollment_status": program.get("durum"), "enrollment_status_code": program.get("durumKodu"), "active": is_active,
            "enrollment_status_evidence": [{"value": row.get("durum"), "source_url": source}
                for row, source in ((program, _STATUS_SOURCE), (matching, _LESSON_SOURCE)) if row is not None],
            "class_level": level, "class_label": label or None,
            "class_level_source": {"url": _STATUS_SOURCE, "term": source_term, "selection": "latest_reported_term",
                                   "scope": "university_overall" if overall else "program"},
            "latest_academic_term": source_term,
            "official_term_gpa": (latest or {}).get("donemlikNotOrtalamasi"),
            "official_cumulative_gpa": (latest or {}).get("genelNotOrtalamasi"),
            "reported_term_credit": {"value": (latest or {}).get("verilenKredi"), "source_field": "verilenKredi"},
            "course_registration_term": target_term,
            "registration_permission": {"status": (lesson_latest or {}).get("dersKayitDurumu"), "source_url": _LESSON_SOURCE,
                "notice": "The reported permission is not evidence that every personal hold or CRN restriction is clear."},
            "registration_window": window, "registration_window_unavailable_reason": window_reason,
            "all_students_reopening": reopening,
            "max_credit": None, "max_credit_unavailable_reason": "These status endpoints do not report an authoritative maximum registration credit.",
            "blockers": None, "blockers_unavailable_reason": "These status endpoints do not provide a complete list of personal registration holds.",
            "academic_standing": None, "academic_standing_unavailable_reason": "Enrollment status and GPA do not establish a separate academic-standing decision.",
            "warnings": [error for error in (latest_error, lesson_error) if error],
        })
    active = [item["program_index"] for item in results if item["active"] is True]
    selected = active[0] if len(active) == 1 and all(item["active"] is not None for item in results) else None
    return {"per_program": results, "program_count": len(results), "selected_program_index": selected,
            "selection_notice": "One program is explicitly active." if selected is not None else "No single active program can be selected unambiguously. Review each program separately.",
            "checked_at": now.isoformat(), "timezone": TIMEZONE,
            "calendar_coverage": calendar.get("coverage") if calendar else None,
            "calendar_parse_complete": calendar.get("parse_complete") if calendar else None,
            "errors": errors}
