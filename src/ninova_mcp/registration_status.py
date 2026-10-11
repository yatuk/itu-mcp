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


def _is_summer(row: dict[str, Any]) -> bool:
    """Summer by the term name, or by the term code when the name has no season."""
    term = _term(row) or {}
    if term.get("academic_term"):
        return term["academic_term"] == "summer"
    labels = normalize_lookup_text(" ".join(str(row.get(field) or "") for field in ("akademikDonemAdi", "akademikDonemAdiEN")))
    if re.search(r"\b(?:yaz|summer)\b", labels):
        return True
    if re.search(r"\b(?:guz|fall|autumn|bahar|spring)\b", labels):
        return False
    return str(row.get("akademikDonemKodu") or "").endswith("30")


def _last_regular_term(rows: Any, latest: dict[str, Any] | None) -> tuple[dict[str, Any] | None, str | None]:
    """The latest fall or spring term, so a summer term does not stand for "last term"."""
    if latest is None or not isinstance(rows, list):
        return None, "The term records could not be read."
    regular = [row for row in rows if not _is_summer(row)]
    if not regular:
        return None, "No fall or spring term is reported; only summer terms are present."
    code = max(str(row["akademikDonemKodu"]) for row in regular)
    candidates = [row for row in regular if str(row["akademikDonemKodu"]) == code]
    if any(row != candidates[0] for row in candidates[1:]):
        return None, "The last regular term has conflicting records."
    chosen = candidates[0]
    term = _term(chosen) or {}
    summary = {**term, "gpa": chosen.get("donemlikNotOrtalamasi"), "cumulative_gpa": chosen.get("genelNotOrtalamasi"),
               "class_label": chosen.get("sinifSeviye") or None,
               "reported_term_credit": chosen.get("verilenKredi"), "source_url": _STATUS_SOURCE}
    note = None
    if _is_summer(latest):
        note = ("The latest reported term is a summer term, so official_term_gpa describes summer courses only. "
                "last_regular_term holds the latest fall or spring term.")
    return summary, note


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


_Window = tuple[datetime, datetime, str, dict[str, Any]]
_AMBIGUOUS_WINDOWS = "Multiple matching calendar windows are ambiguous."


def _window_bounds(event: dict[str, Any]) -> tuple[datetime, datetime] | None:
    """The opening and closing times, only when both are explicit and in order."""
    try:
        start, end = datetime.fromisoformat(event["start_at"]), datetime.fromisoformat(event["end_at"])
    except (KeyError, TypeError, ValueError):
        return None
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        return None
    return start, end


def _window_state(start: datetime, end: datetime, now: datetime) -> str:
    if now < start:
        return "upcoming"
    return "past" if now >= end else "ongoing"


def _nearest_windows(candidates: list[_Window]) -> list[_Window]:
    """An ongoing event takes priority, then the next opening, then the latest expired one."""
    ongoing = [item for item in candidates if item[2] == "ongoing"]
    upcoming = [item for item in candidates if item[2] == "upcoming"]
    preferred = ongoing or upcoming
    if preferred:
        opening = min(item[0] for item in preferred)
        return [item for item in preferred if item[0] == opening]
    closing = max(item[1] for item in candidates)
    return [item for item in candidates if item[1] == closing]


def _choose_window(events: list[dict[str, Any]], now: datetime) -> tuple[dict[str, Any] | None, str | None]:
    candidates: list[_Window] = []
    seen: set[tuple[datetime, datetime, str]] = set()
    for event in events:
        bounds = _window_bounds(event)
        if bounds is None:
            continue
        # The same opening and closing for the same class levels is one
        # window, however many times the calendar lists it; the first is kept.
        identity = (*bounds, repr(event.get("class_levels") or []))
        if identity not in seen:
            seen.add(identity)
            candidates.append((*bounds, _window_state(*bounds, now), event))
    if not candidates:
        return None, "No matching event with explicit opening and closing times is available in the fetched calendar."
    nearest = _nearest_windows(candidates)
    several_ongoing = sum(1 for item in candidates if item[2] == "ongoing") > 1
    if several_ongoing or len({(item[0], item[1]) for item in nearest}) > 1:
        return None, _AMBIGUOUS_WINDOWS
    start, end, state, event = nearest[0]
    return {"start_at": start.isoformat(), "end_at": end.isoformat(), "timezone": TIMEZONE,
            "state": state, "source": "class_calendar", "source_url": event.get("source_url"),
            "description": event.get("description"), "registration_kind": event.get("registration_kind"),
            "class_levels": event.get("class_levels") or [], "academic_year": event.get("academic_year"),
            "academic_term": event.get("academic_term"), "personal_eligibility_verified": False,
            "notice": "This is the published undergraduate class calendar. It does not confirm personal registration clearance or program-specific exceptions."}, None


# Always null with a fixed reason: these endpoints never report them. The tool
# description says so once instead of every result.
_NEVER_REPORTED = (
    "max_credit", "max_credit_unavailable_reason", "blockers", "blockers_unavailable_reason",
    "academic_standing", "academic_standing_unavailable_reason",
)
_MAX_HISTORY_TERMS = 30


def _short_term(term: Any) -> Any:
    """A term with its name in one language."""
    if not isinstance(term, dict):
        return term
    return {key: value for key, value in term.items() if key != "name_en" or not term.get("name")}


def _short_window(window: Any) -> Any:
    """A calendar window without its fixed notice; the result's own timezone applies."""
    if not isinstance(window, dict):
        return window
    return {key: value for key, value in window.items() if key not in ("notice", "timezone")}


def term_history(registration: Any) -> list[list[dict[str, Any]]] | None:
    """Per program, the official record of every term, newest first.

    This is the part of the KayitDurumu payload that answers a question about
    an earlier term. Returns None when the payload is not a successful,
    recognized answer.
    """
    programs = _rows(registration, "kayitDurumuList")
    if programs is None:
        return None
    history = []
    for program in programs:
        terms = program.get("kayitDurumuDonemList")
        rows = [row for row in terms if isinstance(row, dict)] if isinstance(terms, list) else []
        rows.sort(key=lambda row: str(row.get("akademikDonemKodu") or ""), reverse=True)
        history.append([
            {
                "code": None if row.get("akademikDonemKodu") is None else str(row["akademikDonemKodu"]),
                "name": row.get("akademikDonemAdi") or row.get("akademikDonemAdiEN"),
                "gpa": row.get("donemlikNotOrtalamasi"),
                "cumulative_gpa": row.get("genelNotOrtalamasi"),
                "reported_term_credit": row.get("verilenKredi"),
                "class_label": row.get("sinifSeviye") or None,
            }
            for row in rows
        ])
    return history


def _compact_last_regular_term(regular: dict[str, Any], latest: dict[str, Any]) -> dict[str, Any]:
    if regular.get("code") and regular.get("code") == latest.get("code"):
        # Its values are the official_* fields of this same program.
        return {"code": regular["code"], "same_as_latest_academic_term": True}
    return {key: value for key, value in _short_term(regular).items() if key != "source_url"}


def _compact_program(program: dict[str, Any]) -> dict[str, Any]:
    """One program without fixed texts and without values that repeat another field."""
    row = {key: value for key, value in program.items() if key not in _NEVER_REPORTED}
    if row.get("program_name"):
        row.pop("program_name_en", None)
    # Where the class level and the GPA fields come from never varies:
    # the latest reported term of KayitDurumu, which the result names.
    row.pop("class_level_source", None)
    # The evidence matters only when the two endpoints disagree.
    evidence = row.get("enrollment_status_evidence") or []
    if len({str(item.get("value") or "").strip() for item in evidence}) <= 1:
        row.pop("enrollment_status_evidence", None)
    for key in ("latest_academic_term", "course_registration_term"):
        row[key] = _short_term(row.get(key))
    regular = row.get("last_regular_term")
    if isinstance(regular, dict):
        row["last_regular_term"] = _compact_last_regular_term(regular, row.get("latest_academic_term") or {})
    # Present only when it says something.
    for key in ("last_regular_term_note", "all_students_reopening_unavailable_reason"):
        if row.get(key) is None:
            row.pop(key, None)
    permission = row.get("registration_permission")
    if isinstance(permission, dict):
        row["registration_permission"] = {"status": permission.get("status")}
    for key in ("registration_window", "all_students_reopening"):
        row[key] = _short_window(row.get(key))
    return row


def compact_registration_summary(summary: dict[str, Any], registration: Any = None) -> dict[str, Any]:
    """The summary as ``obs_get_registration_status`` returns it by default.

    Fixed texts and values that repeat another field are left out, a term is
    named in one language, and each program gains ``term_history`` so the raw
    payload is not needed for a question about an earlier term.
    """
    history = term_history(registration)
    programs = []
    for position, program in enumerate(summary.get("per_program") or []):
        row = _compact_program(program)
        if history is not None and position < len(history):
            terms = history[position]
            row["term_history"] = terms[:_MAX_HISTORY_TERMS]
            row["term_history_count"] = len(row["term_history"])
            row["term_history_total_count"] = len(terms)
        programs.append(row)
    compact = {key: value for key, value in summary.items() if key != "selection_notice"}
    compact["per_program"] = programs
    return compact


def _class_level(value: Any) -> int | None:
    """The undergraduate class year, 1 to 4, that ``sinifSeviye`` states.

    Read from a label such as ``3. Sınıf`` or ``3rd Class``, or from the bare
    number ``3`` or ``"3"``. Anything else, a number outside 1 to 4 included,
    is no class year: the published class windows exist for these four only.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    match = re.fullmatch(
        r"([1-4])(?:(?:st|nd|rd|th|\.)?\s*(?:sinif|class))?", normalize_lookup_text(str(value or "")))
    return int(match[1]) if match else None


def _has_no_undergraduate_class(program: dict[str, Any]) -> bool:
    """Graduate and preparatory programs, recognized by the program name."""
    labels = " ".join(str(program.get(field) or "") for field in ("akademikProgramAdi", "akademikProgramAdiEN"))
    return bool(re.search(
        r"\b(?:lisansustu|yuksek lisans|master|masters|doctoral|doctorate|phd|doktora|hazirlik|preparatory)\b",
        normalize_lookup_text(labels)))


def _match_lesson_program(
    program: dict[str, Any], programs: list[dict[str, Any]], lesson_programs: list[dict[str, Any]] | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """The DersKayitDurumu row of the same program, and how it was related."""
    key = _program_key(program)
    matches = [item for item in lesson_programs or [] if key is not None and _program_key(item) == key]
    if len(matches) == 1:
        return matches[0], "exact_program_labels"
    sole_lesson_program = lesson_programs[0] if lesson_programs and len(lesson_programs) == 1 else None
    # A row without any field says nothing about a program, so it is not related.
    if sole_lesson_program and len(programs) == 1 and _university_overall(program):
        # The actual status API can return a University Overall row, with
        # the named degree present only in DersKayitDurumu. Relate it only
        # to a sole program and keep its aggregate source explicit.
        return sole_lesson_program, "sole_program_with_university_overall_status"
    return None, None


def _latest_lesson_term(matching: dict[str, Any] | None) -> tuple[dict[str, Any] | None, str | None]:
    if matching is None:
        return None, "The corresponding course-registration program is missing or ambiguous."
    return _latest(matching.get("dersKayitDurumuDonemList"))


def _explicit_activity(program: dict[str, Any], matching: dict[str, Any] | None) -> bool | None:
    # KayitDurumu may leave the label blank while the exactly matched
    # DersKayitDurumu program explicitly says Aktif. Do not infer AS or
    # another undocumented code, and preserve disagreement as unknown.
    activity = {_active(row) for row in (program, matching) if row is not None} - {None}
    return next(iter(activity)) if len(activity) == 1 else None


def _status_label(program: dict[str, Any], matching: dict[str, Any] | None) -> tuple[Any, str | None]:
    # The University Overall row leaves its label blank. Show the first
    # label that is present and name the endpoint it came from.
    for candidate, source in ((program, _STATUS_SOURCE), (matching, _LESSON_SOURCE)):
        if candidate is not None and str(candidate.get("durum") or "").strip():
            return candidate.get("durum"), source
    return program.get("durum"), None


def _term_is_explicit(term: dict[str, Any] | None) -> bool:
    return bool(term and term["academic_year"] and term["academic_term"])


def _class_window_not_applicable(
    is_active: bool | None, program: dict[str, Any], level: int | None,
    target_term: dict[str, Any] | None, calendar: dict[str, Any] | None,
) -> str | None:
    """Why no class window is looked up for this program, or None when one is."""
    if is_active is not True:
        return "The program is inactive or its active status is not established."
    if _has_no_undergraduate_class(program):
        return "An undergraduate class window is not applicable to this program type."
    if level is None:
        return "The latest official class level is unavailable."
    if not _term_is_explicit(target_term):
        return "The course-registration term cannot be matched to an explicit academic year and season."
    if calendar is None:
        return "The official calendar could not be read."
    return None


def _is_undergraduate_registration_event(event: Any, term: dict[str, Any]) -> bool:
    """An undergraduate registration event of exactly this academic year and season."""
    if not isinstance(event, dict):
        return False
    if event.get("scope") != "undergraduate" or event.get("category") != "registration":
        return False
    return event.get("academic_year") == term["academic_year"] and event.get("academic_term") == term["academic_term"]


def _calendar_windows(
    calendar: dict[str, Any], term: dict[str, Any], level: int, now: datetime,
) -> tuple[dict[str, Any] | None, str | None, dict[str, Any] | None, str | None]:
    """The class window and the reopening for all students, each with why it is missing.

    A calendar without a reopening event is the usual case and gets no reason;
    several reopening events that cannot be told apart do.
    """
    events = [event for event in calendar.get("events") or [] if _is_undergraduate_registration_event(event, term)]
    window, window_reason = _choose_window(
        [event for event in events
         if event.get("registration_kind") == "class_window" and event.get("class_levels") == [level]], now)
    reopenings = [event for event in events if event.get("registration_kind") == "all_students_reopening"]
    reopening, reopening_reason = _choose_window(reopenings, now)
    if reopening_reason != _AMBIGUOUS_WINDOWS:
        reopening_reason = None
    return window, window_reason, reopening, reopening_reason


def _latest_term_fields(latest: dict[str, Any] | None, source_term: dict[str, Any] | None) -> dict[str, Any]:
    """What the latest reported term of KayitDurumu says, summer or not."""
    row = latest or {}
    return {
        "latest_academic_term": source_term,
        "official_term_gpa": row.get("donemlikNotOrtalamasi"),
        "official_cumulative_gpa": row.get("genelNotOrtalamasi"),
        "reported_term_credit": {"value": row.get("verilenKredi"), "source_field": "verilenKredi"},
        "latest_term_is_summer": _is_summer(latest) if latest else None,
    }


def _summarize_program(
    index: int, program: dict[str, Any], programs: list[dict[str, Any]],
    lesson_programs: list[dict[str, Any]] | None, calendar: dict[str, Any] | None, now: datetime,
) -> dict[str, Any]:
    term_rows = program.get("kayitDurumuDonemList")
    latest, latest_error = _latest(term_rows)
    matching, match_method = _match_lesson_program(program, programs, lesson_programs)
    lesson_latest, lesson_error = _latest_lesson_term(matching)
    source_term, target_term = _term(latest), _term(lesson_latest)
    # A University Overall row has no program name of its own.
    scope = "university_overall" if _university_overall(program) else "program"
    display_program = matching if scope == "university_overall" and matching else program
    raw_level = (latest or {}).get("sinifSeviye")
    label = str(raw_level or "")
    level = _class_level(raw_level)
    is_active = _explicit_activity(program, matching)
    status_label, status_source = _status_label(program, matching)
    regular_term, regular_note = _last_regular_term(term_rows, latest)
    window = reopening = reopening_reason = None
    window_reason = _class_window_not_applicable(is_active, display_program, level, target_term, calendar)
    if window_reason is None:
        window, window_reason, reopening, reopening_reason = _calendar_windows(calendar, target_term, level, now)
    return {
        "program_index": index, "program_name": display_program.get("akademikProgramAdi"),
        "program_name_en": display_program.get("akademikProgramAdiEN"), "department": display_program.get("akademikBolumAdi"),
        "program_match_method": match_method,
        "academic_status_scope": scope,
        "enrollment_status": status_label, "enrollment_status_source": status_source,
        "enrollment_status_code": program.get("durumKodu"), "active": is_active,
        "enrollment_status_evidence": [{"value": row.get("durum"), "source_url": source}
            for row, source in ((program, _STATUS_SOURCE), (matching, _LESSON_SOURCE)) if row is not None],
        "class_level": level, "class_label": label or None,
        "class_level_source": {"url": _STATUS_SOURCE, "term": source_term, "selection": "latest_reported_term",
                               "scope": scope},
        **_latest_term_fields(latest, source_term),
        "last_regular_term": regular_term, "last_regular_term_note": regular_note,
        "course_registration_term": target_term,
        "registration_permission": {"status": (lesson_latest or {}).get("dersKayitDurumu"), "source_url": _LESSON_SOURCE,
            "notice": "The reported permission is not evidence that every personal hold or CRN restriction is clear."},
        "registration_window": window, "registration_window_unavailable_reason": window_reason,
        "all_students_reopening": reopening,
        "all_students_reopening_unavailable_reason": reopening_reason,
        "max_credit": None, "max_credit_unavailable_reason": "These status endpoints do not report an authoritative maximum registration credit.",
        "blockers": None, "blockers_unavailable_reason": "These status endpoints do not provide a complete list of personal registration holds.",
        "academic_standing": None, "academic_standing_unavailable_reason": "Enrollment status and GPA do not establish a separate academic-standing decision.",
        "warnings": [error for error in (latest_error, lesson_error) if error],
    }


def _payload_errors(programs: Any, lesson_programs: Any, calendar_error: str | None) -> list[str]:
    errors = []
    if programs is None:
        errors.append("Registration status did not provide a successful, recognized program list.")
    if lesson_programs is None:
        errors.append("Course-registration status did not provide a successful, recognized program list.")
    if calendar_error:
        errors.append(calendar_error)
    return errors


def _only_active_program(results: list[dict[str, Any]]) -> int | None:
    """The index of the one active program, when no program's activity is unknown."""
    active = [item["program_index"] for item in results if item["active"] is True]
    return active[0] if len(active) == 1 and all(item["active"] is not None for item in results) else None


def summarize_registration_status(
    registration: Any, lesson_registration: Any, calendar: dict[str, Any] | None,
    *, now: datetime | None = None, calendar_error: str | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(ZoneInfo(TIMEZONE))
    if now.tzinfo is None:
        raise ValueError("now must include a timezone")
    programs = _rows(registration, "kayitDurumuList")
    lesson_programs = _rows(lesson_registration, "dersKayitDurumuList")
    errors = _payload_errors(programs, lesson_programs, calendar_error)
    results = [
        _summarize_program(index, program, programs or [], lesson_programs, calendar, now)
        for index, program in enumerate(programs or [])
    ]
    selected = _only_active_program(results)
    return {"per_program": results, "program_count": len(results), "selected_program_index": selected,
            "selection_notice": "One program is explicitly active." if selected is not None else "No single active program can be selected unambiguously. Review each program separately.",
            "checked_at": now.isoformat(), "timezone": TIMEZONE,
            "calendar_coverage": calendar.get("coverage") if calendar else None,
            "calendar_parse_complete": calendar.get("parse_complete") if calendar else None,
            "errors": errors}
