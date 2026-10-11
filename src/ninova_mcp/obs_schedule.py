"""The weekly schedule of the OBS class calendar as a short, sorted list.

OBS sends one row per class with its meetings nested, times as minutes after
midnight and no day order. ``weekly_schedule`` turns that into one entry per
meeting: day, start and end as HH:MM, course code, title, CRN and place,
sorted by day and start time.
"""

from __future__ import annotations

from typing import Any

from .parsing import normalize_lookup_text
from .registration_draft import _calendar_code, _clock, _label, _room

_DAY_ORDER = {
    "pazartesi": 1, "monday": 1, "sali": 2, "tuesday": 2, "carsamba": 3, "wednesday": 3,
    "persembe": 4, "thursday": 4, "cuma": 5, "friday": 5, "cumartesi": 6, "saturday": 6,
    "pazar": 7, "sunday": 7,
}
_ROW_LISTS = ("kayitSinifResultList", "dersTakvimi", "dersTakvimiList", "takvim")
_MEETING_LISTS = ("sinifYerZaman", "sinifYerZamanList", "yerZamanList")
_UNSORTED = 99


def _time(value: Any) -> str | None:
    """Minutes after midnight, or a clock text OBS already formatted."""
    if isinstance(value, str):
        text = value.strip()
        if len(text) >= 4 and text[:1].isdigit() and ":" in text:
            hour, _, rest = text.partition(":")
            if hour.isdigit() and rest[:2].isdigit() and int(hour) < 24 and int(rest[:2]) < 60:
                return f"{int(hour):02d}:{rest[:2]}"
        return None
    return _clock(value)


def _rows(payload: Any) -> list[dict[str, Any]] | None:
    """The class rows of the payload, or None when no list of them is found."""
    if isinstance(payload, list):
        candidates: list[Any] = [payload]
    elif isinstance(payload, dict):
        candidates = [payload[key] for key in _ROW_LISTS if key in payload]
        candidates += [value for key, value in payload.items() if key not in _ROW_LISTS and isinstance(value, list)]
    else:
        return None
    for rows in candidates:
        if isinstance(rows, list) and all(isinstance(row, dict) for row in rows):
            return rows
    return None


def _meetings(row: dict[str, Any]) -> list[dict[str, Any]]:
    for key in _MEETING_LISTS:
        nested = row.get(key)
        if isinstance(nested, list):
            return [entry for entry in nested if isinstance(entry, dict)]
    # A flat payload: the class row is itself one meeting.
    return [row] if "baslangicSaati" in row else []


def weekly_schedule(payload: Any) -> tuple[list[dict[str, Any]], bool]:
    """``(meetings, recognized)``: the meetings sorted by day and start time.

    ``recognized`` is False when the payload holds class rows but no meeting
    with a day or a time could be read from them, so the caller can fall back
    to the source rows instead of presenting an empty week.
    """
    rows = _rows(payload)
    if rows is None:
        return [], False
    meetings: list[tuple[tuple[int, str, str], dict[str, Any]]] = []
    for row in rows:
        code = _calendar_code(row)
        title = _label(row.get("dersAdiTR")) or _label(row.get("dersAdi")) or _label(row.get("dersAdiEN"))
        crn = _label(row.get("crn"))
        for entry in _meetings(row):
            day = _label(entry.get("gunAdiTR")) or _label(entry.get("gunAdi")) or _label(entry.get("gunAdiEN"))
            start, end = _time(entry.get("baslangicSaati")), _time(entry.get("bitisSaati"))
            if not day and not start:
                continue
            place = _room(entry)
            meeting = {
                "day": day, "start": start, "end": end,
                "course_code": code, "title": title, "crn": crn,
                "building": _label(entry.get("binaAdi")), "room": place["room"],
                "campus": _label(entry.get("kampusAdi")),
            }
            order = _DAY_ORDER.get(normalize_lookup_text(day or ""), _UNSORTED)
            meetings.append((
                (order, start or "99:99", code or ""),
                {key: value for key, value in meeting.items() if value is not None},
            ))
    meetings.sort(key=lambda item: item[0])
    return [meeting for _key, meeting in meetings], bool(meetings) or not rows
