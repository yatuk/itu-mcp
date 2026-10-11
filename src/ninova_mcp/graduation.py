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

import math
from typing import Any

from .archive import normalize_course_code
from .grading import FAILING_GRADES, INCOMPLETE_GRADES, grade_rank, normalize_grade


def completed_course_history(info: dict[str, Any]) -> dict[str, str | None]:
    """Read successful plan and valid unused attempts using one grade ordering."""
    rows = [row for row in info.get("checkMetMezuniyetList") or [] if row.get("isMet")]
    rows += [row for row in info.get("unusedSinifOgrenciList") or [] if row.get("isValidAndUnused") is True]
    completed: dict[str, str | None] = {}
    for row in rows:
        try:
            code = normalize_course_code(str(row.get("bransKodu") or ""))
        except ValueError:
            continue
        grade = normalize_grade(row.get("harfNotu"))
        if grade in FAILING_GRADES or grade in INCOMPLETE_GRADES:
            continue
        if code not in completed or grade_rank(grade) > grade_rank(completed[code]):
            completed[code] = grade or None
    return completed


def _credit_of(item: dict[str, Any]) -> float:
    raw = item.get("kredisiDec")
    if raw is None:
        raw = item.get("kredisi")
    try:
        return float(str(raw).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


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
    credit_gaps: list[dict[str, Any]] = []

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
            # ``credit`` is the plan row's credit; ``counted_credit`` is what OBS
            # counted for the course taken. A slot filled by a course of another
            # size carries two different values.
            counted = _number(item.get("sayilanKredi"))
            differs = counted is not None and counted > 0 and abs(counted - record["credit"]) > 1e-9
            if differs:
                credit_gaps.append({
                    "course_code": code or None, "slot": group or None,
                    "slot_credit": record["credit"], "counted_credit": counted,
                    "difference": round(counted - record["credit"], 2),
                })
            if group:
                # A named elective slot with a real course behind it: this is
                # the mapping the raw payload buries.
                filled_slots.append({**record, "slot": group, "slot_credit": record["credit"], "credit_differs": differs})
            completed_courses.append(record)
        else:
            if group:
                open_slots.append({
                    "slot": group,
                    "credit": _credit_of(item),
                    "slot_credit": _credit_of(item),
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

    items_total = round(
        math.fsum(course["credit"] for course in remaining_courses)
        + math.fsum(slot["credit"] for slot in open_slots), 2,
    )
    reconciliation: dict[str, Any] | None = None
    if credits_left is not None and math.isfinite(credits_left) and abs(items_total - credits_left) > 0.005:
        difference = round(items_total - credits_left, 2)
        explained = round(math.fsum(gap["difference"] for gap in credit_gaps), 2)
        names = ", ".join(
            f"{gap['course_code'] or gap['slot']} ({gap['slot_credit']:g} kredilik yerde "
            f"{gap['counted_credit']:g} kredi sayıldı)" for gap in credit_gaps
        )
        if credit_gaps and abs(explained - difference) <= 0.005:
            note = (
                f"Kalan kalemlerin kredi toplamı ({items_total:g}) ile credits_remaining ({credits_left:g}) "
                f"arasındaki {difference:g} kredilik fark şundan kaynaklanıyor: {names}."
            )
        else:
            note = (
                f"Kalan kalemlerin kredi toplamı ({items_total:g}) ile credits_remaining ({credits_left:g}) "
                f"arasında {difference:g} kredi fark var"
                + (f"; {explained:g} kredisi şundan kaynaklanıyor: {names}. Kalanı" if credit_gaps else "; fark")
                + " listedeki kalemlerle açıklanamıyor."
            )
        reconciliation = {
            "remaining_items_credit_total": items_total,
            "credits_remaining": credits_left,
            "difference": difference,
            "explained_difference": explained,
            "explained_by": credit_gaps,
            "note": note,
        }

    summary = {
        "program": plan.get("akademikProgramAdiTR"),
        "plan_title": plan.get("dersPlaniBaslik"),
        "gpa": info.get("gpa"),
        "required_gpa": plan.get("gerekliMinGPA"),
        "credits_required": required_credits,
        "credits_earned": earned_credits,
        "credits_remaining": credits_left,
        "remaining_items_credit_total": items_total,
        "credit_fields_note": (
            "credit ve slot_credit ders planındaki kalemin kredisidir; counted_credit, o kalem için "
            "alınan dersten OBS'nin saydığı kredidir. İkisi farklıysa credit_differs true olur."
        ),
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
    if reconciliation:
        summary["credit_reconciliation"] = reconciliation
    return summary
