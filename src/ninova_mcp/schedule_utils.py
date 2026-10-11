"""Ders programı gün/saat çözümleme ve zaman çakışma kontrolü yardımcıları."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# Gün adı → haftalık indeks (Pazartesi=0)
DAY_ORDER: dict[str, int] = {
    "pazartesi": 0,
    "salı": 1,
    "sali": 1,
    "çarşamba": 2,
    "carsamba": 2,
    "perşembe": 3,
    "persembe": 3,
    "cuma": 4,
    "cumartesi": 5,
    "pazar": 6,
}

# Folded Turkish key (what ``normalize_day`` returns) → name as OBS writes it.
DAY_DISPLAY_NAMES: dict[str, str] = {
    "pazartesi": "Pazartesi",
    "sali": "Salı",
    "carsamba": "Çarşamba",
    "persembe": "Perşembe",
    "cuma": "Cuma",
    "cumartesi": "Cumartesi",
    "pazar": "Pazar",
}

_DAY_ALIASES: dict[str, str] = {
    "pazartesi": "pazartesi", "pzt": "pazartesi", "pts": "pazartesi", "pzts": "pazartesi",
    "monday": "pazartesi", "mon": "pazartesi",
    "sali": "sali", "sal": "sali", "tuesday": "sali", "tue": "sali", "tues": "sali",
    "carsamba": "carsamba", "car": "carsamba", "cars": "carsamba", "crs": "carsamba",
    "wednesday": "carsamba", "wed": "carsamba",
    "persembe": "persembe", "per": "persembe", "pers": "persembe", "prs": "persembe",
    "thursday": "persembe", "thu": "persembe", "thur": "persembe", "thurs": "persembe",
    "cuma": "cuma", "cum": "cuma", "friday": "cuma", "fri": "cuma",
    "cumartesi": "cumartesi", "cmt": "cumartesi", "cts": "cumartesi",
    "saturday": "cumartesi", "sat": "cumartesi",
    "pazar": "pazar", "paz": "pazar", "sunday": "pazar", "sun": "pazar",
}

DAY_INPUT_HELP = (
    "Pazartesi, Salı, Çarşamba, Perşembe, Cuma, Cumartesi, Pazar; "
    "kısaltmalar Pzt, Sal, Çar, Per, Cum, Cmt, Paz; "
    "İngilizce Monday, Tuesday, Wednesday, Thursday, Friday, Saturday, Sunday (Mon, Tue, ...)"
)
TIME_INPUT_HELP = "HH:MM, örneğin 10:00, 9:45, 10.00, 0945 veya yalnızca saat olarak 10"

_TIME_POINT_PATTERN = re.compile(r"(\d{1,2})(?:[:.](\d{2}))?|(\d{1,2})(\d{2})")


def _fold(value: Any) -> str:
    """Case and accent insensitive key; kept local so this module imports nothing."""
    text = re.sub(r"\s+", " ", str(value or "")).strip().casefold()
    text = text.translate(str.maketrans({"ı": "i", "İ": "i"}))
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def normalize_day(day: Any) -> str:
    """Resolve a Turkish or English weekday name or abbreviation.

    Returns the folded Turkish name (``"sali"``, ``"carsamba"``), which is a
    key of ``DAY_ORDER`` and ``DAY_DISPLAY_NAMES``. Case, Turkish accents and
    a trailing dot are ignored.
    """
    key = _DAY_ALIASES.get(_fold(day).strip(". "))
    if key is None:
        raise ValueError(f"Gün çözümlenemedi: {day!r}. Geçerli değerler: {DAY_INPUT_HELP}.")
    return key


def day_key(day: Any) -> str | None:
    """``normalize_day`` for data read from a page: unknown values give None."""
    try:
        return normalize_day(day)
    except ValueError:
        return None


def parse_time_point(value: Any) -> int:
    """Parse a time of day such as ``10``, ``10.00``, ``9:45`` or ``0945`` into minutes."""
    raw = str(value if value is not None else "").strip()
    match = _TIME_POINT_PATTERN.fullmatch(raw)
    if match is None:
        raise ValueError(f"Saat çözümlenemedi: {value!r}. Kabul edilen biçim: {TIME_INPUT_HELP}.")
    hour = int(match.group(1) or match.group(3))
    minute = int(match.group(2) or match.group(4) or 0)
    if hour > 23 or minute > 59:
        raise ValueError(
            f"Geçersiz saat: {value!r}. Saat 0-23, dakika 0-59 olmalı. Kabul edilen biçim: {TIME_INPUT_HELP}."
        )
    return hour * 60 + minute


def format_time_point(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_time_range(time_str: str) -> tuple[int, int] | None:
    """Parse a time range like ``"09:30/12:29"`` into ``(start_min, end_min)``."""
    if not time_str or "/" not in time_str:
        return None
    parts = time_str.split("/")
    if len(parts) != 2:
        return None

    def _to_min(s: str) -> int:
        s = s.strip()
        if ":" in s:
            h, m = s.split(":", 1)
            return int(h) * 60 + int(m)
        return 0

    try:
        return (_to_min(parts[0]), _to_min(parts[1]))
    except ValueError:
        return None


def check_conflicts(
    courses: list[dict[str, Any]],
) -> dict[str, Any]:
    """Check for time conflicts between a list of courses with session data.

    Each course dict must have:
    - ``crn`` (str)
    - ``code`` (str)
    - ``sessions``: list of ``{day, time}`` dicts

    Returns conflicts as pairs with overlapping session details. A course
    with no usable day and time cannot conflict; it is listed in
    ``unscheduled_crns`` so that "no conflict" is not read as "checked".
    """
    conflicts: list[dict[str, Any]] = []
    # Build flat list of (day_index, start_min, end_min, crn, code, session)
    slots: list[tuple[int, int, int, str, str, dict[str, Any]]] = []
    unscheduled: list[str] = []
    for course in courses:
        crn = str(course.get("crn", ""))
        code = course.get("code", "?")
        usable = 0
        for session in course.get("sessions") or []:
            key = day_key(session.get("day"))
            if key is None:
                continue
            time_range = parse_time_range(session.get("time") or "")
            if time_range is None:
                continue
            slots.append((DAY_ORDER[key], time_range[0], time_range[1], crn, code, session))
            usable += 1
        if not usable:
            unscheduled.append(crn)

    # Compare all pairs
    for i in range(len(slots)):
        for j in range(i + 1, len(slots)):
            d1, s1, e1, crn1, code1, sess1 = slots[i]
            d2, s2, e2, crn2, code2, sess2 = slots[j]

            if crn1 == crn2:
                continue  # Same course, different sessions: not a conflict
            if d1 != d2:
                continue  # Different days: no conflict

            # Overlap check
            if s1 < e2 and s2 < e1:
                conflicts.append({
                    "course_a": {"crn": crn1, "code": code1, "day": sess1.get("day"), "time": sess1.get("time"), "room": sess1.get("room")},
                    "course_b": {"crn": crn2, "code": code2, "day": sess2.get("day"), "time": sess2.get("time"), "room": sess2.get("room")},
                })

    result: dict[str, Any] = {
        "course_count": len(courses),
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
        "ok": len(conflicts) == 0,
        "message": (
            "Çakışma bulunamadı." if not conflicts
            else f"{len(conflicts)} çakışma tespit edildi."
        ),
        "unscheduled_crns": unscheduled,
    }
    if unscheduled:
        result["message"] += (
            f" {len(unscheduled)} dersin gün/saat bilgisi programda yok; bu dersler karşılaştırılamadı."
        )
    return result
