"""What a tool result carries by default: the answer first, the raw source on request.

These rules are shared by the tools that pass OBS data on:

- The upstream payload is returned only with ``include_raw=true``. A default
  result names that switch in the short ``raw_available`` field.
- The OBS envelope (``statusCode``, ``resultCode``, ``resultMessage``) is
  dropped from a successful answer and kept on anything else. A result code
  that says more than "success" (``SCCSinifOgrenciYoklamaYok``) is kept.
- A label OBS sends in Turkish and English is given once, in Turkish. Course
  titles keep both languages when they differ.
- A term or course that the result already states once is not repeated on
  every row.

This is separate from ``compact``: that switch shortens long texts and lists
of whatever a result holds, while the rules here decide what a result holds.
"""

from __future__ import annotations

from typing import Any

ENVELOPE_KEYS = ("statusCode", "resultCode", "resultMessage")
# The text OBS sends with every successful answer.
_SUCCESS_MESSAGES = {"", "islem basarili", "islem basarili.", "success", "ok"}
# Stems whose English value says something the Turkish one does not.
BOTH_LANGUAGE_STEMS = frozenset({"dersAdi"})
# Result codes that only repeat "success". OBS also sends specific codes with
# status 0, such as SCCSinifOgrenciYoklamaYok, and those carry the answer.
_GENERIC_CODES = {"", "scc", "ok", "success", "successful", "basarili", "sccislembasarili", "sccbasarili"}
RAW_AVAILABLE_KEY = "raw_available"
_MAX_DEPTH = 12


# For a result whose rows are the upstream rows, reduced under the same keys.
RAW_UNREDUCED = "include_raw=true returns the unreduced OBS payload"


def raw_hint(*keys: str) -> str:
    """The value of ``raw_available``: how to get the raw data and under which keys."""
    return "include_raw=true adds: " + ", ".join(keys)


def _plain(text: Any) -> str:
    table = str.maketrans("İIıŞşĞğÜüÖöÇç", "iiissgguuoocc")
    return str(text or "").translate(table).strip().lower()


def is_success_envelope(payload: Any) -> bool:
    """A dict OBS answered with status 0 and nothing but its stock success text."""
    if not isinstance(payload, dict) or type(payload.get("statusCode")) is not int or payload["statusCode"] != 0:
        return False
    return _plain(payload.get("resultMessage")) in _SUCCESS_MESSAGES


def drop_success_envelope(value: Any, _depth: int = 0) -> Any:
    """Copy ``value`` without the envelope fields of every successful OBS answer in it.

    A failed answer, or a successful one whose message is not the stock text,
    keeps all three fields. A successful answer keeps a result code that is
    not one of the generic success codes.
    """
    if _depth > _MAX_DEPTH:
        return value
    if isinstance(value, list):
        return [drop_success_envelope(item, _depth + 1) for item in value]
    if not isinstance(value, dict):
        return value
    success = is_success_envelope(value)
    return {
        key: drop_success_envelope(item, _depth + 1)
        for key, item in value.items()
        if not (success and key in ENVELOPE_KEYS and (key != "resultCode" or _plain(item) in _GENERIC_CODES))
    }


def _filled(value: Any) -> bool:
    return value is not None and not (isinstance(value, str) and not value.strip())


def one_language(value: Any, _depth: int = 0) -> Any:
    """Copy ``value`` without the English twin of a label that is present in Turkish.

    ``xEN`` is dropped when ``xTR`` or ``x`` holds a value. A course title
    (``dersAdiEN``) is kept unless it repeats the Turkish title. An English
    value whose Turkish twin is empty is kept, so no information is lost.
    """
    if _depth > _MAX_DEPTH:
        return value
    if isinstance(value, list):
        return [one_language(item, _depth + 1) for item in value]
    if not isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    for key, item in value.items():
        if isinstance(key, str) and key.endswith("EN") and len(key) > 2:
            stem = key[:-2]
            twin = value.get(stem + "TR") if stem + "TR" in value else value.get(stem)
            if _filled(twin) and not isinstance(twin, (dict, list)):
                if stem not in BOTH_LANGUAGE_STEMS or _plain(twin) == _plain(item) or not _filled(item):
                    continue
        result[key] = one_language(item, _depth + 1)
    return result


def reduce_obs(value: Any) -> Any:
    """The default form of an OBS payload: no success envelope, one language per label."""
    return one_language(drop_success_envelope(value))


def term_reference(semester: Any) -> Any:
    """The resolved term, stated once per result with its name in one language."""
    return one_language(semester) if isinstance(semester, dict) else semester


_TERM_ALIASES = {"akademikDonemKodu": ("akademikDonemKodu", "donemKodu")}


def drop_repeated_term(rows: Any, semester: Any) -> Any:
    """Copy ``rows`` without the term fields that repeat the result's own ``semester``.

    Only a field whose value equals the resolved term's is dropped, so a row
    of another term keeps its fields.
    """
    if not isinstance(rows, list) or not isinstance(semester, dict):
        return rows
    reduced = []
    for row in rows:
        if not isinstance(row, dict):
            reduced.append(row)
            continue
        kept = {}
        for key, item in row.items():
            if isinstance(key, str) and key.startswith("akademikDonem"):
                names = _TERM_ALIASES.get(key, (key,))
                if any(name in semester and str(semester[name]) == str(item) for name in names):
                    continue
            kept[key] = item
        reduced.append(kept)
    return reduced


def reduce_term_payload(payload: Any, semester: Any) -> Any:
    """``reduce_obs`` for a payload of one term: its row lists also lose the repeated term."""
    reduced = reduce_obs(payload)
    if not isinstance(reduced, dict):
        return reduced
    return {
        key: drop_repeated_term(value, semester) if isinstance(value, list) else value
        for key, value in reduced.items()
    }


def pick(row: Any, keys: tuple[str, ...]) -> Any:
    """The named fields of ``row`` that hold a value, under their own names."""
    if not isinstance(row, dict):
        return row
    return {key: row[key] for key in keys if _filled(row.get(key))}

