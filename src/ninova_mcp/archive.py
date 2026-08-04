"""Aggregation helpers over the İTÜ ders arşivi.

Pure functions on already-fetched archive JSON: no network, no I/O. The archive
stores history as compact positional rows; everything here turns those into
named fields and the summaries a student actually asks for.
"""

from __future__ import annotations

import re
from typing import Any

# Branch codes run 2-4 letters; course numbers are 3 digits for normal courses
# and 4 for capstone/design courses (CEN 4901E). The trailing letters cover the
# English suffix and lab variants like FIZ 101EL.
COURSE_CODE_PATTERN = re.compile(
    r"\s*([A-Za-zÇĞİÖŞÜçğıöşü]{2,4})\s*(\d{3,4})\s*([A-Za-z]{0,2})\s*"
)

SEASON_LABELS = {"guz": "Güz", "bahar": "Bahar", "yaz": "Yaz"}

# Positional layout of history/courses/<BRANCH>.json rows.
_COURSE_ROW_FIELDS = ("term", "instructor", "capacity", "enrolled", "days")
# Positional layout of history/instructors/<letter>.json rows.
_INSTRUCTOR_ROW_FIELDS = ("term", "code", "name", "capacity", "enrolled")


def split_course_code(course_code: str) -> tuple[str, str]:
    """Split ``"CEN 4901E"`` into ``("CEN", "4901E")``.

    Accepts 3- and 4-digit course numbers and one- or two-letter suffixes, so
    capstone courses and lab variants resolve like any other course.
    """
    match = COURSE_CODE_PATTERN.fullmatch(course_code or "")
    if not match:
        raise ValueError(
            f"Ders kodu çözümlenemedi: {course_code!r}. 'BLG 223E' veya 'CEN 4901E' gibi olmalı."
        )
    branch, number, suffix = match.groups()
    return branch.upper(), f"{number}{suffix}".upper()


def normalize_course_code(course_code: str) -> str:
    """Return the canonical ``"BRANCH NUMBER"`` spelling of a course code."""
    branch, number = split_course_code(course_code)
    return f"{branch} {number}"


def season_of(term_slug: str) -> str:
    """Return ``"Güz"`` / ``"Bahar"`` / ``"Yaz"`` for ``"2025-2026-guz"``."""
    tail = (term_slug or "").rsplit("-", 1)[-1].lower()
    return SEASON_LABELS.get(tail, tail or "?")


def _term_sort_key(term_slug: str) -> tuple[int, int]:
    """Order terms chronologically: academic year, then season within it."""
    order = {"guz": 0, "bahar": 1, "yaz": 2}
    parts = (term_slug or "").split("-")
    try:
        year = int(parts[0])
    except (ValueError, IndexError):
        year = 0
    return year, order.get(parts[-1].lower() if parts else "", 9)


def _rows_as_dicts(rows: Any, fields: tuple[str, ...]) -> list[dict[str, Any]]:
    """Zip positional archive rows onto field names, skipping malformed rows."""
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, list) or len(row) < len(fields):
            continue
        entry = dict(zip(fields, row, strict=False))
        entry["season"] = season_of(str(entry.get("term") or ""))
        out.append(entry)
    return out


def _fill_ratio(capacity: Any, enrolled: Any) -> float | None:
    try:
        cap = float(capacity)
        enr = float(enrolled)
    except (TypeError, ValueError):
        return None
    if cap <= 0:
        return None
    return round(enr / cap, 3)


def course_history(entry: dict[str, Any], *, limit_terms: int | None = None) -> dict[str, Any]:
    """Turn one ``history/courses`` entry into a term-by-term offering record.

    Groups the flat section rows by term so "this course ran in 13 terms, always
    in Bahar, with these instructors" is readable in one pass.
    """
    rows = _rows_as_dicts(entry.get("rows"), _COURSE_ROW_FIELDS)
    by_term: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_term.setdefault(str(row.get("term") or ""), []).append(row)

    ordered = sorted(by_term, key=_term_sort_key, reverse=True)
    if limit_terms is not None:
        ordered = ordered[:limit_terms]

    offerings: list[dict[str, Any]] = []
    for term in ordered:
        sections = by_term[term]
        offerings.append({
            "term": term,
            "season": season_of(term),
            "section_count": len(sections),
            "instructors": sorted({str(s.get("instructor") or "").strip() for s in sections} - {"", "--"}),
            "sections": [
                {
                    "instructor": s.get("instructor"),
                    "capacity": s.get("capacity"),
                    "enrolled": s.get("enrolled"),
                    "fill_ratio": _fill_ratio(s.get("capacity"), s.get("enrolled")),
                    "days": s.get("days"),
                }
                for s in sections
            ],
        })

    return {
        "course_code": entry.get("code"),
        "course_name": entry.get("name"),
        "terms_offered": len(by_term),
        "seasonality": seasonality(list(by_term)),
        "offerings": offerings,
        "truncated": limit_terms is not None and len(by_term) > len(ordered),
    }


def seasonality(term_slugs: list[str]) -> dict[str, Any]:
    """Summarise which seasons a course actually opens in.

    "Only ever offered in Güz, 6 out of 6 terms" is the answer to "can I take
    this in the spring?", which OBS cannot give at all.
    """
    counts: dict[str, int] = {}
    for slug in term_slugs:
        counts[season_of(slug)] = counts.get(season_of(slug), 0) + 1
    total = sum(counts.values())
    dominant = max(counts, key=lambda k: counts[k]) if counts else None
    return {
        "counts": counts,
        "total_terms": total,
        "dominant_season": dominant,
        "only_season": dominant if dominant and counts.get(dominant) == total and total > 0 else None,
    }


def who_taught(entry: dict[str, Any], *, limit_terms: int | None = None) -> dict[str, Any]:
    """Rank instructors for one course by how often and how recently they taught it."""
    rows = _rows_as_dicts(entry.get("rows"), _COURSE_ROW_FIELDS)
    if limit_terms is not None:
        keep = sorted({str(r.get("term") or "") for r in rows}, key=_term_sort_key, reverse=True)[:limit_terms]
        rows = [r for r in rows if str(r.get("term") or "") in set(keep)]

    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = str(row.get("instructor") or "").strip()
        if not name or name == "--":
            continue
        bucket = grouped.setdefault(name, {"instructor": name, "terms": set(), "sections": 0, "ratios": []})
        bucket["terms"].add(str(row.get("term") or ""))
        bucket["sections"] += 1
        ratio = _fill_ratio(row.get("capacity"), row.get("enrolled"))
        if ratio is not None:
            bucket["ratios"].append(ratio)

    instructors = []
    for bucket in grouped.values():
        terms = sorted(bucket["terms"], key=_term_sort_key, reverse=True)
        ratios = bucket["ratios"]
        instructors.append({
            "instructor": bucket["instructor"],
            "term_count": len(terms),
            "section_count": bucket["sections"],
            "latest_term": terms[0] if terms else None,
            "terms": terms,
            "average_fill_ratio": round(sum(ratios) / len(ratios), 3) if ratios else None,
        })
    instructors.sort(
        key=lambda i: (i["term_count"], _term_sort_key(i["latest_term"] or "")),
        reverse=True,
    )
    return {
        "course_code": entry.get("code"),
        "course_name": entry.get("name"),
        "instructors": instructors,
    }


def instructor_courses(entry: dict[str, Any], *, limit: int | None = None) -> dict[str, Any]:
    """Summarise every course one instructor has taught, newest first."""
    rows = _rows_as_dicts(entry.get("rows"), _INSTRUCTOR_ROW_FIELDS)
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = str(row.get("code") or "").strip()
        if not code:
            continue
        bucket = grouped.setdefault(code, {
            "course_code": code,
            "course_name": row.get("name"),
            "terms": set(),
            "sections": 0,
        })
        bucket["terms"].add(str(row.get("term") or ""))
        bucket["sections"] += 1

    courses = []
    for bucket in grouped.values():
        terms = sorted(bucket["terms"], key=_term_sort_key, reverse=True)
        courses.append({
            "course_code": bucket["course_code"],
            "course_name": bucket["course_name"],
            "term_count": len(terms),
            "section_count": bucket["sections"],
            "latest_term": terms[0] if terms else None,
            "terms": terms,
        })
    courses.sort(key=lambda c: (_term_sort_key(c["latest_term"] or ""), c["term_count"]), reverse=True)
    total = len(courses)
    if limit is not None:
        courses = courses[:limit]
    return {
        "instructor": entry.get("name"),
        "distinct_courses": total,
        "term_count": entry.get("terms"),
        "courses": courses,
        "truncated": total > len(courses),
    }


def summarize_section(section: dict[str, Any]) -> dict[str, Any]:
    """Flatten one archived section record into a compact, readable row.

    The archive stores day/time/building as parallel arrays because OBS packs
    multiple weekly sessions into one cell; recombine them into sessions.
    """
    days = section.get("days") or []
    times = section.get("times") or []
    buildings = section.get("buildings") or []
    rooms = section.get("rooms") or []
    sessions = []
    for index in range(max(len(days), len(times))):
        sessions.append({
            "day": days[index] if index < len(days) else None,
            "time": times[index] if index < len(times) else None,
            "building": buildings[index] if index < len(buildings) else None,
            "room": rooms[index] if index < len(rooms) else None,
        })
    return {
        "crn": section.get("crn"),
        "course_code": section.get("code"),
        "course_name": section.get("name"),
        "instructor": section.get("instructor"),
        "level": section.get("level"),
        "capacity": section.get("capacity"),
        "enrolled": section.get("enrolled"),
        "fill_ratio": _fill_ratio(section.get("capacity"), section.get("enrolled")),
        "sessions": sessions,
        "programs": section.get("programs"),
        "prerequisite_note": section.get("prereq") or None,
        "class_requirement": section.get("classReq") or None,
        "method": section.get("method") or None,
        "reserved": section.get("reserved") or None,
    }


def fill_summary(quota: dict[str, Any], crn: str) -> dict[str, Any] | None:
    """Look one CRN up in a term's derived quota summary."""
    target = str(crn).strip()
    for course in quota.get("courses") or []:
        if str(course.get("crn") or "").strip() == target:
            entry = dict(course)
            entry["fill_ratio"] = _fill_ratio(course.get("capacity"), course.get("enrolled"))
            entry["is_full"] = bool(course.get("filledAt")) or (
                entry["fill_ratio"] is not None and entry["fill_ratio"] >= 1.0
            )
            return entry
    return None


def search_courses(codes: list[list[Any]], query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """Search the archive's flat course index by code or name fragment.

    ``codes`` is ``history/codes.json``: ``[code, name, branch, term_count]``
    rows. Exact and prefix code matches rank above substring code matches,
    which rank above name matches, so a code lookup like "BLG 102" still wins
    over an unrelated course whose name happens to contain "102".
    """
    from .parsing import normalize_lookup_text

    target = normalize_lookup_text(query)
    if not target:
        return []

    ranked: list[tuple[int, dict[str, Any]]] = []
    for row in codes:
        if len(row) < 4:
            continue
        code, name, branch, term_count = row[0], row[1], row[2], row[3]
        norm_code = normalize_lookup_text(str(code))
        norm_name = normalize_lookup_text(str(name))
        if target == norm_code:
            rank = 0
        elif norm_code.startswith(target):
            rank = 1
        elif target in norm_code:
            rank = 2
        elif target in norm_name:
            rank = 3
        else:
            continue
        ranked.append((rank, {
            "course_code": code,
            "course_name": name,
            "branch": branch,
            "term_count": term_count,
        }))

    ranked.sort(key=lambda item: (item[0], -(item[1]["term_count"] or 0)))
    return [item[1] for item in ranked[:limit]]


def diff_term_offerings(
    sections_a: list[dict[str, Any]],
    sections_b: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare a course's sections between two terms.

    Takes already-summarized sections (``summarize_section`` output) for the
    same course in two different terms and reports what changed: instructor
    turnover, section-count delta, and capacity/fill movement. This is the
    piece a manual side-by-side ``archive_course_history`` read cannot give
    directly — it names *what changed*, not just two snapshots to compare by eye.
    """

    def _summarize_side(sections: list[dict[str, Any]]) -> dict[str, Any]:
        instructors = sorted({
            s.get("instructor") for s in sections
            if s.get("instructor") and s.get("instructor") != "--"
        })
        capacities = [s["capacity"] for s in sections if isinstance(s.get("capacity"), (int, float))]
        enrolled = [s["enrolled"] for s in sections if isinstance(s.get("enrolled"), (int, float))]
        ratios = [s["fill_ratio"] for s in sections if s.get("fill_ratio") is not None]
        return {
            "section_count": len(sections),
            "instructors": instructors,
            "total_capacity": sum(capacities) if capacities else None,
            "total_enrolled": sum(enrolled) if enrolled else None,
            "average_fill_ratio": round(sum(ratios) / len(ratios), 3) if ratios else None,
        }

    side_a = _summarize_side(sections_a)
    side_b = _summarize_side(sections_b)
    return {
        "first": side_a,
        "second": side_b,
        "instructors_added": sorted(set(side_b["instructors"]) - set(side_a["instructors"])),
        "instructors_removed": sorted(set(side_a["instructors"]) - set(side_b["instructors"])),
        "section_count_delta": side_b["section_count"] - side_a["section_count"],
    }


def recommend_course_timing(
    course_code: str,
    seasonality_summary: dict[str, Any],
    top_instructors: list[dict[str, Any]],
) -> str:
    """Render a one-line scheduling recommendation from archive history.

    Combines what ``seasonality`` and ``who_taught`` already compute
    separately into the sentence a student actually wants: which term to plan
    around, and who is likely to teach it.
    """
    parts: list[str] = []
    total = seasonality_summary.get("total_terms") or 0
    only = seasonality_summary.get("only_season")
    dominant = seasonality_summary.get("dominant_season")
    counts = seasonality_summary.get("counts") or {}

    if only and total > 0:
        parts.append(f"{course_code} yalnızca {only} döneminde açılıyor ({total} dönem kaydı).")
    elif dominant and total > 0:
        parts.append(
            f"{course_code} çoğunlukla {dominant} döneminde açılıyor "
            f"({counts.get(dominant, 0)}/{total} dönem)."
        )
    else:
        parts.append(f"{course_code} için arşivde mevsim eğilimi görülmüyor.")

    if top_instructors:
        top = top_instructors[0]
        fill = top.get("average_fill_ratio")
        fill_text = f", ort. doluluk {fill}" if fill is not None else ""
        parts.append(
            f"En sık {top['instructor']} veriyor ({top['term_count']} dönem, "
            f"son: {top['latest_term']}{fill_text})."
        )

    return " ".join(parts)
