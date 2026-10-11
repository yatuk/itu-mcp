"""Redaction of personal fields in OBS profile and contact payloads."""

from __future__ import annotations

import copy
import re
from typing import Any

from .parsing import clean_text, normalize_lookup_text

REDACTED = "***REDACTED***"
# Field names and contact labels are split into words (camelCase, hyphen,
# underscore, space) and folded with normalize_lookup_text, which maps the
# Turkish dotted and dotless i to a plain "i". So "İkamet", "E-Posta",
# "ituEPosta" and "cepTelefonu" are all recognised.
#
# Distinctive stems are matched anywhere in the name with its separators
# removed, so "evtelefon" and "ikametAdresi" are covered.
_SENSITIVE_KEY = re.compile(
    r"kimlikno|tckimlik|tckn|pasaport|passport"
    r"|eposta|email"
    r"|telefon|phone|gsm"
    r"|adres|address|ikamet"
    r"|dogum|birth"
    r"|anaadi|anneadi|babaadi|mothername|fathername"
)
# Short names are matched as whole words only: "tel" must not hide "hotel"
# or "telif", "mail" must not hide "mailing".
_SENSITIVE_WORD = re.compile(r"mail|tel|cep|mobil|mobile|telno|ceptel|cepno")
# Contact labels are free text about a way to reach a person, so the old
# substring match is kept for them next to the word-aware match.
_SENSITIVE_CONTACT_TYPE = (
    "telefon", "phone", "cep", "mobile", "gsm", "adres", "address", "ikamet",
    "e-posta", "eposta", "e-mail", "email", "mail",
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
# Turkish phone number: optional +90 / 0090 / 90, optional trunk 0, an area or
# operator code starting with 2 to 5, then seven more digits. It is written
# either as ten digits in a row or with the code set apart (space, dot, hyphen
# or parentheses, which may hold the trunk 0) and the rest grouped 3 + 2 + 2. The number may not touch
# another digit, so CRNs (5 digits), term codes (6), student numbers (9),
# years, credits and dates are left alone.
_PHONE = re.compile(
    r"(?<![0-9])(?:(?:\+|00)?90[\s.-]?)?(?:0[\s.-]?)?"
    r"(?:[2-5][0-9]{9}"
    r"|(?:\(0?[2-5][0-9]{2}\)[\s.-]?|[2-5][0-9]{2}[\s.-])[0-9]{3}[\s.-]?[0-9]{2}[\s.-]?[0-9]{2})"
    r"(?![0-9])"
)
# National identity number: exactly 11 digits, the first one not zero.
_NATIONAL_ID = re.compile(r"(?<![0-9])[1-9][0-9]{10}(?![0-9])")
_VALUE_PATTERNS = (_EMAIL, _PHONE, _NATIONAL_ID)
_REDACTION_NOTE = (
    "The fields named in redacted_fields exist and are hidden. They are returned only when the "
    "operator set NINOVA_ALLOW_SENSITIVE_OUTPUT and include_sensitive=true is passed."
)
_CONTACT_LIST_KEY = "iletisimBilgiList"
_CONTACT_VALUE_KEY = "degeri"
_CONTACT_LABEL_KEYS = ("iletisimTipiAdiTR", "iletisimTipiAdiEN")
_REPORT_KEYS = ("redacted_fields", "redaction_note")


def redact_obs_profile(payload: dict[str, Any]) -> dict[str, Any]:
    """Hide personal fields in OBS profile and contact payloads.

    Hidden: national and passport numbers, e-mail addresses, phone numbers,
    addresses, birth date and place, parents' names. The student's own name
    and student number stay visible. The result names what was hidden in
    ``redacted_fields`` so a reader knows the fields exist.
    """
    data = copy.deepcopy(payload)
    if not isinstance(data, dict):
        return data
    # The report describes this call only: whatever came in is dropped.
    for key in _REPORT_KEYS:
        data.pop(key, None)
    hidden: list[str] = []
    _scrub(data, "", hidden)
    data["redacted_fields"] = hidden
    if hidden:
        data["redaction_note"] = _REDACTION_NOTE
    return data


def _note_hidden(hidden: list[str], name: str) -> None:
    if name not in hidden:
        hidden.append(name)


def _words(name: str) -> list[str]:
    """Folded words of a field name or label: camelCase and separators split it."""
    spaced: list[str] = []
    for index, char in enumerate(name):
        if char.isupper() and index:
            before = name[index - 1]
            after = name[index + 1] if index + 1 < len(name) else ""
            if before.islower() or before.isdigit() or (before.isupper() and after.islower()):
                spaced.append(" ")
        spaced.append(char)
    return [word for word in re.split(r"[^a-z0-9]+", normalize_lookup_text("".join(spaced))) if word]


def _is_sensitive_name(name: str) -> bool:
    words = _words(name)
    if _SENSITIVE_KEY.search("".join(words)):
        return True
    return any(_SENSITIVE_WORD.fullmatch(word) for word in words)


def _carries_data(value: Any) -> bool:
    """False for values with nothing to hide: None, blank text, zero, empty containers."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, dict):
        return any(_carries_data(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_carries_data(item) for item in value)
    return True


def _mask_text(text: str) -> str:
    """Replace every e-mail address, phone number and national ID inside a text."""
    for pattern in _VALUE_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def _looks_personal(value: Any) -> bool:
    """True for a text holding an address, phone or ID, or a number that is one."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return bool(_PHONE.fullmatch(str(value)) or _NATIONAL_ID.fullmatch(str(value)))
    return isinstance(value, str) and any(pattern.search(value) for pattern in _VALUE_PATTERNS)


def _scrub(node: Any, path: str, hidden: list[str]) -> Any:
    """Redact everything below ``node`` (records in place) and return its redacted form."""
    if isinstance(node, dict):
        for key in list(node):
            here = f"{path}.{key}" if path else str(key)
            node[key] = _scrub_field(key, node[key], here, hidden)
        return node
    if isinstance(node, list):
        return [_scrub(item, path, hidden) for item in node]
    if _looks_personal(node):
        _note_hidden(hidden, path)
        return _mask_text(node) if isinstance(node, str) else REDACTED
    return node


def _scrub_field(key: Any, value: Any, path: str, hidden: list[str]) -> Any:
    """Redacted value of one record field, decided by the field's name."""
    if key == _CONTACT_LIST_KEY:
        return _scrub_contacts(value, hidden)
    if not _is_sensitive_name(str(key)):
        return _scrub(value, path, hidden)
    if not _carries_data(value):
        return value
    _note_hidden(hidden, path)
    return REDACTED


def _scrub_contacts(node: Any, hidden: list[str]) -> Any:
    """Redact contact rows, which carry their kind in a label and not in the field name."""
    if isinstance(node, list):
        return [_scrub_contacts(item, hidden) for item in node]
    if not isinstance(node, dict):
        return _scrub(node, _CONTACT_LIST_KEY, hidden)
    if _CONTACT_LIST_KEY in node:
        # A group of rows: its own fields are treated like any other record.
        for key in list(node):
            node[key] = _scrub_field(key, node[key], f"{_CONTACT_LIST_KEY}.{key}", hidden)
        return node
    label = str(node.get("iletisimTipiAdiTR") or node.get("iletisimTipiAdiEN") or "")
    name = f"{_CONTACT_LIST_KEY}: {clean_text(label) or 'unlabelled'}"
    sensitive = _is_sensitive_contact_label(label)
    for key in list(node):
        if key in _CONTACT_LABEL_KEYS:
            continue
        node[key] = _scrub_contact_field(key, node[key], sensitive, name, hidden)
    return node


def _scrub_contact_field(key: Any, value: Any, sensitive_row: bool, name: str, hidden: list[str]) -> Any:
    """One field of a contact row; everything hidden is reported under the row's label."""
    if key == _CONTACT_LIST_KEY:
        return _scrub_contacts(value, hidden)
    if key == _CONTACT_VALUE_KEY and _carries_data(value) and (sensitive_row or _looks_personal(value)):
        # The value of the row is hidden as a whole, also when only part of it is an address.
        _note_hidden(hidden, name)
        return REDACTED
    if sensitive_row:
        return _blank_texts(value, name, hidden)
    found: list[str] = []
    value = _scrub_field(key, value, name, found)
    if found:
        _note_hidden(hidden, name)
    return value


def _blank_texts(node: Any, name: str, hidden: list[str]) -> Any:
    """In a sensitive contact row every text is hidden, wherever the Portal put it."""
    if isinstance(node, dict):
        return {key: _blank_texts(value, name, hidden) for key, value in node.items()}
    if isinstance(node, list):
        return [_blank_texts(item, name, hidden) for item in node]
    if (isinstance(node, str) and node.strip()) or _looks_personal(node):
        _note_hidden(hidden, name)
        return REDACTED
    return node


def _is_sensitive_contact_label(label: str) -> bool:
    kind = normalize_lookup_text(label)
    return _is_sensitive_name(label) or any(token in kind for token in _SENSITIVE_CONTACT_TYPE)
