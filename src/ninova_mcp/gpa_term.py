"""The steps of ``obs_calculate_gpa`` between the OBS reads and the calculator.

Pure functions, no I/O: they turn the registered rows of one term and the
graduation-plan evidence into the course records ``gpa.calculate_gpa`` takes,
and describe what could not be settled. The tool method keeps the reads and
their order.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .archive import normalize_course_code
from .client import NinovaError
from .gpa import _name_key, _positive_credit, attempt_notes, parse_credit
from .gpa_reference import official_term_reference
from .grading import LETTER_TO_GRADE, normalize_grade


def registered_rows(payload: Any) -> list[dict[str, Any]]:
    """The registered-course rows of a successful OBS response.

    Raises ``NinovaError`` for a failed response or an unrecognized shape, so
    that no average is calculated from a partial list.
    """
    if not isinstance(payload, dict) or (
        "statusCode" in payload and (type(payload["statusCode"]) is not int or payload["statusCode"] != 0)
    ):
        raise NinovaError("Registered courses could not be read successfully; GPA was not calculated.")
    registered = payload.get("kayitSinifResultList")
    if not isinstance(registered, list) or any(not isinstance(item, dict) for item in registered):
        raise NinovaError("Registered-course data has an unrecognized shape; GPA was not calculated.")
    return registered


def _credit_with_plan_fallback(item: dict[str, Any], plan: dict[str, Any]) -> tuple[Any, str, bool]:
    """The credit to use, its source, and whether none was found.

    The registered-course endpoint can omit usable credit. The reported value
    is kept when it is a positive number; otherwise the degree-plan credit is
    used and named as the source. A course's credit does not depend on the
    attempt, so a plan row of another term is accepted when this term has none.
    """
    credit = item.get("kredi")
    if _positive_credit(credit) is not None:
        return credit, "registration", False
    fallback = plan.get("credit")
    if not fallback:
        return credit, "registration", True
    return fallback, "degree_plan_other_term" if plan["scope"] == "other_term" else "degree_plan", False


def _grade_with_plan_fallback(
    item: dict[str, Any], plan_grades: dict[str, str], code_key: str | None,
) -> tuple[Any, str]:
    """The grade to use and its source.

    Observed on at least one account: the registered-course endpoint's
    harfNotu comes back None for every course in every term, including terms
    years in the past, not just "not graded yet". ``plan_grades`` holds the
    graduation-plan grades of this term only: a retaken course has one entry
    per attempt with a different grade each time, so a code-only lookup would
    risk picking the wrong attempt's grade.
    """
    grade = item.get("harfNotu")
    if not grade:
        fallback = plan_grades.get(code_key)  # type: ignore[arg-type]
        if fallback:
            return fallback, "degree_plan"
    return grade, "registration"


def term_course(
    item: dict[str, Any],
    *,
    credit_table: dict[str, dict[str, Any]],
    plan_grades: dict[str, str],
    plan: list[dict[str, Any]],
    term_code: Any,
) -> tuple[dict[str, Any], bool]:
    """One registered row as a course record, and whether its credit is missing.

    ``credit_table`` is ``gpa.plan_credit_table`` and ``plan`` is
    ``gpa.plan_rows`` of the same graduation payload.
    """
    code = f"{item.get('bransKodu', '')} {item.get('dersKodu', '')}".strip()
    try:
        code_key = normalize_course_code(code)
    except ValueError:
        code_key = None
    plan_entry = credit_table.get(code_key) or {}  # type: ignore[arg-type]
    credit, credit_source, credit_missing = _credit_with_plan_fallback(item, plan_entry)
    grade, grade_source = _grade_with_plan_fallback(item, plan_grades, code_key)

    course = {
        "code": code,
        "name": item.get("dersAdiTR") or item.get("dersAdiEN", ""),
        "credit": credit,
        "recorded_credit": item.get("kredi"),
        "plan_credit": plan_entry.get("credit"),
        "credit_source": credit_source,
        "grade": grade,
        "grade_source": grade_source,
        "crn": item.get("crn"),
    }
    counted = plan_entry.get("counted_credit")
    if counted is not None:
        course["counted_credit"] = counted
        # Only a plan row that itself carries two different values makes
        # the weight ambiguous. Nothing else is ever tried as a credit.
        if credit_source != "registration" and counted != credit:
            course["credit_candidates"] = sorted({credit, counted})
    attempts = attempt_notes(
        plan, code=code_key, term_code=term_code,
        names={_name_key(item.get(field)) for field in ("dersAdiTR", "dersAdiEN")},
        credits={value for value in (parse_credit(credit), counted) if value},
    )
    if attempts:
        course["attempts"] = attempts
    return course, credit_missing


def every_course_failed(courses: list[dict[str, Any]]) -> bool:
    """Whether the term's grades are all failing ones that count as zero.

    OBS reports an average of 0 with 0 earned credits both for a term that
    is not graded yet and for a term in which every course was failed. The
    grades of this term tell the two apart: at least one grade weighs zero
    and no course is ungraded, unrecognized or weighs more. Grades that stay
    out of the average do not matter.
    """
    coefficients = [LETTER_TO_GRADE.get(normalize_grade(course["grade"]), -1.0)
                    if course["grade"] else -1.0 for course in courses]
    return (
        any(value == 0 for value in coefficients)
        and all(value is None or value == 0 for value in coefficients)
    )


def read_official_reference(
    read_status: Callable[[], Any],
    term_code: Any,
    *,
    requested: bool,
    zero_average_is_final: bool,
) -> dict[str, Any]:
    """The official record of the term, read with ``read_status`` only when requested.

    A failed read is reported in the reference and does not stop the estimate.
    """
    official_payload: Any = {}
    reference_error = None
    if requested:
        try:
            official_payload = read_status()
        except (NinovaError, ValueError):
            reference_error = "Official term record could not be read."
    reference = official_term_reference(official_payload, term_code, zero_average_is_final=zero_average_is_final)
    if not requested:
        reference["status"] = "not_requested"
    if reference_error:
        reference["error"] = reference_error
    return reference


def projection_status(result: dict[str, Any]) -> str:
    """How much of the requested what-if reached a course of the term."""
    if not result["unused_projected_courses"]:
        return "applied"
    return "partially_applied" if result["applied_projection_count"] else "not_applied"


def unmatched_projection_report(unmatched_keys: list[str], courses: list[dict[str, Any]]) -> dict[str, Any]:
    """The what-if keys that matched no course, with the keys that would."""
    return {
        "unmatched_projected_keys": unmatched_keys,
        "valid_projected_keys": [
            {"code": course["code"], "crn": None if course["crn"] is None else str(course["crn"])}
            for course in courses
        ],
        "projection_warning": (
            "No course of this term matches: " + ", ".join(unmatched_keys)
            + ". Use a code or CRN from valid_projected_keys."
        ),
    }


def credits_still_needed(missing_credits: list[str], excluded_courses: list[dict[str, Any]]) -> list[str]:
    """Courses without a credit whose grade needs one.

    A course whose grade stays out of the average (BL, BZ and the like)
    needs no credit: a pass/fail course of zero credit is not a gap.
    """
    credit_not_needed = {course.get("code") for course in excluded_courses}
    return [code for code in missing_credits if code not in credit_not_needed]
