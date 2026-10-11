"""Cumulative GPA simulation from the student's own OBS record.

Pure functions, no I/O. ``simulate_cumulative_gpa`` takes three payloads as
OBS returns them (graduation plan, per-term registration status, registered
courses of the projected term) and the what-if arguments, and returns the
result dict of the ``obs_simulate_cumulative_gpa`` tool.

The model:

- Cumulative GPA is ``sum(credit * coefficient) / sum(credit)`` over the
  latest graded attempt of each course. Earlier attempts of the same course
  drop out with both their credit and their points. A failed latest attempt
  counts with coefficient 0. Grades without a coefficient never count.
- "Same course" is the same normalized code, or an inferred equivalent: a
  failed attempt followed by a later attempt under another code with the same
  normalized name and the same credit. An inferred equivalence between past
  attempts is applied only when the official cumulative series needs it.
- A plan row can carry two credit values (``kredisi`` and ``sayilanKredi``).
  The official term average of that row's term decides which one is the GPA
  weight. When it cannot decide, the result is given as a range.
"""

from __future__ import annotations

import itertools
import math
import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Any

from .gpa import COUNTED_GRADES, LETTER_TO_GRADE, UNCOUNTED_GRADES, course_key, parse_credit
from .gpa import finite_number as _number
from .gpa import letter_grade_argument as _grade_argument
from .grading import FAILING_GRADES, normalize_grade

GRADED = COUNTED_GRADES
UNGRADED = UNCOUNTED_GRADES
_FAILING = frozenset(grade for grade in FAILING_GRADES if LETTER_TO_GRADE.get(grade) is not None)
_EXTRA_KEYS = frozenset({"code", "grade", "credit", "replaces"})
_AFTER_EVERYTHING = 10**9
# Two-decimal official values: accept either rounding of an exact half.
_TOLERANCE = 0.005 + 1e-9
_MAX_EQUIVALENCE_CANDIDATES = 8
_MAX_CREDIT_ROWS_PER_TERM = 6
_MAX_RANGE_ROWS = 4

NOTE = (
    "Estimate computed from the student's own OBS record under the stated assumptions "
    "(latest attempt counts, inferred equivalences, chosen credits). It is not an official "
    "result; the official GPA is the one OBS publishes after grades are final."
)


@dataclass(frozen=True)
class _Attempt:
    key: str
    code: str
    name: str
    term: str
    grade: str
    credit: float | None
    alt_credit: float | None = None
    source: str = "plan"
    rank: int = 1
    term_order: int | None = None

    @property
    def term_key(self) -> int:
        return self.term_order if self.term_order is not None else _term_key(self.term)

    @property
    def coefficient(self) -> float | None:
        return LETTER_TO_GRADE.get(self.grade)


def _term_key(term: Any) -> int:
    text = str(term or "").strip()
    return int(text) if text.isdigit() else 0


def _name_key(value: Any) -> str:
    """Case, accent, punctuation and whitespace insensitive course name."""
    text = str(value or "").translate({ord("ı"): "i", ord("İ"): "i"}).casefold()
    text = unicodedata.normalize("NFKD", text.replace("&", " and "))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _same(left: float | None, right: float | None) -> bool:
    return left is not None and right is not None and abs(left - right) < 1e-9


def _matches(calculated: float | None, official: float) -> bool:
    return calculated is not None and abs(calculated - official) <= _TOLERANCE


def _round2(value: float | None) -> float | None:
    return None if value is None else round(value + 1e-9, 2)


def _clean(value: float) -> float:
    return round(value, 4)


def _root(groups: dict[str, str], key: str) -> str:
    while groups.get(key, key) != key:
        key = groups[key]
    return key


# ---------------------------------------------------------------- arguments


def _target_argument(value: Any) -> float | None:
    if value is None:
        return None
    target = None if isinstance(value, (bool, str)) or not isinstance(value, (int, float)) else float(value)
    if target is None or not math.isfinite(target) or not 0.0 <= target <= 4.0:
        raise ValueError("target_gpa must be a number between 0.00 and 4.00, e.g. 2.5.")
    return target


def normalize_arguments(
    projected_grades: Any = None,
    default_grade: Any = None,
    extra_courses: Any = None,
    target_gpa: Any = None,
) -> dict[str, Any]:
    """Validate the what-if arguments. Raises ``ValueError`` with the valid values."""
    target = _target_argument(target_gpa)
    if projected_grades is None:
        projected_grades = {}
    if not isinstance(projected_grades, dict):
        raise ValueError('projected_grades must be an object such as {"CEN 411E": "BB", "15570": "BA"}.')
    grades: dict[str, str] = {}
    for raw_key, raw_grade in projected_grades.items():
        key = str(raw_key).strip() if isinstance(raw_key, (str, int)) and not isinstance(raw_key, bool) else ""
        if not key:
            raise ValueError("projected_grades keys must be a course code or a CRN.")
        grades[key] = _grade_argument(raw_grade, f"projected_grades[{key!r}]")
    default = None if default_grade is None else _grade_argument(default_grade, "default_grade")

    if extra_courses is None:
        extra_courses = []
    if not isinstance(extra_courses, (list, tuple)):
        raise ValueError('extra_courses must be a list such as [{"code": "MAT 271E", "grade": "CC", "credit": 3}].')
    extras: list[dict[str, Any]] = []
    for index, item in enumerate(extra_courses):
        where = f"extra_courses[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{where} must be an object with code, grade and optional credit.")
        unknown = sorted(set(item) - _EXTRA_KEYS)
        if unknown:
            raise ValueError(
                f"{where} has unknown field(s): {', '.join(map(str, unknown))}. "
                "Allowed: code, grade, credit, replaces."
            )
        code = item["code"].strip() if isinstance(item.get("code"), str) else ""
        if not code:
            raise ValueError(f'{where} needs a course code, e.g. "MAT 271E".')
        credit = None
        if item.get("credit") is not None:
            credit = parse_credit(item["credit"])
            if credit is None:
                raise ValueError(f"{where}.credit must be a nonnegative number, e.g. 3 or 3.5.")
        replaces = item.get("replaces")
        if replaces is not None and (not isinstance(replaces, str) or not replaces.strip()):
            raise ValueError(f'{where}.replaces must be the code of the course it replaces, e.g. "BLG 335E".')
        key = course_key(code)
        if any(extra["key"] == key for extra in extras):
            raise ValueError(f"extra_courses names {code} more than once.")
        extras.append({
            "key": key, "code": code,
            "grade": _grade_argument(item.get("grade"), f"{where}.grade"),
            "credit": credit,
            "replaces": course_key(replaces) if replaces else None,
        })
    return {"grades": grades, "default": default, "extras": extras, "target": target}


# ------------------------------------------------------------------ parsing


def _graduation_info(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    nested = payload.get("graduation")
    for candidate in (
        payload.get("mezuniyetimeNeKaldiBilgi"),
        nested.get("mezuniyetimeNeKaldiBilgi") if isinstance(nested, dict) else None,
    ):
        if isinstance(candidate, dict):
            return candidate
    return payload if "checkMetMezuniyetList" in payload or "unusedSinifOgrenciList" in payload else {}


def _row_credit(row: dict[str, Any]) -> float | None:
    credit = parse_credit(row.get("kredisiDec"))
    return credit if credit is not None else parse_credit(row.get("kredisi"))


def _read_record(info: dict[str, Any]) -> tuple[list[_Attempt], dict[str, set[float]], list[str]]:
    """Graded attempts, the credit values seen per course code, and warnings."""
    attempts: list[_Attempt] = []
    plan_credits: dict[str, set[float]] = {}
    seen: set[tuple[str, str]] = set()
    without_credit: list[str] = []
    for bucket, source, rank in (("checkMetMezuniyetList", "plan", 1), ("unusedSinifOgrenciList", "unused", 0)):
        rows = info.get(bucket)
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            code = str(row.get("bransKodu") or "").strip()
            if not code:
                continue
            key = course_key(code)
            credit = _row_credit(row)
            if credit is not None:
                plan_credits.setdefault(key, set()).add(credit)
            grade = normalize_grade(row.get("harfNotu"))
            if not grade:
                continue
            term = str(row.get("donem") or "").strip()
            if term and (key, term) in seen:
                continue
            seen.add((key, term))
            alt = None
            if source == "plan" and LETTER_TO_GRADE.get(grade) is not None:
                counted = parse_credit(row.get("sayilanKredi"))
                if counted is not None and counted > 0:
                    if credit is None:
                        credit = counted
                    elif not _same(counted, credit):
                        alt = counted
            if credit is None and LETTER_TO_GRADE.get(grade) is not None:
                without_credit.append(f"{code} ({term or 'no term'})")
            attempts.append(_Attempt(
                key=key, code=code, name=str(row.get("dersAdi") or "").strip(), term=term,
                grade=grade, credit=credit, alt_credit=alt, source=source, rank=rank,
            ))
    attempts.sort(key=lambda a: (a.term_key, a.rank))
    warnings = []
    if without_credit:
        warnings.append(
            "attempt_without_credit: graded attempts with no readable credit were left out: "
            + ", ".join(without_credit)
        )
    return attempts, plan_credits, warnings


def _official_terms(payload: Any, official_gpa: float | None) -> tuple[list[dict[str, Any]], str | None]:
    """The per-term official record of one program, oldest first."""
    programs = payload.get("kayitDurumuList") if isinstance(payload, dict) else None
    series: list[list[dict[str, Any]]] = []
    for program in programs if isinstance(programs, list) else []:
        rows = program.get("kayitDurumuDonemList") if isinstance(program, dict) else None
        by_term: dict[str, dict[str, Any] | None] = {}
        for row in rows if isinstance(rows, list) else []:
            term = str(row.get("akademikDonemKodu") or "").strip() if isinstance(row, dict) else ""
            if not term.isdigit():
                continue
            by_term[term] = None if term in by_term else {
                "term": term,
                "term_gpa": _number(row.get("donemlikNotOrtalamasi")),
                "cumulative_gpa": _number(row.get("genelNotOrtalamasi")),
                "credits": _number(row.get("verilenKredi")),
            }
        terms = sorted((t for t in by_term.values() if t), key=lambda t: int(t["term"]))
        if terms:
            series.append(terms)
    if len(series) <= 1:
        return (series[0] if series else []), None
    if official_gpa is not None:
        fitting = [
            terms for terms in series
            if any(t["cumulative_gpa"] is not None and abs(t["cumulative_gpa"] - official_gpa) < 1e-9
                   for t in terms[-2:])
        ]
        if len(fitting) == 1:
            return fitting[0], None
    return [], (
        "official_series_ambiguous: the per-term record lists more than one program and none "
        "could be matched to this plan, so the model was not checked against it."
    )


def _read_registered(payload: Any) -> list[dict[str, Any]]:
    rows = payload.get("kayitSinifResultList") if isinstance(payload, dict) else payload
    courses: dict[str, dict[str, Any]] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        code = f"{str(row.get('bransKodu') or '').strip()} {str(row.get('dersKodu') or '').strip()}".strip()
        if not code:
            continue
        key = course_key(code)
        crn = str(row.get("crn") or "").strip()
        if key in courses:
            if crn and crn not in courses[key]["crns"]:
                courses[key]["crns"].append(crn)
            continue
        name_en = str(row.get("dersAdiEN") or "").strip()
        name_tr = str(row.get("dersAdiTR") or "").strip()
        credit = parse_credit(row.get("kredi"))
        courses[key] = {
            "key": key, "code": code, "crns": [crn] if crn else [],
            "name": name_en or name_tr,
            "names": {name for name in (_name_key(name_en), _name_key(name_tr)) if name},
            "credit": credit if credit else None,
            "supplied_credit": None,
        }
    return list(courses.values())


# -------------------------------------------------------------------- model


def _state(attempts: list[_Attempt], groups: dict[str, str], upto: int | None = None) -> dict[str, _Attempt]:
    """The attempt that counts for each course: the latest one with a coefficient."""
    state: dict[str, _Attempt] = {}
    for attempt in attempts:  # sorted by term
        if upto is not None and attempt.term_key > upto:
            break
        if attempt.coefficient is None or attempt.credit is None:
            continue
        state[_root(groups, attempt.key)] = attempt
    return state


def _sum(state: dict[str, _Attempt]) -> tuple[float, float]:
    counted = [(a.credit or 0.0, a.coefficient or 0.0) for a in state.values()]
    return math.fsum(c for c, _ in counted), math.fsum(c * k for c, k in counted)


def _gpa(credits: float, points: float) -> float | None:
    return points / credits if credits > 0 else None


def _resolve_credits(
    attempts: list[_Attempt], official: dict[str, dict[str, Any]],
) -> tuple[list[_Attempt], list[dict[str, Any]], list[int]]:
    """Pick between ``kredisi`` and ``sayilanKredi`` with the official term average."""
    attempts = list(attempts)
    choices: list[dict[str, Any]] = []
    undecided: list[int] = []
    by_term: dict[str, list[int]] = {}
    for index, attempt in enumerate(attempts):
        if attempt.alt_credit is not None:
            by_term.setdefault(attempt.term, []).append(index)
    for term, indexes in by_term.items():
        target = (official.get(term) or {}).get("term_gpa")
        in_term = [i for i, a in enumerate(attempts)
                   if a.term == term and a.coefficient is not None and a.credit is not None]
        fitting: list[tuple[float | None, ...]] = []
        if target is not None and term and len(indexes) <= _MAX_CREDIT_ROWS_PER_TERM:
            options = [(attempts[i].credit, attempts[i].alt_credit) for i in indexes]
            for combo in itertools.product(*options):
                picked = dict(zip(indexes, combo))
                weights = [(picked.get(i, attempts[i].credit) or 0.0, attempts[i].coefficient or 0.0) for i in in_term]
                credits = math.fsum(c for c, _ in weights)
                if credits > 0 and _matches(math.fsum(c * k for c, k in weights) / credits, target):
                    fitting.append(combo)
        for position, index in enumerate(indexes):
            attempt = attempts[index]
            values = {combo[position] for combo in fitting}
            choice = {
                "code": attempt.code, "term": term or None, "grade": attempt.grade,
                "kredisi": attempt.credit, "sayilanKredi": attempt.alt_credit,
            }
            if len(values) == 1:
                chosen = values.pop()
                attempts[index] = replace(attempt, credit=chosen, alt_credit=None)
                choice.update(chosen=chosen, decided=True,
                              basis=f"only this credit reproduces the official term average {target} of {term}")
            else:
                undecided.append(index)
                reason = (
                    "no official term average to compare with" if target is None or not term
                    else "both credits reproduce the official term average" if fitting
                    else "neither credit reproduces the official term average"
                )
                choice.update(chosen=attempt.credit, decided=False, basis=reason)
            choices.append(choice)
    return attempts, choices, undecided


def _equivalence_candidates(attempts: list[_Attempt]) -> list[tuple[_Attempt, _Attempt]]:
    """(failed attempt, later attempt under another code) with the same name and credit."""
    graded: dict[str, list[_Attempt]] = {}
    for attempt in attempts:
        if attempt.coefficient is not None and attempt.credit is not None:
            graded.setdefault(attempt.key, []).append(attempt)
    pairs: list[tuple[_Attempt, _Attempt]] = []
    for key, own in graded.items():
        last = own[-1]
        name = _name_key(last.name)
        if last.grade not in _FAILING or last.source != "unused" or not name:
            continue
        for other_key, others in graded.items():
            if other_key == key:
                continue
            later = [
                a for a in others
                if a.term_key > last.term_key and _name_key(a.name) == name
                and (_same(a.credit, last.credit) or _same(a.alt_credit, last.credit))
            ]
            if later:
                pairs.append((last, later[0]))
    return pairs


def _check(
    attempts: list[_Attempt], groups: dict[str, str],
    official_terms: list[dict[str, Any]], official_gpa: float | None,
) -> dict[str, Any]:
    """Compare the model with the official cumulative GPA after each term."""
    with_attempts = {a.term_key for a in attempts if a.coefficient is not None}
    rows: list[dict[str, Any]] = []
    for term in official_terms:
        official = term["cumulative_gpa"]
        key = int(term["term"])
        if official is None or not 0 <= official <= 4:
            continue
        calculated = _gpa(*_sum(_state(attempts, groups, upto=key)))
        if official == 0 and (
            key not in with_attempts or calculated is None
            or (not term["credits"] and calculated > _TOLERANCE)
        ):
            continue  # a term OBS lists before its grades are final: 0 is a placeholder
        rows.append({"term": term["term"], "official": official,
                     "calculated": _round2(calculated), "matched": _matches(calculated, official)})
    current = _gpa(*_sum(_state(attempts, groups)))
    final = None
    if official_gpa is not None and (current is not None or official_gpa > 0):
        final = _matches(current, official_gpa)
    error = math.fsum(abs((r["calculated"] or 0.0) - r["official"]) for r in rows)
    if final is False:
        error += abs((current or 0.0) - (official_gpa or 0.0))
    matched = sum(1 for r in rows if r["matched"])
    return {
        "rows": rows, "matched": matched, "final": final, "error": error,
        "points": matched + (1 if final else 0),
        "total": len(rows) + (0 if final is None else 1),
    }


def _choose_equivalences(
    attempts: list[_Attempt], pairs: list[tuple[_Attempt, _Attempt]],
    official_terms: list[dict[str, Any]], official_gpa: float | None,
) -> tuple[dict[str, str], set[int], dict[str, Any]]:
    """The smallest set of candidate equivalences that reproduces the official record."""

    def groups_of(subset: tuple[int, ...]) -> dict[str, str]:
        groups: dict[str, str] = {}
        for index in subset:
            old, new = pairs[index]
            old_root, new_root = _root(groups, old.key), _root(groups, new.key)
            if old_root != new_root:
                groups[old_root] = new_root
        return groups

    best: tuple[tuple[float, float, int], dict[str, str], tuple[int, ...], dict[str, Any]] | None = None
    for size in range(len(pairs) + 1):
        for subset in itertools.combinations(range(len(pairs)), size):
            groups = groups_of(subset)
            check = _check(attempts, groups, official_terms, official_gpa)
            if check["points"] == check["total"]:
                return groups, set(subset), check
            score = (check["points"], -check["error"], -size)
            if best is None or score > best[0]:
                best = (score, groups, subset, check)
    if best is None:  # unreachable: the empty subset is always evaluated
        raise RuntimeError("no equivalence subset was evaluated")
    return best[1], set(best[2]), best[3]


def _unique_credit(plan_credits: dict[str, set[float]], key: str) -> float | None:
    values = plan_credits.get(key) or set()
    return next(iter(values)) if len(values) == 1 else None


def _project(
    attempts: list[_Attempt], groups: dict[str, str], courses: list[dict[str, Any]],
    grades: dict[str, str], extras: list[dict[str, Any]],
    plan_credits: dict[str, set[float]], term: str | None,
) -> dict[str, Any]:
    """Apply the projected grades on top of the record."""
    groups = dict(groups)
    state = _state(attempts, groups)
    current_credits, current_points = _sum(state)
    term_key = _term_key(term) if term else max((a.term_key for a in attempts), default=0)
    rows: list[dict[str, Any]] = []
    extra_rows: list[dict[str, Any]] = []
    not_projected: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    equivalences: list[dict[str, Any]] = []
    warnings: list[str] = []
    term_credits = term_points = 0.0

    def apply(root: str, new: _Attempt, old: _Attempt | None, row: dict[str, Any], before: tuple[float, float]) -> None:
        state[root] = new
        after = _sum(state)
        row["effect"] = (
            "new" if old is None
            else f"replaces {old.code} {old.grade} from {old.term or 'an undated record'}"
        )
        row["credit_delta"] = _clean(after[0] - before[0])
        row["points_delta"] = _clean(after[1] - before[1])

    for course in courses:
        key = course["key"]
        base = {"code": course["code"], "crn": ", ".join(course["crns"]) or None, "name": course["name"]}
        grade = grades.get(key)
        if grade is None:
            not_projected.append(base)
            continue
        root = _root(groups, key)
        old = state.get(root)
        known = course["credit"] if course["credit"] is not None else _unique_credit(plan_credits, key)
        if old is None:
            named = [
                a for a in state.values()
                if a.grade in _FAILING and a.key != key and a.term_key < term_key
                and _name_key(a.name) in course["names"]
            ]
            exact = [a for a in named if _same(a.credit, known)]
            if len(exact) == 1:
                old = exact[0]
                root = _root(groups, old.key)
                groups[key] = root
                equivalences.append({
                    "old_code": old.code, "old_grade": old.grade, "old_term": old.term or None,
                    "new_code": course["code"], "new_term": term, "name": old.name, "credit": old.credit,
                    "basis": "name_and_credit", "verified_by_official_series": False,
                    "evidence": (
                        f"{course['code']} is registered now under another code with the same name and "
                        f"credit as the failed {old.code}. No official value exists yet to confirm it."
                    ),
                })
            elif len(exact) > 1:
                warnings.append(
                    f"equivalence_ambiguous: {course['code']} has the same name and credit as more than one "
                    f"failed course ({', '.join(a.code for a in exact)}); it was counted as a new course."
                )
            elif named and known is None:
                base["possible_equivalent"] = named[0].code
        if old is not None and old.key == key:
            credit = old.credit
        elif known is not None:
            credit = known
        elif old is not None:
            credit = old.credit
        else:
            credit = course["supplied_credit"]
        row = {**base, "credit": credit, "grade": grade}
        coefficient = LETTER_TO_GRADE[grade]
        if coefficient is None:
            row.update(effect=f"not counted: {grade} has no GPA coefficient", credit_delta=0.0, points_delta=0.0)
            rows.append(row)
            continue
        if credit is None:
            unresolved.append({**base, "grade": grade})
            continue
        term_credits += credit
        term_points += credit * coefficient
        if old is not None and old.term_key > term_key:
            row.update(effect=f"superseded by {old.code} {old.grade} from {old.term}",
                       credit_delta=0.0, points_delta=0.0)
        else:
            new = _Attempt(key=key, code=course["code"], name=course["name"], term=term or "",
                           grade=grade, credit=credit, source="projected", rank=2, term_order=term_key)
            apply(root, new, old, row, _sum(state))
        rows.append(row)

    for position, extra in enumerate(extras):
        key = extra["key"]
        root = _root(groups, key)
        before = _sum(state)
        if extra["replaces"]:
            target = _root(groups, extra["replaces"])
            if target not in state:
                warnings.append(
                    f"replaces_not_found: extra course {extra['code']} names {extra['replaces']} as the course it "
                    "replaces, but the record has no counted attempt of it."
                )
            elif target != root:
                own = state.pop(root, None)
                groups[root] = target
                if own is not None and own.term_key > state[target].term_key:
                    state[target] = own
                root = target
                equivalences.append({
                    "old_code": extra["replaces"], "new_code": extra["code"], "basis": "stated_by_caller",
                    "verified_by_official_series": False,
                    "evidence": "The caller named the replaced course in extra_courses.replaces.",
                })
        old = state.get(root)
        from_record = old.credit if old is not None and old.key == key else _unique_credit(plan_credits, key)
        credit = from_record if from_record is not None else extra["credit"]
        if credit is None and old is not None:
            credit = old.credit
        if from_record is not None and extra["credit"] is not None and not _same(from_record, extra["credit"]):
            warnings.append(
                f"credit_overridden: {extra['code']} is {from_record:g} credits in the record; "
                f"the supplied {extra['credit']:g} was not used."
            )
        row = {"code": extra["code"], "credit": credit, "grade": extra["grade"]}
        coefficient = LETTER_TO_GRADE[extra["grade"]]
        if coefficient is None:
            row.update(effect=f"not counted: {extra['grade']} has no GPA coefficient",
                       credit_delta=0.0, points_delta=0.0)
        elif credit is None:
            unresolved.append({"code": extra["code"], "crn": None, "name": "", "grade": extra["grade"]})
            continue
        else:
            new = _Attempt(key=key, code=extra["code"], name="", term="", grade=extra["grade"], credit=credit,
                           source="extra", rank=3, term_order=_AFTER_EVERYTHING + position)
            apply(root, new, old, row, before)
        extra_rows.append(row)

    credits, points = _sum(state)
    still_failing = sorted(
        ({"code": a.code, "name": a.name, "credit": a.credit, "term": a.term or None, "grade": a.grade}
         for a in state.values() if a.grade in _FAILING and a.source in ("plan", "unused")),
        key=lambda item: (_term_key(item["term"]), item["code"]),
    )
    return {
        "current": (current_credits, current_points), "projected": (credits, points),
        "term": (term_credits, term_points), "rows": rows, "extra_rows": extra_rows,
        "not_projected": not_projected, "unresolved": unresolved, "equivalences": equivalences,
        "still_failing": still_failing, "warnings": warnings,
    }


def _assign_grades(
    courses: list[dict[str, Any]], arguments: dict[str, Any],
) -> tuple[dict[str, str], list[dict[str, Any]], dict[str, Any] | None]:
    """Projected grade per registered course, the remaining extras, and unmatched keys."""
    by_key = {course["key"]: course for course in courses}
    by_crn = {crn: course for course in courses for crn in course["crns"]}
    grades: dict[str, str] = {}
    unmatched: list[str] = []
    for raw, grade in arguments["grades"].items():
        course = by_crn.get(raw) or by_key.get(course_key(raw))
        if course is None:
            unmatched.append(raw)
        elif grades.setdefault(course["key"], grade) != grade:
            raise ValueError(f"projected_grades gives {course['code']} two different grades.")
    extras = []
    for extra in arguments["extras"]:
        course = by_key.get(extra["key"])
        if course is None:
            extras.append(extra)
            continue
        # A registered course named in extra_courses: the entry supplies its grade and credit.
        if grades.setdefault(course["key"], extra["grade"]) != extra["grade"]:
            raise ValueError(f"{course['code']} has different grades in projected_grades and extra_courses.")
        course["supplied_credit"] = extra["credit"]
    if arguments["default"]:
        for course in courses:
            grades.setdefault(course["key"], arguments["default"])
    report = None
    if unmatched:
        report = {
            "keys": unmatched,
            "valid_keys": [{"code": c["code"], "crn": ", ".join(c["crns"]) or None} for c in courses],
        }
    return grades, extras, report


# One grade per coefficient, lowest first: FF and VF weigh the same.
_UNIFORM_SCALE = tuple(sorted(
    {LETTER_TO_GRADE[grade]: grade for grade in reversed(GRADED)}.values(),
    key=lambda grade: LETTER_TO_GRADE[grade] or 0.0,
))


def uniform_grade_for_target(
    target: float,
    attempts: list[_Attempt], groups: dict[str, str], courses: list[dict[str, Any]],
    fixed: dict[str, str], extras: list[dict[str, Any]],
    plan_credits: dict[str, set[float]], term: str | None,
) -> dict[str, Any]:
    """The lowest single grade that reaches ``target`` in the registered courses.

    ``fixed`` holds the courses whose grade the caller named; every other
    registered course gets the same grade. Each step is a full projection, so
    retakes and equivalences count exactly as they do in the projection.
    """
    varied = [course for course in courses if course["key"] not in fixed]
    result: dict[str, Any] = {
        "target_gpa": target, "status": "no_courses_to_vary", "minimum_uniform_grade": None,
        "gpa_with_minimum_grade": None, "maximum_reachable_gpa": None,
        "varied_courses": [course["code"] for course in varied],
    }
    if fixed:
        by_key = {course["key"]: course["code"] for course in courses}
        result["fixed_grades"] = {by_key[key]: grade for key, grade in fixed.items() if key in by_key}

    def project(grade: str | None) -> dict[str, Any]:
        grades = dict(fixed)
        if grade is not None:
            grades.update({course["key"]: grade for course in varied})
        return _project(attempts, groups, courses, grades, extras, plan_credits, term)

    if not varied:
        result["maximum_reachable_gpa"] = _round2(_gpa(*project(None)["projected"]))
        result["note"] = "Every registered course already has a named grade, so no grade is left to choose."
        return result

    best = project(_UNIFORM_SCALE[-1])
    varied_keys = {course["code"] for course in varied}
    result["maximum_reachable_gpa"] = _round2(_gpa(*best["projected"]))
    result["retakes_among_varied"] = sum(
        1 for row in best["rows"] if row["code"] in varied_keys and str(row.get("effect", "")).startswith("replaces")
    )
    result["status"] = "unreachable"
    for grade in _UNIFORM_SCALE:
        gpa = _gpa(*(best if grade == _UNIFORM_SCALE[-1] else project(grade))["projected"])
        if gpa is not None and gpa >= target - 1e-9:
            result.update(status="reachable", minimum_uniform_grade=grade, gpa_with_minimum_grade=_round2(gpa))
            break
    if best["unresolved"]:
        result["incomplete"] = True
    top = _UNIFORM_SCALE[-1]
    result["note"] = (
        f"Even {top} in every varied course stays below the target." if result["status"] == "unreachable"
        else "The same grade in every varied course; a mix of higher and lower grades can reach the target too."
    )
    return result


def simulate_cumulative_gpa(
    graduation: Any,
    registration_status: Any,
    registered: Any,
    *,
    term_code: str | int | None = None,
    projected_grades: Any = None,
    default_grade: Any = None,
    extra_courses: Any = None,
    target_gpa: Any = None,
) -> dict[str, Any]:
    """Project the cumulative GPA after ``term_code`` from the student's own record.

    ``graduation`` is the MezuniyetimeNeKaldi payload, ``registration_status``
    the KayitDurumu payload and ``registered`` the registered courses of the
    projected term. With ``target_gpa`` the result also carries ``target``:
    the uniform grade the registered courses need to reach it. Raises
    ``ValueError`` for an unusable argument.
    """
    arguments = normalize_arguments(projected_grades, default_grade, extra_courses, target_gpa)
    term = str(term_code).strip() if term_code is not None and not isinstance(term_code, bool) else None
    info = _graduation_info(graduation)
    plan = info.get("dersPlaniVM")
    official_gpa = _number(info.get("gpa"))
    required = _number(plan.get("gerekliMinGPA")) if isinstance(plan, dict) else None

    attempts, plan_credits, warnings = _read_record(info)
    if not any(a.coefficient is not None for a in attempts) and official_gpa == 0:
        official_gpa = None  # nothing graded yet: 0 is a placeholder, not an average
    official_terms, series_warning = _official_terms(registration_status, official_gpa)
    if series_warning:
        warnings.append(series_warning)

    attempts, credit_choices, undecided = _resolve_credits(attempts, {t["term"]: t for t in official_terms})
    pairs = _equivalence_candidates(attempts)
    if len(pairs) > _MAX_EQUIVALENCE_CANDIDATES:
        warnings.append(
            f"equivalence_candidates_truncated: {len(pairs)} possible equivalences were found; "
            f"only the first {_MAX_EQUIVALENCE_CANDIDATES} were tested."
        )
        pairs = pairs[:_MAX_EQUIVALENCE_CANDIDATES]
    groups, applied, check = _choose_equivalences(attempts, pairs, official_terms, official_gpa)
    reproduced = check["total"] > 0 and check["points"] == check["total"]
    status = "not_validated" if check["total"] == 0 else "reproduced" if reproduced else "mismatch"

    equivalences: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for index, (old, new) in enumerate(pairs):
        entry = {
            "old_code": old.code, "old_grade": old.grade, "old_term": old.term or None,
            "new_code": new.code, "new_grade": new.grade, "new_term": new.term or None,
            "name": old.name, "credit": old.credit,
        }
        if index in applied:
            entry.update(
                basis="official_series", verified_by_official_series=reproduced,
                evidence=(
                    "Same course name and credit under another code. The official cumulative GPA of every "
                    "checked term is reproduced only when the later attempt replaces the failed one."
                    if reproduced else
                    "Same course name and credit under another code. Treating it as a replacement brings the "
                    "model closest to the official record, which is still not fully reproduced."
                ),
            )
            equivalences.append(entry)
        else:
            entry["reason"] = (
                "no official value to verify it against" if status == "not_validated"
                else "the official record is reproduced without it" if reproduced
                else "it does not bring the model closer to the official record"
            )
            rejected.append(entry)

    courses = _read_registered(registered)
    grades, extras, unmatched = _assign_grades(courses, arguments)
    outcome = _project(attempts, groups, courses, grades, extras, plan_credits, term)
    target = None
    if arguments["target"] is not None:
        # default_grade fills the projection; the target search chooses that grade itself.
        fixed = _assign_grades(courses, {**arguments, "default": None})[0]
        target = uniform_grade_for_target(
            arguments["target"], attempts, groups, courses, fixed, extras, plan_credits, term,
        )
    equivalences.extend(outcome["equivalences"])
    warnings.extend(outcome["warnings"])

    current_gpa = _gpa(*outcome["current"])
    projected_gpa = _gpa(*outcome["projected"])
    current_range = projected_range = None
    if undecided:
        varied = undecided[:_MAX_RANGE_ROWS]
        currents, projections = [], []
        for combo in itertools.product(*[(attempts[i].credit, attempts[i].alt_credit) for i in varied]):
            trial = list(attempts)
            for index, credit in zip(varied, combo):
                trial[index] = replace(trial[index], credit=credit)
            alternative = _project(trial, groups, courses, grades, extras, plan_credits, term)
            currents.append(_gpa(*alternative["current"]))
            projections.append(_gpa(*alternative["projected"]))
        if all(value is not None for value in currents + projections):
            current_range = [_round2(min(currents)), _round2(max(currents))]
            projected_range = [_round2(min(projections)), _round2(max(projections))]
        codes = ", ".join(
            f"{attempts[i].code} ({attempts[i].credit:g} or {attempts[i].alt_credit:g})" for i in undecided
        )
        warnings.append(
            f"ambiguous_credit: the GPA weight of {codes} could not be decided from the official term "
            "average; gpa uses kredisi and gpa_range covers both values."
        )

    if status == "mismatch":
        failed = [row for row in check["rows"] if not row["matched"]]
        warnings.insert(0, (
            "model_mismatch: THE MODEL DOES NOT REPRODUCE THE OFFICIAL RECORD "
            f"({check['matched']} of {len(check['rows'])} terms match"
            + (f", reconstructed current GPA {_round2(current_gpa)} against official {official_gpa}"
               if check["final"] is False else "")
            + "). Treat every number here as a rough estimate."
            + (f" First mismatch: term {failed[0]['term']}." if failed else "")
        ))
    elif status == "not_validated" and attempts:
        warnings.append("not_validated: no official GPA was available to check the model against.")
    if outcome["unresolved"]:
        warnings.append(
            "unresolved_credit: no credit could be found for "
            + ", ".join(item["code"] for item in outcome["unresolved"])
            + "; they are left out and the projection is incomplete. Pass them in extra_courses "
            "with a credit to include them."
        )
    if unmatched:
        warnings.append(
            "unmatched_projected_grades: no registered course matches "
            + ", ".join(unmatched["keys"]) + "; these grades were not applied. Registered: "
            + ", ".join(c["code"] + (f" (CRN {c['crn']})" if c["crn"] else "") for c in unmatched["valid_keys"])
            + "."
        )
    if not courses:
        warnings.append("no_registered_courses: the projected term has no registered courses.")
    elif outcome["not_projected"] and not outcome["rows"] and not outcome["unresolved"] and target is None:
        warnings.append(
            "no_grades_given: no registered course was given a grade; pass default_grade or projected_grades."
        )

    complete = not outcome["unresolved"] and not unmatched
    meets = None
    if required is not None and projected_gpa is not None:
        ends = projected_range or [_round2(projected_gpa)] * 2
        verdicts = {end is not None and end >= required for end in ends}
        meets = verdicts.pop() if len(verdicts) == 1 else None

    def totals(pair: tuple[float, float], gpa: float | None) -> dict[str, Any]:
        return {
            "gpa": _round2(gpa), "gpa_unrounded": None if gpa is None else round(gpa, 6),
            "credits": _clean(pair[0]), "points": _clean(pair[1]),
        }

    current = totals(outcome["current"], current_gpa)
    current.update(
        reproduces_official=check["final"], model_status=status,
        terms_checked=len(check["rows"]), terms_matched=check["matched"],
        term_mismatches=[row for row in check["rows"] if not row["matched"]],
    )
    projected = totals(outcome["projected"], projected_gpa)
    projected.update(
        term_gpa=_round2(_gpa(*outcome["term"])), term_credits=_clean(outcome["term"][0]),
        term_points=_clean(outcome["term"][1]), complete=complete,
        meets_required_min_gpa=meets,
    )
    if current_range:
        current["gpa_range"], projected["gpa_range"] = current_range, projected_range

    result: dict[str, Any] = {
        "term": term,
        "official": {"cumulative_gpa": official_gpa, "required_min_gpa": required},
        "current": current,
        "projected": projected,
        "courses": outcome["rows"],
        "not_projected": outcome["not_projected"],
        "unresolved_credit": outcome["unresolved"],
        "still_failing": outcome["still_failing"],
        "equivalences": equivalences,
        "warnings": warnings,
        "note": NOTE,
    }
    if target is not None:
        result["target"] = target
    if outcome["extra_rows"]:
        result["extra_courses"] = outcome["extra_rows"]
    if rejected:
        result["equivalence_candidates_not_applied"] = rejected
    if credit_choices:
        result["credit_choices"] = credit_choices
    if unmatched:
        result["unmatched_projected_grades"] = unmatched
    return result
