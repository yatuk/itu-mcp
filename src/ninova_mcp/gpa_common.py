"""Decisions the term average and the cumulative simulation make the same way.

``obs_calculate_gpa`` (``gpa.py``, ``gpa_term.py``) and
``obs_simulate_cumulative_gpa`` (``gpa_simulation.py``) answer GPA questions
from the same OBS record. Whatever both of them have to decide is decided
here, once: how a graduation-plan row is read, when two course names are the
same course, which of two credit values a plan row carries is the GPA weight,
when a calculated average reproduces an official one, and which course a
what-if key means. ``tests/test_gpa_cross_tool_consistency.py`` holds the two
tools to it.

Pure functions, no I/O.
"""

from __future__ import annotations

import itertools
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from .archive import normalize_course_code
from .grading import normalize_grade

PLAN_BUCKETS = ("checkMetMezuniyetList", "unusedSinifOgrenciList")
# A term with more rows of two credit values than this is not enumerated.
MAX_AMBIGUOUS_CREDIT_ROWS = 6
KEY_PADDING = " \t\r\n\"'`"

# An official average is the exact average rounded to two decimals, so the
# exact value lies within half a unit of the last place, 0.005, of it. The
# calculated average is therefore compared unrounded and by distance, with the
# boundary included: whether OBS rounds an exact half up or down is not known,
# and both are accepted. Comparing two rounded values would make the answer
# depend on how binary floating point rounds a half (``round(2.675, 2)`` is
# 2.67). 1e-9 absorbs the error of the float division.
AVERAGE_TOLERANCE = 0.005 + 1e-9


def reproduces_official(calculated: float | None, official: float | None) -> bool:
    """Whether an unrounded calculated average rounds to the official two-decimal one."""
    return calculated is not None and official is not None and abs(calculated - official) <= AVERAGE_TOLERANCE


def round_average(value: float | None) -> float | None:
    """An average as it is reported: two decimals, an exact half rounded up."""
    return None if value is None else round(value + 1e-9, 2)


def course_key(value: Any) -> str:
    try:
        return normalize_course_code(str(value or ""))
    except ValueError:
        # Keep support for callers using display labels instead of OBS codes.
        return " ".join(str(value or "").upper().split())


def finite_number(value: Any) -> float | None:
    """Read a finite number; a decimal comma is accepted, a bool is not a number."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def parse_credit(value: Any) -> float | None:
    """Parse a finite nonnegative credit value, preserving a genuine zero."""
    number = finite_number(value)
    return number if number is not None and number >= 0 else None


def recorded_grade(value: Any) -> str:
    """A grade as a record states it, normalized; ``""`` when there is none yet.

    Only text is a grade. A missing value or a number, a bool or any other
    non-text value means "no grade yet", never an unrecognized grade named
    after the value.
    """
    return normalize_grade(value) if isinstance(value, str) else ""


_NAME_WORD = re.compile(r"[^\W_]+")
_AND_WORDS = frozenset({"and", "ve"})


def course_name_key(value: Any) -> str:
    """The key under which two spellings of one course name are equal.

    The rule, in order:

    - Case does not matter, and the Turkish letters are folded with it: ``İ``,
      ``I``, ``ı`` and ``i`` are one letter, and an accented letter equals its
      base letter (``ş`` and ``s``, ``ö`` and ``o``).
    - ``&``, ``and`` and ``ve`` are the same word.
    - Punctuation and whitespace only separate words, and the separators
      themselves are dropped, so ``Data-Base``, ``Data Base`` and ``Database``
      are equal.
    - Digits and letters are kept as written. A roman numeral is not an arabic
      one (``Physics II`` is not ``Physics 2``) and a level marker is part of
      the name (``Analysis of Algorithms I`` is not ``Analysis of Algorithms
      II``, and neither is ``Analysis of Algorithms``).

    The key is evidence for "same course" only together with something else
    (the same credit, the same course number); it is not shown to anyone.
    """
    text = str(value or "").translate({ord("ı"): "i", ord("İ"): "i"}).casefold()
    text = unicodedata.normalize("NFKD", text.replace("&", " and "))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return "".join("and" if word in _AND_WORDS else word for word in _NAME_WORD.findall(text))


def term_number(term: Any) -> int | None:
    """A term code as a number that orders terms; ``None`` when it is not a number.

    Only numeric term codes can be put in order. A term that is not one has
    no known place: the simulation treats it like an undated record and the
    term tool does not call it earlier or later.
    """
    text = str(term or "").strip()
    return int(text) if text.isdigit() else None


# --- Graduation-plan rows ----------------------------------------------------


def plan_row_credit(row: dict[str, Any]) -> float | None:
    """The credit of a graduation-plan row.

    ``kredisiDec`` when it is a finite nonnegative number, otherwise
    ``kredisi`` (a decimal comma is accepted). A credit of exactly 0 is a real
    zero-credit course, not a missing value; ``None`` means no readable credit.
    """
    credit = parse_credit(row.get("kredisiDec"))
    return credit if credit is not None else parse_credit(row.get("kredisi"))


def plan_records(info: Any) -> list[dict[str, Any]]:
    """Every row of the MezuniyetimeNeKaldi payload, read once for both tools.

    ``code`` is the course key (``None`` for a row without a code, which is a
    plan slot and not a course), ``name`` the name key. ``label`` and ``title``
    are the code and the name as OBS wrote them. ``counted_credit`` is
    ``sayilanKredi`` when it is a positive number; it is a candidate GPA weight
    only on a ``met`` row.
    """
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
            label = str(item.get("bransKodu") or "").strip()
            title = str(item.get("dersAdi") or "").strip()
            counted = parse_credit(item.get("sayilanKredi"))
            rows.append({
                "code": course_key(label) or None,
                "term": str(item.get("donem") or "").strip(),
                "grade": recorded_grade(item.get("harfNotu")),
                "credit": plan_row_credit(item),
                "counted_credit": counted if counted else None,
                "name": course_name_key(title),
                "met": bucket == "checkMetMezuniyetList" and item.get("isMet") is not False,
                "unused": bucket == "unusedSinifOgrenciList",
                "label": label,
                "title": title,
            })
    return rows


def conflicting_attempts(rows: list[dict[str, Any]]) -> set[tuple[str, str]]:
    """(course, term) pairs whose graded rows state two grades or two credits.

    One course has one grade and one credit in one term. Rows that disagree
    are not an attempt either tool can count: the term tool reports the grade
    or the credit as unknown and the simulation leaves the attempt out.
    """
    seen: dict[tuple[str, str], set[tuple[str, float | None]]] = {}
    for row in rows:
        if row["code"] and row["term"] and row["grade"]:
            seen.setdefault((row["code"], row["term"]), set()).add((row["grade"], row["credit"]))
    return {key for key, stated in seen.items() if len(stated) > 1}


# --- Which of two credit values is the GPA weight ----------------------------


@dataclass(frozen=True)
class CreditDecision:
    """What the official term average says about the rows with two credit values.

    ``reason`` is one of ``no_official_average``, ``too_many_candidates``,
    ``not_comparable``, ``none_reproduces``, ``one_reproduces`` and
    ``several_reproduce``. ``decided`` has one entry per row, in order: the
    position of the credit the official average fixes among the row's
    candidates, or ``None`` where it fixes none.
    """

    reason: str
    decided: tuple[int | None, ...]


def decide_credits(
    fixed: list[tuple[float, float]],
    open_rows: list[tuple[tuple[float, ...], float]],
    official: float | None,
    *,
    comparable: bool = True,
    max_rows: int = MAX_AMBIGUOUS_CREDIT_ROWS,
) -> CreditDecision:
    """Choose the GPA weight of every row of one term that carries two credit values.

    ``fixed`` holds ``(credit, coefficient)`` of the term's counted courses
    with one credit value, ``open_rows`` ``(candidate credits, coefficient)``
    of those with two. ``comparable`` is False when the term has a course
    whose grade or credit is not known, so its average cannot be rebuilt.

    Every combination of candidate credits is tried against the official term
    average (see ``AVERAGE_TOLERANCE``):

    - exactly one combination reproduces it: every row takes its value there;
    - several do: a row is decided only when it has the same value in all of
      them, the other rows stay undecided;
    - none does: nothing is decided.
    """
    undecided: tuple[int | None, ...] = (None,) * len(open_rows)
    if official is None:
        return CreditDecision("no_official_average", undecided)
    if len(open_rows) > max_rows:
        return CreditDecision("too_many_candidates", undecided)
    if not comparable:
        return CreditDecision("not_comparable", undecided)
    base_credits = math.fsum(credit for credit, _ in fixed)
    base_points = math.fsum(credit * coefficient for credit, coefficient in fixed)
    reproducing: list[tuple[int, ...]] = []
    for picks in itertools.product(*(range(len(candidates)) for candidates, _ in open_rows)):
        chosen = [(candidates[pick], coefficient)
                  for pick, (candidates, coefficient) in zip(picks, open_rows, strict=True)]
        credits = math.fsum((base_credits, *(credit for credit, _ in chosen)))
        points = math.fsum((base_points, *(credit * coefficient for credit, coefficient in chosen)))
        if credits > 0 and reproduces_official(points / credits, official):
            reproducing.append(picks)
    if not reproducing:
        return CreditDecision("none_reproduces", undecided)
    decided = tuple(picks[0] if len(set(picks)) == 1 else None for picks in zip(*reproducing, strict=True))
    return CreditDecision("one_reproduces" if len(reproducing) == 1 else "several_reproduce", decided)


# --- What-if keys -------------------------------------------------------------


def projection_target(raw: Any, courses_of_crn: dict[str, set[str]], course_keys: set[str]) -> str | None:
    """The course key a what-if key (a CRN or a course code) names, or ``None``.

    Quotes and whitespace around the key are ignored. A CRN is tried first and
    counts only when exactly one course carries it; a CRN shared by two
    courses names neither.
    """
    text = str(raw).strip(KEY_PADDING)
    owners = courses_of_crn.get(text, ())
    if len(owners) == 1:
        return next(iter(owners))
    key = course_key(text)
    return key if key in course_keys else None
