"""Resolving a semester argument against the student's OBS term list.

A semester is named by its internal id, its term code or (part of) its name.
Everything here works on the term list as data; the client fetches it.
"""

from __future__ import annotations

import re
from typing import Any

from .obs_errors import ObsError
from .parsing import clean_text, normalize_lookup_text

_SEASON_WORDS = {
    "guz": "fall", "fall": "fall", "autumn": "fall",
    "bahar": "spring", "spring": "spring",
    "yaz": "summer", "summer": "summer",
}
# Words that describe a term without identifying one.
_TERM_FILLER_WORDS = frozenset({
    "donem", "donemi", "yariyil", "yariyili", "semester", "term", "ogretimi", "okulu", "school",
})
# How many terms an error message or a resolution note lists at most.
_LISTED_TERMS = 8


def pick_semester(items: list[dict[str, Any]], semester: Any) -> dict[str, Any]:
    """The term of ``items`` (oldest first) that ``semester`` names.

    ``None`` means the latest term, and so does text that is empty once
    spaces and quotes are removed. An exact id or term code wins over any
    name; a name has to identify one term, except a season on its own.
    """
    if not items:
        raise ObsError("No OBS semesters found.")
    if semester is None:
        return items[-1]
    target_raw = _semester_lookup_text(semester)
    if not target_raw:
        return items[-1]
    # An exact id or term code wins over any name fragment of another term.
    for item in items:
        if str(item.get("akademikDonemId")) == target_raw:
            return item
        if str(item.get("donemKodu")) == target_raw:
            return item
    return _resolve_semester_by_name(items, semester, target_raw)


def _semester_tokens(value: Any) -> list[str]:
    """Whole words and whole numbers of a term name, seasons in one spelling."""
    text = normalize_lookup_text(str(value or ""))
    return [_SEASON_WORDS.get(token, token) for token in re.findall(r"[0-9]+|[a-z]+", text)]


def describe_semester(item: Any) -> str:
    """Name a resolved term for a message: ``2040-2041 Bahar Dönemi (term code 204120)``."""
    if not isinstance(item, dict):
        return "unknown term"
    name = clean_text(str(item.get("akademikDonemAdi") or item.get("akademikDonemAdiEN") or ""))
    code = item.get("donemKodu")
    if name and code:
        return f"{name} (term code {code})"
    if name:
        return name
    if code:
        return f"term code {code}"
    return f"term id {item.get('akademikDonemId')}"


def _semester_recency(indexed: tuple[int, dict[str, Any]]) -> tuple[int, int]:
    position, item = indexed
    code = str(item.get("donemKodu") or "")
    return (int(code) if code.isdigit() else -1, position)


def _listing(candidates: list[dict[str, Any]]) -> str:
    return "; ".join(
        f"{describe_semester(item)}, id {item.get('akademikDonemId')}" for item in candidates
    )


def _terms_naming_all(items: list[dict[str, Any]], wanted: list[str]) -> list[tuple[int, dict[str, Any]]]:
    """Terms, with their list position, whose Turkish or English name holds every wanted word."""
    if not wanted:
        return []
    matches = []
    for position, item in enumerate(items):
        name = set(_semester_tokens(item.get("akademikDonemAdi"))) | set(_semester_tokens(item.get("akademikDonemAdiEN")))
        if all(token in name for token in wanted):
            matches.append((position, item))
    return matches


def _resolve_semester_by_name(
    items: list[dict[str, Any]], semester: Any, target_raw: str,
) -> dict[str, Any]:
    """Match a term name by whole words; never pick one of several silently.

    A season on its own means the most recent term of that season, and the
    result says so. Every other query has to identify exactly one term.
    """
    wanted = [token for token in _semester_tokens(target_raw) if token not in _TERM_FILLER_WORDS]
    matches = _terms_naming_all(items, wanted)
    found = [item for _, item in matches]
    words = [token for token in wanted if not token.isdigit()]
    numbers = [token for token in wanted if token.isdigit()]
    if not words and len(numbers) == 1:
        raise _bare_number_error(semester, numbers[0], found)
    if len(found) == 1:
        return found[0]
    if not found:
        raise ObsError(f"Semester not found: {semester}. Examples: {_listing(items[-_LISTED_TERMS:])}")
    if not numbers and len(words) == 1 and words[0] in _SEASON_WORDS.values():
        return _most_recent_term_of_season(matches, target_raw)
    raise ObsError(
        f"Semester '{semester}' is ambiguous. Use a term code or add the year range and season. "
        f"Candidates: {_listing(found[-_LISTED_TERMS:])}"
    )


def _bare_number_error(semester: Any, number: str, found: list[dict[str, Any]]) -> ObsError:
    # A lone number is an id or a term code, both checked by the caller.
    # As part of a name it would be a year, and one year spans two
    # academic years, so it never selects a term on its own.
    hint = f" Terms that mention {number}: {_listing(found[-_LISTED_TERMS:])}." if found else ""
    return ObsError(
        f"Semester not found: {semester}. A bare number must be a term code (e.g. 202610) "
        f"or an OBS term id; a year alone does not identify a term.{hint}"
    )


def _most_recent_term_of_season(
    matches: list[tuple[int, dict[str, Any]]], target_raw: str,
) -> dict[str, Any]:
    """The newest of several terms of one season, with a note on how it was chosen."""
    ranked = sorted(matches, key=_semester_recency)
    chosen = ranked[-1][1]
    others = [item for _, item in ranked[:-1]]
    return {
        **chosen,
        "resolution": {
            "query": target_raw,
            "rule": "most_recent_term_of_season",
            "note": (
                f"'{target_raw}' names a season only. The most recent matching term was used: "
                f"{describe_semester(chosen)}. Pass a term code or a year range with the season "
                "(e.g. \"2024-2025 Bahar\") for another term."
            ),
            "other_candidates": [
                {"code": str(item.get("donemKodu") or ""), "id": item.get("akademikDonemId"),
                 "name": item.get("akademikDonemAdi")}
                for item in reversed(others)
            ][:_LISTED_TERMS],
        },
    }


def _semester_lookup_text(semester: Any) -> str:
    """Text form of a semester argument: a term code, an internal id or a name.

    Clients send a term code as a JSON number, as a float, or as a string that
    still carries quotes. All of them mean the same code.
    """
    if isinstance(semester, bool):
        raise ObsError("Semester must be a term code, a term id or a term name.")
    if isinstance(semester, float) and semester.is_integer():
        semester = int(semester)
    text = str(semester).strip().strip("\"'`‘’“”").strip()
    whole, dot, fraction = text.partition(".")
    if dot and whole.isdigit() and fraction and not fraction.strip("0"):
        text = whole
    return text
