"""Select a class's letter grade from the official semester-wide OBS endpoint."""

from __future__ import annotations

from typing import Any

from .obs_client import ObsError

MAX_SEMESTER_LOOKUPS = 40


def _identifier(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    text = str(value or "").strip()
    return str(int(text)) if text.isascii() and text.isdigit() and int(text) > 0 else None


def _rows(payload: Any, field: str, scope: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or type(payload.get("statusCode")) is not int:
        raise ObsError(f"OBS returned an unrecognized {scope} response.")
    if payload["statusCode"] != 0:
        raise ObsError(f"OBS could not provide {scope}.")
    rows = payload.get(field)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ObsError(f"OBS returned an unrecognized {scope} list.")
    return rows


def _registered_class(
    obs: Any,
    class_record: dict[str, Any],
    semester: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve the class to an official term without matching by course name."""
    class_id = _identifier(class_record.get("sinifId"))
    if class_id is None:
        raise ObsError("A positive class ID is required to select a letter grade.")
    if semester is not None:
        semesters = [obs.resolve_semester(semester)]
    elif isinstance(class_record.get("semester"), dict):
        semesters = [class_record["semester"]]
    else:
        semesters = _rows(obs.list_semesters(), "ogrenciDonemListesi", "semester")
    visited: set[str] = set()
    unreadable = False
    for term in semesters:
        if not isinstance(term, dict):
            raise ObsError("OBS returned an unrecognized semester record.")
        term_id = _identifier(term.get("akademikDonemId"))
        if term_id is None:
            unreadable = True
            continue
        if term_id in visited:
            continue
        if len(visited) >= MAX_SEMESTER_LOOKUPS:
            break
        visited.add(term_id)
        try:
            rows = _rows(obs.list_registered_courses(term_id), "kayitSinifResultList", "registered course")
        except ObsError:
            unreadable = True
            continue
        matches = [row for row in rows if _identifier(row.get("sinifId")) == class_id]
        if len(matches) > 1:
            raise ObsError("OBS returned duplicate records for the requested class.")
        if matches:
            registered = matches[0]
            if _identifier(registered.get("crn")) is None:
                raise ObsError("The registered class has no usable CRN for matching its letter grade.")
            expected_crn = _identifier(class_record.get("crn"))
            if expected_crn is not None and expected_crn != _identifier(registered.get("crn")):
                raise ObsError("The requested class and the registered course have different CRNs.")
            return term, registered
    detail = " Some semester records were unavailable." if unreadable else ""
    raise ObsError(
        "The class could not be matched to a registered course in the inspected semesters. "
        "Provide the class's semester to narrow the lookup." + detail
    )


def get_class_letter_grades(
    obs: Any,
    class_record: dict[str, Any],
    *,
    semester: str | None = None,
) -> dict[str, Any]:
    """Return only the requested class's row, matched by exact term and CRN.

    OBS's ``SinifHarfNotuListesi`` route accepts an academic semester ID.
    Its result is a student's course list, rather than a class distribution.
    An explicit class ID is resolved through registered courses first, with a
    bounded search of the student's semesters when the caller omitted the term.
    """
    term, registered = _registered_class(obs, class_record, semester)
    term_id = _identifier(term["akademikDonemId"])
    payload = obs.get_letter_grades(term_id)
    rows = _rows(payload, "sinifHarfNotuResultList", "semester letter grade")
    crn = _identifier(registered["crn"])
    matches = [row for row in rows if _identifier(row.get("crn")) == crn]
    if len(matches) > 1:
        raise ObsError("OBS returned more than one letter grade for the requested term and CRN.")
    available = bool(matches and str(matches[0].get("harfNotu") or "").strip())
    return {
        "statusCode": payload["statusCode"],
        "sinifHarfNotuResultList": matches,
        "academic_semester_id": int(term_id),
        "class_id": int(_identifier(registered["sinifId"])),
        "crn": crn,
        "scope": "requested_class",
        "source": "obs_semester_letter_grades",
        "available": available,
        "status": "available" if available else "not_available",
        "note": (
            "The letter grade is matched to the requested class's semester and CRN."
            if available else "OBS has not returned a published letter grade for the requested class."
        ),
    }
