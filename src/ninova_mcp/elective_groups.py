"""Read official elective-group membership and join current public sections."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from .archive import normalize_course_code, split_course_code
from .parsing import clean_text, make_soup

if TYPE_CHECKING:
    from .obs_client import ObsPublicClient


ELECTIVE_GROUP_PATH = "/public/DersPlan/_DersGrupSearch"


def _number(value: str) -> float | None:
    try:
        return float(value.strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def extract_elective_group(
    html: str,
    page_url: str,
    group_id: int,
) -> dict[str, Any]:
    """Parse the official group page linked from public OBS degree plans.

    The course code in the visible link is authoritative. OBS detail URLs can
    omit a language suffix, so deriving membership from those URLs would merge
    distinct courses such as ``UCK 358`` and ``UCK 358E``.
    """
    soup = make_soup(html)
    content = soup.select_one(".content-area")
    heading = content.find("h3") if content else None
    group_name = clean_text(heading.get_text(" ", strip=True)) if heading else ""
    table = content.select_one("table.datalist") if content else None
    body = table.find("tbody") if table else None
    courses: list[dict[str, Any]] = []
    warnings: list[str] = []
    complete = bool(group_name and body is not None)

    if not group_name:
        warnings.append("The elective group was not found or its title could not be read.")
    if body is None:
        warnings.append("The elective-group course table could not be read.")
    else:
        for row_number, row in enumerate(body.find_all("tr", recursive=False), 1):
            cells = row.find_all("td", recursive=False)
            if not cells:
                continue
            anchor = cells[0].find("a", href=True)
            if len(cells) != 7 or anchor is None:
                complete = False
                warnings.append(f"Elective-group row {row_number} has an unexpected structure.")
                continue
            try:
                code = normalize_course_code(clean_text(anchor.get_text(" ", strip=True)))
            except ValueError:
                complete = False
                warnings.append(f"Elective-group row {row_number} has an unreadable course code.")
                continue
            # Remove only the code anchor, preserving names split into spans.
            name_cell = make_soup(str(cells[0]))
            name_anchor = name_cell.find("a")
            if name_anchor is not None:
                name_anchor.decompose()
            values = [clean_text(cell.get_text(" ", strip=True)) for cell in cells]
            courses.append({
                "course_code": code,
                "course_name": clean_text(name_cell.get_text(" ", strip=True)) or None,
                "language": values[1] or None,
                "credit": _number(values[2]),
                "ects": _number(values[3]),
                "theory_hours": _number(values[4]),
                "practice_hours": _number(values[5]),
                "lab_hours": _number(values[6]),
                "course_url": urljoin(page_url, str(anchor["href"])),
            })

    return {
        "group_id": group_id,
        "group_name": group_name or None,
        "url": page_url,
        "available": bool(group_name and body is not None),
        "complete": complete,
        "count": len(courses),
        "courses": courses,
        "warnings": warnings,
        "untrusted_external_content": True,
    }


def fetch_elective_group(
    client: ObsPublicClient,
    group_id: int | str,
) -> dict[str, Any]:
    """Fetch one official group through the existing public OBS HTTP client."""
    from .obs_client import ObsError

    raw = str(group_id).strip()
    if isinstance(group_id, bool) or not raw.isascii() or not raw.isdigit() or int(raw) <= 0:
        raise ObsError("group_id must be a positive integer.")
    normalized_id = int(raw)
    cache_key = f"elective_group:{normalized_id}"
    cached = client._cache.get(cache_key)
    if cached is not None:
        return deepcopy(cached)
    html, url = client._get_html(ELECTIVE_GROUP_PATH, params={"grupId": str(normalized_id)})
    result = extract_elective_group(html, url, normalized_id)
    if not result["complete"]:
        raise ObsError(
            f"Elective group {normalized_id} could not be read completely. "
            + " ".join(result["warnings"])
        )
    client._cache.set(cache_key, result)
    return deepcopy(result)


def enrich_elective_group(
    group: dict[str, Any],
    schedules: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Attach exact course matches from schedules keyed by branch code.

    A missing schedule, a parsing warning, an empty schedule, or missing term
    metadata leaves ``offered_this_term`` unknown. An empty match is only
    reported as false after reading other sections for that branch and term.
    Student-specific eligibility must be evaluated separately for each CRN.
    """
    result = deepcopy(group)
    normalized_schedules = {str(branch).strip().upper(): data for branch, data in schedules.items()}
    for course in result.get("courses") or []:
        try:
            branch, _ = split_course_code(course.get("course_code") or "")
            target = normalize_course_code(course["course_code"])
        except ValueError:
            course.update({"offered_this_term": None, "sections": [], "schedule_status": "unknown"})
            continue
        schedule = normalized_schedules.get(branch) or {}
        sections = []
        rows = schedule.get("courses")
        malformed_rows = not isinstance(rows, list)
        for section in rows if isinstance(rows, list) else []:
            if not isinstance(section, dict):
                malformed_rows = True
                continue
            try:
                matches = normalize_course_code(section.get("code") or "") == target
            except ValueError:
                malformed_rows = True
                continue
            if not str(section.get("crn") or "").strip().isascii() or not str(section.get("crn") or "").strip().isdigit():
                malformed_rows = True
            if matches:
                sections.append(deepcopy(section))
        term = str(schedule.get("semester") or "").strip()
        has_known_term = bool(term and term.casefold() not in {"unknown", "unknown semester", "bilinmeyen dönem"})
        readable = bool(
            rows
            and not malformed_rows
            and not schedule.get("parse_warning")
            and not schedule.get("parse_warnings")
            and not schedule.get("error")
        )
        offered = bool(sections) if has_known_term and readable else None
        course.update({
            "offered_this_term": offered,
            "sections": sections,
            "crns": [str(section["crn"]) for section in sections if section.get("crn")],
            "schedule_status": "available" if has_known_term and readable else "unknown",
            "semester": schedule.get("semester"),
            "schedule_url": schedule.get("url"),
        })
        if offered is None:
            course["schedule_note"] = "A complete public schedule for a known term was not available."
    result["offered_course_count"] = sum(course.get("offered_this_term") is True for course in result.get("courses") or [])
    result["schedule_unknown_count"] = sum(course.get("offered_this_term") is None for course in result.get("courses") or [])
    return result
