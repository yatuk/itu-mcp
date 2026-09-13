"""Conservative, read-only analysis of a proposed OBS registration plan.

Callers supply current official records. Missing records stay unknown, and
planned courses never count as completed prerequisites for this registration.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from .archive import normalize_course_code
from .schedule_utils import DAY_ORDER

_GRADE_VALUES = {
    "AA": 4.0, "BA": 3.5, "BB": 3.0, "CB": 2.5, "CC": 2.0,
    "DC": 1.5, "DD": 1.0, "FD": 0.5, "FF": 0.0, "VF": 0.0,
}
_FAILING_GRADES = {"FD", "FF", "VF", "BZ", "KF", "IA", "NA"}
_PASS_GRADES = {"BL", "GE", "MU", "TR", "S"}
_ENGLISH_DAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}
_TIME_RANGE = re.compile(r"^(\d{1,2}):(\d{2})\s*[/–-]\s*(\d{1,2}):(\d{2})$")


def _code(value: Any) -> str:
    try:
        return normalize_course_code(str(value or ""))
    except ValueError:
        return ""


def _number(value: Any) -> float | None:
    try:
        number = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _combine(values: list[bool | None], *, either: bool = False) -> bool | None:
    if either:
        if True in values:
            return True
        return None if None in values or not values else False
    if False in values:
        return False
    return None if None in values else True


def _grade_check(earned: Any, minimum: Any) -> bool | None:
    grade = str(earned or "").strip().upper()
    required = str(minimum or "").strip().upper()
    if grade in _FAILING_GRADES:
        return False
    if not required:
        return True if not grade or grade in _GRADE_VALUES or grade in _PASS_GRADES else None
    if required not in _GRADE_VALUES:
        return None
    if grade not in _GRADE_VALUES:
        return None
    return _GRADE_VALUES[grade] >= _GRADE_VALUES[required]


def _evaluate(
    tree: Any,
    completed: dict[str, Any] | None,
    planned: set[str] | None = None,
) -> dict[str, Any]:
    """Evaluate exact AND/OR structure with unknown and minimum-grade support."""
    if tree is None:
        return {"satisfied": True, "missing_courses": []}
    if not isinstance(tree, dict):
        return {"satisfied": None, "missing_courses": []}
    kind = tree.get("type")
    if kind == "course":
        code = _code(tree.get("code"))
        if not code:
            return {"satisfied": None, "missing_courses": []}
        if completed is None:
            satisfied = None
        elif code not in completed:
            satisfied = False
        else:
            satisfied = _grade_check(completed[code], tree.get("min_grade"))
        if satisfied is not True and code in (planned or set()):
            minimum = str(tree.get("min_grade") or "").strip().upper()
            satisfied = True if not minimum or minimum in _GRADE_VALUES else None
        return {"satisfied": satisfied, "missing_courses": [] if satisfied is True else [code]}
    operands = tree.get("operands")
    if kind not in {"and", "or"} or not isinstance(operands, list):
        return {"satisfied": None, "missing_courses": []}
    results = [_evaluate(child, completed, planned) for child in operands]
    satisfied = _combine([r["satisfied"] for r in results], either=kind == "or")
    return {
        "satisfied": satisfied,
        "missing_courses": [] if satisfied is True else sorted({
            code for result in results for code in result["missing_courses"]
        }),
    }


def _leaves(tree: Any) -> list[dict[str, Any]]:
    if not isinstance(tree, dict):
        return []
    if tree.get("type") == "course":
        return [tree]
    return [leaf for node in tree.get("operands") or [] for leaf in _leaves(node)]


def _expression(tree: Any) -> str:
    if tree is None:
        return "No course prerequisite."
    if not isinstance(tree, dict):
        return "Unknown prerequisite expression."
    if tree.get("type") == "course":
        code = _code(tree.get("code")) or "Unknown course"
        return f"{code} (minimum {tree['min_grade']})" if tree.get("min_grade") else code
    parts = [_expression(child) for child in tree.get("operands") or []]
    if tree.get("type") == "and" and not parts:
        return "No course prerequisite."
    if tree.get("type") not in {"and", "or"} or not parts:
        return "Unknown prerequisite expression."
    operator = " OR " if tree["type"] == "or" else " AND "
    return "(" + operator.join(parts) + ")"


def _check(satisfied: bool | None, reason: str, **details: Any) -> dict[str, Any]:
    return {
        "status": "pass" if satisfied is True else "fail" if satisfied is False else "unknown",
        "satisfied": satisfied,
        "reason": reason,
        **details,
    }


def _threshold_checks(
    rule: dict[str, Any] | None,
    completed_credits: float | None,
    class_year: int | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if rule is None:
        return (
            _check(None, "The official credit requirement is unavailable."),
            _check(None, "The official class requirement is unavailable."),
        )
    credit = rule.get("credit_requirement")
    class_requirement = rule.get("class_requirement")
    kind = rule.get("credit_requirement_kind")
    text = str(rule.get("credit_requirement_text") or "").lower()
    if kind is None and credit is not None:
        has_class = "sınıf" in text or "sinif" in text or "class" in text
        has_credit = "kredi" in text or "credit" in text
        if has_class and has_credit:
            kind = "unknown"
        elif has_class:
            kind = "class"
        else:
            kind = "credits"
    if credit is not None and kind == "class":
        class_requirement = credit if class_requirement is None else class_requirement
        credit = None
    if credit is not None and kind not in {"credits", "class"}:
        unknown = _check(None, "The official credit/class requirement cannot be interpreted safely.")
        return unknown.copy(), unknown.copy()

    def compare(raw: Any, actual: Any, label: str) -> dict[str, Any]:
        if raw is None:
            return _check(True, f"No {label} prerequisite is listed.")
        required, observed = _number(raw), _number(actual)
        satisfied = None if required is None or observed is None else observed >= required
        return _check(
            satisfied,
            f"The {label} prerequisite requires {raw}; the completed value is {actual}.",
            required=required,
            actual=observed,
        )

    return compare(credit, completed_credits, "credit"), compare(class_requirement, class_year, "class")


def _schedule_check(sections: list[dict[str, Any]]) -> dict[str, Any]:
    slots: list[tuple[int, int, int, dict[str, Any], dict[str, Any]]] = []
    unknown_crns: set[str] = set()
    for section in sections:
        crn = str(section.get("crn") or "")
        sessions = section.get("sessions")
        if not sessions and section.get("schedule_status") != "no_meetings":
            unknown_crns.add(crn)
        for session in sessions or []:
            if not isinstance(session, dict):
                unknown_crns.add(crn)
                continue
            day_name = str(session.get("day") or "").strip().lower()
            day = DAY_ORDER.get(day_name, _ENGLISH_DAYS.get(day_name))
            match = _TIME_RANGE.fullmatch(str(session.get("time") or "").strip())
            if day is None or not match:
                unknown_crns.add(crn)
                continue
            h1, m1, h2, m2 = map(int, match.groups())
            start, end = h1 * 60 + m1, h2 * 60 + m2
            if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59 or start >= end:
                unknown_crns.add(crn)
                continue
            slots.append((day, start, end, section, session))
    conflicts = []
    for index, (day, start, end, section, session) in enumerate(slots):
        for other_day, other_start, other_end, other, other_session in slots[index + 1:]:
            if str(section.get("crn")) == str(other.get("crn")) or day != other_day:
                continue
            if start < other_end and other_start < end:
                conflicts.append({
                    "course_a": {"crn": str(section["crn"]), "code": section.get("code"), **session},
                    "course_b": {"crn": str(other["crn"]), "code": other.get("code"), **other_session},
                })
    satisfied = False if conflicts else None if unknown_crns else True
    return _check(
        satisfied,
        "Time conflicts were found." if conflicts else
        "Some meeting times are unavailable or unreadable." if unknown_crns else
        "No time conflicts were found in the supplied sections.",
        conflicts=conflicts,
        conflict_count=len(conflicts),
        unknown_crns=sorted(unknown_crns),
    )


def _members(value: Any) -> set[str] | None:
    if isinstance(value, dict):
        if value.get("available") is False or value.get("error"):
            return None
        value = value.get("courses")
    if not isinstance(value, (list, tuple, set)):
        return None
    result = set()
    for item in value:
        code = _code(item.get("course_code") or item.get("code")) if isinstance(item, dict) else _code(item)
        if not code:
            return None
        result.add(code)
    return result


def _elective_coverage(
    sections: list[dict[str, Any]],
    graduation: dict[str, Any] | None,
    groups: dict[str, Any] | None,
    completed: dict[str, Any] | None,
) -> dict[str, Any]:
    if graduation is None or "open_elective_slots" not in graduation:
        return _check(None, "Open graduation slots are unavailable.", assignments=[], remaining_slots=[])
    slots = graduation.get("open_elective_slots") or []
    required_codes = {_code(item.get("course_code")) for item in graduation.get("remaining_required_courses") or []}
    candidates = {
        _code(section.get("code")): section for section in sections
        if _code(section.get("code")) not in required_codes
        and _code(section.get("code")) not in (completed or {})
    }
    candidates.pop("", None)
    groups = {str(key): value for key, value in (groups or {}).items()}
    edges: list[list[str]] = []
    unknown_slots = []
    for index, slot in enumerate(slots):
        members = _members(slot.get("eligible_courses"))
        if members is None and slot.get("group_id") is not None:
            members = _members(groups.get(str(slot["group_id"])))
        if members is None:
            unknown_slots.append(index)
        edges.append(sorted(set(candidates) & (members or set())))

    # Augmenting paths find a maximum matching without using a course twice.
    course_to_slot: dict[str, int] = {}

    def assign(slot_index: int, visited: set[str]) -> bool:
        for code in edges[slot_index]:
            if code in visited:
                continue
            visited.add(code)
            if code not in course_to_slot or assign(course_to_slot[code], visited):
                course_to_slot[code] = slot_index
                return True
        return False

    for slot_index in range(len(slots)):
        assign(slot_index, set())
    slot_to_course = {index: code for code, index in course_to_slot.items()}
    assignments = [{
        "slot": slots[index].get("slot"),
        "group_id": slots[index].get("group_id"),
        "slot_index": index,
        "course_code": code,
        "crn": str(candidates[code].get("crn") or ""),
        "status": "conditional_on_registration_and_passing",
    } for index, code in sorted(slot_to_course.items())]
    remaining = [{
        **slot,
        "slot_index": index,
        "membership_known": index not in unknown_slots,
    } for index, slot in enumerate(slots) if index not in slot_to_course]
    return _check(
        None if unknown_slots or completed is None else True,
        "Elective coverage uses one possible maximum assignment, conditional on registration, passing and official credit counting.",
        assignments=assignments,
        remaining_slots=remaining,
        unknown_slot_indices=unknown_slots,
        all_open_slots_covered=not remaining,
    )


def _graduation_check(
    graduation: dict[str, Any] | None,
    sections: list[dict[str, Any]],
    coverage: dict[str, Any],
) -> dict[str, Any]:
    if graduation is None:
        return _check(None, "The official graduation requirements are unavailable.")
    planned = {_code(section.get("code")) for section in sections}
    remaining = graduation.get("remaining_required_courses")
    pending = [item for item in remaining or [] if _code(item.get("course_code")) not in planned]
    selected = [item for item in remaining or [] if _code(item.get("course_code")) in planned]
    fields = (
        "credits_required", "credits_earned", "credits_remaining", "gpa", "required_gpa",
        "internship_days_required", "internship_days_done",
        "english_credits_required", "english_credits_earned",
    )
    progress = {key: graduation.get(key) for key in fields}
    unknown = [key for key in fields if _number(progress[key]) is None]
    if not isinstance(remaining, list):
        unknown.append("remaining_required_courses")
    return _check(
        None if unknown or coverage["status"] == "unknown" else True,
        "Current official graduation progress is reported separately from conditional plan coverage.",
        current_progress=progress,
        current_gpa_requirement_met=(None if _number(progress["gpa"]) is None or _number(progress["required_gpa"]) is None
                                     else _number(progress["gpa"]) >= _number(progress["required_gpa"])),
        current_internship_requirement_met=(None if _number(progress["internship_days_done"]) is None or _number(progress["internship_days_required"]) is None
                                            else _number(progress["internship_days_done"]) >= _number(progress["internship_days_required"])),
        current_english_credit_requirement_met=(None if _number(progress["english_credits_earned"]) is None or _number(progress["english_credits_required"]) is None
                                               else _number(progress["english_credits_earned"]) >= _number(progress["english_credits_required"])),
        required_courses_planned=selected,
        remaining_required_courses_after_passing=pending,
        remaining_elective_slots_after_passing=coverage.get("remaining_slots") or [],
        unknown_fields=unknown,
        note="A valid registration plan does not certify graduation or predict final GPA, credit counting, or internship completion.",
    )


def _dependency_check(
    rules: dict[str, dict[str, Any]] | None,
    completed: dict[str, Any] | None,
    sections: list[dict[str, Any]],
    graduation: dict[str, Any] | None,
) -> dict[str, Any]:
    planned = {_code(section.get("code")) for section in sections}
    required = {
        _code(item.get("course_code")) for item in (graduation or {}).get("remaining_required_courses") or []
    } - planned - {""}
    normalized = {_code(key): value for key, value in (rules or {}).items()}
    unknown_courses = sorted(required - set(normalized))
    chains = []
    for code, rule in sorted(normalized.items()):
        if not code or code in planned or code in (completed or {}):
            continue
        if not isinstance(rule, dict) or "requirement_tree" not in rule:
            if code not in unknown_courses:
                unknown_courses.append(code)
            continue
        tree = rule["requirement_tree"]
        current = _evaluate(tree, completed)
        future = _evaluate(tree, completed, planned)
        leaves = _leaves(tree)
        leaf_codes = {_code(leaf.get("code")) for leaf in leaves} - {""}
        relevant = sorted(leaf_codes & planned)
        if current["satisfied"] is True:
            status = "already_satisfied"
        elif future["satisfied"] is True:
            status = "preserved_if_requirements_met"
        elif future["satisfied"] is False:
            status = "blocked_by_unmet_prerequisites"
        else:
            status = "unknown"
            if code not in unknown_courses:
                unknown_courses.append(code)
        if not leaf_codes and status == "already_satisfied":
            continue
        deferred = sorted(set(future["missing_courses"]) - planned)
        chains.append({
            "course_code": code,
            "status": status,
            "current_prerequisites_met": current["satisfied"],
            "after_plan_prerequisites_met_conditionally": future["satisfied"],
            "planned_prerequisites": relevant,
            "deferred_or_unmet_prerequisites": deferred,
            "requirement": _expression(tree),
            "requirement_tree": tree,
            "conditional_grade_requirements": [{
                "course_code": _code(leaf.get("code")),
                "minimum_grade": leaf.get("min_grade"),
            } for leaf in leaves if _code(leaf.get("code")) in planned],
            "timing_impact": "unknown",
        })
    return _check(
        None if rules is None or completed is None or graduation is None or unknown_courses else True,
        "Chains use the exact prerequisite expression. Planned grades are conditions, and future offering dates and future credit/class eligibility are not predicted.",
        chains=chains,
        unknown_courses=sorted(set(unknown_courses)),
        timing_impact_verified=False,
    )


def validate_registration_plan(
    sections: list[dict[str, Any]],
    *,
    requested_crns: list[str] | None = None,
    completed_courses: dict[str, str | None] | None = None,
    prerequisite_rules: dict[str, dict[str, Any]] | None = None,
    eligibility: dict[str, dict[str, Any]] | None = None,
    graduation: dict[str, Any] | None = None,
    elective_groups: dict[str, Any] | None = None,
    completed_credits: float | None = None,
    class_year: int | None = None,
    dependency_rules: dict[str, dict[str, Any]] | None = None,
    source_errors: list[str] | None = None,
) -> dict[str, Any]:
    """Validate a hypothetical plan without modifying OBS.

    ``sections`` use ``crn``, ``code`` and ``sessions`` with ``day``/``time``.
    ``completed_courses=None`` means unavailable history, while ``{}`` means
    confirmed empty history. Rule absence means unknown. An explicit rule with
    ``requirement_tree=None`` confirms no course prerequisite. Rules may carry
    ``credit_requirement``, ``class_requirement`` and a disambiguating
    ``credit_requirement_kind``. Eligibility is keyed by CRN and may contain
    ``eligible``, ``prerequisite_eligible``, ``program_eligible``,
    ``class_eligible`` and ``credit_eligible``.

    Graduation uses the summary schema from :mod:`graduation`. Each open slot
    must carry ``group_id`` (resolved through ``elective_groups``) or verified
    ``eligible_courses``. Dependency rules concern future required courses.
    """
    requested = [str(crn).strip() for crn in (requested_crns if requested_crns is not None else [item.get("crn") for item in sections])]
    section_map = {str(item.get("crn") or "").strip(): item for item in sections}
    selected = [section_map[crn] for crn in dict.fromkeys(requested) if crn in section_map]
    selected = [{**item, "crn": str(item.get("crn") or "").strip(), "code": _code(item.get("code") or item.get("course_code"))} for item in selected]
    completed = None if completed_courses is None else {_code(key): value for key, value in completed_courses.items() if _code(key)}
    rules = {_code(key): value for key, value in (prerequisite_rules or {}).items()}
    eligibility = {str(key): value for key, value in (eligibility or {}).items()}
    if completed_credits is None:
        completed_credits = _number((graduation or {}).get("credits_earned"))
    blockers: list[str] = []
    unknowns: list[str] = list(source_errors or [])
    duplicates = sorted(crn for crn, count in Counter(requested).items() if count > 1)
    duplicate_courses = sorted(code for code, count in Counter(item["code"] for item in selected if item["code"]).items() if count > 1)
    missing_crns = sorted(set(requested) - set(section_map))
    if not requested or any(not crn or crn == "None" for crn in requested):
        blockers.append("The plan must contain at least one nonempty CRN.")
    if duplicates:
        blockers.append("The plan contains duplicate CRNs: " + ", ".join(duplicates) + ".")
    if duplicate_courses:
        blockers.append("Multiple sections of the same course were selected: " + ", ".join(duplicate_courses) + ".")
    if missing_crns:
        unknowns.append("Current sections could not be resolved for CRNs: " + ", ".join(missing_crns) + ".")
        for crn in missing_crns:
            if (eligibility.get(crn) or {}).get("eligible") is False:
                blockers.append(f"CRN {crn}: OBS rejected the requested section.")
    course_checks = []
    for section in selected:
        crn, code = section["crn"], section["code"]
        rule = rules.get(code)
        official = eligibility.get(crn) or {}
        if not code:
            unknowns.append(f"The course code for CRN {crn} is unavailable.")
        if not isinstance(rule, dict) or "requirement_tree" not in rule:
            prerequisite = _check(None, "The official prerequisite rule is unavailable.")
            rule = None
        else:
            evaluation = _evaluate(rule["requirement_tree"], completed)
            prerequisite = _check(
                evaluation["satisfied"],
                _expression(rule["requirement_tree"]),
                missing_courses=evaluation["missing_courses"],
                requirement_tree=rule["requirement_tree"],
            )
        credit, class_check = _threshold_checks(rule, completed_credits, class_year)
        for check, field in ((prerequisite, "prerequisite_eligible"), (credit, "credit_eligible"), (class_check, "class_eligible")):
            if official.get(field) is False:
                check.update(_check(False, f"OBS reports that the {field.replace('_', ' ')} check failed."))
            elif check["satisfied"] is None and official.get(field) is True:
                if field == "prerequisite_eligible":
                    check["history_evaluation"] = dict(check)
                    check["missing_courses"] = []
                check.update(_check(True, f"OBS confirms the {field.replace('_', ' ')} check."))
        verdict = official.get("eligible") if isinstance(official.get("eligible"), bool) else None
        program = official.get("program_eligible")
        if not isinstance(program, bool):
            program = True if verdict is True else None
        checks = {
            "prerequisites": prerequisite,
            "credit_prerequisites": credit,
            "class_prerequisites": class_check,
            "program_eligibility": _check(program, "Program eligibility is taken from the official OBS verdict."),
            "obs_eligibility": _check(verdict, "The supplied official OBS verdict applies to its reported registration snapshot.",
                                      source_reason=official.get("reason") or official.get("error_reason")),
        }
        for name, check in checks.items():
            if check["satisfied"] is False:
                blockers.append(f"{code or crn}: {name.replace('_', ' ')} failed.")
            elif check["satisfied"] is None:
                unknowns.append(f"{code or crn}: {name.replace('_', ' ')} is unknown.")
        course_checks.append({"crn": crn, "course_code": code or None, "checks": checks})
    schedule = _schedule_check(selected)
    if schedule["satisfied"] is False:
        blockers.append("The selected sections have overlapping meeting times.")
    elif schedule["satisfied"] is None:
        unknowns.append("Some selected section meeting times are unavailable or unreadable.")
    coverage = _elective_coverage(selected, graduation, elective_groups, completed)
    graduation_check = _graduation_check(graduation, selected, coverage)
    chains = _dependency_check(dependency_rules, completed, selected, graduation)
    for label, check in (("Elective slot coverage", coverage), ("Graduation requirements", graduation_check), ("Prerequisite chain analysis", chains)):
        if check["satisfied"] is None:
            unknowns.append(f"{label} could not be fully verified.")
    blockers, unknowns = list(dict.fromkeys(blockers)), list(dict.fromkeys(unknowns))
    status = "invalid" if blockers else "incomplete" if unknowns else "valid"
    summary = {
        "valid": "The supplied registration checks passed.",
        "invalid": "The proposed registration plan has confirmed blockers.",
        "incomplete": "The proposed registration plan could not be fully validated.",
    }[status]
    details = []
    for chain in chains["chains"]:
        if chain["status"] == "preserved_if_requirements_met":
            details.append(f"The chain to {chain['course_code']} is preserved if {chain['requirement']} is completed.")
        elif chain["status"] == "blocked_by_unmet_prerequisites":
            details.append(f"The chain to {chain['course_code']} still requires {chain['requirement']}.")
    for assignment in coverage.get("assignments") or []:
        details.append(f"{assignment['slot']} can be covered by {assignment['course_code']} after registration and passing.")
    open_names = [str(slot.get("slot") or "Unnamed slot") for slot in coverage.get("remaining_slots") or []]
    if open_names:
        details.append("Elective slots remaining open: " + ", ".join(open_names) + ".")
    if details:
        summary += " " + " ".join(details)
    return {
        "status": status,
        "valid": False if blockers else None if unknowns else True,
        "summary": summary,
        "requested_crns": requested,
        "resolved_crns": [item["crn"] for item in selected],
        "blockers": blockers,
        "unknowns": unknowns,
        "course_checks": course_checks,
        "time_conflicts": schedule,
        "elective_slot_coverage": coverage,
        "graduation_requirements": graduation_check,
        "prerequisite_chains": chains,
        "read_only": True,
        "limitations": [
            "This analysis does not submit or modify registration and does not guarantee a seat or acceptance by OBS.",
            "Only previously completed courses satisfy current registration prerequisites.",
            "Planned elective coverage and prerequisite chains require successful registration and the stated passing grades.",
            "Graduation approval and future course offerings remain subject to the official university records.",
        ],
    }
