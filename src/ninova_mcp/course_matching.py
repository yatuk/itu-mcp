"""Shared, deterministic matching of a course reference against a course list.

Ninova and OBS label the same course differently (``ING 201`` and
``ING 201A``), and people ask for a course by a loose name (``fizik 2`` for
``Fizik II``). The helpers here compare codes and names tolerantly. They never
choose between two candidates: callers get every candidate of the first tier
that matched and decide what an ambiguous result means.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable, Sequence

from .parsing import normalize_lookup_text

_CODE_RE = re.compile(r"\s*([a-z]{2,4})\s*(\d{3,4})\s*([a-z]{0,2})\s*")
_ROMAN = {
    "i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5",
    "vi": "6", "vii": "7", "viii": "8", "ix": "9", "x": "10",
}
_CRN_RE = re.compile(r"(?:crn\s*:?\s*)?(\d{4,6})")

# Tiers that are an exact identification. Every other tier is reported to the
# caller as ``matched_by`` so a tolerant match is never silent.
EXACT_TIERS = frozenset({"code", "name"})


def split_code(value: str | None) -> tuple[str, str, str] | None:
    """Split a course code into ``(branch, number, suffix)``, folded to ASCII lower case."""
    match = _CODE_RE.fullmatch(normalize_lookup_text(value))
    return (match.group(1), match.group(2), match.group(3)) if match else None


def name_tokens(value: str | None) -> list[str]:
    """Accent- and case-insensitive word tokens with Roman numerals as digits."""
    words = re.findall(r"[a-z0-9]+", normalize_lookup_text(value))
    return [_ROMAN.get(word, word) for word in words]


def crn_of(value: str | None) -> str | None:
    """Return the digits of a bare CRN or a ``CRN: 12345`` reference."""
    match = _CRN_RE.fullmatch(normalize_lookup_text(value))
    return match.group(1) if match else None


def match_courses(
    reference: str,
    items: Sequence[Any],
    *,
    codes: Callable[[Any], Iterable[str | None]],
    names: Callable[[Any], Iterable[str | None]],
    crn: Callable[[Any], str | None],
    partial_text: Callable[[Any], Iterable[str | None]] | None = None,
) -> tuple[str | None, list[Any]]:
    """Return ``(tier, candidates)`` for the first tier with any candidate.

    Tiers in order: ``code`` (spacing and case ignored), ``code_without_suffix``
    (same branch and number, one side has a letter suffix the other lacks),
    ``crn``, ``name`` (same words), ``name_words`` (every word of the reference
    occurs in the name) and ``partial_text`` (the reference is a substring).
    ``(None, [])`` means nothing matched.
    """
    target_code = split_code(reference)
    item_codes = [[code for code in map(split_code, codes(item)) if code] for item in items]

    def pick(predicate: Callable[[int], bool]) -> list[Any]:
        return [item for index, item in enumerate(items) if predicate(index)]

    if target_code is not None:
        found = pick(lambda index: target_code in item_codes[index])
        if found:
            return "code", found
        branch, number, suffix = target_code
        found = pick(lambda index: any(
            other[0] == branch and other[1] == number and bool(other[2]) != bool(suffix)
            for other in item_codes[index]
        ))
        if found:
            return "code_without_suffix", found

    target_crn = crn_of(reference)
    if target_crn is not None:
        found = pick(lambda index: str(crn(items[index]) or "").strip() == target_crn)
        if found:
            return "crn", found

    target_tokens = name_tokens(reference)
    if target_tokens:
        item_tokens = [[name_tokens(name) for name in names(item) if name] for item in items]
        found = pick(lambda index: target_tokens in item_tokens[index])
        if found:
            return "name", found
        if target_code is None:
            wanted = set(target_tokens)
            found = pick(lambda index: any(wanted <= set(tokens) for tokens in item_tokens[index]))
            if found:
                return "name_words", found

    target_text = normalize_lookup_text(reference)
    if partial_text is not None and target_text and target_code is None:
        found = pick(lambda index: any(
            target_text in normalize_lookup_text(text) for text in partial_text(items[index]) if text
        ))
        if found:
            return "partial_text", found
    return None, []
