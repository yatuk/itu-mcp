"""Ders programı zaman çakışma kontrolü yardımcıları."""

from __future__ import annotations

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

    return (_to_min(parts[0]), _to_min(parts[1]))


def check_conflicts(
    courses: list[dict[str, Any]],
) -> dict[str, Any]:
    """Check for time conflicts between a list of courses with session data.

    Each course dict must have:
    - ``crn`` (str)
    - ``code`` (str)
    - ``sessions``: list of ``{day, time}`` dicts

    Returns conflicts as pairs with overlapping session details.
    """
    conflicts: list[dict[str, Any]] = []
    # Build flat list of (day_index, start_min, end_min, crn, code, session)
    slots: list[tuple[int, int, int, str, str, dict[str, Any]]] = []
    for course in courses:
        crn = str(course.get("crn", ""))
        code = course.get("code", "?")
        for session in course.get("sessions") or []:
            day_name = (session.get("day") or "").strip().lower()
            day_idx = DAY_ORDER.get(day_name)
            if day_idx is None:
                continue
            time_range = parse_time_range(session.get("time") or "")
            if time_range is None:
                continue
            slots.append((day_idx, time_range[0], time_range[1], crn, code, session))

    # Compare all pairs
    for i in range(len(slots)):
        for j in range(i + 1, len(slots)):
            d1, s1, e1, crn1, code1, sess1 = slots[i]
            d2, s2, e2, crn2, code2, sess2 = slots[j]

            if crn1 == crn2:
                continue  # Same course, different sessions — not a conflict
            if d1 != d2:
                continue  # Different days — no conflict

            # Overlap check
            if s1 < e2 and s2 < e1:
                conflicts.append({
                    "course_a": {"crn": crn1, "code": code1, "day": sess1.get("day"), "time": sess1.get("time"), "room": sess1.get("room")},
                    "course_b": {"crn": crn2, "code": code2, "day": sess2.get("day"), "time": sess2.get("time"), "room": sess2.get("room")},
                })

    return {
        "course_count": len(courses),
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
        "ok": len(conflicts) == 0,
        "message": (
            "Çakışma bulunamadı." if not conflicts
            else f"{len(conflicts)} çakışma tespit edildi."
        ),
    }
