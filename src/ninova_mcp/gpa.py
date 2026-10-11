"""İTÜ GPA / GANO hesaplama yardımcıları.

İTÜ 4'lük sistem harf notu → katsayı dönüşüm tablosu ve ağırlıklı
ortalama hesaplama.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any

from .archive import normalize_course_code
from .grading import FAILING_GRADES, LETTER_TO_GRADE, normalize_grade
from .parsing import normalize_lookup_text

# Kredi genelde OBS'te "kredi" alanındadır; AKTS değil.
# Kayıtlı ders listesinde `kredi` genelde string gelir.


def course_key(value: Any) -> str:
    try:
        return normalize_course_code(str(value or ""))
    except ValueError:
        # Keep support for callers using display labels instead of OBS codes.
        return " ".join(str(value or "").upper().split())


def parse_credit(value: Any) -> float | None:
    """Parse a finite nonnegative credit value, preserving a genuine zero."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _json_safe(value: Any) -> Any:
    """Do not leak nonfinite backend provenance into a JSON tool response."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def validate_projected_grades(projected_grades: Any) -> dict[str, str]:
    """What-if grades with normalized letter grades (``bb`` becomes ``BB``).

    Raises ``ValueError`` naming the key, the value and the valid grades.
    """
    if projected_grades is None:
        return {}
    if not isinstance(projected_grades, dict):
        raise ValueError('projected_grades must be an object such as {"BLG 223E": "AA", "21547": "BB"}.')
    counted = [grade for grade, value in LETTER_TO_GRADE.items() if value is not None]
    uncounted = sorted(grade for grade, value in LETTER_TO_GRADE.items() if value is None)
    checked: dict[str, str] = {}
    for raw_key, raw_grade in projected_grades.items():
        if not course_key(raw_key):
            raise ValueError("projected_grades keys must be a course code or a CRN.")
        grade = normalize_grade(raw_grade) if isinstance(raw_grade, str) else ""
        if grade not in LETTER_TO_GRADE:
            raise ValueError(
                f"Unknown letter grade {raw_grade!r} for projected_grades[{str(raw_key)!r}]. "
                f"Valid grades: {', '.join(counted)}. "
                f"Accepted but not counted in GPA: {', '.join(uncounted)}."
            )
        checked[str(raw_key)] = grade
    return checked


_FAILED_GRADES = ("FF", "VF")
_LOW_GRADES = ("FD", "DD", "DD+", "DC", "DC+")
# Evidence the caller attached to a course; copied to the counted course as is.
_EVIDENCE_FIELDS = ("recorded_credit", "plan_credit", "counted_credit", "credit_source", "grade_source",
                    "credit_candidates", "credit_choice", "crn", "attempts")
_RESULT_NOTE = (
    "Hesap yalnız verilen dersleri kapsar. Eksik veya geçersiz not/kredi varsa "
    "gpa verilmez; known_courses_gpa ve toplamlar yalnız doğrulanan kayıtları kapsar. "
    "Bu sonuç onur derecesi, akademik durum veya kümülatif GANO belirlemez. "
    "BL/BZ/T/E/M ve diğer katsayısız notlar hesaba katılmaz."
)


@dataclass
class _CourseRow:
    """One supplied course with its code, credit and grade read once."""

    source: dict[str, Any]
    code: Any
    name: Any
    key: str
    credit_raw: Any
    credit: float | None
    grade: str | None
    projected: bool

    @property
    def credit_gap(self) -> str:
        """Why there is no usable credit: nothing was supplied, or it is not a credit."""
        return "missing" if self.credit_raw is None or str(self.credit_raw).strip() == "" else "invalid"


@dataclass
class _Tally:
    """Running totals and the courses sorted by what happened to them."""

    total_points: float = 0.0
    total_credits: float = 0.0
    eligible_count: int = 0
    counted: list[dict[str, Any]] = field(default_factory=list)
    ff_risk: list[dict[str, Any]] = field(default_factory=list)
    ungraded: list[dict[str, Any]] = field(default_factory=list)
    unrecognized_grades: list[dict[str, Any]] = field(default_factory=list)
    invalid_credits: list[dict[str, Any]] = field(default_factory=list)
    excluded: list[dict[str, Any]] = field(default_factory=list)


def _projection_by_course(projected_grades: Any) -> dict[str, str]:
    """What-if grades per course key; two keys of one course must agree."""
    projected: dict[str, str] = {}
    for code, grade in validate_projected_grades(projected_grades).items():
        key = course_key(code)
        if key in projected and projected[key] != grade:
            raise ValueError(f"Conflicting projected grades for {key}.")
        projected[key] = grade
    return projected


def _read_course_row(course: dict[str, Any], projected: dict[str, str]) -> _CourseRow:
    code = course.get("code") or course.get("dersKodu") or "?"
    name = course.get("name") or course.get("dersAdiTR") or ""

    # Kredi (None check: 0 is a valid credit value)
    credit_raw = course.get("credit")
    if credit_raw is None:
        credit_raw = course.get("kredi")

    # A what-if value is an explicit override, including for a course that
    # already has an OBS letter grade.  This matches the public tool's
    # documented behaviour and lets users compare alternative outcomes.
    key = course_key(code)
    projected_grade = projected.get(key)
    grade_raw = projected_grade if projected_grade is not None else course.get("grade")
    if grade_raw is None or grade_raw == "":
        grade_raw = course.get("harfNotu")
    grade = (str(grade_raw).strip().upper() or None) if grade_raw is not None else None
    return _CourseRow(
        source=course, code=code, name=name, key=key, credit_raw=credit_raw,
        credit=parse_credit(credit_raw), grade=grade, projected=projected_grade is not None,
    )


def _file_course(tally: _Tally, row: _CourseRow) -> None:
    """Put the course where its grade sends it: ungraded, unknown, excluded or counted."""
    if row.grade is None:
        tally.ungraded.append({**row.source, "code": row.code, "name": row.name, "credit": row.credit,
                               "credit_status": "valid" if row.credit is not None else row.credit_gap})
    elif row.grade not in LETTER_TO_GRADE:
        tally.unrecognized_grades.append({"code": row.code, "grade": row.grade})
    elif LETTER_TO_GRADE[row.grade] is None:
        # GE, KF, IA, MU gibi GANO'ya katılmayan notlar
        tally.excluded.append({"code": row.code, "grade": row.grade, "credit": row.credit,
                               "reason": "grade_excluded_from_gpa"})
    else:
        _count_course(tally, row, LETTER_TO_GRADE[row.grade])


def _count_course(tally: _Tally, row: _CourseRow, coefficient: float) -> None:
    """Add a course whose grade counts, unless its credit cannot be used."""
    tally.eligible_count += 1
    failure_note = f"{row.grade} notu bildirildi. Dersin tekrar veya yerine alma koşulları OBS kurallarına bağlıdır."
    unusable = {"code": row.code, "grade": row.grade}
    if row.credit is None:
        tally.invalid_credits.append({**unusable, "reason": f"{row.credit_gap}_credit", "raw_credit": row.credit_raw})
        if row.grade in _FAILED_GRADES:
            tally.ff_risk.append({**unusable, "credit": None, "note": failure_note})
        return
    points = row.credit * coefficient
    totals = (tally.total_credits + row.credit, tally.total_points + points)
    if not all(math.isfinite(value) for value in (points, *totals)):
        tally.invalid_credits.append({**unusable, "reason": "credit_arithmetic_overflow",
                                      "raw_credit": row.credit_raw})
        return
    tally.total_credits, tally.total_points = totals

    detail = {
        "code": row.code,
        "name": row.name,
        "credit": row.credit,
        "grade": row.grade,
        "coefficient": coefficient,
        "points": round(points, 2),
        "projected": row.projected,
    }
    detail.update({name: row.source[name] for name in _EVIDENCE_FIELDS if name in row.source})
    if row.grade in _FAILED_GRADES:
        detail["note"] = failure_note
        tally.ff_risk.append(detail)
    elif row.grade in _LOW_GRADES:
        detail["note"] = "Düşük not. Bu dersin ortalamaya katkısı kredi ağırlığına bağlıdır."
    tally.counted.append(detail)


def _gpa_result(
    tally: _Tally, input_course_count: int, projected: dict[str, str], used_projections: set[str],
) -> dict[str, Any]:
    known_gpa = round(tally.total_points / tally.total_credits, 2) if tally.total_credits > 0 else None
    complete = not (tally.invalid_credits or tally.unrecognized_grades or tally.ungraded)
    if not complete:
        status = "incomplete"
    else:
        status = "complete" if known_gpa is not None else "no_gpa_credits"
    unused_projections = sorted(set(projected) - used_projections)
    return {
        "gpa": known_gpa if complete else None,
        "known_courses_gpa": known_gpa,
        "calculation_complete": complete,
        "calculation_status": status,
        "average_scope": "supplied_courses",
        "input_course_count": input_course_count,
        "gpa_eligible_course_count": tally.eligible_count,
        "covered_course_count": len(tally.counted),
        "excluded_course_count": len(tally.excluded),
        "total_credits": round(tally.total_credits, 1),
        "total_points": round(tally.total_points, 2),
        "graded_course_count": len(tally.counted),
        "ungraded_course_count": len(tally.ungraded),
        "courses": tally.counted,
        "ungraded": tally.ungraded,
        "unrecognized_grades": tally.unrecognized_grades,
        "invalid_credits": tally.invalid_credits,
        "excluded_courses": tally.excluded,
        "unused_projected_courses": unused_projections,
        "projection_complete": not unused_projections,
        "applied_projection_count": len(used_projections),
        "ff_risk": tally.ff_risk,
        "comment": None,
        "scale": "4.00",
        "note": _RESULT_NOTE,
    }


def calculate_gpa(
    courses: list[dict[str, Any]],
    *,
    projected_grades: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Compute the credit-weighted average of the supplied course records.

    Each course dict should have at least:
    - ``code`` (str): ders kodu, e.g. ``"BLG 223E"``
    - ``credit`` (float | str | None): kredi (AKTS değil)
    - ``grade`` (str | None): mevcut harf notu (yoksa projected_grades'e bakar)

    ``projected_grades``: ``{"BLG 223E": "AA", ...}`` — henüz notu belli
    olmayan veya beklenen not için elle girilmiş tahmin.
    """
    projected = _projection_by_course(projected_grades)
    used_projections: set[str] = set()
    tally = _Tally()
    for course in courses:
        row = _read_course_row(course, projected)
        if row.projected:
            used_projections.add(row.key)
        _file_course(tally, row)
    return _json_safe(_gpa_result(tally, len(courses), projected, used_projections))


# --- The result obs_calculate_gpa returns -----------------------------------

# Removed from the returned result because they never vary; the tool
# description states them instead.
_STATIC_RESULT_FIELDS = ("average_scope", "comment", "scale", "note")
# The same number as another field of the result.
_DUPLICATE_RESULT_FIELDS = ("calculated_term_gpa", "covered_course_count")
_PROJECTION_FIELDS = ("unused_projected_courses", "projection_complete", "applied_projection_count")
_REFERENCE_DUPLICATES = ("gpa", "credits", "term_code")


def _common_source(rows: list[dict[str, Any]], field: str) -> str | None:
    """The source most rows share; a tie goes to the registration record."""
    counts: dict[str, int] = {}
    for row in rows:
        value = row.get(field)
        if isinstance(value, str):
            counts[value] = counts.get(value, 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda value: (counts[value], value == "registration"))


def _course_row(row: dict[str, Any], credit_source: str | None, grade_source: str | None) -> dict[str, Any]:
    """One course as the result lists it: no static note, no field that repeats another."""
    kept: dict[str, Any] = {}
    for key, value in row.items():
        if key in ("note", "recorded_credit"):
            continue
        if key == "projected" and not value:
            continue
        if key == "credit_source" and value == credit_source:
            continue
        if key == "grade_source" and value == grade_source:
            continue
        # The plan values are evidence only where they differ from the credit used.
        if key in ("plan_credit", "counted_credit") and (value is None or value == row.get("credit")):
            continue
        kept[key] = value
    return kept


def compact_term_result(result: dict[str, Any], *, projection_requested: bool) -> dict[str, Any]:
    """The result of ``obs_calculate_gpa`` with every value stated once.

    A source shared by the courses is reported once as ``credit_source`` and
    ``grade_source``; a course carries its own only when it differs. Courses
    in ``ff_risk`` are named by code, since ``courses`` already holds them.
    The official record is the top-level ``official_term_*`` fields;
    ``official_reference`` keeps only what they do not say.
    """
    listed = [row for key in ("courses", "ungraded") for row in result.get(key) or [] if isinstance(row, dict)]
    credit_source = _common_source(listed, "credit_source")
    grade_source = _common_source(listed, "grade_source")
    compact: dict[str, Any] = {}
    for key, value in result.items():
        if key in _STATIC_RESULT_FIELDS or key in _DUPLICATE_RESULT_FIELDS:
            continue
        if key in _PROJECTION_FIELDS and not projection_requested:
            continue
        # Equal to ``gpa`` whenever the calculation is complete.
        if key == "known_courses_gpa" and value == result.get("gpa"):
            continue
        if key in ("courses", "ungraded"):
            value = [_course_row(row, credit_source, grade_source) for row in value]
        elif key == "ff_risk":
            value = [row.get("code") for row in value]
        elif key == "official_reference" and isinstance(value, dict):
            value = {name: item for name, item in value.items() if name not in _REFERENCE_DUPLICATES}
        compact[key] = value
        if key == "courses":
            if credit_source:
                compact["credit_source"] = credit_source
            if grade_source:
                compact["grade_source"] = grade_source
    return compact


# --- Graduation-plan evidence used by obs_calculate_gpa -------------------

PLAN_BUCKETS = ("checkMetMezuniyetList", "unusedSinifOgrenciList")
_KEY_PADDING = " \t\r\n\"'`"


def _positive_credit(value: Any) -> float | None:
    number = parse_credit(value)
    return number if number else None


def _name_key(value: Any) -> str:
    return "".join(ch for ch in normalize_lookup_text(str(value or "")) if ch.isalnum())


def plan_rows(info: Any) -> list[dict[str, Any]]:
    """Reduce the MezuniyetimeNeKaldi rows to the fields the GPA tool reads."""
    rows: list[dict[str, Any]] = []
    if not isinstance(info, dict):
        return rows
    for bucket in PLAN_BUCKETS:
        items = info.get(bucket)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                code = normalize_course_code(str(item.get("bransKodu") or ""))
            except ValueError:
                code = None
            credit = item.get("kredisiDec")
            if credit is None:
                credit = item.get("kredisi")
            rows.append({
                "code": code,
                "term": str(item.get("donem") or ""),
                "grade": normalize_grade(item.get("harfNotu")),
                "credit": _positive_credit(credit),
                "counted_credit": _positive_credit(item.get("sayilanKredi")),
                "name": _name_key(item.get("dersAdi")),
                "met": bucket == "checkMetMezuniyetList" and item.get("isMet") is not False,
                "unused": bucket == "unusedSinifOgrenciList",
            })
    return rows


def plan_credit_table(rows: list[dict[str, Any]], term_code: Any) -> dict[str, dict[str, Any]]:
    """Plan credit per course code, nearest evidence first.

    A course's credit does not depend on the attempt, so the order is: a row of
    the requested term, an undated plan slot, then a row of any other term. A
    tier whose rows disagree is not overruled by a farther tier.
    """
    term = str(term_code) if term_code else ""
    tiers: dict[str, dict[str, list[dict[str, Any]]]] = {"term": {}, "undated": {}, "other_term": {}}
    for row in rows:
        if row["code"] is None or row["credit"] is None:
            continue
        scope = "undated" if not row["term"] else "term" if not term or row["term"] == term else "other_term"
        tiers[scope].setdefault(row["code"], []).append(row)
    table: dict[str, dict[str, Any]] = {}
    for code in {code for tier in tiers.values() for code in tier}:
        for scope, tier in tiers.items():
            matched = tier.get(code)
            if not matched:
                continue
            credits = {row["credit"] for row in matched}
            if len(credits) == 1:
                entry: dict[str, Any] = {"credit": credits.pop(), "scope": scope}
                counted = {row["counted_credit"] for row in matched
                           if row["met"] and row["counted_credit"] is not None}
                if len(counted) == 1:
                    entry["counted_credit"] = counted.pop()
                table[code] = entry
            break
    return table


def resolve_projection_keys(
    projected_grades: dict[str, str] | None,
    courses: list[dict[str, Any]],
) -> tuple[dict[str, str], list[str]]:
    """Map what-if keys (course code or CRN) onto the course codes of this term.

    Returns the translated mapping and the keys that matched no course. An
    unmatched key stays in the mapping so the calculator still reports it.
    """
    codes: set[str] = set()
    by_crn: dict[str, set[str]] = {}
    for course in courses:
        key = course_key(course.get("code"))
        codes.add(key)
        crn = str(course.get("crn") or "").strip()
        if crn:
            by_crn.setdefault(crn, set()).add(key)
    translated: dict[str, str] = {}
    unmatched: list[str] = []
    for raw, grade in (projected_grades or {}).items():
        text = str(raw).strip(_KEY_PADDING)
        target = None
        if len(by_crn.get(text, ())) == 1:
            target = next(iter(by_crn[text]))
        elif course_key(text) in codes:
            target = course_key(text)
        if target is None:
            unmatched.append(str(raw))
            translated[str(raw)] = grade
            continue
        if target in translated and normalize_grade(translated[target]) != normalize_grade(grade):
            raise ValueError(f"Conflicting projected grades for {target}.")
        translated[target] = grade
    return translated, unmatched


def reconcile_credit_candidates(
    courses: list[dict[str, Any]],
    official_gpa: float | None,
    *,
    max_ambiguous: int = 6,
) -> dict[str, Any] | None:
    """Choose between kredisi and sayilanKredi only where a plan row carries both.

    The official term average decides, and only when exactly one combination of
    the carried values reproduces it. Otherwise the current credit stays and
    the courses are named. ``courses`` is updated in place.
    """
    ambiguous = [course for course in courses if len(course.get("credit_candidates") or ()) > 1]
    if not ambiguous:
        return None
    status = "undetermined"
    if official_gpa is None:
        status = "no_official_average"
    elif len(ambiguous) > max_ambiguous:
        status = "too_many_candidates"
    elif calculate_gpa(courses)["gpa"] is None:
        status = "not_comparable"
    else:
        default = tuple(course["credit"] for course in ambiguous)
        matches = []
        for combination in itertools.product(*(course["credit_candidates"] for course in ambiguous)):
            for course, credit in zip(ambiguous, combination, strict=True):
                course["credit"] = credit
            trial = calculate_gpa(courses)["gpa"]
            if trial is not None and abs(trial - official_gpa) < 0.005:
                matches.append(combination)
        chosen = default
        if default in matches:
            status = "confirmed_by_official_average" if len(matches) == 1 else "undetermined"
        elif len(matches) == 1:
            chosen, status = matches[0], "resolved_by_official_average"
        for course, credit in zip(ambiguous, chosen, strict=True):
            course["credit"] = credit
            if status == "resolved_by_official_average":
                course["credit_choice"] = "matches_official_average"
    return {
        "status": status,
        "courses": [{"code": course.get("code"), "kredisi": course.get("plan_credit"),
                     "sayilanKredi": course.get("counted_credit"), "used_credit": course["credit"]}
                    for course in ambiguous],
    }


def _term_order(term: str) -> tuple[int, int | str]:
    return (0, int(term)) if term.isdigit() else (1, term)


def attempt_notes(
    rows: list[dict[str, Any]],
    *,
    code: str | None,
    term_code: Any,
    names: set[str],
    credits: set[float],
) -> dict[str, Any]:
    """Other attempts of a course, and failed attempts that look like the same course.

    A failed unused row with another code, the same normalised name and the same
    credit is offered as ``likely_equivalent``. It is evidence, never a fact.
    """
    term = str(term_code) if term_code else ""
    names = names | {row["name"] for row in rows if code and row["code"] == code and row["name"]}
    names.discard("")
    grouped: dict[str, list[dict[str, Any]]] = {"earlier": [], "later": [], "other_terms": [],
                                                "likely_equivalent": []}
    seen: set[tuple[Any, ...]] = set()
    for row in sorted((row for row in rows if row["term"]), key=lambda row: _term_order(row["term"])):
        if row["term"] == term:
            continue
        if code and row["code"] == code:
            entry: dict[str, Any] = {"term": row["term"], "grade": row["grade"] or None}
            if row["met"]:
                entry["counted"] = True
            bucket = ("other_terms" if not term else
                      "earlier" if _term_order(row["term"]) < _term_order(term) else "later")
        elif (row["unused"] and row["code"] and row["grade"] in FAILING_GRADES
              and row["name"] in names and row["credit"] in credits):
            entry = {"code": row["code"], "term": row["term"], "grade": row["grade"],
                     "evidence": ["same_name", "same_credit"], "certain": False}
            bucket = "likely_equivalent"
        else:
            continue
        marker = (bucket, repr(sorted(entry.items())))
        if marker not in seen:
            seen.add(marker)
            grouped[bucket].append(entry)
    return {bucket: entries for bucket, entries in grouped.items() if entries}


_RETAKE_KEYS = frozenset({"credit", "old_grade"})
_COUNTED_GRADES = tuple(grade for grade, value in LETTER_TO_GRADE.items() if value is not None)


def _retake_totals(retakes: Any) -> tuple[float, float]:
    """Total credit and old GPA points of the retaken courses."""
    if retakes is None:
        return 0.0, 0.0
    example = '[{"credit": 3, "old_grade": "FF"}]'
    if not isinstance(retakes, (list, tuple)):
        raise ValueError(f"retakes must be a list such as {example}")
    credits: list[float] = []
    points: list[float] = []
    for index, item in enumerate(retakes):
        where = f"retakes[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{where} must be an object such as {example[1:-1]}")
        unknown = sorted(str(key) for key in set(item) - _RETAKE_KEYS)
        if unknown:
            raise ValueError(f"{where} has unknown field(s): {', '.join(unknown)}. Allowed: credit, old_grade")
        raw = item.get("credit")
        credit = None if isinstance(raw, str) else parse_credit(raw)
        if credit is None or credit <= 0:
            raise ValueError(f"{where}.credit must be a number greater than zero, e.g. 3 or 3.5")
        grade = normalize_grade(item.get("old_grade")) if isinstance(item.get("old_grade"), str) else ""
        coefficient = LETTER_TO_GRADE.get(grade)
        if coefficient is None:
            raise ValueError(
                f"{where}.old_grade must be the grade that counts in the GPA now, one of: "
                f"{', '.join(_COUNTED_GRADES)}. A course whose earlier grade never counted "
                "is a new course: leave it out of retakes."
            )
        credits.append(credit)
        points.append(credit * coefficient)
    return math.fsum(credits), math.fsum(points)


def calculate_target_gpa(
    *,
    current_gpa: float,
    current_credits: float,
    target_gpa: float,
    future_credits: float,
    retakes: Any = None,
) -> dict[str, Any]:
    """Calculate the average required over future credits to reach a target.

    This is a planning estimate.  It deliberately works from aggregate GPA
    points so it can be used without exposing a transcript.

    ``retakes`` names the part of ``future_credits`` that repeats a course
    already counted in ``current_gpa``. A retake replaces its old grade: its
    credit does not grow the denominator and its old points leave the total.
    """
    values = {}
    for name, raw in {
        "current_gpa": current_gpa, "current_credits": current_credits,
        "target_gpa": target_gpa, "future_credits": future_credits,
    }.items():
        if isinstance(raw, bool):
            raise ValueError(f"{name} must be a finite number")
        try:
            number = float(raw)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite number") from exc
        if not math.isfinite(number):
            raise ValueError(f"{name} must be a finite number")
        values[name] = number
    if not 0.0 <= values["current_gpa"] <= 4.0:
        raise ValueError("current_gpa must be between 0.00 and 4.00")
    if not 0.0 <= values["target_gpa"] <= 4.0:
        raise ValueError("target_gpa must be between 0.00 and 4.00")
    if values["current_credits"] < 0:
        raise ValueError("current_credits cannot be negative")
    if values["future_credits"] <= 0:
        raise ValueError("future_credits must be greater than zero")

    retake_credits, retake_points = _retake_totals(retakes)
    considered = retake_credits > 0
    current_points = values["current_gpa"] * values["current_credits"]
    if retake_credits > values["future_credits"] + 1e-9:
        raise ValueError(
            "retakes add up to more credits than future_credits; future_credits is the total "
            "to be taken, retakes included"
        )
    if retake_credits > values["current_credits"] + 1e-9:
        raise ValueError("retakes add up to more credits than current_credits, which already counts them")
    # current_gpa is a two-decimal value, so the points it implies are off by
    # up to 0.005 per credit.
    if retake_points > current_points + 0.005 * values["current_credits"] + 1e-9:
        raise ValueError("retakes carry more points than current_gpa and current_credits allow")

    kept_points = current_points - retake_points
    resulting_credits = values["current_credits"] + values["future_credits"] - retake_credits
    target_points = values["target_gpa"] * resulting_credits
    required_points = target_points - kept_points
    required_average = required_points / values["future_credits"]
    maximum_possible_gpa = (kept_points + 4.0 * values["future_credits"]) / resulting_credits
    if not all(math.isfinite(value) for value in (current_points, target_points, required_points, required_average, maximum_possible_gpa)):
        raise ValueError("Credit arithmetic exceeds the supported finite numeric range")
    feasible = required_average <= 4.0

    result: dict[str, Any] = {
        **values,
        "required_future_average": round(max(0.0, required_average), 2),
        "required_future_points": round(max(0.0, required_points), 2),
        "feasible_on_4_scale": feasible,
        "already_at_or_above_target": values["current_gpa"] >= values["target_gpa"],
        "target_reached_with_zero_future_points": required_average <= 0.0,
        "maximum_possible_gpa": round(maximum_possible_gpa, 2),
        "retakes_considered": considered,
        "resulting_credits": round(resulting_credits, 2),
    }
    if considered:
        result.update({
            "retake_credits": round(retake_credits, 2),
            "retake_old_points": round(retake_points, 2),
            "new_credits": round(values["future_credits"] - retake_credits, 2),
            "note": (
                "Bilgi amaçlı tahmindir; ders tekrarları yalnızca retakes ile bildirildiği kadar "
                "hesaba katıldı, özel OBS kuralları katılmadı."
            ),
        })
    else:
        result.update({
            "assumption": (
                "Bu sonuç, future_credits içindeki hiçbir dersin tekrar olmadığını varsayar. "
                "Tekrar alınan ders eski notunun yerine geçer ve kredi eklemez; bu durumda "
                "ulaşılabilir ortalama daha yüksektir. Tekrarları retakes ile bildirin ya da "
                "öğrencinin kendi kaydından hesaplayan obs_simulate_cumulative_gpa(target_gpa=...) "
                "aracını kullanın."
            ),
            "note": "Bilgi amaçlı tahmindir; ders tekrarları ve özel OBS kuralları hesaba katılmaz.",
        })
    return result
