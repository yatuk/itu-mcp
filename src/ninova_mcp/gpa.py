"""İTÜ GPA / GANO hesaplama yardımcıları.

İTÜ 4'lük sistem harf notu → katsayı dönüşüm tablosu ve ağırlıklı
ortalama hesaplama.
"""

from __future__ import annotations

import math
from typing import Any

from .archive import normalize_course_code
from .grading import LETTER_TO_GRADE, normalize_grade

# Kredi genelde OBS'te "kredi" alanındadır; AKTS değil.
# Kayıtlı ders listesinde `kredi` genelde string gelir.


def course_key(value: Any) -> str:
    try:
        return normalize_course_code(str(value or ""))
    except ValueError:
        # Keep support for callers using display labels instead of OBS codes.
        return " ".join(str(value or "").upper().split())


def parse_credit(value: Any) -> float | None:
    """Parse a finite nonnegative credit value, preserving a genuine zero."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _json_safe(value: Any) -> Any:
    """Do not leak nonfinite backend provenance into a JSON tool response."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def calculate_gpa(
    courses: list[dict[str, Any]],
    *,
    projected_grades: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Compute the credit-weighted average of the supplied course records.

    Each course dict should have at least:
    - ``code`` (str): ders kodu, e.g. ``"BLG 223E"``
    - ``credit`` (float | str | None): kredi (AKTS değil)
    - ``grade`` (str | None): mevcut harf notu (yoksa projected_grades'e bakar)

    ``projected_grades``: ``{"BLG 223E": "AA", ...}`` — henüz notu belli
    olmayan veya beklenen not için elle girilmiş tahmin.
    """
    projected: dict[str, str] = {}
    for code, grade in (projected_grades or {}).items():
        key, normalized_grade = course_key(code), normalize_grade(grade)
        if not key or normalized_grade not in LETTER_TO_GRADE:
            raise ValueError("Projected grades require a course code and a recognized İTÜ grade.")
        if key in projected and projected[key] != normalized_grade:
            raise ValueError(f"Conflicting projected grades for {key}.")
        projected[key] = normalized_grade
    used_projections: set[str] = set()

    total_points = 0.0
    total_credits = 0.0
    details: list[dict[str, Any]] = []
    ff_risk: list[dict[str, Any]] = []
    ungraded: list[dict[str, Any]] = []
    unrecognized_grades: list[dict[str, Any]] = []
    invalid_credits: list[dict[str, Any]] = []
    excluded_courses: list[dict[str, Any]] = []
    eligible_count = 0

    for course in courses:
        code = course.get("code") or course.get("dersKodu") or "?"
        name = course.get("name") or course.get("dersAdiTR") or ""

        # Kredi (None check — 0 is a valid credit value)
        credit_raw = course.get("credit")
        if credit_raw is None:
            credit_raw = course.get("kredi")
        credit = parse_credit(credit_raw)

        # Not
        # A what-if value is an explicit override, including for a course that
        # already has an OBS letter grade.  This matches the public tool's
        # documented behaviour and lets users compare alternative outcomes.
        key = course_key(code)
        projected_grade = projected.get(key)
        if projected_grade is not None:
            used_projections.add(key)
        grade_raw = projected_grade if projected_grade is not None else course.get("grade")
        if grade_raw is None or grade_raw == "":
            grade_raw = course.get("harfNotu")
        grade = (str(grade_raw).strip().upper() or None) if grade_raw is not None else None

        coefficient = LETTER_TO_GRADE.get(grade) if grade else None

        if grade is None:
            ungraded.append({**course, "code": code, "name": name, "credit": credit,
                             "credit_status": "valid" if credit is not None else "missing" if credit_raw is None or str(credit_raw).strip() == "" else "invalid"})
            continue

        if grade not in LETTER_TO_GRADE:
            unrecognized_grades.append({"code": code, "grade": grade})
            continue

        if coefficient is None:
            # GE, KF, IA, MU gibi GANO'ya katılmayan notlar
            excluded_courses.append({"code": code, "grade": grade, "credit": credit, "reason": "grade_excluded_from_gpa"})
            continue

        eligible_count += 1
        failure_note = f"{grade} notu bildirildi. Dersin tekrar veya yerine alma koşulları OBS kurallarına bağlıdır."
        if credit is None:
            reason = "missing_credit" if credit_raw is None or str(credit_raw).strip() == "" else "invalid_credit"
            invalid_credits.append({"code": code, "grade": grade, "reason": reason, "raw_credit": credit_raw})
            if grade in ("FF", "VF"):
                ff_risk.append({"code": code, "grade": grade, "credit": None, "note": failure_note})
            continue
        points = credit * coefficient
        if not all(math.isfinite(value) for value in (points, total_credits + credit, total_points + points)):
            invalid_credits.append({"code": code, "grade": grade, "reason": "credit_arithmetic_overflow", "raw_credit": credit_raw})
            continue
        total_credits += credit
        total_points += points

        detail = {
            "code": code,
            "name": name,
            "credit": credit,
            "grade": grade,
            "coefficient": coefficient,
            "points": round(points, 2),
            "projected": projected_grade is not None,
        }
        for field in ("recorded_credit", "plan_credit", "counted_credit", "credit_source", "grade_source"):
            if field in course:
                detail[field] = course[field]

        # Risk flags
        if grade in ("FF", "VF"):
            detail["note"] = failure_note
            ff_risk.append(detail)
        elif grade in ("FD", "DD", "DD+", "DC", "DC+"):
            detail["note"] = "Düşük not. Bu dersin ortalamaya katkısı kredi ağırlığına bağlıdır."

        details.append(detail)

    known_gpa = round(total_points / total_credits, 2) if total_credits > 0 else None
    complete = not (invalid_credits or unrecognized_grades or ungraded)
    unused_projections = sorted(set(projected) - used_projections)
    return _json_safe({
        "gpa": known_gpa if complete else None,
        "known_courses_gpa": known_gpa,
        "calculation_complete": complete,
        "calculation_status": "incomplete" if not complete else "complete" if known_gpa is not None else "no_gpa_credits",
        "average_scope": "supplied_courses",
        "input_course_count": len(courses),
        "gpa_eligible_course_count": eligible_count,
        "covered_course_count": len(details),
        "excluded_course_count": len(excluded_courses),
        "total_credits": round(total_credits, 1),
        "total_points": round(total_points, 2),
        "graded_course_count": len(details),
        "ungraded_course_count": len(ungraded),
        "courses": details,
        "ungraded": ungraded,
        "unrecognized_grades": unrecognized_grades,
        "invalid_credits": invalid_credits,
        "excluded_courses": excluded_courses,
        "unused_projected_courses": unused_projections,
        "projection_complete": not unused_projections,
        "applied_projection_count": len(used_projections),
        "ff_risk": ff_risk,
        "comment": None,
        "scale": "4.00",
        "note": (
            "Hesap yalnız verilen dersleri kapsar. Eksik veya geçersiz not/kredi varsa "
            "gpa verilmez; known_courses_gpa ve toplamlar yalnız doğrulanan kayıtları kapsar. "
            "Bu sonuç onur derecesi, akademik durum veya kümülatif GANO belirlemez. "
            "BL/BZ/T/E/M ve diğer katsayısız notlar hesaba katılmaz."
        ),
    })


def calculate_target_gpa(
    *,
    current_gpa: float,
    current_credits: float,
    target_gpa: float,
    future_credits: float,
) -> dict[str, Any]:
    """Calculate the average required over future credits to reach a target.

    This is a planning estimate.  It deliberately works from aggregate GPA
    points so it can be used without exposing a transcript.
    """
    values = {}
    for name, raw in {
        "current_gpa": current_gpa, "current_credits": current_credits,
        "target_gpa": target_gpa, "future_credits": future_credits,
    }.items():
        if isinstance(raw, bool):
            raise ValueError(f"{name} must be a finite number")
        try:
            number = float(raw)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number") from exc
        if not math.isfinite(number):
            raise ValueError(f"{name} must be a finite number")
        values[name] = number
    if not 0.0 <= values["current_gpa"] <= 4.0:
        raise ValueError("current_gpa must be between 0.00 and 4.00")
    if not 0.0 <= values["target_gpa"] <= 4.0:
        raise ValueError("target_gpa must be between 0.00 and 4.00")
    if values["current_credits"] < 0:
        raise ValueError("current_credits cannot be negative")
    if values["future_credits"] <= 0:
        raise ValueError("future_credits must be greater than zero")

    current_points = values["current_gpa"] * values["current_credits"]
    target_points = values["target_gpa"] * (
        values["current_credits"] + values["future_credits"]
    )
    required_points = target_points - current_points
    required_average = required_points / values["future_credits"]
    maximum_possible_gpa = (
        (current_points + 4.0 * values["future_credits"])
        / (values["current_credits"] + values["future_credits"])
    )
    if not all(math.isfinite(value) for value in (current_points, target_points, required_points, required_average, maximum_possible_gpa)):
        raise ValueError("Credit arithmetic exceeds the supported finite numeric range")
    feasible = required_average <= 4.0

    return {
        **values,
        "required_future_average": round(max(0.0, required_average), 2),
        "required_future_points": round(max(0.0, required_points), 2),
        "feasible_on_4_scale": feasible,
        "already_at_or_above_target": values["current_gpa"] >= values["target_gpa"],
        "target_reached_with_zero_future_points": required_average <= 0.0,
        "maximum_possible_gpa": round(maximum_possible_gpa, 2),
        "note": "Bilgi amaçlı tahmindir; ders tekrarları ve özel OBS kuralları hesaba katılmaz.",
    }
