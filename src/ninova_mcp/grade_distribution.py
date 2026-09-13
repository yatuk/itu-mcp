"""Read aggregate grade counts from the official public OBS chart data."""

from __future__ import annotations

from datetime import datetime
import json
import math
import re
from typing import Any
from zoneinfo import ZoneInfo

from .archive import COURSE_CODE_PATTERN, normalize_course_code, split_course_code
from .obs_client import ObsError
from .parsing import clean_text, make_soup, normalize_lookup_text

_PATH = "/public/DersNotDagilimi/NotDagilimiSearch"


def _inputs(course_code: str, year: int | None, term_code: str | None) -> tuple[str, str, int]:
    try:
        branch, number = split_course_code(course_code)
    except (ValueError, TypeError) as exc:
        raise ObsError("Enter a full course code, such as UZB 438E.") from exc
    if year is not None and (type(year) is not int or not 1900 <= year <= 2100):
        raise ObsError("year must be an integer between 1900 and 2100.")
    if term_code is not None and (not isinstance(term_code, str) or not re.fullmatch(r"[0-9]{6}", term_code)):
        raise ObsError("term_code must be the six-digit code returned in available_terms.")
    selected_year = year if year is not None else int(term_code[:4]) if term_code else datetime.now(ZoneInfo("Europe/Istanbul")).year
    if not 1900 <= selected_year <= 2100:
        raise ObsError("year must be an integer between 1900 and 2100.")
    if term_code and not term_code.startswith(str(selected_year)):
        raise ObsError("year and term_code refer to different academic years.")
    return branch, number, selected_year


def _integer(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ObsError(f"OBS returned an invalid {label}.")
    return value


def parse_grade_distribution(html: str, url: str, requested_code: str, year: int) -> dict[str, Any]:
    """Decode the JSON chart constant without evaluating JavaScript."""
    soup = make_soup(html)
    page_text = normalize_lookup_text(soup.get_text(" ", strip=True))
    explicitly_unavailable = "ders not dagilimi bulunamamistir" in page_text
    data = None
    for script in soup.find_all("script"):
        content = script.string or script.get_text()
        assignment = re.search(r"\b(?:const|let|var)\s+ALL_DATA\s*=\s*", content)
        if assignment:
            try:
                data, _end = json.JSONDecoder().raw_decode(content[assignment.end():])
            except (ValueError, TypeError) as exc:
                raise ObsError("The OBS grade distribution chart data could not be decoded.") from exc
            break
    if data is None:
        if explicitly_unavailable:
            data = []
        else:
            raise ObsError("OBS did not return a recognizable grade distribution page.")
    if not isinstance(data, list):
        raise ObsError("OBS returned an unrecognized grade distribution structure.")

    heading = soup.find("h3")
    title = clean_text(heading.get_text(" ", strip=True)) if heading else ""
    codes_part, separator, name = title.partition(",")
    reported_codes = list(dict.fromkeys(normalize_course_code(match.group(0))
                                       for match in COURSE_CODE_PATTERN.finditer(codes_part)))
    if (data or not explicitly_unavailable or heading is not None) and requested_code not in reported_codes:
        raise ObsError("The OBS grade distribution course codes do not match the requested course.")
    course_name = re.sub(r"\s+(?:Not Dağılımı|Grade Distribution)\s*$", "", name, flags=re.I).strip() if separator else None
    terms = []
    seen_terms = set()
    for row in data:
        if not isinstance(row, dict):
            raise ObsError("OBS returned an invalid grade distribution term.")
        code = row.get("DonemKodu")
        if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code) or not code.startswith(str(year)) or code in seen_terms:
            raise ObsError("OBS returned a duplicated or mismatched grade distribution term.")
        seen_terms.add(code)
        academic_year = row.get("YilAdi")
        if not isinstance(academic_year, str) or clean_text(academic_year) != f"{year - 1}-{year}":
            raise ObsError("The OBS grade distribution academic year does not match the requested year.")
        total = _integer(row.get("ToplamAciklananOgrenci"), "announced student total")
        raw_grades = row.get("Dagilim")
        if not isinstance(raw_grades, list):
            raise ObsError("OBS did not return a grade count list.")
        grades = []
        seen_grades = set()
        for entry in raw_grades:
            if not isinstance(entry, dict):
                raise ObsError("OBS returned an invalid grade count row.")
            grade = entry.get("HarfNotu")
            if not isinstance(grade, str) or not re.fullmatch(r"[A-Z]{1,3}\+?", grade) or grade in seen_grades:
                raise ObsError("OBS returned a duplicated or unrecognized grade label.")
            seen_grades.add(grade)
            count = _integer(entry.get("Sayi"), "grade count")
            percentage = entry.get("Yuzde")
            if type(percentage) not in (int, float) or not math.isfinite(percentage) or not 0 <= percentage <= 100:
                raise ObsError("OBS returned an invalid grade percentage.")
            grades.append({"grade": grade, "count": count, "percentage": percentage})
        count_sum = sum(item["count"] for item in grades)
        percentages_match = all(abs(item["percentage"] - (100 * item["count"] / total if total else 0)) <= 0.11 for item in grades)
        consistent = count_sum == total and percentages_match
        terms.append({
            "term_code": code, "academic_year": academic_year, "term_name": row.get("DonemTipAdi"),
            "announced_student_count": total, "counted_student_count": count_sum,
            "grades": grades, "counts_by_grade": {item["grade"]: item["count"] for item in grades},
            "consistent": consistent, "status": "available" if total and consistent else "no_announced_grades" if consistent else "incomplete",
            "warnings": [] if consistent else ["The published grade counts or percentages disagree with the announced total."],
        })
    return {
        "requested_course_code": requested_code, "reported_course_codes": reported_codes,
        "course_name": course_name, "year": year, "terms": terms,
        "aggregation_scope": "combined_course_codes" if len(reported_codes) > 1 else "single_course_code" if reported_codes else "unknown",
        "scope_note": "Counts cover the course codes shown by OBS together. No separate section, instructor, or language-specific breakdown is inferred.",
        "source_url": url, "untrusted_external_content": True,
    }


def get_grade_distribution(public: Any, course_code: str, year: int | None = None, term_code: str | None = None) -> dict[str, Any]:
    """Read one official academic ending year, optionally selecting a published term."""
    branch, number, selected_year = _inputs(course_code, year, term_code)
    html, url = public._get_html(_PATH, params={"bransKodu": branch, "dersNo": number, "yil": selected_year})
    result = parse_grade_distribution(html, url, f"{branch} {number}", selected_year)
    result["available_terms"] = [{key: row[key] for key in ("term_code", "academic_year", "term_name", "announced_student_count")}
                                 for row in result["terms"]]
    if term_code is not None:
        result["terms"] = [row for row in result["terms"] if row["term_code"] == term_code]
    result["requested_term_code"] = term_code
    result["available"] = bool(result["terms"])
    result["has_announced_grades"] = any(row["announced_student_count"] > 0 for row in result["terms"])
    result["complete"] = all(row["consistent"] for row in result["terms"])
    if not result["available"]:
        result["note"] = "OBS has no published distribution for this course/year or selected term. This does not establish that nobody took the course."
    result["retrieved_at"] = datetime.now(ZoneInfo("Europe/Istanbul")).isoformat()
    return result
