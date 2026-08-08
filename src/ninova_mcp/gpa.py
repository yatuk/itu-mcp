"""İTÜ GPA / GANO hesaplama yardımcıları.

İTÜ 4'lük sistem harf notu → katsayı dönüşüm tablosu ve ağırlıklı
ortalama hesaplama.
"""

from __future__ import annotations

from typing import Any

# İTÜ lisans harf notu → 4'lük katsayı.
# None, "kredisi sayılabilir ama GANO'ya katılmaz" demek; 0.00 ise gerçek bir
# başarısızlık notu ve ortalamayı aşağı çeker.
LETTER_TO_GRADE: dict[str, float | None] = {
    "AA": 4.00,
    "BA+": 3.75,  # İTÜ bağıl değerlendirme yönetmeliği Tablo 1 — ara ("+") notlar
    "BA": 3.50,
    "BB+": 3.25,
    "BB": 3.00,
    "CB+": 2.75,
    "CB": 2.50,
    "CC+": 2.25,
    "CC": 2.00,
    "DC+": 1.75,
    "DC": 1.50,
    "DD+": 1.25,
    "DD": 1.00,
    "FD": 0.50,
    "FF": 0.00,
    "VF": 0.00,  # Devamsızlıktan kalma — FF gibi 0.00 sayılır, tekrar gerekir
    # Yüksek lisans
    "BL": 0.00,  # Başarısız (lisansüstü)
    "BZ": 0.00,  # Başarısız (lisansüstü)
    # Geçer / Kalır (katsayıya dahil edilmez)
    "GE": None,  # Geçer — kredi sayılır, GANO'ya katılmaz
    "KF": None,  # Kalır — kredi sayılmaz, GANO'ya katılmaz
    "IA": None,  # İzinsiz ayrıldı
    # Devam eden
    "NA": None,  # Not alınmadı
    "TR": None,  # Transfer
    "MU": None,  # Muaf
    "EK": None,  # Eksik
}

# Kredi genelde OBS'te "kredi" alanındadır; AKTS değil.
# Kayıtlı ders listesinde `kredi` genelde string gelir.


def calculate_gpa(
    courses: list[dict[str, Any]],
    *,
    projected_grades: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Compute weighted GPA (GANO) from a list of course dicts.

    Each course dict should have at least:
    - ``code`` (str): ders kodu, e.g. ``"BLG 223E"``
    - ``credit`` (float | str | None): kredi (AKTS değil)
    - ``grade`` (str | None): mevcut harf notu (yoksa projected_grades'e bakar)

    ``projected_grades``: ``{"BLG 223E": "AA", ...}`` — henüz notu belli
    olmayan veya beklenen not için elle girilmiş tahmin.
    """
    projected = {
        str(code).strip().upper(): str(grade).strip().upper()
        for code, grade in (projected_grades or {}).items()
        if str(code).strip() and str(grade).strip()
    }

    total_points = 0.0
    total_credits = 0.0
    details: list[dict[str, Any]] = []
    ff_risk: list[dict[str, Any]] = []
    ungraded: list[dict[str, Any]] = []

    for course in courses:
        code = course.get("code") or course.get("dersKodu") or "?"
        name = course.get("name") or course.get("dersAdiTR") or ""

        # Kredi (None check — 0 is a valid credit value)
        credit_raw = course.get("credit")
        if credit_raw is None:
            credit_raw = course.get("kredi")
        try:
            credit = float(str(credit_raw).replace(",", "."))
        except (ValueError, TypeError):
            credit = 0.0

        # Not
        # A what-if value is an explicit override, including for a course that
        # already has an OBS letter grade.  This matches the public tool's
        # documented behaviour and lets users compare alternative outcomes.
        projected_grade = projected.get(str(code).strip().upper())
        grade_raw = projected_grade or course.get("grade") or course.get("harfNotu")
        grade = str(grade_raw).strip().upper() if grade_raw else None

        coefficient = LETTER_TO_GRADE.get(grade) if grade else None

        if grade is None:
            ungraded.append({**course, "code": code, "name": name, "credit": credit})
            continue

        if coefficient is None:
            # GE, KF, IA, MU gibi GANO'ya katılmayan notlar
            continue

        points = credit * coefficient
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

        # Risk flags
        if grade in ("FF", "VF"):
            detail["note"] = f"{grade} — dersten kalındı, tekrar alınması gerekir."
            ff_risk.append(detail)
        elif grade in ("FD", "DD", "DD+", "DC", "DC+"):
            detail["note"] = "Düşük not — GANO'yu aşağı çekebilir."

        details.append(detail)

    gpa = round(total_points / total_credits, 2) if total_credits > 0 else None

    # GANO yorumu
    comment: str | None = None
    if gpa is not None:
        if gpa >= 3.50:
            comment = "Yüksek onur derecesinde."
        elif gpa >= 3.00:
            comment = "Onur derecesinde."
        elif gpa >= 2.00:
            comment = "Geçer seviyede (2.00 üstü)."
        elif gpa >= 1.80:
            comment = "Uyarı seviyesinde (2.00 altı); notları yükseltmeye bak."
        else:
            comment = "Kritik seviyede (1.80 altı); akademik uyarı riski var."

    return {
        "gpa": gpa,
        "total_credits": round(total_credits, 1),
        "total_points": round(total_points, 2),
        "graded_course_count": len(details),
        "ungraded_course_count": len(ungraded),
        "courses": details,
        "ungraded": ungraded,
        "ff_risk": ff_risk,
        "comment": comment,
        "scale": "4.00",
        "note": (
            "Bu hesaplama bilgi amaçlıdır; resmi GANO için OBS transkriptine bakın. "
            "GE/KF/IA/MU gibi notlar hesaba katılmaz."
        ),
    }


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
    values = {
        "current_gpa": float(current_gpa),
        "current_credits": float(current_credits),
        "target_gpa": float(target_gpa),
        "future_credits": float(future_credits),
    }
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
    feasible = required_average <= 4.0
    already_reached = required_average <= 0.0

    return {
        **values,
        "required_future_average": round(max(0.0, required_average), 2),
        "required_future_points": round(max(0.0, required_points), 2),
        "feasible_on_4_scale": feasible,
        "already_at_or_above_target": already_reached,
        "maximum_possible_gpa": round(
            (current_points + 4.0 * values["future_credits"])
            / (values["current_credits"] + values["future_credits"]),
            2,
        ),
        "note": "Bilgi amaçlı tahmindir; ders tekrarları ve özel OBS kuralları hesaba katılmaz.",
    }
