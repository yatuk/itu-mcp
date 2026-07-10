"""Compute human-friendly attendance / absence summaries from OBS payloads."""

from __future__ import annotations

import re
from typing import Any


def _parse_ratio_percent(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*%", value)
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def _parse_katilim_fraction(value: str | None) -> tuple[int | None, int | None, float | None]:
    """Parse strings like '30 / 42 (%71.4)'."""
    if not value:
        return None, None, None
    match = re.search(
        r"(\d+)\s*/\s*(\d+)(?:\s*\(\s*%?\s*(\d+(?:[.,]\d+)?)\s*%?\s*\))?",
        value,
    )
    if not match:
        return None, None, _parse_ratio_percent(value)
    present = int(match.group(1))
    total = int(match.group(2))
    pct = float(match.group(3).replace(",", ".")) if match.group(3) else (
        (present / total * 100.0) if total else None
    )
    return present, total, pct


def _count_marks(payload: dict[str, Any]) -> dict[str, int]:
    present = 0
    absent = 0
    unknown = 0
    weeks = 0
    root = payload.get("sinifOgrenciYoklama") if isinstance(payload, dict) else None
    if not isinstance(root, dict):
        return {
            "present_marks": 0,
            "absent_marks": 0,
            "unknown_marks": 0,
            "weeks_recorded": 0,
            "session_marks": 0,
        }
    for week in root.get("yoklamaHaftaListe") or []:
        weeks += 1
        for day in week.get("yoklamaSinifZamanListe") or []:
            for slot in day.get("yoklamaSaatListesi") or []:
                flag = slot.get("katildiMi")
                if flag is True:
                    present += 1
                elif flag is False:
                    absent += 1
                else:
                    unknown += 1
    return {
        "present_marks": present,
        "absent_marks": absent,
        "unknown_marks": unknown,
        "weeks_recorded": weeks,
        "session_marks": present + absent + unknown,
    }


def summarize_obs_attendance(
    attendance_payload: dict[str, Any] | None,
    *,
    max_absence_ratio: float = 0.30,
    course: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a short absence-risk summary.

    ITU practice often treats roughly 30% absence as the FF boundary for many
    courses; the exact rule can vary by course, so max_absence_ratio is configurable.
    """
    if max_absence_ratio <= 0 or max_absence_ratio >= 1:
        max_absence_ratio = 0.30

    if not attendance_payload:
        return {
            "available": False,
            "message": "Yoklama verisi yok.",
            "course": course,
        }

    root = attendance_payload.get("sinifOgrenciYoklama")
    if root is None:
        return {
            "available": False,
            "message": attendance_payload.get("resultMessage")
            or "Bu sinif icin yoklama kaydi bulunamadi.",
            "result_code": attendance_payload.get("resultCode"),
            "course": course,
        }

    marks = _count_marks(attendance_payload)
    present_str = root.get("katilim") if isinstance(root, dict) else None
    genel = root.get("genelKatilim") if isinstance(root, dict) else None
    present, total, attend_pct = _parse_katilim_fraction(
        present_str if isinstance(present_str, str) else None
    )
    if present is None:
        present = marks["present_marks"]
    if total is None:
        total = marks["session_marks"] or None
    if attend_pct is None and total:
        attend_pct = present / total * 100.0

    absent = marks["absent_marks"]
    if total is not None and present is not None and total >= present:
        # Prefer fraction-derived absence when consistent.
        derived_absent = total - present
        if derived_absent >= 0:
            absent = derived_absent

    absence_pct = None
    if total and total > 0:
        absence_pct = round(absent / total * 100.0, 2)

    max_absent_allowed = int(total * max_absence_ratio) if total else None
    remaining_absences = (
        max(0, max_absent_allowed - absent) if max_absent_allowed is not None else None
    )
    over_limit = (
        absent > max_absent_allowed if max_absent_allowed is not None else None
    )

    if over_limit:
        risk = "critical"
        risk_tr = "kritik"
    elif remaining_absences is not None and total:
        # within last ~5% of budget
        if remaining_absences <= max(1, int(0.05 * total)):
            risk = "warning"
            risk_tr = "dikkat"
        else:
            risk = "ok"
            risk_tr = "uygun"
    else:
        risk = "unknown"
        risk_tr = "bilinmiyor"

    summary_tr = None
    if total is not None:
        summary_tr = (
            f"{present}/{total} oturuma katildin "
            f"(devamsizlik %{absence_pct if absence_pct is not None else '?'}). "
        )
        if max_absent_allowed is not None:
            summary_tr += (
                f"Yaklasik %{int(max_absence_ratio * 100)} sinirina gore "
                f"en fazla {max_absent_allowed} devamsizlik; "
                f"kalan hak {remaining_absences}."
            )
            if over_limit:
                summary_tr += " Sinir asilmis olabilir; hocanin kurali dogrula."
        if genel:
            summary_tr += f" Genel katilim: {genel}."

    return {
        "available": True,
        "course": course,
        "raw_labels": {
            "katilim": present_str,
            "genel_katilim": genel,
        },
        "present": present,
        "absent": absent,
        "total_sessions": total,
        "attendance_percent": round(attend_pct, 2) if attend_pct is not None else None,
        "absence_percent": absence_pct,
        "max_absence_ratio_assumed": max_absence_ratio,
        "max_absences_allowed": max_absent_allowed,
        "remaining_absences": remaining_absences,
        "over_limit": over_limit,
        "risk": risk,
        "risk_tr": risk_tr,
        "marks": marks,
        "summary_tr": summary_tr,
        "disclaimer_tr": (
            "Devamsizlik siniri derse gore degisebilir; bu ozet varsayilan "
            f"%{int(max_absence_ratio * 100)} kurali kullanir."
        ),
    }
