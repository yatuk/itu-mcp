"""Shared İTÜ grade meanings for GPA and prerequisite checks.

Undergraduate regulation articles 4 and 20 define the numeric coefficients and
exclude BL/BZ/T/E/M from GPA. Legacy spellings remain supported separately.
"""
from __future__ import annotations

from typing import Any

GRADE_VALUES: dict[str, float] = {
    "AA": 4.00, "BA+": 3.75, "BA": 3.50, "BB+": 3.25, "BB": 3.00,
    "CB+": 2.75, "CB": 2.50, "CC+": 2.25, "CC": 2.00,
    "DC+": 1.75, "DC": 1.50, "DD+": 1.25, "DD": 1.00,
    "FD": 0.50, "FF": 0.00, "VF": 0.00,
}
PASS_GRADES = frozenset({"BL", "GE", "M", "MU", "TR", "S"})
FAILING_GRADES = frozenset({"FD", "FF", "VF", "BZ", "KF", "IA"})
INCOMPLETE_GRADES = frozenset({"NA", "E", "EK", "T"})
GPA_EXCLUDED_GRADES = PASS_GRADES | INCOMPLETE_GRADES | {"BZ", "KF", "IA"}
LETTER_TO_GRADE: dict[str, float | None] = {
    **GRADE_VALUES, **dict.fromkeys(sorted(GPA_EXCLUDED_GRADES)),
}


def normalize_grade(value: Any) -> str:
    return str(value or "").strip().upper()


def grade_satisfies(earned: Any, minimum: Any) -> bool | None:
    """Compare an asserted completed course, preserving unknown grade evidence.

    Completion without a grade proves a requirement with no grade threshold.
    Non-numeric passing grades cannot establish a numeric minimum.
    """
    grade, required = normalize_grade(earned), normalize_grade(minimum)
    if grade in FAILING_GRADES or grade in INCOMPLETE_GRADES:
        return False
    if not required:
        return True if not grade or grade in GRADE_VALUES or grade in PASS_GRADES else None
    if required in PASS_GRADES:
        return True if grade in PASS_GRADES or grade in GRADE_VALUES else None
    if required not in GRADE_VALUES or grade not in GRADE_VALUES:
        return None
    return GRADE_VALUES[grade] >= GRADE_VALUES[required]


def grade_rank(value: Any) -> float:
    """Choose the strongest available evidence among successful attempts."""
    grade = normalize_grade(value)
    return GRADE_VALUES.get(grade, 0.0 if grade in PASS_GRADES else -1.0)
