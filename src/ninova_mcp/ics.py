"""A small iCalendar (RFC 5545) writer for class and exam events.

Only what a phone or desktop calendar needs to import a timetable: VEVENTs in
Europe/Istanbul local time, an optional weekly recurrence with an end, stable
UIDs, CRLF line endings, folding at 75 octets and text escaping.

The timezone is written out as a fixed VTIMEZONE block instead of being read
from the system zone database. Türkiye has been on UTC+3 all year since 2016,
so the block is a constant, and the export behaves the same on a Windows
machine without ``tzdata`` as it does anywhere else.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

TZID = "Europe/Istanbul"
UTC_OFFSET = timedelta(hours=3)
PRODID = "-//itu-mcp//takvim//TR"
UID_DOMAIN = "itu-mcp"
MAX_LINE_OCTETS = 75
# RFC 5545 BYDAY codes, indexed like ``schedule_utils.DAY_ORDER`` (Monday=0).
BYDAY_CODES = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")

_VTIMEZONE_LINES = (
    "BEGIN:VTIMEZONE",
    f"TZID:{TZID}",
    "BEGIN:STANDARD",
    "DTSTART:19700101T000000",
    "TZOFFSETFROM:+0300",
    "TZOFFSETTO:+0300",
    "TZNAME:+03",
    "END:STANDARD",
    "END:VTIMEZONE",
)
_CONTROL_CHARS = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")


@dataclass(frozen=True)
class IcsEvent:
    """One VEVENT. ``start`` and ``end`` are naive Europe/Istanbul local times."""

    uid: str
    summary: str
    start: datetime
    end: datetime | None = None
    location: str | None = None
    description: str | None = None
    # Weekly recurrence: both set, or both left out for a single event.
    weekly_byday: str | None = None
    weekly_until: date | None = None


def escape_text(value: str) -> str:
    """Escape a TEXT value: backslash, semicolon, comma and line breaks."""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_CHARS.sub(" ", text)
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def fold_line(line: str) -> list[str]:
    """Split one content line into physical lines of at most 75 octets.

    Lengths are counted in UTF-8 octets and a multi-byte character is never
    cut in half. Continuation lines start with one space, which counts
    towards their 75 octets.
    """
    parts: list[str] = []
    current = ""
    current_octets = 0
    for char in line:
        size = len(char.encode("utf-8"))
        if current_octets + size > MAX_LINE_OCTETS:
            parts.append(current)
            current = " "
            current_octets = 1
        current += char
        current_octets += size
    parts.append(current)
    return parts


def stable_uid(*parts: object) -> str:
    """A UID that depends only on the identifying data, so re-imports update."""
    key = "|".join(str(part).strip() for part in parts)
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    return f"{digest}@{UID_DOMAIN}"


def first_weekday_on_or_after(day: date, weekday: int) -> date:
    """The first date on or after ``day`` that falls on ``weekday`` (Monday=0)."""
    return day + timedelta(days=(weekday - day.weekday()) % 7)


def _local_stamp(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%S")


def _utc_stamp(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%SZ")


def _until_stamp(last_day: date) -> str:
    """End of ``last_day`` in Istanbul, written in UTC.

    RFC 5545 requires UNTIL in UTC whenever DTSTART carries a TZID.
    """
    return _utc_stamp(datetime.combine(last_day, time(23, 59, 59)) - UTC_OFFSET)


def _event_lines(event: IcsEvent, stamp: str) -> list[str]:
    lines = [
        "BEGIN:VEVENT",
        f"UID:{event.uid}",
        f"DTSTAMP:{stamp}",
        f"DTSTART;TZID={TZID}:{_local_stamp(event.start)}",
    ]
    if event.end is not None:
        lines.append(f"DTEND;TZID={TZID}:{_local_stamp(event.end)}")
    if event.weekly_byday and event.weekly_until:
        lines.append(
            f"RRULE:FREQ=WEEKLY;UNTIL={_until_stamp(event.weekly_until)};BYDAY={event.weekly_byday}"
        )
    lines.append(f"SUMMARY:{escape_text(event.summary)}")
    if event.location:
        lines.append(f"LOCATION:{escape_text(event.location)}")
    if event.description:
        lines.append(f"DESCRIPTION:{escape_text(event.description)}")
    lines.append("END:VEVENT")
    return lines


def build_calendar(
    events: list[IcsEvent],
    *,
    name: str,
    now: datetime | None = None,
) -> str:
    """Serialize ``events`` into one VCALENDAR document with CRLF line endings."""
    moment = now or datetime.now(UTC)
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC)
    stamp = _utc_stamp(moment)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape_text(name)}",
        f"X-WR-TIMEZONE:{TZID}",
        *_VTIMEZONE_LINES,
    ]
    for event in events:
        lines.extend(_event_lines(event, stamp))
    lines.append("END:VCALENDAR")
    physical = [part for line in lines for part in fold_line(line)]
    return "\r\n".join(physical) + "\r\n"
