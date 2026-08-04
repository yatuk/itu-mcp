"""Readable summary of the OBS 'Mezuniyetime Ne Kaldı' payload.

The raw endpoint returns one flat list of every plan slot with an ``isMet``
flag. All the information is there, but two facts get lost in the volume: which
real course was counted against a named elective slot, and which slots are still
open. A plan with sixty entries makes "did BLG 422E already fill 7th Sems.
Elect. Course I (MT)?" effectively unanswerable by reading, even though the
answer is one field away.

This module reshapes that list into slots, remaining requirements, and a credit
tally, so the mapping is stated rather than inferred.
"""

from __future__ import annotations

from typing import Any

# Grades that mean the attempt did not count toward the plan.
FAILING_GRADES = frozenset({"FF", "FD", "VF", "BZ", "KF", "IA", "NA"})


def _credit_of(item: dict[str, Any]) -> float:
    raw = item.get("kredisiDec")
    if raw is None:
        raw = item.get("kredisi")
    try:
        return float(str(raw).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def _semester_label(item: dict[str, Any]) -> str | None:
    number = item.get("donemNo")
    return f"{number}. yarıyıl" if number else None


def summarize_graduation_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """Reshape the graduation payload into satisfied slots and open requirements.

    Every entry carrying a ``grupName`` is an elective slot; the same entry's
    ``bransKodu`` is the course that filled it. Reporting the two together turns
    an opaque met/unmet flag into "BLG 422E → 7th Sems. Elect. Course I (MT)".
    """
    info = payload.get("mezuniyetimeNeKaldiBilgi") or {}
    entries = info.get("checkMetMezuniyetList") or []
    plan = info.get("dersPlaniVM") or {}

    filled_slots: list[dict[str, Any]] = []
    open_slots: list[dict[str, Any]] = []
    completed_courses: list[dict[str, Any]] = []
    remaining_courses: list[dict[str, Any]] = []

    for item in entries:
        group = str(item.get("grupName") or "").strip()
        code = str(item.get("bransKodu") or "").strip()
        record = {
            "course_code": code or None,
            "course_name": item.get("dersAdi"),
            "credit": _credit_of(item),
            "counted_credit": item.get("sayilanKredi"),
            "grade": item.get("harfNotu") or None,
            "semester": _semester_label(item),
            "term": item.get("donem"),
            "crn": item.get("crn"),
        }

        if item.get("isMet"):
            if group:
                # A named elective slot with a real course behind it: this is
                # the mapping the raw payload buries.
                filled_slots.append({**record, "slot": group})
            completed_courses.append(record)
        else:
            if group:
                open_slots.append({
                    "slot": group,
                    "credit": _credit_of(item),
                    "semester": _semester_label(item),
                })
            else:
                remaining_courses.append(record)

    unused = [
        {
            "course_code": item.get("bransKodu"),
            "course_name": item.get("dersAdi"),
            "grade": item.get("harfNotu"),
            "credit": item.get("kredisi"),
            "term": item.get("donem"),
        }
        for item in info.get("unusedSinifOgrenciList") or []
    ]

    required_credits = plan.get("gerekliMezuniyetKredisi")
    earned_credits = info.get("metKrediTotal")
    try:
        credits_left = float(required_credits) - float(earned_credits)
    except (TypeError, ValueError):
        credits_left = None

    return {
        "program": plan.get("akademikProgramAdiTR"),
        "plan_title": plan.get("dersPlaniBaslik"),
        "gpa": info.get("gpa"),
        "required_gpa": plan.get("gerekliMinGPA"),
        "credits_required": required_credits,
        "credits_earned": earned_credits,
        "credits_remaining": credits_left,
        "courses_total": info.get("toplamDersSayisi"),
        "courses_completed": info.get("tamamlananDersSayisi"),
        "internship_days_required": plan.get("gerekliStajGunu"),
        "internship_days_done": info.get("ogrenciTamamlananStaj"),
        "filled_elective_slots": sorted(filled_slots, key=lambda s: s["slot"]),
        "open_elective_slots": sorted(open_slots, key=lambda s: s["slot"]),
        "remaining_required_courses": sorted(
            remaining_courses, key=lambda c: (c["semester"] or "", c["course_code"] or "")
        ),
        "completed_course_count": len(completed_courses),
        "failed_or_unused_attempts": unused,
        "note": (
            "filled_elective_slots, hangi gerçek dersin hangi seçmeli slotu doldurduğunu "
            "gösterir; ham listede bu eşleşme yalnızca grupName alanında gizlidir."
        ),
    }
