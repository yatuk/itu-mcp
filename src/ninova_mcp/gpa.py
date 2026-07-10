"""İTÜ GPA / GANO hesaplama yardımcıları.

İTÜ 4'lük sistem harf notu → katsayı dönüşüm tablosu ve ağırlıklı
ortalama hesaplama.
"""

from __future__ import annotations

from typing import Any

# İTÜ lisans harf notu → 4'lük katsayı
LETTER_TO_GRADE: dict[str, float] = {
    "AA": 4.00,
    "BA": 3.50,
    "BB": 3.00,
    "CB": 2.50,
    "CC": 2.00,
    "DC": 1.50,
    "DD": 1.00,
    "FD": 0.50,
    "FF": 0.00,
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
    projected = projected_grades or {}

    total_points = 0.0
    total_credits = 0.0
    details: list[dict[str, Any]] = []
    ff_risk: list[dict[str, Any]] = []
    ungraded: list[dict[str, Any]] = []

    for course in courses:
        code = course.get("code") or course.get("dersKodu") or "?"
        name = course.get("name") or course.get("dersAdiTR") or ""

        # Kredi
        credit_raw = course.get("credit") or course.get("kredi")
        try:
            credit = float(str(credit_raw).replace(",", "."))
        except (ValueError, TypeError):
            credit = 0.0

        # Not
        grade_raw = course.get("grade") or course.get("harfNotu") or projected.get(code)
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
        }

        # Risk flags
        if grade == "FF":
            detail["note"] = "FF — dersten kalındı, tekrar alınması gerekir."
            ff_risk.append(detail)
        elif grade in ("FD", "DD", "DC"):
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
