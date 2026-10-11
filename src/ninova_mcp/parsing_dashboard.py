"""Ninova dashboard: the course list and the recent announcement, assignment and message tables."""

from __future__ import annotations

import re
from datetime import date
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import Tag

from .parsing_common import (
    COURSE_CODE_RE,
    clean_text,
    infer_yearless_datetime,
    is_course_root,
    is_internal_ninova_url,
    make_soup,
)
from .parsing_page import extract_named_table


def _first_yearless_date(row: dict[str, Any], today: date | None) -> str | None:
    """ISO form of the first value of the row that is a date without a year."""
    for value in row.values():
        iso = infer_yearless_datetime(value, today=today) if isinstance(value, str) else None
        if iso:
            return iso
    return None


def _with_inferred_dates(rows: list[Any], today: date | None) -> list[Any]:
    """Add ``date_iso`` to dashboard rows whose date has no year.

    A header-less two-cell row (text, date) becomes a small record so the date
    is a field of its own. Start/end ranges are left alone: the nearest-year
    rule is not safe for a window that can span most of a year.
    """
    result: list[Any] = []
    for row in rows:
        if isinstance(row, list) and len(row) == 2 and infer_yearless_datetime(row[1], today=today):
            row = {"text": row[0], "date": row[1]}
        if isinstance(row, dict) and "date_iso" not in row:
            iso = _first_yearless_date(row, today)
            if iso:
                row = {**row, "date_iso": iso, "date_year_inferred": True}
        result.append(row)
    return result


def _context_from_anchor(anchor: Tag, page_url: str, base_url: str) -> str:
    """Short text around a course link, never reaching into a neighbouring course.

    The walk stops before a container that also holds another course, the
    same bound ``_course_own_text`` uses, so a code or CRN that belongs to
    the neighbour cannot end up in this course's context.
    """
    group = _course_group(urljoin(page_url, anchor["href"]))
    node: Tag | None = anchor.parent if isinstance(anchor.parent, Tag) else anchor
    for _ in range(4):
        if node is None or _holds_another_course(node, group, page_url, base_url):
            break
        text = clean_text(node.get_text(" ", strip=True))
        if text and len(text) < 300 and text != clean_text(anchor.get_text(" ", strip=True)):
            return text
        node = node.parent if isinstance(node.parent, Tag) else None
    return clean_text(anchor.get_text(" ", strip=True))


def _extract_course_title(anchor: Tag) -> str:
    anchor_text = clean_text(anchor.get_text(" ", strip=True))
    script = anchor.find_next_sibling("script")
    if script:
        script_text = script.string or script.get_text(" ", strip=True)
        match = re.search(r"font-weight:bold;\">([^<]+)<", script_text)
        if match:
            return clean_text(match.group(1))
    return anchor_text


_YEAR_LIKE_NUMBER_RE = re.compile(r"(?:19|20)\d{2}")
_COURSE_GROUP_RE = re.compile(r"/Sinif/(\d+)\.\d+/?$")
_CRN_LABEL_RE = re.compile(r"\bCRN\s*:?\s*(\d{3,6})\b", re.I)


def _course_group(url: str) -> str | None:
    """First id of ``/Sinif/<course>.<section>``: shared by sections of one course."""
    match = _COURSE_GROUP_RE.search(urlparse(url).path)
    return match.group(1) if match else None


def _is_year_like_code(code: str) -> bool:
    """``CEN 2025`` reads as a branch followed by a year, not as a course number."""
    number = re.sub(r"^[A-Z]+\s*", "", code)
    return bool(_YEAR_LIKE_NUMBER_RE.fullmatch(number))


def _pick_code(text: str) -> tuple[str | None, bool]:
    """Return ``(code, uncertain)`` from text, never asserting a year-like number."""
    candidates = [clean_text(match) for match in COURSE_CODE_RE.findall(text)]
    for candidate in candidates:
        if not _is_year_like_code(candidate):
            return candidate, False
    return None, bool(candidates)


def _is_course_anchor(tag: Tag, page_url: str, base_url: str) -> bool:
    absolute = urljoin(page_url, tag.get("href") or "")
    return is_internal_ninova_url(absolute, base_url) and is_course_root(absolute)


def _holds_another_course(node: Tag, group: str | None, page_url: str, base_url: str) -> bool:
    """Whether the element contains a link to a course other than ``group``."""
    return any(
        _is_course_anchor(other, page_url, base_url) and _course_group(urljoin(page_url, other["href"])) != group
        for other in node.find_all("a", href=True)
    )


def _course_own_text(anchor: Tag, page_url: str, base_url: str) -> str:
    """Text that belongs to the anchor's course but to none of its section links.

    Section links carry free-text labels ("CEN 2025 Summer Internship"), so
    the course code is looked for outside them. The walk goes up from the
    anchor and stops before a container that also holds another course.
    """
    group = _course_group(urljoin(page_url, anchor["href"]))

    def is_course_anchor(tag: Tag) -> bool:
        return _is_course_anchor(tag, page_url, base_url)

    def outside_section_links(piece: Any, container: Tag) -> bool:
        for parent in piece.parents:
            if parent.name in {"script", "style"} or (parent.name == "a" and is_course_anchor(parent)):
                return False
            if parent is container:
                break
        return True

    node = anchor.parent
    for _ in range(5):
        if not isinstance(node, Tag) or node.name in {"body", "html", "[document]"}:
            break
        if _holds_another_course(node, group, page_url, base_url):
            break
        text = clean_text(" ".join(
            str(piece) for piece in node.find_all(string=True) if outside_section_links(piece, node)
        ))
        if COURSE_CODE_RE.search(text):
            return text
        node = node.parent
    return ""


def _course_code(anchor: Tag, context: str, page_url: str, base_url: str) -> tuple[str | None, str | None, bool]:
    """``(code, source, uncertain)`` for a course link.

    The text next to the section links is tried first (``course_element``),
    then the link's own context (``section_label``). ``uncertain`` is set when
    a branch followed by a year was seen and refused as a code.
    """
    code, uncertain = _pick_code(_course_own_text(anchor, page_url, base_url))
    if code:
        return code, "course_element", uncertain
    code, label_uncertain = _pick_code(context)
    return code, "section_label" if code else None, uncertain or label_uncertain


def _course_row(anchor: Tag, absolute: str, page_url: str, base_url: str) -> dict[str, Any]:
    context = _context_from_anchor(anchor, page_url, base_url)
    title = _extract_course_title(anchor)
    label = clean_text(anchor.get_text(" ", strip=True))
    crn_match = _CRN_LABEL_RE.search(label)

    code, source, uncertain = _course_code(anchor, context, page_url, base_url)
    course: dict[str, Any] = {
        "code": code,
        "title": title,
        "url": absolute,
        "context": context,
        "section": label if label and label != title else None,
        "crn": crn_match.group(1) if crn_match else None,
    }
    if source == "section_label":
        course["code_source"] = source
    # No code in the course's own region: the code stays null and is marked
    # uncertain instead of being borrowed from whatever stands nearby.
    if source == "section_label" or code is None:
        course["code_uncertain"] = True
    return course


def extract_courses(html: str, page_url: str, base_url: str) -> list[dict[str, Any]]:
    """List the course links of the dashboard, one row per enrolled section.

    ``code`` comes from the text next to the section links, not from the link
    labels. When only a label is available the code read from it is flagged
    with ``code_source: "section_label"``, and a branch followed by a year is
    never reported as a code (``code`` is null and ``code_uncertain`` true).
    ``section`` and ``crn`` tell two sections of one course apart.
    """
    soup = make_soup(html)
    courses: list[dict[str, Any]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        absolute = urljoin(page_url, anchor["href"])
        if not is_internal_ninova_url(absolute, base_url) or not is_course_root(absolute):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        courses.append(_course_row(anchor, absolute, page_url, base_url))
    return courses


def summarize_dashboard(
    page_data: dict[str, Any], html: str, base_url: str, *, today: date | None = None,
) -> dict[str, Any]:
    def table(heading: str) -> list[Any]:
        return _with_inferred_dates(extract_named_table(html, heading), today)

    return {
        "page": {
            "url": page_data["url"],
            "title": page_data["title"],
            "headings": page_data["headings"],
        },
        "courses": extract_courses(html, page_data["url"], base_url=base_url),
        "recent_announcements": table("Son Duyurular"),
        "recent_assignments": table("Son Odevler"),
        "recent_messages": table("Son Mesajlar"),
        "links": page_data["links"],
    }
