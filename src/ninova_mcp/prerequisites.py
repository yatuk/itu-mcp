"""Parser for the official OBS branch prerequisite table.

``/public/GenelTanimlamalar/OnsartAra`` returns the authoritative prerequisite
list for a whole branch: every course that has a prerequisite, with the full
Ve/Veya boolean expression, per-course minimum grades, and the credit or class
requirement. It is the only public source that states the rules exactly.

This matters beyond convenience. The per-course ``DersBilgi`` page yields an
empty list both when a course truly has no prerequisite and when the page simply
does not carry the data, which makes an empty result uninterpretable. The branch
table has no such ambiguity: it lists every constrained course in the branch, so
a course's absence from it is a positive statement that the course is
unconstrained.
"""

from __future__ import annotations

import html as html_module
import re
from typing import Any

# Course codes here run 3-4 digits with up to two trailing letters (FIZ 101EL).
_CODE_RE = re.compile(r"^[A-ZÇĞİÖŞÜ]{2,4}\s+\d{3,4}[A-Z]{0,2}$")
_TOKEN_RE = re.compile(
    # The period after MIN is optional so this also tokenizes the community
    # cross-check dataset's "MIN DD" spelling, not just OBS's "MIN. DD".
    r"\(|\)|MIN\.?\s*[A-Z]{2}[+-]?|[A-ZÇĞİÖŞÜ]{2,4}\s+\d{3,4}[A-Z]{0,2}|\bVeya\b|\bVe\b",
    re.IGNORECASE,
)
_TR_ROW_RE = re.compile(r"<tr\b.*?</tr>", re.IGNORECASE | re.DOTALL)
_TD_SPLIT_RE = re.compile(r"<td\b[^>]*>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def _text_of(fragment: str) -> str:
    """Strip tags and entities from a table cell, collapsing whitespace.

    ``<br>`` becomes a space rather than vanishing, otherwise adjacent codes
    would fuse into one unparseable token.
    """
    without_breaks = re.sub(r"<br\s*/?>", " ", fragment, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", html_module.unescape(_TAG_RE.sub(" ", without_breaks))).strip()


def _codes_in(fragment: str) -> list[str]:
    """Pull the course codes a row applies to, preserving order and dropping dupes."""
    codes = [
        re.sub(r"\s+", " ", match).upper()
        for match in re.findall(r"[A-ZÇĞİÖŞÜ]{2,4}\s+\d{3,4}[A-Z]{0,2}", _text_of(fragment))
    ]
    return list(dict.fromkeys(codes))


# -- boolean expression parsing -------------------------------------------


def _tokenize(expression: str) -> list[str]:
    return [match.group(0).strip() for match in _TOKEN_RE.finditer(expression)]


def parse_prerequisite_expression(expression: str) -> dict[str, Any]:
    """Parse a ``Ve``/``Veya`` prerequisite expression into a boolean tree.

    Grammar, with ``Veya`` (OR) binding looser than ``Ve`` (AND) and explicit
    parentheses overriding both — which is how OBS renders the real rules::

        or_expr  := and_expr ("Veya" and_expr)*
        and_expr := factor ("Ve" factor)*
        factor   := "(" or_expr ")" | COURSE ["MIN. XX"]

    Returns ``{"type": "or"|"and"|"course", ...}``. A course leaf carries
    ``code`` and ``min_grade``.
    """
    tokens = _tokenize(expression)
    position = 0

    def peek() -> str | None:
        return tokens[position] if position < len(tokens) else None

    def parse_or() -> dict[str, Any] | None:
        nonlocal position
        operands = []
        first = parse_and()
        if first is not None:
            operands.append(first)
        while peek() and peek().lower() == "veya":
            position += 1
            operand = parse_and()
            if operand is not None:
                operands.append(operand)
        if not operands:
            return None
        if len(operands) == 1:
            return operands[0]
        return {"type": "or", "operands": operands}

    def parse_and() -> dict[str, Any] | None:
        nonlocal position
        operands = []
        first = parse_factor()
        if first is not None:
            operands.append(first)
        while peek() and peek().lower() == "ve":
            position += 1
            operand = parse_factor()
            if operand is not None:
                operands.append(operand)
        if not operands:
            return None
        if len(operands) == 1:
            return operands[0]
        return {"type": "and", "operands": operands}

    def parse_factor() -> dict[str, Any] | None:
        nonlocal position
        token = peek()
        if token is None:
            return None
        # Comparing against the grammar's parenthesis tokens, not a credential;
        # bandit's B105 heuristic flags bare "(" / ")" string literals.
        if token == "(":  # nosec B105
            position += 1
            inner = parse_or()
            if peek() == ")":  # nosec B105
                position += 1
            return inner
        if token == ")":  # nosec B105
            return None
        if _CODE_RE.match(re.sub(r"\s+", " ", token).upper()):
            position += 1
            code = re.sub(r"\s+", " ", token).upper()
            min_grade = None
            following = peek()
            if following and following.upper().startswith("MIN"):
                position += 1
                grade_match = re.search(r"MIN\.?\s*([A-Z]{2}[+-]?)", following, re.IGNORECASE)
                if grade_match:
                    min_grade = grade_match.group(1).upper()
            return {"type": "course", "code": code, "min_grade": min_grade}
        # An operator in factor position means a malformed run; skip it so the
        # rest of the expression still parses instead of losing the whole row.
        position += 1
        return parse_factor()

    tree = parse_or()
    return tree or {"type": "and", "operands": []}


def flatten_courses(tree: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Collect every course leaf in a prerequisite tree."""
    if not tree:
        return []
    if tree.get("type") == "course":
        return [tree]
    collected: list[dict[str, Any]] = []
    for operand in tree.get("operands") or []:
        collected.extend(flatten_courses(operand))
    return collected


def describe_tree(tree: dict[str, Any] | None) -> str:
    """Render a prerequisite tree back to readable Turkish."""
    if not tree:
        return ""
    if tree.get("type") == "course":
        grade = tree.get("min_grade")
        return f"{tree.get('code')}{f' (en az {grade})' if grade else ''}"
    joiner = " VEYA " if tree.get("type") == "or" else " VE "
    parts = [describe_tree(operand) for operand in tree.get("operands") or []]
    parts = [part for part in parts if part]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    return "(" + joiner.join(parts) + ")"


# -- table parsing ---------------------------------------------------------


def parse_credit_requirement(cell_text: str) -> float | None:
    """Read the 'Başarılan Kredi/Sınıf Önşartı' cell, which uses a decimal comma."""
    match = re.search(r"\d+(?:[.,]\d+)?", cell_text or "")
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def extract_branch_prerequisites(html: str, page_url: str, branch: str) -> dict[str, Any]:
    """Parse the OnsartAra table for one branch into per-course rules.

    The source markup leaves ``<td>`` elements unclosed, so cells are split on
    opening tags rather than trusted to nest.
    """
    rules: dict[str, dict[str, Any]] = {}
    row_count = 0

    for row_html in _TR_ROW_RE.findall(html or ""):
        cells = _TD_SPLIT_RE.split(row_html)[1:]
        if len(cells) < 3:
            continue
        codes = _codes_in(cells[0])
        if not codes:
            continue
        row_count += 1

        name = _text_of(cells[1])
        expression = _text_of(cells[2])
        credit_text = _text_of(cells[3]) if len(cells) > 3 else ""
        tree = parse_prerequisite_expression(expression)
        required = flatten_courses(tree)

        rule = {
            "course_codes": codes,
            "course_name": name,
            "expression": expression,
            "expression_readable": describe_tree(tree),
            "requirement_tree": tree,
            "required_courses": sorted({course["code"] for course in required}),
            "minimum_grades": {
                course["code"]: course["min_grade"]
                for course in required
                if course.get("min_grade")
            },
            "credit_requirement": parse_credit_requirement(credit_text),
            "credit_requirement_text": credit_text or None,
        }
        for code in codes:
            rules[code] = rule

    return {
        "branch": branch.upper(),
        "url": page_url,
        "constrained_course_count": row_count,
        "rules": rules,
        # A branch table that parsed zero rows means the page shape changed, not
        # that the branch is prerequisite-free. Callers must not read the two
        # cases the same way.
        "table_parsed": row_count > 0,
    }


# -- evaluation ------------------------------------------------------------

# İTÜ 4.00 scale, ordered so "at least DD" is a numeric comparison. Grades that
# carry no numeric value (BL/GE pass grades) are handled as a pass separately.
_GRADE_VALUES = {
    "AA": 4.00, "BA": 3.50, "BB": 3.00, "CB": 2.50, "CC": 2.00,
    "DC": 1.50, "DD": 1.00, "FD": 0.50, "FF": 0.00, "VF": 0.00,
}
_PASSING_NON_NUMERIC = {"BL", "GE", "MU", "TR", "S"}


def _grade_value(grade: str | None) -> float | None:
    if not grade:
        return None
    cleaned = str(grade).strip().upper().rstrip("+-")
    return _GRADE_VALUES.get(cleaned)


def _grade_satisfies(earned: str | None, minimum: str | None) -> bool:
    """Does ``earned`` meet a ``MIN. XX`` bar?

    An unknown earned grade counts as satisfied when no minimum is demanded,
    since the caller has already asserted the course was completed.
    """
    if minimum is None:
        return True
    if earned and str(earned).strip().upper().rstrip("+-") in _PASSING_NON_NUMERIC:
        return True
    earned_value = _grade_value(earned)
    minimum_value = _grade_value(minimum)
    if minimum_value is None:
        return True
    if earned_value is None:
        return False
    return earned_value >= minimum_value


def evaluate_tree(
    tree: dict[str, Any] | None,
    completed: dict[str, str | None],
) -> dict[str, Any]:
    """Evaluate a prerequisite tree against completed courses.

    ``completed`` maps a normalised course code to its earned letter grade, or
    ``None`` when only completion is known. Returns the node's verdict plus a
    readable reason, so an ineligible result can say which branch failed.
    """
    if not tree:
        return {"satisfied": True, "reason": "Ön şart yok."}

    if tree.get("type") == "course":
        code = str(tree.get("code") or "").upper()
        minimum = tree.get("min_grade")
        if code not in completed:
            return {
                "satisfied": False,
                "reason": f"{code} alınmamış.",
                "missing": [code],
            }
        earned = completed[code]
        if not _grade_satisfies(earned, minimum):
            return {
                "satisfied": False,
                "reason": f"{code} notu {earned or '?'}, en az {minimum} gerekiyor.",
                "missing": [code],
            }
        return {"satisfied": True, "reason": f"{code} tamamlandı."}

    results = [evaluate_tree(operand, completed) for operand in tree.get("operands") or []]
    if not results:
        return {"satisfied": True, "reason": "Ön şart yok."}

    if tree.get("type") == "or":
        met = [r for r in results if r["satisfied"]]
        if met:
            return {"satisfied": True, "reason": met[0]["reason"], "alternatives_met": len(met)}
        missing = sorted({code for r in results for code in r.get("missing", [])})
        return {
            "satisfied": False,
            "reason": "Şu seçeneklerden hiçbiri karşılanmadı: " + describe_tree(tree),
            "missing": missing,
        }

    unmet = [r for r in results if not r["satisfied"]]
    if unmet:
        return {
            "satisfied": False,
            "reason": " ".join(r["reason"] for r in unmet),
            "missing": sorted({code for r in unmet for code in r.get("missing", [])}),
        }
    return {"satisfied": True, "reason": "Tüm zorunlu ön şartlar karşılandı."}


# -- cross-source comparison -------------------------------------------


def compare_required_course_sets(
    tree_a: dict[str, Any] | None,
    tree_b: dict[str, Any] | None,
) -> dict[str, Any]:
    """Diff the required-course sets (and minimum grades) of two prerequisite trees.

    Used to cross-check the official OBS table against an independently
    maintained second source: two trees can differ in shape (grouping, operand
    order) while agreeing on which courses are required, so the comparison is
    on the flattened course/grade set rather than tree equality.
    """
    courses_a = {course["code"]: course.get("min_grade") for course in flatten_courses(tree_a)}
    courses_b = {course["code"]: course.get("min_grade") for course in flatten_courses(tree_b)}
    only_a = sorted(set(courses_a) - set(courses_b))
    only_b = sorted(set(courses_b) - set(courses_a))
    grade_mismatches = sorted(
        code for code in set(courses_a) & set(courses_b) if courses_a[code] != courses_b[code]
    )
    return {
        "matches": not only_a and not only_b and not grade_mismatches,
        "only_in_first": only_a,
        "only_in_second": only_b,
        "grade_mismatches": grade_mismatches,
    }
