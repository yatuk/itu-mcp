"""Ninova attendance grid and its week calendar."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from bs4 import BeautifulSoup, Tag

from .parsing_common import TURKIYE_TZ, clean_text, make_soup, normalize_lookup_text
from .parsing_page import _find_table_by_headers, _table_after_heading, parse_html_page


def _attendance_student_name(soup: BeautifulSoup) -> str | None:
    student_table = _find_table_by_headers(soup, ["Öğrenci"])
    if student_table is None:
        return None
    rows = student_table.find_all("tr")
    if len(rows) < 2:
        return None
    cells = rows[1].find_all("td")
    return clean_text(cells[0].get_text(" ", strip=True)) if cells else None


def _find_attendance_table(soup: BeautifulSoup) -> Tag | None:
    """The first table whose first header is "Hafta"."""
    for table in soup.find_all("table"):
        headers = [clean_text(cell.get_text(" ", strip=True)) for cell in table.find_all("th")]
        if headers and normalize_lookup_text(headers[0]) == "hafta":
            return table
    return None


def _attendance_source_system(soup: BeautifulSoup, attendance_table: Tag | None) -> str | None:
    # Ninova pages show attendance from two independent systems (native Ninova
    # and Kepler) under separate headings; instructors may use either one, and
    # the page itself warns the two can disagree. Report which table this data
    # actually came from so callers don't mistake Kepler data for a native
    # Ninova record.
    if attendance_table is None:
        return None
    if attendance_table is _table_after_heading(soup, "Kepler Yoklama Bilgileri"):
        return "kepler"
    if attendance_table is _table_after_heading(soup, "Ninova Yoklama Bilgileri"):
        return "ninova"
    return None


def _attendance_session(column_name: str, value: str, node: Tag) -> dict[str, Any]:
    """The marks of one session cell; ``zero_count`` is settled by the week it belongs to."""
    marks = re.findall(r"[01]", value)
    session: dict[str, Any] = {
        "name": column_name,
        "value": value,
        "present_count": marks.count("1"),
        "zero_count": marks.count("0"),
    }
    # Tooltips or image labels, when the page has them, are passed
    # on untouched: they may say what a zero means.
    hints = [
        clean_text(str(tag.get(attribute)))
        for tag in [node, *node.find_all(True)]
        for attribute in ("title", "alt")
        if tag.get(attribute)
    ]
    if hints:
        session["hints"] = list(dict.fromkeys(hints))
    return session


def _attendance_week(headers: list[str], row: Tag) -> dict[str, Any] | None:
    """One row of the grid, or None for a row without cells.

    The grid prints 0 both for "absent" and for a session nobody has entered
    yet (a whole term of future weeks shows as zeros). A zero is therefore
    counted as an absence only in a week that also holds a 1, which shows
    attendance was taken that week. Every other zero is "unmarked or absent".
    """
    nodes = row.find_all("td")
    cells = [clean_text(cell.get_text(" ", strip=True)) for cell in nodes]
    if not cells:
        return None
    sessions = [
        _attendance_session(column_name, value, node)
        for column_name, value, node in zip(headers[1:], cells[1:], nodes[1:], strict=False)
    ]
    recorded = any(session["present_count"] for session in sessions)
    for session in sessions:
        zeros = session.pop("zero_count")
        session["absent_count"] = zeros if recorded else 0
        session["unmarked_or_absent_count"] = 0 if recorded else zeros
    return {
        "week": cells[0],
        "status": "recorded" if recorded else "unmarked_or_absent",
        "sessions": sessions,
    }


def extract_attendance(html: str, page_url: str, base_url: str) -> dict[str, Any]:
    soup = make_soup(html)
    page = parse_html_page(page_url, html, base_url=base_url)

    attendance_table = _find_attendance_table(soup)
    disclaimer_tag = soup.find(class_="uyari")

    headers: list[str] = []
    weeks: list[dict[str, Any]] = []
    if attendance_table is not None:
        headers = [clean_text(cell.get_text(" ", strip=True)) for cell in attendance_table.find_all("th")]
        parsed_rows = (_attendance_week(headers, row) for row in attendance_table.find_all("tr")[1:])
        weeks = [week for week in parsed_rows if week is not None]

    result: dict[str, Any] = {
        "url": page["url"],
        "student_name": _attendance_student_name(soup),
        "source_system": _attendance_source_system(soup, attendance_table),
        "disclaimer": clean_text(disclaimer_tag.get_text(" ", strip=True)) if disclaimer_tag else None,
        "headers": headers,
        "count": len(weeks),
        "weeks": weeks,
    }
    _total_attendance(result)
    return result


_WEEK_NUMBER_RE = re.compile(r"(\d{1,2})")


def _total_attendance(result: dict[str, Any]) -> None:
    sessions = [session for week in result["weeks"] for session in week["sessions"]]
    result["total_present_marks"] = sum(session["present_count"] for session in sessions)
    result["total_absent_marks"] = sum(session["absent_count"] for session in sessions)
    result["total_unmarked_or_absent_marks"] = sum(session["unmarked_or_absent_count"] for session in sessions)
    future = sum(session.get("future_count", 0) for session in sessions)
    if future or "total_future_marks" in result:
        result["total_future_marks"] = future
    result["absence_counts_reliable"] = result["total_unmarked_or_absent_marks"] == 0
    result.pop("note", None)
    if not result["absence_counts_reliable"]:
        result["note"] = (
            "The attendance grid shows 0 both for an absence and for a session that has not been "
            "entered. Zeros in weeks without any recorded 1 are counted in "
            "total_unmarked_or_absent_marks, not as absences; total_absent_marks covers only weeks "
            "where attendance was demonstrably taken, so it can be too low. Do not report "
            "unmarked_or_absent or future marks as absences."
        )


def apply_attendance_calendar(result: dict[str, Any], *, term_start: date | None, today: date | None = None) -> dict[str, Any]:
    """Date the weeks from the term start and take future weeks out of the counts.

    Week ``N`` is taken to start ``7 * (N - 1)`` days after ``term_start``. A
    week that starts after ``today`` cannot hold an absence, whatever the grid
    prints, so its marks move to ``future_count``.
    """
    result["term_start"] = term_start.isoformat() if term_start else None
    if term_start is None:
        return result
    reference = today or datetime.now(tz=TURKIYE_TZ).date()
    for week in result["weeks"]:
        number = _WEEK_NUMBER_RE.search(str(week.get("week") or ""))
        if not number or int(number.group(1)) < 1:
            continue
        week_start = term_start + timedelta(days=7 * (int(number.group(1)) - 1))
        week["week_start"] = week_start.isoformat()
        if week_start > reference:
            week["status"] = "future"
            for session in week["sessions"]:
                session["future_count"] = (
                    session["present_count"] + session["absent_count"] + session["unmarked_or_absent_count"]
                )
                session["present_count"] = session["absent_count"] = session["unmarked_or_absent_count"] = 0
        else:
            for session in week["sessions"]:
                session.setdefault("future_count", 0)
    result["total_future_marks"] = 0
    _total_attendance(result)
    return result
